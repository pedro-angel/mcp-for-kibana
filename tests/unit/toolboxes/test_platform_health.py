"""The platform-health toolbox: 3 read-only tools that return concise health
summaries (never the raw metric trees)."""

import pytest
from fastmcp import Client, FastMCP

from kibana_mcp.core.models import KibanaStatus, ServiceHealth
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.platform_health.toolbox import PlatformHealthToolbox
from tests.fakes import FakeGateway


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    PlatformHealthToolbox().register(server, deps)
    return server


async def test_get_kibana_status_returns_summary(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("get_kibana_status", {})
    assert result.data["overall_level"] == "available"
    assert result.data["version"] == "9.4.3"
    assert result.data["unhealthy"] == []  # healthy fake -> nothing surfaced


async def test_get_kibana_status_surfaces_unhealthy(mcp, gateway):
    gateway.kibana_status = KibanaStatus(
        overall_level="degraded",
        overall_summary="1 service degraded",
        version="9.4.3",
        unhealthy=(ServiceHealth(name="reporting", level="unavailable", summary="down"),),
    )
    async with Client(mcp) as client:
        result = await client.call_tool("get_kibana_status", {})
    assert result.data["overall_level"] == "degraded"
    assert result.data["unhealthy"] == [
        {"name": "reporting", "level": "unavailable", "summary": "down"}
    ]


async def test_get_kibana_stats_returns_runtime(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("get_kibana_stats", {})
    assert result.data["heap_used_bytes"] == 100
    assert result.data["concurrent_connections"] == 2


async def test_get_task_manager_health_returns_status(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("get_task_manager_health", {})
    assert result.data["status"] == "OK"


async def test_platform_health_is_read_only(mcp):
    async with Client(mcp) as client:
        tools = {t.name for t in await client.list_tools()}
    assert tools == {"get_kibana_status", "get_kibana_stats", "get_task_manager_health"}
    async with Client(mcp) as client:
        for t in await client.list_tools():
            assert t.annotations.readOnlyHint is True
