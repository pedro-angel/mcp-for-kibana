"""The platform-admin toolbox: 5 read tools + a write tier (create/update space,
create-or-update role, copy and share saved objects) + a destructive tier
(delete space/role, overwrite and unshare saved objects) over spaces + roles +
upgrade readiness + saved objects between spaces (D49). Driven through the
in-memory fastmcp Client against a FakeGateway. The unit fixture registers with
no tier gating, so all 14 appear."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.platform_admin.toolbox import PlatformAdminToolbox
from tests.fakes import FakeGateway

PLATFORM_ADMIN_READ = {
    "list_spaces", "get_space", "list_roles", "get_role", "get_upgrade_status",
}
PLATFORM_ADMIN_WRITE = {
    "create_space", "update_space", "create_or_update_role",
    "copy_saved_objects_to_spaces", "share_saved_objects_to_spaces",
}
PLATFORM_ADMIN_DESTRUCTIVE = {
    "delete_space", "delete_role",
    "overwrite_saved_objects_in_spaces", "unshare_saved_objects_from_spaces",
}
SPACE_OBJECT_TOOLS = {
    "copy_saved_objects_to_spaces", "share_saved_objects_to_spaces",
    "overwrite_saved_objects_in_spaces", "unshare_saved_objects_from_spaces",
}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def opened():
    return []  # the `space` each gateway was opened with


@pytest.fixture()
def mcp(gateway, opened):
    def factory(space=None):
        opened.append(space)
        gateway.current_space = space or "default"
        return gateway

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    PlatformAdminToolbox().register(server, deps)
    return server


async def test_platform_admin_tools_are_tiered(mcp):
    # The unit fixture registers with no tier gating, so all 14 tools appear.
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == PLATFORM_ADMIN_READ | PLATFORM_ADMIN_WRITE | PLATFORM_ADMIN_DESTRUCTIVE
    for n in PLATFORM_ADMIN_READ:
        assert tools[n].annotations.readOnlyHint is True
    for n in PLATFORM_ADMIN_DESTRUCTIVE:
        assert tools[n].annotations.destructiveHint is True
    for n in PLATFORM_ADMIN_WRITE:
        assert tools[n].annotations.readOnlyHint is False
        assert tools[n].annotations.destructiveHint is False
    # a repeat share adds nothing; a repeat copy makes more copies
    assert tools["share_saved_objects_to_spaces"].annotations.idempotentHint is True
    assert tools["copy_saved_objects_to_spaces"].annotations.idempotentHint is False


async def test_create_space_serializes(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("create_space", {"id": "mk", "name": "Marketing"})
    assert r.data["id"] == "mk"


async def test_create_space_bad_id_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_space", {"id": "Bad Id!", "name": "x"})


async def test_update_space_serializes(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("update_space", {"space_id": "default", "description": "new"})
    assert r.data["description"] == "new"


async def test_create_or_update_role_requires_a_grant(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_or_update_role", {"name": "empty"})


async def test_create_or_update_role_kibana_spaces_needs_base(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_or_update_role",
                                   {"name": "r", "cluster_privileges": ["monitor"],
                                    "kibana_spaces": ["default"]})


async def test_create_or_update_role_maps_index_privileges(mcp, gateway):
    async with Client(mcp) as client:
        r = await client.call_tool("create_or_update_role", {
            "name": "r", "cluster_privileges": ["monitor"],
            "index_privileges": [{"names": ["logs-*"], "privileges": ["read"]}],
            "kibana_base": ["read"], "kibana_spaces": ["*"]})
    assert r.data["name"] == "r"
    role = next(x for x in gateway.roles if x.name == "r")
    assert role.index_privileges[0].names == ("logs-*",)  # IndexPrivilege -> dict -> DTO round-trip
    assert role.kibana_privileges[0].base == ("read",)


async def test_create_or_update_role_bare_call_on_existing_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):  # kibana_system is seeded; create_only defaults True
            await client.call_tool("create_or_update_role",
                                   {"name": "kibana_system", "cluster_privileges": ["monitor"]})


async def test_create_or_update_role_reserved_not_clobbered_even_with_overwrite(mcp):
    # create_only=False (deliberate overwrite) must STILL refuse a reserved system role.
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_or_update_role",
                                   {"name": "kibana_system", "cluster_privileges": ["monitor"],
                                    "create_only": False})


async def test_delete_space_default_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("delete_space", {"space_id": "default", "force": True})


async def test_delete_space_requires_force(mcp, gateway):
    gateway.create_space("mk", "M", None, None, None, None, None)
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("delete_space", {"space_id": "mk"})
        r = await client.call_tool("delete_space", {"space_id": "mk", "force": True})
    assert r.data == {"deleted": True, "space_id": "mk"}


async def test_delete_role_reserved_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("delete_role", {"name": "kibana_system"})


async def test_list_spaces_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_spaces", {})
    assert result.data[0]["id"] == "default"
    assert result.data[0]["reserved"] is True
    assert result.data[0]["disabled_features"] == ["apm", "uptime"]


async def test_get_space_found(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_space", {"space_id": "default"})
    assert result.data["name"] == "Default"
    assert result.data["solution"] == "es"


async def test_get_space_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_space", {"space_id": "nope"})


async def test_get_space_blank_id_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_space", {"space_id": ""})


async def test_list_roles_serializes_nested_privileges(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_roles", {})
    role = result.data[0]
    assert role["name"] == "kibana_system"
    assert role["reserved"] is True
    assert role["cluster_privileges"] == ["monitor", "manage_index_templates"]
    assert role["index_privileges"][0]["names"] == [".kibana*"]
    assert role["kibana_privileges"][0]["spaces"] == ["*"]
    # feature-name summarization survives the DTO -> asdict -> JSON round-trip.
    assert role["kibana_privileges"][0]["features"] == ["dashboard", "discover"]


async def test_get_role_found(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_role", {"role_name": "kibana_system"})
    assert result.data["name"] == "kibana_system"


async def test_get_role_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_role", {"role_name": "nope"})


async def test_get_role_blank_name_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_role", {"role_name": ""})


async def test_get_upgrade_status_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_upgrade_status", {})
    assert result.data["ready_for_upgrade"] is True
    assert result.data["es_deprecation_count"] == 0
    assert result.data["api_deprecations"][0]["level"] == "warning"


# --- saved objects between spaces (D49) -------------------------------------

_DV = {"type": "index-pattern", "id": "dv1"}


def _add_space(gateway, space_id):
    gateway.create_space(space_id, space_id, None, None, None, None, None)


async def test_space_object_tools_take_source_space_never_space(mcp):
    # D10: platform-admin carries no routing `space`; the source is an
    # argument of the operation, validated by the same id grammar.
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in SPACE_OBJECT_TOOLS:
        props = tools[name].inputSchema["properties"]
        assert "space" not in props and "source_space" in props, name
    assert "space" not in str(tools["list_spaces"].inputSchema)


async def test_copy_makes_new_copies_from_the_default_space(mcp, gateway, opened):
    _add_space(gateway, "team-a")
    async with Client(mcp) as client:
        r = await client.call_tool("copy_saved_objects_to_spaces", {
            "objects": [_DV], "target_spaces": ["team-a"]})
    assert opened == [None]
    assert gateway.space_copies == [{"objects": [_DV], "spaces": ["team-a"],
                                     "include_references": True, "overwrite": False}]
    assert r.data == {"include_references": True, "results": [{
        "space": "team-a", "success": True, "failed": [], "unchanged": [],
        "copied": [{"type": "index-pattern", "id": "dv1", "title": None,
                    "destination_id": "team-a-dv1", "overwritten": False}],
    }]}


async def test_copy_from_a_source_space_echoes_it(mcp, gateway, opened):
    _add_space(gateway, "team-a")
    _add_space(gateway, "team-b")
    async with Client(mcp) as client:
        r = await client.call_tool("copy_saved_objects_to_spaces", {
            "objects": [_DV], "target_spaces": ["team-b"], "include_references": False,
            "source_space": "team-a"})
    assert opened == ["team-a"]
    assert r.data["source_space"] == "team-a" and r.data["include_references"] is False


async def test_overwrite_sends_overwrite(mcp, gateway):
    _add_space(gateway, "team-a")
    async with Client(mcp) as client:
        r = await client.call_tool("overwrite_saved_objects_in_spaces", {
            "objects": [_DV], "target_spaces": ["team-a"]})
    assert gateway.space_copies[0]["overwrite"] is True
    assert r.data["results"][0]["copied"][0]["overwritten"] is True


@pytest.mark.parametrize("tool", ["copy_saved_objects_to_spaces", "overwrite_saved_objects_in_spaces"])
async def test_copy_refusals_name_the_fix(mcp, gateway, tool):
    _add_space(gateway, "team-a")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="list_spaces"):
            await client.call_tool(tool, {"objects": [_DV], "target_spaces": ["ghost"]})
        with pytest.raises(ToolError, match="the source space"):
            await client.call_tool(tool, {"objects": [_DV], "target_spaces": ["default"]})
        with pytest.raises(ToolError, match="find_saved_objects"):
            await client.call_tool(tool, {
                "objects": [{"type": "index-pattern", "id": "nope"}], "target_spaces": ["team-a"]})
        with pytest.raises(ToolError, match="index-pattern/dv1 more than once"):
            await client.call_tool(tool, {"objects": [_DV, _DV], "target_spaces": ["team-a"]})
        with pytest.raises(ToolError, match="'team-a' more than once"):
            await client.call_tool(tool, {"objects": [_DV], "target_spaces": ["team-a", "team-a"]})
        for bad in ({"objects": [], "target_spaces": ["team-a"]},
                    {"objects": [_DV], "target_spaces": []},
                    {"objects": [_DV], "target_spaces": ["*"]},
                    {"objects": [{"type": "", "id": "x"}], "target_spaces": ["team-a"]}):
            with pytest.raises(ToolError):
                await client.call_tool(tool, bad)
    assert gateway.space_copies == []


async def test_copy_reports_an_uncopyable_type_as_failed_with_its_fix(mcp, gateway):
    _add_space(gateway, "team-a")
    gateway.object_spaces[("ingest-outputs", "out-1")] = []
    async with Client(mcp) as client:
        r = await client.call_tool("copy_saved_objects_to_spaces", {
            "objects": [{"type": "ingest-outputs", "id": "out-1"}], "target_spaces": ["team-a"]})
    [result] = r.data["results"]
    assert result["success"] is False and result["copied"] == []
    assert result["failed"][0]["error"] == "unsupported_type"
    assert "cannot copy this type" in result["failed"][0]["fix"]


async def test_a_missing_source_space_is_worded_for_source_space(gateway):
    from kibana_mcp.core.errors import KibanaRejected, KibanaSpaceNotFound, KibanaUnavailable

    def missing(space=None):
        raise KibanaSpaceNotFound("space 'x' not found — … or omit `space` …")

    def pinned(space=None):
        raise KibanaRejected("KIBANA_URL is already space-pinned; the `space` parameter …")

    def unreachable(space=None):  # any other gateway error is a tool error too
        raise KibanaUnavailable("cannot reach Kibana — check that the server's KIBANA_URL is reachable")

    for factory, needle in ((missing, "source_space 'team-z' not found — check the ids with "
                                      "list_spaces, or omit source_space"),
                            (pinned, "space-pinned ('/s/<id>' base path) — omit source_space; "
                                     "the objects are then read from the space KIBANA_URL "
                                     "pins, or from the default space when only the public "
                                     "URL is pinned"),
                            (unreachable, "cannot reach Kibana")):
        server = FastMCP("test")
        PlatformAdminToolbox().register(server, ToolboxDeps(
            gateway_factory=factory, public_kibana_url="http://kb:5601"))
        async with Client(server) as client:
            with pytest.raises(ToolError) as exc:
                await client.call_tool("share_saved_objects_to_spaces", {
                    "objects": [_DV], "spaces": ["default"], "source_space": "team-z"})
        assert needle in str(exc.value)


async def test_share_adds_spaces_and_a_repeat_adds_none(mcp, gateway):
    _add_space(gateway, "team-a")
    async with Client(mcp) as client:
        first = await client.call_tool("share_saved_objects_to_spaces", {
            "objects": [_DV], "spaces": ["team-a", "*"]})
        again = await client.call_tool("share_saved_objects_to_spaces", {
            "objects": [_DV], "spaces": ["team-a"]})
    assert first.data == {"objects": [{
        "type": "index-pattern", "id": "dv1", "spaces": ["default", "team-a", "*"],
        "added": ["team-a", "*"], "removed": [], "deleted": False,
        "spaces_with_matching_origins": [], "error": None,
    }]}
    assert again.data["objects"][0]["added"] == []


async def test_share_refuses_a_one_space_type_and_a_missing_space(mcp, gateway):
    _add_space(gateway, "team-a")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="copy_saved_objects_to_spaces"):
            await client.call_tool("share_saved_objects_to_spaces", {
                "objects": [{"type": "dashboard", "id": "dash-1"}], "spaces": ["team-a"]})
        with pytest.raises(ToolError, match="space 'ghost' not found"):
            await client.call_tool("share_saved_objects_to_spaces", {
                "objects": [_DV], "spaces": ["ghost"]})
        with pytest.raises(ToolError):  # not a space id
            await client.call_tool("share_saved_objects_to_spaces", {
                "objects": [_DV], "spaces": ["Team A"]})
    assert gateway.sharing_updates == []


async def test_unshare_removes_and_refuses_the_last_space_unless_forced(mcp, gateway):
    _add_space(gateway, "team-a")
    gateway.object_spaces[("index-pattern", "dv1")] = ["default", "team-a"]
    async with Client(mcp) as client:
        kept = await client.call_tool("unshare_saved_objects_from_spaces", {
            "objects": [_DV], "spaces": ["team-a"]})
        with pytest.raises(ToolError, match="nothing to remove"):
            await client.call_tool("unshare_saved_objects_from_spaces", {
                "objects": [_DV], "spaces": ["team-a"]})
        with pytest.raises(ToolError) as last:
            await client.call_tool("unshare_saved_objects_from_spaces", {
                "objects": [_DV], "spaces": ["default"]})
        assert gateway.object_spaces[("index-pattern", "dv1")] == ["default"]
        gone = await client.call_tool("unshare_saved_objects_from_spaces", {
            "objects": [_DV], "spaces": ["default"], "force": True})
    assert kept.data["objects"][0]["spaces"] == ["default"]
    assert kept.data["objects"][0]["removed"] == ["team-a"]
    assert "Kibana deletes an object left in no space" in str(last.value)
    assert "force=True" in str(last.value)
    assert gone.data["objects"][0]["deleted"] is True
    assert ("index-pattern", "dv1") in gateway.deleted
