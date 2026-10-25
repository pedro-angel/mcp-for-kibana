"""The platform-admin toolbox (Wave 4): access to the Kibana administration
surface available on a Basic license and wrapped by kibana-py — Spaces, security
Roles, and Upgrade-Assistant readiness.

`read` tools list/get spaces + roles + upgrade readiness; the `write` tier
creates/updates spaces + creates-or-updates roles; the `destructive` tier deletes
them (delete_space wipes every saved object in the space — force-gated;
delete_role revokes access for its assignees). Both delete tools refuse the
reserved system objects (the default space, reserved roles). Saved objects move
between spaces too (D49): a copy as new objects and a share are `write`, a copy
that overwrites and an unshare (which deletes an object left in no space) are
`destructive`; they take `source_space`, an argument of the operation, never the
routing `space` parameter (D10). Logstash pipeline management (Platinum, 403 on
Basic) and session invalidation are out of scope.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from kibana_mcp.core.errors import KibanaRejected, KibanaSpaceNotFound
from kibana_mcp.core.models import SharingChange, SpaceCopyResult
from kibana_mcp.core.space_sharing import duplicates
from kibana_mcp.ports.gateway import KibanaGateway
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


# Idempotent writes: a repeat share changes nothing (probed).
_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


class SavedObjectRef(BaseModel):
    """One saved object: its `type` (e.g. 'dashboard', 'index-pattern') and
    `id`, as find_saved_objects returns them for the source space."""

    type: Annotated[str, Field(min_length=1)]
    id: Annotated[str, Field(min_length=1)]


# Kibana's limits on the copy and share routes (its 400s name them).
_Objects = Annotated[list[SavedObjectRef], Field(min_length=1, max_length=1000)]
_CopyTargets = Annotated[
    list[Annotated[str, Field(pattern=SPACE_ID_PATTERN)]], Field(min_length=1, max_length=100)
]
# Sharing takes '*' too: every space, current and future.
_ShareSpaces = Annotated[
    list[Annotated[str, Field(pattern=r"^(\*|[a-z0-9_-]+)$")]],
    Field(min_length=1, max_length=1000),
]
_SourceSpace = Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None


def _refs(objects: list[SavedObjectRef], spaces: list[str], spaces_arg: str) -> list[dict[str, str]]:
    """The objects as Kibana takes them, refusing a repeat in either list
    (Kibana 400s a repeated object on the copy and answers 409 for the second
    on the share; a repeated space id is a 400 on the copy, probed)."""
    refs = [{"type": o.type, "id": o.id} for o in objects]
    repeated = duplicates(f"{r['type']}/{r['id']}" for r in refs)
    if repeated:
        raise ToolError(f"`objects` names {', '.join(repeated)} more than once — name each once")
    repeated = duplicates(spaces)
    if repeated:
        raise ToolError(
            f"`{spaces_arg}` names {', '.join(repr(x) for x in repeated)} more than once — "
            "name each once")
    return refs


@contextmanager
def _source_gateway(deps: ToolboxDeps, source_space: str | None) -> Iterator[KibanaGateway]:
    """A gateway on the source space, inside gateway_errors like every other
    tool's, with the source_space wording on the two refusals the factory
    words for `space`."""
    with gateway_errors():
        try:
            gw = deps.gateway_factory(source_space)
        except KibanaSpaceNotFound as e:
            raise KibanaSpaceNotFound(
                f"source_space '{source_space}' not found — check the ids with list_spaces, "
                "or omit source_space to use the default space"
            ) from e
        except KibanaRejected as e:
            if source_space is None:
                raise
            raise KibanaRejected(
                "this deployment's Kibana URL is already space-pinned ('/s/<id>' base "
                "path), so the objects are read from that space: omit source_space"
            ) from e
        with gw:
            yield gw


def _copy_results(
    results: list[SpaceCopyResult], source_space: str | None, include_references: bool
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "include_references": include_references,
        "results": [asdict(r) for r in results],
    }
    return out if source_space is None else {"source_space": source_space, **out}


def _sharing_result(changes: list[SharingChange], source_space: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"objects": [asdict(c) for c in changes]}
    return out if source_space is None else {"source_space": source_space, **out}


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

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def copy_saved_objects_to_spaces(
            objects: _Objects,
            target_spaces: _CopyTargets,
            include_references: bool = True,
            source_space: _SourceSpace = None,
        ) -> dict[str, Any]:
            """Copy saved objects (dashboards, visualizations, data views, …)
            from `source_space` (default: the default space) into each of
            `target_spaces` as NEW objects with new ids — nothing already in a
            target space changes, and each call makes further copies. Take
            `objects` ({"type", "id"}) from find_saved_objects run on the
            source space, and space ids from list_spaces; every target space
            must exist. `include_references` (default true) copies what the
            objects use too (a dashboard's data view and visualizations, each
            as a new copy); without it a dashboard whose references are not
            copied fails. Returns, per target space, `copied` (each object's
            `destination_id`, its id there), `failed` (Kibana's `error` and a
            `fix`; nothing was written for those) and `unchanged`. To undo,
            delete the copies by their destination ids in the target space.
            To replace copies made earlier instead, use
            overwrite_saved_objects_in_spaces (destructive tier)."""
            refs = _refs(objects, target_spaces, "target_spaces")
            with _source_gateway(deps, source_space) as gw:
                results = gw.copy_saved_objects_to_spaces(
                    refs, target_spaces, include_references, False)
            return _copy_results(results, source_space, include_references)

        @mcp.tool(tags={self.name, "write"}, annotations=_IDEMPOTENT_WRITE)
        def share_saved_objects_to_spaces(
            objects: _Objects,
            spaces: _ShareSpaces,
            source_space: _SourceSpace = None,
        ) -> dict[str, Any]:
            """Share saved objects to more spaces: the SAME object (same id)
            then shows in each of `spaces` too, and an edit in one space shows
            in all — unlike copy_saved_objects_to_spaces, which makes separate
            objects. Only adds spaces; '*' shares to every space, current and
            future. Take `objects` ({"type", "id"}) from find_saved_objects on
            `source_space` (default: the default space), and space ids from
            list_spaces; every space must exist. Kibana keeps some types in
            one space (dashboards, visualizations, saved searches, maps, tags);
            data views can be shared, and a call naming a one-space type is
            refused before any change. Returns each object's `spaces` after
            the call and the ones `added` (a repeat adds none);
            `spaces_with_matching_origins` lists spaces already holding a copy
            of it, which sharing leaves in place beside it. To undo, remove the
            `added` spaces with unshare_saved_objects_from_spaces (destructive
            tier)."""
            refs = _refs(objects, spaces, "spaces")
            with _source_gateway(deps, source_space) as gw:
                changes = gw.share_saved_objects(refs, spaces)
            return _sharing_result(changes, source_space)

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

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def overwrite_saved_objects_in_spaces(
            objects: _Objects,
            target_spaces: _CopyTargets,
            include_references: bool = True,
            source_space: _SourceSpace = None,
        ) -> dict[str, Any]:
            """Copy saved objects from `source_space` (default: the default
            space) into each of `target_spaces`, REPLACING the objects earlier
            copies of them made there. DESTRUCTIVE: whatever changed in those
            copies is lost and cannot be recovered. An object with no earlier
            copy in a target space is copied there. With `include_references`
            (default true) the objects they use are copied and replaced too.
            Take `objects` ({"type", "id"}) from find_saved_objects on the
            source space, and space ids from list_spaces; every target space
            must exist. Returns, per target space, `copied` (`overwritten`
            true for each replaced object, with its `destination_id`),
            `failed` (Kibana's `error` and a `fix`: when more than one object
            there came from this one, nothing is replaced) and `unchanged`
            (objects already in that space by sharing, left as they are).
            copy_saved_objects_to_spaces makes new copies instead and changes
            nothing that exists."""
            refs = _refs(objects, target_spaces, "target_spaces")
            with _source_gateway(deps, source_space) as gw:
                results = gw.copy_saved_objects_to_spaces(
                    refs, target_spaces, include_references, True)
            return _copy_results(results, source_space, include_references)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def unshare_saved_objects_from_spaces(
            objects: _Objects,
            spaces: _ShareSpaces,
            force: bool = False,
            source_space: _SourceSpace = None,
        ) -> dict[str, Any]:
            """Stop sharing saved objects to `spaces`: they no longer show
            there, and objects in those spaces that use them (a dashboard on a
            data view) lose them. DESTRUCTIVE: Kibana DELETES an object
            removed from every space it is in, so that is refused unless
            force=True. '*' removes the every-space share (spaces named
            alongside it stay). Take `objects` ({"type", "id"}) from
            find_saved_objects on `source_space` (default: the default space),
            which may be one of the spaces removed. A call that would remove
            nothing is refused. Returns each object's `spaces` after the call,
            the ones `removed`, and `deleted` (true when it was removed from
            its last space). To undo a removal that kept the object, share it
            back with share_saved_objects_to_spaces; a deleted object is gone.
            The spaces are read before the change, not atomically with it."""
            refs = _refs(objects, spaces, "spaces")
            with _source_gateway(deps, source_space) as gw:
                changes = gw.unshare_saved_objects(refs, spaces, force)
            return _sharing_result(changes, source_space)
