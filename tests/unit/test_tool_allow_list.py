"""KIBANA_MCP_TOOLS: the per-tool allow list (D34).

Published tools = listed ∩ tier, nothing else; the list alone decides which
toolboxes register; a listed tool above the tier stays hidden (logged); an
unknown name fails startup; unset/empty is byte-identical to before.
"""

import logging

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from tests.fakes import FakeGateway

# The owner's small-local-model example (docs/configuration.md).
EXAMPLE = [
    "list_data_views", "describe_data_view", "search_dashboards", "get_dashboard",
    "create_dashboard", "add_panel", "update_panel", "create_data_view",
]
EXAMPLE_READ = {"list_data_views", "describe_data_view", "search_dashboards", "get_dashboard"}


def _build(**over):
    return build_server(Settings(**over), lambda space=None: FakeGateway())


async def _visible(**over):
    async with Client(_build(**over)) as client:
        return {t.name for t in await client.list_tools()}


async def test_example_list_at_write_publishes_exactly_the_eight():
    names = await _visible(tools=EXAMPLE, tier=Tier.WRITE)
    assert names == set(EXAMPLE)
    assert len(names) == 8


async def test_example_list_at_read_publishes_only_the_four_reads():
    assert await _visible(tools=EXAMPLE, tier=Tier.READ) == EXAMPLE_READ


async def test_listed_destructive_tool_stays_hidden_at_write(caplog):
    # The safety pin: the allow list can only hide. A destructive tool on the
    # list must not be re-published past the tier cap.
    with caplog.at_level(logging.WARNING, logger="kibana_mcp.server"):
        names = await _visible(tools=[*EXAMPLE, "delete_dashboard"], tier=Tier.WRITE)
    assert "delete_dashboard" not in names
    assert names == set(EXAMPLE)
    lines = [r.getMessage() for r in caplog.records if "KIBANA_MCP_TIER" in r.getMessage()]
    assert len(lines) == 1
    assert "delete_dashboard (destructive)" in lines[0]
    assert "KIBANA_MCP_TIER=write" in lines[0]


async def test_listed_destructive_tool_is_published_at_destructive():
    names = await _visible(tools=["get_dashboard", "delete_dashboard"], tier=Tier.DESTRUCTIVE)
    assert names == {"get_dashboard", "delete_dashboard"}


async def test_above_tier_log_names_every_hidden_tool(caplog):
    with caplog.at_level(logging.WARNING, logger="kibana_mcp.server"):
        await _visible(tools=EXAMPLE, tier=Tier.READ)
    (line,) = [r.getMessage() for r in caplog.records if "KIBANA_MCP_TIER" in r.getMessage()]
    for name in set(EXAMPLE) - EXAMPLE_READ:
        assert f"{name} (write)" in line


def test_allow_list_never_calls_enable(monkeypatch):
    # enable() after disable() re-enables (FastMCP: later transforms win), so an
    # enable-based allow list could undo the tier. The mechanism is disable-only.
    def _refuse(self, *args, **kwargs):
        raise AssertionError("build_server must never call FastMCP.enable")

    monkeypatch.setattr(FastMCP, "enable", _refuse)
    _build(tools=[*EXAMPLE, "delete_dashboard"], tier=Tier.WRITE)


async def test_list_alone_decides_the_toolboxes():
    # fleet and platform-health are not in the default toolboxes; the list
    # registers whatever owns the listed tools.
    names = await _visible(tools=["list_agents", "get_kibana_status"])
    assert names == {"list_agents", "get_kibana_status"}


async def test_explicit_toolboxes_are_ignored_with_one_log_line(caplog):
    with caplog.at_level(logging.WARNING, logger="kibana_mcp.server"):
        names = await _visible(tools=["get_dashboard"], toolboxes=["fleet", "no-such-toolbox"])
    assert names == {"get_dashboard"}
    lines = [r.getMessage() for r in caplog.records if "KIBANA_MCP_TOOLBOXES" in r.getMessage()]
    assert len(lines) == 1
    assert "ignored" in lines[0] and "KIBANA_MCP_TOOLS" in lines[0]


async def test_default_toolboxes_log_nothing(caplog):
    # A coherent list (get_dashboard's ids come from search_dashboards, D41), so
    # no coherence line either: unset toolboxes add no line of their own.
    with caplog.at_level(logging.WARNING, logger="kibana_mcp.server"):
        await _visible(tools=["search_dashboards", "get_dashboard"])
    assert not caplog.records


def test_unknown_names_fail_startup_with_a_suggestion():
    with pytest.raises(ValueError) as err:
        _build(tools=["list_data_view", "get_dashboard", "zzzz_nothing_like_it"])
    text = str(err.value)
    assert "'list_data_view' (did you mean 'list_data_views'?)" in text
    assert "'zzzz_nothing_like_it'" in text  # all unknown names at once
    assert "get_dashboard" not in text
    assert "docs/tools.md" in text


async def test_unlisted_tool_cannot_be_called():
    async with Client(_build(tools=["get_dashboard"])) as client:
        with pytest.raises(ToolError):
            await client.call_tool("search_dashboards", {})


async def test_docs_resources_still_served():
    async with Client(_build(tools=["get_dashboard"])) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
    assert uris == {"docs://user-guide", "docs://tools", "docs://troubleshooting"}


async def _surface(mcp):
    async with Client(mcp) as client:
        tools = [t.model_dump() for t in await client.list_tools()]
        resources = [r.model_dump() for r in await client.list_resources()]
    return tools, resources


@pytest.mark.parametrize("tier", list(Tier))
async def test_empty_list_is_byte_identical_to_unset(tier):
    unset = await _surface(_build(tier=tier))
    empty = await _surface(build_server(Settings(tools=[], tier=tier), lambda space=None: None))
    assert empty == unset
