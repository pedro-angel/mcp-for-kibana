import pytest
from pydantic import ValidationError

from kibana_mcp.core.visualizations.spec import (
    ChartType, FilterSpec, GroupBySpec, MetricAgg, MetricSpec, TimeRange, VizSpec,
)


def make_spec(**overrides):
    base = dict(
        title="Avg ticket price by carrier",
        chart_type=ChartType.BAR,
        data_view="kibana_sample_data_flights",
        time_field="timestamp",
        metrics=[MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice")],
        group_by=[GroupBySpec(field="Carrier")],
    )
    base.update(overrides)
    return VizSpec(**base)


def test_valid_bar_spec_roundtrip():
    spec = make_spec()
    assert spec.chart_type is ChartType.BAR


def test_time_range_accepts_from_alias():
    tr = TimeRange.model_validate({"from": "now-1d", "to": "now"})
    assert tr.from_ == "now-1d"


def test_non_count_metric_requires_field():
    with pytest.raises(ValidationError, match="field"):
        MetricSpec(agg=MetricAgg.SUM)
    MetricSpec(agg=MetricAgg.COUNT)  # fine without field


def test_metric_chart_rejects_group_by():
    with pytest.raises(ValidationError, match="metric"):
        make_spec(chart_type=ChartType.METRIC)


def test_pie_needs_exactly_one_metric_and_terms_groups():
    with pytest.raises(ValidationError, match="pie"):
        make_spec(chart_type=ChartType.PIE, group_by=[])
    with pytest.raises(ValidationError, match="pie"):
        make_spec(
            chart_type=ChartType.PIE,
            metrics=[MetricSpec(agg=MetricAgg.COUNT), MetricSpec(agg=MetricAgg.COUNT)],
        )


def test_xy_needs_group_by():
    with pytest.raises(ValidationError, match="group_by"):
        make_spec(group_by=[])


def test_date_histogram_requires_time_field():
    with pytest.raises(ValidationError, match="time_field"):
        make_spec(
            time_field=None,
            group_by=[GroupBySpec(field="timestamp", kind="date_histogram")],
        )


def test_filter_spec_types():
    f = FilterSpec(field="Cancelled", eq=False)
    assert f.eq is False


# --- new chart types (#11) validation ---

_C = [MetricSpec(agg=MetricAgg.COUNT)]
_G1 = [GroupBySpec(field="Carrier")]
_G2 = [GroupBySpec(field="Carrier"), GroupBySpec(field="DestCountry")]


def _spec(chart_type, groups, metrics=None):
    return VizSpec(title="T", chart_type=chart_type, data_view="kibana_sample_data_flights",
                   metrics=metrics or _C, group_by=groups)


def test_gauge_rejects_group_by():
    _spec(ChartType.GAUGE, [])  # ok
    with pytest.raises(ValidationError, match="gauge"):
        _spec(ChartType.GAUGE, _G1)


def test_heatmap_needs_exactly_two_group_by():
    _spec(ChartType.HEATMAP, _G2)  # ok
    with pytest.raises(ValidationError, match="heatmap"):
        _spec(ChartType.HEATMAP, _G1)


@pytest.mark.parametrize("ct", [ChartType.TAG_CLOUD, ChartType.REGION_MAP])
def test_tag_cloud_and_region_map_need_exactly_one_group_by(ct):
    _spec(ct, _G1)  # ok
    with pytest.raises(ValidationError):
        _spec(ct, _G2)


@pytest.mark.parametrize("ct", [ChartType.MOSAIC, ChartType.TREEMAP, ChartType.WAFFLE])
def test_partition_types_need_one_metric_and_1_to_2_terms_groups(ct):
    _spec(ct, _G1)  # ok
    _spec(ct, _G2)  # ok (2 groups)
    with pytest.raises(ValidationError):
        _spec(ct, [])  # needs >=1 group
    with pytest.raises(ValidationError, match="terms"):
        _spec(ct, [GroupBySpec(field="timestamp", kind="date_histogram")])


@pytest.mark.parametrize("ct,groups", [
    (ChartType.GAUGE, []),
    (ChartType.HEATMAP, _G2),
    (ChartType.TAG_CLOUD, _G1),
    (ChartType.REGION_MAP, _G1),
    (ChartType.MOSAIC, _G1),
    (ChartType.TREEMAP, _G1),
    (ChartType.WAFFLE, _G1),
])
def test_new_types_reject_multiple_metrics(ct, groups):
    # each new type reads only metrics[0]; the rule must reject 2 metrics so data
    # can't be silently dropped.
    two = [MetricSpec(agg=MetricAgg.COUNT), MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice")]
    with pytest.raises(ValidationError):
        _spec(ct, groups, metrics=two)
