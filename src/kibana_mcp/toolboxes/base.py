"""Toolbox contract: a vertical slice of tools registered onto the server."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.errors import KibanaMcpError, KibanaRejected
from kibana_mcp.ports.gateway import KibanaGateway

# Kibana space id grammar. Shared single owner: the create_space tool and
# every space-targeting parameter. No max_length — Kibana accepted a
# 300-char id live (probe P8); there is no server bound to mirror.
SPACE_ID_PATTERN = r"^[a-z0-9_-]+$"


class GatewayFactory(Protocol):
    def __call__(self, space: str | None = None) -> KibanaGateway: ...


def with_space(result: dict[str, Any], space: str | None) -> dict[str, Any]:
    """Echo the effective space in a dict-returning tool result — only when
    the caller chose one; the default path stays byte-identical."""
    return result if space is None else {**result, "space": space}


@dataclass(frozen=True)
class ToolboxDeps:
    gateway_factory: GatewayFactory
    public_kibana_url: str
    # Server always injects this (server._resolve_export_dir via main). The default
    # is a fail-closed, NON-predictable sentinel (not a guessable /tmp path): any
    # caller that forgets to set it fails on first use rather than writing exports
    # to a hijackable location. Only saved-objects export/import reads it.
    export_dir: Path = Path("/nonexistent-mcp-for-kibana-export-dir")


class Toolbox(Protocol):
    name: str

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None: ...


@contextmanager
def gateway_errors() -> Iterator[None]:
    """Translate domain errors into LLM-facing tool errors."""
    try:
        yield
    except KibanaRejected as e:
        raise ToolError(f"{e.message}: {e.detail}" if e.detail else e.message) from e
    except KibanaMcpError as e:
        raise ToolError(e.message) from e
