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

These call the KibanaPyGateway directly (the `gateway` fixture), so a rejected
call surfaces as KibanaRejected — not the toolbox's ToolError wrapper.
"""

import time
import uuid

import pytest

from kibana_mcp.core.errors import KibanaRejected

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


def _new_target_policy(gateway, name):
    # Throwaway agent policy used only as a reassign target. Left behind on teardown
    # (the whole stack is disposable) — deleting it while a just-unenrolled agent
    # still references it could 400, so we don't.
    return gateway.create_agent_policy(
        name=name, namespace="default", monitoring_enabled=[],
        description=None, inactivity_timeout=None).id


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


def test_agent1_lifecycle(gateway):
    # Ordered single-agent path; the terminal unenroll runs last.
    uniq = uuid.uuid4().hex[:8]
    sac = _sacrificial_ids(gateway)
    assert len(sac) >= 2, f"expected >=2 sacrificial agents, saw {sac}"
    agent1 = sac[0]
    target = _new_target_policy(gateway, f"eph-target-{uniq}")

    # 1) reassign -> the agent's policy_id must actually change to the target.
    gateway.reassign_agent(agent_id=agent1, policy_id=target)
    _poll_until(
        lambda: _agent_policy_id(gateway, agent1) == target,
        what=f"agent1 {agent1} to land on policy {target}",
    )
    assert _agent_policy_id(gateway, agent1) == target

    # 2) upgrade to an invalid version. The agent exists, so Kibana validates the
    #    version against its available_versions and rejects it — the gateway maps
    #    that 400 to KibanaRejected (confirmed live). A completed upgrade (real
    #    binary download) is not verified offline; the rejection path is.
    with pytest.raises(KibanaRejected):
        gateway.upgrade_agent(agent_id=agent1, version="0.0.0-invalid")

    # 3) terminal: force+revoke unenroll -> agent1 leaves the fleet.
    gateway.unenroll_agent(agent_id=agent1, force=True, revoke=True)
    _poll_until(
        lambda: _agent_removed(gateway, agent1),
        what=f"agent1 {agent1} to unenroll (removed/inactive)",
    )
    assert _agent_removed(gateway, agent1)


def test_agent2_lifecycle(gateway):
    # Ordered bulk path; each bulk call is async and returns an action-id string.
    uniq = uuid.uuid4().hex[:8]
    sac = _sacrificial_ids(gateway)
    assert sac, f"expected >=1 sacrificial agent for agent2, saw {sac}"
    agent2 = sac[-1]  # largest id — stable even once agent1 has dropped out
    target = _new_target_policy(gateway, f"eph-target2-{uniq}")

    # 1) bulk_reassign returns an action-id string; agent2 lands on the target.
    r = gateway.bulk_reassign(agent_ids=[agent2], policy_id=target)
    assert isinstance(r, str) and r, f"bulk_reassign returned {r!r}"
    _poll_until(
        lambda: _agent_policy_id(gateway, agent2) == target,
        what=f"agent2 {agent2} to land on policy {target}",
    )
    assert _agent_policy_id(gateway, agent2) == target

    # 2) bulk_upgrade QUEUES the action (live-probed: bulk does NOT sync-reject an
    #    invalid version the way the single upgrade does) — just an action-id back.
    r = gateway.bulk_upgrade(agent_ids=[agent2], version="0.0.0-invalid")
    assert isinstance(r, str) and r, f"bulk_upgrade returned {r!r}"

    # 3) terminal: bulk_unenroll returns an action-id; agent2 leaves the fleet.
    r = gateway.bulk_unenroll(agent_ids=[agent2], force=True, revoke=True)
    assert isinstance(r, str) and r, f"bulk_unenroll returned {r!r}"
    _poll_until(
        lambda: _agent_removed(gateway, agent2),
        what=f"agent2 {agent2} to unenroll (removed/inactive)",
    )
    assert _agent_removed(gateway, agent2)


def test_output_promotion(gateway):
    # Promoting a non-default output auto-un-defaults the prior default (live-probed).
    # Isolated stack, so this is safe here (never run against a shared default).
    uniq = uuid.uuid4().hex[:8]
    original = next(o for o in gateway.list_outputs() if o.is_default)
    a = b = None
    try:
        a = gateway.create_output(
            name=f"eph-out-a-{uniq}", type="elasticsearch", hosts=["http://localhost:9200"])
        b = gateway.create_output(
            name=f"eph-out-b-{uniq}", type="elasticsearch", hosts=["http://localhost:9200"])
        assert a.is_default is False and b.is_default is False

        gateway.update_output(output_id=a.id, changes={"is_default": True})
        refreshed = next(o for o in gateway.list_outputs() if o.id == original.id)
        assert refreshed.is_default is False  # Fleet flipped the prior default off
    finally:
        # Restore the original default FIRST (idempotent; auto-un-defaults A) so the
        # delete-guard no longer refuses A, then remove both throwaways. The stack is
        # disposable, but leaving it clean keeps a re-seeded dev stack sane.
        gateway.update_output(
            output_id=original.id, changes={"is_default": True}, confirm=True)
        if a is not None:
            gateway.delete_output(output_id=a.id)
        if b is not None:
            gateway.delete_output(output_id=b.id)
