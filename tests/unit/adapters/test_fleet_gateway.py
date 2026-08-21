"""Adapter translation tests for the fleet toolbox. Raw bodies mirror shapes
captured from a live Fleet Server. The load-bearing tests are
the SECRET-REDACTION ones: enrollment keys must not carry api_key, and outputs
must not carry ssl/secret fields across the port."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway, _rmw_body
from kibana_mcp.core.errors import KibanaRejected
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

_AGENT = {
    "id": "a1", "status": "online", "policy_id": "fleet-agent-policy", "active": True,
    "enrolled_at": "2026-07-18T08:51:23Z", "last_checkin": "2026-07-18T08:51:57Z",
    "last_checkin_status": "online",
    "local_metadata": {
        "host": {"hostname": "demo-host", "ip": ["10.0.0.1"]},
        "elastic": {"agent": {"version": "9.4.3", "id": "a1"}},
        "os": {"name": "Linux"},
    },
}


def _gw(**namespaces):
    return KibanaPyGateway(make_fake_client(**namespaces))


def test_list_agents_maps_nested_metadata():
    gw = _gw(fleet_agents=SimpleNamespace(
        get_all=lambda **k: FakeResponse({"items": [_AGENT], "total": 1})))
    agents = gw.list_agents()
    assert len(agents) == 1
    a = agents[0]
    assert a.id == "a1" and a.status == "online" and a.policy_id == "fleet-agent-policy"
    assert a.hostname == "demo-host"  # from local_metadata.host.hostname
    assert a.version == "9.4.3"       # from local_metadata.elastic.agent.version
    assert a.active is True


def test_get_agent_status_maps_counts():
    gw = _gw(fleet_agents=SimpleNamespace(
        get_status=lambda **k: FakeResponse(
            {"results": {"online": 2, "error": 1, "offline": 0, "inactive": 0,
                         "updating": 0, "unenrolled": 0, "all": 3}})))
    s = gw.get_agent_status()
    assert s.online == 2 and s.error == 1 and s.total == 3


def test_list_agent_policies_maps():
    body = {"items": [{"id": "p1", "name": "P", "namespace": "default", "description": None,
                       "agents": 2, "status": "active", "is_managed": False,
                       "updated_at": "t", "monitoring_enabled": ["logs", "metrics"]}], "total": 1}
    gw = _gw(fleet_policies=SimpleNamespace(get_agent_policies=lambda **k: FakeResponse(body)))
    p = gw.list_agent_policies()[0]
    assert p.id == "p1" and p.agent_count == 2 and p.monitoring_enabled == ("logs", "metrics")


def test_list_package_policies_maps_package_block():
    body = {"items": [{"id": "pp1", "name": "nginx-1", "namespace": "", "enabled": True,
                       "policy_id": "p1",
                       "package": {"name": "nginx", "title": "Nginx", "version": "1.2.0"}}], "total": 1}
    gw = _gw(fleet_policies=SimpleNamespace(get_package_policies=lambda **k: FakeResponse(body)))
    pp = gw.list_package_policies()[0]
    assert pp.package_name == "nginx" and pp.package_title == "Nginx" and pp.package_version == "1.2.0"
    assert pp.agent_policy_id == "p1"


def test_enrollment_key_redacts_api_key():
    # Raw body carries the secret api_key + api_key_id — they must NOT survive.
    body = {"items": [{"id": "ek1", "name": "Default", "policy_id": "p1", "active": True,
                       "created_at": "t", "api_key": "SUPER-SECRET-VALUE",
                       "api_key_id": "kid", "hidden": False}]}
    gw = _gw(fleet_enrollment=SimpleNamespace(get_keys=lambda **k: FakeResponse(body)))
    key = gw.list_enrollment_keys()[0]
    d = asdict(key)
    assert "api_key" not in d and "api_key_id" not in d
    assert "SUPER-SECRET-VALUE" not in str(d)
    assert d == {"id": "ek1", "name": "Default", "policy_id": "p1", "active": True, "created_at": "t"}


def test_output_redacts_secret_and_ssl_fields():
    # A logstash-ish output with ssl + secrets — only non-secret fields cross.
    body = {"items": [{"id": "o1", "name": "ls", "type": "logstash",
                       "hosts": ["ls:5044"], "is_default": False, "is_default_monitoring": False,
                       "ssl": {"certificate": "PEM..."}, "secrets": {"ssl": {"key": "PRIVKEY"}}}]}
    gw = _gw(fleet_outputs=SimpleNamespace(get_outputs=lambda **k: FakeResponse(body)))
    out = gw.list_outputs()[0]
    d = asdict(out)
    assert "ssl" not in d and "secrets" not in d
    assert "PRIVKEY" not in str(d) and "PEM..." not in str(d)
    assert d["hosts"] == ("ls:5044",) and d["type"] == "logstash"


def test_list_packages_and_installed_map():
    gw = _gw(fleet_epm=SimpleNamespace(
        get_packages=lambda **k: FakeResponse({"items": [
            {"name": "nginx", "title": "Nginx", "version": "1.0.0",
             "status": "not_installed", "description": "d", "type": "integration"}]}),
        get_installed_packages=lambda **k: FakeResponse({"items": [
            {"name": "system", "title": "System", "version": "1.0.0",
             "status": "installed", "description": "d"}], "total": 1})))
    assert gw.list_packages()[0].type == "integration"
    inst = gw.list_installed_packages()[0]
    assert inst.name == "system" and inst.type is None  # installed entries carry no type


def test_list_installed_packages_walks_the_cursor():
    pages = [
        {"items": [{"name": "system", "title": "System", "version": "1.0.0",
                    "status": "installed", "description": "d"}],
         "total": 2, "searchAfter": ["cursor-1"]},
        {"items": [{"name": "nginx", "title": "Nginx", "version": "1.0.0",
                    "status": "installed", "description": "d"}],
         "total": 2},
    ]
    calls = []

    def get_installed_packages(**kw):
        calls.append(kw)
        return FakeResponse(pages[len(calls) - 1])

    gw = _gw(fleet_epm=SimpleNamespace(get_installed_packages=get_installed_packages))
    installed = gw.list_installed_packages()
    assert [p.name for p in installed] == ["system", "nginx"]
    assert calls == [{"per_page": 100}, {"per_page": 100, "search_after": ["cursor-1"]}]


def test_settings_and_permissions_map():
    gw = _gw(fleet=SimpleNamespace(
        get_settings=lambda **k: FakeResponse({"item": {
            "id": "fleet-default-settings", "prerelease_integrations_enabled": False,
            "integration_knowledge_enabled": True,
            "use_space_awareness_migration_status": "success"}}),
        check_permissions=lambda **k: FakeResponse({"success": True})))
    s = gw.get_fleet_settings()
    assert s.id == "fleet-default-settings" and s.integration_knowledge_enabled is True
    assert s.prerelease_integrations_enabled is False
    assert s.space_awareness_migration_status == "success"
    assert gw.check_fleet_permissions().success is True


def test_categories_map():
    gw = _gw(fleet_epm=SimpleNamespace(get_categories=lambda **k: FakeResponse(
        {"items": [{"id": "security", "title": "Security", "count": 42, "parent_id": None}]})))
    c = gw.list_package_categories()[0]
    assert c.id == "security" and c.title == "Security" and c.count == 42


def test_list_agents_paginates_all_pages():
    # A fleet with >100 agents spans pages; the gateway must return every one,
    # not just the first page (regression guard for silent truncation).
    pages = {
        1: {"items": [{"id": f"a{i}"} for i in range(100)], "total": 150},
        2: {"items": [{"id": f"a{i}"} for i in range(100, 150)], "total": 150},
    }
    seen = []

    def get_all(**k):
        seen.append(k["page"])
        return FakeResponse(pages[k["page"]])

    gw = _gw(fleet_agents=SimpleNamespace(get_all=get_all))
    agents = gw.list_agents()
    assert len(agents) == 150 and seen == [1, 2]


def test_output_health_and_server_hosts_map():
    gw = _gw(fleet_outputs=SimpleNamespace(
        get_output_health=lambda **k: FakeResponse(
            {"state": "HEALTHY", "message": "", "timestamp": "t"}),
        get_fleet_server_hosts=lambda **k: FakeResponse(
            {"items": [{"id": "h1", "name": "def", "host_urls": ["http://fs:8220"], "is_default": True}]})))
    assert gw.get_output_health("o1").state == "HEALTHY"
    h = gw.list_fleet_server_hosts()[0]
    assert h.host_urls == ("http://fs:8220",) and h.is_default is True


# --- _rmw_body: tested IN ISOLATION against a real-signature fake. A stubbed
# client method here is always `lambda **k: ...` (see FakeResponse idiom above),
# whose introspected signature is just {"k"} — it cannot stand in as the
# writable-fields allowlist that inspect.signature(update_method) computes
# against the real kibana-py bound method. ---


def _fake_update(*, agent_policy_id, name, namespace, description=None, inactivity_timeout=None): ...


def test_rmw_body_keeps_writable_drops_readonly():
    raw = {"id": "p1", "name": "P", "namespace": "default", "inactivity_timeout": 999,
           "description": "orig", "revision": 7, "updated_at": "t", "package_policies": ["x"]}
    body = _rmw_body(_fake_update, raw, {"name": "P2"})
    assert body == {"name": "P2", "namespace": "default", "inactivity_timeout": 999,
                    "description": "orig"}  # id/revision/updated_at/package_policies dropped (not params)


# --- fleet writes: agent-policy CRUD (#81) ---


def test_create_agent_policy_sends_kwargs_and_maps_dto():
    sent = {}

    def _create(**k):
        sent.update(k)
        return FakeResponse({"item": {"id": "p9", "name": "New", "namespace": "default",
            "description": None, "agents": 0, "status": "active", "is_managed": False,
            "updated_at": "t", "monitoring_enabled": ["logs"]}})

    gw = _gw(fleet_policies=SimpleNamespace(create_agent_policy=_create))
    result = gw.create_agent_policy(
        name="New", namespace="default", description=None,
        monitoring_enabled=["logs"], inactivity_timeout=None)
    assert sent == {"name": "New", "namespace": "default", "description": None,
                    "monitoring_enabled": ["logs"], "inactivity_timeout": None}
    assert result.id == "p9" and result.monitoring_enabled == ("logs",)


def test_update_agent_policy_rmw_merges_and_path_id_overrides_raw_id():
    # raw's own "id" differs from the path kwarg on purpose: the caller must
    # explicitly re-set agent_policy_id from the path, not trust the raw body's id.
    raw_item = {"id": "raw-id-should-not-be-used-as-path", "name": "P", "namespace": "default",
                "description": "orig", "inactivity_timeout": 999, "is_managed": False,
                "revision": 7, "updated_at": "t", "package_policies": ["x"]}
    sent = {}

    def _update(*, agent_policy_id, name, namespace, description=None,
                inactivity_timeout=None, id=None):
        sent.update(dict(agent_policy_id=agent_policy_id, name=name, namespace=namespace,
                          description=description, inactivity_timeout=inactivity_timeout, id=id))
        return FakeResponse({"item": {"id": "p1", "name": name, "namespace": namespace,
            "description": description, "agents": 0, "status": "active", "is_managed": False,
            "updated_at": "t2", "monitoring_enabled": []}})

    gw = _gw(fleet_policies=SimpleNamespace(
        get_agent_policy=lambda **k: FakeResponse({"item": raw_item}),
        update_agent_policy=_update))
    result = gw.update_agent_policy(agent_policy_id="p1", changes={"name": "P2"})
    assert sent == {"agent_policy_id": "p1", "name": "P2", "namespace": "default",
                    "description": "orig", "inactivity_timeout": 999,
                    "id": "raw-id-should-not-be-used-as-path"}  # id retained (a real kwarg), harmless
    assert result.name == "P2" and result.updated_at == "t2"


def test_update_agent_policy_refuses_managed():
    gw = _gw(fleet_policies=SimpleNamespace(
        get_agent_policy=lambda **k: FakeResponse({"item": {"id": "p1", "is_managed": True,
            "name": "P", "namespace": "default"}})))
    with pytest.raises(KibanaRejected):
        gw.update_agent_policy(agent_policy_id="p1", changes={"name": "x"})


def test_delete_agent_policy_refuses_managed():
    gw = _gw(fleet_policies=SimpleNamespace(
        get_agent_policy=lambda **k: FakeResponse({"item": {"id": "p1", "is_managed": True,
            "name": "P", "namespace": "default"}})))
    with pytest.raises(KibanaRejected):
        gw.delete_agent_policy(agent_policy_id="p1")


def test_delete_agent_policy_refuses_fleet_server():
    gw = _gw(fleet_policies=SimpleNamespace(
        get_agent_policy=lambda **k: FakeResponse({"item": {"id": "fs",
            "is_default_fleet_server": True, "name": "F", "namespace": "default"}})))
    with pytest.raises(KibanaRejected):
        gw.delete_agent_policy(agent_policy_id="fs")


def test_delete_agent_policy_happy_path_returns_none_and_passes_force():
    sent = {}

    def _delete(**k):
        sent.update(k)
        return FakeResponse({})

    gw = _gw(fleet_policies=SimpleNamespace(
        get_agent_policy=lambda **k: FakeResponse({"item": {"id": "p1", "is_managed": False,
            "is_default_fleet_server": False, "name": "P", "namespace": "default"}}),
        delete_agent_policy=_delete))
    result = gw.delete_agent_policy(agent_policy_id="p1", force=True)
    assert result is None
    assert sent == {"agent_policy_id": "p1", "force": True}


def test_delete_agent_policy_force_false_sends_none_not_false():
    sent = {}

    def _delete(**k):
        sent.update(k)
        return FakeResponse({})

    gw = _gw(fleet_policies=SimpleNamespace(
        get_agent_policy=lambda **k: FakeResponse({"item": {"id": "p1", "is_managed": False,
            "is_default_fleet_server": False, "name": "P", "namespace": "default"}}),
        delete_agent_policy=_delete))
    gw.delete_agent_policy(agent_policy_id="p1")
    assert sent["force"] is None


# --- fleet writes: package-policy CRUD (#81) ---


def test_create_package_policy_maps_policy_id():
    sent = {}

    def _create(**k):
        sent.update(k)
        return FakeResponse({"item": {"id": "pp1", "name": "n"}})

    gw = _gw(fleet_policies=SimpleNamespace(create_package_policy=_create))
    gw.create_package_policy(
        name="n", package={"name": "fleet_server", "version": "1.6.1"},
        agent_policy_id="ap1", inputs={})
    assert sent["policy_id"] == "ap1"
    assert sent["package"]["name"] == "fleet_server"


def test_update_package_policy_rmw_strips_compiled():
    raw_item = {
        "id": "pp1", "name": "n", "namespace": "default", "enabled": True,
        "policy_id": "ap1", "package": {"name": "fleet_server", "version": "1.6.1"},
        "description": None,
        "inputs": [{
            "type": "x", "compiled_input": {"secret": "computed"},
            "streams": [{"enabled": True, "compiled_stream": {"secret": "computed"}}],
        }],
        "revision": 3, "updated_at": "t",
    }
    sent = {}

    def _update(*, package_policy_id, package, name=None, namespace=None, enabled=None,
                policy_id=None, description=None, inputs=None, id=None):
        sent.update(dict(package_policy_id=package_policy_id, package=package, name=name,
                          namespace=namespace, enabled=enabled, policy_id=policy_id,
                          description=description, inputs=inputs, id=id))
        return FakeResponse({"item": {**raw_item, "name": name}})

    gw = _gw(fleet_policies=SimpleNamespace(
        get_package_policy=lambda **k: FakeResponse({"item": raw_item}),
        update_package_policy=_update))
    result = gw.update_package_policy(package_policy_id="pp1", changes={"name": "n2"})
    sent_input = sent["inputs"][0]
    assert "compiled_input" not in sent_input
    assert sent_input["type"] == "x"
    sent_stream = sent_input["streams"][0]
    assert "compiled_stream" not in sent_stream
    assert sent_stream["enabled"] is True
    assert sent["package_policy_id"] == "pp1"
    # Real Kibana 400s on a body-level "id" here (redundant with the path's
    # package_policy_id) — confirmed live; the raw GET's "id" must NOT survive
    # into the update body, unlike update_agent_policy where it's harmless.
    assert sent["id"] is None
    assert result.name == "n2"


def test_update_package_policy_maps_agent_policy_id_to_policy_id():
    # agent_policy_id is a friendly tool-facing name only — _rmw_body's allowlist
    # is the REAL kibana-py kwargs, which is "policy_id" not "agent_policy_id".
    # Without translation this silently no-ops (the finding this test guards).
    raw_item = {
        "id": "pp1", "name": "n", "namespace": "default", "enabled": True,
        "policy_id": "ap-old", "package": {"name": "fleet_server", "version": "1.6.1"},
        "description": None, "revision": 3, "updated_at": "t",
    }
    sent = {}

    def _update(*, package_policy_id, package, name=None, namespace=None, enabled=None,
                policy_id=None, description=None, inputs=None, id=None):
        sent.update(dict(package_policy_id=package_policy_id, package=package, name=name,
                          namespace=namespace, enabled=enabled, policy_id=policy_id,
                          description=description, inputs=inputs, id=id))
        return FakeResponse({"item": {**raw_item, "name": name, "policy_id": policy_id}})

    gw = _gw(fleet_policies=SimpleNamespace(
        get_package_policy=lambda **k: FakeResponse({"item": raw_item}),
        update_package_policy=_update))
    changes = {"agent_policy_id": "ap-new", "name": "renamed"}
    result = gw.update_package_policy(package_policy_id="pp1", changes=changes)
    assert sent["policy_id"] == "ap-new"
    assert sent["name"] == "renamed"
    assert "agent_policy_id" not in sent
    assert changes == {"agent_policy_id": "ap-new", "name": "renamed"}  # caller's dict untouched
    assert result.name == "renamed" and result.agent_policy_id == "ap-new"


def test_delete_package_policy_forwards_force():
    sent = {}

    def _delete(**k):
        sent.update(k)
        return FakeResponse({})

    gw = _gw(fleet_policies=SimpleNamespace(delete_package_policy=_delete))
    result = gw.delete_package_policy(package_policy_id="pp1", force=True)
    assert result is None
    assert sent == {"package_policy_id": "pp1", "force": True}


# --- fleet writes: output CRUD (#81) ---


def test_create_output_result_redacted():
    # The stub echoes ssl/secrets back in the created item — they must NOT
    # survive the _to_fleet_output mapper (proves create reuses it, not a raw pass-through).
    def _create(**k):
        return FakeResponse({"item": {
            "id": "o9", "name": "ls", "type": "logstash", "hosts": ["ls:5044"],
            "is_default": False, "is_default_monitoring": False,
            "ssl": {"certificate": "PEM..."}, "secrets": {"ssl": {"key": "PRIVKEY"}},
        }})

    gw = _gw(fleet_outputs=SimpleNamespace(create_output=_create))
    result = gw.create_output(name="ls", type="logstash", hosts=["ls:5044"])
    d = asdict(result)
    assert "ssl" not in d and "secrets" not in d
    assert "PRIVKEY" not in str(d) and "PEM..." not in str(d)
    assert result.id == "o9" and result.hosts == ("ls:5044",)


def test_delete_output_refuses_default():
    gw = _gw(fleet_outputs=SimpleNamespace(
        get_output=lambda **k: FakeResponse({"item": {"id": "d", "is_default": True}})))
    with pytest.raises(KibanaRejected):
        gw.delete_output(output_id="d")


def test_update_output_default_requires_confirm():
    raw_item = {"id": "d", "name": "Default", "type": "elasticsearch", "hosts": ["h1"],
                "is_default": True, "is_default_monitoring": False}
    sent = {}

    def _update(*, output_id, name=None, type=None, hosts=None,
                is_default=None, is_default_monitoring=None, id=None):
        sent.update(dict(output_id=output_id, name=name, type=type, hosts=hosts,
                          is_default=is_default, is_default_monitoring=is_default_monitoring, id=id))
        return FakeResponse({"item": {**raw_item, "hosts": hosts}})

    gw = _gw(fleet_outputs=SimpleNamespace(
        get_output=lambda **k: FakeResponse({"item": raw_item}),
        update_output=_update))
    with pytest.raises(KibanaRejected):
        gw.update_output(output_id="d", changes={"hosts": ["h2"]}, confirm=False)
    assert sent == {}  # refused before ever calling the update method

    result = gw.update_output(output_id="d", changes={"hosts": ["h2"]}, confirm=True)
    assert sent["output_id"] == "d" and sent["hosts"] == ["h2"]
    assert result.hosts == ("h2",)


# --- fleet writes: agent lifecycle, single + bulk (#81) ---

_VALID_TARGET = SimpleNamespace(get_agent_policy=lambda **k: FakeResponse(
    {"item": {"id": "t1", "is_managed": False, "is_default_fleet_server": False,
              "name": "T", "namespace": "default"}}))


def test_reassign_refuses_managed_target():
    gw = _gw(fleet_policies=SimpleNamespace(get_agent_policy=lambda **k: FakeResponse(
        {"item": {"id": "t1", "is_managed": True, "name": "T", "namespace": "default"}})))
    with pytest.raises(KibanaRejected):
        gw.reassign_agent(agent_id="a1", policy_id="t1")


def test_reassign_returns_none_and_calls():
    sent = {}

    def _reassign(**k):
        sent.update(k)
        return FakeResponse({})

    gw = _gw(fleet_policies=_VALID_TARGET, fleet_agents=SimpleNamespace(reassign=_reassign))
    result = gw.reassign_agent(agent_id="a1", policy_id="t1")
    assert result is None
    assert sent == {"agent_id": "a1", "policy_id": "t1"}


def test_upgrade_forwards_version_source_uri():
    # No target guard: get_agent_policy is never called for upgrade.
    sent = {}

    def _upgrade(**k):
        sent.update(k)
        return FakeResponse({})

    gw = _gw(fleet_agents=SimpleNamespace(upgrade=_upgrade))
    result = gw.upgrade_agent(agent_id="a1", version="9.4.3", source_uri="https://example/agent")
    assert result is None
    assert sent == {"agent_id": "a1", "version": "9.4.3", "source_uri": "https://example/agent"}


def test_unenroll_forwards_force_revoke():
    sent = {}

    def _unenroll(**k):
        sent.update(k)
        return FakeResponse({})

    gw = _gw(fleet_agents=SimpleNamespace(unenroll=_unenroll))
    result = gw.unenroll_agent(agent_id="a1", force=True, revoke=True)
    assert result is None
    assert sent == {"agent_id": "a1", "force": True, "revoke": True}


def test_bulk_reassign_returns_action_id():
    sent = {}

    def _bulk_reassign(**k):
        sent.update(k)
        return FakeResponse({"actionId": "act-9"})

    gw = _gw(fleet_policies=_VALID_TARGET, fleet_agents=SimpleNamespace(bulk_reassign=_bulk_reassign))
    result = gw.bulk_reassign(agent_ids=["a1", "a2"], policy_id="t1")
    assert result == "act-9"
    assert sent == {"agents": ["a1", "a2"], "policy_id": "t1"}


def test_bulk_upgrade_forwards_and_returns_action_id():
    sent = {}

    def _bulk_upgrade(**k):
        sent.update(k)
        return FakeResponse({"actionId": "act-10"})

    gw = _gw(fleet_agents=SimpleNamespace(bulk_upgrade=_bulk_upgrade))
    result = gw.bulk_upgrade(agent_ids=["a1", "a2"], version="9.4.3", source_uri="https://example/agent")
    assert result == "act-10"
    assert sent == {"agents": ["a1", "a2"], "version": "9.4.3", "source_uri": "https://example/agent"}


def test_bulk_unenroll_forwards_force_revoke():
    sent = {}

    def _bulk_unenroll(**k):
        sent.update(k)
        return FakeResponse({"actionId": "act-11"})

    gw = _gw(fleet_agents=SimpleNamespace(bulk_unenroll=_bulk_unenroll))
    result = gw.bulk_unenroll(agent_ids=["a1", "a2"], force=True, revoke=True)
    assert result == "act-11"
    assert sent == {"agents": ["a1", "a2"], "force": True, "revoke": True}
