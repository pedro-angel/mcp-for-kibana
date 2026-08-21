"""ES|QL metric panel (#40): the esql_metric_config helper builds the one
live-verified ES|QL Lens shape. ES|QL is kept OUT of VizSpec (exposed via a
separate add_esql_metric_panel tool) so the data-view create_dashboard schema —
and the small-model e2e — is unaffected."""

from kibana_mcp.core.visualizations.translate import (
    esql_metric_config,
    esql_table_config,
    esql_xy_config,
)


def test_esql_metric_config_shape():
    cfg = esql_metric_config("Total flights", "FROM idx | STATS total = COUNT(*)", "total")
    assert cfg == {
        "type": "metric",
        "title": "Total flights",
        "data_source": {"type": "esql", "query": "FROM idx | STATS total = COUNT(*)"},
        "metrics": [{"type": "primary", "column": "total"}],
    }


def test_esql_metric_config_has_no_kql_query_field():
    # The esql query IS the data source; there must be no kql `query` field.
    assert "query" not in esql_metric_config("t", "FROM idx", "c")


# --- #52: esql table + xy chart types ---


def test_esql_table_config_splits_rows_and_metrics():
    cfg = esql_table_config("t", "FROM x | STATS c=COUNT(*) BY host", ["host"], ["c"])
    assert cfg["type"] == "data_table"
    assert cfg["data_source"] == {"type": "esql", "query": "FROM x | STATS c=COUNT(*) BY host"}
    assert cfg["rows"] == [{"column": "host"}]
    assert cfg["metrics"] == [{"column": "c"}]
    assert "query" not in cfg  # no kql query field


def test_esql_table_config_metrics_omitted_when_none():
    cfg = esql_table_config("t", "FROM x", ["a", "b"])
    assert cfg["rows"] == [{"column": "a"}, {"column": "b"}]
    assert "metrics" not in cfg


def test_esql_xy_config_shape():
    cfg = esql_xy_config("t", "FROM x | STATS c=COUNT(*) BY host", "bar", "host", ["c"])
    assert cfg["type"] == "xy"
    (layer,) = cfg["layers"]
    assert layer["type"] == "bar"
    assert layer["data_source"] == {"type": "esql", "query": "FROM x | STATS c=COUNT(*) BY host"}
    assert layer["x"] == {"column": "host"}
    assert layer["y"] == [{"column": "c"}]
    assert "breakdown_by" not in layer


def test_esql_xy_config_multi_y_and_breakdown():
    cfg = esql_xy_config("t", "Q", "line", "host", ["c", "avg"], breakdown_column="region")
    (layer,) = cfg["layers"]
    assert layer["type"] == "line"
    assert layer["y"] == [{"column": "c"}, {"column": "avg"}]
    assert layer["breakdown_by"] == {"column": "region"}
