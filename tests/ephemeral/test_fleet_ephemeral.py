"""Fleet agent-lifecycle battle-test on the isolated 2-agent ephemeral stack.

Runs ONLY under scripts/fleet_ephemeral.sh, which boots an isolated single-node
stack + a Fleet Server + TWO sacrificial enrolled agents, then passes the stack
creds via the process env (KIBANA_URL/KIBANA_TEST_API_KEY) — never the
machine-owned elastic-start-local/.env.seed. The default pytest run excludes this
tier (pyproject addopts `-m 'not ... and not fleet_ephemeral'`).

Test order matters and pytest runs a file top-to-bottom:
  1. test_two_agents_online   — smoke gate: both sacrificial agents reach 'online'
                                 BEFORE the destructive lifecycle tests act on them.
  2. test_agent1_lifecycle    — single-agent path: reassign -> upgrade -> unenroll.
  3. test_agent2_lifecycle    — bulk path: bulk_reassign -> bulk_upgrade -> bulk_unenroll.
  4. test_output_promotion    — default-output promote/restore lifecycle.

The lifecycle tests are each ONE ordered test so the terminal action (unenroll)
runs last, and each acts on a DISTINCT agent: agent1 := the smallest sacrificial
agent id (acted on first), agent2 := the largest. Picking the extremes (not the
first two) keeps agent2's identity stable even after agent1 has been unenrolled
and dropped out of the fleet listing.

Every action goes through the MCP tool layer (the `call_tool` fixture: the full
server and a fastmcp Client, D60), so the confirm gates, the argument schemas
and the ToolError text of a Kibana refusal run live; each gated call is first
made without confirm=True and must be refused. The `gateway` fixture only
observes (agent listings, polling).
"""

import time
import uuid

import pytest
from fastmcp.exceptions import ToolError

pytestmark = pytest.mark.fleet_ephemeral


# --- helpers: agent/policy identification + bounded polling -----------------


def _all_agents(gateway):
    # show_inactive=True so a just-unenrolled agent still lists — this keeps the
    # sacrificial ordering stable across the ordered lifecycle tests (agent1 stays
    # visible after its own test unenrolls it, so agent2's max-id pick is unchanged).
    return gateway._client.fleet_agents.get_all(per_page=50, show_inactive=True).body["items"]


def _fleet_server_policy_id(gateway):
    policies = gateway._client.fleet_policies.get_agent_policies(per_page=50).body["items"]
    fsp = next(p for p in policies if p.get("is_default_fleet_server"))
    return fsp["id"]


def _sacrificial_ids(gateway):
    # Every enrolled agent NOT on the fleet-server policy is sacrificial. Sorted by
    # id for a deterministic order across the ordered tests. A reassigned agent's
    # policy_id changes to a throwaway target (never the fleet-server policy — the
    # reassign guard forbids that), so it stays classified sacrificial here.
    fsp_id = _fleet_server_policy_id(gateway)
    return sorted(a["id"] for a in _all_agents(gateway) if a.get("policy_id") != fsp_id)


def _agent_policy_id(gateway, agent_id):
    match = next((a for a in _all_agents(gateway) if a["id"] == agent_id), None)
    return match.get("policy_id") if match else None


def _agent_removed(gateway, agent_id):
    # Terminal-state check for a force+revoke unenroll: the agent is either gone
    # from the listing entirely, or still listed but flipped inactive/unenrolled.
    match = next((a for a in _all_agents(gateway) if a["id"] == agent_id), None)
    if match is None:
        return True
    if match.get("active") is False:
        return True
    return match.get("status") in {"inactive", "unenrolled", "offline"}


def _poll_until(predicate, *, what):
    # Bounded poll (60 × 2s = 120s) — mirrors the readiness bounds in
    # scripts/fleet_ephemeral.sh; fail loud on timeout rather than hang/false-pass.
    for _ in range(60):
        if predicate():
            return
        time.sleep(2)
    pytest.fail(f"timed out waiting for {what}")


async def _new_target_policy(call_tool, name):
    # Throwaway agent policy used only as a reassign target. Left behind on teardown
    # (the whole stack is disposable) — deleting it while a just-unenrolled agent
    # still references it could 400, so we don't.
    created = await call_tool("create_agent_policy", {
        "name": name, "namespace": "default", "monitoring_enabled": []})
    return created["id"]


async def _refused_without_confirm(call_tool, tool, args):
    # The confirm gate first: the same call without confirm=True must be refused,
    # naming the fix, and change nothing (the bulk gates refuse in the tool; the
    # default-output gate in the gateway, after it reads the output).
    with pytest.raises(ToolError, match="confirm=True"):
        await call_tool(tool, args)


# --- tests (file order is the execution order) ------------------------------


def test_two_agents_online(gateway):
    # Both sacrificial agents must reach 'online' before the lifecycle tests can
    # act on them. Bounded poll (60 × 2s = 120s) — fail loud on timeout.
    for _ in range(60):
        agents = gateway._client.fleet_agents.get_all(per_page=50).body["items"]
        if len([a for a in agents if a.get("status") == "online"]) >= 2:
            break
        time.sleep(2)
    else:
        pytest.fail("two sacrificial agents never reached online")


async def test_agent1_lifecycle(gateway, call_tool):
    # Ordered single-agent path; the terminal unenroll runs last.
    uniq = uuid.uuid4().hex[:8]
    sac = _sacrificial_ids(gateway)
    assert len(sac) >= 2, f"expected >=2 sacrificial agents, saw {sac}"
    agent1 = sac[0]
    target = await _new_target_policy(call_tool, f"eph-target-{uniq}")

    # 1) reassign -> the agent's policy_id must actually change to the target.
    await call_tool("reassign_agent", {"agent_id": agent1, "policy_id": target})
    _poll_until(
        lambda: _agent_policy_id(gateway, agent1) == target,
        what=f"agent1 {agent1} to land on policy {target}",
    )
    assert _agent_policy_id(gateway, agent1) == target

    # 2) upgrade to an invalid version. The agent exists, so Kibana validates the
    #    version against its available_versions and rejects it (400, confirmed
    #    live); the tool hands the refusal back as a ToolError. A completed upgrade
    #    (real binary download) is not verified offline; the rejection path is.
    with pytest.raises(ToolError, match="Kibana rejected"):
        await call_tool("upgrade_agent", {"agent_id": agent1, "version": "0.0.0-invalid"})

    # 3) terminal: force+revoke unenroll -> agent1 leaves the fleet.
    await call_tool("unenroll_agent", {"agent_id": agent1, "force": True, "revoke": True})
    _poll_until(
        lambda: _agent_removed(gateway, agent1),
        what=f"agent1 {agent1} to unenroll (removed/inactive)",
    )
    assert _agent_removed(gateway, agent1)


async def test_agent2_lifecycle(gateway, call_tool):
    # Ordered bulk path; each bulk call is async and returns an action id.
    uniq = uuid.uuid4().hex[:8]
    sac = _sacrificial_ids(gateway)
    assert sac, f"expected >=1 sacrificial agent for agent2, saw {sac}"
    agent2 = sac[-1]  # largest id — stable even once agent1 has dropped out
    target = await _new_target_policy(call_tool, f"eph-target2-{uniq}")

    # 1) bulk_reassign returns an action id; agent2 lands on the target.
    args = {"agent_ids": [agent2], "policy_id": target}
    await _refused_without_confirm(call_tool, "bulk_reassign", args)
    r = await call_tool("bulk_reassign", {**args, "confirm": True})
    assert r["action_id"], f"bulk_reassign returned {r!r}"
    _poll_until(
        lambda: _agent_policy_id(gateway, agent2) == target,
        what=f"agent2 {agent2} to land on policy {target}",
    )
    assert _agent_policy_id(gateway, agent2) == target

    # 2) bulk_upgrade QUEUES the action (live-probed: bulk does NOT sync-reject an
    #    invalid version the way the single upgrade does) — just an action id back.
    args = {"agent_ids": [agent2], "version": "0.0.0-invalid"}
    await _refused_without_confirm(call_tool, "bulk_upgrade", args)
    r = await call_tool("bulk_upgrade", {**args, "confirm": True})
    assert r["action_id"], f"bulk_upgrade returned {r!r}"

    # 3) terminal: bulk_unenroll returns an action id; agent2 leaves the fleet.
    args = {"agent_ids": [agent2], "force": True, "revoke": True}
    await _refused_without_confirm(call_tool, "bulk_unenroll", args)
    r = await call_tool("bulk_unenroll", {**args, "confirm": True})
    assert r["action_id"], f"bulk_unenroll returned {r!r}"
    _poll_until(
        lambda: _agent_removed(gateway, agent2),
        what=f"agent2 {agent2} to unenroll (removed/inactive)",
    )
    assert _agent_removed(gateway, agent2)


async def test_output_promotion(gateway, call_tool):
    # Promoting a non-default output auto-un-defaults the prior default (live-probed).
    # Isolated stack, so this is safe here (never run against a shared default).
    uniq = uuid.uuid4().hex[:8]
    original = next(o for o in gateway.list_outputs() if o.is_default)
    a = b = None
    try:
        a = await call_tool("create_output", {
            "name": f"eph-out-a-{uniq}", "type": "elasticsearch",
            "hosts": ["http://localhost:9200"]})
        b = await call_tool("create_output", {
            "name": f"eph-out-b-{uniq}", "type": "elasticsearch",
            "hosts": ["http://localhost:9200"]})
        assert a["is_default"] is False and b["is_default"] is False

        await call_tool("update_output", {"output_id": a["id"], "is_default": True})
        refreshed = next(o for o in gateway.list_outputs() if o.id == original.id)
        assert refreshed.is_default is False  # Fleet flipped the prior default off
        # A is the default now: editing it without confirm=True is refused.
        await _refused_without_confirm(
            call_tool, "update_output", {"output_id": a["id"], "name": f"eph-out-a2-{uniq}"})
    finally:
        # Restore the original default FIRST (idempotent; auto-un-defaults A) so the
        # delete-guard no longer refuses A, then remove both throwaways. The stack is
        # disposable, but leaving it clean keeps a re-seeded dev stack sane.
        await call_tool("update_output", {
            "output_id": original.id, "is_default": True, "confirm": True})
        if a is not None:
            await call_tool("delete_output", {"output_id": a["id"]})
        if b is not None:
            await call_tool("delete_output", {"output_id": b["id"]})
