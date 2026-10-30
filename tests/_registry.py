"""The live tool registry as plain data, for the docs drift tests (D57).

tests/unit/test_docs_counts.py and tests/unit/test_docs_tool_reference.py
compare what the prose says (counts, tier cells, annotation tables) with what
the server registers. Both read it from here: every toolbox at `destructive`,
built against a FakeGateway (registration does no I/O), so the figures are the
code's and never typed by hand.
"""

import asyncio
import functools
import json
import pathlib
from collections import Counter
from dataclasses import dataclass

from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import _tool_catalog, build_server
from kibana_mcp.toolboxes import TOOLBOXES
from kibana_mcp.toolboxes.base import ToolboxDeps
from tests.fakes import FakeGateway

TIERS = ("read", "write", "destructive")
PROFILES = pathlib.Path(__file__).resolve().parents[1] / "profiles"


@dataclass(frozen=True)
class Tool:
    name: str
    toolbox: str
    tier: str
    read_only: bool
    destructive: bool
    idempotent: bool
    space_aware: bool  # takes the shared optional `space` parameter


async def _listed():
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"))
    async with Client(build_server(settings, lambda space=None: FakeGateway())) as client:
        return await client.list_tools()


@functools.cache
def registry() -> dict[str, Tool]:
    """Every registered tool by name."""
    deps = ToolboxDeps(gateway_factory=lambda space=None: FakeGateway(), public_kibana_url="http://k")
    catalog = _tool_catalog(deps)
    tools = {}
    for t in asyncio.run(_listed()):
        box, tags = catalog[t.name]
        (tier,) = set(tags) & set(TIERS)
        hints = t.annotations
        tools[t.name] = Tool(
            name=t.name,
            toolbox=box,
            tier=tier,
            read_only=bool(hints and hints.read_only_hint),
            destructive=bool(hints and hints.destructive_hint),
            idempotent=bool(hints and hints.idempotent_hint),
            space_aware="space" in t.input_schema.get("properties", {}),
        )
    assert len(tools) == len(catalog), "build_server and the catalog disagree"
    return tools


@functools.cache
def profile_sizes() -> dict[str, int]:
    """Each shipped profile's published tool count: its toolboxes at its tier
    (no shipped profile sets KIBANA_MCP_TOOLS; test_profiles.py pins that)."""
    sizes = {}
    for path in sorted(PROFILES.glob("*.mcp.json")):
        env = json.loads(path.read_text())["mcpServers"]["mcp-for-kibana"]["env"]
        assert "KIBANA_MCP_TOOLS" not in env, path
        selection = {"toolboxes": env["KIBANA_MCP_TOOLBOXES"]} if "KIBANA_MCP_TOOLBOXES" in env else {}
        settings = Settings(**selection, tier=Tier(env["KIBANA_MCP_TIER"]))
        allowed = TIERS[: TIERS.index(env["KIBANA_MCP_TIER"]) + 1]
        sizes[path.name.removesuffix(".mcp.json")] = sum(
            1 for t in registry().values() if t.toolbox in settings.toolboxes and t.tier in allowed
        )
    return sizes


def tier_counts(tools) -> Counter:
    """Tools per tier, every tier present (0 when empty)."""
    counts = Counter({tier: 0 for tier in TIERS})
    counts.update(t.tier for t in tools)
    return counts
