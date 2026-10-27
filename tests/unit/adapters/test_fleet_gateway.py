"""Adapter translation tests for the fleet toolbox. Raw bodies mirror shapes
captured from a live Fleet Server. The load-bearing tests are
the SECRET-REDACTION ones: enrollment keys must not carry api_key, and outputs
must not carry ssl/secret fields across the port."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest
from kibana.exceptions import BadRequestError, NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway, _rmw_body
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected
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


def _rmw_output_gw(raw_item, sent):
    def _update(*, output_id, name=None, type=None, hosts=None, is_default=None,
                is_default_monitoring=None, config_yaml=None, preset=None, fields=None):
        sent.update({k: v for k, v in dict(
            output_id=output_id, name=name, type=type, hosts=hosts, is_default=is_default,
            is_default_monitoring=is_default_monitoring, config_yaml=config_yaml,
            preset=preset, fields=fields).items() if v is not None})
        return FakeResponse({"item": {**raw_item, "id": output_id}})

    return _gw(fleet_outputs=SimpleNamespace(
        get_output=lambda **k: FakeResponse({"item": raw_item}), update_output=_update))


_REMOTE_ES = {"id": "r", "name": "Remote", "type": "remote_elasticsearch",
              "hosts": ["https://r:9200"], "service_token": "tok",
              "is_default": False, "is_default_monitoring": False}


def test_update_output_sends_the_stored_service_token_back():
    # A PUT carrying `type` made Fleet null a remote_elasticsearch output's
    # service_token, which is no kibana-py parameter; leaving `type` out made a
    # promotion take 68 s instead of 3 (9.5.4, D59). So both go back.
    sent = {}
    _rmw_output_gw(_REMOTE_ES, sent).update_output(output_id="r", changes={"name": "x"})
    assert sent["type"] == "remote_elasticsearch"
    assert sent["fields"] == {"service_token": "tok"}
    assert sent["name"] == "x" and sent["hosts"] == ["https://r:9200"]


def test_update_output_type_change_carries_no_service_token():
    sent = {}
    _rmw_output_gw(_REMOTE_ES, sent).update_output(
        output_id="r", changes={"type": "elasticsearch"})
    assert sent["type"] == "elasticsearch" and "fields" not in sent


@pytest.mark.parametrize("token", ["absent", None])
def test_update_output_without_a_token_sends_no_fields(token):
    # Fleet reads back service_token: null on an output a type change reset.
    raw = {k: v for k, v in _REMOTE_ES.items() if k != "service_token"}
    raw["type"] = "elasticsearch"
    if token != "absent":
        raw["service_token"] = token
    sent = {}
    _rmw_output_gw(raw, sent).update_output(output_id="r", changes={"name": "x"})
    assert sent["type"] == "elasticsearch" and "fields" not in sent


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


# --- fleet advanced ops (D51): agent tags, diagnostics, package install/uninstall.
# Bodies mirror Kibana 9.5.4 (probed 2026-10-25). ---


def _not_found(message):
    return NotFoundError(message, meta=SimpleNamespace(status=404), body=None)


def _agents_ns(tags, calls, **extra):
    """fleet_agents with get/bulk_update_tags over a {id: [tags]} store; Kibana
    applies an id-list tag update before it answers (probed), so does this."""
    def get(*, agent_id):
        if agent_id not in tags:
            raise _not_found(f"Agent {agent_id} not found")
        return FakeResponse({"item": {**_AGENT, "id": agent_id, "tags": tags[agent_id]}})

    def bulk_update_tags(**kw):
        calls.append(kw)
        for agent_id in kw["agents"]:
            if agent_id in tags and agent_id not in extra.get("skip", ()):
                kept = [t for t in tags[agent_id] if t not in kw.get("tags_to_remove", [])]
                tags[agent_id] = kept + [t for t in kw.get("tags_to_add", []) if t not in kept]
        return FakeResponse({"actionId": "act-tags"})

    return SimpleNamespace(get=get, bulk_update_tags=bulk_update_tags)


def test_agent_maps_tags():
    gw = _gw(fleet_agents=SimpleNamespace(
        get=lambda **k: FakeResponse({"item": {**_AGENT, "tags": ["prod", "linux"]}})))
    assert gw.get_agent("a1").tags == ("prod", "linux")
    # An agent never tagged has no `tags` key (probed): an empty tuple, not None.
    gw = _gw(fleet_agents=SimpleNamespace(get=lambda **k: FakeResponse({"item": _AGENT})))
    assert gw.get_agent("a1").tags == ()


def test_list_agent_tags_includes_inactive_agents():
    sent = {}

    def get_tags(**k):
        sent.update(k)
        return FakeResponse({"items": ["prod", "linux"]})

    gw = _gw(fleet_agents=SimpleNamespace(get_tags=get_tags))
    assert gw.list_agent_tags() == ["prod", "linux"]
    assert sent == {"show_inactive": True}


def test_update_agent_tags_sends_ids_and_reads_back():
    tags, calls = {"a1": ["old"], "a2": []}, []
    gw = _gw(fleet_agents=_agents_ns(tags, calls))
    r = gw.update_agent_tags(agent_ids=["a1", "a2", "a1"], tags_to_add=["prod"], tags_to_remove=["old"])
    assert calls == [{"agents": ["a1", "a2"], "tags_to_add": ["prod"], "tags_to_remove": ["old"]}]
    assert r.action_id == "act-tags"
    assert [(c.agent_id, c.previous_tags, c.tags) for c in r.agents] == [
        ("a1", ("old",), ("prod",)), ("a2", (), ("prod",))]
    assert r.not_applied_agent_ids == ()


def test_update_agent_tags_sends_only_the_lists_given():
    tags, calls = {"a1": ["old"]}, []
    gw = _gw(fleet_agents=_agents_ns(tags, calls))
    gw.update_agent_tags(agent_ids=["a1"], tags_to_add=[], tags_to_remove=["old"])
    assert calls == [{"agents": ["a1"], "tags_to_remove": ["old"]}]


def test_update_agent_tags_refuses_unknown_ids_before_sending():
    # Kibana answers 200 and skips an unknown id (probed: 2 of 3 updated).
    tags, calls = {"a1": []}, []
    gw = _gw(fleet_agents=_agents_ns(tags, calls))
    with pytest.raises(KibanaNotFound, match=(
        r"^no agent has agent ids 'nope-1', 'nope-2' — Kibana would skip them without an "
        r"error; list_agents shows the enrolled agents' ids\. Nothing was changed\.$"
    )):
        gw.update_agent_tags(agent_ids=["nope-1", "a1", "nope-2"], tags_to_add=["x"], tags_to_remove=[])
    assert calls == [] and tags == {"a1": []}


def test_update_agent_tags_reports_an_agent_kibana_did_not_update():
    tags, calls = {"a1": [], "hosted": ["keep"]}, []
    gw = _gw(fleet_agents=_agents_ns(tags, calls, skip={"hosted"}))
    r = gw.update_agent_tags(agent_ids=["a1", "hosted"], tags_to_add=["x"], tags_to_remove=["keep"])
    assert r.not_applied_agent_ids == ("hosted",)
    assert r.agents[1].previous_tags == r.agents[1].tags == ("keep",)


def test_request_agent_diagnostics_reads_status_and_sends_cpu_only_when_asked():
    sent = []

    def request_diagnostics(**k):
        sent.append(k)
        return FakeResponse({"actionId": "act-diag"})

    agents = SimpleNamespace(
        get=lambda **k: FakeResponse({"item": {**_AGENT, "status": "offline"}}),
        request_diagnostics=request_diagnostics)
    gw = _gw(fleet_agents=agents)
    r = gw.request_agent_diagnostics(agent_id="a1")
    assert (r.action_id, r.agent_id, r.agent_status) == ("act-diag", "a1", "offline")
    gw.request_agent_diagnostics(agent_id="a1", include_cpu_metrics=True)
    assert sent == [{"agent_id": "a1"}, {"agent_id": "a1", "additional_metrics": ["CPU"]}]


def test_request_agent_diagnostics_unknown_agent_names_list_agents():
    def get(**k):
        raise _not_found("Agent nope not found")

    def request_diagnostics(**k):
        raise AssertionError("must not be sent")

    gw = _gw(fleet_agents=SimpleNamespace(get=get, request_diagnostics=request_diagnostics))
    with pytest.raises(KibanaNotFound, match=(
        r"^agent 'nope' not found — list_agents shows the enrolled agents' ids$")):
        gw.request_agent_diagnostics(agent_id="nope")


def test_list_agent_diagnostics_maps_metadata_only():
    body = {"items": [{
        "actionId": "act-diag", "id": "act-diag.a1", "status": "READY",
        "name": "elastic-agent-diagnostics-2026-10-25T09-21-54Z-00.zip",
        "createTime": "2026-10-25T09:21:54.697Z",
        "filePath": "/api/fleet/agents/files/act-diag.a1/elastic-agent-diagnostics.zip"}]}
    gw = _gw(fleet_agents=SimpleNamespace(get_uploads=lambda **k: FakeResponse(body)))
    (d,) = gw.list_agent_diagnostics("a1")
    assert asdict(d) == {
        "id": "act-diag.a1", "name": "elastic-agent-diagnostics-2026-10-25T09-21-54Z-00.zip",
        "status": "READY", "created_at": "2026-10-25T09:21:54.697Z", "action_id": "act-diag"}

    def missing(**k):
        raise _not_found("Agent nope not found")

    gw = _gw(fleet_agents=SimpleNamespace(get_uploads=missing))
    with pytest.raises(KibanaNotFound, match=r"list_agents shows the enrolled agents' ids"):
        gw.list_agent_diagnostics("nope")


def _epm(item, calls, *, install=None, uninstall=None):
    def get_package(*, pkg_name):
        if item is None:
            raise _not_found(f"[{pkg_name}] package not installed or found in registry")
        return FakeResponse({"item": dict(item)})

    def install_package(**k):
        calls.append(("install", k))
        if install is not None:
            install(**k)
        item.update(status="installed", version=k.get("pkg_version", item["latestVersion"]))
        return FakeResponse({"items": [], "_meta": {"install_source": "registry", "name": k["pkg_name"]}})

    def uninstall_package(**k):
        calls.append(("uninstall", k))
        if uninstall is not None:
            uninstall(**k)
        return FakeResponse({"items": []})

    return SimpleNamespace(
        get_package=get_package, install_package=install_package, uninstall_package=uninstall_package)


_TCP = {"name": "tcp", "version": "2.3.1", "latestVersion": "2.3.1", "status": "not_installed"}


def test_install_package_installs_and_reads_back():
    item, calls = dict(_TCP), []
    gw = _gw(fleet_epm=_epm(item, calls))
    r = gw.install_package(name="tcp")
    assert calls == [("install", {"pkg_name": "tcp"})]
    assert asdict(r) == {"name": "tcp", "version": "2.3.1", "status": "installed",
                         "already_installed": False}


def test_install_package_forwards_the_version():
    item, calls = dict(_TCP), []
    gw = _gw(fleet_epm=_epm(item, calls))
    assert gw.install_package(name="tcp", version="2.3.1").version == "2.3.1"
    assert calls == [("install", {"pkg_name": "tcp", "pkg_version": "2.3.1"})]


@pytest.mark.parametrize("version", [None, "2.2.0"])
def test_install_package_already_installed_sends_nothing(version):
    # item.version is the installed version once installed (probed: 2.2.0 beside
    # latestVersion 2.3.1); without a version Kibana would UPGRADE it (probed).
    item = {**_TCP, "status": "installed", "version": "2.2.0"}
    calls = []
    gw = _gw(fleet_epm=_epm(item, calls))
    r = gw.install_package(name="tcp", version=version)
    assert calls == [] and r.already_installed is True and r.version == "2.2.0"


def test_install_package_refuses_another_version_of_an_installed_package():
    item, calls = {**_TCP, "status": "installed", "version": "2.2.0"}, []
    gw = _gw(fleet_epm=_epm(item, calls))
    with pytest.raises(KibanaRejected, match=(
        r"^integration package 'tcp' is installed at version 2\.2\.0, not 2\.3\.1; "
        r"install_package only installs a package that is not installed — an upgrade "
        r"cannot be undone on a Basic license")):
        gw.install_package(name="tcp", version="2.3.1")
    assert calls == []


def test_install_package_unknown_name_names_list_packages():
    calls = []
    gw = _gw(fleet_epm=_epm(None, calls))
    with pytest.raises(KibanaNotFound, match=(
        r"^integration package 'nope' not found in the registry — list_packages shows")):
        gw.install_package(name="nope")
    assert calls == []


def test_install_package_unknown_version_names_the_latest():
    def install(**k):
        raise _not_found("tcp@99.0.0 not found")

    gw = _gw(fleet_epm=_epm(dict(_TCP), [], install=install))
    with pytest.raises(KibanaNotFound, match=(
        r"^integration package 'tcp' has no version '99\.0\.0' in the registry — omit "
        r"version to install the latest \(2\.3\.1\)$")):
        gw.install_package(name="tcp", version="99.0.0")


def test_install_package_out_of_date_version_names_the_latest():
    def install(**k):
        raise BadRequestError("tcp-2.2.0 is out-of-date and cannot be installed or updated",
                              meta=SimpleNamespace(status=400), body=None)

    gw = _gw(fleet_epm=_epm(dict(_TCP), [], install=install))
    with pytest.raises(KibanaRejected, match=(
        r"^Kibana installs only the latest version of 'tcp' \(2\.3\.1\), not '2\.2\.0'")):
        gw.install_package(name="tcp", version="2.2.0")


def _pp(pp_id, pkg, policy_ids):
    return {"id": pp_id, "name": f"{pkg}-{pp_id}", "namespace": "", "enabled": True,
            "policy_id": policy_ids[0] if policy_ids else None, "policy_ids": policy_ids,
            "package": {"name": pkg, "title": pkg, "version": "2.3.1"}}


def _policies_ns(package_policies, agent_policies):
    return SimpleNamespace(
        get_package_policies=lambda **k: FakeResponse(
            {"items": package_policies, "total": len(package_policies)}),
        get_agent_policies=lambda **k: FakeResponse(
            {"items": list(agent_policies.values()), "total": len(agent_policies)}),
        get_agent_policy=lambda *, agent_policy_id, **k: FakeResponse(
            {"item": agent_policies[agent_policy_id]}))


_INSTALLED_TCP = {**_TCP, "status": "installed"}


def _ugw(*, migration_status="", spaces=("default",), pinned_space=None, **namespaces):
    """A gateway for uninstall_package: Fleet settings with space awareness off
    unless `migration_status` says otherwise ('success' on the dev stack), and a
    spaces listing of `spaces` (what the API key can read)."""
    fleet = SimpleNamespace(get_settings=lambda: FakeResponse(
        {"item": {"id": "fleet-default-settings",
                  "use_space_awareness_migration_status": migration_status}}))
    space_ns = SimpleNamespace(get_all=lambda **k: FakeResponse([{"id": s} for s in spaces]))
    return KibanaPyGateway(make_fake_client(fleet=fleet, spaces=space_ns, **namespaces),
                           pinned_space=pinned_space)


def _recording_policies_ns(package_policies, agent_policies, calls):
    """_policies_ns that records each call's name and keyword arguments."""
    inner = _policies_ns(package_policies, agent_policies)

    def rec(name):
        def call(**k):
            calls.append((name, k))
            return getattr(inner, name)(**k)
        return call

    return SimpleNamespace(**{n: rec(n) for n in (
        "get_package_policies", "get_agent_policies", "get_agent_policy")})


def test_uninstall_package_without_package_policies_sends_no_force():
    calls = []
    gw = _ugw(fleet_epm=_epm(dict(_INSTALLED_TCP), calls),
             fleet_policies=_policies_ns([_pp("pp-x", "nginx", ["p1"])], {}))
    r = gw.uninstall_package(name="tcp")
    assert calls == [("uninstall", {"pkg_name": "tcp", "force": None})]
    assert (r.name, r.version, r.deleted_package_policies) == ("tcp", "2.3.1", ())


def test_uninstall_package_never_sends_force_without_package_policies():
    # Kibana's force is only there to delete package policies agents use.
    calls = []
    gw = _ugw(fleet_epm=_epm(dict(_INSTALLED_TCP), calls), fleet_policies=_policies_ns([], {}))
    gw.uninstall_package(name="tcp", force=True)
    assert calls == [("uninstall", {"pkg_name": "tcp", "force": None})]


def test_uninstall_package_refuses_package_policies_without_force():
    calls = []
    pps = [_pp("pp-1", "tcp", ["p1"]), _pp("pp-2", "tcp", ["p1", "p2"])]
    aps = {"p1": {"id": "p1", "agents": 1}, "p2": {"id": "p2", "agents": 0}}
    gw = _ugw(fleet_epm=_epm(dict(_INSTALLED_TCP), calls), fleet_policies=_policies_ns(pps, aps))
    with pytest.raises(KibanaRejected, match=(
        r"^refusing to uninstall integration package 'tcp': it has 2 package policies — "
        r"'pp-1' \('tcp-pp-1'\) on agent policy 'p1' \(1 agent\); 'pp-2' \('tcp-pp-2'\) on "
        r"agent policy 'p1' \(1 agent\); 'pp-2' \('tcp-pp-2'\) on agent policy 'p2' \(0 agents\) "
        r"— and Kibana deletes them with the package.*delete_package_policy, or pass "
        r"force=True")):
        gw.uninstall_package(name="tcp")
    assert calls == []


def test_uninstall_package_force_deletes_and_reports_the_package_policies():
    calls = []
    pps = [_pp("pp-1", "tcp", ["p1"])]
    gw = _ugw(fleet_epm=_epm(dict(_INSTALLED_TCP), calls),
             fleet_policies=_policies_ns(pps, {"p1": {"id": "p1", "agents": 1}}))
    r = gw.uninstall_package(name="tcp", force=True)
    # force reaches Kibana too: it 400s package policies agents use without it (probed).
    assert calls == [("uninstall", {"pkg_name": "tcp", "force": True})]
    assert [pp.id for pp in r.deleted_package_policies] == ["pp-1"]
    assert r.deleted_package_policies[0].agent_policy_id == "p1"


@pytest.mark.parametrize(("flags", "why"), [
    ({"is_managed": True}, "managed"),
    ({"is_default_fleet_server": True}, "the default Fleet Server policy"),
])
def test_uninstall_package_refuses_a_guarded_agent_policy_even_with_force(flags, why):
    calls = []
    pps = [_pp("pp-fs", "fleet_server", ["fleet-server-policy"])]
    aps = {"fleet-server-policy": {"id": "fleet-server-policy", "agents": 1, **flags}}
    gw = _ugw(fleet_epm=_epm({**_INSTALLED_TCP, "name": "fleet_server"}, calls),
             fleet_policies=_policies_ns(pps, aps))
    with pytest.raises(KibanaRejected, match=(
        rf"^refusing to uninstall integration package 'fleet_server': its package policy "
        rf"'pp-fs' is on agent policy 'fleet-server-policy', which is {why}, .*regardless of "
        rf"force\.$")):
        gw.uninstall_package(name="fleet_server", force=True)
    assert calls == []


def test_uninstall_package_refuses_fleet_server_on_any_fleet_server_policy_even_with_force():
    # A user-made Fleet Server policy: neither managed nor the default one.
    calls = []
    pps = [_pp("pp-fs2", "fleet_server", ["fs-2"])]
    gw = _ugw(fleet_epm=_epm({**_INSTALLED_TCP, "name": "fleet_server"}, calls),
             fleet_policies=_policies_ns(pps, {"fs-2": {"id": "fs-2", "agents": 1}}))
    with pytest.raises(KibanaRejected, match=(
        r"^refusing to uninstall integration package 'fleet_server': its package policy "
        r"'pp-fs2' is on agent policy 'fs-2', which is a Fleet Server policy \(that package "
        r"policy runs its Fleet Servers\), .*regardless of force\. .*delete_package_policy "
        r"once no Fleet Server is enrolled on that agent policy\.$")):
        gw.uninstall_package(name="fleet_server", force=True)
    assert calls == []


def test_uninstall_package_names_every_guarded_package_policy():
    calls = []
    pps = [_pp("pp-fs", "fleet_server", ["fleet-server-policy"]), _pp("pp-fs2", "fleet_server", ["fs-2"])]
    aps = {"fleet-server-policy": {"id": "fleet-server-policy", "is_default_fleet_server": True},
           "fs-2": {"id": "fs-2"}}
    gw = _ugw(fleet_epm=_epm({**_INSTALLED_TCP, "name": "fleet_server"}, calls),
             fleet_policies=_policies_ns(pps, aps))
    with pytest.raises(KibanaRejected, match=(
        r"'pp-fs' is on agent policy 'fleet-server-policy', which is the default Fleet Server "
        r"policy; its package policy 'pp-fs2' is on agent policy 'fs-2', which is a Fleet Server")):
        gw.uninstall_package(name="fleet_server")
    assert calls == []


def test_uninstall_package_refuses_elastic_agent_while_an_agent_policy_monitors():
    # Probed: Kibana uninstalls it under a monitoring agent policy, no force needed.
    calls = []
    aps = {"p1": {"id": "p1", "monitoring_enabled": []},
           "p2": {"id": "p2", "monitoring_enabled": ["logs", "metrics"]},
           "p3": {"id": "p3", "monitoring_enabled": ["metrics"]}}
    gw = _ugw(fleet_epm=_epm({**_INSTALLED_TCP, "name": "elastic_agent"}, calls),
             fleet_policies=_policies_ns([], aps))
    with pytest.raises(KibanaRejected, match=(
        r"^refusing to uninstall integration package 'elastic_agent': agent monitoring is on "
        r"in agent policies 'p2', 'p3', .*regardless of force — turn monitoring off on each "
        r"first with update_agent_policy monitoring_enabled=\[\]")):
        gw.uninstall_package(name="elastic_agent", force=True)
    assert calls == []


def test_uninstall_package_sends_elastic_agent_when_no_agent_policy_monitors():
    calls = []
    gw = _ugw(fleet_epm=_epm({**_INSTALLED_TCP, "name": "elastic_agent"}, calls),
             fleet_policies=_policies_ns([], {"p1": {"id": "p1", "monitoring_enabled": []}}))
    gw.uninstall_package(name="elastic_agent", force=True)
    assert calls == [("uninstall", {"pkg_name": "elastic_agent", "force": None})]


def test_uninstall_package_not_installed_names_list_installed_packages():
    calls = []
    gw = _ugw(fleet_epm=_epm(dict(_TCP), calls), fleet_policies=_policies_ns([], {}))
    with pytest.raises(KibanaNotFound, match=(
        r"^integration package 'tcp' is not installed — list_installed_packages shows")):
        gw.uninstall_package(name="tcp")
    assert calls == []


def test_uninstall_package_refuses_fleet_server_on_no_agent_policy_even_with_force():
    # Every fleet_server package policy is guarded, an unattached one too.
    calls = []
    gw = _ugw(fleet_epm=_epm({**_INSTALLED_TCP, "name": "fleet_server"}, calls),
              fleet_policies=_policies_ns([_pp("pp-loose", "fleet_server", [])], {}))
    with pytest.raises(KibanaRejected, match=(
        r"^refusing to uninstall integration package 'fleet_server': its package policy "
        r"'pp-loose' is a fleet_server package policy on no agent policy, .*regardless of "
        r"force\. A fleet_server package policy on no agent policy, or on one .*"
        r"delete_package_policy once no Fleet Server is enrolled on that agent policy\.$")):
        gw.uninstall_package(name="fleet_server", force=True)
    assert calls == []


def test_uninstall_package_reads_the_calling_space_only_when_space_awareness_is_off():
    # Off, Fleet's policies are not space-scoped: one listing holds them all.
    reads = []
    pps = [_pp("pp-1", "tcp", ["p1"])]
    gw = _ugw(fleet_epm=_epm(dict(_INSTALLED_TCP), []), spaces=("default", "ops"),
              fleet_policies=_recording_policies_ns(pps, {"p1": {"id": "p1"}}, reads))
    with pytest.raises(KibanaRejected, match=r"it has 1 package policy"):
        gw.uninstall_package(name="tcp")
    assert [n for n, _ in reads] == ["get_package_policies", "get_agent_policy"]
    assert not any("space_id" in k for _, k in reads)


_RESTRICTED_LISTINGS = [
    # (spaces the key's listing shows, KIBANA_URL's pinned space): probed on
    # 9.5.4 — a key restricted to 'default' listed only 'default' while space
    # 'ops' held a tcp package policy, and Kibana's uninstall with that key,
    # no force, deleted it. A listing never shows what it leaves out.
    (("default",), None),
    (("ops",), "ops"),
    (("default", "ops"), None),
]


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize(("spaces", "pinned"), _RESTRICTED_LISTINGS)
def test_uninstall_package_refuses_under_space_awareness_whatever_the_key_lists(
        spaces, pinned, force):
    calls, reads = [], []
    gw = _ugw(fleet_epm=_epm(dict(_INSTALLED_TCP), calls), migration_status="success",
              spaces=spaces, pinned_space=pinned,
              fleet_policies=_recording_policies_ns([], {}, reads))
    with pytest.raises(KibanaRejected, match=(
        r"^refusing to uninstall integration package 'tcp': Fleet space awareness is on, so "
        r"Kibana's uninstall deletes the package's package policies in every space, and this "
        r"server cannot prove its API key reads every space \(Kibana's spaces listing leaves "
        r"out, without an error, each space the key cannot read\), .*This is refused "
        r"regardless of force; nothing was changed\. Uninstall it in Kibana's Integrations UI "
        r"\('tcp' → Settings\) as a user whose role grants Fleet and Integrations in all "
        r'spaces \(spaces \["\*"\]\)')):
        gw.uninstall_package(name="tcp", force=force)
    assert calls == [] and reads == []  # nothing read, nothing sent


@pytest.mark.parametrize("status", ["pending", "error"])
def test_uninstall_package_refuses_under_any_space_awareness_status(status):
    # A migration under way or failed may have scoped some policies already.
    calls = []
    gw = _ugw(fleet_epm=_epm({**_INSTALLED_TCP, "name": "fleet_server"}, calls),
              migration_status=status, fleet_policies=_policies_ns([], {}))
    with pytest.raises(KibanaRejected, match=r"Fleet space awareness is on"):
        gw.uninstall_package(name="fleet_server", force=True)
    assert calls == []


def test_uninstall_package_not_installed_comes_before_the_space_awareness_refusal():
    calls = []
    gw = _ugw(fleet_epm=_epm(dict(_TCP), calls), migration_status="success",
              fleet_policies=_policies_ns([], {}))
    with pytest.raises(KibanaNotFound, match=r"list_installed_packages"):
        gw.uninstall_package(name="tcp")
    assert calls == []
