"""Adapter translation tests for the security-detections toolbox. The rule and
exception-list bodies are real captures from seeded-then-torn-down objects
on a live stack; the paginator fixtures
are fabricated (the live stack is empty) and set total != a full page so a
regression to a wrong termination key over-collects and fails."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import ConflictError, NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

# Real GET bodies captured from a seeded rule / exception list.
_RULE = {
    "id": "8ae4c667-0bec-4fc7-94c1-2102ad5d80a6",
    "rule_id": "probe-rule-xyz",
    "name": "probe-rule",
    "enabled": False,
    "type": "query",
    "severity": "low",
    "risk_score": 21,
    "tags": ["probe"],
    "immutable": False,
    "version": 1,
}
_EXC = {
    "id": "33c07bff-9efc-4217-890b-9d0fbc7aae32",
    "list_id": "probe-exc-xyz",
    "name": "probe-exc",
    "type": "detection",
    "namespace_type": "single",
    "tags": [],
    "os_types": [],
}


def _gw(**ns):
    return KibanaPyGateway(make_fake_client(**ns))


def test_find_detection_rules_maps_real_body():
    gw = _gw(
        detection_engine=SimpleNamespace(
            find_rules=lambda **kw: FakeResponse(
                {"data": [_RULE], "page": 1, "perPage": 100, "total": 1}
            )
        )
    )
    [r] = gw.find_detection_rules()
    assert r.id == "8ae4c667-0bec-4fc7-94c1-2102ad5d80a6"
    assert r.rule_id == "probe-rule-xyz"
    assert r.enabled is False
    assert r.type == "query"
    assert r.severity == "low"
    assert r.risk_score == 21
    assert r.tags == ("probe",)
    assert r.immutable is False
    assert r.version == 1


def test_find_detection_rules_paginates_via_total():
    def paged(**kw):
        page = kw.get("page", 1)
        start = (page - 1) * 100
        count = 100 if page == 1 else 30
        data = [{**_RULE, "id": f"r{start + i}"} for i in range(count)]
        return FakeResponse({"data": data, "page": page, "perPage": 100, "total": 130})

    gw = _gw(detection_engine=SimpleNamespace(find_rules=paged))
    rules = gw.find_detection_rules()
    assert len(rules) == 130
    assert rules[0].id == "r0"
    assert rules[-1].id == "r129"


def test_get_detection_rule_by_rule_id():
    gw = _gw(detection_engine=SimpleNamespace(get_rule=lambda **kw: FakeResponse(_RULE)))
    r = gw.get_detection_rule("probe-rule-xyz", None)
    assert r.rule_id == "probe-rule-xyz"


def test_get_detection_rule_requires_an_identifier():
    # The guard fires before any client call -> a clean KibanaRejected.
    with pytest.raises(KibanaRejected):
        _gw().get_detection_rule(None, None)


def test_get_detection_rule_rejects_both_identifiers():
    # kibana-py get_rule enforces EXACTLY one; passing both would leak a bare
    # ValueError without the exactly-one guard.
    with pytest.raises(KibanaRejected):
        _gw().get_detection_rule("rule-1", "uuid-1")


def test_get_detection_rule_notfound_maps_to_domain_error():
    def raise_nf(**kw):
        raise NotFoundError("not found", meta=SimpleNamespace(status=404), body=None)

    gw = _gw(detection_engine=SimpleNamespace(get_rule=raise_nf))
    with pytest.raises(KibanaNotFound):
        gw.get_detection_rule("nope", None)


def test_prepackaged_status_coerces_ints():
    gw = _gw(
        detection_engine=SimpleNamespace(
            get_prepackaged_rules_status=lambda **kw: FakeResponse(
                {
                    "rules_installed": 5, "rules_not_installed": 2, "rules_custom_installed": 1,
                    "rules_not_updated": 0, "timelines_installed": 3,
                    "timelines_not_installed": 7, "timelines_not_updated": 0,
                }
            )
        )
    )
    s = gw.get_prepackaged_rules_status()
    assert s.rules_installed == 5
    assert s.timelines_not_installed == 7


def test_list_detection_rule_tags_bare_array():
    gw = _gw(detection_engine=SimpleNamespace(get_tags=lambda **kw: FakeResponse(["auth", "network"])))
    assert gw.list_detection_rule_tags() == ["auth", "network"]


def test_search_detection_alerts_maps_hits():
    # `fields` projection: each value is an array, flat-dotted (shape-agnostic).
    hit = {
        "_id": "a1",
        "fields": {
            "kibana.alert.rule.name": ["Suspicious login"],
            "kibana.alert.severity": ["high"],
            "kibana.alert.workflow_status": ["open"],
            "@timestamp": ["2026-07-13T00:00:00Z"],
        },
    }
    captured = {}

    def search(**kw):
        captured.update(kw)
        return FakeResponse({"hits": {"total": {"value": 1}, "hits": [hit]}})

    gw = _gw(detection_engine=SimpleNamespace(search_alerts=search))
    [a] = gw.search_detection_alerts(20)
    assert a.id == "a1"
    assert a.rule_name == "Suspicious login"
    assert a.severity == "high"
    assert a.status == "open"
    assert a.timestamp == "2026-07-13T00:00:00Z"
    # the query is sorted most-recent-first (else match_all yields arbitrary order)
    assert captured["sort"] == [{"@timestamp": {"order": "desc"}}]


def test_search_detection_alerts_clamps_size():
    captured = {}

    def search(**kw):
        captured.update(kw)
        return FakeResponse({"hits": {"hits": []}})

    gw = _gw(detection_engine=SimpleNamespace(search_alerts=search))
    gw.search_detection_alerts(100_000)
    assert captured["size"] == 500  # clamped to the sane max


def test_get_detection_rule_empty_string_id_treated_as_absent():
    # rule_id="x", id="" -> exactly one provided (empty coerced to None) -> no leak.
    gw = _gw(detection_engine=SimpleNamespace(get_rule=lambda **kw: FakeResponse(_RULE)))
    r = gw.get_detection_rule("probe-rule-xyz", "")
    assert r.rule_id == "probe-rule-xyz"


def test_search_detection_alerts_empty_hits():
    gw = _gw(
        detection_engine=SimpleNamespace(
            search_alerts=lambda **kw: FakeResponse({"hits": {"total": {"value": 0}, "hits": []}})
        )
    )
    assert gw.search_detection_alerts(20) == []


def test_find_exception_lists_maps_real_body():
    gw = _gw(
        exception_lists=SimpleNamespace(
            find=lambda **kw: FakeResponse({"data": [_EXC], "page": 1, "per_page": 100, "total": 1})
        )
    )
    [x] = gw.find_exception_lists()
    assert x.id == "33c07bff-9efc-4217-890b-9d0fbc7aae32"
    assert x.list_id == "probe-exc-xyz"
    assert x.type == "detection"
    assert x.namespace_type == "single"


def test_get_exception_list_requires_an_identifier():
    with pytest.raises(KibanaRejected):
        _gw().get_exception_list(None, None)


def test_find_exception_items_paginates_via_total():
    def paged(**kw):
        page = kw.get("page", 1)
        start = (page - 1) * 100
        count = 100 if page == 1 else 20
        data = [
            {"id": f"i{start + i}", "item_id": f"item-{start + i}", "name": "n", "list_id": "L"}
            for i in range(count)
        ]
        return FakeResponse({"data": data, "page": page, "per_page": 100, "total": 120})

    gw = _gw(exception_lists=SimpleNamespace(find_items=paged))
    items = gw.find_exception_items("L")
    assert len(items) == 120
    assert items[0].item_id == "item-0"


def test_find_value_lists_cursor_paginates_via_total():
    def paged(**kw):
        cursor = kw.get("cursor")
        if not cursor:
            data = [{"id": f"v{i}", "name": "n", "type": "ip", "description": "d"} for i in range(100)]
            return FakeResponse({"data": data, "total": 130, "cursor": "PAGE2"})
        data = [{"id": f"v{100 + i}", "name": "n", "type": "ip", "description": "d"} for i in range(30)]
        return FakeResponse({"data": data, "total": 130, "cursor": "END"})

    gw = _gw(lists=SimpleNamespace(find=paged))
    vls = gw.find_value_lists()
    assert len(vls) == 130  # cursor advanced + terminates on total
    assert vls[0].id == "v0"
    assert vls[-1].id == "v129"


def test_find_timelines_paginates_via_totalcount():
    def paged(**kw):
        page = kw.get("page_index", 1)
        start = (page - 1) * 100
        count = 100 if page == 1 else 10
        tl = [{"savedObjectId": f"t{start + i}", "title": "T", "description": None} for i in range(count)]
        return FakeResponse({"timeline": tl, "totalCount": 110})

    gw = _gw(timeline=SimpleNamespace(get_all=paged))
    tls = gw.find_timelines()
    assert len(tls) == 110
    assert tls[0].saved_object_id == "t0"
    assert tls[0].description == ""  # None -> ""


# --- v2 write/destructive: rule + exception create/delete ---


def test_create_detection_rule_sends_query_type_and_maps_body():
    captured = {}

    def create_rule(**kw):
        captured.update(kw)
        return FakeResponse({**_RULE, "name": kw["name"], "enabled": kw["enabled"]})

    gw = _gw(detection_engine=SimpleNamespace(create_rule=create_rule))
    r = gw.create_detection_rule(
        name="block bad IPs", description="d", query="*:*", index=["logs-*"],
        severity="high", risk_score=73, rule_id="my-rule", tags=["mcp"],
        interval="10m", language="kuery", enabled=False,
    )
    assert captured["type"] == "query"  # v2 creates query rules only
    assert captured["name"] == "block bad IPs"
    assert captured["index"] == ["logs-*"]
    assert captured["risk_score"] == 73
    assert captured["rule_id"] == "my-rule"
    assert captured["enabled"] is False
    assert r.name == "block bad IPs"  # returned body mapped via _to_detection_rule


def test_create_detection_rule_blank_rule_id_becomes_none():
    captured = {}

    def create_rule(**kw):
        captured.update(kw)
        return FakeResponse(_RULE)

    gw = _gw(detection_engine=SimpleNamespace(create_rule=create_rule))
    gw.create_detection_rule(
        name="n", description="d", query="*:*", index=["logs-*"], severity="low",
        risk_score=21, rule_id="", tags=[], interval="5m", language="kuery", enabled=False,
    )
    assert captured["rule_id"] is None  # "" coerced to None so Kibana generates one


def test_delete_detection_rule_requires_exactly_one_identifier():
    gw = _gw(detection_engine=SimpleNamespace(delete_rule=lambda **kw: FakeResponse({})))
    with pytest.raises(KibanaRejected):
        gw.delete_detection_rule(rule_id=None, id=None)
    with pytest.raises(KibanaRejected):
        gw.delete_detection_rule(rule_id="a", id="b")


def test_delete_detection_rule_calls_client():
    captured = {}
    gw = _gw(detection_engine=SimpleNamespace(
        delete_rule=lambda **kw: captured.update(kw) or FakeResponse({})
    ))
    gw.delete_detection_rule(rule_id="my-rule", id=None)
    assert captured == {"id": None, "rule_id": "my-rule"}


def test_create_exception_list_maps_body():
    captured = {}
    _EXC = {"id": "el1", "list_id": "my-list", "name": "allow", "type": "detection",
            "namespace_type": "single", "tags": [], "os_types": []}

    def create(**kw):
        captured.update(kw)
        return FakeResponse(_EXC)

    gw = _gw(exception_lists=SimpleNamespace(create=create))
    el = gw.create_exception_list(
        name="allow", description="d", type="detection", list_id="my-list",
        namespace_type="single", tags=[],
    )
    assert captured["type"] == "detection"
    assert captured["namespace_type"] == "single"
    assert el.list_id == "my-list"


def test_create_exception_item_sends_entries_and_simple_type():
    captured = {}
    _ITEM = {"id": "ei1", "item_id": "my-item", "name": "allow host", "list_id": "my-list"}

    def create_item(**kw):
        captured.update(kw)
        return FakeResponse(_ITEM)

    gw = _gw(exception_lists=SimpleNamespace(create_item=create_item))
    entries = [{"field": "host.name", "operator": "included", "value": "trusted"}]
    item = gw.create_exception_item(
        list_id="my-list", name="allow host", description="d", entries=entries,
        item_id="my-item", namespace_type="single", tags=[],
    )
    assert captured["type"] == "simple"
    assert captured["entries"] == entries
    assert item.item_id == "my-item"


def test_delete_exception_list_and_item_exactly_one_guard():
    gw = _gw(exception_lists=SimpleNamespace(
        delete=lambda **kw: FakeResponse({}), delete_item=lambda **kw: FakeResponse({})
    ))
    with pytest.raises(KibanaRejected):
        gw.delete_exception_list(id=None, list_id=None, namespace_type="single")
    with pytest.raises(KibanaRejected):
        gw.delete_exception_item(id="a", item_id="b", namespace_type="single")


def test_delete_exception_item_passes_namespace_type():
    captured = {}
    gw = _gw(exception_lists=SimpleNamespace(
        delete_item=lambda **kw: captured.update(kw) or FakeResponse({})
    ))
    gw.delete_exception_item(id=None, item_id="my-item", namespace_type="agnostic")
    assert captured == {"id": None, "item_id": "my-item", "namespace_type": "agnostic"}


# --- security-detections write extras (#60): update rule + value lists ---


def test_update_detection_rule_forwards_only_set_fields():
    calls = {}

    def fake_patch(**kw):
        calls.update(kw)
        return FakeResponse({"id": "u", "rule_id": "r", "name": "n2", "enabled": False,
                             "type": "query", "severity": "high", "risk_score": 70,
                             "tags": ["x"], "immutable": False, "version": 2})

    gw = _gw(detection_engine=SimpleNamespace(patch_rule=fake_patch))
    out = gw.update_detection_rule(rule_id="r", id=None, name="n2", description=None,
                                   tags=[], severity="high", risk_score=70, query=None, interval=None)
    assert out.severity == "high" and out.name == "n2"
    # only the set identifier + is-not-None fields (tags=[] kept; None dropped; no id/enabled)
    assert calls == {"rule_id": "r", "name": "n2", "tags": [], "severity": "high", "risk_score": 70}


def test_update_detection_rule_requires_one_identifier():
    gw = _gw(detection_engine=SimpleNamespace())
    with pytest.raises(KibanaRejected):
        gw.update_detection_rule(rule_id="r", id="i", name="n", description=None, tags=None,
                                 severity=None, risk_score=None, query=None, interval=None)


def test_update_detection_rule_requires_a_field():
    gw = _gw(detection_engine=SimpleNamespace())
    with pytest.raises(KibanaRejected):
        gw.update_detection_rule(rule_id="r", id=None, name=None, description=None, tags=None,
                                 severity=None, risk_score=None, query=None, interval=None)


def test_update_detection_rule_missing_maps_not_found():
    def boom(**kw):
        raise NotFoundError("not found", meta=SimpleNamespace(status=404), body=None)
    gw = _gw(detection_engine=SimpleNamespace(patch_rule=boom))
    with pytest.raises(KibanaNotFound):
        gw.update_detection_rule(rule_id="nope", id=None, name="x", description=None, tags=None,
                                 severity=None, risk_score=None, query=None, interval=None)


def _lists(**kw):
    return SimpleNamespace(**kw)


def test_create_value_list_ensures_index_when_absent():
    calls = []

    def status():
        raise NotFoundError("absent", meta=SimpleNamespace(status=404), body=None)

    gw = _gw(lists=_lists(
        get_index_status=status,
        create_index=lambda: calls.append("create_index") or FakeResponse({"acknowledged": True}),
        create=lambda **kw: FakeResponse({"id": kw["id"], "name": kw["name"], "type": kw["type"],
                                          "description": kw["description"]})))
    out = gw.create_value_list(name="l", description="d", type="keyword", id="vl1")
    assert out.id == "vl1" and out.type == "keyword" and calls == ["create_index"]


def test_create_value_list_partial_index_still_creates():
    calls = []
    gw = _gw(lists=_lists(
        get_index_status=lambda: FakeResponse({"list_index": True, "list_item_index": False}),
        create_index=lambda: calls.append("ci") or FakeResponse({"acknowledged": True}),
        create=lambda **kw: FakeResponse({"id": kw["id"], "name": kw["name"], "type": kw["type"], "description": ""})))
    gw.create_value_list(name="l", description="d", type="keyword", id="vl2")
    assert calls == ["ci"]  # not both booleans -> create_index ran


def test_create_value_list_skips_index_when_both_present():
    calls = []
    gw = _gw(lists=_lists(
        get_index_status=lambda: FakeResponse({"list_index": True, "list_item_index": True}),
        create_index=lambda: calls.append("ci"),
        create=lambda **kw: FakeResponse({"id": kw["id"], "name": kw["name"], "type": kw["type"], "description": ""})))
    gw.create_value_list(name="l", description="d", type="keyword", id="vl3")
    assert calls == []  # both true -> skip create_index


def test_create_value_list_duplicate_id_maps_rejected():
    def dup(**kw):
        raise ConflictError("exists", meta=SimpleNamespace(status=409), body=None)
    gw = _gw(lists=_lists(
        get_index_status=lambda: FakeResponse({"list_index": True, "list_item_index": True}),
        create=dup))
    with pytest.raises(KibanaRejected):
        gw.create_value_list(name="l", description="d", type="keyword", id="dupe")


def test_delete_value_list_passes_force_as_ignore_references():
    calls = {}
    gw = _gw(lists=_lists(delete=lambda **kw: calls.update(kw) or FakeResponse({"id": kw["id"]})))
    gw.delete_value_list(id="vl9", force=True)
    assert calls == {"id": "vl9", "ignore_references": True}


def test_delete_value_list_missing_maps_not_found():
    def boom(**kw):
        raise NotFoundError("gone", meta=SimpleNamespace(status=404), body=None)
    gw = _gw(lists=_lists(delete=boom))
    with pytest.raises(KibanaNotFound):
        gw.delete_value_list(id="nope", force=False)


# --- value-list item CRUD (#73 follow-ups task 2) ---


def test_create_value_list_item_ensures_index_and_maps_body():
    calls = []

    def status():
        raise NotFoundError("absent", meta=SimpleNamespace(status=404), body=None)

    captured = {}

    def create_item(**kw):
        captured.update(kw)
        return FakeResponse({
            "id": "vli1", "list_id": kw["list_id"], "value": kw["value"],
            "type": "ip", "@timestamp": "2026-07-18T00:00:00Z",
        })

    gw = _gw(lists=_lists(
        get_index_status=status,
        create_index=lambda: calls.append("create_index") or FakeResponse({"acknowledged": True}),
        create_item=create_item,
    ))
    item = gw.create_value_list_item(list_id="vl1", value="10.0.0.1")
    assert calls == ["create_index"]  # _ensure_value_list_index runs first
    assert captured == {"list_id": "vl1", "value": "10.0.0.1"}  # no raw passthrough of extras
    assert item.id == "vli1"
    assert item.list_id == "vl1"
    assert item.value == "10.0.0.1"
    assert item.type == "ip"
    assert item.timestamp == "2026-07-18T00:00:00Z"  # "@timestamp" body key -> DTO .timestamp


def test_find_value_list_items_paginates():
    # lists.find_items defaults to ~20/page; a full page (100) then a short
    # page (<100) must both be collected, proving no silent truncation.
    def paged(**kw):
        page = kw.get("page", 1)
        assert kw.get("per_page") == 100
        start = (page - 1) * 100
        count = 100 if page == 1 else 20
        data = [
            {"id": f"vli{start + i}", "list_id": "vl1", "value": f"v{start + i}",
             "type": "ip", "@timestamp": "2026-07-18T00:00:00Z"}
            for i in range(count)
        ]
        return FakeResponse({"data": data, "page": page, "per_page": 100, "total": 120})

    gw = _gw(lists=_lists(find_items=paged))
    items = gw.find_value_list_items(list_id="vl1")
    assert len(items) == 120  # both pages present -> no truncation
    assert items[0].id == "vli0"
    assert items[-1].id == "vli119"


def test_delete_value_list_item():
    captured = {}
    gw = _gw(lists=_lists(
        delete_item=lambda **kw: captured.update(kw) or FakeResponse({"id": kw["id"]})
    ))
    assert gw.delete_value_list_item(item_id="vli9") is None
    assert captured == {"id": "vli9"}


# --- detection-rule RMW (#73 follow-ups task 3): replace + enable/disable ---

# Real GET body shape for a rule with a non-default query window / actions,
# used to prove the RMW echoes everything instead of wiping it on the PUT.
# Carries BOTH id and rule_id (mirrors _RULE above) so tests can prove the
# unused identifier is stripped rather than echoed alongside the caller's.
_RMW_RULE = {
    "id": "r1",
    "rule_id": "orig-rid",
    "from": "now-30m",
    "interval": "10m",
    "tags": ["orig"],
    "actions": [{"id": "a"}],
    "enabled": False,
    "immutable": False,
    "created_at": "t",
    "name": "N",
    "description": "d",
    "severity": "low",
    "risk_score": 21,
    "type": "query",
}


def _fake_update_rule(captured):
    # A REAL keyword-only signature (not **kwargs) — a catch-all fake would
    # collapse _rmw_body's allowlist to nothing meaningful and hide whether
    # the intersection logic actually works.
    def update_rule(*, type, name, description, severity, risk_score, id=None, rule_id=None,
                     actions=None, enabled=None, from_=None, interval=None, tags=None):
        captured.update(dict(
            type=type, name=name, description=description, severity=severity,
            risk_score=risk_score, id=id, rule_id=rule_id, actions=actions,
            enabled=enabled, from_=from_, interval=interval, tags=tags,
        ))
        return FakeResponse({**_RMW_RULE, "name": name, "enabled": bool(enabled)})

    return update_rule


def test_replace_rule_translates_from_and_echoes():
    captured = {}
    gw = _gw(detection_engine=SimpleNamespace(
        get_rule=lambda **kw: FakeResponse(_RMW_RULE),
        update_rule=_fake_update_rule(captured),
    ))
    gw.replace_detection_rule(id="r1", rule_id="", changes={"name": "new"})
    assert captured["from_"] == "now-30m"  # "from" -> "from_" translated AND kept, not dropped
    assert captured["interval"] == "10m"
    assert captured["tags"] == ["orig"]
    assert captured["actions"] == [{"id": "a"}]
    assert captured["enabled"] is False  # echoed unchanged (replace doesn't touch it)
    assert captured["name"] == "new"  # the caller's change applied
    assert captured["id"] == "r1"  # path id re-set
    assert captured["rule_id"] is None  # echoed rule_id stripped, not sent alongside id
    assert "created_at" not in captured  # read-only field: not an update_rule param -> dropped


def test_replace_rule_rule_id_path_strips_echoed_id():
    # Mirror of the id-primary test above, but selecting the rule via
    # rule_id: the echoed "id" from the fetched rule must be stripped, not
    # sent alongside the caller's rule_id (both are real update_rule kwargs
    # and Kibana 400s on the redundant one — the update_package_policy
    # class of bug).
    captured = {}
    gw = _gw(detection_engine=SimpleNamespace(
        get_rule=lambda **kw: FakeResponse(_RMW_RULE),
        update_rule=_fake_update_rule(captured),
    ))
    gw.replace_detection_rule(id="", rule_id="probe-rid", changes={"name": "new"})
    assert captured["rule_id"] == "probe-rid"  # path rule_id re-set
    assert captured["id"] is None  # echoed id stripped, not sent alongside rule_id


def test_replace_rule_refuses_immutable():
    def boom(**kw):
        raise AssertionError("update_rule must not be called for an immutable rule")

    gw = _gw(detection_engine=SimpleNamespace(
        get_rule=lambda **kw: FakeResponse({**_RMW_RULE, "immutable": True}),
        update_rule=boom,
    ))
    with pytest.raises(KibanaRejected):
        gw.replace_detection_rule(id="r1", rule_id="", changes={"name": "new"})


def test_enable_sets_enabled_true():
    captured = {}
    # immutable=True here on purpose: enable/disable must NOT refuse a
    # prebuilt rule the way replace_detection_rule does.
    gw = _gw(detection_engine=SimpleNamespace(
        get_rule=lambda **kw: FakeResponse({**_RMW_RULE, "immutable": True, "enabled": False}),
        update_rule=_fake_update_rule(captured),
    ))
    gw.enable_detection_rule(id="r1", rule_id="")
    assert captured["enabled"] is True


def test_disable_sets_enabled_false():
    captured = {}
    gw = _gw(detection_engine=SimpleNamespace(
        get_rule=lambda **kw: FakeResponse({**_RMW_RULE, "immutable": True, "enabled": True}),
        update_rule=_fake_update_rule(captured),
    ))
    gw.disable_detection_rule(id="r1", rule_id="")
    assert captured["enabled"] is False
