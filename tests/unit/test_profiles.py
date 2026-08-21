"""Guard against profile tool-count drift: each shipped profile's live visible
tool count must match the count advertised in profiles/README.md. The README is
parsed (not hardcoded), so it is the single source of truth."""

import json
import pathlib
import re

import pytest
from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from tests.fakes import FakeGateway

PROFILES = pathlib.Path(__file__).resolve().parents[2] / "profiles"

# Parse the "[`<name>.mcp.json`](...) — N tools" headers out of README.
_HEADER = re.compile(r"\[`(?P<name>[\w-]+)\.mcp\.json`\]\([^)]+\)\s*—\s*(?P<n>\d+)\s+tools")
README_COUNTS = {
    m.group("name"): int(m.group("n"))
    for m in _HEADER.finditer((PROFILES / "README.md").read_text())
}


async def _visible_count(env):
    settings = Settings(toolboxes=env["KIBANA_MCP_TOOLBOXES"], tier=Tier(env["KIBANA_MCP_TIER"]))
    mcp = build_server(settings, lambda space=None: FakeGateway())
    async with Client(mcp) as client:
        return len(await client.list_tools())


def test_readme_advertises_live_profiles():
    # If this fails, the header-parsing regex or the README format drifted.
    assert set(README_COUNTS) == {"read-only-explorer", "dashboards-analyst", "fleet-admin"}


@pytest.mark.parametrize("profile", sorted(README_COUNTS))
async def test_shipped_profile_tool_count_matches_readme(profile):
    cfg = json.loads((PROFILES / f"{profile}.mcp.json").read_text())
    env = cfg["mcpServers"]["mcp-for-kibana"]["env"]
    assert await _visible_count(env) == README_COUNTS[profile]
