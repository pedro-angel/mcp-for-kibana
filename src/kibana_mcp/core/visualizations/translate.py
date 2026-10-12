"""Translate VizSpec into Kibana 9.4 Lens API configs (simplified snake_case
schema). This module owns ALL knowledge of Kibana payload shapes; nothing
else in the codebase may build Lens JSON.

Shapes grounded in elastic/dashboards-api-spec (2026-07-09); contract tests
against live Kibana are the final authority."""

from typing import Any

from kibana_mcp.core.visualizations.spec import (
    ChartType,
    FilterSpec,
    GroupBySpec,
    MetricAgg,
    MetricSpec,
    VizSpec,
)


def kql_expression(filters: list[FilterSpec]) -> str:
    parts: list[str] = []
    for f in filters:
        if isinstance(f.eq, bool):
            value = "true" if f.eq else "false"
        elif isinstance(f.eq, (int, float)):
            value = str(f.eq)
        else:
            escaped = str(f.eq).replace("\\", "\\\\").replace('"', '\\"')
            value = f'"{escaped}"'
        parts.append(f"{f.field}: {value}")
    return " AND ".join(parts)


def _metric_dim(m: MetricSpec) -> dict[str, Any]:
    dim: dict[str, Any] = {"operation": m.agg.value}
    if m.agg is not MetricAgg.COUNT:
        dim["field"] = m.field
    if m.label:
        dim["label"] = m.label
    return dim


def _bucket_dim(g: GroupBySpec) -> dict[str, Any]:
    if g.kind == "date_histogram":
        return {"operation": "date_histogram", "field": g.field}
    return {"operation": "terms", "fields": [g.field], "limit": g.limit}


def _data_source(spec: VizSpec) -> dict[str, Any]:
    ds: dict[str, Any] = {"type": "data_view_spec", "index_pattern": spec.data_view}
    if spec.time_field:
        ds["time_field"] = spec.time_field
    return ds


def _common(spec: VizSpec) -> dict[str, Any]:
    # NOTE: live Kibana 9.4.3 rejects a top-level "time_range" on the
    # Visualizations API payload for every chart type ("Additional properties
    # are not allowed ('time_range' was unexpected)") — a Lens library item
    # has no time range of its own; only dashboards do (the create_dashboard
    # tool's time_range parameter, see core/dashboards/compose.py).
    return {
        "title": spec.title,
        "query": {"expression": kql_expression(spec.filters), "language": "kql"},
    }


def to_lens_config(spec: VizSpec) -> dict[str, Any]:
    match spec.chart_type:
        case ChartType.LINE | ChartType.AREA | ChartType.BAR:
            return _xy(spec)
        case ChartType.PIE:
            return {
                "type": "pie",
                **_common(spec),
                "data_source": _data_source(spec),
                "metrics": [_metric_dim(spec.metrics[0])],
                "group_by": [_bucket_dim(g) for g in spec.group_by],
            }
        case ChartType.METRIC:
            primary, *rest = spec.metrics
            return {
                "type": "metric",
                **_common(spec),
                "data_source": _data_source(spec),
                "metrics": [{"type": "primary", **_metric_dim(primary)}]
                + [{"type": "secondary", **_metric_dim(m)} for m in rest],
            }
        case ChartType.TABLE:
            return {
                "type": "data_table",
                **_common(spec),
                "data_source": _data_source(spec),
                "metrics": [_metric_dim(m) for m in spec.metrics],
                "rows": [_bucket_dim(g) for g in spec.group_by],
            }
        # The dimension KEY names below are non-uniform across types and are each
        # pinned by probing a live Kibana 9.4.3, not read off the spec: gauge/
        # tag_cloud/region_map/mosaic use a SINGULAR `metric`; treemap/waffle use a
        # PLURAL `metrics[]`; the bucket key differs (tag_by / region / group_by / x+y).
        case ChartType.GAUGE:
            return {
                "type": "gauge",
                **_common(spec),
                "data_source": _data_source(spec),
                "metric": _metric_dim(spec.metrics[0]),
            }
        case ChartType.HEATMAP:
            return {
                "type": "heatmap",
                **_common(spec),
                "data_source": _data_source(spec),
                "x": _bucket_dim(spec.group_by[0]),
                "y": _bucket_dim(spec.group_by[1]),
                "metric": _metric_dim(spec.metrics[0]),
            }
        case ChartType.TAG_CLOUD:
            return {
                "type": "tag_cloud",
                **_common(spec),
                "data_source": _data_source(spec),
                "metric": _metric_dim(spec.metrics[0]),
                "tag_by": _bucket_dim(spec.group_by[0]),
            }
        case ChartType.REGION_MAP:
            return {
                "type": "region_map",
                **_common(spec),
                "data_source": _data_source(spec),
                "metric": _metric_dim(spec.metrics[0]),
                "region": _bucket_dim(spec.group_by[0]),
            }
        case ChartType.MOSAIC:
            return {
                "type": "mosaic",
                **_common(spec),
                "data_source": _data_source(spec),
                "metric": _metric_dim(spec.metrics[0]),
                "group_by": [_bucket_dim(g) for g in spec.group_by],
            }
        case ChartType.TREEMAP | ChartType.WAFFLE:
            return {
                "type": spec.chart_type.value,
                **_common(spec),
                "data_source": _data_source(spec),
                "metrics": [_metric_dim(spec.metrics[0])],
                "group_by": [_bucket_dim(g) for g in spec.group_by],
            }
        case _:
            raise ValueError(f"unsupported chart type: {spec.chart_type}")


def esql_metric_config(title: str, query: str, column: str) -> dict[str, Any]:
    """Build a Kibana Lens 'metric' panel config backed by an ES|QL query (#40).

    The query is the data source and the metric shows one of its output columns;
    there is NO kql `query` field. This is the only live-verified ES|QL panel
    shape, added to a dashboard inline (never a library visualization — the
    library API rejects an esql data source). Kept OUT of VizSpec on purpose: an
    esql discriminator there made data_view/agg optional and measurably degraded
    a small model's success rate on create_dashboard."""
    return {
        "type": "metric",
        "title": title,
        "data_source": {"type": "esql", "query": query},
        "metrics": [{"type": "primary", "column": column}],
    }


def esql_table_config(
    title: str, query: str, columns: list[str], metric_columns: list[str] | None = None
) -> dict[str, Any]:
    """Build a Kibana Lens 'data_table' panel of an ES|QL query (#52). `columns`
    are the grouping/dimension columns (table rows — one row per distinct value);
    `metric_columns` are the numeric value columns shown per row. This split
    matches Elastic's canonical esql-table example; a metrics-only table would
    collapse the query's rows. Shape confirmed live (the dashboard PUT rejects a
    table with neither, and normalizes a valid one)."""
    cfg: dict[str, Any] = {
        "type": "data_table",
        "title": title,
        "data_source": {"type": "esql", "query": query},
        "rows": [{"column": c} for c in columns],
    }
    if metric_columns:
        cfg["metrics"] = [{"column": c} for c in metric_columns]
    return cfg


def esql_xy_config(
    title: str,
    query: str,
    chart_type: str,
    x_column: str,
    y_columns: list[str],
    breakdown_column: str | None = None,
) -> dict[str, Any]:
    """Build a Kibana Lens 'xy' (bar/line/area) panel of an ES|QL query (#52):
    `x_column` on the x axis, each of `y_columns` a series, optional
    `breakdown_column` split. Shape confirmed live (accepted + axis-normalized)."""
    layer: dict[str, Any] = {
        "type": chart_type,
        "data_source": {"type": "esql", "query": query},
        "x": {"column": x_column},
        "y": [{"column": c} for c in y_columns],
    }
    if breakdown_column:
        layer["breakdown_by"] = {"column": breakdown_column}
    return {"type": "xy", "title": title, "layers": [layer]}


def _xy(spec: VizSpec) -> dict[str, Any]:
    x, *breakdown = spec.group_by
    layer_type = spec.chart_type.value
    if spec.chart_type is ChartType.BAR and breakdown:
        layer_type = "bar_stacked"
    layer: dict[str, Any] = {
        "type": layer_type,
        "data_source": _data_source(spec),
        "x": _bucket_dim(x),
        "y": [_metric_dim(m) for m in spec.metrics],
    }
    if breakdown:
        layer["breakdown_by"] = _bucket_dim(breakdown[0])
    return {"type": "xy", **_common(spec), "layers": [layer]}


# --- Reading a stored Lens config back into VizSpec terms ---------------------
# The inverse direction, for get_visualization / search_visualizations. Kibana
# returns the config it stored, normalized (probed on 9.5.4: a bar chart with a
# breakdown reads back as layer type "bar_stacked", terms buckets gain rank_by,
# empty filters drop `query`). Library items may also come from the Kibana UI,
# so every read is defensive: an unknown shape yields fewer entries, never an
# error.

_XY_LAYER_CHART = (("bar", "bar"), ("line", "line"), ("area", "area"))
_TYPE_TO_CHART = {"data_table": "table"}


def lens_chart_type(config: dict[str, Any]) -> str:
    """The VizSpec chart_type a stored config shows (Kibana's own type name
    when it has no VizSpec equivalent, e.g. 'legacy_metric')."""
    kind = str(config.get("type", "unknown"))
    if kind == "xy":
        layers = config.get("layers") or [{}]
        layer_type = str((layers[0] or {}).get("type", ""))
        return next((c for prefix, c in _XY_LAYER_CHART if layer_type.startswith(prefix)), "xy")
    return _TYPE_TO_CHART.get(kind, kind)


def _read_metric(dim: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"agg": dim.get("operation", dim.get("column", "unknown"))}
    for key in ("field", "label"):
        if dim.get(key):
            out[key] = dim[key]
    return out


def _read_bucket(dim: dict[str, Any]) -> dict[str, Any]:
    fields = dim.get("fields") or ([dim["field"]] if dim.get("field") else [])
    out: dict[str, Any] = {
        "field": fields[0] if fields else dim.get("column", ""),
        "kind": dim.get("operation", "unknown"),
    }
    if dim.get("operation") == "terms" and "limit" in dim:
        out["limit"] = dim["limit"]
    return out


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def describe_lens_config(config: dict[str, Any]) -> dict[str, Any]:
    """chart_type, data_view (index pattern), time_field, metrics, group_by and
    query of a stored Lens config, keyed as in VizSpec."""
    layers = _dicts(config.get("layers"))
    sources = [config] + layers
    data_source: dict[str, Any] = next(
        (s["data_source"] for s in sources if isinstance(s.get("data_source"), dict)), {}
    )
    metrics: list[dict[str, Any]] = []
    group_by: list[dict[str, Any]] = []
    for layer in layers:
        metrics += [_read_metric(m) for m in _dicts(layer.get("y"))]
        buckets = _dicts(layer.get("x")) + _dicts(layer.get("breakdown_by"))
        group_by += [_read_bucket(b) for b in buckets]
    if not layers:
        # metric/pie/table/treemap/waffle hold `metrics`; gauge, heatmap,
        # tag_cloud, region_map and mosaic a singular `metric`. Buckets sit
        # under x+y (heatmap), group_by, rows (table), tag_by or region.
        dims = _dicts(config.get("metrics")) + _dicts(config.get("metric"))
        metrics += [_read_metric(m) for m in dims]
        for key in ("x", "y", "group_by", "rows", "tag_by", "region"):
            group_by += [_read_bucket(b) for b in _dicts(config.get(key))]
    query = config.get("query")
    return {
        "chart_type": lens_chart_type(config),
        "data_view": str(
            data_source.get("index_pattern") or data_source.get("ref_id")
            or data_source.get("id") or ""
        ),
        "time_field": data_source.get("time_field") or None,
        "metrics": metrics,
        "group_by": group_by,
        "query": str(query.get("expression", "")) if isinstance(query, dict) else "",
    }
