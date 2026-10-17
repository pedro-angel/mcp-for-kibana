"""The server's MCP instructions follow what is published.

The dashboards-flow text is given only when the three tools it walks through
are all published; any other surface gets a generic text that names no tool.
The default selection keeps the exact text it always had.
"""

import pytest
from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from tests.fakes import FakeGateway

# The pre-change text, byte for byte: the default surface must keep it.
DASHBOARDS_TEXT = """Tools for working with Kibana dashboards and visualizations.
Typical flow: list_data_views -> describe_data_view (learn field names) ->
create_dashboard with one or more visualization specs. Field names are
case-sensitive; always verify them with describe_data_view first."""


async def _instructions(**over):
    mcp = build_server(Settings(**over), lambda space=None: FakeGateway())
    async with Client(mcp) as client:
        return client.instructions, {t.name for t in await client.list_tools()}


async def test_default_surface_keeps_the_dashboards_text_byte_for_byte():
    text, _ = await _instructions()
    assert text == DASHBOARDS_TEXT


async def test_dashboards_text_at_destructive_too():
    text, _ = await _instructions(tier=Tier.DESTRUCTIVE)
    assert text == DASHBOARDS_TEXT


@pytest.mark.parametrize(
    "over",
    [
        {"toolboxes": ["alerting"]},
        {"toolboxes": ["fleet"], "tier": Tier.DESTRUCTIVE},
        # read tier hides create_dashboard: the flow cannot be walked
        {"tier": Tier.READ},
        # dashboards without data-management: no describe_data_view
        {"toolboxes": ["dashboards"]},
        {"tools": ["list_data_views", "describe_data_view"]},
    ],
)
async def test_other_surfaces_get_text_naming_no_unpublished_tool(over):
    text, published = await _instructions(**over)
    assert text != DASHBOARDS_TEXT
    assert text.startswith("Tools for working with Kibana.")
    for name in ("list_data_views", "describe_data_view", "create_dashboard"):
        assert name not in text or name in published


async def test_allow_list_with_the_flow_gets_the_dashboards_text():
    text, _ = await _instructions(
        tools=["list_data_views", "describe_data_view", "create_dashboard"]
    )
    assert text == DASHBOARDS_TEXT


async def test_flow_listed_above_the_tier_is_not_described():
    text, published = await _instructions(
        tools=["list_data_views", "describe_data_view", "create_dashboard"], tier=Tier.READ
    )
    assert "create_dashboard" not in published
    assert text != DASHBOARDS_TEXT
