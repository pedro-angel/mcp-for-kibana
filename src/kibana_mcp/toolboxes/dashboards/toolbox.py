"""The dashboards toolbox: MVP tools for creating and managing dashboards."""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.core.dashboards.compose import (
    append_panel,
    build_dashboard_data,
    layout_panels,
    remove_panel,
    replace_panel_config,
)
from kibana_mcp.core.dashboards.identity import derive_dashboard_id, normalize
from kibana_mcp.core.errors import KibanaNotFound, KibanaSpaceNotFound
from kibana_mcp.core.visualizations.spec import TimeRange, VizSpec
from kibana_mcp.core.visualizations.translate import (
    esql_metric_config,
    esql_table_config,
    esql_xy_config,
    to_lens_config,
)
from kibana_mcp.core.visualizations.validate import validate_spec
from kibana_mcp.ports.gateway import KibanaGateway
from kibana_mcp.toolboxes.base import SPACE_ID_PATTERN, ToolboxDeps, with_space, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_UPSERT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)


def _prepare_config(gw: KibanaGateway, spec: VizSpec) -> dict[str, Any]:
    data_view = gw.get_data_view(spec.data_view)
    updates: dict[str, Any] = {"data_view": data_view.index_pattern}
    if spec.time_field is None and data_view.time_field:
        updates["time_field"] = data_view.time_field
    spec = spec.model_copy(update=updates)
    errors = validate_spec(spec, data_view)
    if errors:
        raise ToolError(
            "invalid visualization spec:\n- " + "\n- ".join(errors)
            + "\nUse describe_data_view to check field names and types."
        )
    return to_lens_config(spec)


def _dashboard_summary(gw: KibanaGateway, dashboard_id: str) -> dict[str, Any]:
    # visualization_id appears only on by-reference panels, so a dashboard of
    # inline panels keeps its pre-library result shape byte for byte.
    detail = asdict(gw.get_dashboard(dashboard_id))
    detail["panels"] = [
        {k: v for k, v in p.items() if k != "visualization_id" or v is not None}
        for p in detail["panels"]
    ]
    return detail


def _checked_dashboard_data(gw: KibanaGateway, dashboard_id: str) -> dict[str, Any]:
    data, warnings = gw.get_dashboard_data(dashboard_id)
    if warnings:
        raise ToolError(
            f"dashboard '{dashboard_id}' contains unsupported panels or fields that the "
            f"Kibana API cannot round-trip (they would be silently deleted): {warnings}. "
            "Modify this dashboard in the Kibana UI instead."
        )
    return data


class DashboardsToolbox:
    name = "dashboards"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        # NB: list_data_views / describe_data_view moved to the data-management
        # toolbox (issue #28) so the data_views namespace is owned once. The
        # default toolbox set pairs the two, so create_dashboard's guidance to
        # "call describe_data_view first" still resolves.
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def search_dashboards(
            query: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """Search dashboards by title/description. Empty query lists all.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(d) for d in gw.search_dashboards(query)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_dashboard(
            dashboard_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get a dashboard summary: title, description, and its panels.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(_dashboard_summary(gw, dashboard_id), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def search_visualizations(
            query: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """Search library visualizations (from create_visualization) by title.
            Empty query lists all.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(v) for v in gw.search_visualizations(query)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_visualization(
            visualization_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get a library visualization: title, chart type, data view, metrics,
            group_by and filter query.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_visualization(visualization_id)), space)

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        def dashboard_url(dashboard_id: str, space: str | None) -> str:
            prefix = f"/s/{space}" if space else ""
            return f"{deps.public_kibana_url}{prefix}/app/dashboards#/view/{dashboard_id}"

        @mcp.tool(tags={self.name, "write"}, annotations=_UPSERT)
        def create_dashboard(
            title: Annotated[str, Field(min_length=1)],
            panels: Annotated[list[VizSpec], Field(min_length=1)],
            description: str = "",
            time_range: TimeRange | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create a dashboard from one or more visualization specs. Idempotent:
            creating with the same title (case-insensitive,
            whitespace/punctuation-normalized) updates that dashboard instead of
            duplicating it, replacing its panels (filters, query, tags and display
            options are preserved); the return's `status` is
            `created` or `replaced`. Call describe_data_view first to learn real
            field names.

            `space` targets a Kibana space by id (default: the default space)."""
            if not normalize(title):
                raise ToolError("dashboard title cannot be blank or whitespace-only")
            dashboard_id = derive_dashboard_id(title)
            with gateway_errors(), deps.gateway_factory(space) as gw:
                try:
                    existing, warnings = gw.get_dashboard_data(dashboard_id)
                    existed = True
                except KibanaSpaceNotFound:
                    # a mid-call space-validation failure is NOT "the dashboard
                    # does not exist" — proceeding would reopen the fail-closed
                    # guarantee (spec: Existence check timing)
                    raise
                except KibanaNotFound:
                    existing, warnings = {}, []
                    existed = False
                if existed and warnings:
                    raise ToolError(
                        f"a dashboard titled '{title}' already exists and holds content "
                        f"that can't be safely replaced ({warnings}). Edit it in the "
                        "Kibana UI, or use a different title."
                    )
                configs = [_prepare_config(gw, spec) for spec in panels]
                tr = time_range or TimeRange()
                data = build_dashboard_data(
                    title, description, layout_panels(configs),
                    {"from": tr.from_, "to": tr.to},
                )
                for key in ("options", "filters", "query", "refresh_interval", "tags", "pinned_panels"):
                    if key in existing and key not in data:
                        data[key] = existing[key]
                gw.upsert_dashboard(dashboard_id, data)
            return with_space({
                "id": dashboard_id, "url": dashboard_url(dashboard_id, space),
                "title": title, "panel_count": len(configs),
                "status": "replaced" if existed else "created",
            }, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_visualization(
            spec: VizSpec,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create a reusable visualization in the library (not on a dashboard).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                new_id = gw.create_visualization(_prepare_config(gw, spec))
            return with_space({"id": new_id, "title": spec.title}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_UPSERT)
        def update_visualization(
            visualization_id: str,
            spec: VizSpec,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Replace a library visualization with a new spec; its id stays the same.
            Dashboards that show it through add_library_panel change too; panels
            added with add_panel or create_dashboard are separate copies and do not.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.get_visualization(visualization_id)  # a wrong id fails before the pre-flight
                gw.update_visualization(visualization_id, _prepare_config(gw, spec))
            return with_space({"id": visualization_id, "title": spec.title}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def add_library_panel(
            dashboard_id: str,
            visualization_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Show a library visualization on an existing dashboard. The panel is
            linked, not copied: update_visualization changes it on every dashboard.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                data = _checked_dashboard_data(gw, dashboard_id)
                # Kibana stores a ref_id that names nothing (probed on 9.5.4), so
                # the item is checked here, in the same space, before the write.
                gw.get_visualization(visualization_id)
                new = append_panel(data, {"ref_id": visualization_id})
                gw.update_dashboard(dashboard_id, new)
            return with_space({
                "id": dashboard_id, "url": dashboard_url(dashboard_id, space),
                "panel_index": len(new["panels"]) - 1,
            }, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def add_panel(
            dashboard_id: str,
            panel: VizSpec,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Add a visualization panel to an existing dashboard.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                data = _checked_dashboard_data(gw, dashboard_id)
                gw.update_dashboard(dashboard_id, append_panel(data, _prepare_config(gw, panel)))
            return with_space({"id": dashboard_id, "url": dashboard_url(dashboard_id, space)}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def add_esql_metric_panel(
            dashboard_id: str,
            title: Annotated[str, Field(min_length=1)],
            esql: Annotated[str, Field(min_length=1)],
            column: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Add an ES|QL metric panel to an existing dashboard: run an ES|QL
            query and show one of its output columns as a single-number metric.
            `esql` is the query (e.g. 'FROM logs | STATS total = COUNT(*)') and
            `column` names the output column to display (e.g. 'total'). For
            field-based charts use add_panel with a VizSpec instead. The query is
            NOT validated server-side — a wrong query or column yields an empty
            panel, so write both carefully.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                data = _checked_dashboard_data(gw, dashboard_id)
                config = esql_metric_config(title, esql, column)
                gw.update_dashboard(dashboard_id, append_panel(data, config))
            return with_space({"id": dashboard_id, "url": dashboard_url(dashboard_id, space)}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def add_esql_table_panel(
            dashboard_id: str,
            title: Annotated[str, Field(min_length=1)],
            esql: Annotated[str, Field(min_length=1)],
            columns: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)],
            metric_columns: list[Annotated[str, Field(min_length=1)]] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Add an ES|QL table panel to an existing dashboard: show a query's
            output columns as a table. `columns` are the grouping/dimension
            columns — one table row per distinct value (e.g. ["status"]);
            `metric_columns` are the numeric value columns shown per row (e.g.
            ["count"]). `esql` is the query. It is NOT validated server-side — a
            wrong query or column name yields an empty panel, so write them
            carefully. For field-based charts use add_panel with a VizSpec.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                data = _checked_dashboard_data(gw, dashboard_id)
                config = esql_table_config(title, esql, columns, metric_columns)
                gw.update_dashboard(dashboard_id, append_panel(data, config))
            return with_space({"id": dashboard_id, "url": dashboard_url(dashboard_id, space)}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def add_esql_xy_panel(
            dashboard_id: str,
            title: Annotated[str, Field(min_length=1)],
            esql: Annotated[str, Field(min_length=1)],
            x_column: Annotated[str, Field(min_length=1)],
            y_columns: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)],
            chart_type: Literal["bar", "line", "area"] = "bar",
            breakdown_column: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Add an ES|QL bar/line/area panel to an existing dashboard:
            `x_column` on the x axis, each of `y_columns` a plotted series, and an
            optional `breakdown_column` to split each series. `esql` is the query.
            It is NOT validated server-side — a wrong query or column name yields
            an empty panel. For field-based charts use add_panel with a VizSpec.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                data = _checked_dashboard_data(gw, dashboard_id)
                config = esql_xy_config(
                    title, esql, chart_type, x_column, y_columns, breakdown_column
                )
                gw.update_dashboard(dashboard_id, append_panel(data, config))
            return with_space({"id": dashboard_id, "url": dashboard_url(dashboard_id, space)}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_UPSERT)
        def update_panel(
            dashboard_id: str,
            panel_index: int,
            panel: VizSpec,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Replace the visualization at panel_index (see get_dashboard for indexes).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                data = _checked_dashboard_data(gw, dashboard_id)
                panels = data.get("panels", [])
                if not 0 <= panel_index < len(panels):
                    raise ToolError(
                        f"panel_index {panel_index} out of range — "
                        f"dashboard has {len(panels)} panels"
                    )
                existing_type = panels[panel_index].get("type")
                if existing_type != "vis":
                    raise ToolError(
                        f"panel {panel_index} is type '{existing_type}', not a visualization — "
                        "only vis panels can be updated"
                    )
                ref = (panels[panel_index].get("config") or {}).get("ref_id")
                if ref:
                    # Replacing the config would silently unlink the panel from
                    # the library item; the chart is edited where it lives (D37).
                    # Kibana keeps a ref whose item was deleted, and then there
                    # is nothing to edit: name the tools that fix the panel.
                    try:
                        gw.get_visualization(ref)
                    except KibanaSpaceNotFound:
                        raise
                    except KibanaNotFound:
                        raise ToolError(
                            f"panel {panel_index} shows library visualization '{ref}', which "
                            "no longer exists — remove the broken panel with delete_panel "
                            "(destructive tier), then place a chart with add_panel or "
                            "add_library_panel"
                        ) from None
                    raise ToolError(
                        f"panel {panel_index} shows library visualization '{ref}' — "
                        "change it with update_visualization, which updates every "
                        "dashboard that shows it"
                    )
                new = replace_panel_config(data, panel_index, _prepare_config(gw, panel))
                gw.update_dashboard(dashboard_id, new)
            return with_space({"id": dashboard_id, "panel_index": panel_index}, space)

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_visualization(
            visualization_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a library visualization. Kibana does not check
            dashboards first: a panel added with add_library_panel stays behind,
            broken; remove it with delete_panel.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_visualization(visualization_id)
            return with_space({"id": visualization_id, "deleted": True}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_dashboard(
            dashboard_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a dashboard.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_dashboard(dashboard_id)
            return with_space({"id": dashboard_id, "deleted": True}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_panel(
            dashboard_id: str,
            panel_index: int,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently remove one panel from a dashboard.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                data = _checked_dashboard_data(gw, dashboard_id)
                panels = data.get("panels", [])
                if not 0 <= panel_index < len(panels):
                    raise ToolError(
                        f"panel_index {panel_index} out of range — "
                        f"dashboard has {len(panels)} panels"
                    )
                entry = panels[panel_index]
                if "grid" not in entry or isinstance(entry.get("panels"), list):
                    raise ToolError(
                        f"entry {panel_index} is a dashboard section, not a panel — "
                        "deleting it would remove every panel inside it; edit sections in the Kibana UI"
                    )
                gw.update_dashboard(dashboard_id, remove_panel(data, panel_index))
            return with_space({"id": dashboard_id, "deleted_panel": panel_index}, space)
