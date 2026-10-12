"""The no-mcp arm's trace anchor (live mode): a tool-less stdio MCP server."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "experiment"))

import efficiency_run as er  # noqa: E402
import trace_anchor_server as tas  # noqa: E402


def test_no_mcp_without_anchor_is_unchanged(tmp_path):
    assert er.arm_mcp("no-mcp", tmp_path / "m.json", "http://k", "kb") == ([], [])
    assert not (tmp_path / "m.json").exists()


def test_no_mcp_anchor_adds_a_server_and_no_tools(tmp_path):
    args, allowed = er.arm_mcp("no-mcp", tmp_path / "m.json", "http://k", "kb", trace_anchor=True)
    assert args == ["--mcp-config", str(tmp_path / "m.json")]
    assert allowed == []
    text = (tmp_path / "m.json").read_text()
    servers = json.loads(text)["mcpServers"]
    assert list(servers) == [er.TRACE_ANCHOR_SERVER]
    assert servers[er.TRACE_ANCHOR_SERVER]["args"][-1].endswith("trace_anchor_server.py")
    assert "kb" not in text and "env" not in servers[er.TRACE_ANCHOR_SERVER]


def test_anchor_flag_does_not_touch_server_arms(tmp_path):
    a = er.arm_mcp("with-mcp", tmp_path / "a.json", "http://k", "kb", trace_anchor=True)
    assert a[1] == ["mcp__mcp-for-kibana"]
    assert list(json.loads((tmp_path / "a.json").read_text())["mcpServers"]) == ["mcp-for-kibana"]


def test_anchor_answers_with_nothing_to_offer():
    init = tas.answer({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["capabilities"] == {}
    assert tas.answer({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"] == {"tools": []}
    assert tas.answer({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert tas.answer({"jsonrpc": "2.0", "id": 3, "method": "tools/call"})["error"]["code"] == -32601


def test_anchor_speaks_stdio():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    entry = er.trace_anchor_server()
    out = subprocess.run(
        [entry["command"], *entry["args"]],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.splitlines()
    replies = [json.loads(line) for line in out]
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[1]["result"]["tools"] == []
