"""The data-management toolbox: data views + short URLs + saved-objects
export/import.

Owns the `data_views` namespace (the two read tools were extracted from the
dashboards toolbox so the namespace is owned once) plus data-view create/delete,
short-URL create/resolve/delete, and saved-objects export/import (#37) — the
latter handle-based so a whole-space NDJSON export never crosses the model
context.
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.core.errors import KibanaRejected
from kibana_mcp.core.saved_objects import read_export, summarize_export, to_ndjson, write_export
from kibana_mcp.toolboxes.base import SPACE_ID_PATTERN, ToolboxDeps, with_space, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

# Kibana does NOT validate short-URL locator params (kibana-py warns ill-formed
# params can break Kibana), so the tool constrains to verified locators + shape.
_ALLOWED_LOCATORS = {"LEGACY_SHORT_URL_LOCATOR"}


class DataManagementToolbox:
    name = "data-management"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_data_views(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List Kibana data views (the datasets you can visualize).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(v) for v in gw.list_data_views()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def describe_data_view(
            data_view: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get a data view's fields and types. Call this before creating a
            visualization so you use real field names.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_data_view(data_view)), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def resolve_short_url(
            slug: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Resolve a Kibana short-URL slug to its locator and target app path.
            (Short URLs are a Technical-Preview API.)

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.resolve_short_url(slug)), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def export_saved_objects(
            types: list[str] | None = None,
            objects: list[dict[str, Any]] | None = None,
            include_references_deep: bool = True,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Export saved objects (dashboards, data views, …) as an NDJSON file
            on the server, and return a summary + an opaque `handle` — NOT the
            content — that you pass to import_saved_objects. Select EITHER `types`
            (e.g. ["dashboard"], or ["*"] for the whole space) OR `objects`
            (a list of {"type":…, "id":…}), not both. This is a sensitive export
            surface; what can actually be read is bounded by the API key's
            privileges, not by this tool.

            `space` targets a Kibana space by id (default: the default space)."""
            if bool(types) == bool(objects):
                raise ToolError("provide exactly one of `types` or `objects` to export")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                body = gw.export_saved_objects(types, objects, include_references_deep)
            content = to_ndjson(body)
            try:
                handle = write_export(deps.export_dir, content)
            except OSError as e:  # ENOSPC, missing export_dir, etc. -> clean tool error
                raise ToolError(f"could not store the export: {e.strerror or e}") from e
            return with_space(asdict(summarize_export(body, handle, len(content))), space)

    @staticmethod
    def _import_by_handle(
        deps: ToolboxDeps, handle: str, overwrite: bool, space: str | None
    ) -> dict[str, Any]:
        # Shared by the write (clone) + destructive (overwrite) import tools: read
        # the confined export, run the import, and translate a Kibana rejection to
        # a CONTENT-FREE error (its `detail` can echo object bytes — the #37 rule).
        with gateway_errors():
            try:
                content = read_export(deps.export_dir, handle)  # confined to export_dir
            except ValueError as e:  # malformed/traversal handle -> clean tool error
                raise ToolError(str(e)) from e
            with deps.gateway_factory(space) as gw:
                try:
                    return with_space(asdict(gw.import_saved_objects(content, overwrite)), space)
                except KibanaRejected as e:
                    verb = "restore" if overwrite else "import"
                    raise ToolError(
                        f"{verb} failed for handle '{handle}': the export is "
                        "invalid or incompatible with this Kibana version"
                    ) from e

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def import_saved_objects(
            handle: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Import a previously-exported set of saved objects, identified by the
            `handle` returned from export_saved_objects, as NEW copies (regenerated
            ids — a clone into the current space, not an in-place restore). Existing
            objects are never touched. Returns which objects were created (source id
            -> new destination id). A missing/expired handle errors. To restore in
            place instead, use overwrite_saved_objects (destructive). A handle
            carries no space — importing it with `space="b"` clones its content into
            b (new ids), regardless of which space it was exported from.

            `space` targets a Kibana space by id (default: the default space)."""
            return self._import_by_handle(deps, handle, overwrite=False, space=space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_data_view(
            index_pattern: Annotated[str, Field(min_length=1)],
            name: str | None = None,
            time_field: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create a Kibana data view over an index pattern (e.g. 'logs-*').
            Optionally set a display name and a time field (for time-series data).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                view = gw.create_data_view(index_pattern, name, time_field)
                return with_space(asdict(view), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_short_url(
            locator_id: str,
            params: dict[str, Any],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create a Kibana short URL. Supports
            locator_id='LEGACY_SHORT_URL_LOCATOR' with params={'url': '/app/...'}
            (a Kibana app path). (Technical-Preview API.) A slug created in a space
            resolves under `/s/<space>/goto/<slug>` and via
            resolve_short_url(slug, space=…). When `space` is set, pass the app path
            WITHOUT a `/s/<space>` prefix — the `space` parameter chooses the space,
            not the path. A `/s/<id>`-prefixed path passed without `space` creates
            the slug in the default space.

            `space` targets a Kibana space by id (default: the default space)."""
            if locator_id not in _ALLOWED_LOCATORS:
                raise ToolError(
                    f"unsupported locator_id '{locator_id}'; supported: {sorted(_ALLOWED_LOCATORS)}"
                )
            url = params.get("url")
            # Must be a SAME-ORIGIN path: a single leading '/'. Reject '//' and
            # '/\' — Kibana's /goto/<slug> redirect would send those off-site
            # (open redirect), which the allow-list exists to prevent.
            if not isinstance(url, str) or not url.startswith("/") or url[:2] in ("//", "/\\"):
                raise ToolError(
                    "LEGACY_SHORT_URL_LOCATOR requires params.url to be a same-origin Kibana "
                    "app path: a single leading '/' (not '//' or '/\\'), "
                    "e.g. '/app/dashboards#/view/<id>'"
                )
            if space is not None and url.startswith("/s/"):
                raise ToolError(
                    "pass the app path without the `/s/<space>` prefix; the `space` "
                    "parameter chooses the space"
                )
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.create_short_url(locator_id, params)), space)

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_data_view(
            view_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a data view by id. This cannot be recovered.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_data_view(view_id)
            return with_space({"id": view_id, "deleted": True}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_short_url(
            short_url_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a short URL by id.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_short_url(short_url_id)
            return with_space({"id": short_url_id, "deleted": True}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def overwrite_saved_objects(
            handle: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Restore a previously-exported set of saved objects IN PLACE (same
            ids), identified by the `handle` from export_saved_objects. This
            OVERWRITES any existing objects with those ids — unlike
            import_saved_objects, which clones to new ids and never touches
            existing objects. The overwrite cannot be undone. Returns which objects
            were restored. Restoring into the space the handle came from returns
            `destination_id` equal to `source_id` (in place); restoring into a
            DIFFERENT space mints a new `destination_id` on the first restore
            (ids are globally unique across spaces) and replaces that copy on
            repeats. A missing/expired handle errors.

            `space` targets a Kibana space by id (default: the default space)."""
            return self._import_by_handle(deps, handle, overwrite=True, space=space)
