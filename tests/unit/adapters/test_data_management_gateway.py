"""Adapter unit tests for the data-management methods: raw Kibana body -> DTO,
using SimpleNamespace client stubs. Raw shapes were captured from a
live stack, not transcribed from the API docs."""

from types import SimpleNamespace

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client


def _gw(**client_overrides):
    return KibanaPyGateway(make_fake_client(**client_overrides))


def test_create_data_view_maps_summary_and_omits_time_field_when_none():
    captured = {}

    def create(**kw):
        captured.update(kw)
        return FakeResponse({"data_view": {"id": "dv-new", "name": "logs", "title": "logs-*"}})

    summary = _gw(data_views=SimpleNamespace(create=create)).create_data_view("logs-*", None, None)
    assert (summary.id, summary.index_pattern, summary.name) == ("dv-new", "logs-*", "logs")
    assert captured["data_view"]["title"] == "logs-*"
    assert "timeFieldName" not in captured["data_view"]  # omitted when None
    assert "name" not in captured["data_view"]


def test_create_data_view_includes_optional_fields_when_set():
    captured = {}

    def create(**kw):
        captured.update(kw)
        return FakeResponse({"data_view": {"id": "dv1", "name": "n", "title": "logs-*"}})

    _gw(data_views=SimpleNamespace(create=create)).create_data_view("logs-*", "n", "@timestamp")
    assert captured["data_view"] == {"title": "logs-*", "name": "n", "timeFieldName": "@timestamp"}


def test_delete_data_view_calls_client_and_returns_none():
    called = {}

    def delete(view_id):
        called["id"] = view_id
        return FakeResponse({})

    assert _gw(data_views=SimpleNamespace(delete=delete)).delete_data_view("dv-x") is None
    assert called["id"] == "dv-x"


def test_short_url_create_resolve_delete_translation():
    body = {"id": "su1", "slug": "abc", "locator": {"id": "LEGACY_SHORT_URL_LOCATOR"}, "url": "/app/x"}
    su = SimpleNamespace(
        create=lambda **kw: FakeResponse(body),
        resolve=lambda **kw: FakeResponse(body),
        delete=lambda **kw: FakeResponse({}),
    )
    gw = _gw(short_urls=su)
    created = gw.create_short_url("LEGACY_SHORT_URL_LOCATOR", {"url": "/app/x"})
    assert (created.id, created.slug, created.locator_id, created.url) == (
        "su1", "abc", "LEGACY_SHORT_URL_LOCATOR", "/app/x",
    )
    assert gw.resolve_short_url("abc").id == "su1"
    assert gw.delete_short_url("su1") is None


def test_short_url_tolerates_missing_locator_and_url():
    su = SimpleNamespace(create=lambda **kw: FakeResponse({"id": "su2", "slug": "s"}))
    created = _gw(short_urls=su).create_short_url("LEGACY_SHORT_URL_LOCATOR", {"url": "/x"})
    assert (created.locator_id, created.url) == ("", None)


# --- saved_objects export/import (#37) ---

import pytest  # noqa: E402
from kibana.exceptions import BadRequestError  # noqa: E402

from kibana_mcp.core.errors import KibanaRejected  # noqa: E402


def test_export_saved_objects_exactly_one_selector_guard():
    gw = _gw()  # guard fires before any client call
    with pytest.raises(KibanaRejected):
        gw.export_saved_objects(None, None, True)  # neither
    with pytest.raises(KibanaRejected):
        gw.export_saved_objects(["dashboard"], [{"type": "x", "id": "1"}], True)  # both


def test_export_saved_objects_by_objects_returns_raw_list():
    body = [{"type": "index-pattern", "id": "a"}, {"exportedCount": 1}]
    captured = {}

    def export(**kw):
        captured.update(kw)
        return FakeResponse(body)

    gw = _gw(saved_objects=SimpleNamespace(export=export))
    result = gw.export_saved_objects(None, [{"type": "index-pattern", "id": "a"}], True)
    assert result == body
    assert captured["objects"] == [{"type": "index-pattern", "id": "a"}]
    assert captured["include_references_deep"] is True
    assert "type" not in captured


def test_export_saved_objects_by_types_sends_type():
    captured = {}

    def export(**kw):
        captured.update(kw)
        return FakeResponse([{"exportedCount": 0}])

    _gw(saved_objects=SimpleNamespace(export=export)).export_saved_objects(["*"], None, False)
    assert captured["type"] == ["*"] and "objects" not in captured


def test_import_result_maps_success_results_and_warnings():
    body = {
        "success": True, "successCount": 2,
        "successResults": [
            {"type": "dashboard", "id": "src1", "destinationId": "new1"},
            {"type": "index-pattern", "id": "src2"},  # no destinationId -> falls back to id
        ],
        "warnings": [{"type": "action_required", "message": "do X"}],
        "errors": [],
    }
    r = _gw(saved_objects=SimpleNamespace(import_objects=lambda **kw: FakeResponse(body))).import_saved_objects(b"x", False)
    assert r.success is True and r.imported_count == 2
    assert r.objects[0].destination_id == "new1"
    assert r.objects[1].destination_id == "src2"  # fallback
    assert r.warnings == ("action_required",)  # `type` only (content-free), not the message
    assert r.errors == ()


def test_export_dispatches_by_type_when_objects_is_empty_list():
    # Regression: empty objects=[] alongside types must select BY TYPE, not send
    # an empty (0-object) export (the guard/dispatch truthiness mismatch).
    captured = {}

    def export(**kw):
        captured.update(kw)
        return FakeResponse([{"exportedCount": 0}])

    _gw(saved_objects=SimpleNamespace(export=export)).export_saved_objects(["dashboard"], [], True)
    assert captured.get("type") == ["dashboard"] and "objects" not in captured


def test_import_result_maps_errors_on_failure():
    body = {
        "success": False, "successCount": 0, "successResults": [],
        "errors": [{"type": "dashboard", "id": "d1", "error": {"type": "conflict"}}],
        "warnings": [],
    }
    r = _gw(saved_objects=SimpleNamespace(import_objects=lambda **kw: FakeResponse(body))).import_saved_objects(b"x", False)
    assert r.success is False and r.errors == ("dashboard/d1: conflict",)


def test_import_dispatches_overwrite_vs_create_new_copies():
    # overwrite=True -> import_objects(overwrite=True); overwrite=False ->
    # create_new_copies=True. The two modes are mutually exclusive.
    captured = {}

    def import_objects(**kw):
        captured.clear()
        captured.update(kw)
        return FakeResponse({"success": True, "successCount": 0, "successResults": [],
                             "warnings": [], "errors": []})

    gw = _gw(saved_objects=SimpleNamespace(import_objects=import_objects))
    gw.import_saved_objects(b"x", True)
    assert captured.get("overwrite") is True and "create_new_copies" not in captured
    gw.import_saved_objects(b"x", False)
    assert captured.get("create_new_copies") is True and "overwrite" not in captured


def test_export_both_selectors_at_client_maps_to_rejected():
    # If the guard were bypassed, a Kibana 400 still maps to KibanaRejected.
    def export(**kw):
        raise BadRequestError("cannot combine", meta=SimpleNamespace(status=400), body=None)

    with pytest.raises(KibanaRejected):
        _gw(saved_objects=SimpleNamespace(export=export)).export_saved_objects(["dashboard"], None, True)


# --- update_data_view: partial POST, guarded time field (probed on 9.5.4) ---

from kibana_mcp.toolboxes.base import gateway_errors  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

_FLIGHT_FIELDS = {
    "timestamp": {"name": "timestamp", "type": "date"},
    "Carrier": {"name": "Carrier", "type": "string"},
}
_LOG_FIELDS = {
    "@timestamp": {"name": "@timestamp", "type": "date"},
    "bytes": {"name": "bytes", "type": "number"},
}


class _DataViews:
    """A data-view client stub that merges POSTed keys like Kibana does and
    recomputes `fields` from the pattern (Kibana re-reads field caps)."""

    def __init__(self, fields_by_pattern, view=None):
        self.fields_by_pattern = fields_by_pattern
        self.view = view or {
            "id": "dv1", "name": "flights", "title": "flights", "timeFieldName": "timestamp",
        }
        self.posts = []

    def _body(self):
        return {"data_view": {**self.view, "fields": self.fields_by_pattern.get(self.view["title"], {})}}

    def get(self, view_id):
        return FakeResponse(self._body())

    def update(self, **kw):
        self.posts.append(kw)
        self.view = {**self.view, **kw["data_view"]}
        return FakeResponse(self._body())


def _dv(**kw):
    stub = _DataViews({"flights": _FLIGHT_FIELDS, "logs-*": _LOG_FIELDS}, **kw)
    return stub, _gw(data_views=stub)


def test_update_data_view_posts_only_the_given_keys():
    stub, gw = _dv()
    view = gw.update_data_view("dv1", "Flights", None, None)
    assert stub.posts == [{"view_id": "dv1", "data_view": {"name": "Flights"}}]
    assert (view.name, view.index_pattern, view.time_field) == ("Flights", "flights", "timestamp")
    assert view.fields == {"timestamp": "date", "Carrier": "string"}


def test_update_data_view_same_pattern_writes_nothing():
    stub, gw = _dv()
    assert gw.update_data_view("dv1", None, "flights", None).index_pattern == "flights"
    assert stub.posts == []


@pytest.mark.parametrize(("field", "why"), [
    ("Carrier", "is a string field, not a date field, of 'flights'"),
    ("nope", "is not a field of 'flights'"),
])
def test_update_data_view_refuses_a_time_field_that_is_not_a_date(field, why):
    stub, gw = _dv()
    with pytest.raises(KibanaRejected) as exc:
        gw.update_data_view("dv1", None, None, field)
    assert exc.value.message == (
        f"time field '{field}' {why} — pass time_field as one of its date fields: timestamp"
    )
    assert stub.posts == []  # checked before any write


def test_update_data_view_pattern_change_without_a_time_field_is_one_post():
    stub, gw = _dv(view={"id": "dv1", "name": "n", "title": "flights"})
    view = gw.update_data_view("dv1", "logs", "logs-*", None)
    assert stub.posts == [{
        "view_id": "dv1", "data_view": {"name": "logs", "title": "logs-*"}, "refresh_fields": True,
    }]
    assert view.time_field is None and view.index_pattern == "logs-*"


def test_update_data_view_pattern_and_time_field_check_the_new_fields():
    stub, gw = _dv()
    view = gw.update_data_view("dv1", None, "logs-*", "@timestamp")
    assert stub.posts == [
        {"view_id": "dv1", "data_view": {"title": "logs-*"}, "refresh_fields": True},
        {"view_id": "dv1", "data_view": {"timeFieldName": "@timestamp"}},
    ]
    assert (view.index_pattern, view.time_field) == ("logs-*", "@timestamp")


def test_update_data_view_pattern_change_restores_when_the_kept_time_field_is_missing():
    stub, gw = _dv()
    with pytest.raises(KibanaRejected) as exc:
        gw.update_data_view("dv1", "renamed", "logs-*", None)
    assert exc.value.message == (
        "time field 'timestamp' is not a field of 'logs-*' — pass time_field as one of its "
        "date fields: @timestamp (nothing was changed)"
    )
    assert [p["data_view"] for p in stub.posts] == [{"title": "logs-*"}, {"title": "flights"}]
    assert stub.view["title"] == "flights" and stub.view["name"] == "flights"


def test_update_data_view_pattern_with_no_date_fields_names_create_data_view():
    stub = _DataViews({"flights": _FLIGHT_FIELDS, "kw": {"k": {"name": "k", "type": "string"}}})
    with pytest.raises(KibanaRejected, match=r"'kw' has no date fields, .*use create_data_view"):
        _gw(data_views=stub).update_data_view("dv1", None, "kw", None)
    assert stub.view["title"] == "flights"


def test_update_data_view_accepts_a_pattern_that_matches_no_index_yet():
    stub, gw = _dv()
    view = gw.update_data_view("dv1", None, "future-*", None)
    assert (view.index_pattern, view.time_field, view.fields) == ("future-*", "timestamp", {})
    assert len(stub.posts) == 1  # nothing to check against, nothing to restore


def test_update_data_view_lists_at_most_twenty_date_fields():
    many = {f"d{i:02}": {"name": f"d{i:02}", "type": "date"} for i in range(25)}
    stub = _DataViews({"flights": many})
    with pytest.raises(KibanaRejected) as exc:
        _gw(data_views=stub).update_data_view("dv1", None, None, "nope")
    assert exc.value.message.endswith("d18, d19, …")


def test_update_data_view_kibana_400_keeps_the_rejected_payload_prefix():
    stub, _ = _dv()

    def update(**kw):
        raise BadRequestError(
            "[request body.data_view.name]: expected value of type [string]",
            meta=SimpleNamespace(status=400), body=None,
        )

    stub.update = update
    with pytest.raises(ToolError, match=r"^Kibana rejected the payload: \[request body"):
        with gateway_errors():
            _gw(data_views=stub).update_data_view("dv1", "x", None, None)


# --- update_data_view: a write that fails after an earlier one landed says so ---


def _fail_on_post(stub, n, exc):
    """Make the stub's n-th POST (1-based) raise `exc` instead of writing."""
    real = stub.update

    def update(**kw):
        if len(stub.posts) + 1 == n:
            stub.posts.append(kw)
            raise exc
        return real(**kw)

    stub.update = update


def _bad_request(msg):
    return BadRequestError(msg, meta=SimpleNamespace(status=400), body=None)


def test_update_data_view_failed_restore_states_the_new_pattern_and_the_undo():
    stub, gw = _dv()
    _fail_on_post(stub, 2, _bad_request("restore refused"))
    with pytest.raises(KibanaRejected) as exc:
        gw.update_data_view("dv1", None, "logs-*", None)
    assert exc.value.message == (
        "time field 'timestamp' is not a field of 'logs-*' — pass time_field as one of its "
        "date fields: @timestamp. Restoring the old index pattern then failed (Kibana "
        "rejected the payload: restore refused), so the index pattern is now 'logs-*' — "
        "to undo, call update_data_view with index_pattern='flights'"
    )
    assert isinstance(exc.value.__cause__, BadRequestError)  # the original cause is kept
    assert stub.view["title"] == "logs-*"  # the state the message reports


def test_update_data_view_failed_second_write_states_what_landed():
    stub, gw = _dv()
    _fail_on_post(stub, 2, _bad_request("name refused"))
    with pytest.raises(ToolError) as exc:
        with gateway_errors():
            gw.update_data_view("dv1", "Logs", "logs-*", "@timestamp")
    assert str(exc.value) == (
        "Kibana rejected the payload: name refused — the index pattern was changed to "
        "'logs-*' but name='Logs', time_field='@timestamp' was not saved; call "
        "update_data_view again with name='Logs', time_field='@timestamp' to finish, or "
        "to undo, call update_data_view with index_pattern='flights'"
    )
    assert (stub.view["title"], stub.view["name"]) == ("logs-*", "flights")


def test_update_data_view_failed_second_write_keeps_the_error_kind():
    from kibana.exceptions import ConnectionError as KibanaConnectionError

    from kibana_mcp.core.errors import KibanaUnavailable

    stub, gw = _dv()
    _fail_on_post(stub, 2, KibanaConnectionError("down"))
    with pytest.raises(KibanaUnavailable) as exc:
        gw.update_data_view("dv1", None, "logs-*", "@timestamp")
    assert exc.value.message.startswith("cannot reach Kibana")
    assert "was changed to 'logs-*' but time_field='@timestamp' was not saved" in (
        exc.value.message
    )


# --- create_data_view: the time field is checked; a refused call leaves nothing ---


class _CreatingDataViews:
    """A data-view client stub that creates like Kibana does: it stores any
    time field and returns the view with the fields of the indices its pattern
    matches (empty for a pattern that matches none; probed on 9.5.4)."""

    def __init__(self, fields_by_pattern, delete_error=None):
        self.fields_by_pattern = fields_by_pattern
        self.delete_error = delete_error
        self.views = {}
        self.deleted = []

    def create(self, data_view):
        view_id = f"dv-{len(self.views) + len(self.deleted) + 1}"
        self.views[view_id] = {"id": view_id, "name": data_view["title"], **data_view}
        fields = self.fields_by_pattern.get(data_view["title"], {})
        return FakeResponse({"data_view": {**self.views[view_id], "fields": fields}})

    def delete(self, view_id):
        self.deleted.append(view_id)
        if self.delete_error is not None:
            raise self.delete_error
        del self.views[view_id]
        return FakeResponse({})


_NANOS_FIELDS = {
    # A date_nanos field is typed "date" in the view's fields (esTypes keeps
    # the mapping type; probed on 9.5.4).
    "ts": {"name": "ts", "type": "date", "esTypes": ["date_nanos"]},
    "k": {"name": "k", "type": "string", "esTypes": ["keyword"]},
}


def _creating(**kw):
    stub = _CreatingDataViews({
        "flights": _FLIGHT_FIELDS, "nanos": _NANOS_FIELDS,
        "kw": {"k": {"name": "k", "type": "string"}},
    }, **kw)
    return stub, _gw(data_views=stub)


@pytest.mark.parametrize(("field", "why"), [
    ("@timestamp", "is not a field of 'flights'"),
    ("Carrier", "is a string field, not a date field, of 'flights'"),
])
def test_create_data_view_refuses_a_time_field_that_is_not_a_date_and_deletes_the_view(field, why):
    stub, gw = _creating()
    with pytest.raises(KibanaRejected) as exc:
        gw.create_data_view("flights", "Flights", field)
    assert exc.value.message == (
        f"time field '{field}' {why} — pass time_field as one of its date fields: "
        "timestamp (no data view was created)"
    )
    assert stub.deleted == ["dv-1"] and stub.views == {}  # nothing left behind


def test_create_data_view_on_a_pattern_without_date_fields_says_to_omit_the_time_field():
    stub, gw = _creating()
    with pytest.raises(KibanaRejected) as exc:
        gw.create_data_view("kw", None, "k")
    assert exc.value.message == (
        "time field 'k' is a string field, not a date field, of 'kw' — 'kw' has no date "
        "fields, so a data view over it cannot have a time field; use create_data_view "
        "without time_field (no data view was created)"
    )
    assert stub.views == {}


@pytest.mark.parametrize(("pattern", "field"), [
    ("flights", "timestamp"),
    ("nanos", "ts"),  # date_nanos
])
def test_create_data_view_keeps_a_view_whose_time_field_is_a_date(pattern, field):
    stub, gw = _creating()
    view = gw.create_data_view(pattern, None, field)
    assert (view.id, view.index_pattern) == ("dv-1", pattern)
    assert stub.views["dv-1"]["timeFieldName"] == field and stub.deleted == []


def test_create_data_view_accepts_any_time_field_on_a_pattern_that_matches_no_index():
    stub, gw = _creating()
    view = gw.create_data_view("future-*", None, "@timestamp")
    assert view.id == "dv-1" and stub.views["dv-1"]["timeFieldName"] == "@timestamp"
    assert stub.deleted == []  # no fields yet: nothing to check against


def test_create_data_view_failed_cleanup_names_the_view_and_the_fix():
    stub, gw = _creating(delete_error=_bad_request("delete refused"))
    with pytest.raises(KibanaRejected) as exc:
        gw.create_data_view("flights", None, "@timestamp")
    assert exc.value.message == (
        "time field '@timestamp' is not a field of 'flights' — pass time_field as one of "
        "its date fields: timestamp. Deleting the data view this call created then failed "
        "(Kibana rejected the payload: delete refused), so data view 'dv-1' exists with "
        "time field '@timestamp' — fix it with update_data_view (view_id='dv-1', "
        "time_field=<a date field>), or remove it with delete_data_view (view_id='dv-1')"
    )
    assert isinstance(exc.value.__cause__, BadRequestError)  # the original cause is kept
    assert "dv-1" in stub.views  # the state the message reports


def _refusing_create(message, views=None):
    def create(data_view):
        raise _bad_request(message)

    listing = [] if views is None else views
    return _gw(data_views=SimpleNamespace(
        create=create, get_all=lambda: FakeResponse({"data_view": listing}),
    ))


def test_create_data_view_duplicate_name_names_the_existing_view_and_update_data_view():
    gw = _refusing_create("Duplicate data view: flights", [
        {"id": "dv-old", "name": "flights", "title": "flights"},
        {"id": "dv-other", "name": "Flights 2", "title": "flights"},
    ])
    with pytest.raises(ToolError) as exc:
        with gateway_errors():
            gw.create_data_view("flights", None, None)
    assert str(exc.value) == (
        "Kibana rejected the payload: Duplicate data view: flights — a data view named "
        "'flights' (id 'dv-old') already exists: use it, change it in place with "
        "update_data_view(view_id='dv-old'), or pass a different name"
    )


def test_create_data_view_duplicate_name_without_the_id_still_names_update_data_view():
    def get_all():
        raise _bad_request("listing refused")

    def create(data_view):
        raise _bad_request("Duplicate data view: Flights")

    gw = _gw(data_views=SimpleNamespace(create=create, get_all=get_all))
    with pytest.raises(KibanaRejected) as exc:
        gw.create_data_view("flights", "Flights", None)
    assert exc.value.detail == (
        "Duplicate data view: Flights — a data view named 'Flights' already exists: use "
        "it, change it in place with update_data_view, or pass a different name"
    )


def test_create_data_view_other_400_is_unchanged():
    with pytest.raises(KibanaRejected) as exc:
        _refusing_create("[request body.data_view.title]: expected string").create_data_view(
            "flights", None, None
        )
    assert (exc.value.message, exc.value.detail) == (
        "Kibana rejected the payload", "[request body.data_view.title]: expected string"
    )


# --- runtime fields, the default data view, saved-objects find/get (D48) ---
# Raw shapes captured on 9.5.4: a view's runtimeFieldMap holds the definitions,
# its fields map lists mapped fields, runtime fields and a composite's
# subfields ('comp.a'), and fieldAttrs holds the custom label and description.

from kibana.exceptions import NotFoundError, SpaceNotFoundError  # noqa: E402

from kibana_mcp.core.errors import KibanaNotFound  # noqa: E402
from kibana_mcp.core.models import DefaultDataView, RuntimeField  # noqa: E402


def _not_found(msg):
    return NotFoundError(msg, meta=SimpleNamespace(status=404), body=None)


class _RuntimeViews:
    """A data-view client stub holding one view with runtime fields, which
    records every write and answers like Kibana 9.5.4 (bodies probed)."""

    def __init__(self, runtime=None, attrs=None, default=""):
        self.view = {
            "id": "dv1", "name": "flights", "title": "kibana_sample_data_flights",
            "fields": {
                "Carrier": {"name": "Carrier", "type": "string"},
                "hour": {"name": "hour", "type": "number"},
                "comp.a": {"name": "comp.a", "type": "string"},
            },
            "runtimeFieldMap": runtime if runtime is not None else {
                "hour": {"type": "long", "script": {"source": "emit(1)"}},
                "comp": {"type": "composite", "script": {"source": "emit(['a': 'x'])"},
                         "fields": {"a": {"type": "keyword"}}},
            },
            "fieldAttrs": attrs or {},
        }
        self.default = default
        self.writes = []

    def get(self, view_id):
        if view_id != "dv1":
            raise _not_found(f"Saved object [index-pattern/{view_id}] not found")
        return FakeResponse({"data_view": self.view})

    def _stored(self, name, runtime_field):
        self.view["runtimeFieldMap"] = {
            **self.view["runtimeFieldMap"],
            name: {k: v for k, v in runtime_field.items() if k in ("type", "script", "fields")},
        }
        return FakeResponse({"fields": [], "data_view": self.view})

    def create_runtime_field(self, view_id, name, runtime_field):
        self.writes.append(("POST", name, runtime_field))
        return self._stored(name, runtime_field)

    def create_or_update_runtime_field(self, view_id, name, runtime_field):
        self.writes.append(("PUT", name, runtime_field))
        return self._stored(name, runtime_field)

    def delete_runtime_field(self, view_id, name):
        self.writes.append(("DELETE", name))
        return FakeResponse({})

    def get_default(self):
        return FakeResponse({"data_view_id": self.default})

    def set_default(self, data_view_id, force=None):
        self.writes.append(("SET_DEFAULT", data_view_id, force))
        self.default = data_view_id or ""
        return FakeResponse({"acknowledged": True})


def _rt(**kw):
    stub = _RuntimeViews(**kw)
    return stub, _gw(data_views=stub)


def test_upsert_runtime_field_creates_with_the_post_and_reports_no_previous():
    stub, gw = _rt()
    out = gw.upsert_runtime_field("dv1", RuntimeField(name="h2", type="long", script="emit(2)"))
    assert stub.writes == [("POST", "h2", {"type": "long", "script": {"source": "emit(2)"}})]
    assert out.field == RuntimeField(name="h2", type="long", script="emit(2)")
    assert out.previous is None and out.view_id == "dv1"


def test_upsert_runtime_field_without_a_script_sends_only_the_type():
    stub, gw = _rt()
    out = gw.upsert_runtime_field("dv1", RuntimeField(name="h2", type="keyword", script=None))
    assert stub.writes == [("POST", "h2", {"type": "keyword"})]
    assert out.field.script is None


def test_upsert_runtime_field_replaces_with_the_put_and_returns_what_it_replaced():
    stub, gw = _rt(attrs={"hour": {"customLabel": "Hour", "customDescription": "UTC", "count": 3}})
    out = gw.upsert_runtime_field("dv1", RuntimeField(name="hour", type="keyword", script="emit('x')"))
    # The PUT drops a custom label and description unless re-sent (probed): both go back.
    assert stub.writes == [("PUT", "hour", {
        "type": "keyword", "script": {"source": "emit('x')"},
        "customLabel": "Hour", "customDescription": "UTC",
    })]
    assert out.previous == RuntimeField(name="hour", type="long", script="emit(1)")
    assert out.field == RuntimeField(name="hour", type="keyword", script="emit('x')")


def test_upsert_runtime_field_refuses_a_mapped_name_before_any_write():
    stub, gw = _rt()
    with pytest.raises(KibanaRejected) as exc:
        gw.upsert_runtime_field("dv1", RuntimeField(name="Carrier", type="keyword", script=None))
    assert exc.value.message == (
        "'Carrier' is already a field of data view 'dv1' ('kibana_sample_data_flights') and "
        "not a runtime field (a mapped or scripted field) — pick a name the view does not "
        "have; describe_data_view lists its fields"
    )
    assert stub.writes == []


def test_upsert_runtime_field_refuses_a_composite_and_its_subfields():
    stub, gw = _rt()
    with pytest.raises(KibanaRejected, match=r"'comp' of data view 'dv1' is a composite field, "
                                             r"which this tool does not replace — remove it "
                                             r"with delete_runtime_field first"):
        gw.upsert_runtime_field("dv1", RuntimeField(name="comp", type="long", script=None))
    with pytest.raises(KibanaRejected, match=r"'comp.a' is a subfield of the composite runtime "
                                             r"field 'comp'"):
        gw.upsert_runtime_field("dv1", RuntimeField(name="comp.a", type="long", script=None))
    assert stub.writes == []


def test_upsert_runtime_field_create_race_names_the_retry():
    stub, gw = _rt()

    def taken(**kw):
        raise _bad_request("Field [name = h2] already exists.")

    stub.create_runtime_field = taken
    with pytest.raises(KibanaRejected) as exc:
        gw.upsert_runtime_field("dv1", RuntimeField(name="h2", type="long", script="emit(1)"))
    assert exc.value.detail == (
        "Field [name = h2] already exists. — a field 'h2' was added to data view 'dv1' after "
        "this call read it; call upsert_runtime_field again (a runtime field is then "
        "replaced and returned as `previous`)"
    )


def test_upsert_runtime_field_other_400_keeps_kibanas_message():
    stub, gw = _rt()

    def bad(**kw):
        raise _bad_request("[request body.name]: value has length [0]")

    stub.create_runtime_field = bad
    with pytest.raises(KibanaRejected) as exc:
        gw.upsert_runtime_field("dv1", RuntimeField(name="h2", type="long", script=None))
    assert (exc.value.message, exc.value.detail) == (
        "Kibana rejected the payload", "[request body.name]: value has length [0]"
    )


def test_upsert_runtime_field_unknown_view_names_list_data_views():
    _, gw = _rt()
    with pytest.raises(KibanaNotFound, match=r"data view 'nope' not found — view_id is the id "
                                             r"that create_data_view or list_data_views"):
        gw.upsert_runtime_field("nope", RuntimeField(name="h2", type="long", script=None))


def test_delete_runtime_field_returns_the_deleted_definition():
    stub, gw = _rt()
    assert gw.delete_runtime_field("dv1", "hour") == RuntimeField(
        name="hour", type="long", script="emit(1)"
    )
    deleted = gw.delete_runtime_field("dv1", "comp")
    assert (deleted.type, deleted.fields) == ("composite", {"a": "keyword"})
    assert stub.writes == [("DELETE", "hour"), ("DELETE", "comp")]


def test_delete_runtime_field_miss_lists_the_views_runtime_fields():
    stub, gw = _rt()
    with pytest.raises(KibanaNotFound) as exc:
        gw.delete_runtime_field("dv1", "zz")
    assert exc.value.message == (
        "data view 'dv1' has no runtime field 'zz' — its runtime fields: comp, hour"
    )
    _, empty = _rt(runtime={})
    with pytest.raises(KibanaNotFound, match=r"— it has no runtime fields$"):
        empty.delete_runtime_field("dv1", "zz")
    assert stub.writes == []


def test_delete_runtime_field_refuses_a_mapped_field_and_a_composite_subfield():
    stub, gw = _rt()
    with pytest.raises(KibanaRejected, match=r"'Carrier' is a field of data view 'dv1' but not "
                                             r"a runtime field — only runtime fields can be "
                                             r"deleted \(its runtime fields: comp, hour\)"):
        gw.delete_runtime_field("dv1", "Carrier")
    with pytest.raises(KibanaRejected, match=r"delete 'comp' to remove it with its subfields"):
        gw.delete_runtime_field("dv1", "comp.a")
    assert stub.writes == []


def test_delete_runtime_field_gone_between_read_and_delete_says_nothing_was_deleted():
    stub, gw = _rt()

    def gone(**kw):
        raise _not_found("Field [index_pattern = dv1, field = hour] not found.")

    stub.delete_runtime_field = gone
    with pytest.raises(KibanaNotFound, match=r"this call deleted nothing"):
        gw.delete_runtime_field("dv1", "hour")


def test_delete_runtime_field_in_a_space_gets_the_scoped_suffix_once():
    stub = _RuntimeViews()
    gw = KibanaPyGateway(make_fake_client(data_views=stub), space="sales")
    with pytest.raises(KibanaNotFound) as exc:
        gw.delete_runtime_field("dv1", "zz")
    assert exc.value.message.endswith("its runtime fields: comp, hour (in space 'sales')")


def test_get_default_data_view_unset_set_and_dangling():
    _, unset = _rt(default="")
    assert unset.get_default_data_view() == DefaultDataView(None, None, None, False)
    _, set_ = _rt(default="dv1")
    assert set_.get_default_data_view() == DefaultDataView(
        "dv1", "flights", "kibana_sample_data_flights", True
    )
    # Kibana keeps the id of a deleted default view (probed): it reads as not existing.
    _, dangling = _rt(default="gone-1")
    assert dangling.get_default_data_view() == DefaultDataView("gone-1", None, None, False)


def test_get_default_data_view_missing_space_stays_the_space_error():
    stub = _RuntimeViews(default="dv1")

    def no_space(view_id):
        raise SpaceNotFoundError("sales")

    stub.get = no_space
    with pytest.raises(Exception) as exc:
        KibanaPyGateway(make_fake_client(data_views=stub), space="sales").get_default_data_view()
    assert "space 'sales' not found" in str(exc.value)


def test_set_default_data_view_forces_and_returns_the_previous_id():
    stub, gw = _rt(default="old-1")
    change = gw.set_default_data_view("dv1")
    # force=True: without it Kibana keeps a default already set and answers 200 (probed).
    assert stub.writes == [("SET_DEFAULT", "dv1", True)]
    assert change.previous_id == "old-1"
    assert change.current == DefaultDataView(
        "dv1", "flights", "kibana_sample_data_flights", True
    )
    # old-1 names no view (Kibana keeps a deleted default's id): passing it back
    # would be refused, so the result says so.
    assert change.previous_exists is False


def test_set_default_data_view_previous_exists_when_the_old_default_is_a_view():
    stub, gw = _rt(default="dv0")
    views = {"dv0": {**stub.view, "id": "dv0"}, "dv1": stub.view}

    def get(view_id):
        if view_id not in views:
            raise _not_found(f"Saved object [index-pattern/{view_id}] not found")
        return FakeResponse({"data_view": views[view_id]})

    stub.get = get
    assert gw.set_default_data_view("dv1").previous_exists is True
    # A repeat of the current default reads no other view and reports it.
    assert gw.set_default_data_view("dv1").previous_exists is True
    assert gw.set_default_data_view(None).previous_exists is True
    assert gw.set_default_data_view("dv1").previous_exists is False  # none was set


def test_set_default_data_view_none_clears_and_unset_previous_is_none():
    stub, gw = _rt(default="")
    change = gw.set_default_data_view(None)
    assert stub.writes == [("SET_DEFAULT", None, True)]
    assert change.previous_id is None and change.current.id is None


def test_set_default_data_view_refuses_an_unknown_view_before_writing():
    stub, gw = _rt(default="dv1")
    with pytest.raises(KibanaNotFound, match=r"call list_data_views to find it"):
        gw.set_default_data_view("bogus-id")
    assert stub.writes == []


# Raw _find hit and GET body, trimmed from 9.5.4 (the flights sample dashboard).
_DASH = {
    "type": "dashboard", "id": "7adfa750", "namespaces": ["default"],
    "attributes": {"title": "[Flights] Global Flight Dashboard"},
    "references": [
        {"name": "4:panel_4", "type": "search", "id": "571aaf70"},
        {"type": "index-pattern", "id": "d3d7af60", "name": "7:layer-a"},
        {"type": "index-pattern", "id": "d3d7af60", "name": "7:layer-b"},
    ],
    "managed": False, "updated_at": "2026-10-25T03:03:32.348Z",
    "created_at": "2026-10-25T03:03:32.348Z", "version": "WzE2OTUsMV0=", "score": 0,
}


def test_find_saved_objects_sends_title_and_name_fields_and_maps_summaries():
    captured = {}

    def find(**kw):
        captured.update(kw)
        return FakeResponse({"page": 2, "per_page": 5, "total": 6, "saved_objects": [
            _DASH,
            {"type": "index-pattern", "id": "d3d7af60", "namespaces": ["default"],
             "attributes": {"title": "kibana_sample_data_flights", "name": "Flights"}},
            {"type": "config", "id": "9.5.4", "attributes": {}},
        ]})

    page = _gw(saved_objects=SimpleNamespace(find=find)).find_saved_objects(
        ["dashboard", "index-pattern"], "flights", {"type": "search", "id": "571aaf70"}, 2, 5
    )
    # search_fields: without it Kibana matches every attribute (probed: a word
    # only in a dashboard's description found it), not the title and name a hit shows.
    assert captured == {
        "type": ["dashboard", "index-pattern"], "fields": ["title", "name"], "page": 2,
        "per_page": 5, "search": "flights", "search_fields": ["title", "name"],
        "has_reference": {"type": "search", "id": "571aaf70"},
    }
    assert (page.total, page.page, page.per_page) == (6, 2, 5)
    dash, view, config = page.saved_objects
    assert (dash.type, dash.id, dash.title, dash.name, dash.managed) == (
        "dashboard", "7adfa750", "[Flights] Global Flight Dashboard", None, False,
    )
    assert dash.namespaces == ("default",) and dash.updated_at == "2026-10-25T03:03:32.348Z"
    assert (view.title, view.name) == ("kibana_sample_data_flights", "Flights")
    assert (config.title, config.name, config.namespaces, config.updated_at) == (
        None, None, (), None
    )


def test_find_saved_objects_omits_search_and_reference_when_none():
    captured = {}

    def find(**kw):
        captured.update(kw)
        return FakeResponse({"page": 1, "per_page": 20, "total": 0, "saved_objects": []})

    _gw(saved_objects=SimpleNamespace(find=find)).find_saved_objects(["tag"], None, None, 1, 20)
    assert set(captured) == {"type", "fields", "page", "per_page"}


def test_get_saved_object_keeps_distinct_references_in_order():
    obj = _gw(saved_objects=SimpleNamespace(get=lambda **kw: FakeResponse(_DASH))).get_saved_object(
        "dashboard", "7adfa750"
    )
    assert obj.references == (
        {"type": "search", "id": "571aaf70"}, {"type": "index-pattern", "id": "d3d7af60"},
    )
    assert obj.attributes == {"title": "[Flights] Global Flight Dashboard"}
    assert (obj.created_at, obj.namespaces, obj.managed) == (
        "2026-10-25T03:03:32.348Z", ("default",), False,
    )


def test_get_saved_object_miss_names_find_saved_objects():
    def get(**kw):
        raise _not_found("Saved object [dashboard/x] not found")

    gw = KibanaPyGateway(make_fake_client(saved_objects=SimpleNamespace(get=get)), space="sales")
    with pytest.raises(KibanaNotFound) as exc:
        gw.get_saved_object("dashboard", "x")
    assert exc.value.message == (
        "saved object 'dashboard/x' not found — check the type and id with "
        "find_saved_objects(types=['dashboard']) (in space 'sales')"
    )


@pytest.mark.parametrize("type_", ["fleet-proxy", "ingest-outputs", "action", "config", "*"])
def test_get_saved_object_refuses_a_type_outside_the_allowlist_before_kibana(type_):
    # 9.5.4 serves fleet-proxy (certificate_key, proxy_headers) and ingest-outputs
    # (config_yaml, service_token) with those attributes in plain text: Kibana
    # strips only attributes registered as encrypted, so they are never fetched.
    def get(**kw):
        raise AssertionError("must not reach Kibana")

    gw = _gw(saved_objects=SimpleNamespace(get=get))
    with pytest.raises(KibanaRejected) as exc:
        gw.get_saved_object(type_, "x")
    assert f"does not read '{type_}' objects" in exc.value.message
    # The per-type ways forward are the core's (tests/unit/toolboxes).
    from kibana_mcp.core.saved_objects import unreadable_type_message

    assert exc.value.message == unreadable_type_message(type_)


def test_get_saved_object_blank_type_or_id_is_rejected_before_kibana():
    def get(**kw):
        raise AssertionError("must not reach kibana-py, whose ValueError is unmapped")

    gw = _gw(saved_objects=SimpleNamespace(get=get))
    for type_, id_ in ((" ", "x"), ("dashboard", " ")):
        with pytest.raises(KibanaRejected, match="must not be blank"):
            gw.get_saved_object(type_, id_)
