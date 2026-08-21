"""Validate a VizSpec against a real data view BEFORE calling Kibana, so a
wrong guess returns a precise correction instead of an opaque 400 or a
broken chart."""

import difflib

from kibana_mcp.core.models import DataViewDetail
from kibana_mcp.core.visualizations.spec import MetricAgg, VizSpec

_NUMERIC_AGGS = {MetricAgg.SUM, MetricAgg.AVG, MetricAgg.MIN, MetricAgg.MAX, MetricAgg.MEDIAN}


def _unknown_field(field: str, fields: dict[str, str], where: str) -> str:
    msg = f"{where}: field '{field}' does not exist in the data view"
    close = difflib.get_close_matches(field, fields, n=1)
    if close:
        return msg + f" — did you mean '{close[0]}'?"
    preview = ", ".join(sorted(fields)[:8])
    return msg + f" — available fields include: {preview}"


def validate_spec(spec: VizSpec, data_view: DataViewDetail) -> list[str]:
    errors: list[str] = []
    fields = data_view.fields

    for m in spec.metrics:
        if m.field is None:
            continue
        if m.field not in fields:
            errors.append(_unknown_field(m.field, fields, f"metric '{m.agg}'"))
        elif m.agg in _NUMERIC_AGGS and fields[m.field] != "number":
            errors.append(
                f"metric '{m.agg}' needs a number field but '{m.field}' is {fields[m.field]}"
            )

    for g in spec.group_by:
        if g.field not in fields:
            errors.append(_unknown_field(g.field, fields, "group_by"))
        elif g.kind == "date_histogram" and fields[g.field] != "date":
            errors.append(
                f"group_by date_histogram needs a date field but '{g.field}' is {fields[g.field]}"
            )

    for f in spec.filters:
        if f.field not in fields:
            errors.append(_unknown_field(f.field, fields, "filter"))

    if spec.time_field and data_view.time_field and spec.time_field != data_view.time_field:
        errors.append(
            f"time_field '{spec.time_field}' does not match the data view's "
            f"time field '{data_view.time_field}'"
        )
    return errors
