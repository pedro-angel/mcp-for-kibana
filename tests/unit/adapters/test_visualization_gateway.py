"""Adapter unit tests for library visualizations and by-reference dashboard
panels (D37), with SimpleNamespace client stubs. The raw shapes are the ones a
live Kibana 9.5.4 returned, trimmed: a by-reference panel reads back as
{"type": "vis", "config": {"ref_id": ...}} and keeps a ref_id whose item was
deleted; visualizations.get_all answers {"data": [...], "meta": {"total"}}."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

# A stored bar chart as Kibana returns it: a breakdown makes it "bar_stacked",
# terms buckets gain rank_by, the data source gains a name.
_STORED_BAR = {
    "title": "Avg price",
    "type": "xy",
    "layers": [{
        "type": "bar_stacked",
        "data_source": {
            "type": "data_view_spec", "index_pattern": "kibana_sample_data_flights",
            "time_field": "timestamp", "name": "kibana_sample_data_flights",
        },
        "x": {"operation": "date_histogram", "field": "timestamp", "suggested_interval": "auto"},
        "y": [{"operation": "average", "field": "AvgTicketPrice", "color": {"type": "auto"}}],
        "breakdown_by": {
            "operation": "terms", "fields": ["Carrier"], "limit": 5,
            "rank_by": {"type": "metric", "metric_index": 0, "direction": "desc"},
        },
    }],
    "query": {"expression": "Cancelled: false", "language": "kql"},
}


def _missing(**_kw):
    raise NotFoundError("not found", meta=SimpleNamespace(status=404), body=None)


def _viz_client(store, calls=None):
    calls = calls if calls is not None else []

    def get(id):
        calls.append(("get", id))
        if id not in store:
            _missing()
        return FakeResponse({"id": id, "data": store[id], "meta": {}})

    def update(id, data):
        calls.append(("update", id))
        store[id] = data
        return FakeResponse({"id": id, "data": data})

    def delete(id):
        calls.append(("delete", id))
        if id not in store:
            _missing()
        del store[id]
        return FakeResponse({})

    return SimpleNamespace(get=get, update=update, delete=delete)


def test_search_visualizations_walks_pages_and_names_chart_types():
    seen = []

    def get_all(**kw):
        seen.append(kw)
        items = {
            1: [{"id": "v1", "data": _STORED_BAR, "meta": {}}],
            2: [{"id": "v2", "data": {"title": "Rows", "type": "data_table", "description": "d"}}],
        }[kw["page"]]
        return FakeResponse({"data": items, "meta": {"page": kw["page"], "total": 2}})

    gw = KibanaPyGateway(make_fake_client(visualizations=SimpleNamespace(get_all=get_all)))
    found = gw.search_visualizations("price")
    assert [(v.id, v.title, v.description, v.chart_type) for v in found] == [
        ("v1", "Avg price", "", "bar"), ("v2", "Rows", "d", "table"),
    ]
    assert seen == [
        {"per_page": 100, "query": "price", "page": 1},
        {"per_page": 100, "query": "price", "page": 2},
    ]


def test_search_visualizations_omits_an_empty_query():
    seen = []

    def get_all(**kw):
        seen.append(kw)
        return FakeResponse({"data": [], "meta": {"total": 0}})

    gw = KibanaPyGateway(make_fake_client(visualizations=SimpleNamespace(get_all=get_all)))
    assert gw.search_visualizations(None) == []
    assert gw.search_visualizations("") == []
    assert all("query" not in kw for kw in seen)


def test_get_visualization_reads_the_stored_config_in_vizspec_terms():
    gw = KibanaPyGateway(make_fake_client(visualizations=_viz_client({"v1": _STORED_BAR})))
    v = gw.get_visualization("v1")
    assert (v.id, v.title, v.chart_type, v.data_view, v.time_field, v.query) == (
        "v1", "Avg price", "bar", "kibana_sample_data_flights", "timestamp", "Cancelled: false",
    )
    assert v.metrics == ({"agg": "average", "field": "AvgTicketPrice"},)
    assert v.group_by == (
        {"field": "timestamp", "kind": "date_histogram"},
        {"field": "Carrier", "kind": "terms", "limit": 5},
    )


def test_update_visualization_reads_before_the_put():
    calls = []
    store = {"v1": _STORED_BAR}
    gw = KibanaPyGateway(make_fake_client(visualizations=_viz_client(store, calls)))
    gw.update_visualization("v1", {"type": "metric", "title": "n"})
    assert calls == [("get", "v1"), ("update", "v1")]
    assert store["v1"] == {"type": "metric", "title": "n"}


def test_update_visualization_never_creates_an_unknown_id():
    # Kibana's PUT would upsert: the read turns the miss into guidance first.
    calls = []
    store = {}
    gw = KibanaPyGateway(make_fake_client(visualizations=_viz_client(store, calls)))
    with pytest.raises(KibanaNotFound, match=r"call search_visualizations to find it$"):
        gw.update_visualization("Avg price", {"type": "metric"})
    assert calls == [("get", "Avg price")]
    assert store == {}


def test_delete_visualization_passes_the_id():
    calls = []
    gw = KibanaPyGateway(make_fake_client(visualizations=_viz_client({"v1": {}}, calls)))
    gw.delete_visualization("v1")
    assert calls == [("delete", "v1")]


def _dash_client(panels):
    return SimpleNamespace(get=lambda id: FakeResponse(
        {"id": id, "data": {"title": "Ops", "description": "", "panels": panels}, "meta": {}}
    ))


def test_get_dashboard_names_a_library_panel_by_its_visualization():
    panels = [
        {"type": "vis", "grid": {"x": 0, "y": 0}, "config": {"title": "inline"}},
        {"type": "vis", "grid": {"x": 0, "y": 10}, "config": {"ref_id": "v1"}, "id": "p-uuid"},
        {"type": "vis", "grid": {"x": 0, "y": 20}, "config": {"ref_id": "v1", "title": "custom"}},
        {"type": "vis", "grid": {"x": 0, "y": 30}, "config": {"ref_id": "gone"}},
    ]
    gw = KibanaPyGateway(make_fake_client(
        dashboards=_dash_client(panels), visualizations=_viz_client({"v1": _STORED_BAR}),
    ))
    got = [(p.index, p.title, p.visualization_id) for p in gw.get_dashboard("d1").panels]
    assert got == [
        (0, "inline", None),
        (1, "Avg price", "v1"),  # the library item's title
        (2, "custom", "v1"),  # a per-panel title override wins
        (3, "", "gone"),  # the item was deleted: Kibana keeps the ref
    ]
