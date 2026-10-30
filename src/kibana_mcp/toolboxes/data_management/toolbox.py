"""The data-management toolbox: data views + short URLs + saved-objects
export/import and reads.

Owns the `data_views` namespace (the two read tools were extracted from the
dashboards toolbox so the namespace is owned once) plus data-view create/update/delete,
runtime fields and the default data view (D48), short-URL create/resolve/delete,
saved-objects export/import (#37) — handle-based so a whole-space NDJSON export
never crosses the model context — and the saved-objects find/get reads (D48).
"""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.core.errors import KibanaRejected
from kibana_mcp.core.models import DefaultDataView, RuntimeField
from kibana_mcp.core.saved_objects import (
    READABLE_TYPES,
    cap_attributes,
    read_export,
    summarize_export,
    to_ndjson,
    unreadable_type_message,
    write_export,
)
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

# The single-value runtime-field types Kibana's runtime-field routes accept
# (probed on 9.5.4: its 400 lists these plus `composite`, which takes subfields
# and is not managed here, D48).
RuntimeFieldType = Literal["keyword", "long", "double", "date", "ip", "boolean", "geo_point"]

# Elasticsearch's default index.max_result_window: Kibana's find 400s past it
# ("Result window is too large", probed), so page * per_page is checked first.
_FIND_WINDOW = 10_000

# Kibana does NOT validate short-URL locator params (kibana-py warns ill-formed
# params can break Kibana), so the tool constrains to verified locators + shape.
_ALLOWED_LOCATORS = {"LEGACY_SHORT_URL_LOCATOR"}


def _runtime_field(field: RuntimeField) -> dict[str, Any]:
    out: dict[str, Any] = {"name": field.name, "type": field.type, "script": field.script}
    if field.fields is not None:
        out["fields"] = dict(field.fields)
    return out


def _default_view(view: DefaultDataView) -> dict[str, Any]:
    return {
        "id": view.id, "name": view.name, "index_pattern": view.index_pattern,
        "exists": view.exists,
    }


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
        def get_default_data_view(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get the space's default data view — the one Discover and other Kibana
            apps open with. Returns its id, name and index_pattern, and `exists`:
            id is null when no default is set, and exists is false when the stored
            id names no data view in the space (Kibana keeps the id of a deleted
            view); set another with set_default_data_view.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(_default_view(gw.get_default_data_view()), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def find_saved_objects(
            types: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)],
            search: Annotated[str, Field(min_length=1)] | None = None,
            has_reference: dict[str, str] | None = None,
            page: Annotated[int, Field(ge=1)] = 1,
            per_page: Annotated[int, Field(ge=1, le=100)] = 20,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Find saved objects of the given `types` — e.g. ["dashboard"],
            ["lens", "visualization"], ["index-pattern"] (data views), ["search"],
            ["tag"], ["map"] — optionally matching `search` (words of the title
            or name, no other attribute; `flight*` matches a prefix) and/or
            `has_reference` ({"type": …,
            "id": …}: only objects that reference that object, e.g. the
            dashboards that use a visualization or a data view). Returns one page
            (`page`, `per_page` up to 100) of summaries — type, id, title, name,
            updated_at, namespaces, managed — and the `total`; read one in full
            with get_saved_object. Kibana answers an empty page for a type name it
            does not know or does not serve here (action, alert, …), so an empty
            result is not proof that nothing exists; `note` says so. Uses Kibana's
            saved-objects find API, which Kibana marks deprecated.

            `space` targets a Kibana space by id (default: the default space)."""
            if any(not t.strip() or t.strip() == "*" for t in types):
                raise ToolError(
                    "`types` needs type names, e.g. ['dashboard'] — not blank and not '*' "
                    "(Kibana's find answers nothing for '*'; export_saved_objects takes "
                    "types=['*'] for a whole space)"
                )
            if search is not None and not search.strip():
                raise ToolError("`search` is blank — pass words to match, or omit it")
            if has_reference is not None and (
                set(has_reference) != {"type", "id"}
                or not all(v.strip() for v in has_reference.values())
            ):
                raise ToolError(
                    "`has_reference` must be exactly {\"type\": …, \"id\": …} of the "
                    "referenced object, e.g. {\"type\": \"lens\", \"id\": \"<id>\"}"
                )
            if page * per_page > _FIND_WINDOW:
                raise ToolError(
                    f"page {page} of {per_page} reaches past the first {_FIND_WINDOW:,} "
                    "results, which Kibana does not serve — narrow the search with `search` "
                    "or `has_reference`"
                )
            with gateway_errors(), deps.gateway_factory(space) as gw:
                found = gw.find_saved_objects(types, search, has_reference, page, per_page)
            result: dict[str, Any] = {
                "total": found.total, "page": found.page, "per_page": found.per_page,
                "saved_objects": [asdict(o) for o in found.saved_objects],
            }
            if found.total == 0:
                result["note"] = (
                    "nothing matched — Kibana also answers an empty page for a type name it "
                    "does not know or does not serve through this API (action, alert, …), "
                    "so check the type names"
                )
            elif not found.saved_objects:
                result["note"] = (
                    f"page {found.page} is past the last page ({found.total} results, "
                    f"{found.per_page} per page)"
                )
            return with_space(result, space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_saved_object(
            type: Annotated[str, Field(min_length=1)],
            id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get one saved object by `type` and `id` (from find_saved_objects):
            its attributes, the distinct objects it references ({"type", "id"}),
            its namespaces, timestamps and `managed` flag. Only analytics
            content is read — canvas-element, canvas-workpad,
            canvas-workpad-template, dashboard, event-annotation-group,
            graph-workspace, index-pattern (data views), lens, links, map, query,
            search, tag, visualization — because other types can keep
            credentials in plain attributes (Fleet outputs and proxies do); any
            other type is refused: read it with its own tools. An attribute
            longer than 4,000 characters (a dashboard's panelsJSON, a data
            view's field list) is left out and listed in `omitted_attributes` with
            its size: read it with the type's own tool (get_dashboard,
            get_visualization, describe_data_view) or move the object with
            export_saved_objects. Uses Kibana's saved-objects get API, which
            Kibana marks deprecated.

            `space` targets a Kibana space by id (default: the default space)."""
            if not type.strip() or not id.strip():
                raise ToolError("`type` and `id` must not be blank — pass the ones "
                                "find_saved_objects returns")
            if type not in READABLE_TYPES:
                raise ToolError(unreadable_type_message(type))
            with gateway_errors(), deps.gateway_factory(space) as gw:
                obj = gw.get_saved_object(type, id)
            shown, omitted = cap_attributes(obj.attributes)
            result: dict[str, Any] = {
                "type": obj.type, "id": obj.id, "namespaces": list(obj.namespaces),
                "created_at": obj.created_at, "updated_at": obj.updated_at,
                "managed": obj.managed, "attributes": shown,
                "references": [dict(r) for r in obj.references],
            }
            if omitted:
                result["omitted_attributes"] = omitted
            return with_space(result, space)

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
            (a list of {"type":…, "id":…}, from find_saved_objects), not both.
            This is a sensitive export surface; what can actually be read is
            bounded by the API key's privileges, not by this tool.

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
            The time field must be a date field of the indices the pattern
            matches: a field they do not have, or one that is not a date, is
            refused with the pattern's date fields listed, and no data view is
            created. A pattern that matches no index yet is accepted, but its
            time field cannot be checked. The name (it defaults to the index
            pattern) must be unused; to change an existing view, call
            update_data_view.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                view = gw.create_data_view(index_pattern, name, time_field)
                return with_space(asdict(view), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_data_view(
            view_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)] | None = None,
            index_pattern: Annotated[str, Field(min_length=1)] | None = None,
            time_field: Annotated[str, Field(min_length=1)] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Edit an existing data view in place, by id (from list_data_views):
            its display `name`, its `index_pattern` (e.g. 'logs-*') and/or its
            default `time_field`. Only the fields you pass change. The time field
            must be a date field of the view: when you change the index pattern,
            the time field (the one you pass, or the current one) is checked
            against the NEW pattern's fields, and on a mismatch nothing changes.
            A pattern that matches no index yet is accepted, but its time field
            cannot be checked (field_count is then 0). A name that defaulted to
            the old pattern stays as it is; pass `name` to change it too. The
            time field cannot be removed. Dashboard panels built by this server
            keep the pattern and time field they were built with; rebuild one
            with update_panel to use the new ones. Returns id, name,
            index_pattern, time_field and field_count.

            `space` targets a Kibana space by id (default: the default space)."""
            given = {"name": name, "index_pattern": index_pattern, "time_field": time_field}
            if all(v is None for v in given.values()):
                raise ToolError(
                    "provide at least one field to update: name, index_pattern or time_field"
                )
            for key, value in given.items():
                if value is not None and not value.strip():
                    raise ToolError(f"`{key}` is blank — pass a value, or omit it to keep the current one")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                view = gw.update_data_view(view_id, name, index_pattern, time_field)
            return with_space({
                "id": view.id, "name": view.name, "index_pattern": view.index_pattern,
                "time_field": view.time_field, "field_count": len(view.fields),
            }, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_UPSERT)
        def upsert_runtime_field(
            view_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)],
            type: RuntimeFieldType,
            script: Annotated[str, Field(min_length=1)] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create or replace a runtime field on a data view, by view id (from
            list_data_views): a field computed at search time by a Painless
            `script` that calls emit(...) with a value of `type` (e.g.
            "emit(doc['timestamp'].value.getHour())" for a long). Without a script
            the field reads the _source value of the same name. Kibana does NOT
            compile the script: one that does not compile, or emits another type,
            makes every search that Kibana runs on the data view fail (Discover,
            and visualizations that use the view) until you fix it with another
            call or remove it with delete_runtime_field. A name the view already
            has as a mapped field is refused (describe_data_view lists its
            fields); composite runtime fields are not managed here. A replacement
            keeps the field's custom label and description. Returns the stored
            `field` (name, type, script), `created`, and `previous` — the
            definition replaced (null on a create): pass its type and script
            back to undo.

            `space` targets a Kibana space by id (default: the default space)."""
            if script is not None and not script.strip():
                raise ToolError("`script` is blank — pass Painless source, or omit it")
            if not name.strip():
                raise ToolError("`name` is blank — pass the runtime field's name")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                out = gw.upsert_runtime_field(
                    view_id, RuntimeField(name=name, type=type, script=script)
                )
            return with_space({
                "view_id": out.view_id, "field": _runtime_field(out.field),
                "created": out.previous is None,
                "previous": _runtime_field(out.previous) if out.previous else None,
            }, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_UPSERT)
        def set_default_data_view(
            view_id: Annotated[str, Field(min_length=1)] | None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Make a data view the space's default — the one Discover and other
            Kibana apps open with — by id (from list_data_views), or pass
            view_id=null to clear the default. The view must exist in the space
            (Kibana itself would store any id). Replaces the current default;
            returns the new one (id, name, index_pattern, exists), `previous_id`,
            the default before the call (null when none was set), and
            `previous_exists`: pass previous_id back to undo when it is true; when
            it is false the old id named no data view (Kibana keeps a deleted
            view's id, and treats it as no default), so view_id=null undoes it.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                change = gw.set_default_data_view(view_id)
            return with_space(
                {**_default_view(change.current), "previous_id": change.previous_id,
                 "previous_exists": change.previous_exists}, space
            )

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
            `view_id` comes from list_data_views.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_data_view(view_id)
            return with_space({"id": view_id, "deleted": True}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_runtime_field(
            view_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a runtime field from a data view, by view id
            (from list_data_views) and field name. Only runtime fields can be
            deleted, not mapped ones; a composite field's subfield goes only with
            its parent's name. Searches and visualizations that use the field
            lose it. Returns the deleted definition (name, type, script, and a
            composite's `fields`), from which upsert_runtime_field can recreate a
            single-value field.

            `space` targets a Kibana space by id (default: the default space)."""
            if not name.strip():
                raise ToolError("`name` is blank — pass the runtime field's name")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                deleted = gw.delete_runtime_field(view_id, name)
            return with_space(
                {"view_id": view_id, "deleted": True, "field": _runtime_field(deleted)}, space
            )

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_short_url(
            short_url_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a short URL by id. `short_url_id` comes from resolve_short_url.

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
