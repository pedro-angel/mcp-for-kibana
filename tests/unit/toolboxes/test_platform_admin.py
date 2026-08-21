"""The platform-admin toolbox: 5 read tools + a write tier (create/update space,
create-or-update role) + a destructive tier (delete space/role) over spaces +
roles + upgrade readiness. Driven through the in-memory fastmcp Client against a
FakeGateway. The unit fixture registers with no tier gating, so all 10 appear."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.platform_admin.toolbox import PlatformAdminToolbox
from tests.fakes import FakeGateway

PLATFORM_ADMIN_READ = {
    "list_spaces", "get_space", "list_roles", "get_role", "get_upgrade_status",
}
PLATFORM_ADMIN_WRITE = {"create_space", "update_space", "create_or_update_role"}
PLATFORM_ADMIN_DESTRUCTIVE = {"delete_space", "delete_role"}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    PlatformAdminToolbox().register(server, deps)
    return server


async def test_platform_admin_tools_are_tiered(mcp):
    # The unit fixture registers with no tier gating, so all 10 tools appear.
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == PLATFORM_ADMIN_READ | PLATFORM_ADMIN_WRITE | PLATFORM_ADMIN_DESTRUCTIVE
    for n in PLATFORM_ADMIN_READ:
        assert tools[n].annotations.readOnlyHint is True
    for n in PLATFORM_ADMIN_DESTRUCTIVE:
        assert tools[n].annotations.destructiveHint is True


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
