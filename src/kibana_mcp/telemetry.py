"""Optional OpenTelemetry span export (Phase C; additive, default-off).

FastMCP already wraps every tool call in a span via the *global* OTEL tracer;
with no provider installed those spans are non-recording, so an unconfigured
server pays nothing and imports no OpenTelemetry SDK. `configure_telemetry()`
installs an SDK provider + OTLP/HTTP exporter ONLY when `settings.otel_enabled`,
pointing at the OTLP/APM backend (the local stack's apm-server by default).
Requires the `otel` extra: ``pip install 'mcp-for-kibana[otel]'``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

    from kibana_mcp.config import Settings

logger = logging.getLogger(__name__)

_MISSING_SDK_HINT = (
    "KIBANA_MCP_OTEL_ENABLED is set but the OpenTelemetry SDK is not installed — "
    "install the extra: pip install 'mcp-for-kibana[otel]'"
)


def _traces_endpoint(settings: Settings) -> str:
    """The OTLP/HTTP traces URL: the configured base with `/v1/traces` appended.
    Single source so the built exporter and the log line never drift."""
    return settings.otel_endpoint.rstrip("/") + "/v1/traces"


def build_tracer_provider(settings: Settings) -> TracerProvider | None:
    """Build a TracerProvider exporting to the OTLP endpoint, or ``None`` when
    OTEL is disabled. Pure — touches no global state. The SDK is imported here
    (function-local) so the default-off path imports nothing; enabled-but-not-
    installed raises RuntimeError with an actionable hint."""
    if not settings.otel_enabled:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        raise RuntimeError(_MISSING_SDK_HINT) from e

    endpoint = _traces_endpoint(settings)
    # The Phase B OTLP endpoint is token-gated (no-token -> 401); send the token
    # as a Bearer header when configured. Never logged.
    headers = (
        {"Authorization": f"Bearer {settings.otel_secret_token}"}
        if settings.otel_secret_token
        else None
    )
    # Resource.create also merges standard OTEL_RESOURCE_ATTRIBUTES from the env.
    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
    )
    return provider


def configure_telemetry(settings: Settings) -> bool:
    """Install the global OTEL tracer provider when enabled; return whether one
    was installed. Logs the endpoint + service name only — never the token."""
    provider = build_tracer_provider(settings)
    if provider is None:
        return False
    from opentelemetry import trace  # always available (fastmcp pulls the api)

    trace.set_tracer_provider(provider)
    logger.info(
        "OpenTelemetry span export enabled: endpoint=%s service=%s",
        _traces_endpoint(settings),
        settings.otel_service_name,
    )
    return True
