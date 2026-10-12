"""Live telemetry for the efficiency study (``efficiency_run.py --live-telemetry``).

Every signal is emitted at run time by the component that owns it:

    Claude Code    spans (claude_code.interaction > llm_request, tool > tool.execution),
                   log events (user_prompt, assistant_response, api_request,
                   tool_decision, tool_result) and metrics, over OTLP/HTTP JSON
    MCP server     FastMCP's per-request spans (tools/call <name>, ...), parented
                   under the runner's root through TRACEPARENT
                   (KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV)
    runner         the root span efficiency.run (study.* attributes, outcome) and
                   child spans for scoring and reset

All three export to a local relay that the runner starts per run. The relay
scrubs known secrets and the account identity attributes out of every request,
keeps a copy of what it forwards (telemetry.jsonl in the run directory, in the
otlp_sink format) and forwards the request to Honeycomb with the team key. The
key therefore lives only in the runner: neither Claude Code, the model's shell
nor the MCP server ever sees it.

Findings that shaped this module are in LIVE_RERUN.md (smoke tests,
2026-10-11).
"""

from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import otlp_sink

DEFAULT_DATASET = "mcp-for-kibana-live"
DEFAULT_ENDPOINT = "https://api.eu1.honeycomb.io"

# Export intervals short enough that a -p run's signals are out before it
# exits (the smoke test saw all three signals flushed at exit with these).
INTERVALS_MS = {
    "OTEL_METRIC_EXPORT_INTERVAL": "5000",
    "OTEL_LOGS_EXPORT_INTERVAL": "1000",
    "OTEL_TRACES_EXPORT_INTERVAL": "1000",
}

# Claude Code stamps the signed-in account on every span, log and metric
# point. The relay drops these unless --keep-identity.
IDENTITY_ATTRS = ("user.email", "user.account_uuid", "user.account_id", "organization.id")

# Never handed to the measured claude process: the telemetry key (the relay
# holds it), ambient OTel settings and trace context of the parent session.
_STRIP_PREFIXES = ("HONEYCOMB_", "OTEL_")
_STRIP_EXACT = ("TRACEPARENT", "TRACESTATE", "OTEL_LOG_RAW_API_BODIES")

REDACTED = "[REDACTED]"


# ---------------------------------------------------------------- trace ids


def new_trace() -> tuple[str, str]:
    """A fresh (trace_id, root_span_id) in hex."""
    return secrets.token_hex(16), secrets.token_hex(8)


def traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"


# ---------------------------------------------------------------- attributes


def resource_attributes(
    run_id: str, model: str, arm: str, block: str, component: str | None = None
) -> dict[str, str]:
    """The study's resource attributes, identical across components (except
    study.component). gen_ai.conversation.id carries the run id: nothing in
    Claude Code's own telemetry identifies the run (session.id is the parent
    session's, shared by every run), and Honeycomb's Agent Timeline groups on
    it."""
    attrs = {
        "study.run_id": run_id,
        "study.model": model,
        "study.arm": arm,
        "study.block": block,
        "gen_ai.conversation.id": run_id,
    }
    if component:
        attrs["study.component"] = component
    return attrs


def encode_resource_attributes(attrs: dict[str, str]) -> str:
    """OTEL_RESOURCE_ATTRIBUTES: comma-separated key=value, values
    percent-encoded (no spaces, commas or equals signs survive unencoded)."""
    return ",".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in attrs.items())


# ---------------------------------------------------------------- env


def claude_env(
    base: dict[str, str],
    relay_url: str,
    service_name: str,
    attrs: dict[str, str],
    parent: str,
    metrics: bool = False,
) -> dict[str, str]:
    """The measured claude process's environment in live mode: the base env
    without telemetry keys or ambient OTel settings, plus Claude Code's
    documented telemetry switches. Prompt, response and tool content are on;
    raw API bodies (the whole conversation per request) stay off.

    Metrics are off unless asked for: Honeycomb files OTLP metrics in the
    environment's shared `metrics` dataset whatever the dataset header says
    (smoke test 2026-10-11), and every number they carry is also on the
    api_request events and llm_request spans."""
    env = {
        k: v
        for k, v in base.items()
        if not k.startswith(_STRIP_PREFIXES) and k not in _STRIP_EXACT
    }
    env.update(
        {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
            "OTEL_METRICS_EXPORTER": "otlp" if metrics else "none",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
            "OTEL_EXPORTER_OTLP_ENDPOINT": relay_url,
            "OTEL_SERVICE_NAME": service_name,
            "OTEL_RESOURCE_ATTRIBUTES": encode_resource_attributes(
                {**attrs, "study.component": "claude-code"}
            ),
            "OTEL_LOG_USER_PROMPTS": "1",
            "OTEL_LOG_ASSISTANT_RESPONSES": "1",
            "OTEL_LOG_TOOL_DETAILS": "1",
            "OTEL_LOG_TOOL_CONTENT": "1",
            "TRACEPARENT": parent,
            **INTERVALS_MS,
        }
    )
    return env


def server_env(relay_url: str, service_name: str, attrs: dict[str, str]) -> dict[str, str]:
    """The MCP server's OTel settings, for the env block of its MCP config
    (Claude Code passes no OTEL_* variables to the servers it spawns, but the
    config's own env block reaches them). TRACEPARENT itself is inherited
    from the claude process."""
    return {
        "KIBANA_MCP_OTEL_ENABLED": "true",
        "KIBANA_MCP_OTEL_ENDPOINT": relay_url,
        "KIBANA_MCP_OTEL_SERVICE_NAME": service_name,
        "KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV": "true",
        "OTEL_RESOURCE_ATTRIBUTES": encode_resource_attributes(
            {**attrs, "study.component": "mcp-server"}
        ),
        "OTEL_BSP_SCHEDULE_DELAY": "500",
    }


# ---------------------------------------------------------------- redaction


class Scrubber:
    """Replaces known secret values in every string of an OTLP payload and
    drops attributes by key. Pure, works on the JSON-shaped dict."""

    def __init__(self, secret_values: list[str], drop_keys: tuple[str, ...] = IDENTITY_ATTRS):
        # Longest first so a secret that contains another is replaced whole.
        vals = sorted({s for s in secret_values if s and len(s) >= 8}, key=len, reverse=True)
        self._pattern = re.compile("|".join(map(re.escape, vals))) if vals else None
        self.drop_keys = set(drop_keys)
        self.replaced = 0
        self.dropped = 0

    def text(self, s: str) -> str:
        if self._pattern is None:
            return s
        out, n = self._pattern.subn(REDACTED, s)
        self.replaced += n
        return out

    def scrub(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, list):
            kept = []
            for item in obj:
                if isinstance(item, dict) and item.get("key") in self.drop_keys and "value" in item:
                    self.dropped += 1
                    continue
                kept.append(self.scrub(item))
            return kept
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        return obj


def mark_model_text_secrets(body: dict, known: str | None) -> int:
    """agent.secret.* (location model_text) on every assistant_response log
    record whose response text holds a credential, found with
    efficiency_analyze.scan_secrets before the scrubber removes it. Returns
    records marked."""
    import efficiency_analyze as ea

    n = 0
    for rl in body.get("resourceLogs", []):
        for sl in rl.get("scopeLogs", []):
            for lr in sl.get("logRecords", []):
                attrs = lr.get("attributes") or []
                have = {a.get("key"): (a.get("value") or {}) for a in attrs}
                if have.get("event.name", {}).get("stringValue") != "assistant_response":
                    continue
                found = ea.scan_secrets(have.get("response", {}).get("stringValue") or "", known)
                marks = {"agent.secret.exposed": bool(found)}
                if found:
                    marks.update(
                        {
                            "agent.secret.kind": ",".join(sorted(found)),
                            "agent.secret.location": "model_text",
                            "agent.secret.count": sum(found.values()),
                            "agent.secret.known_exposed": "known_secret" in found,
                        }
                    )
                    n += 1
                attrs.extend(_attr(k, v) for k, v in marks.items())
                lr["attributes"] = attrs
    return n


def scrub_request(
    path: str,
    content_type: str,
    raw: bytes,
    scrubber: Scrubber,
    agent_name: str | None = None,
    known: str | None = None,
) -> tuple[bytes, dict]:
    """(bytes to forward, the scrubbed body as a dict). Protobuf stays
    protobuf: decoded, scrubbed and re-encoded. With agent_name, trace bodies
    also get the GenAI names (enrich) and log bodies the model-text secret
    marks, computed before scrubbing."""
    body = otlp_sink.decode_body(path, content_type, raw)
    if agent_name and path == "/v1/logs":
        mark_model_text_secrets(body, known)
    body = scrubber.scrub(body)
    if agent_name and path == "/v1/traces":
        enrich(body, agent_name)
    if "json" in (content_type or ""):
        return json.dumps(body).encode(), body
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

    kinds = {
        "/v1/traces": trace_service_pb2.ExportTraceServiceRequest,
        "/v1/logs": logs_service_pb2.ExportLogsServiceRequest,
        "/v1/metrics": metrics_service_pb2.ExportMetricsServiceRequest,
    }
    return ParseDict(body, kinds[path]()).SerializeToString(), body


# ---------------------------------------------------------------- relay


# GenAI semantic-convention names for Claude Code's own spans, so Honeycomb's
# Agent Timeline (which keys on gen_ai.operation.name within a
# gen_ai.conversation.id) shows the run. Added, never overwriting.
_GENAI_OPS = {
    "claude_code.interaction": "invoke_agent",
    "efficiency.run": "invoke_agent",
    "claude_code.llm_request": "chat",
    "claude_code.tool": "execute_tool",
}
_GENAI_COPY = {
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "cache_read_tokens": "gen_ai.usage.cache_read.input_tokens",
    "cache_creation_tokens": "gen_ai.usage.cache_creation.input_tokens",
    "tool_name": "gen_ai.tool.name",
}


def enrich(body: dict, agent_name: str) -> int:
    """Add gen_ai.operation.name / gen_ai.agent.name / gen_ai.usage.* to the
    spans of an OTLP traces body (JSON-shaped). Returns spans touched."""
    n = 0
    for rs in body.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                op = _GENAI_OPS.get(sp.get("name"))
                if not op:
                    continue
                attrs = sp.setdefault("attributes", [])
                have = {a.get("key"): a.get("value") for a in attrs}
                add = {"gen_ai.operation.name": {"stringValue": op}}
                add["gen_ai.agent.name"] = {"stringValue": agent_name}
                add["gen_ai.provider.name"] = {"stringValue": "anthropic"}
                for src, dst in _GENAI_COPY.items():
                    if src in have and dst not in have:
                        add[dst] = have[src]
                attrs.extend({"key": k, "value": v} for k, v in add.items() if k not in have)
                n += 1
    return n


class Relay:
    """A loopback OTLP/HTTP endpoint that scrubs, keeps a copy and forwards.

    upstream=None keeps the copy only (dry runs, tests). The exporter gets its
    answer as soon as the request is scrubbed and kept; forwarding runs on a
    worker thread so a hook or exporter never waits on Honeycomb, and stop()
    drains the queue, so the exit flush of a short -p run is not lost.
    """

    def __init__(
        self,
        tee: Path,
        scrubber: Scrubber,
        upstream: str | None = None,
        api_key: str | None = None,
        dataset: str = DEFAULT_DATASET,
        agent_name: str | None = None,
        known: str | None = None,
    ):
        self.tee, self.scrubber = tee, scrubber
        self.agent_name, self._known = agent_name, known
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self.upstream = upstream.rstrip("/") if upstream else None
        self._api_key, self.dataset = api_key, dataset
        self.stats = {"requests": 0, "forwarded": 0, "failed": 0, "by_path": {}}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "relay not started"
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> Relay:
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 (http.server API)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("Content-Encoding") == "gzip":
                    import gzip

                    raw = gzip.decompress(raw)
                status = relay.handle(self.path.split("?")[0], self.headers.get("Content-Type", ""), raw)
                ctype = self.headers.get("Content-Type", "") or "application/x-protobuf"
                reply = b"{}" if "json" in ctype else b""
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        if self.upstream:
            self._worker = threading.Thread(target=self._drain, daemon=True)
            self._worker.start()
        return self

    def stop(self, timeout: float = 60.0) -> None:
        """Stop accepting, then wait (up to timeout) until every queued
        request has been forwarded."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout)

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            path, ctype, data = item
            status, error = self._forward(path, ctype, data)
            with self._lock:
                if error:
                    self.stats["failed"] += 1
                    self.stats.setdefault("errors", []).append(f"{path}: {error}"[:300])
                else:
                    self.stats["forwarded"] += 1

    def handle(self, path: str, content_type: str, raw: bytes) -> int:
        """Scrub, keep, forward. Returns the HTTP status for the exporter."""
        error = None
        try:
            data, body = scrub_request(
                path, content_type, raw, self.scrubber, self.agent_name, self._known
            )
        except Exception as e:  # undecodable: drop it rather than forward unscrubbed
            data, body, error = None, None, f"undecodable: {e!r}"
        if data is not None and self.upstream:
            self._queue.put((path, content_type, data))
        with self._lock:
            self.stats["requests"] += 1
            self.stats["by_path"][path] = self.stats["by_path"].get(path, 0) + 1
            if error:
                self.stats["failed"] += 1
            with self.tee.open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "path": path,
                            "ts": time.time(),
                            "content_type": content_type,
                            "body": body,
                            "forwarded": bool(data is not None and self.upstream),
                            "error": error,
                        }
                    )
                    + "\n"
                )
        return 200

    def _forward(self, path: str, content_type: str, data: bytes) -> tuple[int, str | None]:
        headers = {"Content-Type": content_type or "application/x-protobuf"}
        if self._api_key:
            headers["x-honeycomb-team"] = self._api_key
            # Ingest keys name the dataset after service.name and ignore this;
            # metrics and classic keys need it.
            headers["x-honeycomb-dataset"] = self.dataset
        req = urllib.request.Request(self.upstream + path, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, None
        except urllib.error.HTTPError as e:
            return e.code, f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
        except Exception as e:
            return 503, f"forward failed: {e!r}"

    def post_json(self, path: str, body: dict) -> int:
        """Send a runner-built OTLP/JSON payload through the same path."""
        return self.handle(path, "application/json", json.dumps(body).encode())


# ---------------------------------------------------------------- runner spans


def _attr(k: str, v) -> dict:
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": str(v)}}


ROOT_KEYS = (
    "run_id", "block", "arm", "model_requested", "marker", "kibana_version",
    "prompt_version", "server_rev", "server_dirty", "server_label", "claude_cli",
    "key_in_model_env", "sample_data_installed", "success", "failure_reason",
    "num_turns", "n_tool_calls", "n_mcp_calls", "n_bash_calls", "total_cost_usd",
    "input_tokens", "output_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "duration_ms", "duration_api_ms", "wall_s",
    "timed_out", "valid", "n_permission_denials", "n_result_events",
    "rule_enabled", "dashboard_panels",
    # --task runs only (study 4); the checks come from study_tasks.span_attributes
    "steps_passed", "first_failed_step", "tasks_sha",
    # with-mcp-es arm only (absent elsewhere, so not on the span)
    "es_mcp_image", "es_key_privileges", "n_es_mcp_calls", "es_key_in_output",
    "es_key_invalidated",
)  # fmt: skip


def runner_spans(
    record: dict,
    trace_id: str,
    root_id: str,
    phases: dict[str, tuple[float, float]],
    service_name: str,
    attrs: dict[str, str],
    extra: dict | None = None,
) -> dict:
    """The runner's OTLP/JSON trace: the root efficiency.run span (start to
    end of the run, outcome attached) and one child per timed phase
    (agent, score, reset), times in epoch seconds."""

    def ns(t: float) -> str:
        return str(int(t * 1e9))

    start = min(a for a, _ in phases.values())
    end = max(b for _, b in phases.values())
    root_attrs = [_attr(f"study.{k}", record[k]) for k in ROOT_KEYS if record.get(k) is not None]
    root_attrs.append(_attr("gen_ai.conversation.id", record["run_id"]))
    # Per-run agent.* rollups of the hook's live classification.
    root_attrs += [_attr(k, v) for k, v in (extra or {}).items() if v is not None and v != ""]
    spans = [
        {
            "traceId": trace_id,
            "spanId": root_id,
            "name": "efficiency.run",
            "kind": 1,
            "startTimeUnixNano": ns(start),
            "endTimeUnixNano": ns(end),
            "attributes": root_attrs,
            "status": {"code": 1 if record.get("success") else 2},
        }
    ]
    for name, (a, b) in phases.items():
        spans.append(
            {
                "traceId": trace_id,
                "spanId": secrets.token_hex(8),
                "parentSpanId": root_id,
                "name": f"efficiency.{name}",
                "kind": 1,
                "startTimeUnixNano": ns(a),
                "endTimeUnixNano": ns(b),
                "attributes": [_attr("study.run_id", record["run_id"])],
            }
        )
    resource = {"service.name": service_name, **attrs, "study.component": "runner"}
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr(k, v) for k, v in resource.items()]},
                "scopeSpans": [{"scope": {"name": "efficiency_run"}, "spans": spans}],
            }
        ]
    }
