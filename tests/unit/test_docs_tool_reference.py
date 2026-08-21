"""Drift-guard: every registered tool must be documented in docs/tools.md.

The public storefront once advertised "10 tools / dashboards only" while the
server actually shipped 133 tools across 10 toolboxes. This test ties the tool
reference to the live registry so that gap cannot silently reopen: it builds the
server with every toolbox at the `destructive` tier (so all tools are visible),
then asserts each registered tool name appears on the reference page. Add a tool
without documenting it and this fails.
"""

import pathlib

from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests.fakes import FakeGateway

TOOLS_MD = pathlib.Path(__file__).resolve().parents[2] / "docs" / "tools.md"


async def _all_registered_tool_names() -> set[str]:
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"))
    mcp = build_server(settings, lambda space=None: FakeGateway())
    async with Client(mcp) as client:
        return {t.name for t in await client.list_tools()}


async def test_every_registered_tool_is_documented():
    text = TOOLS_MD.read_text()
    names = await _all_registered_tool_names()
    assert names, "no tools registered — build_server / registry regression"
    undocumented = sorted(n for n in names if f"`{n}`" not in text)
    assert not undocumented, (
        f"{len(undocumented)} registered tool(s) have no `name` entry in "
        f"docs/tools.md (document them there): {undocumented}"
    )
