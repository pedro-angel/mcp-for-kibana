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
# publish the same tools. Every profile states its surface on the toolbox + tier
# axes, the ones that say it exactly (read-only-explorer: three toolboxes at read;
# dashboards-analyst: two at write, the default selection, D39); the list is the
# copy for mixing with other tools. So a tool added to those toolboxes at that
# tier joins the profile and fails this test until the list names it too (D41).
@pytest.mark.parametrize("profile", sorted(BUILTIN_LISTS))
async def test_builtin_list_equals_its_profile_surface(profile):
    env = profile_env(profile)
    assert "KIBANA_MCP_TOOLS" not in env  # else the list would be compared with itself
    assert await published(env) == set(BUILTIN_LISTS[profile])


def test_read_only_explorer_profile_is_three_toolboxes_at_read():
    env = profile_env("read-only-explorer")
    assert env["KIBANA_MCP_TOOLBOXES"] == "dashboards,platform-health,data-management"
    assert env["KIBANA_MCP_TIER"] == "read"
