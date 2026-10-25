"""Optional OpenTelemetry span export (Phase C; additive, default-off).

FastMCP already wraps every tool call in a span via the *global* OTEL tracer;
with no provider installed those spans are non-recording, so an unconfigured
server pays nothing and imports no OpenTelemetry SDK. `configure_telemetry()`
installs an SDK provider + OTLP/HTTP exporter ONLY when `settings.otel_enabled`,
pointing at the OTLP/APM backend (the local stack's apm-server by default).
Requires the `otel` extra: ``pip install 'mcp-for-kibana[otel]'``.

Spans leave the process soon after they end and are flushed on exit, SIGTERM
included: a stdio client tears its server down within about half a second
(Claude Code sends SIGINT, then SIGTERM about 100 ms later, then SIGKILL), and
the SDK's default 5 s batch delay lost the spans of a session's last calls.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

    from kibana_mcp.config import Settings

logger = logging.getLogger(__name__)

_MISSING_SDK_HINT = (
    "KIBANA_MCP_OTEL_ENABLED is set but the OpenTelemetry SDK is not installed — "
    "install the extra: pip install 'mcp-for-kibana[otel]'"
)


# Batch delay when OTEL_BSP_SCHEDULE_DELAY is unset (the SDK's own default is
# 5000 ms). Short enough that a call's span is exported while the client is
# still reading the result, so a fast teardown does not take it with it.
_EXPORT_DELAY_MS = 500

# Longest the SIGTERM handler waits for the exit flush before the process dies.
# A reachable collector takes well under this; a dead one would otherwise hold
# the exit for the exporter's 10 s timeout and retries.
_EXIT_FLUSH_S = 2.0


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
    # An explicit OTEL_BSP_SCHEDULE_DELAY wins: None lets the SDK read it.
    delay = None if os.environ.get("OTEL_BSP_SCHEDULE_DELAY") else _EXPORT_DELAY_MS
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, headers=headers),
            schedule_delay_millis=delay,
        )
    )
    return provider


def install_exit_flush(provider: TracerProvider) -> None:
    """Flush the provider's queued spans when the process is told to stop.

    A normal exit (SIGINT included) already flushes through the provider's own
    ``atexit`` shutdown. SIGTERM's default action kills the process without
    running ``atexit``, so a handler here shuts the provider down (exporting
    what is queued) for at most ``_EXIT_FLUSH_S`` seconds, then re-raises
    SIGTERM with its default action, so the exit status is unchanged and an
    unreachable collector cannot hold the exit up for the exporter's own
    timeout. The handler puts the default action back before it flushes: a
    second SIGTERM ends the process at once. A SIGTERM that lands while an
    ``atexit`` flush is already running starts no flush of its own, so that
    flush can go on; the next SIGTERM ends it. Installed only from the main
    thread (``signal.signal`` refuses any other). On the HTTP transport uvicorn
    saves this handler, restores it after its graceful shutdown and re-raises
    the SIGTERM it caught, so that exit goes through the handler too."""
    exiting = threading.Event()

    def _on_sigterm(signum: int, _frame: object) -> None:
        signal.signal(signum, signal.SIG_DFL)
        if exiting.is_set():
            return
        exiting.set()
        # The SDK exports in the calling thread with no deadline of its own;
        # a daemon thread bounds the wait and dies with the process.
        flusher = threading.Thread(target=provider.shutdown, name="otel-exit-flush", daemon=True)
        flusher.start()
        flusher.join(_EXIT_FLUSH_S)
        os.kill(os.getpid(), signum)

    # atexit runs last-registered first: this marks the exit before the
    # provider's own shutdown (registered when it was built) starts flushing.
    atexit.register(exiting.set)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _on_sigterm)


def attach_env_parent(settings: Settings, environ: Mapping[str, str] | None = None) -> object | None:
    """Make the W3C ``TRACEPARENT`` (and ``TRACESTATE``) of the process
    environment the current trace context, so every span the server opens
    afterwards — FastMCP's per-request spans included — joins the caller's
    trace. Call before the server's event loop starts: asyncio copies the
    context into the tasks it creates. Only when both ``otel_enabled`` and
    ``otel_traceparent_from_env`` are set and a valid TRACEPARENT is present;
    returns the context token (for ``opentelemetry.context.detach``) or
    ``None`` when nothing was attached. A request that carries its own
    ``_meta`` traceparent is still parented here: FastMCP keeps an existing
    valid context."""
    if not (settings.otel_enabled and settings.otel_traceparent_from_env):
        return None
    env = os.environ if environ is None else environ
    carrier = {k.lower(): env[k] for k in ("TRACEPARENT", "TRACESTATE") if env.get(k)}
    if "traceparent" not in carrier:
        return None
    from opentelemetry import context, trace
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    ctx = TraceContextTextMapPropagator().extract(carrier)
    if not trace.get_current_span(ctx).get_span_context().is_valid:
        logger.warning("TRACEPARENT is not a valid W3C trace context; spans start their own trace")
        return None
    token = context.attach(ctx)
    logger.info("OpenTelemetry spans parented under the TRACEPARENT of the environment")
    return token


def configure_telemetry(settings: Settings) -> bool:
    """Install the global OTEL tracer provider when enabled; return whether one
    was installed. Logs the endpoint + service name only — never the token."""
    provider = build_tracer_provider(settings)
    if provider is None:
        return False
    from opentelemetry import trace  # always available (fastmcp pulls the api)

    trace.set_tracer_provider(provider)
    install_exit_flush(provider)
    logger.info(
        "OpenTelemetry span export enabled: endpoint=%s service=%s",
        _traces_endpoint(settings),
        settings.otel_service_name,
    )
    attach_env_parent(settings)
    return True
