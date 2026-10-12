"""HTTP fakes for the local-model study runner (scripts/experiment/local_run.py):
a fake Kibana REST API, a fake LM Studio ``/api/v1`` server with scripted model
behaviours, a fake Honeycomb OTLP intake, and an in-memory stand-in for the
checker's Elasticsearch. A loader-parsed helper module, never a test module.

tests/fakes.py fakes the gateway port in memory; the runner speaks HTTP to
Kibana, LM Studio and Honeycomb, so these fakes are HTTP servers on
127.0.0.1 with an ephemeral port each.

The fake LM Studio plays the MCP server's part too: a scripted tool call
changes the fake Kibana's state the way the real server would. Model
behaviours, chosen by the requested model key:

    fake/pass   every step right; one recoverable tool error; emits a second,
                bogus chat.end so the runner must read the first
    fake/fail   wrong time field, a one-panel dashboard, a rule left disabled,
                no panel edit, an LM Studio-rejected call; claims success
    fake/leak   fake/pass, but a tool result echoes the Kibana API key
    fake/oom    LM Studio cannot load the model (HTTP 400, memory)
    fake/hang   starts, makes one call, then stalls past the run timeout

The SSE event names and the final ``chat.end`` event carrying ``result`` are
the runner's reading of LM Studio's streaming API; the Mac pilot confirms
them against the real runtime (LOCAL_STUDY.md).
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

LOGS_DV_ID = "90943e30-9a47-11e8-b64d-95841ca0b247"
LOGS_INDEX = "kibana_sample_data_logs"


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def _serve(handler_cls) -> tuple[_Server, str]:
    srv = _Server(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw) if raw.strip() else None

    def _send(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


# ---------------------------------------------------------------- Kibana


class FakeKibana:
    """Kibana's REST surface as the runner uses it, with pre-existing objects
    a reset must never touch."""

    def __init__(self, api_key: str, version: str = "9.5.5", logs_installed: bool = True):
        self.api_key = api_key
        self.version = version
        self.logs_installed = logs_installed
        self.lock = threading.Lock()
        self.dashboards: dict[str, dict] = {
            "preexisting-flights": {"title": "[Flights] Global Flight Dashboard", "panels": []}
        }
        self.rules: dict[str, dict] = {
            "preexisting-rule": {"id": "preexisting-rule", "name": "ops watch", "enabled": True}
        }
        self.other: set[tuple[str, str]] = (
            {("index-pattern", LOGS_DV_ID)} if logs_installed else set()
        )
        self.requests: list[tuple[str, str]] = []
        fk = self

        class H(_Base):
            def do_GET(self):
                fk.handle(self, "GET")

            def do_POST(self):
                fk.handle(self, "POST")

            def do_DELETE(self):
                fk.handle(self, "DELETE")

            def do_PUT(self):
                fk.handle(self, "PUT")

        self.server, self.url = _serve(H)

    def close(self):
        self.server.shutdown()

    # state changes the fake MCP server makes
    def create_dashboard(self, title: str, panels: list[dict]) -> str:
        with self.lock:
            for did, d in self.dashboards.items():
                if d["title"] == title:  # same title replaces in place, as the server does
                    d["panels"] = panels
                    return did
            did = str(uuid.uuid4())
            self.dashboards[did] = {"title": title, "panels": panels}
            return did

    def create_rule(self, name: str, params: dict, interval: str, enabled: bool) -> str:
        rid = str(uuid.uuid4())
        with self.lock:
            self.rules[rid] = {
                "id": rid,
                "name": name,
                "enabled": enabled,
                "rule_type_id": ".es-query",
                "schedule": {"interval": interval},
                "params": params,
            }
        return rid

    def find_dashboard(self, marker: str) -> str | None:
        return next((i for i, d in self.dashboards.items() if marker in d["title"]), None)

    def find_rule(self, marker: str) -> str | None:
        return next((i for i, r in self.rules.items() if marker in r["name"]), None)

    def handle(self, h: _Base, method: str):
        parts = urlsplit(h.path)
        path, query = parts.path, parse_qs(parts.query)
        self.requests.append((method, path))
        if path == "/api/status":
            return h._send(
                200,
                {
                    "version": {"number": self.version},
                    "status": {"overall": {"level": "available"}},
                },
            )
        if h.headers.get("Authorization") != f"ApiKey {self.api_key}":
            return h._send(401, {"statusCode": 401, "error": "Unauthorized"})
        if method in ("POST", "PUT"):
            h._body()  # drain the request body
        with self.lock:
            if path == "/api/features":
                return h._send(200, [])
            if path == "/api/sample_data" and method == "GET":
                return h._send(
                    200,
                    [
                        {"id": "flights", "status": "installed"},
                        {
                            "id": "logs",
                            "status": "installed" if self.logs_installed else "not_installed",
                        },
                    ],
                )
            if path == "/api/sample_data/logs" and method == "POST":
                self.logs_installed = True
                self.other.add(("index-pattern", LOGS_DV_ID))
                return h._send(200, {})
            if path == "/api/saved_objects/_find":
                so_type = query.get("type", [""])[0]
                if so_type == "dashboard":
                    objs = [{"id": i} for i in self.dashboards]
                else:
                    objs = [{"id": i} for t, i in self.other if t == so_type]
                return h._send(200, {"saved_objects": objs})
            if path == "/api/alerting/rules/_find":
                return h._send(200, {"data": list(self.rules.values())})
            if path == "/api/actions/connectors":
                return h._send(200, [])
            if path == "/api/data_views":
                dvs = [
                    {
                        "id": i,
                        "title": LOGS_INDEX,
                        "name": "Kibana Sample Data Logs",
                        "timeFieldName": "timestamp",
                    }
                    for t, i in self.other
                    if t == "index-pattern"
                ]
                return h._send(200, {"data_view": dvs})
            m = re.fullmatch(r"/api/dashboards/([^/]+)", path)
            if m:
                did = m.group(1)
                if did not in self.dashboards:
                    return h._send(404, {"statusCode": 404, "error": "Not Found"})
                if method == "DELETE":
                    del self.dashboards[did]
                    return h._send(200, {})
                d = self.dashboards[did]
                return h._send(
                    200,
                    {"id": did, "data": {"title": d["title"], "panels": d["panels"]}, "meta": {}},
                )
            m = re.fullmatch(r"/api/alerting/rule/([^/]+)", path)
            if m and method == "DELETE":
                self.rules.pop(m.group(1), None)
                return h._send(204, {})
            if m and method == "GET":
                rule = self.rules.get(m.group(1))
                if rule is None:
                    return h._send(404, {"statusCode": 404, "error": "Not Found"})
                return h._send(200, rule)
            m = re.fullmatch(r"/api/saved_objects/([^/]+)/([^/]+)", path)
            if m and method == "DELETE":
                self.other.discard((m.group(1), m.group(2)))
                return h._send(200, {})
        return h._send(404, {"statusCode": 404, "error": "Not Found", "message": path})


# ---------------------------------------------------------------- LM Studio


def _tool_out(obj) -> str:
    """An MCP tool result as LM Studio puts it in ``output``: a JSON string of
    content parts."""
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return json.dumps([{"type": "text", "text": text}])


def _panel(title: str, field: str) -> dict:
    return {
        "type": "vis",
        "config": {
            "title": title,
            "type": "xy",
            "dataset": {"type": "dataView", "id": LOGS_DV_ID},
            "layers": [{"x": {"fields": [field]}, "y": [{"operation": "count"}]}],
        },
    }


class FakeLMStudio:
    MODELS = ("fake/pass", "fake/fail", "fake/leak", "fake/oom", "fake/hang")

    def __init__(self, kibana: FakeKibana, token: str, entry: str, hang_s: float = 3.0):
        self.kibana = kibana
        self.token = token
        self.entry = entry
        self.hang_s = hang_s
        self.requests: list[dict] = []
        self.loaded = set(self.MODELS)  # every model resident unless a test clears it
        self.load_log: list[tuple[str, str, int | None]] = []
        fl = self

        class H(_Base):
            def do_GET(self):
                fl.handle_get(self)

            def do_POST(self):
                fl.handle_post(self)

        self.server, self.url = _serve(H)

    def close(self):
        self.server.shutdown()

    def _authed(self, h) -> bool:
        return h.headers.get("Authorization") == f"Bearer {self.token}"

    def handle_get(self, h):
        if urlsplit(h.path).path != "/api/v1/models":
            return h._send(404, {"error": "not found"})
        if not self._authed(h):
            return h._send(401, {"error": {"message": "Invalid or missing API token"}})
        models = [
            {
                "type": "llm",
                "key": k,
                "display_name": k,
                "max_context_length": 131072,
                "loaded_instances": [{"id": k, "config": {"context_length": 131072}}]
                if k in self.loaded
                else [],
                "capabilities": {"trained_for_tool_use": True},
            }
            for k in self.MODELS
        ]
        return h._send(200, {"models": models})

    def handle_post(self, h):
        path = urlsplit(h.path).path
        if path not in ("/api/v1/chat", "/api/v1/models/load", "/api/v1/models/unload"):
            return h._send(404, {"error": "not found"})
        if not self._authed(h):
            return h._send(401, {"error": {"message": "Invalid or missing API token"}})
        req = h._body()
        if path == "/api/v1/models/load":
            self.loaded.add(req["model"])
            self.load_log.append(("load", req["model"], req.get("context_length")))
            return h._send(200, {"type": "llm", "instance_id": req["model"], "status": "loaded"})
        if path == "/api/v1/models/unload":
            self.loaded.discard(req["instance_id"])
            self.load_log.append(("unload", req["instance_id"], None))
            return h._send(200, {"instance_id": req["instance_id"]})
        self.requests.append(req)
        if req["model"] not in self.loaded:
            return h._send(
                404,
                {
                    "error": {
                        "message": f'Invalid model identifier "{req["model"]}". No matching loaded '
                        "model found, and just-in-time (JIT) model loading is disabled.",
                        "type": "invalid_request",
                    }
                },
            )
        if req.get("integrations") != [f"mcp/{self.entry}"]:
            return h._send(
                403,
                {
                    "error": {
                        "message": f"Permission denied to use plugin '{req.get('integrations')}'.",
                        "type": "invalid_request",
                    }
                },
            )
        model = req["model"]
        if model == "fake/oom":
            return h._send(
                400,
                {
                    "error": {
                        "message": 'Failed to load model "fake/oom". Error: insufficient system resources'
                    }
                },
            )
        items = self.script(model, req["input"])
        stats = {
            "input_tokens": 1000 + 300 * len(items),
            "total_output_tokens": 100 * len(items),
            "reasoning_output_tokens": 40 * len(items),
            "tokens_per_second": 42.5,
            "time_to_first_token_seconds": 0.25,
            "model_load_time_seconds": 1.5,
        }
        result = {
            "model_instance_id": model,
            "output": items,
            "stats": stats,
            "response_id": "resp_" + uuid.uuid4().hex[:8],
        }
        if not req.get("stream"):
            if model == "fake/hang":
                time.sleep(self.hang_s)
            return h._send(200, result)
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.end_headers()

        def ev(etype, data):
            h.wfile.write(
                f"event: {etype}\ndata: {json.dumps({'type': etype, **data})}\n\n".encode()
            )
            h.wfile.flush()

        try:
            ev("chat.start", {"model_instance_id": model})
            for it in items:
                if it["type"] in ("reasoning", "message"):
                    ev(f"{it['type']}.delta", {"content": it["content"]})
                elif it["type"] == "tool_call":
                    ev("tool_call.start", {"tool": it["tool"]})
                    ev("tool_call.arguments", {"tool": it["tool"], "arguments": it["arguments"]})
                    time.sleep(0.01)
                    ev(
                        "tool_call.success",
                        {"tool": it["tool"], "arguments": it["arguments"], "output": it["output"]},
                    )
                elif it["type"] == "invalid_tool_call":
                    ev("tool_call.failure", {"reason": it["reason"], "metadata": it["metadata"]})
                if model == "fake/hang" and it["type"] == "tool_call":
                    time.sleep(self.hang_s)
            ev("chat.end", {"result": result})
            if model == "fake/pass":  # a stray second final event; the runner must keep the first
                bogus = {**result, "stats": {**stats, "total_output_tokens": 1}, "output": []}
                ev("chat.end", {"result": bogus})
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------ behaviours

    def script(self, model: str, prompt: str) -> list[dict]:
        kb = self.kibana
        if "single word OK" in prompt:  # preflight's load check
            return [{"type": "message", "content": "OK"}]
        found = re.search(r"(LOC-[0-9a-f]{8})", prompt)
        marker = found.group(1) if found else None  # L1 names no dashboard or rule
        steps = {
            "describe": "TIME_FIELD" in prompt,
            "dashboard": "Build a dashboard" in prompt,
            "rule": "alert rule named" in prompt,
            "edit": "Responses by code" in prompt,
        }
        fail = model == "fake/fail"
        items: list[dict] = [
            {"type": "reasoning", "content": "I will look at the data view first."}
        ]
        if fail:
            items.append(
                {
                    "type": "invalid_tool_call",
                    "reason": 'Failed to parse arguments for tool "describe_data_view": params.name is required',
                    "metadata": {"tool_name": "describe_data_view", "arguments": {}},
                }
            )
        dv = {
            "id": LOGS_DV_ID,
            "name": "Kibana Sample Data Logs",
            "time_field": "timestamp",
            "fields": {"timestamp": "date", "response": "string", "bytes": "number"},
        }
        if model == "fake/leak":
            dv["debug"] = f"KIBANA_API_KEY={kb.api_key}"
        items.append(
            {
                "type": "tool_call",
                "tool": "describe_data_view",
                "arguments": {"name": "Kibana Sample Data Logs"},
                "output": _tool_out(dv),
                "provider_info": {"plugin_id": f"mcp/{self.entry}"},
            }
        )
        if model == "fake/hang":
            items.append({"type": "message", "content": "still thinking"})
            return items
        if steps["dashboard"]:
            title = f"{marker} Web logs"
            if not fail:
                items.append(
                    {
                        "type": "tool_call",
                        "tool": "create_dashboard",
                        "arguments": {"title": title, "panels": [{"field": "status"}]},
                        "output": _tool_out(
                            "invalid visualization spec:\n- field 'status' does not exist in the data view — did you mean 'response'?"
                        ),
                    }
                )
                panels = [
                    _panel("Requests over time", "timestamp"),
                    _panel("Requests by response code", "response.keyword"),
                ]
            else:
                panels = [_panel("Requests over time", "timestamp")]
            did = kb.create_dashboard(title, panels)
            items.append(
                {
                    "type": "tool_call",
                    "tool": "create_dashboard",
                    "arguments": {"title": title, "panels": [p["config"]["title"] for p in panels]},
                    "output": _tool_out(
                        {"id": did, "status": "created", "panel_count": len(panels)}
                    ),
                }
            )
        if steps["rule"]:
            params = {
                "index": [LOGS_INDEX],
                "timeField": "timestamp",
                "esQuery": json.dumps({"query": {"range": {"response": {"gte": 500, "lt": 600}}}}),
                "threshold": [10],
                "thresholdComparator": ">",
                "timeWindowSize": 5,
                "timeWindowUnit": "m",
            }
            rid = kb.create_rule(f"{marker} 5xx errors", params, "1m", enabled=False)
            items.append(
                {
                    "type": "tool_call",
                    "tool": "create_alert_rule",
                    "arguments": {"name": f"{marker} 5xx errors", "params": params},
                    "output": _tool_out({"id": rid, "enabled": False}),
                }
            )
            if not fail:
                kb.rules[rid]["enabled"] = True
                items.append(
                    {
                        "type": "tool_call",
                        "tool": "enable_alert_rule",
                        "arguments": {"rule_id": rid},
                        "output": _tool_out({"id": rid, "enabled": True}),
                    }
                )
        if steps["edit"] and not fail:
            did = kb.find_dashboard(marker)
            kb.dashboards[did]["panels"][1] = _panel("Responses by code", "response.keyword")
            items.append(
                {
                    "type": "tool_call",
                    "tool": "update_panel",
                    "arguments": {"dashboard_id": did, "panel_index": 1},
                    "output": _tool_out({"id": did, "panel_index": 1}),
                }
            )
            rid = kb.find_rule(marker)
            items.append(
                {
                    "type": "tool_call",
                    "tool": "get_alert_rule",
                    "arguments": {"rule_id": rid},
                    "output": _tool_out(kb.rules[rid]),
                }
            )
        lines = ["All steps are done."]
        lines.append("TIME_FIELD: @timestamp" if fail else "TIME_FIELD: timestamp")
        if steps["edit"]:
            lines.append("RULE_ENABLED: yes")
        items.append({"type": "message", "content": "\n".join(lines)})
        return items


# ---------------------------------------------------------------- Elasticsearch


class FakeElastic:
    """The checker's Elasticsearch (study_tasks.Elastic) in memory: the
    web-logs mapping, and the same count for every query, so every rule
    filter evaluates as correct. The filter evaluation itself is exercised
    on the live stack (study_tasks.py replay; STUDY_DESIGN.md decision 12)."""

    MAPPING = {
        "@timestamp": {"type": "date"},
        "timestamp": {"type": "alias", "path": "@timestamp"},
        "response": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
    }

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def call(self, method, path, body=None, ndjson=None):
        self.calls.append((method, path))
        if path == "/":
            return 200, {"version": {"number": "9.5.5"}}
        if path.endswith("/_mapping"):
            index = path.strip("/").split("/")[0]
            return 200, {index: {"mappings": {"properties": self.MAPPING}}}
        if path.endswith("/_count"):
            return 200, {"count": 5}
        if path == "/_query":
            return 200, {"columns": [{"name": "c"}], "values": [[5]]}
        return 200, {}


# ---------------------------------------------------------------- Honeycomb


class FakeHoneycomb:
    def __init__(self, key: str):
        self.key = key
        self.posts: list[dict] = []
        self.raw: list[str] = []
        fh = self

        class H(_Base):
            def do_GET(self):
                if (
                    urlsplit(self.path).path == "/1/auth"
                    and self.headers.get("x-honeycomb-team") == fh.key
                ):
                    return self._send(200, {"team": {"slug": "t"}, "environment": {"slug": "test"}})
                return self._send(401, {"error": "unknown key"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode()
                if self.headers.get("x-honeycomb-team") != fh.key:
                    return self._send(401, {"error": "unknown key"})
                fh.raw.append(raw)
                fh.posts.append(json.loads(raw))
                return self._send(200, {})

        self.server, self.url = _serve(H)

    def close(self):
        self.server.shutdown()

    def spans(self) -> list[dict]:
        return [
            s
            for p in self.posts
            for rs in p["resourceSpans"]
            for ss in rs["scopeSpans"]
            for s in ss["spans"]
        ]
