"""Adapter unit tests for the alerting methods: raw body -> DTO + kwarg building,
using SimpleNamespace client stubs. Raw shapes from the probe (A0b/A1/A3)."""

from types import SimpleNamespace

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
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
