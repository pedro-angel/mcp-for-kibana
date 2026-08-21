"""Composition root: assemble the FastMCP server from Settings."""

import tempfile
from pathlib import Path

from fastmcp import FastMCP

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway, is_space_pinned
from kibana_mcp.adapters.mcp import docs_resources
from kibana_mcp.adapters.mcp.auth import resolve_api_key
from kibana_mcp.config import Settings
from kibana_mcp.core.errors import KibanaRejected
from kibana_mcp.ports.gateway import KibanaGateway
from kibana_mcp.telemetry import configure_telemetry
from kibana_mcp.toolboxes import TOOLBOXES
from kibana_mcp.toolboxes.base import GatewayFactory, ToolboxDeps

_TIER_TAGS = {"read", "write", "destructive"}


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


def build_server(
    settings: Settings,
    gateway_factory: GatewayFactory,
    export_dir: Path | None = None,
) -> FastMCP:
    unknown = set(settings.toolboxes) - TOOLBOXES.keys()
    if unknown:
        raise ValueError(
            f"unknown toolbox(es) {sorted(unknown)}; available: {sorted(TOOLBOXES)}"
        )
    # on_duplicate="ignore": toolboxes compose freely; if two ever register the
    # same tool name, the first one in KIBANA_MCP_TOOLBOXES wins, deterministically
    # and silently — rather than the default "warn" (last wins + a log warning).
    # (Each tool is owned by exactly one toolbox today; this keeps overlap safe.)
    mcp = FastMCP(
        "mcp-for-kibana", instructions=_INSTRUCTIONS, on_duplicate="ignore", mask_error_details=True
    )
    # No I/O here (build_server stays pure for FakeGateway tests): main() creates
    # export_dir and passes it; unit tests take the ToolboxDeps default (unused).
    deps = ToolboxDeps(
        gateway_factory=gateway_factory,
        public_kibana_url=settings.effective_public_url,
        **({"export_dir": export_dir} if export_dir is not None else {}),
    )
    for name in settings.toolboxes:
        TOOLBOXES[name].register(mcp, deps)
    for tier_tag in _TIER_TAGS - settings.tier.allowed:
        mcp.disable(tags={tier_tag})
    _register_docs_resources(mcp)
    return mcp


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

    # Same rationale as configure_telemetry: the export-dir I/O lives here, not in
    # build_server, so FakeGateway assembly tests stay side-effect-free.
    mcp = build_server(settings, gateway_factory, export_dir=_resolve_export_dir(settings))
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
