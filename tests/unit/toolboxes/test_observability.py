"""The observability toolbox: 10 read-only tools over synthetics + uptime +
apm-config. Driven through the in-memory fastmcp Client against a FakeGateway."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.models import ApmAnnotation
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.observability.toolbox import ObservabilityToolbox
from tests.fakes import FakeGateway

OBSERVABILITY_TOOLS = {
    "list_synthetic_monitors", "get_synthetic_monitor", "list_synthetic_params",
    "list_synthetic_private_locations", "get_uptime_settings",
    "list_apm_agent_configs", "get_apm_agent_config", "list_apm_environments",
    "list_apm_sourcemaps", "search_apm_annotations",
}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    ObservabilityToolbox().register(server, deps)
    return server


async def test_observability_is_read_only(mcp):
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == OBSERVABILITY_TOOLS
    for t in tools:
        assert t.annotations.readOnlyHint is True


async def test_list_synthetic_monitors_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_synthetic_monitors", {})
    assert result.data[0]["id"] == "mon-1"
    assert result.data[0]["schedule"] == "10m"
    assert result.data[0]["target"] == "https://example.com"


async def test_get_synthetic_monitor_found(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_synthetic_monitor", {"monitor_id": "mon-1"})
    assert result.data["name"] == "home"


async def test_get_synthetic_monitor_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_synthetic_monitor", {"monitor_id": "nope"})


async def test_list_apm_environments_surfaces_sentinel(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_apm_environments", {})
    assert result.data[0]["name"] == "ALL_OPTION_VALUE"


async def test_get_uptime_settings_serializes_nested_email(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_uptime_settings", {})
    assert result.data["heartbeat_indices"] == "heartbeat-*"
    assert result.data["default_email"] == {"to": [], "cc": [], "bcc": []}


async def test_search_apm_annotations_defaults_environment(mcp, gateway):
    gateway.apm_annotations = [
        ApmAnnotation(id="a1", timestamp="2026-07-12T00:00:00Z", text="v2 deploy", type="deployment")
    ]
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_apm_annotations",
            {"service_name": "checkout", "start": "2026-07-01T00:00:00Z", "end": "2026-07-02T00:00:00Z"},
        )
    assert result.data[0]["id"] == "a1"
    assert result.data[0]["type"] == "deployment"
