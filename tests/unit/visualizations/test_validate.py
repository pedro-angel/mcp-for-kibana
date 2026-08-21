from kibana_mcp.core.models import DataViewDetail
from kibana_mcp.core.visualizations.spec import (
    ChartType, FilterSpec, GroupBySpec, MetricAgg, MetricSpec, VizSpec,
)
from kibana_mcp.core.visualizations.validate import validate_spec

DV = DataViewDetail(
    id="dv1",
    name="flights",
    index_pattern="kibana_sample_data_flights",
    time_field="timestamp",
    fields={
        "Carrier": "string",
        "AvgTicketPrice": "number",
        "Cancelled": "boolean",
        "timestamp": "date",
    },
)


def spec_with(**overrides):
    base = dict(
        title="t", chart_type=ChartType.BAR, data_view=DV.index_pattern,
        time_field="timestamp",
        metrics=[MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice")],
        group_by=[GroupBySpec(field="Carrier")],
    )
    base.update(overrides)
    return VizSpec(**base)


def test_valid_spec_returns_no_errors():
    assert validate_spec(spec_with(), DV) == []


def test_unknown_field_suggests_close_match():
    errors = validate_spec(
        spec_with(group_by=[GroupBySpec(field="carrier")]), DV
    )
    assert len(errors) == 1
    assert "carrier" in errors[0] and "Carrier" in errors[0]


def test_numeric_agg_on_non_numeric_field():
    errors = validate_spec(
        spec_with(metrics=[MetricSpec(agg=MetricAgg.SUM, field="Carrier")]), DV
    )
    assert any("number" in e for e in errors)


def test_date_histogram_on_non_date_field():
    errors = validate_spec(
        spec_with(group_by=[GroupBySpec(field="Carrier", kind="date_histogram")]), DV
    )
    assert any("date" in e for e in errors)


def test_filter_field_checked():
    errors = validate_spec(
        spec_with(filters=[FilterSpec(field="Canceled", eq=True)]), DV
    )
    assert any("Cancelled" in e for e in errors)


def test_unknown_field_without_close_match_lists_available():
    errors = validate_spec(
        spec_with(group_by=[GroupBySpec(field="zzz_nonsense")]), DV
    )
    assert len(errors) == 1
    assert "available fields include" in errors[0]
    assert "Carrier" in errors[0]
