"""Adapter unit tests for the alerting methods: raw body -> DTO + kwarg building,
using SimpleNamespace client stubs. Raw shapes from the probe (A0b/A1/A3)."""

from types import SimpleNamespace

import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaRejected
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

_RULE_BODY = {
    "id": "r1", "name": "my rule", "rule_type_id": ".es-query", "consumer": "stackAlerts",
    "enabled": False, "schedule": {"interval": "1m"},
    "execution_status": {"status": "pending"}, "tags": ["a", "b"],
}


def _gw(**client_overrides):
    return KibanaPyGateway(make_fake_client(**client_overrides))


def test_to_alert_rule_extracts_nested_fields():
    alerting = SimpleNamespace(rule=SimpleNamespace(get=lambda id: FakeResponse(_RULE_BODY)))
    r = _gw(alerting=alerting).get_alert_rule("r1")
    assert (r.id, r.rule_type_id, r.consumer, r.enabled) == ("r1", ".es-query", "stackAlerts", False)
    assert r.schedule_interval == "1m"
    assert r.status == "pending"
    assert r.tags == ("a", "b")


def test_list_alert_rules_maps_data_array_and_passes_search():
    captured = {}

    def find(**kw):
        captured.update(kw)
        return FakeResponse({"data": [_RULE_BODY], "total": 1})

    rules = _gw(alerting=SimpleNamespace(rule=SimpleNamespace(find=find))).list_alert_rules("my")
    assert len(rules) == 1 and rules[0].id == "r1"
    assert captured.get("search") == "my"


def test_create_alert_rule_wraps_schedule_and_defaults_tags():
    captured = {}

    def create(**kw):
        captured.update(kw)
        return FakeResponse(_RULE_BODY)

    _gw(alerting=SimpleNamespace(rule=SimpleNamespace(create=create))).create_alert_rule(
        "n", ".es-query", "stackAlerts", "5m", {"p": 1}, None, False
    )
    assert captured["schedule"] == {"interval": "5m"}
    assert captured["tags"] == []  # None -> []
    assert captured["enabled"] is False


def test_alerting_health_status_is_worst_substatus():
    body = {
        "is_sufficiently_secure": True, "has_permanent_encryption_key": True,
        "alerting_framework_health": {
            "decryption_health": {"status": "ok"},
            "execution_health": {"status": "warn"},
            "read_health": {"status": "ok"},
        },
    }
    h = _gw(alerting=SimpleNamespace(health=lambda: FakeResponse(body))).get_alerting_health()
    assert h.status == "warn"
    assert h.has_permanent_encryption_key is True and h.is_sufficiently_secure is True


def test_list_alert_rules_paginates_to_exhaustion():
    pages = {1: {"data": [{"id": "r1"}], "total": 2}, 2: {"data": [{"id": "r2"}], "total": 2}}
    seen = []

    def find(**kw):
        seen.append(kw["page"])
        return FakeResponse(pages[kw["page"]])

    rules = _gw(alerting=SimpleNamespace(rule=SimpleNamespace(find=find))).list_alert_rules(None)
    assert [r.id for r in rules] == ["r1", "r2"]  # BOTH pages, not just the first
    assert seen == [1, 2]


def test_health_status_unknown_when_substatus_missing():
    body = {
        "has_permanent_encryption_key": True, "is_sufficiently_secure": True,
        "alerting_framework_health": {  # read_health absent
            "decryption_health": {"status": "ok"}, "execution_health": {"status": "ok"},
        },
    }
    h = _gw(alerting=SimpleNamespace(health=lambda: FakeResponse(body))).get_alerting_health()
    assert h.status == "unknown"  # missing signal is NOT silently "ok"


def test_execute_connector_returns_connector_id_and_status():
    connectors = SimpleNamespace(
        execute=lambda id, params: FakeResponse({"connector_id": id, "status": "ok"})
    )
    assert _gw(connectors=connectors).execute_connector("c1", {"message": "x"}) == {
        "connector_id": "c1", "status": "ok",
    }


def test_execute_connector_surfaces_error_message():
    connectors = SimpleNamespace(
        execute=lambda id, params: FakeResponse({"status": "error", "service_message": "boom"})
    )
    out = _gw(connectors=connectors).execute_connector("c1", {})
    assert out["status"] == "error" and out["message"] == "boom"


def test_connector_surfaces_is_missing_secrets():
    connectors = SimpleNamespace(
        create=lambda **kw: FakeResponse(
            {"id": "c1", "name": "n", "connector_type_id": ".index", "is_missing_secrets": True}
        )
    )
    conn = _gw(connectors=connectors).create_connector("n", ".index", {"index": "x"}, None)
    assert conn.is_missing_secrets is True


def test_create_connector_omits_config_secrets_when_none():
    captured = {}

    def create(**kw):
        captured.update(kw)
        return FakeResponse({"id": "c1", "name": "n", "connector_type_id": ".server-log"})

    _gw(connectors=SimpleNamespace(create=create)).create_connector("n", ".server-log", None, None)
    assert "config" not in captured and "secrets" not in captured


def test_list_rule_types_maps_writable_consumers_and_licence():
    body = [
        {
            "id": ".es-query", "name": "Elasticsearch query", "category": "management",
            "producer": "stackAlerts", "enabled_in_license": True,
            "minimum_license_required": "basic", "is_internally_managed": False,
            "authorized_consumers": {
                "stackAlerts": {"read": True, "all": True},
                "discover": {"read": True, "all": True},
                "infrastructure": {"read": True, "all": False},
            },
            "action_variables": {"params": [{"name": "size", "description": "x"}]},
        },
        {"id": ".geo-containment", "name": "Tracking containment", "producer": "stackAlerts",
         "enabled_in_license": False, "minimum_license_required": "gold",
         "authorized_consumers": {}},
    ]
    alerting = SimpleNamespace(rule_types=lambda: FakeResponse(body))
    types = _gw(alerting=alerting).list_rule_types()
    es = types[0]
    assert (es.id, es.name, es.category, es.producer) == (
        ".es-query", "Elasticsearch query", "management", "stackAlerts"
    )
    assert es.consumers == ("discover", "stackAlerts")  # writable (all) only, sorted
    assert (es.enabled_in_license, es.minimum_license_required, es.internally_managed) == (
        True, "basic", False
    )
    geo = types[1]
    assert (geo.consumers, geo.enabled_in_license, geo.category, geo.internally_managed) == (
        (), False, "", False
    )


# --- in-place maintenance: update_alert_rule / get_connector / update_connector ---
# Shapes from the 9.5.4 probe: GET echoes each action's connector_type_id (the
# PUT schema rejects it) and its uuid (accepted, keeps the action's identity);
# a PUT that omits actions/tags wipes them, one that omits params fails.

_FULL_RULE = {
    **_RULE_BODY,
    "params": {"index": ["logs-*"], "threshold": [0], "size": 100, "aggType": "count"},
    "actions": [{
        "group": "query matched", "id": "c1", "params": {"message": "hi"},
        "connector_type_id": ".server-log", "uuid": "u-1",
        "frequency": {"summary": False, "notify_when": "onActionGroupChange", "throttle": None},
    }],
    "notify_when": None, "throttle": "10m", "alert_delay": {"active": 2},
    "revision": 3, "execution_status": {"status": "ok"}, "mute_all": False,
}


def _rule_rmw_gw(sent, current=_FULL_RULE):
    def update(**kw):
        sent.update(kw)
        return FakeResponse({**current, **kw})

    rule = SimpleNamespace(get=lambda id: FakeResponse(dict(current)), update=update)
    return _gw(alerting=SimpleNamespace(rule=rule))


def test_update_alert_rule_resends_every_writable_field_and_merges_params():
    sent = {}
    _rule_rmw_gw(sent).update_alert_rule("r1", None, None, None, {"threshold": [10]}, None)
    assert sent["id"] == "r1"
    assert sent["name"] == "my rule" and sent["schedule"] == {"interval": "1m"}
    assert sent["params"] == {"index": ["logs-*"], "threshold": [10], "size": 100, "aggType": "count"}
    assert sent["tags"] == ["a", "b"]  # omitted would be wiped to []
    assert sent["actions"] == [{
        "group": "query matched", "id": "c1", "params": {"message": "hi"}, "uuid": "u-1",
        "frequency": {"summary": False, "notify_when": "onActionGroupChange", "throttle": None},
    }]  # connector_type_id stripped, everything else kept
    assert sent["throttle"] == "10m" and sent["alert_delay"] == {"active": 2}
    assert "notify_when" not in sent  # null on the rule: nothing to preserve
    # never writable here: enabled is enable/disable_alert_rule's, and read-only
    # response fields never reach the PUT
    for key in ("enabled", "rule_type_id", "consumer", "revision", "execution_status", "mute_all"):
        assert key not in sent


def test_update_alert_rule_overrides_and_clears_lists():
    sent = {}
    rule = _rule_rmw_gw(sent).update_alert_rule("r1", "renamed", [], "5m", None, [])
    assert sent["name"] == "renamed" and sent["schedule"] == {"interval": "5m"}
    assert sent["tags"] == [] and sent["actions"] == []  # [] clears, deliberately
    assert sent["params"] == _FULL_RULE["params"]  # untouched when not passed
    assert rule.name == "renamed"


def test_update_alert_rule_passes_caller_actions_verbatim():
    sent = {}
    new = [{"id": "c2", "group": "query matched", "params": {"message": "x"}}]
    _rule_rmw_gw(sent).update_alert_rule("r1", None, None, None, None, new)
    assert sent["actions"] == new


_LEGACY_TIMED_RULE = {**_FULL_RULE, "notify_when": "onActiveAlert", "throttle": "5m", "actions": [{
    "group": "query matched", "id": "c1", "params": {"message": "hi"},
    "connector_type_id": ".server-log", "uuid": "u-1",
}]}
_FREQ = {"summary": False, "notify_when": "onActionGroupChange", "throttle": None}


def test_update_alert_rule_actions_with_frequency_replace_rule_level_timing():
    # Kibana 400s per-action frequency next to a rule-level notify_when or
    # throttle (probed on 9.5.4); omitting the pair clears it on the rule.
    sent = {}
    new = [{"id": "c1", "group": "query matched", "params": {"message": "x"}, "frequency": _FREQ}]
    _rule_rmw_gw(sent, _LEGACY_TIMED_RULE).update_alert_rule("r1", None, None, None, None, new)
    assert sent["actions"] == new
    assert "notify_when" not in sent and "throttle" not in sent
    assert sent["alert_delay"] == {"active": 2}  # the other preserved keys still ride along


@pytest.mark.parametrize("actions", [
    None,  # the rule's own actions are re-sent
    [{"id": "c1", "group": "query matched", "params": {"message": "x"}}],  # no frequency
])
def test_update_alert_rule_keeps_rule_level_timing_without_per_action_frequency(actions):
    sent = {}
    _rule_rmw_gw(sent, _LEGACY_TIMED_RULE).update_alert_rule("r1", "n", None, None, None, actions)
    assert sent["notify_when"] == "onActiveAlert" and sent["throttle"] == "5m"


_CONN_BODY = {
    "id": "c1", "name": "idx", "connector_type_id": ".index",
    "config": {"index": "a", "refresh": False, "executionTimeField": None},
    "is_preconfigured": False, "is_deprecated": False, "is_missing_secrets": False,
    "is_system_action": False, "is_connector_type_deprecated": False, "auth_mode": "shared",
}


def _conn_gw(sent, current=_CONN_BODY):
    def update(**kw):
        sent.update(kw)
        return FakeResponse({**current, "name": kw["name"], "config": kw["config"]})

    return _gw(connectors=SimpleNamespace(get=lambda id: FakeResponse(dict(current)), update=update))


def test_get_connector_maps_detail_and_drops_unknown_keys():
    body = {**_CONN_BODY, "secrets": {"password": "s3cr3t"}}  # hostile: never surfaced
    c = _gw(connectors=SimpleNamespace(get=lambda id: FakeResponse(body))).get_connector("c1")
    assert (c.id, c.name, c.connector_type_id) == ("c1", "idx", ".index")
    assert c.config == {"index": "a", "refresh": False, "executionTimeField": None}
    assert (c.is_preconfigured, c.is_system_action, c.is_missing_secrets) == (False, False, False)
    assert "s3cr3t" not in repr(c) and not hasattr(c, "secrets")


def test_update_connector_merges_config_keeps_name_and_sends_no_secrets():
    sent = {}
    out = _conn_gw(sent).update_connector("c1", None, {"index": "b"}, None)
    assert sent == {
        "id": "c1", "name": "idx",
        "config": {"index": "b", "refresh": False, "executionTimeField": None},
    }  # .index has no secrets schema: omitting them loses nothing
    assert out.config["index"] == "b"


def test_update_connector_forwards_secrets_but_never_returns_them():
    sent = {}
    out = _conn_gw(sent).update_connector("c1", "renamed", None, {"token": "s3cr3t"})
    assert sent["secrets"] == {"token": "s3cr3t"} and sent["name"] == "renamed"
    assert sent["config"] == _CONN_BODY["config"]  # re-sent: an omitted config resets to {}
    assert "s3cr3t" not in repr(out)


def test_update_connector_refuses_preconfigured_before_any_write():
    sent = {}
    gw = _conn_gw(sent, {**_CONN_BODY, "is_preconfigured": True})
    with pytest.raises(KibanaRejected, match=r"preconfigured .*kibana\.yml.*create_connector"):
        gw.update_connector("c1", "x", None, None)
    assert sent == {}


def test_update_connector_requires_secrets_for_a_type_that_can_hold_them():
    sent = {}
    gw = _conn_gw(sent, {**_CONN_BODY, "connector_type_id": ".slack", "config": {}})
    with pytest.raises(KibanaRejected, match=r"\.slack connector, whose secrets .*pass `secrets`"):
        gw.update_connector("c1", "renamed", None, None)
    assert sent == {}  # refused before the PUT that would have reset them
    gw.update_connector("c1", "renamed", None, {})  # explicit {}: the caller's choice
    assert sent["secrets"] == {}
