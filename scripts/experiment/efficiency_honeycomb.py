#!/usr/bin/env python3
"""Send the efficiency study to Honeycomb as OpenTelemetry GenAI traces.

One trace per run (dataset and service.name ``mcp-for-kibana-study``):

    invoke_agent claude-code <arm>        run totals: agent.* and study.* signals
    ├── chat <model>                      one per model call, with the new input
    │                                     messages and the reply
    └── execute_tool <tool>               one per tool call, tool_use -> tool_result

``gen_ai.conversation.id`` is the run id on every span. Run-level signals are
read from ``analysis.json`` (the numbers the report cites); call-level signals
come from the same functions in ``efficiency_analyze.py``. The signals are
defined in ``SIGNALS.md``.

Content is redacted before it leaves the machine: the Kibana API key the runs
used, then generic credential patterns. The payload is scanned after redaction
and the send is refused if the key, or any credential pattern, remains.

    uv run python scripts/experiment/efficiency_honeycomb.py --check
    uv run python scripts/experiment/efficiency_honeycomb.py --dry-run payload.json
    HONEYCOMB_API_KEY=... uv run python scripts/experiment/efficiency_honeycomb.py

Stdlib only. Span and trace ids derive from the run and call ids, so a dry
run is reproducible; sending twice duplicates the dataset's events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import efficiency_analyze as ea

HERE = Path(__file__).resolve().parent
FULL = HERE / "runs" / "efficiency" / "full"
ANALYSIS = FULL / "analysis.json"
DATASET = "mcp-for-kibana-study"
ENDPOINT = os.environ.get("HONEYCOMB_OTLP_ENDPOINT", "https://api.eu1.honeycomb.io/v1/traces")
MSG_CAP = 16_000  # bytes per serialised message attribute
TOOL_CAP = 8_000  # bytes per tool argument / result attribute
BATCH_BYTES = 4_000_000

_HEADER = re.compile(
    r"(?i)((?:authorization|x-api-key)\s*[:=]\s*[\"']?(?:apikey|bearer|basic)?\s*)"
    r"[^\s\"',}\\$]{16,}"
)
_ENVKEY = re.compile(
    r"(?i)((?:api_?key|token|secret|password)[A-Z_]*\s*=\s*[\"']?)[^\s\"';&$\[*<)\\]+"
)
_B64 = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")


def _b64(m: re.Match) -> str:
    t = m.group(0)
    mixed = all(any(f(c) for c in t) for f in (str.isdigit, str.isupper, str.islower))
    return "[REDACTED_TOKEN]" if mixed and max(len(s) for s in t.split("/")) >= 16 else t


class Redactor:
    def __init__(self, known: str | None):
        self.known = known

    def __call__(self, text: str) -> str:
        if self.known:
            text = text.replace(self.known, "[REDACTED_KNOWN_SECRET]")
        text = _HEADER.sub(r"\1[REDACTED]", text)
        text = _ENVKEY.sub(r"\1[REDACTED]", text)
        text = _B64.sub(_b64, text)
        for kind, rx in ea._SECRET_RX:  # anything a detector still sees goes too
            text = rx.sub(f"[REDACTED_{kind.upper()}]", text)
        return text

    def tree(self, x):
        if isinstance(x, str):
            return self(x)
        if isinstance(x, list):
            return [self.tree(v) for v in x]
        if isinstance(x, dict):
            return {k: self.tree(v) for k, v in x.items()}
        return x


def _leaves(x, path=()):
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _leaves(v, (*path, i))
    elif isinstance(x, dict):
        for k, v in x.items():
            if k not in ("role", "type", "id", "name", "finish_reason"):
                yield from _leaves(v, (*path, k))


def fit(obj, redact: Redactor, cap: int) -> str:
    """Redact every string, then shrink the longest strings until the JSON fits."""
    obj = redact.tree(obj)
    while True:
        out = json.dumps(obj, ensure_ascii=False)
        over = len(out.encode()) - cap
        if over <= 0:
            return out
        leaves = list(_leaves(obj))
        if not leaves:
            return out.encode()[:cap].decode(errors="ignore")
        path, longest = max(leaves, key=lambda pv: len(pv[1]))
        keep = max(200, len(longest) - over - 64)
        shorter = longest[:keep] + f" [truncated {len(longest) - keep} chars]"
        if len(shorter) >= len(longest):  # every leaf is at the floor: no progress left
            return out.encode()[:cap].decode(errors="ignore")
        target = obj
        for k in path[:-1]:
            target = target[k]
        target[path[-1]] = shorter


def clip(text: str, redact: Redactor, cap: int = TOOL_CAP) -> str:
    text = redact(text)
    raw = text.encode()
    if len(raw) <= cap:
        return text
    return raw[: cap - 40].decode(errors="ignore") + f" [truncated {len(raw) - cap + 40} bytes]"


def attr(k: str, v) -> dict:
    if isinstance(v, bool):
        val = {"boolValue": v}
    elif isinstance(v, int):
        val = {"intValue": str(v)}
    elif isinstance(v, float):
        val = {"doubleValue": v}
    elif isinstance(v, list):
        val = {"arrayValue": {"values": [{"stringValue": str(x)} for x in v]}}
    else:
        val = {"stringValue": str(v)}
    return {"key": k, "value": val}


def attrs(d: dict) -> list[dict]:
    return [attr(k, v) for k, v in d.items() if v is not None and v != ""]


def _id(*parts: str, n: int) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[: n * 2]


def _secret_attrs(found: dict[str, dict[str, int]]) -> dict:
    """agent.secret.* for one span from {location: {kind: count}}."""
    hits = {loc: f for loc, f in found.items() if f}
    if not hits:
        return {"agent.secret.exposed": False}
    kinds = sorted({k for f in hits.values() for k in f})
    return {
        "agent.secret.exposed": True,
        "agent.secret.kind": ",".join(kinds),
        "agent.secret.location": ",".join(sorted(hits)),
        "agent.secret.count": sum(n for f in hits.values() for n in f.values()),
        "agent.secret.known_exposed": "known_secret" in kinds,
    }


def run_spans(run: dict, known: str | None, redact: Redactor, commit: str) -> tuple[list, dict]:
    rdir = FULL / run["block"] / run["run_id"]
    loaded = ea.load_run(rdir, known)
    parsed, first, start = loaded["parsed"], loaded["first"], loaded["start"]
    rid, arm = run["run_id"], run["arm"]
    trace_id, root_id = _id(rid, n=16), _id(rid, "root", n=8)
    agent_name = f"claude-code {arm}"
    common = {
        "gen_ai.conversation.id": rid,
        "gen_ai.agent.name": agent_name,
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": run["model"],
        "study.model": run["model"],
        "study.arm": arm,
        "study.block": run["block"],
        "study.run_id": rid,
    }
    sig = run["agent"]
    root = {
        **common,
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.id": f"claude-code/{arm}",
        "gen_ai.agent.version": sig["agent.harness.version"],
        # Per-call output tokens are not in the transcript: the run total sits
        # here once; input tokens sit on the chat spans. SUM over spans = run.
        "gen_ai.usage.output_tokens": sig["agent.usage.output_tokens"],
        "gen_ai.usage.reasoning.output_tokens": sig["agent.usage.reasoning_tokens"],
        **{k: v for k, v in sig.items() if k != "agent.harness.version"},
        "agent.task.success": run["success_strict"],
        **run["study"],
        "study.analysis_commit": commit,
    }
    ok = run["success_strict"]
    if not ok:
        root["error.type"] = "task_failed"
    wall_ns = int(sig["agent.time.wall_ms"] or 0) * 1_000_000
    spans = [
        {
            "traceId": trace_id,
            "spanId": root_id,
            "name": f"invoke_agent {agent_name}",
            "kind": 1,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(start + wall_ns),
            "attributes": attrs(root),
            "status": {"code": 1 if ok else 2},
        }
    ]
    chat_in = 0
    for c in parsed["chats"]:
        a = c["agent"]
        chat_in += a["agent.model.input_tokens"]
        fr = a["agent.model.finish_reason"]
        d = {
            **common,
            "gen_ai.operation.name": "chat",
            "gen_ai.response.model": c["model"],
            "gen_ai.response.id": c["id"],
            "gen_ai.usage.input_tokens": a["agent.model.input_tokens"],
            "gen_ai.usage.cache_read.input_tokens": a["agent.model.cache_read_tokens"],
            "gen_ai.usage.cache_creation.input_tokens": int(
                c["usage"].get("cache_creation_input_tokens") or 0
            ),
            "gen_ai.response.finish_reasons": [fr],
            "gen_ai.input.messages": fit(c["input"], redact, MSG_CAP),
            "gen_ai.output.messages": fit(
                [{"role": "assistant", "parts": c["output"], "finish_reason": fr}],
                redact,
                MSG_CAP,
            ),
            **{k: v for k, v in a.items() if not k.startswith("agent.secret.")},
            "agent.model.finish_reason_inferred": True,
            **_secret_attrs({"model_text": a["agent.secret.in_model_text"]}),
        }
        if c["index"] == 0 and first.get("ttft_ms") is not None:
            d["gen_ai.response.time_to_first_chunk"] = first["ttft_ms"] / 1000
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _id(rid, c["id"], n=8),
                "parentSpanId": root_id,
                "name": f"chat {c['model']}",
                "kind": 3,
                "startTimeUnixNano": str(c["start"]),
                "endTimeUnixNano": str(max(c["end"], c["start"])),
                "attributes": attrs(d),
                "status": {"code": 0},
            }
        )
    for t in parsed["tools"]:
        a = t["agent"]
        d = {
            **common,
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": t["name"],
            "gen_ai.tool.call.id": t["id"],
            "gen_ai.tool.type": "extension" if t["name"].startswith(ea.MCP_PREFIX) else "function",
            "gen_ai.tool.call.arguments": clip(json.dumps(t["input"], ensure_ascii=False), redact),
            "gen_ai.tool.call.result": clip(t["result"], redact),
            **{
                k: (",".join(v) if isinstance(v, list) else v)
                for k, v in a.items()
                if not k.startswith("agent.secret.")
            },
            "agent.tool.outside_workdir": bool(a["agent.tool.outside_workdir_paths"]),
            **_secret_attrs(
                {
                    "tool_arguments": a["agent.secret.in_arguments"],
                    "tool_result": a["agent.secret.in_result"],
                }
            ),
        }
        if a["agent.tool.outside_workdir_paths"]:
            d["agent.tool.outside_workdir_paths"] = clip(
                ",".join(a["agent.tool.outside_workdir_paths"]), redact, 2000
            )
        if a["agent.tool.failed"]:
            d["error.type"] = a["agent.tool.error_class"]
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _id(rid, t["id"], n=8),
                "parentSpanId": root_id,
                "name": f"execute_tool {t['name']}",
                "kind": 1,
                "startTimeUnixNano": str(t["start"]),
                "endTimeUnixNano": str(t["end"]),
                "attributes": attrs(d),
                "status": {"code": 2 if a["agent.tool.failed"] else 0},
            }
        )
    check = {
        "run_id": rid,
        "chat_input_tokens": chat_in,
        "analysis_input_tokens": sig["agent.usage.input_tokens"],
        "tool_spans": len(parsed["tools"]),
        "analysis_tool_calls": run["tool_calls"],
        "signals_match": loaded["signals"] == sig,
    }
    return spans, check


def analysis_commit() -> str:
    rel = ANALYSIS.relative_to(HERE.parent.parent)
    root = HERE.parent.parent
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", str(rel)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    sha = subprocess.run(
        ["git", "log", "-1", "--format=%h", "--", str(rel)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"{sha}-dirty" if dirty or not sha else sha


def body(spans: list[dict]) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [attr("service.name", DATASET)]},
                "scopeSpans": [{"scope": {"name": "efficiency_honeycomb"}, "spans": spans}],
            }
        ]
    }


def post(spans: list[dict], key: str) -> int:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body(spans)).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-honeycomb-team": key,
            "x-honeycomb-dataset": DATASET,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def leftover_secrets(blob: str, known: str | None) -> dict[str, int]:
    """Credential findings still present in the serialised payload. The
    redaction markers themselves are ignored."""
    found = ea.scan_secrets(blob, known)
    return {k: n for k, n in found.items() if n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", metavar="PAYLOAD", type=Path, help="write the payload, no send")
    ap.add_argument("--check", action="store_true", help="build and verify only, no send")
    ap.add_argument("--limit", type=int, default=None, help="first N runs only (for testing)")
    args = ap.parse_args()

    known = ea.load_known_secret()
    if known is None:
        raise SystemExit(
            "FAIL: the Kibana API key is not readable from elastic-start-local/.env.seed, "
            "so its redaction cannot be verified. Seed the stack (scripts/stack.sh up && "
            "scripts/stack.sh seed) before sending."
        )
    analysis = json.loads(ANALYSIS.read_text())
    runs = analysis["runs"][: args.limit] if args.limit else analysis["runs"]
    if any("agent" not in r for r in runs):
        raise SystemExit(
            "FAIL: analysis.json has no agent.* signals; regenerate it with "
            "scripts/experiment/efficiency_analyze.py"
        )
    commit = analysis_commit()
    redact = Redactor(known)
    built, checks = [], []
    for r in runs:
        spans, chk = run_spans(r, known, redact, commit)
        built.append(spans)
        checks.append(chk)

    blob = json.dumps(built, ensure_ascii=False)
    n_spans = sum(len(b) for b in built)
    convs = {
        a["value"]["stringValue"]
        for b in built
        for s in b
        for a in s["attributes"]
        if a["key"] == "gen_ai.conversation.id"
    }
    tok_bad = [c["run_id"] for c in checks if c["chat_input_tokens"] != c["analysis_input_tokens"]]
    tool_bad = [c["run_id"] for c in checks if c["tool_spans"] != c["analysis_tool_calls"]]
    sig_bad = [c["run_id"] for c in checks if not c["signals_match"]]
    left = leftover_secrets(blob, known)
    print(f"analysis commit: {commit}")
    print(f"runs {len(runs)}, spans {n_spans}, distinct conversations {len(convs)}")
    print(f"payload {len(blob.encode()) / 1e6:.1f} MB, redaction markers {blob.count('[REDACTED')}")
    print(f"chat input tokens != analysis agent.usage.input_tokens: {len(tok_bad)} runs")
    print(f"execute_tool spans != analysis tool_calls: {len(tool_bad)} runs")
    print(f"re-derived run signals != analysis.json: {len(sig_bad)} runs")
    print(
        f"known key occurrences in payload: {blob.count(known)}; credential patterns left: {left}"
    )
    failed = bool(tok_bad or tool_bad or sig_bad or left or known in blob)
    failed |= len(convs) != len(runs)
    if failed:
        print("FAIL: payload checks did not pass; nothing sent", file=sys.stderr)
        return 1
    if args.dry_run:
        args.dry_run.write_text(json.dumps(body([s for b in built for s in b]), ensure_ascii=False))
        print(f"wrote {args.dry_run}")
    if args.dry_run or args.check:
        return 0
    if commit.endswith("-dirty"):
        raise SystemExit(
            "FAIL: analysis.json has uncommitted changes; commit it so study.analysis_commit "
            "names the numbers being sent."
        )
    key = os.environ.get("HONEYCOMB_API_KEY")
    if not key:
        raise SystemExit("FAIL: HONEYCOMB_API_KEY is not set (a key that can send events).")
    batch: list[dict] = []
    size = 0
    sent = 0
    for spans in built:
        n = len(json.dumps(spans).encode())
        if batch and size + n > BATCH_BYTES:
            print(f"sent {len(batch)} spans: HTTP {post(batch, key)}")
            sent += len(batch)
            batch, size = [], 0
        batch.extend(spans)
        size += n
    if batch:
        print(f"sent {len(batch)} spans: HTTP {post(batch, key)}")
        sent += len(batch)
    print(f"done: {sent} spans to dataset {DATASET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
