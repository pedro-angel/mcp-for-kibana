"""Unit tests for the optional OpenTelemetry wiring (Phase C). No stack needed.

Covers the four contract points: default-off is a true no-op, env parsing,
enabled builds a correct provider, enabled-but-SDK-missing fails with an
actionable hint, and the secret token is never logged.
"""

import logging
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from kibana_mcp.config import Settings
from kibana_mcp.telemetry import build_tracer_provider, configure_telemetry

_OTEL_ENV = (
    "KIBANA_MCP_OTEL_ENABLED",
    "KIBANA_MCP_OTEL_ENDPOINT",
    "KIBANA_MCP_OTEL_SECRET_TOKEN",
    "KIBANA_MCP_OTEL_SERVICE_NAME",
    "KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV",
)


@pytest.fixture(autouse=True)
def _clear_otel_env(monkeypatch):
    for var in _OTEL_ENV:
        monkeypatch.delenv(var, raising=False)


def test_disabled_by_default():
    s = Settings()
    assert s.otel_enabled is False
    assert build_tracer_provider(s) is None
    assert configure_telemetry(s) is False


def test_settings_parse_otel_env(monkeypatch):
    monkeypatch.setenv("KIBANA_MCP_OTEL_ENABLED", "true")
    monkeypatch.setenv("KIBANA_MCP_OTEL_ENDPOINT", "http://apm:9999")
    monkeypatch.setenv("KIBANA_MCP_OTEL_SECRET_TOKEN", "tok")
    monkeypatch.setenv("KIBANA_MCP_OTEL_SERVICE_NAME", "svc")
    s = Settings()
    assert s.otel_enabled is True
    assert s.otel_endpoint == "http://apm:9999"
    assert s.otel_secret_token == "tok"
    assert s.otel_service_name == "svc"


def test_enabled_builds_provider_with_service_name():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider

    # Unroutable endpoint: this is a unit test, it must never reach real infra.
    s = Settings(
        otel_enabled=True,
        otel_endpoint="http://otel.invalid:4318/",
        otel_service_name="mcp-for-kibana-test",
    )
    provider = build_tracer_provider(s)
    try:
        assert isinstance(provider, TracerProvider)
        assert provider.resource.attributes["service.name"] == "mcp-for-kibana-test"
    finally:
        provider.shutdown()  # stop the BatchSpanProcessor's background thread


def test_enabled_but_sdk_missing_raises(monkeypatch):
    # Simulate the `otel` extra not being installed: null the SDK submodule so
    # its import inside build_tracer_provider raises ImportError.
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)
    s = Settings(otel_enabled=True)
    with pytest.raises(RuntimeError, match=r"mcp-for-kibana\[otel\]"):
        build_tracer_provider(s)


def test_configure_telemetry_logs_endpoint_never_token(caplog):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace

    # Unroutable endpoint (never a live APM) so the installed global provider
    # can't export over the network during/after the run; shut it down after.
    s = Settings(
        otel_enabled=True,
        otel_endpoint="http://otel.invalid:4318",
        otel_secret_token="super-secret-token",
        otel_service_name="mcp-for-kibana",
    )
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    try:
        with caplog.at_level(logging.INFO, logger="kibana_mcp.telemetry"):
            assert configure_telemetry(s) is True
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "http://otel.invalid:4318/v1/traces" in logged
        assert "super-secret-token" not in logged
        # configure_telemetry also installs the SIGTERM flush.
        assert signal.getsignal(signal.SIGTERM) is not previous_sigterm
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        trace.get_tracer_provider().shutdown()


def _schedule_delay_ms(provider) -> float:
    (bsp,) = provider._active_span_processor._span_processors
    return bsp._batch_processor._schedule_delay_millis


def test_spans_are_exported_within_half_a_second_by_default(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    # The SDK's 5 s default outlived a stdio client's teardown (SIGINT, SIGTERM
    # ~100 ms later, SIGKILL ~400 ms after that), losing a session's last spans.
    monkeypatch.delenv("OTEL_BSP_SCHEDULE_DELAY", raising=False)
    provider = build_tracer_provider(Settings(otel_enabled=True, otel_endpoint="http://otel.invalid"))
    try:
        assert _schedule_delay_ms(provider) == 500
    finally:
        provider.shutdown()


def test_explicit_bsp_schedule_delay_wins(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "2500")
    provider = build_tracer_provider(Settings(otel_enabled=True, otel_endpoint="http://otel.invalid"))
    try:
        assert _schedule_delay_ms(provider) == 2500
    finally:
        provider.shutdown()


_SIGTERM_CHILD = textwrap.dedent(
    """
    import sys, time
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult, SpanExporter
    from kibana_mcp.telemetry import install_exit_flush

    out = sys.argv[1]

    class FileExporter(SpanExporter):
        def export(self, spans):
            with open(out, "a") as f:
                f.writelines(s.name + "\\n" for s in spans)
            return SpanExportResult.SUCCESS

    provider = TracerProvider()
    # A delay no test outlives: the span can only leave through the flush.
    provider.add_span_processor(BatchSpanProcessor(FileExporter(), schedule_delay_millis=600_000))
    install_exit_flush(provider)
    with provider.get_tracer("t").start_as_current_span("tools/call last"):
        pass
    print("ready", flush=True)
    time.sleep(60)
    """
)


def test_sigterm_flushes_queued_spans_then_dies_by_sigterm(tmp_path):
    pytest.importorskip("opentelemetry.sdk")
    out = tmp_path / "spans.txt"
    child = subprocess.Popen(
        [sys.executable, "-c", _SIGTERM_CHILD, str(out)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=20) == -signal.SIGTERM
    finally:
        child.kill()
        child.stdout.close()
    assert out.read_text().splitlines() == ["tools/call last"]


_HANGING_CHILD = textwrap.dedent(
    """
    import sys, threading, time
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
    from kibana_mcp import telemetry

    telemetry._EXIT_FLUSH_S = float(sys.argv[1])

    class DeadCollector(SpanExporter):
        def export(self, spans):
            time.sleep(600)

    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(DeadCollector(), schedule_delay_millis=600_000))
    telemetry.install_exit_flush(provider)
    with provider.get_tracer("t").start_as_current_span("tools/call last"):
        pass
    print("ready", flush=True)
    time.sleep(60)
    """
)


def _hanging_child(bound_s: float) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _HANGING_CHILD, str(bound_s)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )


def test_sigterm_flush_is_bounded_when_the_collector_hangs():
    pytest.importorskip("opentelemetry.sdk")
    # An unreachable collector must not hold the exit for the exporter's timeout.
    child = _hanging_child(0.5)
    try:
        assert child.stdout.readline().strip() == "ready"
        started = time.monotonic()
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=20) == -signal.SIGTERM
        assert time.monotonic() - started < 5
    finally:
        child.kill()
        child.stdout.close()


def test_second_sigterm_ends_a_flush_at_once():
    pytest.importorskip("opentelemetry.sdk")
    child = _hanging_child(600)
    try:
        assert child.stdout.readline().strip() == "ready"
        child.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        started = time.monotonic()
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=20) == -signal.SIGTERM
        assert time.monotonic() - started < 5
    finally:
        child.kill()
        child.stdout.close()


# --- TRACEPARENT from the environment (additive, default-off) ---------------

_TP = "00-0af7651916cd43a8f2c4d1e5b6a7c8d9-b7ad6b7169203331-01"


def test_traceparent_from_env_is_off_by_default():
    from kibana_mcp.telemetry import attach_env_parent

    assert Settings().otel_traceparent_from_env is False
    s = Settings(otel_enabled=True)
    assert attach_env_parent(s, {"TRACEPARENT": _TP}) is None


def test_traceparent_from_env_needs_otel_enabled():
    from kibana_mcp.telemetry import attach_env_parent

    s = Settings(otel_traceparent_from_env=True)
    assert attach_env_parent(s, {"TRACEPARENT": _TP}) is None


def test_traceparent_from_env_parses_env(monkeypatch):
    monkeypatch.setenv("KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV", "true")
    assert Settings().otel_traceparent_from_env is True


def test_traceparent_from_env_parents_new_spans():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from kibana_mcp.telemetry import attach_env_parent

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    s = Settings(otel_enabled=True, otel_traceparent_from_env=True)
    token = attach_env_parent(s, {"TRACEPARENT": _TP})
    assert token is not None
    try:
        with provider.get_tracer("t").start_as_current_span("tools/call x"):
            pass
    finally:
        context.detach(token)
    (span,) = exporter.get_finished_spans()
    assert format(span.context.trace_id, "032x") == "0af7651916cd43a8f2c4d1e5b6a7c8d9"
    assert format(span.parent.span_id, "016x") == "b7ad6b7169203331"


@pytest.mark.parametrize("env", [{}, {"TRACEPARENT": "not-a-traceparent"}])
def test_traceparent_from_env_absent_or_invalid_attaches_nothing(env):
    pytest.importorskip("opentelemetry.sdk")
    from kibana_mcp.telemetry import attach_env_parent

    s = Settings(otel_enabled=True, otel_traceparent_from_env=True)
    assert attach_env_parent(s, env) is None
