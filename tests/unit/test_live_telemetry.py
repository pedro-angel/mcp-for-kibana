"""The efficiency study's live-telemetry mode (scripts/experiment/live_telemetry.py
and efficiency_run.py's live hooks), against a fake OTLP upstream on the
loopback: env building, resource attributes, redaction, the relay and the
runner's root span. No network beyond 127.0.0.1, no claude, no Kibana.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "experiment"))

lt = importlib.import_module("live_telemetry")
sink = importlib.import_module("otlp_sink")
er = importlib.import_module("efficiency_run")

KIBANA_KEY = "VnVhQ2ZHY0JDZGJrUXctMjBpZGQ6dWkybHAyYXhUTm1zeXFhd3FRclFsQQ=="
HC_KEY = "hcaik_01jx7Qp4Lm2Nn8Rr6Tt0Vv9Ww3Yy5Zz1Aa"
ATTRS = lt.resource_attributes("run-1", "claude-haiku-5-5", "with-mcp", "b01")


class FakeUpstream:
    """Records what the relay forwards, with headers."""

    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(
                    {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "raw": raw}
                )
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def upstream():
    u = FakeUpstream()
    yield u
    u.close()


def _log_payload(text: str) -> dict:
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "x"}}]},
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "body": {"stringValue": "claude_code.tool_result"},
                                "attributes": [
                                    {"key": "event.name", "value": {"stringValue": "tool_result"}},
                                    {"key": "tool_input", "value": {"stringValue": text}},
                                    {"key": "user.email", "value": {"stringValue": "a@b.c"}},
                                    {"key": "organization.id", "value": {"stringValue": "org"}},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }


# ---------------------------------------------------------------- attributes


def test_resource_attributes_carry_run_and_conversation_id():
    assert ATTRS == {
        "study.run_id": "run-1",
        "study.model": "claude-haiku-5-5",
        "study.arm": "with-mcp",
        "study.block": "b01",
        "gen_ai.conversation.id": "run-1",
    }


def test_encode_resource_attributes_percent_encodes_values():
    enc = lt.encode_resource_attributes({"a.b": "x y,z=1", "c": "plain-1_2"})
    assert enc == "a.b=x%20y%2Cz%3D1,c=plain-1_2"
    assert " " not in enc


def test_traceparent_format():
    tid, sid = lt.new_trace()
    assert len(tid) == 32 and len(sid) == 16
    assert lt.traceparent(tid, sid) == f"00-{tid}-{sid}-01"


# ---------------------------------------------------------------- env


def test_claude_env_sets_documented_switches_and_strips_secrets():
    base = {
        "PATH": "/usr/bin",
        "HONEYCOMB_API_KEY": HC_KEY,
        "HONEYCOMB_DATASET": "x",
        "OTEL_EXPORTER_OTLP_HEADERS": "x-honeycomb-team=" + HC_KEY,
        "OTEL_LOG_RAW_API_BODIES": "1",
        "TRACEPARENT": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        "KIBANA_TEST_API_KEY": KIBANA_KEY,
    }
    env = lt.claude_env(base, "http://127.0.0.1:9", "svc", ATTRS, "00-a-b-01")
    assert HC_KEY not in json.dumps(env)
    assert "OTEL_LOG_RAW_API_BODIES" not in env
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in env
    assert env["TRACEPARENT"] == "00-a-b-01"
    assert env["PATH"] == "/usr/bin"
    assert env["KIBANA_TEST_API_KEY"] == KIBANA_KEY  # the arm's own business
    for k, v in {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        "OTEL_SERVICE_NAME": "svc",
        "OTEL_LOG_USER_PROMPTS": "1",
        "OTEL_LOG_ASSISTANT_RESPONSES": "1",
        "OTEL_LOG_TOOL_DETAILS": "1",
        "OTEL_LOG_TOOL_CONTENT": "1",
    }.items():
        assert env[k] == v, k
    assert "study.component=claude-code" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "gen_ai.conversation.id=run-1" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert int(env["OTEL_METRIC_EXPORT_INTERVAL"]) <= 10000
    with_metrics = lt.claude_env(base, "http://127.0.0.1:9", "svc", ATTRS, "00-a-b-01", metrics=True)
    assert with_metrics["OTEL_METRICS_EXPORTER"] == "otlp"


def test_server_env_enables_export_and_env_parent():
    env = lt.server_env("http://127.0.0.1:9", "svc", ATTRS)
    assert env["KIBANA_MCP_OTEL_ENABLED"] == "true"
    assert env["KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV"] == "true"
    assert env["KIBANA_MCP_OTEL_ENDPOINT"] == "http://127.0.0.1:9"
    assert env["KIBANA_MCP_OTEL_SERVICE_NAME"] == "svc"
    assert "study.component=mcp-server" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert not any("HONEYCOMB" in k or HC_KEY in v for k, v in env.items())


def test_mcp_config_carries_server_otel_env(tmp_path):
    path = er.mcp_config(tmp_path / "m.json", "http://k", "key", {"KIBANA_MCP_OTEL_ENABLED": "true"})
    env = json.loads(path.read_text())["mcpServers"]["mcp-for-kibana"]["env"]
    assert env["KIBANA_MCP_OTEL_ENABLED"] == "true"
    # default unchanged for the first study
    plain = er.mcp_config(tmp_path / "p.json", "http://k", "key")
    assert "KIBANA_MCP_OTEL_ENABLED" not in plain.read_text()


# ---------------------------------------------------------------- redaction


def test_scrubber_replaces_secrets_and_drops_identity():
    sc = lt.Scrubber([KIBANA_KEY, HC_KEY, "short"])
    out = sc.scrub(_log_payload(f"curl -H 'Authorization: ApiKey {KIBANA_KEY}' ; echo {HC_KEY}"))
    text = json.dumps(out)
    assert KIBANA_KEY not in text and HC_KEY not in text
    assert text.count(lt.REDACTED) == 2
    assert "user.email" not in text and "organization.id" not in text
    assert "event.name" in text
    assert sc.replaced == 2 and sc.dropped == 2


def test_scrubber_keep_identity():
    sc = lt.Scrubber([KIBANA_KEY], drop_keys=())
    assert "user.email" in json.dumps(sc.scrub(_log_payload("x")))


def test_scrub_request_round_trips_protobuf():
    pytest.importorskip("opentelemetry.proto")
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

    raw = ParseDict(
        _log_payload("key " + KIBANA_KEY), logs_service_pb2.ExportLogsServiceRequest()
    ).SerializeToString()
    data, body = lt.scrub_request("/v1/logs", "application/x-protobuf", raw, lt.Scrubber([KIBANA_KEY]))
    msg = logs_service_pb2.ExportLogsServiceRequest()
    msg.ParseFromString(data)
    assert KIBANA_KEY.encode() not in data
    attrs = {a.key: a.value.string_value for a in msg.resource_logs[0].scope_logs[0].log_records[0].attributes}
    assert attrs["tool_input"] == "key " + lt.REDACTED
    assert "user.email" not in attrs


# ---------------------------------------------------------------- relay


def _post(url: str, body: dict) -> int:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def test_relay_scrubs_tees_and_forwards_with_headers(tmp_path, upstream):
    tee = tmp_path / "telemetry.jsonl"
    relay = lt.Relay(tee, lt.Scrubber([KIBANA_KEY]), upstream.url, HC_KEY, "ds-live").start()
    try:
        assert _post(relay.url + "/v1/logs", _log_payload("k=" + KIBANA_KEY)) == 200
    finally:
        relay.stop()
    (fwd,) = upstream.requests
    assert fwd["path"] == "/v1/logs"
    assert fwd["headers"]["x-honeycomb-team"] == HC_KEY
    assert fwd["headers"]["x-honeycomb-dataset"] == "ds-live"
    assert KIBANA_KEY.encode() not in fwd["raw"]
    kept = tee.read_text()
    assert KIBANA_KEY not in kept and HC_KEY not in kept  # tee never holds the team key
    assert relay.stats == {"requests": 1, "forwarded": 1, "failed": 0, "by_path": {"/v1/logs": 1}}
    # the copy reads back with the sink helpers
    (lr,) = sink.log_records(sink.load(tee))
    assert lr["attrs"]["tool_input"] == "k=" + lt.REDACTED


def test_relay_without_upstream_keeps_copy_only(tmp_path):
    tee = tmp_path / "t.jsonl"
    relay = lt.Relay(tee, lt.Scrubber([])).start()
    try:
        assert _post(relay.url + "/v1/logs", _log_payload("x")) == 200
    finally:
        relay.stop()
    assert relay.stats["forwarded"] == 0 and relay.stats["requests"] == 1
    assert json.loads(tee.read_text())["forwarded"] is False


def test_relay_reports_upstream_failure(tmp_path):
    relay = lt.Relay(tmp_path / "t.jsonl", lt.Scrubber([]), "http://127.0.0.1:9", HC_KEY).start()
    try:
        assert _post(relay.url + "/v1/traces", {"resourceSpans": []}) == 200
    finally:
        relay.stop()
    assert relay.stats["failed"] == 1 and relay.stats["forwarded"] == 0
    assert "forward failed" in relay.stats["errors"][0]


def test_enrich_adds_genai_names_without_overwriting():
    body = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "claude_code.llm_request",
                                "attributes": [
                                    {"key": "input_tokens", "value": {"intValue": "5"}},
                                    {"key": "gen_ai.provider.name", "value": {"stringValue": "x"}},
                                ],
                            },
                            {"name": "claude_code.tool", "attributes": [
                                {"key": "tool_name", "value": {"stringValue": "Bash"}}]},
                            {"name": "other", "attributes": []},
                        ]
                    }
                ]
            }
        ]
    }
    assert lt.enrich(body, "claude-code with-mcp") == 2
    llm, tool, other = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    a = {x["key"]: x["value"] for x in llm["attributes"]}
    assert a["gen_ai.operation.name"] == {"stringValue": "chat"}
    assert a["gen_ai.usage.input_tokens"] == {"intValue": "5"}
    assert a["gen_ai.provider.name"] == {"stringValue": "x"}  # kept
    t = {x["key"]: x["value"] for x in tool["attributes"]}
    assert t["gen_ai.operation.name"] == {"stringValue": "execute_tool"}
    assert t["gen_ai.tool.name"] == {"stringValue": "Bash"}
    assert other["attributes"] == []


# ---------------------------------------------------------------- root span


def test_runner_spans_root_and_phases():
    record = {
        "run_id": "run-1",
        "arm": "with-mcp",
        "success": True,
        "num_turns": 7,
        "total_cost_usd": 0.12,
        "server_rev": "abc1234",
        "server_dirty": False,
        "key_in_model_env": True,
        "harness_error": None,
    }
    body = lt.runner_spans(
        record, "a" * 32, "b" * 16, {"agent": (10.0, 20.0), "score": (20.0, 21.0)}, "svc", ATTRS
    )
    rs = body["resourceSpans"][0]
    res = {a["key"]: a["value"]["stringValue"] for a in rs["resource"]["attributes"]}
    assert res["service.name"] == "svc" and res["study.component"] == "runner"
    assert res["gen_ai.conversation.id"] == "run-1"
    root, *kids = rs["scopeSpans"][0]["spans"]
    assert root["name"] == "efficiency.run" and "parentSpanId" not in root
    assert (root["startTimeUnixNano"], root["endTimeUnixNano"]) == (str(10**10), str(21 * 10**9))
    attrs = {a["key"]: next(iter(a["value"].values())) for a in root["attributes"]}
    assert attrs["study.success"] is True
    assert attrs["study.num_turns"] == "7"
    assert attrs["study.server_rev"] == "abc1234"
    assert attrs["study.server_dirty"] is False
    assert root["status"]["code"] == 1
    assert [k["name"] for k in kids] == ["efficiency.agent", "efficiency.score"]
    assert all(k["parentSpanId"] == "b" * 16 and k["traceId"] == "a" * 32 for k in kids)


# ---------------------------------------------------------------- parked fixes


def _stream(*events) -> list[str]:
    return [json.dumps(e) for e in events]


def test_parse_stream_first_result_only_in_live_mode():
    lines = _stream(
        {"type": "system", "subtype": "init", "tools": []},
        {"type": "system", "subtype": "permission_denied", "tool_use_id": "t1"},
        {"type": "result", "num_turns": 12, "permission_denials": []},
        {"type": "result", "num_turns": 1, "permission_denials": [{"tool_use_id": "t2"}]},
    )
    legacy = er.parse_stream(lines)
    live = er.parse_stream(lines, first_result=True)
    assert legacy["num_turns"] == 1  # the first study's behaviour, unchanged
    assert live["num_turns"] == 12
    assert live["n_result_events"] == 2
    assert live["permission_denials_stream"] == ["t1", "t2"]


def test_prompt_versions():
    v1, v2 = er.task_prompt("M", 1), er.task_prompt("M", 2)
    assert v1 == er.task_prompt("M")  # default unchanged
    assert v1.endswith("Finish when the dashboard and the rule both exist.")
    assert v2.endswith("the rule is enabled.")


# ---------------------------------------------------------------- agent-signals hook

hook = importlib.import_module("agent_signals_hook")
TP = "00-" + "c" * 32 + "-" + "d" * 16 + "-01"


def _payloads() -> list[dict]:
    base = {"session_id": "s", "cwd": "/w", "transcript_path": "/x", "permission_mode": "dontAsk"}
    curl = 'curl -s -H "Authorization: ApiKey $KIBANA_TEST_API_KEY" "$KIBANA_URL/api/saved_objects/dashboard/nope"'
    return [
        {**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "t1",
         "tool_input": {"command": curl}, "duration_ms": 120,
         "tool_response": {"stdout": '{"statusCode":404,"error":"Not Found","message":"x"}',
                           "stderr": "", "interrupted": False}},
        {**base, "hook_event_name": "PostToolUseFailure", "tool_name": "mcp__k__create_dashboard",
         "tool_use_id": "t2", "tool_input": {"title": "a"}, "duration_ms": 30, "is_interrupt": False,
         "error": "1 validation error for call[create_dashboard]\npanels.0.title\n  Field required"},
        {**base, "hook_event_name": "PostToolUse", "tool_name": "mcp__k__create_dashboard",
         "tool_use_id": "t3", "tool_input": {"title": "b"}, "duration_ms": 40,
         "tool_response": [{"type": "text", "text": "{\"id\": \"d1\"}"}]},
        {**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "t4",
         "tool_input": {"command": "env | grep KIB"}, "duration_ms": 5,
         "tool_response": {"stdout": f"KIBANA_TEST_API_KEY={KIBANA_KEY}", "stderr": ""}},
        {**base, "hook_event_name": "Stop", "last_assistant_message": "done"},
    ]  # fmt: skip


def _run_hook(cfg: Path, payload: dict, env: dict | None = None):
    import subprocess

    return subprocess.run(
        [sys.executable, "-I", "-S", str(REPO / "scripts/experiment/agent_signals_hook.py"), str(cfg)],
        input=json.dumps(payload), capture_output=True, text=True,
        env={"PATH": "/usr/bin", **(env or {})}, timeout=30, check=False,
    )  # fmt: skip


def test_hook_is_silent_classifies_live_and_joins_the_trace(tmp_path):
    tee = tmp_path / "tee.jsonl"
    relay = lt.Relay(tee, lt.Scrubber([KIBANA_KEY]), agent_name="claude-code with-mcp").start()
    try:
        cfg = er.write_hook_config(tmp_path, relay.url, "svc", ATTRS, KIBANA_KEY)
        assert oct(cfg.stat().st_mode & 0o777) == "0o600"
        for p in _payloads():
            proc = _run_hook(cfg, p, {"TRACEPARENT": TP})
            assert (proc.stdout, proc.stderr, proc.returncode) == ("", "", 0)
    finally:
        relay.stop()
    assert not Path(str(cfg) + ".errors").exists()
    spans = {s["attrs"].get("gen_ai.tool.call.id") or s["name"]: s for s in sink.spans(sink.load(tee))}
    assert all(s["traceId"] == "c" * 32 and s["parentSpanId"] == "d" * 16 for s in spans.values())
    curl = spans["t1"]["attrs"]
    assert curl["agent.tool.hidden_failure"] is True and curl["agent.tool.http_status"] == "404"
    assert curl["agent.tool.shell_http"] is True and curl["error.type"] == "http_4xx"
    bad = spans["t2"]["attrs"]
    assert bad["agent.tool.first_attempt_failed"] is True
    assert bad["agent.tool.error_class"] == "validation"
    good = spans["t3"]["attrs"]
    assert good["agent.tool.retry_of"] == "t2" and good["agent.tool.recovers"] == "t2"
    dump = spans["t4"]["attrs"]
    assert dump["agent.tool.env_enumeration"] is True
    assert dump["agent.secret.known_exposed"] is True and dump["agent.secret.location"] == "tool_result"
    assert spans["agent.turn_end"]["attrs"]["agent.secret.exposed"] is False
    assert spans["t1"]["resource"]["study.component"] == "hook"
    assert KIBANA_KEY not in tee.read_text()

    state = json.loads((tmp_path / "agent_hook_state.json").read_text())
    roll = hook.rollups(state, KIBANA_KEY)
    assert roll["agent.tool.calls"] == 4
    assert roll["agent.tool.hidden_failures"] == 1
    assert roll["agent.tool.first_attempt_failures"] == 2  # curl target, create_dashboard
    assert roll["agent.tool.failures_recovered"] == 1
    assert roll["agent.tool.env_enumeration_calls"] == 1
    assert roll["agent.secret.known_exposed"] is True
    assert roll["agent.hook.invocations"] == 5


def test_hook_failure_stays_silent(tmp_path):
    proc = _run_hook(tmp_path / "missing.json", _payloads()[0])
    assert (proc.stdout, proc.stderr, proc.returncode) == ("", "", 0)
    assert "Traceback" in (tmp_path / "missing.json.errors").read_text()


def test_hook_matches_the_transcript_classifier():
    """Same call, same verdict: the hook's to_call feeds efficiency_analyze."""
    ea = importlib.import_module("efficiency_analyze")
    call = hook.to_call(_payloads()[0])
    assert ea.classify(call, set())["error_class"] == "http_4xx"
    assert hook.to_call({"hook_event_name": "Stop"}) is None


def test_hook_settings_register_silent_command(tmp_path):
    cfg = er.write_hook_config(tmp_path, "http://127.0.0.1:9", "svc", ATTRS, KIBANA_KEY)
    st = er.hook_settings(cfg)
    assert set(st["hooks"]) == {"PostToolUse", "PostToolUseFailure", "PermissionDenied", "Stop"}
    (entry,) = st["hooks"]["PostToolUse"]
    assert entry["matcher"] == "*"
    assert "agent_signals_hook.py" in entry["hooks"][0]["command"]
    assert KIBANA_KEY not in json.dumps(st)


def test_model_text_secrets_marked_before_scrubbing():
    body = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": [
        {"key": "event.name", "value": {"stringValue": "assistant_response"}},
        {"key": "response", "value": {"stringValue": f"the key is {KIBANA_KEY}"}},
    ]}]}]}]}  # fmt: skip
    data, out = lt.scrub_request(
        "/v1/logs", "application/json", json.dumps(body).encode(), lt.Scrubber([KIBANA_KEY]),
        agent_name="a", known=KIBANA_KEY,
    )  # fmt: skip
    attrs = {a["key"]: next(iter(a["value"].values())) for a in
             out["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]}  # fmt: skip
    assert attrs["agent.secret.known_exposed"] is True
    assert attrs["agent.secret.location"] == "model_text"
    assert KIBANA_KEY.encode() not in data
