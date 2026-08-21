"""The observability toolbox (Wave 3): read-first access to the Kibana
observability surface that is available on a Basic license and wrapped by
kibana-py — Synthetics monitoring, Uptime settings, and APM *configuration*.

All tools are `read` tier. This toolbox does NOT expose APM service/transaction/
trace/service-map telemetry (those are internal-only Kibana APIs, unreachable by
an external client) or SLOs (which need a Platinum license). Those are deferred
to future additive tiers of this same toolbox.
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.toolboxes.base import ToolboxDeps, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


class ObservabilityToolbox:
    name = "observability"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_synthetic_monitors() -> list[dict[str, Any]]:
            """List all Synthetics monitors (HTTP/TCP/ICMP/browser availability
            checks): id, name, type, enabled, tags, locations, schedule, and
            target (the url or host being checked). Paginated to completion."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(m) for m in gw.list_synthetic_monitors()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_synthetic_monitor(monitor_id: str) -> dict[str, Any]:
            """Get one Synthetics monitor by its config id (the `id` field
            returned for each monitor by list_synthetic_monitors)."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_synthetic_monitor(monitor_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_synthetic_params() -> list[dict[str, Any]]:
            """List Synthetics global parameters (id, key, description, tags).
            Parameter VALUES are never returned."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(p) for p in gw.list_synthetic_params()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_synthetic_private_locations() -> list[dict[str, Any]]:
            """List Synthetics private locations (id, label, agent_policy_id,
            is_invalid, tags) — the Fleet-agent-backed places monitors run
            from."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(loc) for loc in gw.list_synthetic_private_locations()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_uptime_settings() -> dict[str, Any]:
            """The Uptime app settings: the heartbeat index pattern, TLS
            certificate age / expiration alert thresholds, and default alert
            connectors and email recipients."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_uptime_settings())

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_apm_agent_configs() -> list[dict[str, Any]]:
            """List APM agent configurations (central config pushed to APM
            agents): service name/environment, the settings map, whether an
            agent has applied it, and the etag. This is APM *configuration*, not
            service metrics or traces."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(c) for c in gw.list_apm_agent_configs()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_apm_agent_config(
            service_name: str | None = None,
            environment: str | None = None,
        ) -> dict[str, Any]:
            """Get one APM agent configuration by service name + environment.
            Omit both to fetch the all-services/all-environments configuration.
            Errors if no matching configuration exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_apm_agent_config(service_name, environment))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_apm_environments(service_name: str | None = None) -> list[dict[str, Any]]:
            """List the APM service environments known to agent-configuration.
            Omit service_name to list across all services. The sentinel
            environment name "ALL_OPTION_VALUE" means "all environments" and is
            always present — it is not a literal environment."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(e) for e in gw.list_apm_environments(service_name)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_apm_sourcemaps() -> list[dict[str, Any]]:
            """List uploaded RUM source map artifacts (identifier, created).
            Paginated to completion."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(s) for s in gw.list_apm_sourcemaps()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def search_apm_annotations(
            service_name: Annotated[str, Field(min_length=1)],
            start: Annotated[str, Field(min_length=1)],
            end: Annotated[str, Field(min_length=1)],
            environment: str = "ENVIRONMENT_ALL",
        ) -> list[dict[str, Any]]:
            """Search APM annotations (e.g. deployment markers) for a service in
            an ISO-8601 [start, end] time window. `environment` defaults to all
            environments. Returns each annotation's id, timestamp, text, type."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [
                    asdict(a)
                    for a in gw.search_apm_annotations(service_name, start, end, environment)
                ]
