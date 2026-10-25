"""Named tool lists and the tool dependency map (D41): data, not code.

This module is the one owner of both. It is framework-free (import-linter
keeps it beside `config`): it knows tool *names* only; whether a name is a
real tool is checked by the server against its live catalog, and by unit
tests for the data held here.
"""

import difflib
import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

# The built-in lists: the shipped personas as tool names (profiles/README.md).
# `read-only-explorer` is the small-model reference surface; `dashboards-analyst`
# is the default selection at `write` (D39). `fleet-admin` is not a list: it is
# the whole `fleet` toolbox at `destructive`, which the toolbox axis already
# says exactly, and a list would only drift from the toolbox as it grows.
BUILTIN_LISTS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "read-only-explorer": (
        "list_data_views", "describe_data_view", "resolve_short_url", "export_saved_objects",
        "search_dashboards", "get_dashboard", "search_visualizations", "get_visualization",
        "get_kibana_status", "get_kibana_stats", "get_task_manager_health",
    ),
    "dashboards-analyst": (
        "list_data_views", "describe_data_view", "create_data_view", "update_data_view",
        "resolve_short_url", "create_short_url", "export_saved_objects", "import_saved_objects",
        "search_dashboards", "get_dashboard", "create_dashboard", "add_panel", "update_panel",
        "add_esql_metric_panel", "add_esql_table_panel", "add_esql_xy_panel",
        "search_visualizations", "get_visualization", "create_visualization",
        "update_visualization", "add_library_panel",
    ),
})

# Tool -> the tools it relies on: B is listed for A when A takes an input that
# only B shows the model and A's own description or error guidance sends the
# model to B — an index or field name (update_panel -> get_dashboard, every
# VizSpec tool -> describe_data_view), the current state an in-place edit
# merges into (update_alert_rule -> get_alert_rule, D40), a handle, or an
# existing object's id that a not-found error says to look up ("call
# search_dashboards to find it"). Edges point at tools of the same or a lower
# tier, so a missing one is always publishable by listing it. Direct edges
# only: the server follows them transitively.
DEPENDENCIES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    # dashboards
    "get_dashboard": ("search_dashboards",),
    "get_visualization": ("search_visualizations",),
    "create_dashboard": ("describe_data_view",),
    "create_visualization": ("describe_data_view",),
    "update_visualization": ("describe_data_view", "search_visualizations"),
    "add_panel": ("describe_data_view", "search_dashboards"),
    "add_library_panel": ("search_dashboards", "search_visualizations"),
    "add_esql_metric_panel": ("search_dashboards",),
    "add_esql_table_panel": ("search_dashboards",),
    "add_esql_xy_panel": ("search_dashboards",),
    "update_panel": ("describe_data_view", "get_dashboard"),
    "delete_panel": ("get_dashboard",),
    "delete_dashboard": ("search_dashboards",),
    "delete_visualization": ("search_visualizations",),
    # data-management
    "describe_data_view": ("list_data_views",),
    "update_data_view": ("list_data_views",),
    "import_saved_objects": ("export_saved_objects",),
    "overwrite_saved_objects": ("export_saved_objects",),
    # alerting
    "get_alert_rule": ("list_alert_rules",),
    "update_alert_rule": ("get_alert_rule",),
    "enable_alert_rule": ("list_alert_rules",),
    "disable_alert_rule": ("list_alert_rules",),
    "delete_alert_rule": ("list_alert_rules",),
    "mute_alert_rule": ("list_alert_rules",),
    "unmute_alert_rule": ("list_alert_rules",),
    "mute_alert": ("list_alert_rules",),
    "unmute_alert": ("get_alert_rule",),  # muted_alert_ids
    "snooze_alert_rule": ("list_alert_rules",),
    "unsnooze_alert_rule": ("snooze_alert_rule",),  # the only source of a schedule_id
    "update_alert_rule_api_key": ("list_alert_rules",),
    "get_connector": ("list_connectors",),
    "update_connector": ("get_connector",),
    # observability (the Synthetics write tier, D46), platform-admin, fleet
    "get_synthetic_monitor": ("list_synthetic_monitors",),
    "create_synthetic_monitor": ("list_synthetic_private_locations",),
    "update_synthetic_monitor": ("list_synthetic_monitors", "list_synthetic_private_locations"),
    "test_synthetic_monitor": ("list_synthetic_monitors",),
    "delete_synthetic_monitor": ("list_synthetic_monitors",),
    "update_synthetic_param": ("list_synthetic_params",),
    "delete_synthetic_param": ("list_synthetic_params",),
    "create_synthetic_private_location": ("list_agent_policies",),
    "update_synthetic_private_location": ("list_synthetic_private_locations",),
    "delete_synthetic_private_location": ("list_synthetic_private_locations",),
    "get_space": ("list_spaces",),
    "create_package_policy": ("get_package",),
    "update_package_policy": ("get_package",),
    "upgrade_agent": ("list_agent_versions",),
})

LIST_FILE_VERSION = 1
FILE_SETTING = "KIBANA_MCP_TOOL_LISTS_FILE"
# A list name must survive a comma-separated env var after '@'. Used with
# fullmatch: `$` alone would also accept a name ending in a newline.
_LIST_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ToolListError(ValueError):
    """A tool-list reference or file the server cannot use; the text names the fix."""


def load_list_file(path: str) -> dict[str, tuple[str, ...]]:
    """Parse and strictly validate an operator's tool-list file (format version 1):

        version = 1
        [lists.<name>]
        tools = ["tool_a", "tool_b"]

    A leading `~` is expanded; a relative path resolves against the server's
    working directory, which the MCP client chooses. Every problem is an error
    that names the fix; nothing is skipped. Tool names are checked against the
    server's catalog later, when a list is used."""
    def fail(problem: str) -> ToolListError:
        return ToolListError(f"{FILE_SETTING}={path!r}: {problem}")

    file = Path(path).expanduser()
    try:
        text = file.read_text(encoding="utf-8-sig")
    except OSError as e:
        where = "" if file.is_absolute() else (
            f" (a relative path, resolved against the server's working directory "
            f"{str(Path.cwd())!r}, which the MCP client chooses)"
        )
        raise fail(
            f"could not be read{where}: {e.strerror or e}; set {FILE_SETTING} to the "
            "absolute path of a readable file"
        ) from e
    except UnicodeDecodeError as e:
        raise fail(f"is not UTF-8 text ({e.reason} at byte {e.start}); save it as UTF-8") from e
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise fail(f"is not valid TOML: {e}") from e
    unknown = sorted(set(data) - {"version", "lists"})
    if unknown:
        raise fail(f"unknown top-level key(s) {unknown}; only 'version' and 'lists' are allowed")
    version = data.get("version")
    if version is None:
        raise fail(f"has no 'version'; add `version = {LIST_FILE_VERSION}` as its first line")
    if type(version) is not int or version != LIST_FILE_VERSION:
        raise fail(
            f"version {version!r} is not supported; this server reads "
            f"`version = {LIST_FILE_VERSION}` files"
        )
    lists = data.get("lists")
    if not isinstance(lists, dict) or not lists:
        raise fail('defines no lists; add one as `[lists.<name>]` with `tools = ["..."]`')
    parsed: dict[str, tuple[str, ...]] = {}
    for name, body in lists.items():
        if not _LIST_NAME_RE.fullmatch(name):
            raise fail(
                f"list name {name!r} is not allowed; use lowercase letters, digits, "
                "'-' and '_', starting with a letter or digit"
            )
        if name in BUILTIN_LISTS:
            raise fail(
                f"list {name!r} has the name of a built-in list; rename it "
                f"(built-in lists: {', '.join(sorted(BUILTIN_LISTS))})"
            )
        if not isinstance(body, dict):
            raise fail(f'list {name!r} must be a table: `[lists.{name}]` with `tools = ["..."]`')
        extra = sorted(set(body) - {"tools"})
        if extra:
            raise fail(f"list {name!r} has unknown key(s) {extra}; only 'tools' is allowed")
        tools = body.get("tools")
        if not isinstance(tools, list) or not tools:
            raise fail(f'list {name!r} needs a non-empty `tools = ["..."]` array')
        bad = [t for t in tools if not isinstance(t, str) or not t.strip()]
        if bad:
            raise fail(f"list {name!r} has tool entries that are not names: {bad!r}")
        nested = [t for t in tools if t.strip().startswith("@")]
        if nested:
            raise fail(
                f"list {name!r} references other lists {nested}; a list holds tool "
                "names only — write out their tools"
            )
        parsed[name] = tuple(dict.fromkeys(t.strip() for t in tools))
    return parsed


def available_lists(path: str | None) -> dict[str, tuple[str, ...]]:
    """The built-in lists, plus the operator's file when one is named."""
    return {**BUILTIN_LISTS, **(load_list_file(path) if path else {})}


def expand(entries: Iterable[str], lists: Mapping[str, tuple[str, ...]]) -> dict[str, str | None]:
    """Expand `@name` entries into their tools, order kept, first occurrence wins.
    Returns tool name -> the list it came from (None for a plainly listed name).
    Every unknown `@name` fails at once, naming the available lists."""
    expanded: dict[str, str | None] = {}
    unknown: list[str] = []
    for entry in entries:
        if not entry.startswith("@"):
            expanded.setdefault(entry, None)
            continue
        name = entry[1:]
        if name not in lists:
            unknown.append(name)
            continue
        for tool in lists[name]:
            expanded.setdefault(tool, name)
    if unknown:
        hints = []
        for name in unknown:
            close = difflib.get_close_matches(name, lists, n=1)
            hints.append(f"'@{name}' (did you mean '@{close[0]}'?)" if close else f"'@{name}'")
        raise ToolListError(
            f"unknown tool list(s) in KIBANA_MCP_TOOLS: {', '.join(hints)}; "
            f"available lists: {', '.join(sorted(lists))} (lists of your own go in "
            f"the file named by {FILE_SETTING})"
        )
    return expanded


def missing_dependencies(published: Iterable[str]) -> dict[str, list[str]]:
    """Tools the published ones rely on (followed transitively) that are not
    published: missing tool -> the tools relying on it, both sorted."""
    have = set(published)
    missing: dict[str, set[str]] = {}
    frontier = sorted(have)
    while frontier:
        reached = []
        for tool in frontier:
            for needed in DEPENDENCIES.get(tool, ()):
                if needed in have:
                    continue
                if needed not in missing:
                    missing[needed] = set()
                    reached.append(needed)
                missing[needed].add(tool)
        frontier = reached
    return {tool: sorted(missing[tool]) for tool in sorted(missing)}
