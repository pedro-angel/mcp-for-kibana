from kibana_mcp.core.visualizations.spec import (
    ChartType, FilterSpec, GroupBySpec, MetricAgg, MetricSpec, VizSpec,
)
from kibana_mcp.core.visualizations.translate import kql_expression, to_lens_config

FLIGHTS = "kibana_sample_data_flights"


def test_kql_expression():
    assert kql_expression([]) == ""
    filters = [FilterSpec(field="Cancelled", eq=False), FilterSpec(field="Carrier", eq="ES-Air")]
    assert kql_expression(filters) == 'Cancelled: false AND Carrier: "ES-Air"'


def test_kql_escapes_backslash_in_value():
    out = kql_expression([FilterSpec(field="path", eq="C:\\Users")])
    assert out == 'path: "C:\\\\Users"'


def test_kql_escapes_trailing_backslash():
    out = kql_expression([FilterSpec(field="status", eq="x\\")])
    assert out == 'status: "x\\\\"'


def test_bar_chart_with_filter_golden():
    spec = VizSpec(
        title="Avg ticket price by carrier",
        chart_type=ChartType.BAR,
        data_view=FLIGHTS,
        time_field="timestamp",
        metrics=[MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice")],
        group_by=[GroupBySpec(field="Carrier")],
        filters=[FilterSpec(field="Cancelled", eq=False)],
    )
    assert to_lens_config(spec) == {
        "type": "xy",
        "title": "Avg ticket price by carrier",
        "query": {"expression": "Cancelled: false", "language": "kql"},
        "layers": [
            {
                "type": "bar",
                "data_source": {
                    "type": "data_view_spec",
                    "index_pattern": FLIGHTS,
                    "time_field": "timestamp",
                },
                "x": {"operation": "terms", "fields": ["Carrier"], "limit": 10},
                "y": [{"operation": "average", "field": "AvgTicketPrice"}],
            }
        ],
    }


def test_line_over_time_with_breakdown_is_dated_and_split():
    spec = VizSpec(
        title="Flights over time by carrier",
        chart_type=ChartType.LINE,
        data_view=FLIGHTS,
        time_field="timestamp",
        metrics=[MetricSpec(agg=MetricAgg.COUNT)],
        group_by=[
            GroupBySpec(field="timestamp", kind="date_histogram"),
            GroupBySpec(field="Carrier", limit=5),
        ],
    )
    layer = to_lens_config(spec)["layers"][0]
    assert layer["type"] == "line"
    assert layer["x"] == {"operation": "date_histogram", "field": "timestamp"}
    assert layer["y"] == [{"operation": "count"}]
    assert layer["breakdown_by"] == {"operation": "terms", "fields": ["Carrier"], "limit": 5}


def test_bar_with_breakdown_becomes_stacked():
    spec = VizSpec(
        title="x", chart_type=ChartType.BAR, data_view=FLIGHTS, time_field="timestamp",
        metrics=[MetricSpec(agg=MetricAgg.COUNT)],
        group_by=[GroupBySpec(field="Carrier"), GroupBySpec(field="DestCountry")],
    )
    assert to_lens_config(spec)["layers"][0]["type"] == "bar_stacked"


def test_pie_golden():
    spec = VizSpec(
        title="Flights by carrier", chart_type=ChartType.PIE, data_view=FLIGHTS,
        metrics=[MetricSpec(agg=MetricAgg.COUNT)],
        group_by=[GroupBySpec(field="Carrier")],
    )
    assert to_lens_config(spec) == {
        "type": "pie",
        "title": "Flights by carrier",
        "query": {"expression": "", "language": "kql"},
        "data_source": {"type": "data_view_spec", "index_pattern": FLIGHTS},
        "metrics": [{"operation": "count"}],
        "group_by": [{"operation": "terms", "fields": ["Carrier"], "limit": 10}],
    }


def test_metric_golden_has_primary_discriminator():
    spec = VizSpec(
        title="Total flights", chart_type=ChartType.METRIC, data_view=FLIGHTS,
        time_field="timestamp", metrics=[MetricSpec(agg=MetricAgg.COUNT)],
    )
    cfg = to_lens_config(spec)
    assert cfg["type"] == "metric"
    assert cfg["metrics"] == [{"type": "primary", "operation": "count"}]


def test_table_golden_uses_rows():
    spec = VizSpec(
        title="Carrier stats", chart_type=ChartType.TABLE, data_view=FLIGHTS,
        time_field="timestamp",
        metrics=[
            MetricSpec(agg=MetricAgg.COUNT, label="Flights"),
            MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice"),
        ],
        group_by=[GroupBySpec(field="Carrier", limit=20)],
    )
    cfg = to_lens_config(spec)
    assert cfg["type"] == "data_table"
    assert cfg["rows"] == [{"operation": "terms", "fields": ["Carrier"], "limit": 20}]
    assert cfg["metrics"][0] == {"operation": "count", "label": "Flights"}


def test_kql_expression_escapes_quotes_and_numbers():
    filters = [
        FilterSpec(field="Carrier", eq='Air "Elite"'),
        FilterSpec(field="FlightDelayMin", eq=15),
    ]
    assert kql_expression(filters) == 'Carrier: "Air \\"Elite\\"" AND FlightDelayMin: 15'


def test_metric_with_secondary():
    spec = VizSpec(
        title="Flights and avg price", chart_type=ChartType.METRIC, data_view=FLIGHTS,
        time_field="timestamp",
        metrics=[
            MetricSpec(agg=MetricAgg.COUNT),
            MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice"),
        ],
    )
    cfg = to_lens_config(spec)
    assert cfg["metrics"] == [
        {"type": "primary", "operation": "count"},
        {"type": "secondary", "operation": "average", "field": "AvgTicketPrice"},
    ]


# --- new chart types (#11); dimension keys pinned to live 9.4.3 probes ---

_COUNT = [MetricSpec(agg=MetricAgg.COUNT)]
_CARRIER = GroupBySpec(field="Carrier")
_DEST = GroupBySpec(field="DestCountry")
_DS = {"type": "data_view_spec", "index_pattern": FLIGHTS}
_Q = {"expression": "", "language": "kql"}
_TERMS_CARRIER = {"operation": "terms", "fields": ["Carrier"], "limit": 10}
_TERMS_DEST = {"operation": "terms", "fields": ["DestCountry"], "limit": 10}


def _spec(chart_type, groups):
    return VizSpec(title="T", chart_type=chart_type, data_view=FLIGHTS, metrics=_COUNT, group_by=groups)


def test_gauge_golden_singular_metric_no_groups():
    assert to_lens_config(_spec(ChartType.GAUGE, [])) == {
        "type": "gauge", "title": "T", "query": _Q, "data_source": _DS,
        "metric": {"operation": "count"},
    }


def test_heatmap_golden_x_y_and_metric():
    assert to_lens_config(_spec(ChartType.HEATMAP, [_CARRIER, _DEST])) == {
        "type": "heatmap", "title": "T", "query": _Q, "data_source": _DS,
        "x": _TERMS_CARRIER, "y": _TERMS_DEST, "metric": {"operation": "count"},
    }


def test_tag_cloud_golden_uses_tag_by():
    assert to_lens_config(_spec(ChartType.TAG_CLOUD, [_CARRIER])) == {
        "type": "tag_cloud", "title": "T", "query": _Q, "data_source": _DS,
        "metric": {"operation": "count"}, "tag_by": _TERMS_CARRIER,
    }


def test_region_map_golden_uses_region():
    assert to_lens_config(_spec(ChartType.REGION_MAP, [_DEST])) == {
        "type": "region_map", "title": "T", "query": _Q, "data_source": _DS,
        "metric": {"operation": "count"}, "region": _TERMS_DEST,
    }


def test_mosaic_golden_singular_metric_plural_group_by():
    assert to_lens_config(_spec(ChartType.MOSAIC, [_CARRIER])) == {
        "type": "mosaic", "title": "T", "query": _Q, "data_source": _DS,
        "metric": {"operation": "count"}, "group_by": [_TERMS_CARRIER],
    }


def test_treemap_and_waffle_golden_use_plural_metrics():
    for ct in (ChartType.TREEMAP, ChartType.WAFFLE):
        assert to_lens_config(_spec(ct, [_CARRIER])) == {
            "type": ct.value, "title": "T", "query": _Q, "data_source": _DS,
            "metrics": [{"operation": "count"}], "group_by": [_TERMS_CARRIER],
        }
