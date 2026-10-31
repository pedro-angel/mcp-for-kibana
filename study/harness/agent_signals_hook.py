"""Claude Code hook: classify every tool call as it happens and emit the
``agent.*`` signals (SIGNALS.md) as one OTel span per call, in the run's trace.

    <python> agent_signals_hook.py <config.json>     (stdin: the hook payload)

Registered by efficiency_run.py --live-telemetry through --settings for
PostToolUse, PostToolUseFailure, PermissionDenied and Stop. The classification
is efficiency_analyze.annotate itself, run over the calls seen so far, so the
live signals and the transcript-derived ones come from the same code: a call's
first-attempt, retry and duplicate flags depend only on earlier calls, and a
later success that recovers an earlier failure is reported on the later call
(agent.tool.recovers) because a sent event cannot be changed.

The hook prints nothing and always exits 0: Claude Code adds nothing to the
model's context for a silent PostToolUse/Stop hook, so the model sees what it
saw in the first study. It sends to the runner's loopback relay only (which
scrubs secrets); errors go to <state>.errors, never to stdout or stderr.

config.json (written per run by the runner, mode 0600): relay, service,
resource (dict), state (path), known_secret (the run's Kibana key, used only
for exact-match exposure detection, never sent), workdir (the run's working
directory; outside-workdir paths are judged against it).
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
import time
from pathlib import Path

T0 = time.time()
RESULT_CAP = 65536  # chars of a tool result kept in the state for re-classification

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _hex(n: int) -> str:
    return os.urandom(n).hex()


def _text(resp) -> str:
    """A tool_response as the text the model saw (Bash: stdout, then stderr)."""
    if isinstance(resp, dict) and ("stdout" in resp or "stderr" in resp):
        out = resp.get("stdout") or ""
        err = resp.get("stderr") or ""
        return out + ("\n" + err if err else "")
    import efficiency_analyze as ea

    if isinstance(resp, dict) and "content" in resp:
        return ea.as_text(resp["content"])
    return ea.as_text(resp)


def to_call(payload: dict) -> dict | None:
    """The hook payload as a call in efficiency_analyze.parse_transcript's shape."""
    event = payload.get("hook_event_name")
    if event not in ("PostToolUse", "PostToolUseFailure", "PermissionDenied"):
        return None
    end = time.time_ns()
    dur = int(payload.get("duration_ms") or 0)
    resp = payload.get("tool_response")
    if event == "PostToolUse":
        result, is_error = _text(resp), False
    elif event == "PostToolUseFailure":
        result, is_error = str(payload.get("error") or ""), True
    else:
        result = str(payload.get("reason") or "Permission to use this tool has been denied")
        is_error = True
    return {
        "id": payload.get("tool_use_id") or _hex(8),
        "name": payload.get("tool_name") or "?",
        "input": payload.get("tool_input") or {},
        "chat_id": None,
        "start": end - dur * 1_000_000,
        "end": end,
        "result": result[:RESULT_CAP],
        "is_error": is_error,
        "answered": True,
        "meta": resp if isinstance(resp, dict) else {},
        "interrupted": bool(payload.get("is_interrupt")),
    }


def classify(state: dict, call: dict, denied_now: bool, known: str | None) -> dict:
    """Append the call to the run's state and return its agent.* dict, plus
    agent.tool.recovers: earlier failed calls this call recovers."""
    import efficiency_analyze as ea

    if denied_now:
        state["denied"].append(call["id"])
    before = {t["id"]: t.get("recovered") for t in state["tools"]}
    state["tools"].append(call)
    parsed = {"cwd": state.get("cwd"), "chats": [], "tools": state["tools"]}
    ea.annotate(parsed, set(state["denied"]), known)
    recovers = []
    for t in state["tools"]:
        rec = t["agent"]["agent.tool.recovered"]
        if rec and not before.get(t["id"]) and t["id"] != call["id"]:
            recovers.append(t["id"])
        t["recovered"] = rec
    sig = dict(call["agent"])
    sig["agent.tool.recovers"] = recovers
    return sig


def secret_attrs(found: dict[str, dict[str, int]]) -> dict:
    """agent.secret.* for one span from {location: {kind: count}}
    (efficiency_honeycomb._secret_attrs, repeated to keep the hook's imports
    to the standard library and efficiency_analyze)."""
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


def tool_attributes(call: dict, sig: dict) -> dict:
    """Flat span attributes: GenAI names joinable to Claude Code's own
    claude_code.tool span (gen_ai.tool.call.id), then the agent.* signals."""
    attrs = {
        "gen_ai.tool.name": call["name"],
        "gen_ai.tool.call.id": call["id"],
        "gen_ai.tool.type": "extension" if call["name"].startswith("mcp__") else "function",
    }
    if call["name"].startswith("mcp__"):  # mcp__<server>__<tool>: which server answered
        attrs["agent.tool.mcp_server"] = call["name"].split("__")[1]
    for k, v in sig.items():
        if k.startswith("agent.secret.") or v is None:
            continue
        attrs[k] = ",".join(v) if isinstance(v, list) else v
    attrs["agent.tool.outside_workdir"] = bool(sig["agent.tool.outside_workdir_paths"])
    attrs.update(
        secret_attrs(
            {
                "tool_arguments": sig["agent.secret.in_arguments"],
                "tool_result": sig["agent.secret.in_result"],
            }
        )
    )
    if sig["agent.tool.failed"]:
        attrs["error.type"] = sig["agent.tool.error_class"]
    return {k: v for k, v in attrs.items() if v != "" and v != []}


def _otlp_attr(k: str, v) -> dict:
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": str(v)}}


def span_body(
    name: str, start_ns: int, end_ns: int, attrs: dict, resource: dict, traceparent: str | None
) -> dict:
    """One OTLP/JSON span, parented under TRACEPARENT when it is valid."""
    parts = (traceparent or "").split("-")
    span = {
        "traceId": parts[1] if len(parts) == 4 else _hex(16),
        "spanId": _hex(8),
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(max(end_ns, start_ns)),
        "attributes": [_otlp_attr(k, v) for k, v in attrs.items()],
        "status": {"code": 2 if attrs.get("agent.tool.failed") else 0},
    }
    if len(parts) == 4:
        span["parentSpanId"] = parts[2]
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_otlp_attr(k, v) for k, v in resource.items()]},
                "scopeSpans": [{"scope": {"name": "agent_signals_hook"}, "spans": [span]}],
            }
        ]
    }


def post(relay: str, body: dict) -> None:
    """POST to the loopback relay over a plain socket (urllib's import alone
    costs ~40 ms per hook run)."""
    hostport = relay.split("://", 1)[1].rstrip("/")
    host, port = hostport.rsplit(":", 1)
    data = json.dumps(body).encode()
    head = (
        f"POST /v1/traces HTTP/1.1\r\nHost: {hostport}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n"
    ).encode()
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(head + data)
        while sock.recv(4096):
            pass


def handle(payload: dict, cfg: dict, environ) -> dict | None:
    """Classify one hook event, update the state, return the span body sent
    (None for events that carry no signal)."""
    state_path = Path(cfg["state"])
    known = cfg.get("known_secret") or None
    resource = {**cfg["resource"], "service.name": cfg["service"], "study.component": "hook"}
    parent = environ.get("TRACEPARENT")
    event = payload.get("hook_event_name")
    with open(state_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # parallel tool calls run hooks concurrently
        fh.seek(0)
        raw = fh.read()
        state = json.loads(raw) if raw.strip() else {"tools": [], "denied": [], "hook_ms": []}
        # The runner's work directory when the config names it: the payload's
        # cwd is Claude Code's current directory, which a `cd sub` moves.
        state.setdefault("cwd", cfg.get("workdir") or payload.get("cwd"))
        if event == "Stop":
            import efficiency_analyze as ea

            found = ea.scan_secrets(payload.get("last_assistant_message") or "", known)
            attrs = {
                "agent.hook.event": "Stop",
                "agent.tool.calls_seen": len(state["tools"]),
                **secret_attrs({"model_text": found}),
            }
            now = time.time_ns()
            body = span_body("agent.turn_end", now, now, attrs, resource, parent)
        else:
            call = to_call(payload)
            if call is None:
                return None
            sig = classify(state, call, event == "PermissionDenied", known)
            attrs = tool_attributes(call, sig)
            attrs["agent.hook.event"] = event
            attrs["agent.tool.interrupted"] = call.pop("interrupted")
            attrs["agent.hook.classify_ms"] = round((time.time() - T0) * 1000, 2)
            body = span_body(
                f"agent.tool {call['name']}", call["start"], call["end"], attrs, resource, parent
            )
        state["hook_ms"].append(round((time.time() - T0) * 1000, 2))
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(state))
    post(cfg["relay"], body)
    return body


_ROLLUP_KEYS = (
    "agent.tool.",
    "agent.fs.",
    "agent.permission.",
    "agent.secret.",
    "agent.harness.backgrounded_calls",
    "agent.harness.timed_out_calls",
    "agent.time.tool_ms",
    "agent.time.tool_busy_ms",
    "agent.time.wait_ms",
    "agent.usage.tool_result_bytes",
)


def rollups(state: dict, known: str | None = None) -> dict:
    """Run-level agent.* signals from the calls the hook classified, through
    efficiency_analyze.run_signals itself (tool-derived keys only: model calls,
    tokens and timing come from Claude Code's own telemetry and the stream's
    result event). Model-text secrets come from the Stop hook and the relay."""
    import efficiency_analyze as ea

    tools = state.get("tools") or []
    if not tools:
        return {"agent.tool.calls": 0}
    parsed = {"cwd": state.get("cwd"), "chats": [], "tools": tools, "version": None}
    ea.annotate(parsed, set(state.get("denied") or []), known)
    full = ea.run_signals(parsed, {}, [], [], min(t["start"] for t in tools))
    out = {k: v for k, v in full.items() if k.startswith(_ROLLUP_KEYS)}
    ms = sorted(state.get("hook_ms") or [])
    if ms:
        out["agent.hook.invocations"] = len(ms)
        out["agent.hook.p50_ms"] = ms[len(ms) // 2]
        out["agent.hook.max_ms"] = ms[-1]
    return out


def main() -> int:
    devnull = open(os.devnull, "w")  # noqa: SIM115 (held for the process lifetime)
    sys.stdout = sys.stderr = devnull
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        cfg = json.loads(Path(cfg_path).read_text())
        handle(json.load(sys.stdin), cfg, os.environ)
    except BaseException:  # never disturb the run: no output, exit 0
        try:
            import traceback

            with open(str(cfg_path) + ".errors", "a") as f:
                f.write(traceback.format_exc()[-2000:] + "\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    os._exit(main())
