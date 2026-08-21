"""Adapter translation tests for the streams toolbox. Raw bodies mirror shapes
captured from a live stack: wired streams carry
ingest.wired.{routing,fields}; classic streams carry ingest.classic{} and no
wired block; lifecycle is a single-key discriminated object."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

# Real GET /api/streams/logs.ecs/_ingest ingest block (trimmed), a wired stream.
_WIRED_INGEST = {
    "lifecycle": {"dsl": {}},
    "failure_store": {"lifecycle": {"enabled": {"data_retention": "30d"}}},
    "settings": {},
    "processing": {"steps": [], "updated_at": "2026-07-11T13:23:57.798Z"},
    "wired": {
        "routing": [],
        "fields": {
            "@timestamp": {"type": "date"},
            "host.name": {"type": "keyword", "ignore_above": 1024},
            "stream.name": {"type": "system"},
        },
    },
}
_WIRED_STREAM = {
    "type": "wired",
    "name": "logs.ecs",
    "description": "Root stream for logs.ecs",
    "updated_at": "2026-07-11T13:23:57.798Z",
    "ingest": _WIRED_INGEST,
}
# Classic stream (apm): ingest.classic{}, no wired block, lifecycle inherit.
_CLASSIC_INGEST = {
    "lifecycle": {"inherit": {}},
    "processing": {"steps": []},
    "settings": {},
    "classic": {},
    "failure_store": {},
}
_CLASSIC_STREAM = {
    "type": "classic",
    "name": "traces-apm-default",
    "description": "",
    "updated_at": "2026-07-11T00:00:00.000Z",
    "ingest": _CLASSIC_INGEST,
}


def _gw(**namespaces):
    return KibanaPyGateway(make_fake_client(**namespaces))


def test_list_streams_bare_list():
    gw = _gw(
        streams=SimpleNamespace(
            get_all=lambda: FakeResponse({"streams": [_WIRED_STREAM, _CLASSIC_STREAM]})
        )
    )
    streams = gw.list_streams()
    assert [s.name for s in streams] == ["logs.ecs", "traces-apm-default"]
    assert streams[0].type == "wired"
    assert streams[1].type == "classic"
    assert streams[0].description == "Root stream for logs.ecs"


def test_get_stream_wired_summarizes_ingest_counts():
    gw = _gw(streams=SimpleNamespace(get=lambda *, name: FakeResponse({"stream": _WIRED_STREAM})))
    s = gw.get_stream("logs.ecs")
    assert s.name == "logs.ecs"
    assert s.type == "wired"
    assert s.lifecycle == "dsl"  # discriminator key
    assert s.data_retention is None  # {"dsl": {}} has no data_retention
    assert s.processing_step_count == 0
    assert s.routing_count == 0
    assert s.field_count == 3  # three managed fields


def test_get_stream_classic_has_no_wired_counts():
    gw = _gw(streams=SimpleNamespace(get=lambda *, name: FakeResponse({"stream": _CLASSIC_STREAM})))
    s = gw.get_stream("traces-apm-default")
    assert s.type == "classic"
    assert s.lifecycle == "inherit"
    assert s.routing_count == 0
    assert s.field_count == 0  # classic streams have no wired.fields


def test_get_stream_extracts_data_retention_when_set():
    body = {**_WIRED_STREAM, "ingest": {**_WIRED_INGEST, "lifecycle": {"dsl": {"data_retention": "30d"}}}}
    gw = _gw(streams=SimpleNamespace(get=lambda *, name: FakeResponse({"stream": body})))
    s = gw.get_stream("logs.ecs")
    assert s.lifecycle == "dsl"
    assert s.data_retention == "30d"  # extracted from under the lifecycle mode


def test_get_stream_missing_maps_to_domain_error():
    def raise_nf(*, name):
        raise NotFoundError("Not Found", meta=SimpleNamespace(status=404), body=None)

    gw = _gw(streams=SimpleNamespace(get=raise_nf))
    with pytest.raises(KibanaNotFound):
        gw.get_stream("no-such-stream")


def test_get_stream_ingest_wired_surfaces_field_types():
    gw = _gw(
        streams=SimpleNamespace(get_ingest=lambda *, name: FakeResponse({"ingest": _WIRED_INGEST}))
    )
    ing = gw.get_stream_ingest("logs.ecs")
    assert ing.lifecycle == "dsl"
    assert ing.processing_step_count == 0
    assert ing.routing_count == 0
    assert ing.fields == {"@timestamp": "date", "host.name": "keyword", "stream.name": "system"}


def test_get_stream_ingest_classic_has_empty_fields():
    gw = _gw(
        streams=SimpleNamespace(get_ingest=lambda *, name: FakeResponse({"ingest": _CLASSIC_INGEST}))
    )
    ing = gw.get_stream_ingest("traces-apm-default")
    assert ing.lifecycle == "inherit"
    assert ing.fields == {}


def test_stream_ingest_non_dict_nested_degrades_to_empty():
    # A shape drift where ingest/wired/processing come back the WRONG TYPE must
    # degrade to empty via the isinstance guards, never raise.
    bad = {"lifecycle": [], "processing": "nope", "wired": 5}
    gw = _gw(streams=SimpleNamespace(get_ingest=lambda *, name: FakeResponse({"ingest": bad})))
    ing = gw.get_stream_ingest("x")
    assert ing.lifecycle == ""  # lifecycle a list -> no mode
    assert ing.data_retention is None
    assert ing.processing_step_count == 0
    assert ing.routing_count == 0
    assert ing.fields == {}


def test_get_stream_missing_stream_key_raises():
    # A 200 without the "stream" wrapper key is Tech-Preview envelope drift: surface
    # it as an error, never a phantom-empty Stream an LLM would read as a real one.
    from kibana_mcp.core.errors import KibanaUnavailable

    gw = _gw(streams=SimpleNamespace(get=lambda *, name: FakeResponse({})))
    with pytest.raises(KibanaUnavailable):
        gw.get_stream("x")


def test_get_stream_ingest_missing_ingest_key_raises():
    from kibana_mcp.core.errors import KibanaUnavailable

    gw = _gw(streams=SimpleNamespace(get_ingest=lambda *, name: FakeResponse({})))
    with pytest.raises(KibanaUnavailable):
        gw.get_stream_ingest("x")


def test_stream_field_without_type_is_unknown():
    # A managed field spec lacking `type` maps to "unknown" (matches _normalize_fields).
    ingest = {"lifecycle": {"dsl": {}}, "wired": {"routing": [], "fields": {"f1": {}}}}
    gw = _gw(streams=SimpleNamespace(get_ingest=lambda *, name: FakeResponse({"ingest": ingest})))
    ing = gw.get_stream_ingest("x")
    assert ing.fields == {"f1": "unknown"}


# --- streams write/destructive tier (adapter translation) ---

from kibana_mcp.adapters.kibana.gateway import _to_stream_write_result  # noqa: E402
from kibana_mcp.core.errors import KibanaRejected, KibanaUnavailable  # noqa: E402


def test_to_stream_write_result_happy():
    r = _to_stream_write_result({"acknowledged": True, "result": "noop"})
    assert (r.acknowledged, r.result) == (True, "noop")


def test_to_stream_write_result_result_only_defaults_acknowledged_true():
    r = _to_stream_write_result({"result": "deleted"})  # delete/disable may omit acknowledged
    assert (r.acknowledged, r.result) == (True, "deleted")


def test_to_stream_write_result_empty_envelope_raises():
    with pytest.raises(KibanaUnavailable):
        _to_stream_write_result({"unexpected": 1})


def test_to_stream_write_result_unacknowledged_raises():
    with pytest.raises(KibanaUnavailable):
        _to_stream_write_result({"acknowledged": False, "result": ""})


def test_enable_streams_maps_ack():
    gw = _gw(streams=SimpleNamespace(enable=lambda: FakeResponse({"acknowledged": True, "result": "noop"})))
    assert gw.enable_streams().result == "noop"


def test_resync_streams_maps_ack():
    gw = _gw(streams=SimpleNamespace(resync=lambda: FakeResponse({"acknowledged": True, "result": "updated"})))
    assert gw.resync_streams().result == "updated"


def test_fork_stream_builds_disabled_where():
    calls = {}

    def fake_fork(*, name, stream_name, where, status):
        calls.update(name=name, stream_name=stream_name, where=where, status=status)
        return FakeResponse({"acknowledged": True, "result": "created"})

    gw = _gw(streams=SimpleNamespace(fork=fake_fork))
    r = gw.fork_stream("logs.ecs", "logs.ecs.app", "service.name", "app")
    assert r.result == "created"
    assert calls == dict(
        name="logs.ecs", stream_name="logs.ecs.app",
        where={"field": "service.name", "eq": "app"}, status="disabled",
    )


def test_set_stream_retention_rmw():
    import copy
    original = {"lifecycle": {"dsl": {}}, "processing": {"steps": [], "updated_at": "x"},
                "settings": {}, "failure_store": {},
                "wired": {"fields": {"@timestamp": {"type": "date"}}, "routing": []}}
    server = {}  # what update_ingest actually persisted ("server state")

    def fake_get_ingest(*, name):
        # deep-copy so the gateway's in-place mutation can't leak into server state;
        # the read-back reflects the PERSISTED write (a real round-trip), not the
        # local mutation — so a no-op update_ingest would fail the retention assert.
        return FakeResponse({"ingest": copy.deepcopy(server.get("ingest", original))})

    def fake_update_ingest(*, name, ingest):
        server["ingest"] = copy.deepcopy(ingest)
        return FakeResponse({"acknowledged": True, "result": "updated"})

    gw = _gw(streams=SimpleNamespace(get_ingest=fake_get_ingest, update_ingest=fake_update_ingest))
    out = gw.set_stream_retention("logs.ecs.app", "7d")
    assert "updated_at" not in server["ingest"]["processing"]  # read-only stripped before write
    assert server["ingest"]["lifecycle"] == {"dsl": {"data_retention": "7d"}}
    assert out.lifecycle == "dsl" and out.data_retention == "7d"  # from the read-back


def test_set_stream_retention_unacknowledged_raises():
    ingest = {"lifecycle": {"dsl": {}}, "processing": {"steps": []}}
    gw = _gw(streams=SimpleNamespace(
        get_ingest=lambda *, name: FakeResponse({"ingest": dict(ingest)}),
        update_ingest=lambda *, name, ingest: FakeResponse({"acknowledged": False})))
    with pytest.raises(KibanaUnavailable):
        gw.set_stream_retention("logs.ecs.app", "7d")


def test_set_stream_retention_rejects_ilm():
    gw = _gw(streams=SimpleNamespace(
        get_ingest=lambda *, name: FakeResponse({"ingest": {"lifecycle": {"ilm": {"policy": "p"}}}})))
    with pytest.raises(KibanaRejected):
        gw.set_stream_retention("logs.ecs.app", "7d")


def _streams_body(*items):  # (name, type)
    return {"streams": [{"name": n, "type": t, "description": ""} for n, t in items]}


def test_delete_stream_refuses_root():
    gw = _gw(streams=SimpleNamespace(
        get_all=lambda: FakeResponse(_streams_body(("logs.ecs", "wired"), ("logs.otel", "wired")))))
    with pytest.raises(KibanaRejected):
        gw.delete_stream("logs.ecs", force=False)


def test_delete_stream_classic_logs_sibling_does_not_bypass_root():
    gw = _gw(streams=SimpleNamespace(
        get_all=lambda: FakeResponse(_streams_body(("logs", "classic"), ("logs.ecs", "wired")))))
    with pytest.raises(KibanaRejected):
        gw.delete_stream("logs.ecs", force=False)


def test_delete_stream_refuses_parent_with_children():
    gw = _gw(streams=SimpleNamespace(get_all=lambda: FakeResponse(
        _streams_body(("logs.ecs", "wired"), ("logs.ecs.app", "wired"), ("logs.ecs.app.err", "wired")))))
    with pytest.raises(KibanaRejected):
        gw.delete_stream("logs.ecs.app", force=False)


def test_delete_stream_leaf_deletes_and_normalizes():
    calls = {}

    def fake_delete(*, name):
        calls["name"] = name
        return FakeResponse({"acknowledged": True, "result": "deleted"})

    gw = _gw(streams=SimpleNamespace(
        get_all=lambda: FakeResponse(_streams_body(("logs.ecs", "wired"), ("logs.ecs.app", "wired"))),
        delete=fake_delete))
    assert gw.delete_stream(" logs.ecs.app ", force=False).result == "deleted"
    assert calls["name"] == "logs.ecs.app"  # stripped


def test_delete_stream_force_bypasses_guard():
    gw = _gw(streams=SimpleNamespace(
        get_all=lambda: FakeResponse(_streams_body(("logs.ecs", "wired"))),
        delete=lambda *, name: FakeResponse({"acknowledged": True, "result": "deleted"})))
    assert gw.delete_stream("logs.ecs", force=True).result == "deleted"


# --- streams write follow-ups (#71): set_stream_processing, activate/deactivate_fork ---


def test_set_stream_processing_rmw():
    import copy
    original = {
        "processing": {"steps": [], "updated_at": "2026-07-11T13:23:57.798Z"},
        "lifecycle": {"dsl": {"data_retention": "7d"}},
        "settings": {"k": "v"},
        "wired": {"routing": [{"destination": "p.c", "status": "enabled"}], "fields": {}},
    }
    server = {}

    def fake_get_ingest(*, name):
        return FakeResponse({"ingest": copy.deepcopy(server.get("ingest", original))})

    def fake_update_ingest(*, name, ingest):
        server["ingest"] = copy.deepcopy(ingest)
        return FakeResponse({"acknowledged": True, "result": "updated"})

    gw = _gw(streams=SimpleNamespace(get_ingest=fake_get_ingest, update_ingest=fake_update_ingest))
    new_steps = [{"action": "set", "to": "x", "value": "1"}]
    out = gw.set_stream_processing(name="logs.ecs", steps=new_steps)
    sent = server["ingest"]
    assert sent["processing"]["steps"] == new_steps
    assert "updated_at" not in sent["processing"]  # read-only, stripped before write
    assert sent["lifecycle"] == original["lifecycle"]  # preserved (whole-ingest echo)
    assert sent["settings"] == original["settings"]
    assert sent["wired"] == original["wired"]
    assert out.processing_step_count == 1  # from the read-back


def _parent_ingest_with_routing(child_status="disabled"):
    return {
        "processing": {"steps": [], "updated_at": "t"},
        "lifecycle": {"dsl": {}},
        "settings": {},
        "wired": {
            "routing": [
                {"destination": "p.child", "where": {"field": "f", "eq": "v"}, "status": child_status},
                {"destination": "p.other", "status": "enabled"},
            ],
            "fields": {},
        },
    }


def test_activate_fork_flips_routing():
    import copy
    server = {"ingest": _parent_ingest_with_routing(child_status="disabled")}

    def fake_get_ingest(*, name):
        return FakeResponse({"ingest": copy.deepcopy(server["ingest"])})

    def fake_update_ingest(*, name, ingest):
        server["ingest"] = copy.deepcopy(ingest)
        return FakeResponse({"acknowledged": True, "result": "updated"})

    gw = _gw(streams=SimpleNamespace(get_ingest=fake_get_ingest, update_ingest=fake_update_ingest))
    gw.activate_fork(parent="p", child="p.child")
    routing = server["ingest"]["wired"]["routing"]
    by_dest = {r["destination"]: r for r in routing}
    assert by_dest["p.child"]["status"] == "enabled"
    assert by_dest["p.other"]["status"] == "enabled"  # untouched
    assert "updated_at" not in server["ingest"]["processing"]


def test_deactivate_fork_flips_routing():
    import copy
    server = {"ingest": _parent_ingest_with_routing(child_status="enabled")}

    def fake_get_ingest(*, name):
        return FakeResponse({"ingest": copy.deepcopy(server["ingest"])})

    def fake_update_ingest(*, name, ingest):
        server["ingest"] = copy.deepcopy(ingest)
        return FakeResponse({"acknowledged": True, "result": "updated"})

    gw = _gw(streams=SimpleNamespace(get_ingest=fake_get_ingest, update_ingest=fake_update_ingest))
    gw.deactivate_fork(parent="p", child="p.child")
    routing = server["ingest"]["wired"]["routing"]
    by_dest = {r["destination"]: r for r in routing}
    assert by_dest["p.child"]["status"] == "disabled"
    assert by_dest["p.other"]["status"] == "enabled"  # untouched


def test_activate_fork_missing_entry_rejected():
    gw = _gw(streams=SimpleNamespace(
        get_ingest=lambda *, name: FakeResponse({"ingest": _parent_ingest_with_routing()})))
    with pytest.raises(KibanaRejected):
        gw.activate_fork(parent="p", child="p.nope")


def test_deactivate_fork_missing_entry_rejected():
    gw = _gw(streams=SimpleNamespace(
        get_ingest=lambda *, name: FakeResponse({"ingest": _parent_ingest_with_routing()})))
    with pytest.raises(KibanaRejected):
        gw.deactivate_fork(parent="p", child="p.nope")


def test_set_stream_processing_acknowledged_false_raises():
    gw = _gw(streams=SimpleNamespace(
        get_ingest=lambda *, name: FakeResponse({"ingest": {
            "processing": {"steps": []}, "lifecycle": {"dsl": {}},
        }}),
        update_ingest=lambda *, name, ingest: FakeResponse({"acknowledged": False})))
    with pytest.raises(KibanaUnavailable):
        gw.set_stream_processing(name="logs.ecs", steps=[])


def test_activate_fork_acknowledged_false_raises():
    gw = _gw(streams=SimpleNamespace(
        get_ingest=lambda *, name: FakeResponse({"ingest": _parent_ingest_with_routing()}),
        update_ingest=lambda *, name, ingest: FakeResponse({"acknowledged": False})))
    with pytest.raises(KibanaUnavailable):
        gw.activate_fork(parent="p", child="p.child")
