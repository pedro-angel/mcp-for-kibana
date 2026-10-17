"""Composition root: assemble the FastMCP server from Settings."""

import asyncio
import difflib
import logging
import tempfile
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway, is_space_pinned
from kibana_mcp.adapters.mcp import docs_resources
from kibana_mcp.adapters.mcp.auth import resolve_api_key
from kibana_mcp.config import Settings
from kibana_mcp.core.errors import KibanaRejected
from kibana_mcp.ports.gateway import KibanaGateway
from kibana_mcp.telemetry import configure_telemetry
from kibana_mcp.tool_lists import (
    BUILTIN_LISTS,
    FILE_SETTING,
    available_lists,
    expand,
    missing_dependencies,
)
from kibana_mcp.toolboxes import TOOLBOXES
from kibana_mcp.toolboxes.base import GatewayFactory, ToolboxDeps

_TIER_TAGS = {"read", "write", "destructive"}

# Startup notes go to stderr via logging, never stdout (the stdio MCP channel).
_log = logging.getLogger(__name__)


def _resolve_export_dir(settings: Settings) -> Path:
    """Create the saved-objects export dir (0700). Called from main(), NOT
    build_server, so build_server stays side-effect-free for FakeGateway tests.

    Default: `mkdtemp` — a fresh, unguessable, atomically-0700 dir that fails
    closed, so a pre-created world-writable `/tmp/<fixed-name>` or a planted
    symlink cannot be hijacked. An explicit KIBANA_MCP_EXPORT_DIR is the
    operator's path: create it if absent, refuse a symlink, tighten to 0700."""
    if settings.export_dir:
        path = Path(settings.export_dir)
        if path.is_symlink():  # check BEFORE mkdir (a dangling symlink would else raise)
            raise RuntimeError(f"KIBANA_MCP_EXPORT_DIR {settings.export_dir!r} is a symlink; refusing")
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not path.is_dir():
            raise RuntimeError(f"KIBANA_MCP_EXPORT_DIR {settings.export_dir!r} is not a directory")
        path.chmod(0o700)  # tighten a pre-existing dir (mkdir mode= is umask-limited)
        return path
    return Path(tempfile.mkdtemp(prefix="mcp-for-kibana-exports-"))


_INSTRUCTIONS = """Tools for working with Kibana dashboards and visualizations.
Typical flow: list_data_views -> describe_data_view (learn field names) ->
create_dashboard with one or more visualization specs. Field names are
case-sensitive; always verify them with describe_data_view first."""
# _INSTRUCTIONS walks this flow, so it is given only when all three are
# published; any other surface gets the generic text, which names no tool.
_DASHBOARDS_FLOW = frozenset({"list_data_views", "describe_data_view", "create_dashboard"})
_GENERIC_INSTRUCTIONS = """Tools for working with Kibana. Each tool's description says
what it takes; when a call fails, its error text names the fix."""


def _instructions(published: set[str]) -> str:
    return _INSTRUCTIONS if _DASHBOARDS_FLOW <= published else _GENERIC_INSTRUCTIONS


def load_tool_lists(settings: Settings) -> Mapping[str, tuple[str, ...]]:
    """The named lists `@name` can use: the built-ins, plus the operator's
    KIBANA_MCP_TOOL_LISTS_FILE when KIBANA_MCP_TOOLS is set (D41). main() calls
    this and passes the result to build_server, which reads no file. The file
    is read and validated whenever the list is set, even with no `@` entry, so
    a broken file fails the first start that could use it."""
    if not settings.tool_lists_file:
        return BUILTIN_LISTS
    if not settings.tools:
        _log.warning(
            "%s is ignored: KIBANA_MCP_TOOLS is not set, so no named list is used",
            FILE_SETTING,
        )
        return BUILTIN_LISTS
    return available_lists(settings.tool_lists_file)


def build_server(
    settings: Settings,
    gateway_factory: GatewayFactory,
    export_dir: Path | None = None,
    tool_lists: Mapping[str, tuple[str, ...]] | None = None,
) -> FastMCP:
    # No I/O here (build_server stays pure for FakeGateway tests): main() creates
    # export_dir and loads the list file (load_tool_lists) and passes both; unit
    # tests take the ToolboxDeps default (unused) and the built-in lists.
    deps = ToolboxDeps(
        gateway_factory=gateway_factory,
        public_kibana_url=settings.effective_public_url,
        **({"export_dir": export_dir} if export_dir is not None else {}),
    )
    if tool_lists is None:
        if settings.tools and settings.tool_lists_file:
            raise ValueError(
                f"build_server reads no files: {FILE_SETTING} is set, so pass "
                "tool_lists=load_tool_lists(settings)"
            )
        tool_lists = BUILTIN_LISTS
    if settings.tools:
        toolboxes, unlisted = _select_tools(settings, deps, tool_lists)
    else:
        unknown = set(settings.toolboxes) - TOOLBOXES.keys()
        if unknown:
            raise ValueError(
                f"unknown toolbox(es) {sorted(unknown)}; available: {sorted(TOOLBOXES)}"
            )
        toolboxes, unlisted = settings.toolboxes, set()
    # on_duplicate="ignore": toolboxes compose freely; if two ever register the
    # same tool name, the first one in KIBANA_MCP_TOOLBOXES wins, deterministically
    # and silently — rather than the default "warn" (last wins + a log warning).
    # (Each tool is owned by exactly one toolbox today; this keeps overlap safe.)
    mcp = FastMCP("mcp-for-kibana", on_duplicate="ignore", mask_error_details=True)
    for name in toolboxes:
        TOOLBOXES[name].register(mcp, deps)
    hidden_tiers = _TIER_TAGS - settings.tier.allowed
    for tier_tag in hidden_tiers:
        mcp.disable(tags={tier_tag})
    # The allow list only ever hides (D34). Never mcp.enable(): a later enable
    # overrides an earlier disable, so it could re-publish a tool the tier hid.
    if unlisted:
        mcp.disable(names=unlisted, components={"tool"})
    # The instructions describe what is published: the same two rules as the
    # disables above, applied to what was registered.
    published = {
        name
        for name, tags in _registered_tools(mcp).items()
        if not tags & hidden_tiers and name not in unlisted
    }
    if settings.tools:
        _warn_missing_dependencies(published)
    mcp.instructions = _instructions(published)
    _register_docs_resources(mcp)
    return mcp


def _run_async[T](make: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run a coroutine to completion on its own event loop in a worker thread:
    FastMCP lists tools only through an async API, and this works whether or not
    the caller is already inside a running loop (the async unit tests are)."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(make())).result()


def _registered_tools(mcp: FastMCP) -> dict[str, frozenset[str]]:
    """Every tool registered on the server -> its tags, visibility not applied.
    Read from the local provider, not FastMCP's tools/list, which would open a
    telemetry span (KIBANA_MCP_OTEL_ENABLED) before any client asked."""
    return {t.name: frozenset(t.tags) for t in _run_async(mcp.local_provider.list_tools)}


def _tool_catalog(deps: ToolboxDeps) -> dict[str, tuple[str, frozenset[str]]]:
    """Every tool name -> (owning toolbox, its tags), from a throwaway server per
    toolbox. Registration does no I/O; the listing runs through _run_async."""
    scratch = {name: FastMCP(f"catalog-{name}") for name in TOOLBOXES}
    for name, box in TOOLBOXES.items():
        box.register(scratch[name], deps)

    async def _list() -> dict[str, list[tuple[str, frozenset[str]]]]:
        return {
            name: [(t.name, frozenset(t.tags)) for t in await server.local_provider.list_tools()]
            for name, server in scratch.items()
        }

    listed = _run_async(_list)
    catalog: dict[str, tuple[str, frozenset[str]]] = {}
    for box_name, tools in listed.items():
        for tool_name, tags in tools:
            catalog.setdefault(tool_name, (box_name, tags))
    return catalog


def _select_tools(
    settings: Settings, deps: ToolboxDeps, lists: Mapping[str, tuple[str, ...]]
) -> tuple[list[str], set[str]]:
    """KIBANA_MCP_TOOLS decides alone: the toolboxes owning the listed tools, and
    the names those toolboxes register that are not listed (to hide). `@name`
    entries expand to a named list's tools first (D41). Listed tools above the
    tier stay hidden by the tier; an unknown name fails startup."""
    selection = expand(settings.tools, lists)
    catalog = _tool_catalog(deps)
    unknown = [name for name in selection if name not in catalog]
    if unknown:
        raise ValueError(_unknown_tools_message(unknown, selection, catalog, settings))
    listed = set(selection)
    if "toolboxes" in settings.model_fields_set:
        _log.warning(
            "KIBANA_MCP_TOOLBOXES is ignored: KIBANA_MCP_TOOLS is set and decides "
            "which tools are published"
        )
    hidden_tiers = _TIER_TAGS - settings.tier.allowed
    above = {
        name: "/".join(sorted(catalog[name][1] & hidden_tiers))
        for name in selection
        if catalog[name][1] & hidden_tiers
    }
    if above:
        _log.warning(
            "KIBANA_MCP_TOOLS lists %d tool(s) above KIBANA_MCP_TIER=%s, not published: %s; "
            "set KIBANA_MCP_TIER to the tier in parentheses to publish them",
            len(above),
            settings.tier.value,
            ", ".join(f"{name} ({tier})" for name, tier in above.items()),
        )
    toolboxes = [box for box in TOOLBOXES if any(catalog[n][0] == box for n in listed)]
    unlisted = {n for n, (box, _) in catalog.items() if box in toolboxes} - listed
    return toolboxes, unlisted


def _unknown_tools_message(
    unknown: list[str],
    selection: Mapping[str, str | None],
    catalog: Mapping[str, object],
    settings: Settings,
) -> str:
    """Name each unknown tool, where it came from, and where to fix it: a plain
    name in KIBANA_MCP_TOOLS, or a list in the operator's file. (A built-in list
    names only real tools; tests/unit/test_tool_lists.py pins that.)"""
    hints = []
    for name in unknown:
        close = difflib.get_close_matches(name, catalog, n=1)
        hint = f"{name!r} (did you mean {close[0]!r}?)" if close else repr(name)
        origin = selection[name]
        hints.append(f"{hint} from list '@{origin}'" if origin else hint)
    fixes = []
    if any(selection[name] is None for name in unknown):
        fixes.append("fix the name(s) in KIBANA_MCP_TOOLS")
    in_file = sorted({o for name in unknown if (o := selection[name]) and o not in BUILTIN_LISTS})
    if in_file:
        tables = ", ".join(f"[lists.{o}]" for o in in_file)
        fixes.append(f"edit {tables} in {FILE_SETTING}={settings.tool_lists_file!r}")
    return (
        f"unknown tool(s) in KIBANA_MCP_TOOLS: {', '.join(hints)}; "
        f"{' and '.join(fixes)} — every tool is listed in docs/tools.md"
    )


def _warn_missing_dependencies(published: set[str]) -> None:
    """One stderr line when the published tools rely on tools the list left out
    (tool_lists.DEPENDENCIES, followed transitively). A warning, not an error:
    the model may get those inputs another way, e.g. from the user (D41)."""
    missing = missing_dependencies(published)
    if missing:
        _log.warning(
            "KIBANA_MCP_TOOLS leaves out %d tool(s) that published tools rely on: %s; "
            "add them to KIBANA_MCP_TOOLS, or ignore this if the model gets those "
            "inputs another way",
            len(missing),
            ", ".join(f"{tool} (relied on by {', '.join(by)})" for tool, by in missing.items()),
        )


def _register_docs_resources(mcp: FastMCP) -> None:
    """Serve selected project docs as read-only MCP resources so any
    connected agent can read the manual inside the session, with no
    internet or repo checkout access needed. Server-level, not
    toolbox-level: resources are read-only by nature, so tier gating
    (which only hides write/destructive tools) does not apply to them."""
    mcp.resource(
        "docs://user-guide",
        name="user-guide",
        description="Full getting-started guide: setup, first dashboard, safety rails.",
        mime_type="text/markdown",
    )(docs_resources.user_guide)
    mcp.resource(
        "docs://tools",
        name="tools",
        description="Tool reference: tiers, inputs, return shapes, error behavior.",
        mime_type="text/markdown",
    )(docs_resources.tools)
    mcp.resource(
        "docs://troubleshooting",
        name="troubleshooting",
        description="The user guide's Troubleshooting section, extracted.",
        mime_type="text/markdown",
    )(docs_resources.troubleshooting)


def _env_key_fallback(settings: Settings) -> str | None:
    """The env API key is a legitimate fallback for stdio (single user);
    in HTTP mode it would silently act as a shared credential, so it
    requires explicit opt-in."""
    if settings.transport == "http" and not settings.allow_env_key_http:
        return None
    return settings.api_key


def build_gateway_factory(settings: Settings) -> GatewayFactory:
    def gateway_factory(space: str | None = None) -> KibanaGateway:
        if (
            space is not None
            and settings.public_kibana_url is not None
            and is_space_pinned(settings.public_kibana_url)
        ):
            # ONLY the explicitly-set public URL — effective_public_url falls
            # back to kibana_url, which connect guards itself; using the
            # fallback here would shadow connect's message.
            raise KibanaRejected(
                "this deployment's public Kibana URL is already space-pinned "
                "('/s/<id>' base path); the `space` parameter cannot be used here"
            )
        api_key = resolve_api_key(_env_key_fallback(settings))
        return KibanaPyGateway.connect(settings.kibana_url, api_key, space)
    return gateway_factory


def main() -> None:
    settings = Settings.load()
    # Additive, default-off: installs a global OTEL provider only when
    # KIBANA_MCP_OTEL_ENABLED is set (FastMCP's tool-call spans then export).
    # Kept out of build_server so tests with fakes stay side-effect-free.
    configure_telemetry(settings)

    gateway_factory = build_gateway_factory(settings)

    # Same rationale as configure_telemetry: the export-dir and list-file I/O live
    # here, not in build_server, so FakeGateway assembly tests stay side-effect-free.
    tool_lists = load_tool_lists(settings)  # first: a bad list file fails before any dir is made
    mcp = build_server(
        settings, gateway_factory, export_dir=_resolve_export_dir(settings), tool_lists=tool_lists
    )
    if settings.transport == "http":
        mcp.run(
            transport="http",
            host=settings.host,
            port=settings.port,
            stateless_http=True,
            host_origin_protection="auto",
        )
    else:
        mcp.run()
