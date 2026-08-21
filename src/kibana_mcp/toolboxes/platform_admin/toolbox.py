"""The platform-admin toolbox (Wave 4): access to the Kibana administration
surface available on a Basic license and wrapped by kibana-py — Spaces, security
Roles, and Upgrade-Assistant readiness.

`read` tools list/get spaces + roles + upgrade readiness; the `write` tier
creates/updates spaces + creates-or-updates roles; the `destructive` tier deletes
them (delete_space wipes every saved object in the space — force-gated;
delete_role revokes access for its assignees). Both delete tools refuse the
reserved system objects (the default space, reserved roles). Logstash pipeline
management (Platinum, 403 on Basic) and session invalidation stay deferred.
"""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from kibana_mcp.toolboxes.base import SPACE_ID_PATTERN, ToolboxDeps, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)


class IndexPrivilege(BaseModel):
    """One Elasticsearch index-privilege grant for a role: `privileges` (e.g.
    ['read']) on the index patterns in `names` (e.g. ['logs-*'])."""

    names: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
    privileges: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]


class PlatformAdminToolbox:
    name = "platform-admin"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_spaces() -> list[dict[str, Any]]:
            """List all Kibana spaces: id, name, description, solution
            ('es'/'classic'/'oblt'/'security'), the features disabled in the
            space, and whether it is a reserved system space."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(s) for s in gw.list_spaces()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_space(space_id: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one Kibana space by its id (the `id` field returned by
            list_spaces, e.g. 'default'). Errors if the space does not exist."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_space(space_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_roles() -> list[dict[str, Any]]:
            """List all Kibana roles (including reserved system roles): name,
            description, whether reserved, and a summary of granted privileges —
            Elasticsearch cluster + index privileges, run_as, and per-space
            Kibana base/feature grants. This is RBAC *configuration*, not
            secrets."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(r) for r in gw.list_roles()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_role(role_name: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one Kibana role by name (e.g. 'kibana_system'), with its full
            privilege summary. Errors if the role does not exist."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_role(role_name))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_upgrade_status() -> dict[str, Any]:
            """The Upgrade Assistant readiness status: whether the deployment is
            ready to upgrade, a details message, the count of recent
            Elasticsearch deprecation-log entries, and the deprecated Kibana APIs
            still in use (each as title + severity level + type). Deprecation
            counts vary with cluster usage."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_upgrade_status())

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_space(
            id: Annotated[str, Field(pattern=SPACE_ID_PATTERN)],
            name: Annotated[str, Field(min_length=1)],
            description: Annotated[str, Field(min_length=1)] | None = None,
            color: Annotated[str, Field(min_length=1)] | None = None,
            initials: Annotated[str, Field(min_length=1)] | None = None,
            disabled_features: list[Annotated[str, Field(min_length=1)]] | None = None,
            solution: Literal["es", "classic", "oblt", "security"] | None = None,
        ) -> dict[str, Any]:
            """Create a Kibana space. `id` is immutable (lowercase alnum/_/-);
            `disabled_features` turns off feature ids; `solution` picks the space's
            nav/feature view. Returns the created space."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(
                    gw.create_space(
                        id, name, description, color, initials, disabled_features, solution
                    )
                )

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_space(
            space_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)] | None = None,
            description: str | None = None,
            color: Annotated[str, Field(min_length=1)] | None = None,
            initials: Annotated[str, Field(min_length=1)] | None = None,
            disabled_features: list[Annotated[str, Field(min_length=1)]] | None = None,
            solution: Literal["es", "classic", "oblt", "security"] | None = None,
        ) -> dict[str, Any]:
            """Update a Kibana space's fields (read-modify-write — omitted fields are
            preserved). The `id` cannot change. `solution` reshapes the space's
            features. Pass `description=""` to clear the description. Returns the
            updated space."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(
                    gw.update_space(
                        space_id, name, description, color, initials, disabled_features, solution
                    )
                )

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_or_update_role(
            name: Annotated[str, Field(min_length=1)],
            cluster_privileges: list[Annotated[str, Field(min_length=1)]] | None = None,
            index_privileges: list[IndexPrivilege] | None = None,
            kibana_base: list[Annotated[str, Field(min_length=1)]] | None = None,
            kibana_spaces: list[Annotated[str, Field(min_length=1)]] | None = None,
            description: Annotated[str, Field(min_length=1)] | None = None,
            create_only: bool = True,
        ) -> dict[str, Any]:
            """Create or FULL-REPLACE a Kibana role. `create_only` defaults True — a
            bare call on an existing role errors instead of silently replacing its
            grants; pass create_only=False to deliberately overwrite (a full replace:
            omitted grants are dropped). Grant Elasticsearch `cluster_privileges` +
            `index_privileges`, and a Kibana `kibana_base` (e.g. ['read']/['all'])
            across `kibana_spaces` (default all). Returns the role."""
            if not (cluster_privileges or index_privileges or kibana_base):
                raise ToolError("provide at least one privilege grant (cluster/index/kibana)")
            if kibana_spaces and not kibana_base:
                raise ToolError("kibana_spaces requires kibana_base")
            mapped = [
                {"names": p.names, "privileges": p.privileges} for p in (index_privileges or [])
            ]
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(
                    gw.create_or_update_role(
                        name,
                        cluster_privileges,
                        mapped,
                        kibana_base,
                        kibana_spaces,
                        description,
                        create_only,
                    )
                )

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_space(
            space_id: Annotated[str, Field(min_length=1)],
            force: bool = False,
        ) -> dict[str, Any]:
            """Delete a Kibana space. DESTRUCTIVE: permanently removes EVERY saved
            object in the space (dashboards, rules, data views, …). Requires
            force=True (the whole-space wipe is not reversible); refuses the default
            + reserved system spaces regardless of force."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_space(space_id, force)
            return {"deleted": True, "space_id": space_id}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_role(name: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Delete a Kibana role by name. DESTRUCTIVE: revokes the role's access
            for everyone assigned it. Refuses reserved system roles (e.g.
            kibana_system)."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_role(name)
            return {"deleted": True, "name": name}
