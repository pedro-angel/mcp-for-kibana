"""The fleet toolbox: 41 tools over Fleet (22 read + 9 write + 10 destructive).
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
    "list_agent_tags", "list_agent_diagnostics",
}
WRITE_NAMES = {
    "create_agent_policy", "update_agent_policy",
    "create_package_policy", "update_package_policy",
    "create_output", "update_output",
    "install_package", "update_agent_tags", "request_agent_diagnostics",
}
DESTRUCTIVE_NAMES = {
    "delete_agent_policy", "delete_package_policy", "delete_output", "uninstall_package",
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
    # The unit fixture registers with no tier gating, so all 41 tools appear
    # (tier hiding is proven at the server-assembly level).
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == FLEET_READ | WRITE_NAMES | DESTRUCTIVE_NAMES
    assert len(tools) == 41
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


# --- fleet advanced ops (D51) ---


async def test_d51_annotations(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    # A repeat leaves the same state: same tags, already installed.
    for n in ("update_agent_tags", "install_package"):
        assert tools[n].annotations.idempotentHint is True
    # Each request makes a new bundle.
    assert tools["request_agent_diagnostics"].annotations.idempotentHint is False
    assert tools["uninstall_package"].annotations.destructiveHint is True
    for n in ("list_agent_tags", "list_agent_diagnostics"):
        assert tools[n].annotations.readOnlyHint is True


async def test_update_agent_tags_round_trip(mcp, gateway):
    async with Client(mcp) as client:
        r = await client.call_tool("update_agent_tags", {
            "agent_ids": ["agent-demo", "agent-fs"], "tags_to_add": ["prod", "prod"]})
        assert r.data["agents"] == [
            {"agent_id": "agent-demo", "previous_tags": [], "tags": ["prod"]},
            {"agent_id": "agent-fs", "previous_tags": [], "tags": ["prod"]},
        ]
        assert r.data["action_id"] and r.data["not_applied_agent_ids"] == []
        assert (await client.call_tool("list_agent_tags", {})).data == ["prod"]
        undo = await client.call_tool("update_agent_tags", {
            "agent_ids": ["agent-demo", "agent-fs"], "tags_to_remove": ["prod"]})
    assert [a["tags"] for a in undo.data["agents"]] == [[], []]
    assert gateway.list_agent_tags() == []


async def test_update_agent_tags_reports_not_applied(mcp, gateway):
    gateway.fleet_tag_refused_agent_ids = {"agent-fs"}
    async with Client(mcp) as client:
        r = await client.call_tool("update_agent_tags", {
            "agent_ids": ["agent-demo", "agent-fs"], "tags_to_add": ["x"]})
    assert r.data["not_applied_agent_ids"] == ["agent-fs"]


@pytest.mark.parametrize(("args", "message"), [
    ({"agent_ids": ["agent-demo"]}, "nothing to change: pass tags_to_add and/or tags_to_remove"),
    ({"agent_ids": ["agent-demo"], "tags_to_add": [], "tags_to_remove": []}, "nothing to change"),
    ({"agent_ids": ["agent-demo"], "tags_to_add": ["a", "b"], "tags_to_remove": ["b"]},
     r"tag\(s\) \['b'\] are in both tags_to_add and tags_to_remove"),
])
async def test_update_agent_tags_refuses_before_the_gateway(mcp, gateway, args, message):
    gateway.update_agent_tags = None  # acquired only after the checks
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool("update_agent_tags", args)


@pytest.mark.parametrize("agent_ids", [[], [f"a{i}" for i in range(101)]])
async def test_update_agent_tags_bounds_the_id_list(mcp, agent_ids):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_agent_tags", {"agent_ids": agent_ids, "tags_to_add": ["x"]})


async def test_update_agent_tags_unknown_id_changes_nothing(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"^no agent has agent id 'nope' .*list_agents"):
            await client.call_tool("update_agent_tags", {
                "agent_ids": ["agent-demo", "nope"], "tags_to_add": ["x"]})
    assert gateway.list_agent_tags() == []


async def test_request_then_list_agent_diagnostics(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("request_agent_diagnostics", {"agent_id": "agent-demo"})
        assert r.data["agent_id"] == "agent-demo" and r.data["agent_status"] == "online"
        bundles = (await client.call_tool("list_agent_diagnostics", {"agent_id": "agent-demo"})).data
    assert [(b["action_id"], b["status"]) for b in bundles] == [(r.data["action_id"], "READY")]
    assert set(bundles[0]) == {"id", "name", "status", "created_at", "action_id"}


@pytest.mark.parametrize("tool", ["request_agent_diagnostics", "list_agent_diagnostics"])
async def test_diagnostics_unknown_agent_names_list_agents(mcp, tool):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"^agent 'nope' not found — list_agents shows"):
            await client.call_tool(tool, {"agent_id": "nope"})


async def test_install_then_uninstall_package(mcp, gateway):
    async with Client(mcp) as client:
        r = await client.call_tool("install_package", {"name": "nginx"})
        assert r.data == {"name": "nginx", "version": "1.0.0", "status": "installed",
                          "already_installed": False}
        again = await client.call_tool("install_package", {"name": "nginx"})
        assert again.data["already_installed"] is True
        gone = await client.call_tool("uninstall_package", {"name": "nginx"})
    assert gone.data == {"uninstalled": True, "name": "nginx", "version": "1.0.0",
                         "deleted_package_policies": []}
    assert "nginx" not in {p.name for p in gateway.list_installed_packages()}


async def test_install_package_refuses_a_version_change(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=(
            r"^integration package 'system' is installed at version 1\.0\.0, not 2\.0\.0")):
            await client.call_tool("install_package", {"name": "system", "version": "2.0.0"})


async def test_uninstall_package_with_package_policies_needs_force(mcp, gateway):
    gateway.create_package_policy(
        name="sys-1", package={"name": "system", "version": "1.0.0"}, agent_policy_id="fleet-agent-policy")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=(
            r"^refusing to uninstall integration package 'system': it has 1 package policy — "
            r"'fpp-\d+' \('sys-1'\) on agent policy 'fleet-agent-policy' \(1 agent\)")):
            await client.call_tool("uninstall_package", {"name": "system"})
        assert any(pp.package_name == "system" for pp in gateway.list_package_policies())
        r = await client.call_tool("uninstall_package", {"name": "system", "force": True})
    assert [pp["name"] for pp in r.data["deleted_package_policies"]] == ["sys-1"]
    assert not any(pp.package_name == "system" for pp in gateway.list_package_policies())


async def test_uninstall_package_refuses_the_fleet_server_policy_even_forced(mcp, gateway):
    gateway.fleet_packages.append(gateway.fleet_installed_packages[0].__class__(
        name="fleet_server", title="Fleet Server", version="1.6.1", status="installed",
        description="", type="integration"))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"the default Fleet Server policy, .*regardless of force"):
            await client.call_tool("uninstall_package", {"name": "fleet_server", "force": True})
    assert any(pp.package_name == "fleet_server" for pp in gateway.list_package_policies())


async def test_uninstall_package_not_installed(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"^integration package 'nginx' is not installed"):
            await client.call_tool("uninstall_package", {"name": "nginx"})
