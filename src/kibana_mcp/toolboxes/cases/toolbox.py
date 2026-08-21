"""The cases toolbox: Kibana incident cases.

GA and usable with the default "none" connector — no external ITSM needed. First
cut: create / read / update / comment / delete. Deferred (design doc §9): `push`
to external ITSM (side-effecting), reading comment bodies, case configuration,
assignees, and alert/visualization attachments.
"""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.toolboxes.base import SPACE_ID_PATTERN, ToolboxDeps, gateway_errors, with_space

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

_Severity = Literal["critical", "high", "medium", "low"]
_Status = Literal["open", "in-progress", "closed"]


class CasesToolbox:
    name = "cases"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_cases(
            search: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List cases (optionally filtered by a title/description search).
            Concise summaries: id, title, status, severity, owner, tags,
            total_comments.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(c) for c in gw.list_cases(search)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_case(
            case_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get one case's summary by id.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_case(case_id)), space)

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_case(
            title: Annotated[str, Field(min_length=1)],
            description: Annotated[str, Field(min_length=1)],
            tags: list[str] | None = None,
            severity: _Severity | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Open a new Kibana case. Uses the built-in 'none' connector (no
            external ITSM setup required).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                created = gw.create_case(title, description, tags, severity)
                return with_space(asdict(created), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_case(
            case_id: str,
            status: _Status | None = None,
            severity: _Severity | None = None,
            tags: list[str] | None = None,
            title: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Update a case's status, severity, tags, and/or title. The current
            version is handled for you. Provide at least one field to change.

            `space` targets a Kibana space by id (default: the default space)."""
            if status is None and severity is None and tags is None and title is None:
                raise ToolError("provide at least one field to update (status/severity/tags/title)")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                updated = gw.update_case(case_id, status, severity, tags, title)
                return with_space(asdict(updated), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def add_case_comment(
            case_id: str,
            comment: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Add a text comment to a case. Returns the updated case.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.add_case_comment(case_id, comment)), space)

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_case(
            case_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete a case. This cannot be undone.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_case(case_id)
            return with_space({"id": case_id, "deleted": True}, space)
