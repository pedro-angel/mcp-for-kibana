"""Live contract tests for the fleet toolbox against the dev stack's always-on
Fleet Server + demo agent (scripts/fleet_stack.sh). Assert SHAPE, not pinned
values. The load-bearing checks are the live secret-redaction ones: enrollment
keys and outputs must carry no secret material off a real Kibana. Registry-backed
reads (EPM catalog/categories) are asserted by type only — they proxy the
external Elastic Package Registry and would flake on a non-empty assertion.

The write/destructive-tier tests below (#81 task 8) create THROWAWAY objects
only, deleted in a `finally` with force=True — never the two always-on demo
agents or their policy revisions (no reassign/upgrade/unenroll here; that's
the ephemeral-stack suite). The three refusal tests never delete a shared
object: they assert Kibana/the guard rejects the call and the object survives.
"""

import uuid
from dataclasses import asdict

import pytest

from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected

pytestmark = pytest.mark.contract


def test_list_agents_has_the_two_enrolled(gateway):
    agents = gateway.list_agents()
    assert len(agents) >= 2  # fleet-server self-registration + the demo agent
    policies = {a.policy_id for a in agents}
    assert "fleet-server-policy" in policies
    for a in agents:
        assert a.id and isinstance(a.status, str) and a.version


def test_get_agent_roundtrip_and_missing(gateway):
    one = gateway.list_agents()[0]
    got = gateway.get_agent(one.id)
    assert got.id == one.id
    with pytest.raises(KibanaNotFound):
        gateway.get_agent("does-not-exist-00000000")


def test_agent_status_summary(gateway):
    s = gateway.get_agent_status()
    # total ('all') counts every agent; the tracked buckets are a subset of the
    # API's status set (orphaned/updating/... exist too). Assert total plus a real
    # online floor (the fleet-server + demo agent both check in online).
    assert s.total >= 2 and s.online >= 1


def test_agent_versions_nonempty(gateway):
    versions = gateway.list_agent_versions()
    assert versions and all(isinstance(v, str) for v in versions)


def test_agent_policies_include_fixtures(gateway):
    by_id = {p.id: p for p in gateway.list_agent_policies()}
    assert {"fleet-server-policy", "fleet-agent-policy"} <= set(by_id)
    # The demo agent is enrolled on fleet-agent-policy, so the LIST must report a
    # real assigned count — regression guard for the missing with_agent_count
    # (the list endpoint returns 0 for every policy without it).
    assert by_id["fleet-agent-policy"].agent_count >= 1
    got = gateway.get_agent_policy("fleet-agent-policy")
    assert got.id == "fleet-agent-policy" and got.name and got.agent_count >= 1
    with pytest.raises(KibanaNotFound):
        gateway.get_agent_policy("no-such-policy-00000000")


def test_package_policies_include_fleet_server(gateway):
    pps = gateway.list_package_policies()
    assert any(pp.package_name == "fleet_server" for pp in pps)
    if pps:
        got = gateway.get_package_policy(pps[0].id)
        assert got.id == pps[0].id
    with pytest.raises(KibanaNotFound):
        gateway.get_package_policy("no-such-package-policy-00000000")


def test_enrollment_keys_carry_no_secret(gateway):
    keys = gateway.list_enrollment_keys()
    assert keys  # the demo-agent policy has at least one
    for k in keys:
        d = asdict(k)
        assert "api_key" not in d and "api_key_id" not in d
        assert set(d) == {"id", "name", "policy_id", "active", "created_at"}
    got = gateway.get_enrollment_key(keys[0].id)
    assert "api_key" not in asdict(got)


def test_uninstall_tokens_are_metadata_only(gateway):
    for t in gateway.list_uninstall_tokens():
        d = asdict(t)
        assert set(d) == {"id", "policy_id", "policy_name", "created_at"}


def test_installed_packages_include_fleet_server(gateway):
    installed = gateway.list_installed_packages()
    assert any(p.name == "fleet_server" for p in installed)
    got = gateway.get_package("fleet_server")
    assert got.name == "fleet_server" and got.version
    with pytest.raises(KibanaNotFound):
        gateway.get_package("no-such-integration-00000000")


def test_registry_reads_return_lists(gateway):
    # EPR-backed: assert type only (a non-empty assertion would couple to the
    # external Elastic Package Registry's availability).
    assert isinstance(gateway.list_packages(), list)
    assert isinstance(gateway.list_package_categories(), list)


def test_outputs_carry_no_secret(gateway):
    outputs = gateway.list_outputs()
    assert any(o.is_default for o in outputs)
    for o in outputs:
        d = asdict(o)
        assert set(d) == {"id", "name", "type", "hosts", "is_default", "is_default_monitoring"}
    default = next(o for o in outputs if o.is_default)
    health = gateway.get_output_health(default.id)
    assert isinstance(health.state, str)


def test_fleet_server_hosts_list(gateway):
    assert isinstance(gateway.list_fleet_server_hosts(), list)


def test_settings_and_permissions(gateway):
    s = gateway.get_fleet_settings()
    assert s.id  # 'fleet-default-settings'
    assert gateway.check_fleet_permissions().success is True


# --- fleet writes: non-agent tools, live (#81 task 8) ---


def test_agent_policy_crud_rmw_live(gateway):
    suffix = uuid.uuid4().hex[:8]
    pol = gateway.create_agent_policy(
        name=f"mcp-t8-{suffix}", namespace="default", monitoring_enabled=[],
        inactivity_timeout=999, description="orig")
    try:
        updated = gateway.update_agent_policy(
            agent_policy_id=pol.id, changes={"name": "mcp-t8-renamed"})
        assert updated.name == "mcp-t8-renamed"
        # inactivity_timeout/description aren't on the FleetAgentPolicy DTO ->
        # read the raw body to prove the RMW carried them through untouched.
        raw = gateway._client.fleet_policies.get_agent_policy(
            agent_policy_id=pol.id).body["item"]
        assert raw["inactivity_timeout"] == 999
        assert raw["description"] == "orig"
    finally:
        gateway.delete_agent_policy(agent_policy_id=pol.id, force=True)


def test_package_policy_crud_live(gateway):
    suffix = uuid.uuid4().hex[:8]
    pol = gateway.create_agent_policy(
        name=f"mcp-t8-pol-{suffix}", namespace="default", monitoring_enabled=[],
        inactivity_timeout=None, description=None)
    pp = None
    try:
        version = gateway._client.fleet_epm.get_package(
            pkg_name="fleet_server").body["item"]["version"]
        pp = gateway.create_package_policy(
            name=f"mcp-t8-pp-{suffix}", package={"name": "fleet_server", "version": version},
            agent_policy_id=pol.id, inputs={})
        assert pp.package_name == "fleet_server" and pp.agent_policy_id == pol.id
        got = gateway.get_package_policy(pp.id)
        assert got.id == pp.id
        updated = gateway.update_package_policy(
            package_policy_id=pp.id, changes={"description": "changed"})
        assert updated.description == "changed"
    finally:
        if pp is not None:
            gateway.delete_package_policy(package_policy_id=pp.id, force=True)
        gateway.delete_agent_policy(agent_policy_id=pol.id, force=True)


def test_output_crud_live(gateway):
    suffix = uuid.uuid4().hex[:8]
    out = gateway.create_output(
        name=f"mcp-t8-out-{suffix}", type="elasticsearch", hosts=["http://localhost:19200"])
    try:
        d = asdict(out)
        assert "ssl" not in d and "secrets" not in d
        assert out.is_default is False
        updated = gateway.update_output(
            output_id=out.id,
            changes={"hosts": ["http://localhost:19200", "http://es2:9200"]})
        assert updated.hosts == ("http://localhost:19200", "http://es2:9200")
    finally:
        gateway.delete_output(output_id=out.id)


def test_delete_default_output_refused_live(gateway):
    default = next(o for o in gateway.list_outputs() if o.is_default)
    with pytest.raises(KibanaRejected):
        gateway.delete_output(output_id=default.id)
    still = gateway.list_outputs()
    assert any(o.id == default.id for o in still)


def test_delete_assigned_policy_refused_live(gateway):
    # fleet-agent-policy carries the always-on demo agent -> Kibana's own
    # assigned-agent 400. force=False: never actually force this one through.
    with pytest.raises(KibanaRejected):
        gateway.delete_agent_policy(agent_policy_id="fleet-agent-policy", force=False)
    survivor = gateway.get_agent_policy("fleet-agent-policy")
    assert survivor.id == "fleet-agent-policy"


def test_delete_fleet_server_policy_refused_live(gateway):
    # Client-side guard: is_default_fleet_server. Never force this one either
    # (it would strand the always-on Fleet Server).
    with pytest.raises(KibanaRejected):
        gateway.delete_agent_policy(agent_policy_id="fleet-server-policy")
    survivor = gateway.get_agent_policy("fleet-server-policy")
    assert survivor.id == "fleet-server-policy"


# --- fleet advanced ops (D51): agent tags, diagnostics, package install/uninstall.
# Tags go on the demo agent and come off in a `finally`; a diagnostics bundle is
# deleted after the test; the package is `tcp` (small, no agent needed), left
# uninstalled; fleet_server is only ever asked WITHOUT force. ---


def _demo_agent(gateway):
    agents = [a for a in gateway.list_agents()
              if a.policy_id == "fleet-agent-policy" and a.status == "online"]
    assert agents, "the stack's demo agent is not online — scripts/stack.sh up && seed"
    return agents[0]


def test_agent_tags_live(gateway):
    agent = _demo_agent(gateway)
    tag = f"mcp-d51-{uuid.uuid4().hex[:8]}"
    try:
        added = gateway.update_agent_tags(agent_ids=[agent.id], tags_to_add=[tag], tags_to_remove=[])
        (change,) = added.agents
        assert added.action_id and added.not_applied_agent_ids == ()
        assert tag not in change.previous_tags and tag in change.tags
        # Applied before Kibana answers (an id list, not a kuery): readable at once.
        assert tag in gateway.get_agent(agent.id).tags
        assert tag in gateway.list_agent_tags()
        # Premise of the pre-read: Kibana answers 200 for an id that names no agent.
        raw = gateway._client.fleet_agents.bulk_update_tags(
            agents=["no-such-agent-00000000"], tags_to_add=[tag]).body
        assert raw["actionId"]
        with pytest.raises(KibanaNotFound, match="list_agents"):
            gateway.update_agent_tags(
                agent_ids=[agent.id, "no-such-agent-00000000"], tags_to_add=[],
                tags_to_remove=[tag])
        assert tag in gateway.get_agent(agent.id).tags  # the refusal changed nothing
        removed = gateway.update_agent_tags(agent_ids=[agent.id], tags_to_add=[], tags_to_remove=[tag])
        assert tag in removed.agents[0].previous_tags and tag not in removed.agents[0].tags
        assert tag not in gateway.list_agent_tags()
    finally:
        gateway._client.fleet_agents.bulk_update_tags(agents=[agent.id], tags_to_remove=[tag])


def test_agent_diagnostics_live(gateway):
    import time

    agent = _demo_agent(gateway)
    req = gateway.request_agent_diagnostics(agent_id=agent.id)
    assert req.action_id and req.agent_id == agent.id and req.agent_status == "online"
    bundle = None
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            mine = [d for d in gateway.list_agent_diagnostics(agent.id) if d.action_id == req.action_id]
            if mine and mine[0].status in ("READY", "FAILED"):
                bundle = mine[0]
                break
            time.sleep(3)
        assert bundle is not None, "the demo agent did not upload the bundle within 120 s"
        assert bundle.status == "READY" and bundle.name.endswith(".zip") and bundle.created_at
    finally:
        for d in gateway.list_agent_diagnostics(agent.id):
            if d.action_id == req.action_id and d.status == "READY":
                gateway._client.fleet_agents.delete_file(file_id=d.id)
    for call in (lambda: gateway.request_agent_diagnostics(agent_id="no-such-agent-00000000"),
                 lambda: gateway.list_agent_diagnostics("no-such-agent-00000000")):
        with pytest.raises(KibanaNotFound, match="list_agents"):
            call()


def _ensure_uninstalled(gateway, name):
    if gateway._client.fleet_epm.get_package(pkg_name=name).body["item"].get("status") == "installed":
        gateway._client.fleet_epm.uninstall_package(pkg_name=name, force=True)


def test_package_install_uninstall_live(gateway):
    from kibana.exceptions import ApiError

    _ensure_uninstalled(gateway, "tcp")
    suffix = uuid.uuid4().hex[:8]
    pol = gateway.create_agent_policy(
        name=f"mcp-d51-pol-{suffix}", namespace="default", monitoring_enabled=[])
    try:
        with pytest.raises(KibanaNotFound, match=r"has no version '99\.0\.0' .*omit version"):
            gateway.install_package(name="tcp", version="99.0.0")
        with pytest.raises(KibanaNotFound, match="list_packages"):
            gateway.install_package(name="no-such-integration-00000000")

        installed = gateway.install_package(name="tcp")
        latest = gateway._client.fleet_epm.get_package(pkg_name="tcp").body["item"]["latestVersion"]
        assert (installed.status, installed.version, installed.already_installed) == (
            "installed", latest, False)
        assert gateway.install_package(name="tcp").already_installed is True
        with pytest.raises(KibanaRejected, match="only installs a package that is not installed"):
            gateway.install_package(name="tcp", version="0.0.1")
        # Premise of the version refusal: an upgrade cannot be undone on Basic.
        with pytest.raises(ApiError) as rollback:
            gateway._client.fleet_epm.rollback_package(pkg_name="tcp")
        assert rollback.value.meta.status in (403, 404)

        pp = gateway.create_package_policy(
            name=f"mcp-d51-tcp-{suffix}", package={"name": "tcp", "version": latest},
            agent_policy_id=pol.id)
        with pytest.raises(KibanaRejected, match=rf"'{pp.id}' .*on agent policy '{pol.id}' \(0 agents\)"):
            gateway.uninstall_package(name="tcp")
        assert gateway.get_package_policy(pp.id).id == pp.id  # the refusal kept it
        gone = gateway.uninstall_package(name="tcp", force=True)
        assert [p.id for p in gone.deleted_package_policies] == [pp.id]
        with pytest.raises(KibanaNotFound):
            gateway.get_package_policy(pp.id)
        with pytest.raises(KibanaNotFound, match="list_installed_packages"):
            gateway.uninstall_package(name="tcp")

        # Premise of the refusal: Kibana itself deletes a package's package
        # policies on an uninstall without force when no agent uses them.
        gateway.install_package(name="tcp")
        pp2 = gateway.create_package_policy(
            name=f"mcp-d51-tcp2-{suffix}", package={"name": "tcp", "version": latest},
            agent_policy_id=pol.id)
        gateway._client.fleet_epm.uninstall_package(pkg_name="tcp")
        with pytest.raises(KibanaNotFound):
            gateway.get_package_policy(pp2.id)
    finally:
        _ensure_uninstalled(gateway, "tcp")
        gateway.delete_agent_policy(agent_policy_id=pol.id, force=True)


def test_uninstall_fleet_server_package_refused_live(gateway):
    # Never forced: were the guard to fail, the package-policy refusal still holds.
    with pytest.raises(KibanaRejected, match=r"the default Fleet Server policy, .*regardless of force"):
        gateway.uninstall_package(name="fleet_server")
    assert any(p.name == "fleet_server" for p in gateway.list_installed_packages())
