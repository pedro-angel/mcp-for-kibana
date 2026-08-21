"""The fleet toolbox: 35 tools over Fleet (20 read + 6 write + 9 destructive).
Driven through the in-memory fastmcp Client against a FakeGateway. The
enrollment-key/uninstall-token tests assert the tool surface exposes only
allowlisted (non-secret) fields; the real mapper-level redaction proof (raw
secret-bearing bodies) lives in tests/unit/adapters/test_fleet_gateway.py."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.fleet.toolbox import FleetToolbox
from tests.fakes import FakeGateway

FLEET_READ = {
    "get_fleet_settings", "check_fleet_permissions",
    "list_agents", "get_agent", "get_agent_status_summary", "list_agent_versions",
    "list_agent_policies", "get_agent_policy", "list_package_policies", "get_package_policy",
    "list_enrollment_keys", "get_enrollment_key", "list_uninstall_tokens",
    "list_packages", "list_installed_packages", "get_package", "list_package_categories",
    "list_outputs", "get_output_health", "list_fleet_server_hosts",
}
WRITE_NAMES = {
    "create_agent_policy", "update_agent_policy",
    "create_package_policy", "update_package_policy",
    "create_output", "update_output",
}
DESTRUCTIVE_NAMES = {
    "delete_agent_policy", "delete_package_policy", "delete_output",
    "reassign_agent", "upgrade_agent", "unenroll_agent",
    "bulk_reassign", "bulk_upgrade", "bulk_unenroll",
}


def _extra(tool):
    # Extra required args (beyond agent_ids/confirm) each bulk tool needs to
    # reach the empty/missing-confirm guard.
    return {
        "bulk_reassign": {"policy_id": "fleet-agent-policy"},
        "bulk_upgrade": {"version": "8.15.0"},
        "bulk_unenroll": {},
    }[tool]


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    FleetToolbox().register(server, deps)
    return server


async def test_fleet_tools_are_tiered(mcp):
    # The unit fixture registers with no tier gating, so all 35 tools appear
    # (tier hiding is proven at the server-assembly level).
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == FLEET_READ | WRITE_NAMES | DESTRUCTIVE_NAMES
    assert len(tools) == 35
    for n in FLEET_READ:
        assert tools[n].annotations.readOnlyHint is True


async def test_fleet_write_destructive_annotations(mcp):
    # Annotations match tier BOTH directions: the destructiveHint invariant an
    # MCP host keys auto-approval on.
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for n in DESTRUCTIVE_NAMES:
        assert tools[n].annotations.destructiveHint is True
    for n in WRITE_NAMES:
        assert tools[n].annotations.destructiveHint is not True


@pytest.mark.parametrize("tool", ["bulk_reassign", "bulk_upgrade", "bulk_unenroll"])
async def test_bulk_tools_reject_empty_and_missing_confirm(mcp, tool):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(tool, {"agent_ids": [], "confirm": True, **_extra(tool)})
        with pytest.raises(ToolError):
            await client.call_tool(tool, {"agent_ids": ["a"], "confirm": False, **_extra(tool)})


async def test_update_output_forwards_confirm(mcp, gateway):
    # update_output exposes confirm as a separate tool arg, excluded from
    # `changes`, and forwarded verbatim to the gateway.
    calls = []
    original = gateway.update_output

    def _spy(*, output_id, changes, confirm=False):
        calls.append(confirm)
        return original(output_id=output_id, changes=changes, confirm=confirm)

    gateway.update_output = _spy
    async with Client(mcp) as client:
        await client.call_tool(
            "update_output", {"output_id": "fleet-default-output", "name": "renamed", "confirm": True}
        )
    assert calls == [True]


async def test_update_package_policy_forwards_real_fields(mcp, gateway):
    # agent_policy_id/name/enabled must reach the gateway's `changes` dict
    # verbatim — regression guard for the phantom-params finding (#81), where
    # package_name/package_title/package_version/agent_policy_id used to be
    # silently dropped by the adapter's _rmw_body allowlist.
    calls = []
    original = gateway.update_package_policy

    def _spy(*, package_policy_id, changes):
        calls.append(changes)
        return original(package_policy_id=package_policy_id, changes=changes)

    gateway.update_package_policy = _spy
    async with Client(mcp) as client:
        await client.call_tool(
            "update_package_policy",
            {"package_policy_id": gateway.fleet_package_policies[0].id,
             "agent_policy_id": "fap-new", "name": "renamed", "enabled": False},
        )
    assert calls == [{"agent_policy_id": "fap-new", "name": "renamed", "enabled": False}]


async def test_list_agents_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("list_agents", {})
    assert len(r.data) == 2
    assert {a["policy_id"] for a in r.data} == {"fleet-server-policy", "fleet-agent-policy"}


async def test_get_agent_missing_raises(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_agent", {"agent_id": "does-not-exist"})


async def test_get_agent_status_summary_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("get_agent_status_summary", {})
    assert r.data["online"] == 2 and r.data["total"] == 2


async def test_list_enrollment_keys_tool_exposes_only_allowlisted_fields(mcp):
    # Allowlist/shape guard at the tool boundary (the mapper-level redaction of a
    # raw api_key is proven in test_fleet_gateway.py::test_enrollment_key_redacts_api_key).
    async with Client(mcp) as client:
        r = await client.call_tool("list_enrollment_keys", {})
    assert r.data, "expected at least one enrollment key"
    for k in r.data:
        assert "api_key" not in k and "api_key_id" not in k
        assert set(k) == {"id", "name", "policy_id", "active", "created_at"}


async def test_list_uninstall_tokens_tool_is_metadata_only(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("list_uninstall_tokens", {})
    for t in r.data:
        assert "token" not in t and set(t) == {"id", "policy_id", "policy_name", "created_at"}


async def test_get_fleet_settings_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("get_fleet_settings", {})
    assert r.data["id"] == "fleet-default-settings"
    assert r.data["integration_knowledge_enabled"] is True


async def test_list_installed_packages_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("list_installed_packages", {})
    assert [p["name"] for p in r.data] == ["system"]


async def test_get_package_missing_raises(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_package", {"name": "nonexistent-pkg"})
