"""The efficiency runner's third arm (with-mcp-es): MCP config with both
servers, allowedTools, the read-only Elasticsearch key and where that key may
and may not appear. No network, no Docker."""

from __future__ import annotations

import importlib
import json
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "experiment"))

er = importlib.import_module("efficiency_run")
hook = importlib.import_module("agent_signals_hook")
lt = importlib.import_module("live_telemetry")

ES_KEY = "ZXMta2V5LWlkOmVzLWtleS1zZWNyZXQtdmFsdWU="
ES = {"url": "http://localhost:19200", "api_key": ES_KEY, "container": "eff-es-mcp-test"}


def test_arms_are_additive():
    assert er.ARMS == ("with-mcp", "no-mcp", "with-mcp-es")
    assert er.SERVER_ARMS == ("with-mcp", "with-mcp-es")


def test_no_mcp_and_with_mcp_are_unchanged(tmp_path):
    assert er.arm_mcp("no-mcp", tmp_path / "m.json", "http://k", "kb") == ([], [])
    assert not (tmp_path / "m.json").exists()
    args, allowed = er.arm_mcp("with-mcp", tmp_path / "m.json", "http://k", "kb", None, ES)
    assert args == ["--mcp-config", str(tmp_path / "m.json")]
    assert allowed == ["mcp__mcp-for-kibana"]
    servers = json.loads((tmp_path / "m.json").read_text())["mcpServers"]
    assert list(servers) == ["mcp-for-kibana"]
    assert ES_KEY not in (tmp_path / "m.json").read_text()


def test_with_mcp_es_registers_both_servers(tmp_path):
    otel = {"KIBANA_MCP_OTEL_ENABLED": "true"}
    args, allowed = er.arm_mcp("with-mcp-es", tmp_path / "m.json", "http://k", "kb", otel, ES)
    assert args == ["--mcp-config", str(tmp_path / "m.json")]
    assert allowed == ["mcp__mcp-for-kibana", "mcp__elasticsearch"]
    servers = json.loads((tmp_path / "m.json").read_text())["mcpServers"]
    assert list(servers) == ["mcp-for-kibana", "elasticsearch"]
    kb = servers["mcp-for-kibana"]
    assert kb["env"]["KIBANA_API_KEY"] == "kb" and kb["env"]["KIBANA_MCP_OTEL_ENABLED"] == "true"
    assert ES_KEY not in json.dumps(kb)
    es = servers["elasticsearch"]
    assert es["command"] == "docker"
    assert er.ES_MCP_IMAGE in es["args"] and es["args"][-1] == "stdio"
    assert "@sha256:" in er.ES_MCP_IMAGE  # pinned by digest
    assert ["--name", "eff-es-mcp-test"] == es["args"][3:5]
    assert "--network" in es["args"] and "--rm" in es["args"]
    # The key travels by name only: never on the docker command line.
    assert ES_KEY not in " ".join(es["args"])
    assert es["args"].count("-e") == 2 and "ES_API_KEY" in es["args"]
    assert es["env"] == {"ES_URL": "http://127.0.0.1:19200", "ES_API_KEY": ES_KEY}
    # No OTel for the ES server: it has none (tracing logs only).
    assert not any(k.startswith(("OTEL_", "KIBANA_MCP_OTEL")) for k in es["env"])
    # The config holds keys: owner-only.
    assert stat.S_IMODE((tmp_path / "m.json").stat().st_mode) == 0o600


def test_with_mcp_es_needs_the_es_server(tmp_path):
    with pytest.raises(ValueError, match="with-mcp-es arm needs"):
        er.arm_mcp("with-mcp-es", tmp_path / "m.json", "http://k", "kb")
    with pytest.raises(ValueError, match="unknown arm"):
        er.arm_mcp("with-es", tmp_path / "m.json", "http://k", "kb")


def test_es_key_request_is_read_only_on_the_logs_index():
    body = er.es_key_request("eff-run")
    assert body["name"] == "eff-run" and body["expiration"] == er.ES_KEY_EXPIRATION
    (role,) = body["role_descriptors"].values()
    assert role["cluster"] == ["monitor"]
    (idx,) = role["indices"]
    assert idx["names"] == [er.LOGS_INDEX]
    assert set(idx["privileges"]) == {"read", "view_index_metadata", "monitor"}
    writes = {"all", "write", "index", "create", "create_doc", "delete", "manage", "create_index"}
    assert not writes & set(idx["privileges"])
    assert not {"all", "manage", "manage_security", "manage_api_key", "manage_own_api_key"} & set(
        role["cluster"]
    )


def test_load_es_admin_reads_the_stack_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "ES_LOCAL_PORT=19200\nES_LOCAL_URL=http://localhost:${ES_LOCAL_PORT}\nES_LOCAL_PASSWORD=pw\n"
    )
    assert er.load_es_admin(env) == ("http://localhost:19200", "pw")
    env.write_text("ES_LOCAL_PORT=19200\n")
    with pytest.raises(SystemExit, match="ES_LOCAL_PASSWORD missing"):
        er.load_es_admin(env)


def test_mint_failure_names_the_fix(monkeypatch):
    monkeypatch.setattr(
        er, "_es_call", lambda *a: (401, {"error": {"reason": "unable to authenticate"}})
    )
    with pytest.raises(SystemExit, match="scripts/stack.sh status"):
        er.mint_es_key("http://es", "pw", "n")


def test_invalidate_checks_the_answer(monkeypatch):
    monkeypatch.setattr(er, "_es_call", lambda *a: (200, {"invalidated_api_keys": ["id1"]}))
    assert er.invalidate_es_key("http://es", "pw", "id1")
    monkeypatch.setattr(er, "_es_call", lambda *a: (200, {"invalidated_api_keys": []}))
    assert not er.invalidate_es_key("http://es", "pw", "id1")


def test_relay_scrubber_removes_the_es_key():
    sc = lt.Scrubber(["kb-key", "", ES_KEY])
    body = {"x": [{"stringValue": f"Authorization: ApiKey {ES_KEY}"}]}
    assert ES_KEY not in json.dumps(sc.scrub(body))


def test_hook_names_the_mcp_server():
    sig = {
        "agent.tool.outside_workdir_paths": [],
        "agent.secret.in_arguments": {},
        "agent.secret.in_result": {},
        "agent.tool.failed": False,
    }
    es = hook.tool_attributes({"name": "mcp__elasticsearch__esql", "id": "t1"}, sig)
    assert es["agent.tool.mcp_server"] == "elasticsearch"
    assert es["gen_ai.tool.type"] == "extension"
    kb = hook.tool_attributes({"name": "mcp__mcp-for-kibana__create_dashboard", "id": "t2"}, sig)
    assert kb["agent.tool.mcp_server"] == "mcp-for-kibana"
    assert "agent.tool.mcp_server" not in hook.tool_attributes({"name": "Bash", "id": "t3"}, sig)


def test_es_tools_count_as_mcp_calls():
    ea = importlib.import_module("efficiency_analyze")
    assert ea.tool_category("mcp__elasticsearch__search") == "mcp"


def test_root_span_carries_the_es_arm_fields():
    record = {
        "run_id": "r",
        "arm": "with-mcp-es",
        "es_mcp_image": er.ES_MCP_IMAGE,
        "n_es_mcp_calls": 3,
        "es_key_in_output": False,
        "es_key_invalidated": True,
        "success": True,
    }
    body = lt.runner_spans(record, "0" * 32, "1" * 16, {"agent": (1.0, 2.0)}, "svc", {})
    keys = {a["key"] for a in body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]}
    assert {"study.es_mcp_image", "study.n_es_mcp_calls", "study.es_key_invalidated"} <= keys
