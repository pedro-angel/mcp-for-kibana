"""Unit tests for the optional OpenTelemetry wiring (Phase C). No stack needed.

Covers the four contract points: default-off is a true no-op, env parsing,
enabled builds a correct provider, enabled-but-SDK-missing fails with an
actionable hint, and the secret token is never logged.
"""

import logging
import sys

import pytest

from kibana_mcp.config import Settings
from kibana_mcp.telemetry import build_tracer_provider, configure_telemetry

_OTEL_ENV = (
    "KIBANA_MCP_OTEL_ENABLED",
    "KIBANA_MCP_OTEL_ENDPOINT",
    "KIBANA_MCP_OTEL_SECRET_TOKEN",
    "KIBANA_MCP_OTEL_SERVICE_NAME",
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
    try:
        with caplog.at_level(logging.INFO, logger="kibana_mcp.telemetry"):
            assert configure_telemetry(s) is True
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "http://otel.invalid:4318/v1/traces" in logged
        assert "super-secret-token" not in logged
    finally:
        trace.get_tracer_provider().shutdown()
