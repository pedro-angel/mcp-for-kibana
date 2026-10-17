"""describe_lens_config / lens_chart_type: a stored Lens config read back in
VizSpec terms (get_visualization, search_visualizations — D37). Every chart
type the server builds must come back as the spec that built it; a config the
Kibana UI made, or a shape this module does not know, must never raise."""

import pytest

from kibana_mcp.core.visualizations.spec import (
    ChartType, FilterSpec, GroupBySpec, MetricAgg, MetricSpec, VizSpec,
)
from kibana_mcp.core.visualizations.translate import (
    describe_lens_config, lens_chart_type, to_lens_config,
)

FLIGHTS = "kibana_sample_data_flights"
COUNT = MetricSpec(agg=MetricAgg.COUNT)
AVG = MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice", label="avg")
TERMS = GroupBySpec(field="Carrier", limit=5)
TERMS2 = GroupBySpec(field="DestCountry")
DATES = GroupBySpec(field="timestamp", kind="date_histogram")


def _spec(chart_type, metrics, group_by, **kw):
    return VizSpec(title="t", chart_type=chart_type, data_view=FLIGHTS, time_field="timestamp",
                   metrics=metrics, group_by=group_by, **kw)


def _metric(m):
    out = {"agg": m.agg.value}
    if m.field:
        out["field"] = m.field
    if m.label:
        out["label"] = m.label
    return out


def _bucket(g):
    out = {"field": g.field, "kind": g.kind}
    if g.kind == "terms":
        out["limit"] = g.limit
    return out


@pytest.mark.parametrize("spec", [
    _spec(ChartType.BAR, [AVG, COUNT], [DATES, TERMS]),
    _spec(ChartType.LINE, [COUNT], [DATES]),
    _spec(ChartType.AREA, [COUNT], [DATES, TERMS]),
    _spec(ChartType.PIE, [COUNT], [TERMS, TERMS2]),
    _spec(ChartType.METRIC, [COUNT, AVG], []),
    _spec(ChartType.TABLE, [COUNT, AVG], [TERMS, TERMS2]),
    _spec(ChartType.GAUGE, [AVG], []),
    _spec(ChartType.HEATMAP, [COUNT], [DATES, TERMS]),
    _spec(ChartType.TAG_CLOUD, [COUNT], [TERMS]),
    _spec(ChartType.REGION_MAP, [COUNT], [TERMS2]),
    _spec(ChartType.MOSAIC, [COUNT], [TERMS]),
    _spec(ChartType.TREEMAP, [COUNT], [TERMS, TERMS2]),
    _spec(ChartType.WAFFLE, [COUNT], [TERMS]),
], ids=lambda s: s.chart_type.value)
def test_every_built_chart_type_reads_back_as_its_spec(spec):
    assert describe_lens_config(to_lens_config(spec)) == {
        "chart_type": spec.chart_type.value,
        "data_view": FLIGHTS,
        "time_field": "timestamp",
        "metrics": [_metric(m) for m in spec.metrics],
        "group_by": [_bucket(g) for g in spec.group_by],
        "query": "",
    }


def test_filters_read_back_as_their_kql():
    spec = _spec(ChartType.METRIC, [COUNT], [], filters=[
        FilterSpec(field="Cancelled", eq=False), FilterSpec(field="Carrier", eq="JetBeats"),
    ])
    assert describe_lens_config(to_lens_config(spec))["query"] == (
        'Cancelled: false AND Carrier: "JetBeats"'
    )


@pytest.mark.parametrize("layer_type,chart", [
    ("bar_stacked", "bar"), ("bar_horizontal", "bar"), ("line", "line"),
    ("area_percentage", "area"), ("reference_lines", "xy"),
])
def test_xy_layer_types_map_to_the_vizspec_chart(layer_type, chart):
    assert lens_chart_type({"type": "xy", "layers": [{"type": layer_type}]}) == chart


def test_kibana_type_names_without_a_vizspec_twin_pass_through():
    assert lens_chart_type({"type": "data_table"}) == "table"
    assert lens_chart_type({"type": "legacy_metric"}) == "legacy_metric"
    assert lens_chart_type({}) == "unknown"
    assert lens_chart_type({"type": "xy", "layers": []}) == "xy"


@pytest.mark.parametrize("config", [
    {},
    {"type": "xy", "layers": None},
    {"type": "xy", "layers": [None, {"y": "not-a-list"}]},
    {"type": "pie", "metrics": "x", "group_by": [1, None], "query": "kql text"},
    {"type": "metric", "data_source": {"type": "data_view_reference", "ref_id": "dv-1"},
     "metrics": [{"type": "primary", "operation": "count"}]},
])
def test_unknown_shapes_never_raise(config):
    described = describe_lens_config(config)
    assert set(described) == {"chart_type", "data_view", "time_field", "metrics", "group_by", "query"}


def test_a_data_view_reference_reports_the_reference():
    described = describe_lens_config({
        "type": "metric", "data_source": {"type": "data_view_reference", "ref_id": "dv-1"},
        "metrics": [{"type": "primary", "operation": "count"}],
    })
    assert described["data_view"] == "dv-1"
    assert described["time_field"] is None
    assert described["metrics"] == [{"agg": "count"}]
