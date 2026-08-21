"""VizSpec: the flat, LLM-friendly visualization description.

This model IS the MCP tool input schema for creating charts. Keep it small:
every field here must be guessable by a 14B local model from a one-sentence
user request plus a data-view field listing."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChartType(StrEnum):
    LINE = "line"
    AREA = "area"
    BAR = "bar"
    PIE = "pie"
    METRIC = "metric"
    TABLE = "table"
    GAUGE = "gauge"
    HEATMAP = "heatmap"
    TAG_CLOUD = "tag_cloud"
    REGION_MAP = "region_map"
    MOSAIC = "mosaic"
    TREEMAP = "treemap"
    WAFFLE = "waffle"


# Partition/categorical charts: 1 metric + terms bucket(s), no date_histogram.
_PARTITION_TYPES = {ChartType.MOSAIC, ChartType.TREEMAP, ChartType.WAFFLE}


class MetricAgg(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "average"
    MIN = "min"
    MAX = "max"
    MEDIAN = "median"
    UNIQUE_COUNT = "unique_count"


class MetricSpec(BaseModel):
    agg: MetricAgg = Field(description="Aggregation to compute.")
    field: str | None = Field(None, description="Field to aggregate. Omit only for count.")
    label: str | None = Field(None, description="Optional display label.")

    @model_validator(mode="after")
    def _field_required_unless_count(self) -> Self:
        if self.agg is not MetricAgg.COUNT and not self.field:
            raise ValueError(f"metric with agg '{self.agg}' requires a field")
        return self


class GroupBySpec(BaseModel):
    field: str = Field(description="Field to group by.")
    kind: Literal["terms", "date_histogram"] = Field(
        "terms", description="'terms' = top values; 'date_histogram' = over time."
    )
    limit: int = Field(10, ge=1, le=100, description="Top N buckets (terms only).")


class TimeRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field("now-7d", alias="from", description="Date math or ISO start.")
    to: str = Field("now", description="Date math or ISO end.")


class FilterSpec(BaseModel):
    field: str = Field(description="Field to filter on.")
    eq: str | int | float | bool = Field(description="Keep documents where field equals this.")


_XY_TYPES = {ChartType.LINE, ChartType.AREA, ChartType.BAR}


class VizSpec(BaseModel):
    title: str = Field(min_length=1, description="Human-readable chart title.")
    chart_type: ChartType = Field(
        description=(
            "Chart to draw: 'line'/'area'/'bar' compare a metric across groups or time, "
            "'pie'/'mosaic'/'treemap'/'waffle' show proportions of a whole, 'metric'/'gauge' "
            "are a single number, 'table' lists rows, 'heatmap' is a value grid over two "
            "dimensions, 'tag_cloud' sizes terms by a metric, 'region_map' shades a map by region."
        )
    )
    data_view: str = Field(description="Data view name, index pattern, or id.")
    time_field: str | None = Field(
        None, description="Time field of the data view. Auto-filled by the server if omitted."
    )
    metrics: list[MetricSpec] = Field(min_length=1, description="What to measure (1+).")
    group_by: list[GroupBySpec] = Field(
        default_factory=list, description="How to split the data (x axis / slices / rows)."
    )
    filters: list[FilterSpec] = Field(
        default_factory=list, description="Optional equality filters ANDed together."
    )

    @model_validator(mode="after")
    def _chart_rules(self) -> Self:
        ct, n_groups = self.chart_type, len(self.group_by)
        if ct is ChartType.METRIC and n_groups:
            raise ValueError("chart_type 'metric' takes no group_by (it is a single number)")
        if ct is ChartType.PIE:
            if len(self.metrics) != 1 or not 1 <= n_groups <= 2:
                raise ValueError("chart_type 'pie' needs exactly 1 metric and 1-2 group_by")
            if any(g.kind != "terms" for g in self.group_by):
                raise ValueError("chart_type 'pie' group_by must be kind 'terms'")
        if ct in _XY_TYPES and not 1 <= n_groups <= 2:
            raise ValueError(
                f"chart_type '{ct}' needs 1-2 group_by entries (x axis, optional series split)"
            )
        if ct is ChartType.TABLE and n_groups > 3:
            raise ValueError("chart_type 'table' supports at most 3 group_by")
        if ct is ChartType.GAUGE and (len(self.metrics) != 1 or n_groups):
            raise ValueError("chart_type 'gauge' needs exactly 1 metric and no group_by")
        if ct is ChartType.HEATMAP and (len(self.metrics) != 1 or n_groups != 2):
            raise ValueError("chart_type 'heatmap' needs exactly 1 metric and exactly 2 group_by (x, y)")
        if ct in (ChartType.TAG_CLOUD, ChartType.REGION_MAP) and (
            len(self.metrics) != 1 or n_groups != 1
        ):
            raise ValueError(f"chart_type '{ct}' needs exactly 1 metric and exactly 1 group_by")
        if ct in _PARTITION_TYPES and (len(self.metrics) != 1 or not 1 <= n_groups <= 2):
            raise ValueError(f"chart_type '{ct}' needs exactly 1 metric and 1-2 group_by")
        # Partition + tag_cloud + region_map are categorical: terms buckets only.
        if ct in (_PARTITION_TYPES | {ChartType.TAG_CLOUD, ChartType.REGION_MAP}) and any(
            g.kind != "terms" for g in self.group_by
        ):
            raise ValueError(f"chart_type '{ct}' group_by must be kind 'terms'")
        if any(g.kind == "date_histogram" for g in self.group_by) and not self.time_field:
            raise ValueError("date_histogram group_by requires time_field")
        return self
