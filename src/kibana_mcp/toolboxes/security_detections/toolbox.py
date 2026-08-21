"""The security-detections toolbox (Wave 3 reads + Wave 4 writes): access to the
Kibana Security detection engine and its supporting objects — detection rules,
detection alerts (signals), rule tags + prepackaged-rule status, exception lists +
items, value lists (and their items), and timelines.

Reads (v1) never mutate. The write/destructive tiers author + remove detection
rules and exception lists/items (v2), partial-update a rule's non-`enabled`
fields (v2), create/delete value lists (v3, #60), and full-replace-update,
enable/disable a rule and create/find/delete value-list items (v4, #73). This
toolbox does NOT include the `security-ai` assistant / attack-discovery surface
(a separate toolbox that needs an LLM connector). Some read object shapes
(alerts, timelines) could not be seeded on the test stack, so their fields are
mapped defensively.
"""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from kibana_mcp.toolboxes.base import SPACE_ID_PATTERN, ToolboxDeps, gateway_errors, with_space

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)


class ExceptionEntry(BaseModel):
    """One match condition of an exception item: keep/drop documents where
    `field` equals `value`. `operator` 'included' means the exception matches
    when field == value; 'excluded' means when field != value."""

    field: Annotated[str, Field(min_length=1)]
    value: Annotated[str, Field(min_length=1)]
    operator: Literal["included", "excluded"] = "included"


class SecurityDetectionsToolbox:
    name = "security-detections"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def find_detection_rules(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List detection-engine rules: id, rule_id, name, enabled, type,
            severity, risk_score, tags, immutable, version. Paginated to
            completion.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(r) for r in gw.find_detection_rules()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_detection_rule(
            rule_id: str | None = None,
            id: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get one detection rule by its stable `rule_id` or its `id` (uuid).
            Provide exactly one. Errors if no matching rule exists.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_detection_rule(rule_id, id)), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_prepackaged_rules_status(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Elastic prebuilt detection-rule install status: counts of installed
            / not-installed / custom / not-updated rules and timelines.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_prepackaged_rules_status()), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_detection_rule_tags(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[str]:
            """List the distinct tags used across detection rules.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return gw.list_detection_rule_tags()

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def search_detection_alerts(
            size: int = 20,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """Read the most recent detection alerts (signals): id, rule_name,
            severity, status, timestamp. `size` caps how many are returned.

            In a space where the detection engine has never run, the per-space alerts index may not exist yet.
            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(a) for a in gw.search_detection_alerts(size)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def find_exception_lists(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List exception-list containers: id, list_id, name, type,
            namespace_type, tags, os_types. Paginated to completion.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(x) for x in gw.find_exception_lists()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_exception_list(
            id: str | None = None,
            list_id: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get one exception-list container by its `id` (uuid) or stable
            `list_id`. Provide exactly one. Errors if no matching list exists.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_exception_list(id, list_id)), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def find_exception_items(
            list_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List the exception items within an exception list (by its
            `list_id`): id, item_id, name, list_id. Paginated to completion.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(x) for x in gw.find_exception_items(list_id)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def find_value_lists(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List value lists (id, name, type, description) — the shared value
            sets referenced by rule exceptions. Paginated to completion.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(x) for x in gw.find_value_lists()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def find_value_list_items(
            list_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List the items of a value list, by the list's `id` (passed here as
            `list_id`): id, list_id, value, type, timestamp. Paginated to
            completion.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(x) for x in gw.find_value_list_items(list_id=list_id)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def find_timelines(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List investigation timelines: saved_object_id, title, description.
            Paginated to completion.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(t) for t in gw.find_timelines()]

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_detection_rule(
            name: Annotated[str, Field(min_length=1)],
            description: Annotated[str, Field(min_length=1)],
            query: Annotated[str, Field(min_length=1)],
            index: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)],
            severity: Literal["low", "medium", "high", "critical"] = "low",
            risk_score: Annotated[int, Field(ge=0, le=100)] = 21,
            rule_id: str | None = None,
            tags: list[Annotated[str, Field(min_length=1)]] | None = None,
            interval: Annotated[str, Field(min_length=1)] = "5m",
            language: Literal["kuery", "lucene"] = "kuery",
            enabled: bool = False,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create a custom KQL/Lucene 'query' detection rule. `query` runs
            against the `index` patterns on the `interval` schedule; a hit raises
            an alert. `enabled` defaults false — a new rule starts inactive (use
            enable_detection_rule to turn it on later). Provide `rule_id` for a
            stable id, or omit to have Kibana generate one. Returns the created
            rule.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.create_detection_rule(
                    name, description, query, index, severity, risk_score,
                    rule_id, tags or [], interval, language, enabled,
                )), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_exception_list(
            name: Annotated[str, Field(min_length=1)],
            description: Annotated[str, Field(min_length=1)],
            type: Literal["detection", "rule_default", "endpoint"] = "detection",
            list_id: str | None = None,
            namespace_type: Literal["single", "agnostic"] = "single",
            tags: list[Annotated[str, Field(min_length=1)]] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create an exception-list container (the box that holds exception
            items). Provide `list_id` for a stable id, or omit to have Kibana
            generate one. Returns the created list.

            `namespace_type="agnostic"` objects are shared across ALL spaces; `space` chooses the routing space but does not isolate them.
            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.create_exception_list(
                    name, description, type, list_id, namespace_type, tags or [],
                )), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_exception_item(
            list_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)],
            description: Annotated[str, Field(min_length=1)],
            entries: Annotated[list[ExceptionEntry], Field(min_length=1)],
            item_id: str | None = None,
            namespace_type: Literal["single", "agnostic"] = "single",
            tags: list[Annotated[str, Field(min_length=1)]] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Add an exception item to an exception list (by the list's
            `list_id`). Each `entries` condition is a field==value (operator
            'included') or field!=value ('excluded') match; all entries in one
            item are ANDed. Returns the created item.

            `namespace_type="agnostic"` objects are shared across ALL spaces; `space` chooses the routing space but does not isolate them.
            `space` targets a Kibana space by id (default: the default space)."""
            # Each ExceptionEntry -> a Kibana "match" list entry. The per-entry
            # discriminator `"type": "match"` is required (a single
            # field==/!=value match); without it Kibana rejects the item.
            # (Written this way round deliberately: a comment line starting
            # `# type:` is parsed as a PEP 484 type comment and fails the
            # type-check with a bogus syntax error.)
            mapped = [
                {"field": e.field, "operator": e.operator, "type": "match", "value": e.value}
                for e in entries
            ]
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.create_exception_item(
                    list_id, name, description, mapped, item_id, namespace_type, tags or [],
                )), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_detection_rule(
            rule_id: str | None = None,
            id: str | None = None,
            name: Annotated[str, Field(min_length=1)] | None = None,
            description: Annotated[str, Field(min_length=1)] | None = None,
            tags: list[Annotated[str, Field(min_length=1)]] | None = None,
            severity: Literal["low", "medium", "high", "critical"] | None = None,
            risk_score: Annotated[int, Field(ge=0, le=100)] | None = None,
            query: Annotated[str, Field(min_length=1)] | None = None,
            interval: Annotated[str, Field(min_length=1)] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Partial-update a detection rule by `rule_id` or `id` (exactly one). Only the
            fields you pass change (PATCH); pass at least one. Does NOT edit `enabled` (use
            enable_detection_rule / disable_detection_rule instead) or actions. The returned
            rule is a summary: patched `query`/`description`/`interval` apply but are not
            echoed. A static `severity` is overridden per-alert if the rule has a
            severity_mapping.

            `space` targets a Kibana space by id (default: the default space)."""
            if bool(rule_id) == bool(id):
                raise ToolError("provide exactly one of rule_id or id")
            if all(v is None for v in (name, description, tags, severity, risk_score, query, interval)):
                raise ToolError("provide at least one field to update")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.update_detection_rule(
                    rule_id, id, name, description, tags, severity, risk_score, query, interval,
                )), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def replace_detection_rule(
            rule_id: str | None = None,
            id: str | None = None,
            name: Annotated[str, Field(min_length=1)] | None = None,
            description: Annotated[str, Field(min_length=1)] | None = None,
            tags: list[Annotated[str, Field(min_length=1)]] | None = None,
            severity: Literal["low", "medium", "high", "critical"] | None = None,
            risk_score: Annotated[int, Field(ge=0, le=100)] | None = None,
            query: Annotated[str, Field(min_length=1)] | None = None,
            index: list[Annotated[str, Field(min_length=1)]] | None = None,
            interval: Annotated[str, Field(min_length=1)] | None = None,
            language: Literal["kuery", "lucene"] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Full-replace (PUT) a detection rule by `rule_id` or `id` (exactly one).
            Unlike `update_detection_rule` (a PATCH), this reads the current rule,
            echoes every writable field back, and layers your changed fields on top —
            so it can reach fields `update_detection_rule` can't touch, like `index` or
            `language`. Only the fields you pass change; pass at least one. Does NOT
            edit `enabled` (use enable_detection_rule / disable_detection_rule instead)
            or actions. Refuses an Elastic-prebuilt (immutable) rule.

            `space` targets a Kibana space by id (default: the default space)."""
            if bool(rule_id) == bool(id):
                raise ToolError("provide exactly one of rule_id or id")
            changes = {k: v for k, v in {
                "name": name, "description": description, "tags": tags,
                "severity": severity, "risk_score": risk_score, "query": query,
                "index": index, "interval": interval, "language": language,
            }.items() if v is not None}
            if not changes:
                raise ToolError("provide at least one field to change")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(
                    asdict(gw.replace_detection_rule(rule_id=rule_id, id=id, changes=changes)), space
                )

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def enable_detection_rule(
            rule_id: str | None = None,
            id: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Enable a detection rule (start its schedule) by its stable `rule_id`
            or its `id` (uuid). Provide exactly one.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.enable_detection_rule(rule_id=rule_id, id=id)), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def disable_detection_rule(
            rule_id: str | None = None,
            id: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Disable a detection rule (stop its schedule) by its stable `rule_id`
            or its `id` (uuid). Provide exactly one.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.disable_detection_rule(rule_id=rule_id, id=id)), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_value_list(
            name: Annotated[str, Field(min_length=1)],
            description: Annotated[str, Field(min_length=1)],
            type: Annotated[str, Field(min_length=1)],
            id: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create a value list — a shared value set referenced by rule exceptions.
            `type` is the ES data type of the values (e.g. keyword, ip, ip_range, text,
            date, integer, long, double). The `.lists`/`.items` backing indices are
            per-space (created on first value-list write in a space). Provide `id` for
            a stable id, else Kibana generates one.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.create_value_list(name, description, type, id)), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_value_list_item(
            list_id: Annotated[str, Field(min_length=1)],
            value: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Add an item (a value) to a value list, by the list's `id` (passed
            here as `list_id`). The `.lists`/`.items` backing indices are per-space
            (created on first value-list write in a space; mirrors
            create_value_list). Returns the created item.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(
                    asdict(gw.create_value_list_item(list_id=list_id, value=value)), space
                )

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_detection_rule(
            rule_id: str | None = None,
            id: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a detection rule by its stable `rule_id` or its
            `id` (uuid). Provide exactly one.

            `space` targets a Kibana space by id (default: the default space)."""
            if bool(rule_id) == bool(id):
                raise ToolError("provide exactly one of rule_id or id")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_detection_rule(rule_id, id)
            return with_space({"deleted": True, "rule_id": rule_id, "id": id}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_exception_list(
            id: str | None = None,
            list_id: str | None = None,
            namespace_type: Literal["single", "agnostic"] = "single",
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete an exception-list container (and its items) by
            its `id` (uuid) or stable `list_id`. Provide exactly one.

            `namespace_type="agnostic"` objects are shared across ALL spaces; `space` chooses the routing space but does not isolate them.
            `space` targets a Kibana space by id (default: the default space)."""
            if bool(id) == bool(list_id):
                raise ToolError("provide exactly one of id or list_id")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_exception_list(id, list_id, namespace_type)
            return with_space({"deleted": True, "id": id, "list_id": list_id}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_exception_item(
            id: str | None = None,
            item_id: str | None = None,
            namespace_type: Literal["single", "agnostic"] = "single",
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete an exception item by its `id` (uuid) or stable
            `item_id`. Provide exactly one.

            `namespace_type="agnostic"` objects are shared across ALL spaces; `space` chooses the routing space but does not isolate them.
            `space` targets a Kibana space by id (default: the default space)."""
            if bool(id) == bool(item_id):
                raise ToolError("provide exactly one of id or item_id")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_exception_item(id, item_id, namespace_type)
            return with_space({"deleted": True, "id": id, "item_id": item_id}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_value_list(
            id: Annotated[str, Field(min_length=1)],
            force: bool = False,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a value list (and all its items) by `id`. Refuses (409)
            a list referenced by an exception item unless `force=True`, which deletes it
            anyway and leaves those exceptions with a dangling reference.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_value_list(id, force)
            return with_space({"deleted": True, "id": id}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_value_list_item(
            item_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a single value-list item by its `item_id`
            (leaves the parent value list and its other items untouched).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_value_list_item(item_id=item_id)
            return with_space({"deleted": True, "item_id": item_id}, space)
