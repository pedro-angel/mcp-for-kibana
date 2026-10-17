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
from kibana_mcp.tool_lists import BUILTIN_LISTS
from tests.fakes import FakeGateway

PROFILES = pathlib.Path(__file__).resolve().parents[2] / "profiles"

# Parse the "[`<name>.mcp.json`](...) — N tools" headers out of README.
_HEADER = re.compile(r"\[`(?P<name>[\w-]+)\.mcp\.json`\]\([^)]+\)\s*—\s*(?P<n>\d+)\s+tools")
README_COUNTS = {
    m.group("name"): int(m.group("n"))
    for m in _HEADER.finditer((PROFILES / "README.md").read_text())
}


# The selection settings a profile may carry, by env var.
_FIELDS = {"KIBANA_MCP_TOOLBOXES": "toolboxes", "KIBANA_MCP_TOOLS": "tools"}


async def published(env):
    """The tool names a profile's env publishes."""
    selection = {field: env[var] for var, field in _FIELDS.items() if var in env}
    settings = Settings(**selection, tier=Tier(env["KIBANA_MCP_TIER"]))
    mcp = build_server(settings, lambda space=None: FakeGateway())
    async with Client(mcp) as client:
        return {t.name for t in await client.list_tools()}


def profile_env(profile):
    cfg = json.loads((PROFILES / f"{profile}.mcp.json").read_text())
    return cfg["mcpServers"]["mcp-for-kibana"]["env"]


def test_readme_advertises_live_profiles():
    # If this fails, the header-parsing regex or the README format drifted.
    assert set(README_COUNTS) == {"read-only-explorer", "dashboards-analyst", "fleet-admin"}


@pytest.mark.parametrize("profile", sorted(README_COUNTS))
async def test_shipped_profile_tool_count_matches_readme(profile):
    assert len(await published(profile_env(profile))) == README_COUNTS[profile]


# Each persona has one surface: a profile and the built-in list of the same name
# publish the same tools. read-only-explorer's profile references its list
# (KIBANA_MCP_TOOLS=@read-only-explorer); dashboards-analyst's profile stays on
# toolbox + tier because it is the default selection (D39) and raising its tier
# to destructive is how it gains the delete tools, so its list must keep equal
# to that surface at write. fleet-admin is the whole fleet toolbox, not a list.
@pytest.mark.parametrize("profile", sorted(BUILTIN_LISTS))
async def test_builtin_list_equals_its_profile_surface(profile):
    assert await published(profile_env(profile)) == set(BUILTIN_LISTS[profile])


def test_read_only_explorer_profile_references_its_list():
    env = profile_env("read-only-explorer")
    assert env["KIBANA_MCP_TOOLS"] == "@read-only-explorer"
    assert "KIBANA_MCP_TOOLBOXES" not in env  # it would be ignored, with a log line
