"""Adapter unit tests for the alerting methods: raw body -> DTO + kwarg building,
using SimpleNamespace client stubs. Raw shapes from the probe (A0b/A1/A3)."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from kibana.exceptions import BadRequestError, NotFoundError, SpaceNotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected, KibanaSpaceNotFound
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


# --- get_alert_rule: the rule's params and actions, in update_alert_rule's shape ---
# Body keys from the 9.5.4 probe of GET /api/alerting/rule/{id} (an enabled rule
# adds scheduled_task_id, api_key_owner as a user name, api_key_created_by_user).

_GET_RULE_BODY = {
    **_FULL_RULE,
    "created_by": "elastic", "updated_by": "elastic", "created_at": "t", "updated_at": "t",
    "api_key_owner": "elastic", "api_key_created_by_user": False, "scheduled_task_id": "t1",
    "muted_alert_ids": [], "running": False,
}


def _get_gw(body):
    return _gw(alerting=SimpleNamespace(rule=SimpleNamespace(get=lambda id: FakeResponse(body))))


def test_get_alert_rule_returns_params_and_actions_beside_the_summary():
    r = _get_gw(_GET_RULE_BODY).get_alert_rule("r1")
    assert (r.id, r.name, r.enabled, r.status, r.tags) == ("r1", "my rule", False, "ok", ("a", "b"))
    assert r.params == {"index": ["logs-*"], "threshold": [0], "size": 100, "aggType": "count"}
    assert r.actions == ({
        "id": "c1", "uuid": "u-1", "group": "query matched", "params": {"message": "hi"},
        "frequency": {"summary": False, "notify_when": "onActionGroupChange", "throttle": None},
    },)  # connector_type_id (rejected by the PUT) is not returned; uuid is (D40)


def test_get_alert_rule_returns_named_keys_only():
    body = {**_GET_RULE_BODY, "apiKey": "s3cr3t", "secrets": {"password": "s3cr3t"}}  # hostile
    out = asdict(_get_gw(body).get_alert_rule("r1"))
    assert set(out) == {
        "id", "name", "rule_type_id", "consumer", "enabled", "schedule_interval", "status",
        "tags", "params", "actions", "muted", "muted_alert_ids",
    }
    assert "s3cr3t" not in repr(out) and "api_key_owner" not in out


def test_get_alert_rule_actions_carry_uuid_group_and_frequency_only_when_kibana_sent_them():
    body = {**_RULE_BODY, "actions": [
        {"id": "c1", "group": "query matched", "params": {"message": "x"},
         "connector_type_id": ".server-log", "uuid": "u-1"},
        {"id": "system-connector-.cases", "params": {"subAction": "run"}, "uuid": "u-2"},
        {"id": "c2", "group": "query matched", "params": {}},
    ]}  # no "params" key on the rule: read as {}
    r = _get_gw(body).get_alert_rule("r1")
    assert r.params == {}
    assert r.actions == (
        {"id": "c1", "uuid": "u-1", "group": "query matched", "params": {"message": "x"}},
        {"id": "system-connector-.cases", "uuid": "u-2", "params": {"subAction": "run"}},
        {"id": "c2", "group": "query matched", "params": {}},
    )


def test_get_alert_rule_actions_pass_back_into_update_alert_rule_unchanged():
    # Read-modify-write through the tools: what get returns, update accepts.
    read = _get_gw(_GET_RULE_BODY).get_alert_rule("r1")
    sent = {}
    _rule_rmw_gw(sent).update_alert_rule("r1", None, None, None, None, list(read.actions))
    assert sent["actions"] == list(read.actions)
    assert all("connector_type_id" not in a for a in sent["actions"])
    # the uuid goes back too, so Kibana keeps the action's identity (D40)
    assert [a["uuid"] for a in sent["actions"]] == ["u-1"]


def test_list_alert_rules_stays_a_summary():
    find = lambda **kw: FakeResponse({"data": [_GET_RULE_BODY], "total": 1})  # noqa: E731
    rules = _gw(alerting=SimpleNamespace(rule=SimpleNamespace(find=find))).list_alert_rules(None)
    assert not hasattr(rules[0], "params") and not hasattr(rules[0], "actions")


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


# --- rule lifecycle: mute, snooze, API key, untrack (B1; shapes probed on 9.5.4) ---
# Every call below answers 204 with no body on success, except the snooze, which
# answers 200 {"schedule": {"id", "custom": {...}}}. A missing rule is a 404
# "Saved object [alert/<id>] not found" on every route.


def _missing_rule(**_kw):
    raise NotFoundError(
        "Saved object [alert/nope] not found", meta=SimpleNamespace(status=404), body=None
    )


def _recording_rule_gw(calls, **fns):
    def rec(name):
        def fn(**kw):
            calls.append((name, kw))
            return FakeResponse(None)
        return fn
    names = ("mute_all", "unmute_all", "mute_alert", "unmute_alert", "update_api_key",
             "disable", "unsnooze")
    rule = SimpleNamespace(**{n: rec(n) for n in names}, **fns)
    return _gw(alerting=SimpleNamespace(rule=rule))


def test_rule_wide_mute_and_api_key_calls_send_only_the_rule_id():
    calls = []
    gw = _recording_rule_gw(calls, get=lambda id: FakeResponse(_RULE_BODY))
    gw.mute_alert_rule("r1")
    gw.unmute_alert_rule("r1")
    gw.update_alert_rule_api_key("r1")
    assert calls == [("mute_all", {"id": "r1"}), ("unmute_all", {"id": "r1"}),
                     ("update_api_key", {"id": "r1"})]


@pytest.mark.parametrize("method", ["mute_alert_rule", "unmute_alert_rule"])
def test_rule_wide_mute_and_unmute_return_the_per_alert_mutes_they_clear(method):
    # Kibana resets muted_alert_ids on both calls (probed) and no public route
    # lists a rule's alert ids: they are read before the call and returned.
    order = []
    body = {**_RULE_BODY, "muted_alert_ids": ["host-a", "host-b"]}

    def get(id):
        order.append(("get", {"id": id}))
        return FakeResponse(body)
    gw = _recording_rule_gw(order, get=get)
    assert getattr(gw, method)("r1") == ("host-a", "host-b")
    assert order[0] == ("get", {"id": "r1"}) and len(order) == 2  # read, then the mute
    gw = _recording_rule_gw([], get=lambda id: FakeResponse(_RULE_BODY))
    assert getattr(gw, method)("r1") == ()  # no muted_alert_ids key: nothing cleared


def test_mute_alert_sends_rule_and_alert_ids_and_keeps_kibana_existence_check():
    calls = []
    gw = _recording_rule_gw(calls, get=lambda id: FakeResponse(_RULE_BODY))
    gw.mute_alert("r1", "JetBeats")
    gw.unmute_alert("r1", "JetBeats")
    # validate_alerts_existence is not sent: Kibana's default (true) refuses a
    # typo'd alert id instead of muting an id the rule never raises.
    assert calls == [("mute_alert", {"rule_id": "r1", "alert_id": "JetBeats"}),
                     ("unmute_alert", {"rule_id": "r1", "alert_id": "JetBeats"})]


def test_disable_sends_no_untrack_by_default_and_true_only_when_asked():
    calls = []
    gw = _recording_rule_gw(calls)
    gw.disable_alert_rule("r1")
    gw.disable_alert_rule("r1", untrack=True)
    assert calls == [("disable", {"id": "r1"}), ("disable", {"id": "r1", "untrack": True})]


def test_unknown_alert_id_names_where_alert_ids_come_from():
    def mute_alert(**_kw):
        raise NotFoundError(
            'Alert instance with id "NoSuch" does not exist for rule with id "r1"',
            meta=SimpleNamespace(status=404), body=None,
        )
    rule = SimpleNamespace(mute_alert=mute_alert, get=lambda id: FakeResponse(_RULE_BODY))
    gw = _gw(alerting=SimpleNamespace(rule=rule))
    with pytest.raises(KibanaNotFound, match=(
        r"^alert 'NoSuch' not found on alert rule 'r1' — alert_id is the id of an alert "
        r"the rule has raised .*'query matched'.*not the rule's name"
    )):
        gw.mute_alert("r1", "NoSuch")


def test_mute_alert_on_a_rule_muted_as_a_whole_is_refused_before_the_mute():
    calls = []
    muted = {**_RULE_BODY, "mute_all": True}
    gw = _recording_rule_gw(calls, get=lambda id: FakeResponse(muted))
    with pytest.raises(KibanaRejected, match=(
        r"^alert rule 'r1' is muted as a whole \(mute_alert_rule\).*call unmute_alert_rule "
        r"first \(it clears every per-alert mute\), then mute_alert"
    )):
        gw.mute_alert("r1", "JetBeats")
    assert calls == []  # Kibana would answer 204 and record nothing


def test_mute_alert_on_a_missing_rule_names_list_alert_rules():
    rule = SimpleNamespace(mute_alert=_missing_rule, get=_missing_rule)
    gw = _gw(alerting=SimpleNamespace(rule=rule))
    with pytest.raises(KibanaNotFound, match=r"^alert rule 'nope' not found — .*list_alert_rules"):
        gw.mute_alert("nope", "JetBeats")


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_alert_and_schedule_ids_are_refused_before_kibana(blank):
    calls = []
    gw = _recording_rule_gw(calls)
    with pytest.raises(KibanaRejected, match=r"^alert_id is empty"):
        gw.mute_alert("r1", blank)
    with pytest.raises(KibanaRejected, match=r"^alert_id is empty"):
        gw.unmute_alert("r1", blank)
    with pytest.raises(KibanaRejected, match=r"^schedule_id is empty — .*snooze_alert_rule"):
        gw.unsnooze_alert_rule("r1", blank)
    assert calls == []


_START = datetime(2026, 10, 25, 1, 30, tzinfo=timezone(timedelta(hours=2)))


def _snooze_gw(sent, response=None):
    def snooze(**kw):
        sent.update(kw)
        custom = dict(kw["schedule"]["custom"])
        custom.setdefault("timezone", "UTC")
        return FakeResponse(response or {"schedule": {"id": "s-1", "custom": custom}})
    return _gw(alerting=SimpleNamespace(rule=SimpleNamespace(snooze=snooze)))


def test_snooze_sends_kibanas_strict_utc_start_and_returns_the_schedule_id():
    sent = {}
    s = _snooze_gw(sent).snooze_alert_rule("r1", "2h", _START, None)
    # Kibana accepts only YYYY-MM-DDTHH:mm:ss.sssZ (probed: "...:00Z" and
    # "+02:00" offsets are 400s), so the instant is sent in UTC with milliseconds.
    assert sent == {"id": "r1", "schedule": {"custom": {
        "duration": "2h", "start": "2026-10-24T23:30:00.000Z"}}}
    assert asdict(s) == {
        "rule_id": "r1", "schedule_id": "s-1", "duration": "2h",
        "start": "2026-10-24T23:30:00.000Z", "timezone": "UTC", "recurring": None,
    }


def test_snooze_recurring_end_is_sent_in_the_same_strict_format():
    sent = {}
    end = datetime(2026, 12, 1, tzinfo=UTC)
    s = _snooze_gw(sent).snooze_alert_rule(
        "r1", "1h", _START, {"every": "1w", "onWeekDay": ["MO"], "end": end}
    )
    assert sent["schedule"]["custom"]["recurring"] == {
        "every": "1w", "onWeekDay": ["MO"], "end": "2026-12-01T00:00:00.000Z"}
    assert s.recurring == {"every": "1w", "onWeekDay": ["MO"], "end": "2026-12-01T00:00:00.000Z"}


def test_snooze_past_kibanas_five_schedule_limit_names_unsnooze():
    def snooze(**_kw):
        raise BadRequestError(
            "Rule cannot have more than 5 snooze schedules",
            meta=SimpleNamespace(status=400), body=None,
        )
    gw = _gw(alerting=SimpleNamespace(rule=SimpleNamespace(snooze=snooze)))
    with pytest.raises(KibanaRejected, match=(
        r"^alert rule 'r1' already has 5 snooze schedules, Kibana's limit \(expired ones "
        r"count\) — remove one with unsnooze_alert_rule"
    )):
        gw.snooze_alert_rule("r1", "1h", _START, None)


def test_snooze_on_a_missing_rule_names_list_alert_rules():
    gw = _gw(alerting=SimpleNamespace(rule=SimpleNamespace(snooze=_missing_rule)))
    with pytest.raises(KibanaNotFound, match=r"^alert rule 'nope' not found — .*list_alert_rules"):
        gw.snooze_alert_rule("nope", "1h", _START, None)


def test_unsnooze_sends_the_rule_and_schedule_ids():
    calls = []
    _recording_rule_gw(calls).unsnooze_alert_rule("r1", "s-1")
    assert calls == [("unsnooze", {"rule_id": "r1", "schedule_id": "s-1"})]


@pytest.mark.parametrize("error", [
    # a rule with other snoozes: 404; a rule with none at all: 400 (both probed)
    NotFoundError("Rule has no snooze schedule with id s-9.",
                  meta=SimpleNamespace(status=404), body=None),
    BadRequestError("Rule has no snooze schedules.", meta=SimpleNamespace(status=400), body=None),
])
def test_unknown_schedule_id_names_snooze_alert_rule(error):
    def unsnooze(**_kw):
        raise error
    gw = _gw(alerting=SimpleNamespace(rule=SimpleNamespace(unsnooze=unsnooze)))
    with pytest.raises(KibanaNotFound, match=(
        r"^snooze schedule 's-9' not found on alert rule 'r1' — schedule_id is the "
        r"schedule_id that snooze_alert_rule returned"
    )):
        gw.unsnooze_alert_rule("r1", "s-9")


def test_unsnooze_on_a_missing_rule_names_list_alert_rules():
    gw = _gw(alerting=SimpleNamespace(rule=SimpleNamespace(unsnooze=_missing_rule)))
    with pytest.raises(KibanaNotFound, match=r"^alert rule 'nope' not found — .*list_alert_rules"):
        gw.unsnooze_alert_rule("nope", "s-1")


def test_get_alert_rule_reports_mute_state():
    body = {**_GET_RULE_BODY, "mute_all": True, "muted_alert_ids": ["JetBeats", "ES-Air"]}
    r = _get_gw(body).get_alert_rule("r1")
    assert r.muted is True and r.muted_alert_ids == ("JetBeats", "ES-Air")
    r = _get_gw(_RULE_BODY).get_alert_rule("r1")  # keys absent: not muted
    assert r.muted is False and r.muted_alert_ids == ()


def test_a_rule_gone_between_read_and_mute_still_gets_the_rule_guidance():
    rule = SimpleNamespace(get=lambda id: FakeResponse(_RULE_BODY), mute_alert=_missing_rule)
    gw = _gw(alerting=SimpleNamespace(rule=rule))
    with pytest.raises(KibanaNotFound, match=r"^alert rule 'r1' not found — .*list_alert_rules"):
        gw.mute_alert("r1", "JetBeats")


@pytest.mark.parametrize("call", [
    lambda gw: gw.mute_alert("r1", "JetBeats"),
    lambda gw: gw.unsnooze_alert_rule("r1", "s-1"),
])
def test_a_missing_space_on_the_alert_and_snooze_routes_is_reported_as_the_space(call):
    def no_space(**_kw):
        raise SpaceNotFoundError("sales")
    rule = SimpleNamespace(
        get=lambda id: FakeResponse(_RULE_BODY), mute_alert=no_space, unsnooze=no_space
    )
    with pytest.raises(KibanaSpaceNotFound):
        call(_gw(alerting=SimpleNamespace(rule=rule)))
