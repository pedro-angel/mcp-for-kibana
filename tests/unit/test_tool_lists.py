"""Named tool lists and coherence warnings (D41).

`KIBANA_MCP_TOOLS` entries starting with `@` stand for a named list: a
built-in one (kibana_mcp.tool_lists.BUILTIN_LISTS) or one from the operator's
TOML file (KIBANA_MCP_TOOL_LISTS_FILE). Expansion happens before validation,
so the published set is still listed ∩ tier (D34). When the list is set, one
stderr line names every tool the published ones rely on that it left out.
"""

import logging
import re
from pathlib import Path

import pytest
from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import _tool_catalog, build_server, load_tool_lists
from kibana_mcp.tool_lists import (
    BUILTIN_LISTS,
    DEPENDENCIES,
    ToolListError,
    available_lists,
    expand,
    load_list_file,
    missing_dependencies,
)
from kibana_mcp.toolboxes.base import ToolboxDeps
from tests.fakes import FakeGateway

_TIER_RANK = {"read": 0, "write": 1, "destructive": 2}
_LOGGER = "kibana_mcp.server"


@pytest.fixture(scope="module")
def catalog():
    deps = ToolboxDeps(gateway_factory=lambda space=None: FakeGateway(), public_kibana_url="http://k")
    return _tool_catalog(deps)


def _tier(catalog, name):
    (tier,) = catalog[name][1] & set(_TIER_RANK)
    return tier


def _build(**over):
    # As main() does: the list file is loaded outside build_server, which reads no file.
    settings = Settings(**over)
    return build_server(
        settings, lambda space=None: FakeGateway(), tool_lists=load_tool_lists(settings)
    )


async def _visible(**over):
    async with Client(_build(**over)) as client:
        return [t.name for t in await client.list_tools()]


def _lines(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


def _write(tmp_path, text, name="lists.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


MINE = """version = 1

[lists.alert-triage]
tools = ["list_alert_rules", "get_alert_rule", "update_alert_rule"]
"""


# --- the data: built-in lists and the dependency map name real tools ---------


@pytest.mark.parametrize("name", sorted(BUILTIN_LISTS))
def test_builtin_list_names_only_real_tools(catalog, name):
    tools = BUILTIN_LISTS[name]
    assert tools, name
    assert len(set(tools)) == len(tools), f"{name} repeats a tool"
    assert [t for t in tools if t not in catalog] == []


@pytest.mark.parametrize("name", sorted(BUILTIN_LISTS))
def test_builtin_list_is_coherent(name):
    # A shipped list never triggers its own coherence warning.
    assert missing_dependencies(BUILTIN_LISTS[name]) == {}


def test_read_only_explorer_is_read_tier_only(catalog):
    assert {_tier(catalog, t) for t in BUILTIN_LISTS["read-only-explorer"]} == {"read"}


def test_dependency_map_names_only_real_tools(catalog):
    named = set(DEPENDENCIES) | {t for needed in DEPENDENCIES.values() for t in needed}
    assert sorted(named - catalog.keys()) == []


def test_dependencies_point_at_the_same_or_a_lower_tier(catalog):
    # So a missing dependency of a published tool is always publishable by
    # listing it: the warning's fix ("add them") is never blocked by the tier.
    for tool, needed in DEPENDENCIES.items():
        assert tool not in needed
        for other in needed:
            assert _TIER_RANK[_tier(catalog, other)] <= _TIER_RANK[_tier(catalog, tool)], (
                tool, other,
            )


# --- expansion ---------------------------------------------------------------


def test_expand_keeps_order_and_marks_origins():
    lists = {"a": ("x", "y"), "b": ("y", "z")}
    assert expand(["w", "@a", "@b", "x"], lists) == {"w": None, "x": "a", "y": "a", "z": "b"}


def test_plain_names_expand_to_themselves():
    assert expand(["get_dashboard", "list_data_views"], BUILTIN_LISTS) == {
        "get_dashboard": None, "list_data_views": None,
    }


def test_unknown_list_names_fail_at_once_with_suggestions():
    with pytest.raises(ToolListError) as err:
        expand(["@dashboard-analyst", "get_dashboard", "@zzz", "@"], BUILTIN_LISTS)
    text = str(err.value)
    assert "'@dashboard-analyst' (did you mean '@dashboards-analyst'?)" in text
    assert "'@zzz'" in text and "'@'" in text
    assert "available lists: dashboards-analyst, read-only-explorer" in text
    assert "KIBANA_MCP_TOOL_LISTS_FILE" in text


def test_missing_dependencies_follow_edges_transitively():
    assert missing_dependencies(["update_alert_rule"]) == {
        "get_alert_rule": ["update_alert_rule"],
        "list_alert_rules": ["get_alert_rule"],
    }
    assert missing_dependencies(["update_alert_rule", "get_alert_rule", "list_alert_rules"]) == {}


# --- the operator's file -----------------------------------------------------


def test_file_lists_load_and_join_the_builtins(tmp_path):
    path = _write(tmp_path, MINE)
    assert load_list_file(path) == {
        "alert-triage": ("list_alert_rules", "get_alert_rule", "update_alert_rule"),
    }
    lists = available_lists(path)
    assert set(lists) == {*BUILTIN_LISTS, "alert-triage"}


def test_no_file_means_builtins_only():
    assert available_lists(None) == dict(BUILTIN_LISTS)
    assert available_lists("") == dict(BUILTIN_LISTS)


def test_file_trims_and_collapses_repeated_tools(tmp_path):
    path = _write(tmp_path, 'version = 1\n[lists.x]\ntools = [" a ", "b", "a"]\n')
    assert load_list_file(path) == {"x": ("a", "b")}


def test_file_with_a_bom_is_read(tmp_path):
    path = tmp_path / "bom.toml"
    path.write_bytes("﻿".encode() + MINE.encode())
    assert "alert-triage" in load_list_file(str(path))


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("version = 1\nlists = {}\n", "defines no lists"),
        ('[lists.x]\ntools = ["a"]\n', "add `version = 1`"),
        ('version = 2\n[lists.x]\ntools = ["a"]\n', "version 2 is not supported"),
        ('version = "1"\n[lists.x]\ntools = ["a"]\n', "version '1' is not supported"),
        ('version = true\n[lists.x]\ntools = ["a"]\n', "version True is not supported"),
        ('version = 1\nname = "mine"\n[lists.x]\ntools = ["a"]\n', "unknown top-level key(s) ['name']"),
        ("version = 1\n", "defines no lists"),
        ('version = 1\nlists = ["a"]\n', "defines no lists"),
        ('version = 1\n[lists."Has Space"]\ntools = ["a"]\n', "list name 'Has Space' is not allowed"),
        ('version = 1\n[lists.dashboards-analyst]\ntools = ["a"]\n', "has the name of a built-in list"),
        ("version = 1\n[lists]\nx = 3\n", "list 'x' must be a table"),
        ('version = 1\n[lists.x]\ntools = ["a"]\nnote = "n"\n', "unknown key(s) ['note']"),
        ("version = 1\n[lists.x]\n", "needs a non-empty `tools"),
        ("version = 1\n[lists.x]\ntools = []\n", "needs a non-empty `tools"),
        ('version = 1\n[lists.x]\ntools = "a,b"\n', "needs a non-empty `tools"),
        ('version = 1\n[lists.x]\ntools = ["a", 3]\n', "tool entries that are not names: [3]"),
        ('version = 1\n[lists.x]\ntools = ["a", " "]\n', "tool entries that are not names"),
        ('version = 1\n[lists.x]\ntools = ["@read-only-explorer"]\n', "references other lists"),
        ("version = 1\n[lists.x\n", "is not valid TOML"),
        # `$` would accept a trailing newline; the name grammar is matched in full
        ('version = 1\n[lists."ok\\n"]\ntools = ["a"]\n', "list name 'ok\\n' is not allowed"),
    ],
)
def test_file_problems_fail_naming_the_fix(tmp_path, text, needle):
    path = _write(tmp_path, text)
    with pytest.raises(ToolListError) as err:
        load_list_file(path)
    assert needle in str(err.value)
    assert str(err.value).startswith(f"KIBANA_MCP_TOOL_LISTS_FILE={path!r}: ")


def test_unreadable_file_fails_asking_for_a_readable_absolute_path(tmp_path):
    missing = str(tmp_path / "nope.toml")
    with pytest.raises(ToolListError) as err:
        load_list_file(missing)
    text = str(err.value)
    assert "could not be read: No such file or directory" in text
    assert "set KIBANA_MCP_TOOL_LISTS_FILE to the absolute path of a readable file" in text
    assert "working directory" not in text  # absolute already: no cwd caveat


def test_relative_path_error_says_what_it_resolved_against(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolListError) as err:
        load_list_file("lists.toml")
    text = str(err.value)
    assert f"resolved against the server's working directory {str(tmp_path)!r}" in text
    assert "which the MCP client chooses" in text
    assert "absolute path of a readable file" in text


def test_home_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path, MINE)
    assert "alert-triage" in load_list_file("~/lists.toml")


def test_non_utf8_file_fails_asking_for_utf8(tmp_path):
    path = tmp_path / "latin1.toml"
    path.write_bytes(b"version = 1\n# caf\xe9\n")
    with pytest.raises(ToolListError, match=r"is not UTF-8 text \(.* at byte 17\); save it as UTF-8"):
        load_list_file(str(path))


# --- the server ----------------------------------------------------------------


async def test_builtin_list_publishes_its_tools():
    names = await _visible(tools=["@read-only-explorer"], tier=Tier.READ)
    assert set(names) == set(BUILTIN_LISTS["read-only-explorer"])


async def test_list_mixes_with_plain_names_and_registers_their_toolboxes():
    names = await _visible(tools=["@dashboards-analyst", "create_alert_rule"])
    assert set(names) == {*BUILTIN_LISTS["dashboards-analyst"], "create_alert_rule"}


async def test_list_is_still_capped_by_the_tier(caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        names = await _visible(tools=["@dashboards-analyst"], tier=Tier.READ)
    assert set(names) == set(BUILTIN_LISTS["read-only-explorer"]) - {
        "get_kibana_status", "get_kibana_stats", "get_task_manager_health",
    }
    (line,) = _lines(caplog, "KIBANA_MCP_TIER")
    assert "create_dashboard (write)" in line


async def test_operator_list_from_the_file(tmp_path):
    path = _write(tmp_path, MINE)
    names = await _visible(tools=["@alert-triage", "get_kibana_status"], tool_lists_file=path)
    assert set(names) == {
        "list_alert_rules", "get_alert_rule", "update_alert_rule", "get_kibana_status",
    }


def test_unknown_list_fails_startup():
    with pytest.raises(ValueError, match=r"'@read-only' \(did you mean '@read-only-explorer'\?\)"):
        _build(tools=["@read-only"])


def test_unknown_tool_in_a_file_list_names_the_list_and_the_file(tmp_path):
    path = _write(tmp_path, 'version = 1\n[lists.mine]\ntools = ["get_dashbord"]\n')
    with pytest.raises(ValueError) as err:
        _build(tools=["@mine", "list_data_view"], tool_lists_file=path)
    text = str(err.value)
    assert "'get_dashbord' (did you mean 'get_dashboard'?) from list '@mine'" in text
    assert "'list_data_view' (did you mean 'list_data_views'?);" in text  # plain: no origin
    assert (
        f"fix the name(s) in KIBANA_MCP_TOOLS and edit [lists.mine] in "
        f"KIBANA_MCP_TOOL_LISTS_FILE={path!r}"
    ) in text


def test_unknown_plain_tool_points_at_the_variable_only():
    with pytest.raises(ValueError) as err:
        _build(tools=["get_dashbord"])
    text = str(err.value)
    assert "fix the name(s) in KIBANA_MCP_TOOLS — every tool is listed in docs/tools.md" in text
    assert "KIBANA_MCP_TOOL_LISTS_FILE" not in text


def test_build_server_reads_no_file(tmp_path):
    # main() loads the file (load_tool_lists) and passes the lists in; given
    # them, build_server never opens the path, even one that does not exist.
    settings = Settings(tools=["@read-only-explorer"], tool_lists_file=str(tmp_path / "nope.toml"))
    build_server(settings, lambda space=None: FakeGateway(), tool_lists=BUILTIN_LISTS)
    # Without them it refuses rather than silently dropping the operator's lists.
    with pytest.raises(ValueError, match=r"pass tool_lists=load_tool_lists\(settings\)"):
        build_server(settings, lambda space=None: FakeGateway())


def test_file_is_read_with_a_plain_list_too(tmp_path):
    # No `@` entry, but KIBANA_MCP_TOOLS is set: the named file is still validated.
    with pytest.raises(ToolListError, match="could not be read"):
        load_tool_lists(Settings(tools=["get_dashboard"], tool_lists_file=str(tmp_path / "x")))


def test_bad_file_fails_startup_when_the_list_is_set(tmp_path):
    with pytest.raises(ValueError, match="KIBANA_MCP_TOOL_LISTS_FILE=.*could not be read"):
        _build(tools=["get_dashboard"], tool_lists_file=str(tmp_path / "nope.toml"))


async def test_file_without_the_list_is_ignored_with_one_line(tmp_path, caplog):
    # Read only when KIBANA_MCP_TOOLS is set: the default path never opens it.
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        names = await _visible(tool_lists_file=str(tmp_path / "nope.toml"))
    assert len(names) == 27
    (line,) = _lines(caplog, "KIBANA_MCP_TOOL_LISTS_FILE")
    assert "ignored" in line and "KIBANA_MCP_TOOLS is not set" in line


# --- coherence warnings ----------------------------------------------------------


async def test_incoherent_list_warns_once_naming_what_relies_on_what(caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        names = await _visible(tools=["create_dashboard", "add_panel", "update_alert_rule"])
    assert set(names) == {"create_dashboard", "add_panel", "update_alert_rule"}  # still starts
    (line,) = _lines(caplog, "rely on")
    assert "leaves out 5 tool(s)" in line
    assert "describe_data_view (relied on by add_panel, create_dashboard)" in line
    assert "search_dashboards (relied on by add_panel)" in line
    assert "list_data_views (relied on by describe_data_view)" in line
    assert "get_alert_rule (relied on by update_alert_rule)" in line
    assert "list_alert_rules (relied on by get_alert_rule)" in line
    assert "add them to KIBANA_MCP_TOOLS" in line


@pytest.mark.parametrize(
    "tools",
    [
        # the owner's small-model example (docs/configuration.md)
        ["list_data_views", "describe_data_view", "search_dashboards", "get_dashboard",
         "create_dashboard", "add_panel", "update_panel", "create_data_view"],
        ["@dashboards-analyst"],
        ["@read-only-explorer", "get_kibana_status"],
    ],
)
async def test_coherent_list_logs_nothing(caplog, tools):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _visible(tools=tools)
    assert not caplog.records


async def test_unpublished_tool_needs_nothing(caplog):
    # update_panel is above the read tier, so what it relies on is not missing.
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        names = await _visible(tools=["update_panel"], tier=Tier.READ)
    assert names == []
    assert _lines(caplog, "rely on") == []


@pytest.mark.parametrize(
    "over", [{}, {"toolboxes": ["dashboards"]}, {"toolboxes": ["alerting"], "tier": Tier.READ}]
)
async def test_no_warning_without_the_list(caplog, over):
    # Toolbox selections are not checked: the warning belongs to KIBANA_MCP_TOOLS.
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _visible(**over)
    assert not caplog.records


@pytest.mark.parametrize("tier", list(Tier))
async def test_plain_list_surface_is_unchanged_by_named_lists(tier):
    # No `@` entry: the same names, in the same order, as the D34 allow list
    # published before named lists existed (pinned there by exact sets).
    plain = ["get_dashboard", "search_dashboards", "list_agents", "delete_dashboard"]
    names = await _visible(tools=plain, tier=tier)
    expected = {"read": ["search_dashboards", "get_dashboard", "list_agents"]}
    expected["write"] = expected["read"]
    expected["destructive"] = [*expected["read"][:2], "delete_dashboard", "list_agents"]
    assert names == expected[tier.value]


# --- the documentation states the data as it ships -------------------------------

_CONFIG_DOC = Path(__file__).resolve().parents[2] / "docs" / "configuration.md"


def _doc_section(title):
    text = _CONFIG_DOC.read_text(encoding="utf-8")
    return text.split(title, 1)[1].split("\n#", 1)[0]


def test_documented_builtin_lists_match_the_data():
    rows = re.findall(r"^\| `([\w-]+)` \| (`.+?`) \|", _doc_section("#### Built-in lists"), re.M)
    documented = {name: tuple(re.findall(r"`(\w+)`", tools)) for name, tools in rows}
    assert documented == dict(BUILTIN_LISTS)


def test_documented_example_file_is_valid(tmp_path, catalog):
    example = re.search(r"```toml\n(.*?)```", _doc_section("#### Your own lists"), re.S)
    lists = load_list_file(_write(tmp_path, example.group(1)))
    assert lists and all(t in catalog for tools in lists.values() for t in tools)


async def test_documented_warning_line_is_what_the_server_logs(caplog):
    section = _doc_section("### Coherence warnings")
    tools = re.search(r"`KIBANA_MCP_TOOLS=([\w,]+)`", section).group(1).split(",")
    documented = re.search(r"```text\n(.*?)\n```", section, re.S).group(1)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _visible(tools=tools)
    assert _lines(caplog, "rely on") == [documented]
