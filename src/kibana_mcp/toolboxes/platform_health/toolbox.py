"""The platform-health toolbox: read-only Kibana + Task Manager health summaries.

All tools are `read` tier — this toolbox registers nothing at write/destructive.
The gateway summarises the (large) raw Kibana status/stats/health responses into
small models; the tools just serialise them, so a small local model gets concise
health signals rather than a deep metric tree.
"""

from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from kibana_mcp.toolboxes.base import ToolboxDeps, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


class PlatformHealthToolbox:
    name = "platform-health"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_kibana_status() -> dict[str, Any]:
            """Kibana's overall health: the overall status level, the Kibana
            version, and any core services or plugins that are not fully
            available. Concise — omits the raw metrics blob."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_kibana_status())

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_kibana_stats() -> dict[str, Any]:
            """Kibana runtime resource stats: heap used/total/limit, event-loop
            delay, and concurrent connections."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_kibana_stats())

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_task_manager_health() -> dict[str, Any]:
            """Kibana Task Manager health: status (OK / warn / error) and the
            latest health/update timestamps."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_task_manager_health())
