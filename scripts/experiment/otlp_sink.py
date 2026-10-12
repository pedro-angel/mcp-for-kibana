"""A local OTLP/HTTP sink: records every export request so we can see exactly
what each component emits, before anything goes to Honeycomb.

    uv run python scripts/experiment/otlp_sink.py serve --port 4318 --out sink.jsonl
    uv run python scripts/experiment/otlp_sink.py summary sink.jsonl

Accepts /v1/traces, /v1/logs and /v1/metrics in http/protobuf (decoded with
the opentelemetry-proto package) or http/json, gzip or plain. Each request
becomes one JSON line: path, arrival time, content type, the headers with
credentials redacted, and the decoded body. Stdlib plus opentelemetry-proto
only; no network beyond the loopback listener.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SECRET_HEADERS = ("x-honeycomb-team", "authorization", "api-key", "x-api-key")


def decode_body(path: str, content_type: str, raw: bytes) -> dict:
    """The request body as an OTLP JSON-shaped dict."""
    if "json" in (content_type or ""):
        return json.loads(raw or b"{}")
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

    kinds = {
        "/v1/traces": trace_service_pb2.ExportTraceServiceRequest,
        "/v1/logs": logs_service_pb2.ExportLogsServiceRequest,
        "/v1/metrics": metrics_service_pb2.ExportMetricsServiceRequest,
    }
    msg = kinds[path]()
    msg.ParseFromString(raw)
    return MessageToDict(msg)


def redact_headers(headers: dict) -> dict:
    return {
        k: ("<redacted>" if k.lower() in _SECRET_HEADERS else v) for k, v in headers.items()
    }


def make_handler(out: Path, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 (http.server API)
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            ctype = self.headers.get("Content-Type", "")
            path = self.path.split("?")[0]
            try:
                body = decode_body(path, ctype, raw)
                error = None
            except Exception as e:  # keep the request even if undecodable
                body, error = None, repr(e)
            record = {
                "path": path,
                "ts": time.time(),
                "content_type": ctype,
                "headers": redact_headers(dict(self.headers)),
                "body": body,
                "decode_error": error,
            }
            with lock, out.open("a") as f:
                f.write(json.dumps(record) + "\n")
            reply = b"{}" if "json" in ctype else b""
            self.send_response(200)
            self.send_header("Content-Type", ctype or "application/x-protobuf")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):  # quiet
            pass

    return Handler


def start(port: int, out: Path) -> ThreadingHTTPServer:
    """Start the sink on a daemon thread; port 0 picks a free one."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(out, threading.Lock()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------- reading


_ID_FIELDS = ("traceId", "spanId", "parentSpanId")


def _hex_ids(obj: dict) -> dict:
    """Protobuf JSON renders ids as base64; OTLP/JSON and every UI use hex."""
    out = dict(obj)
    for k in _ID_FIELDS:
        v = out.get(k)
        if v and not re.fullmatch(r"[0-9a-f]+", v):
            out[k] = base64.b64decode(v).hex()
    return out


def _attrs(items) -> dict:
    out = {}
    for a in items or []:
        v = a.get("value") or {}
        out[a["key"]] = next(iter(v.values()), None) if v else None
    return out


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def spans(records: list[dict]) -> list[dict]:
    """Every span, flattened with its resource attributes."""
    out = []
    for r in records:
        if r["path"] != "/v1/traces" or not r["body"]:
            continue
        for rs in r["body"].get("resourceSpans", []):
            res = _attrs((rs.get("resource") or {}).get("attributes"))
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    out.append({**_hex_ids(s), "attrs": _attrs(s.get("attributes")), "resource": res})
    return out


def log_records(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        if r["path"] != "/v1/logs" or not r["body"]:
            continue
        for rl in r["body"].get("resourceLogs", []):
            res = _attrs((rl.get("resource") or {}).get("attributes"))
            for sl in rl.get("scopeLogs", []):
                for lr in sl.get("logRecords", []):
                    out.append({**_hex_ids(lr), "attrs": _attrs(lr.get("attributes")), "resource": res})
    return out


def metric_points(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        if r["path"] != "/v1/metrics" or not r["body"]:
            continue
        for rm in r["body"].get("resourceMetrics", []):
            res = _attrs((rm.get("resource") or {}).get("attributes"))
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    for kind in ("sum", "gauge", "histogram"):
                        for dp in (m.get(kind) or {}).get("dataPoints", []):
                            out.append(
                                {"name": m["name"], "attrs": _attrs(dp.get("attributes")),
                                 "resource": res}
                            )
    return out


def summary(path: Path) -> str:
    recs = load(path)
    lines = [f"requests: {Counter(r['path'] for r in recs)}"]
    for s in spans(recs):
        lines.append(
            f"span {s['name']:<32} trace={s.get('traceId')} span={s.get('spanId')} "
            f"parent={s.get('parentSpanId')} service={s['resource'].get('service.name')}"
        )
    for lr in log_records(recs):
        name = lr["attrs"].get("event.name") or lr.get("eventName") or "?"
        lines.append(
            f"log  {name:<32} trace={lr.get('traceId')} "
            f"service={lr['resource'].get('service.name')}"
        )
    for m in metric_points(recs):
        lines.append(f"metric {m['name']:<30} service={m['resource'].get('service.name')}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=4318)
    s.add_argument("--out", type=Path, required=True)
    m = sub.add_parser("summary")
    m.add_argument("file", type=Path)
    args = ap.parse_args()
    if args.cmd == "summary":
        print(summary(args.file))
        return 0
    server = start(args.port, args.out)
    print(f"OTLP sink on http://127.0.0.1:{server.server_address[1]} -> {args.out}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
