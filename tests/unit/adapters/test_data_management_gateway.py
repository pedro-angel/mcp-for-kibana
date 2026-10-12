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
