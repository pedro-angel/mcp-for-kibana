import pytest
from fastmcp import Client, FastMCP

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from tests.fakes import FakeGateway


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    DashboardsToolbox().register(server, deps)
    return server


async def test_read_tools_registered_with_annotations(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        # data-view tools moved to the data-management toolbox (#28).
        assert "list_data_views" not in tools
        assert "describe_data_view" not in tools
        for name in ("search_dashboards", "get_dashboard"):
            assert name in tools
            assert tools[name].annotations.readOnlyHint is True
            assert tools[name].annotations.openWorldHint is False


async def test_get_dashboard_not_found_is_actionable(mcp):
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="not found"):
            await client.call_tool("get_dashboard", {"dashboard_id": "missing"})


async def test_search_dashboards_empty(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("search_dashboards", {})
        assert result.data == []
