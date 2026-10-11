#!/usr/bin/env python3
"""Analyse the efficiency study: with vs without the MCP server.

Reads every run directory under ``runs/efficiency/full/bNN/<run_id>/`` (the
pilot is excluded) and writes ``runs/efficiency/full/analysis.json``.

Every metric is re-derived from ``transcript.jsonl`` rather than taken from
``record.json``: ``efficiency_run.parse_stream`` reads only the LAST ``result``
event, and runs where a hung background Bash task was killed at the CLI's
10-minute limit emit a second, one-turn result event. The primary measure is
the FIRST result event (the model's own end of turn). A sensitivity variant
covers all result events: turns, durations and per-turn ``usage`` tokens are
summed; ``total_cost_usd`` is cumulative across a session, so the last value
is the all-events cost.

Success comes from ``record.json`` (``success``, strict). The lenient pass
also counts a run whose only failure reason is "rule disabled".

Each run also carries the agent-health signals defined in SIGNALS.md: a flat
``agent`` dict (outcome, effort, time split, tool reliability with error
classes, retries and recovery, secret exposure, permission denials,
out-of-workdir paths, harness anomalies), a ``study`` dict and ``tool_stats``
per tool. ``signals`` holds their per model x arm and pooled summaries, with
bootstrap comparisons of the medians where with/without is meaningful. The
secret scan matches the Kibana API key the runs used (read from the seed file,
never written out) and generic credential patterns.

Deterministic: fixed bootstrap seed, sorted inputs, sorted JSON keys.
Stdlib only.

    uv run python scripts/experiment/efficiency_analyze.py
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE / "runs" / "efficiency" / "full"

SEED = 20261011
RESAMPLES = 10_000
ARMS = ("with-mcp", "no-mcp")
MCP_PREFIX = "mcp__"
ENABLE_TOOL = "mcp__mcp-for-kibana__enable_alert_rule"
CREATE_TOOL = "mcp__mcp-for-kibana__create_alert_rule"

# Metrics summarised per model x arm and compared with a bootstrap.
METRICS = (
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "total_tokens",
    "num_turns",
    "tool_calls",
    "mcp_calls",
    "bash_calls",
    "other_calls",
    "duration_ms",
    "duration_api_ms",
    "wall_s",
)
# agent.* signals described per model x arm, and compared with a bootstrap
# (all runs; separate RNG stream so the METRICS comparisons are unchanged).
SIGNAL_DESCRIBE = (
    "agent.model.calls",
    "agent.tool.calls",
    "agent.usage.input_tokens_per_call",
    "agent.usage.cache_read_share",
    "agent.usage.tool_result_bytes",
    "agent.usage.reasoning_tokens",
    "agent.time.wall_ms",
    "agent.time.model_ms",
    "agent.time.tool_ms",
    "agent.time.tool_busy_ms",
    "agent.time.wait_ms",
    "agent.time.other_ms",
    "agent.time.model_share",
    "agent.time.tool_share",
    "agent.time.first_tool_ms",
    "agent.time.ttft_ms",
    "agent.tool.failure_rate",
    "agent.tool.failures",
    "agent.tool.retries",
)
SIGNAL_COMPARE = (
    "agent.model.calls",
    "agent.usage.input_tokens_per_call",
    "agent.usage.tool_result_bytes",
    "agent.time.model_ms",
    "agent.time.tool_ms",
    "agent.time.tool_busy_ms",
    "agent.time.other_ms",
    "agent.time.first_tool_ms",
    "agent.tool.failure_rate",
)
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


# ---------------------------------------------------------------- per run


def _events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _usage_metrics(result: dict) -> dict:
    usage = result.get("usage") or {}
    out = {k: usage.get(k) or 0 for k in TOKEN_KEYS}
    out["total_tokens"] = sum(out.values())
    return out


# ---------------------------------------------------------------- agent signals
#
# Generic agent-health signals (``agent.*``), defined in SIGNALS.md next to this
# file. ``parse_transcript`` rebuilds the model calls and tool calls of one run
# up to its first result event; ``annotate`` derives the per-call signals and
# ``run_signals`` aggregates them. efficiency_honeycomb.py imports the same
# functions for its span attributes, so the report and the traces agree.

ERROR_CLASSES = (
    "validation",
    "http_4xx",
    "http_5xx",
    "timeout",
    "refused",
    "unknown_tool",
    "command_failed",
    "other",
)
SECRET_KINDS = (
    "known_secret",
    "anthropic_key",
    "auth_header",
    "credential_assignment",
    "opaque_token",
)
SECRET_LOCATIONS = ("tool_result", "tool_arguments", "model_text")
SHELL_TOOLS = frozenset({"Bash"})
FILE_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit"})
HARNESS_DIR_PREFIX = "/tmp/claude-"  # the CLI's own scratch and task-output dir

_KIBANA_ERR = re.compile(r'"statusCode"\s*:\s*([45]\d\d)\s*,\s*"error"\s*:\s*"([^"]*)"')
_ES_ERR = re.compile(r'"root_cause".{0,4000}?"status"\s*:\s*([45]\d\d)', re.S)
_HTTP_LINE = re.compile(r"(?m)^HTTP/[\d.]+\s+([45]\d\d)\b")
_VALIDATION = re.compile(
    r"(?i)validation error|rejected the payload|params invalid|\[request body\]|"
    r"\binvalid\b|is required|expected value of type|schema"
)
_REFUSED = re.compile(r"(?i)permission to use|has been denied|not allowed|<tool_use_error>Blocked:")
_EXIT = re.compile(r"^(?:Error: )?Exit code (\d+)")
_SECRET_NAME = r"[A-Za-z_][A-Za-z0-9_]*(?:API_?KEY|APIKEY|_KEY|TOKEN|SECRET|PASSWORD|PASSWD)"
_VALUE = r"[^\s\"'$;&|,}{)(<>`*\[\]\\]"
_SECRET_RX = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    (
        "auth_header",
        re.compile(
            r"(?i)\b(?:authorization|x-api-key)[ \t]*:[ \t]*(?:(?:apikey|bearer|basic)[ \t]+)?"
            + _VALUE
            + r"{16,}"
        ),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b" + _SECRET_NAME + r"[ \t]*=[ \t]*[\"']?" + _VALUE + r"{8,}"
            r"|\"(?:api_?key|token|secret|password)\"[ \t]*:[ \t]*\"" + _VALUE + r"{8,}\""
        ),
    ),
    (
        "opaque_token",
        re.compile(
            r"(?i)(?:api[_-]?key|token|secret|password|bearer|credential)s?[\\\"']{0,3}"
            r"[ \t]*[:=]?[ \t]*[\\\"']{0,3}(?:(?:apikey|bearer|basic)[ \t]+)?"
            r"([A-Za-z0-9+/_-]{32,}={0,2})(?![A-Za-z0-9+/_-])"
        ),
    ),
)
_OUTSIDE = re.compile(
    r"(?:^|[\s\"'=<>|;(:,])"
    r"(/(?:home|root|Users|etc|var|opt|proc|srv|mnt|usr/local|tmp)(?:/[\w.@+~-]*)*)"
)
_ENV_DUMP = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:env|printenv)\s*(?:$|[|;&>)])|/proc/[\w/]*environ|\bexport\s+-p\b"
)
_WAIT = re.compile(r"\bsleep\s+\d|\bwatch\s|\bwait\b\s*(?:$|[;&|])")
_SHELL_HTTP = re.compile(r"\b(?:curl|wget)\b|https?://|urllib|\brequests\.")
_API_PATH = re.compile(r"(/(?:s/[\w-]+/)?(?:api|internal)/[\w./{}:-]*)")
_ID_SEG = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{16,}|\d+)(?=/|$)")


def ts_ns(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000


def as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return json.dumps(content) if content is not None else ""


def tool_category(name: str) -> str:
    if name.startswith(MCP_PREFIX):
        return "mcp"
    if name in SHELL_TOOLS:
        return "shell"
    return "file" if name in FILE_TOOLS else "other"


def parse_transcript(events: list[dict], start_ns: int, prompt: str) -> dict:
    """Model calls and tool calls up to the first result event.

    A model call is one assistant message id; it starts at the latest input
    (prompt, tool result) and ends at its last streamed block. A tool call runs
    from its tool_use block to its tool_result. ``input`` holds only the
    messages that are new to that call (prompt or tool results)."""
    chats: dict[str, dict] = {}
    order: list[str] = []
    tools: list[dict] = []
    pending: dict[str, dict] = {}
    new_input = [{"role": "user", "parts": [{"type": "text", "content": prompt}]}]
    last_input = start_ns
    cwd = version = None
    for e in events:
        if e.get("type") == "result":
            break
        if e.get("type") == "system" and e.get("subtype") == "init" and cwd is None:
            cwd, version = e.get("cwd"), e.get("claude_code_version")
        msg = e.get("message")
        ts = e.get("timestamp")
        if not ts or not isinstance(msg, dict):
            continue
        t = ts_ns(ts)
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if e.get("type") == "assistant":
            mid = msg.get("id")
            c = chats.get(mid)
            if c is None:
                c = chats[mid] = {
                    "id": mid,
                    "index": len(order),
                    "model": msg.get("model"),
                    "start": last_input,
                    "end": t,
                    "usage": msg.get("usage") or {},
                    "thinking": False,
                    "input": new_input,
                    "output": [],
                    "text": [],
                    "tool_ids": [],
                }
                new_input = []
                order.append(mid)
            c["end"] = max(c["end"], t)
            for b in content:
                if not isinstance(b, dict):
                    continue
                kind = b.get("type")
                if kind in ("thinking", "redacted_thinking") and not c["thinking"]:
                    c["thinking"] = True
                    c["output"].append(
                        {"type": "reasoning", "content": "[thinking occurred; not recorded]"}
                    )
                elif kind == "text" and b.get("text"):
                    c["text"].append(b["text"])
                    c["output"].append({"type": "text", "content": b["text"]})
                elif kind == "tool_use":
                    c["tool_ids"].append(b["id"])
                    c["output"].append(
                        {
                            "type": "tool_call",
                            "id": b["id"],
                            "name": b.get("name"),
                            "arguments": b.get("input"),
                        }
                    )
                    pending[b["id"]] = {
                        "id": b["id"],
                        "name": b.get("name", "?"),
                        "input": b.get("input") or {},
                        "chat_id": mid,
                        "start": t,
                        "end": t,
                        "result": "",
                        "is_error": False,
                        "answered": False,
                        "meta": {},
                    }
                    tools.append(pending[b["id"]])
        elif e.get("type") == "user":
            raw = msg.get("content")
            if isinstance(raw, str) and raw.strip():
                new_input.append({"role": "user", "parts": [{"type": "text", "content": raw}]})
            tur = e.get("tool_use_result")
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    text = as_text(b.get("content"))
                    new_input.append(
                        {
                            "role": "tool",
                            "parts": [
                                {
                                    "type": "tool_call_response",
                                    "id": b.get("tool_use_id"),
                                    "result": text,
                                }
                            ],
                        }
                    )
                    tc = pending.pop(b.get("tool_use_id"), None)
                    if tc is not None:
                        tc.update(
                            end=max(t, tc["start"]),
                            result=text,
                            is_error=bool(b.get("is_error")),
                            answered=True,
                            meta=tur if isinstance(tur, dict) else {},
                        )
                elif b.get("type") == "text" and b.get("text"):
                    new_input.append(
                        {"role": "user", "parts": [{"type": "text", "content": b["text"]}]}
                    )
            last_input = t
    for c in chats.values():
        c["text"] = "\n".join(c["text"])
    return {
        "cwd": cwd,
        "version": version,
        "chats": [chats[m] for m in order],
        "tools": tools,
    }


def http_error(text: str) -> tuple[int | None, str]:
    """First HTTP error status (>= 400) visible in a tool's output, with its
    message. Matches Kibana's compact error body, Elasticsearch's error body
    and raw HTTP status lines; a status printed any other way is missed."""
    m = _KIBANA_ERR.search(text)
    if m:
        tail = text[m.end() : m.end() + 400]
        return int(m.group(1)), f"{m.group(2)} {tail}"
    m = _ES_ERR.search(text)
    if m:
        return int(m.group(1)), text[max(0, m.start() - 200) : m.end()]
    m = _HTTP_LINE.search(text)
    if m:
        return int(m.group(1)), text[m.start() : m.start() + 400]
    return None, ""


def classify(tc: dict, denied: set) -> dict:
    """failed / error_class / error_source / http_status for one tool call."""
    text = tc["result"]
    meta = tc["meta"]
    status, detail = http_error(text)
    out = {"failed": False, "error_class": None, "error_source": None, "http_status": status}
    if tc["id"] in denied:
        out.update(failed=True, error_class="refused", error_source="harness")
    elif meta.get("timedOutAfterMs") or meta.get("backgroundTaskId"):
        out.update(failed=True, error_class="timeout", error_source="timeout")
    elif not tc["answered"]:
        out.update(failed=True, error_class="other", error_source="no_result")
    elif tc["is_error"]:
        out.update(failed=True, error_source="harness")
        shell = tc["name"] in SHELL_TOOLS
        if "No such tool available" in text:
            out["error_class"] = "unknown_tool"
        elif _REFUSED.search(text) and status is None:
            out["error_class"] = "refused"
        elif not shell and _VALIDATION.search(text) and status in (None, 400, 422):
            out["error_class"] = "validation"
        elif status is not None:
            out["error_class"] = _status_class(status, detail)
        elif re.search(r"(?i)timed? ?out", text):
            out["error_class"] = "timeout"
        elif _EXIT.search(text):
            out["error_class"] = "command_failed"
        else:
            out["error_class"] = "other"
    elif status is not None:
        out.update(failed=True, error_source="output", error_class=_status_class(status, detail))
    return out


def _status_class(status: int, detail: str) -> str:
    if status >= 500:
        return "http_5xx"
    if status in (400, 422) and _VALIDATION.search(detail):
        return "validation"
    return "http_4xx"


def call_target(name: str, args: dict) -> str:
    """What a call acts on, for retry and first-attempt tracking: the tool name,
    or for a shell call its first HTTP method + API path (ids folded), else its
    first program."""
    if name not in SHELL_TOOLS:
        return name
    cmd = str(args.get("command", ""))
    m = _API_PATH.search(cmd)
    if m and _SHELL_HTTP.search(cmd):
        path = _ID_SEG.sub("/{id}", m.group(1).split("?")[0].rstrip("/."))
        mm = re.search(r"(?:-X|--request)\s*['\"]?([A-Z]+)", cmd)
        method = mm.group(1) if mm else ("POST" if re.search(r"\s(?:-d|--data)", cmd) else "GET")
        return f"{name} {method} {path}"
    words = [w for w in re.split(r"[\s;&|()]+", cmd) if w and "=" not in w]
    words = [w for w in words if w not in ("cd", "export", "set", "then", "do", "for")]
    return f"{name} {words[0] if words else ''}".strip()


def scan_secrets(text: str, known: str | None) -> dict[str, int]:
    """Count credential-looking values per kind. ``known_secret`` is an exact
    match of a registered secret value; the other kinds are generic patterns
    whose value is a literal (a ``$VAR`` reference does not count). Generic
    matches that overlap an earlier kind's match are not counted twice."""
    found: dict[str, int] = {}
    if not text:
        return found
    if known and known in text:
        found["known_secret"] = text.count(known)
    taken: list[tuple[int, int]] = []
    for kind, rx in _SECRET_RX:
        for m in rx.finditer(text):
            span = m.span()
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            if kind == "opaque_token":
                tok = m.group(1)
                mixed = all(any(f(c) for c in tok) for f in (str.isdigit, str.isupper, str.islower))
                if not mixed or max(len(seg) for seg in tok.split("/")) < 16:
                    continue
            taken.append(span)
            found[kind] = found.get(kind, 0) + 1
    return found


def outside_paths(text: str, cwd: str | None) -> list[str]:
    """Absolute filesystem paths outside the working directory and the
    harness's own directory (/dev/* is never matched)."""
    out = []
    for p in _OUTSIDE.findall(text):
        if cwd and (p == cwd or p.startswith(cwd.rstrip("/") + "/")):
            continue
        if p.startswith(HARNESS_DIR_PREFIX):
            continue
        out.append(p)
    return sorted(set(out))


def path_kind(p: str) -> str:
    root = p.split("/")[1]
    if root in ("home", "root", "Users"):
        return "home"
    return "temp" if root == "tmp" else "system"


def annotate(parsed: dict, denied: set, known: str | None) -> None:
    """Attach per-call ``agent`` signal dicts to every tool and model call."""
    cwd = parsed["cwd"]
    seen_args: dict[str, str] = {}
    seen_targets: set[str] = set()
    last_failed: dict[str, str] = {}  # target -> id of the latest failed call
    tools = parsed["tools"]
    for i, tc in enumerate(tools):
        args_json = json.dumps(tc["input"], sort_keys=True, ensure_ascii=False)
        cls = classify(tc, denied)
        target = call_target(tc["name"], tc["input"])
        cmd = str(tc["input"].get("command", "")) if tc["name"] in SHELL_TOOLS else ""
        sec_args = scan_secrets(args_json, known)
        sec_res = scan_secrets(tc["result"], known)
        paths = outside_paths(cmd if cmd else args_json, cwd)
        sig = {
            "agent.tool.index": i,
            "agent.tool.category": tool_category(tc["name"]),
            "agent.tool.target": target,
            "agent.tool.duration_ms": (tc["end"] - tc["start"]) / 1e6,
            "agent.tool.result_bytes": len(tc["result"].encode()),
            "agent.tool.error": tc["is_error"],
            "agent.tool.failed": cls["failed"],
            "agent.tool.error_class": cls["error_class"],
            "agent.tool.error_source": cls["error_source"],
            "agent.tool.http_status": cls["http_status"],
            "agent.tool.hidden_failure": cls["failed"] and cls["error_source"] == "output",
            "agent.tool.first_attempt": target not in seen_targets,
            "agent.tool.retry_of": last_failed.get(target),
            "agent.tool.duplicate_of": seen_args.get(f"{tc['name']}\0{args_json}"),
            "agent.tool.permission_denied": tc["id"] in denied,
            "agent.tool.backgrounded": bool(tc["meta"].get("backgroundTaskId")),
            "agent.tool.timed_out": bool(tc["meta"].get("timedOutAfterMs")),
            "agent.tool.shell_http": bool(cmd and _SHELL_HTTP.search(cmd)),
            "agent.tool.env_enumeration": bool(cmd and _ENV_DUMP.search(cmd)),
            "agent.tool.deliberate_wait": bool(cmd and _WAIT.search(cmd)),
            "agent.tool.outside_workdir_paths": paths,
            "agent.secret.in_arguments": sec_args,
            "agent.secret.in_result": sec_res,
            "agent.tool.recovered": None,
        }
        sig["agent.tool.retry"] = sig["agent.tool.retry_of"] is not None
        sig["agent.tool.first_attempt_failed"] = sig["agent.tool.first_attempt"] and cls["failed"]
        seen_targets.add(target)
        seen_args.setdefault(f"{tc['name']}\0{args_json}", tc["id"])
        if cls["failed"]:
            last_failed[target] = tc["id"]
        else:
            last_failed.pop(target, None)
        tc["agent"] = sig
    # Recovery: a failed call recovers when a later call on the same target succeeds.
    for i, tc in enumerate(tools):
        if tc["agent"]["agent.tool.failed"]:
            tgt = tc["agent"]["agent.tool.target"]
            tc["agent"]["agent.tool.recovered"] = any(
                not u["agent"]["agent.tool.failed"] and u["agent"]["agent.tool.target"] == tgt
                for u in tools[i + 1 :]
            )
    by_chat: dict[str, int] = {}
    for tc in tools:
        by_chat[tc["chat_id"]] = by_chat.get(tc["chat_id"], 0) + 1
    for c in parsed["chats"]:
        u = c["usage"]
        cr = int(u.get("cache_read_input_tokens") or 0)
        cc = int(u.get("cache_creation_input_tokens") or 0)
        it = int(u.get("input_tokens") or 0)
        c["agent"] = {
            "agent.model.call_index": c["index"],
            "agent.model.input_tokens": it + cr + cc,
            "agent.model.cache_read_tokens": cr,
            "agent.model.new_input_tokens": it + cc,
            "agent.model.thinking": c["thinking"],
            "agent.model.tool_calls": by_chat.get(c["id"], 0),
            "agent.model.finish_reason": "tool_use" if c["tool_ids"] else "end_turn",
            "agent.model.duration_ms": (c["end"] - c["start"]) / 1e6,
            "agent.secret.in_model_text": scan_secrets(c["text"], known),
        }


def _union_ms(intervals: list[tuple[int, int]]) -> float:
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total / 1e6


def run_signals(parsed: dict, first: dict, results: list[dict], events: list[dict], start: int):
    """Run-level ``agent.*`` signals (flat dict, attribute names as keys)."""
    tools, chats = parsed["tools"], parsed["chats"]
    sig = [t["agent"] for t in tools]
    usage = first.get("usage") or {}
    inp = sum(int(usage.get(k) or 0) for k in TOKEN_KEYS if k != "output_tokens")
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    n_tools, n_calls = len(tools), len(chats)
    failed = [s for s in sig if s["agent.tool.failed"]]
    wall = first.get("duration_ms")
    model_ms = first.get("duration_api_ms")
    tool_ms = _union_ms([(t["start"], t["end"]) for t in tools])
    busy_ms = _union_ms(
        [
            (t["start"], t["end"])
            for t in tools
            if not (t["agent"]["agent.tool.deliberate_wait"] or t["agent"]["agent.tool.timed_out"])
        ]
    )

    secret_hits: dict[str, int] = dict.fromkeys(SECRET_KINDS, 0)
    locations: dict[str, int] = dict.fromkeys(SECRET_LOCATIONS, 0)
    known_locations: set[str] = set()
    for s in sig:
        for loc, found in (
            ("tool_arguments", s["agent.secret.in_arguments"]),
            ("tool_result", s["agent.secret.in_result"]),
        ):
            for k, n in found.items():
                secret_hits[k] += n
            if found:
                locations[loc] += 1
            if "known_secret" in found:
                known_locations.add(loc)
    for c in chats:
        found = c["agent"]["agent.secret.in_model_text"]
        for k, n in found.items():
            secret_hits[k] += n
        if found:
            locations["model_text"] += 1
        if "known_secret" in found:
            known_locations.add("model_text")
    paths = sorted({p for s in sig for p in s["agent.tool.outside_workdir_paths"]})
    repeats: dict[str, int] = {}
    for s in sig:
        if s["agent.tool.duplicate_of"]:
            repeats[s["agent.tool.duplicate_of"]] = repeats.get(s["agent.tool.duplicate_of"], 1) + 1
    killed = sum(
        1
        for e in events
        if e.get("type") == "system"
        and e.get("subtype") == "task_notification"
        and e.get("status") in ("stopped", "killed", "failed")
    )
    rate_limited = sum(
        1
        for e in events
        if e.get("type") == "rate_limit_event"
        and (e.get("rate_limit_info") or {}).get("status") not in (None, "allowed")
    )
    first_tool = (min(t["start"] for t in tools) - start) / 1e6 if tools else None
    out = {
        # outcome
        "agent.run.end_reason": first.get("terminal_reason") or first.get("subtype"),
        "agent.run.stop_reason": first.get("stop_reason"),
        "agent.run.is_error": bool(first.get("is_error")),
        "agent.run.api_error_status": first.get("api_error_status"),
        "agent.run.safety_stops": first.get("safety_stops") or 0,
        # effort and cost
        "agent.model.calls": n_calls,
        "agent.harness.turns": first.get("num_turns"),
        "agent.tool.calls": n_tools,
        "agent.tool.calls_mcp": sum(s["agent.tool.category"] == "mcp" for s in sig),
        "agent.tool.calls_shell": sum(s["agent.tool.category"] == "shell" for s in sig),
        "agent.tool.calls_file": sum(s["agent.tool.category"] == "file" for s in sig),
        "agent.tool.calls_other": sum(s["agent.tool.category"] == "other" for s in sig),
        "agent.tool.distinct_tools": len({t["name"] for t in tools}),
        "agent.usage.input_tokens": inp,
        "agent.usage.output_tokens": int(usage.get("output_tokens") or 0),
        "agent.usage.cache_read_tokens": cache_read,
        "agent.usage.cache_creation_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "agent.usage.uncached_input_tokens": int(usage.get("input_tokens") or 0),
        "agent.usage.reasoning_tokens": int(
            (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0
        ),
        "agent.usage.cache_read_share": cache_read / inp if inp else None,
        "agent.usage.input_tokens_per_call": inp / n_calls if n_calls else None,
        "agent.usage.tool_result_bytes": sum(s["agent.tool.result_bytes"] for s in sig),
        "agent.cost.usd": first.get("total_cost_usd"),
        # time
        "agent.time.wall_ms": wall,
        "agent.time.model_ms": model_ms,
        "agent.time.tool_ms": tool_ms,
        "agent.time.tool_busy_ms": busy_ms,
        "agent.time.wait_ms": sum(
            s["agent.tool.duration_ms"] for s in sig if s["agent.tool.deliberate_wait"]
        ),
        "agent.time.other_ms": (wall - model_ms - tool_ms)
        if wall is not None and model_ms is not None
        else None,
        "agent.time.model_share": model_ms / wall if wall and model_ms is not None else None,
        "agent.time.tool_share": tool_ms / wall if wall else None,
        "agent.time.first_tool_ms": first_tool,
        "agent.time.ttft_ms": first.get("ttft_ms"),
        # reliability
        "agent.tool.errors": sum(s["agent.tool.error"] for s in sig),
        "agent.tool.failures": len(failed),
        "agent.tool.failure_rate": len(failed) / n_tools if n_tools else 0.0,
        "agent.tool.hidden_failures": sum(s["agent.tool.hidden_failure"] for s in sig),
        **{
            f"agent.tool.failures_{c}": sum(s["agent.tool.error_class"] == c for s in failed)
            for c in ERROR_CLASSES
        },
        "agent.tool.first_attempt_failures": sum(s["agent.tool.first_attempt_failed"] for s in sig),
        "agent.tool.retries": sum(s["agent.tool.retry"] for s in sig),
        "agent.tool.failures_recovered": sum(bool(s["agent.tool.recovered"]) for s in failed),
        "agent.tool.failures_unrecovered": sum(s["agent.tool.recovered"] is False for s in failed),
        "agent.tool.duplicate_calls": sum(bool(s["agent.tool.duplicate_of"]) for s in sig),
        "agent.tool.max_identical_calls": max(repeats.values(), default=1) if tools else 0,
        # safety
        "agent.secret.exposed": any(secret_hits.values()),
        "agent.secret.known_exposed": secret_hits["known_secret"] > 0,
        "agent.secret.exposures": sum(locations.values()),
        **{f"agent.secret.kind_{k}": secret_hits[k] for k in SECRET_KINDS},
        **{f"agent.secret.location_{k}": locations[k] for k in SECRET_LOCATIONS},
        "agent.secret.known_locations": ",".join(sorted(known_locations)),
        "agent.permission.denials": sum(s["agent.tool.permission_denied"] for s in sig),
        "agent.fs.outside_workdir_calls": sum(
            bool(s["agent.tool.outside_workdir_paths"]) for s in sig
        ),
        "agent.fs.outside_workdir_paths": len(paths),
        "agent.fs.outside_workdir_kinds": ",".join(sorted({path_kind(p) for p in paths})),
        "agent.tool.env_enumeration_calls": sum(s["agent.tool.env_enumeration"] for s in sig),
        "agent.tool.shell_http_calls": sum(s["agent.tool.shell_http"] for s in sig),
        # harness / runtime
        "agent.tool.wait_calls": sum(s["agent.tool.deliberate_wait"] for s in sig),
        "agent.harness.result_events": len(results),
        "agent.harness.backgrounded_calls": sum(s["agent.tool.backgrounded"] for s in sig),
        "agent.harness.timed_out_calls": sum(s["agent.tool.timed_out"] for s in sig),
        "agent.harness.unanswered_calls": sum(not t["answered"] for t in tools),
        "agent.harness.background_tasks_ended_abnormally": killed,
        "agent.harness.rate_limited_events": rate_limited,
        "agent.harness.version": parsed["version"],
    }
    return out


def tool_stats(tools: list[dict]) -> dict:
    """Per tool name: calls, failures, first-attempt failures, retries."""
    out: dict[str, dict[str, int]] = {}
    for t in tools:
        a = t["agent"]
        c = out.setdefault(
            t["name"], {"calls": 0, "failures": 0, "first_attempt_failures": 0, "retries": 0}
        )
        c["calls"] += 1
        c["failures"] += a["agent.tool.failed"]
        c["first_attempt_failures"] += a["agent.tool.first_attempt_failed"]
        c["retries"] += a["agent.tool.retry"]
    return dict(sorted(out.items()))


def load_known_secret() -> str | None:
    """The Kibana API key the runs were given (efficiency_run.load_seed);
    None when the seed file is absent. Never printed or written."""
    try:
        from efficiency_run import load_seed  # noqa: PLC0415 (sibling script)
    except ImportError:
        return None
    try:
        return load_seed()[1]
    except (OSError, KeyError):
        return None


def _denied(events: list[dict], results: list[dict]) -> set:
    """Denials: the stream's permission_denied events, unioned with every
    result event's list (record.json keeps only the last result's)."""
    denied = {
        e.get("tool_use_id") or e.get("uuid")
        for e in events
        if e.get("type") == "system" and e.get("subtype") == "permission_denied"
    }
    for r in results:
        denied.update(d.get("tool_use_id") for d in r.get("permission_denials") or [])
    denied.discard(None)
    return denied


def load_run(run_dir: Path, known: str | None) -> dict:
    """Everything one run's signals derive from: record, events, the parsed and
    annotated calls, the result events and the run's start time (ns)."""
    record = json.loads((run_dir / "record.json").read_text())
    events = _events(run_dir / "transcript.jsonl")
    results = [e for e in events if e.get("type") == "result"]
    start = ts_ns(record["ts_start"])
    prompt_path = run_dir / "prompt.txt"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    parsed = parse_transcript(events, start, prompt)
    denied = _denied(events, results)
    annotate(parsed, denied, known)
    first = results[0] if results else {}
    return {
        "record": record,
        "events": events,
        "results": results,
        "first": first,
        "start": start,
        "parsed": parsed,
        "denied": denied,
        "signals": run_signals(parsed, first, results, events, start),
    }


def derive_run(run_dir: Path, known: str | None = None) -> dict:
    loaded = load_run(run_dir, known)
    record, events, results = loaded["record"], loaded["events"], loaded["results"]
    first = loaded["first"]
    result_idx = [i for i, e in enumerate(events) if e.get("type") == "result"]
    cutoff = result_idx[0] if result_idx else len(events)

    tool_uses = [
        b
        for e in events[:cutoff]
        if e.get("type") == "assistant"
        for b in (e.get("message") or {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    names = [t.get("name", "?") for t in tool_uses]
    n_mcp = sum(1 for n in names if n.startswith(MCP_PREFIX))
    n_bash = names.count("Bash")

    denied = loaded["denied"]

    # How the alert rule got enabled (with-mcp mechanism; recorded for all).
    creates = [t for t in tool_uses if t.get("name") == CREATE_TOOL]
    enable_calls = names.count(ENABLE_TOOL)
    create_enabled = any((t.get("input") or {}).get("enabled") is True for t in creates)
    if enable_calls:
        enablement = "enable_alert_rule_call"
    elif create_enabled:
        enablement = "enabled_true_on_create"
    elif record.get("rule_enabled"):
        enablement = "enabled_other"
    elif record.get("rule_found"):
        enablement = "left_disabled"
    else:
        enablement = "no_rule"

    reasons = [r.strip() for r in (record.get("failure_reason") or "").split(";") if r.strip()]
    strict = bool(record.get("success"))
    lenient = strict or (bool(reasons) and all(r == "rule disabled" for r in reasons))

    primary = {
        "cost_usd": first.get("total_cost_usd"),
        "num_turns": first.get("num_turns"),
        "duration_ms": first.get("duration_ms"),
        "duration_api_ms": first.get("duration_api_ms"),
        **_usage_metrics(first),
    }
    all_usage = [_usage_metrics(r) for r in results]
    all_results = {
        "cost_usd": results[-1].get("total_cost_usd") if results else None,
        "num_turns": sum(r.get("num_turns") or 0 for r in results),
        "duration_ms": sum(r.get("duration_ms") or 0 for r in results),
        "duration_api_ms": sum(r.get("duration_api_ms") or 0 for r in results),
        **{k: sum(u[k] for u in all_usage) for k in (*TOKEN_KEYS, "total_tokens")},
    }

    return {
        "run_id": record["run_id"],
        "block": record["block"],
        "model": record["model_requested"],
        "arm": record["arm"],
        "models_reported": sorted((first.get("modelUsage") or {}).keys()),
        "n_result_events": len(results),
        "first_result_is_error": first.get("is_error"),
        **primary,
        "tool_calls": len(names),
        "mcp_calls": n_mcp,
        "bash_calls": n_bash,
        "other_calls": len(names) - n_mcp - n_bash,
        "wall_s": record.get("wall_s"),
        "wall_s_flag": "secondary: harness wall clock, includes CLI startup and any "
        "post-turn background-task wait",
        "all_results": all_results,
        "permission_denials": len(denied),
        "record_permission_denials": len(record.get("permission_denials") or []),
        "record_num_turns": record.get("num_turns"),
        "record_metrics_diverge": record.get("num_turns") != primary["num_turns"]
        or record.get("total_cost_usd") != primary["cost_usd"],
        "success_strict": strict,
        "success_lenient": lenient,
        "failure_reason": record.get("failure_reason"),
        "rule_enabled": record.get("rule_enabled"),
        "create_alert_rule_calls": len(creates),
        "enable_alert_rule_calls": enable_calls,
        "create_with_enabled_true": create_enabled,
        "rule_enablement": enablement,
        "agent": loaded["signals"],
        "tool_stats": tool_stats(loaded["parsed"]["tools"]),
        "study": {
            "study.run_id": record["run_id"],
            "study.block": record["block"],
            "study.model": record["model_requested"],
            "study.arm": record["arm"],
            "study.success_strict": strict,
            "study.success_lenient": lenient,
            "study.failure_reason": record.get("failure_reason") or "",
            "study.rule_enablement": enablement,
            "study.kibana_version": record.get("kibana_version"),
            "study.claude_code_version": loaded["parsed"]["version"],
            "study.runner_commit": record.get("git_sha"),
        },
    }


# ---------------------------------------------------------------- statistics


def _quartiles(xs: list[float]) -> tuple[float, float]:
    if len(xs) < 2:
        return (xs[0], xs[0]) if xs else (float("nan"), float("nan"))
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], q[2]


def describe(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    q1, q3 = _quartiles(xs)
    return {
        "n": len(xs),
        "median": statistics.median(xs),
        "mean": statistics.fmean(xs),
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "min": min(xs),
        "max": max(xs),
    }


def _ci(samples: list[float]) -> list[float] | None:
    s = sorted(x for x in samples if x == x)  # drop NaN
    if not s:
        return None
    lo = s[int(round(0.025 * (len(s) - 1)))]
    hi = s[int(round(0.975 * (len(s) - 1)))]
    return [lo, hi]


def compare(
    rng: random.Random, with_groups: list[list[float]], without_groups: list[list[float]]
) -> dict | None:
    """Difference and ratio of medians (with - without, with / without) with a
    percentile bootstrap CI. Each group is a stratum resampled independently
    (one stratum per model; three for the pooled line), then pooled."""
    if any(len(g) < 2 for g in (*with_groups, *without_groups)):
        return None
    w_all = [x for g in with_groups for x in g]
    o_all = [x for g in without_groups for x in g]
    mw, mo = statistics.median(w_all), statistics.median(o_all)
    diffs, ratios = [], []
    for _ in range(RESAMPLES):
        bw = [x for g in with_groups for x in rng.choices(g, k=len(g))]
        bo = [x for g in without_groups for x in rng.choices(g, k=len(g))]
        a, b = statistics.median(bw), statistics.median(bo)
        diffs.append(a - b)
        ratios.append(a / b if b else float("nan"))
    return {
        "n_with": len(w_all),
        "n_without": len(o_all),
        "median_with": mw,
        "median_without": mo,
        "diff": mw - mo,
        "diff_ci95": _ci(diffs),
        "ratio": mw / mo if mo else None,
        "ratio_ci95": _ci(ratios),
    }


def summarise(runs: list[dict]) -> dict:
    out: dict = {
        "n": len(runs),
        "pass_strict": sum(r["success_strict"] for r in runs),
        "pass_lenient": sum(r["success_lenient"] for r in runs),
        "runs_with_permission_denials": sum(1 for r in runs if r["permission_denials"]),
        "runs_with_multiple_result_events": sum(1 for r in runs if r["n_result_events"] > 1),
        "runs_using_bash": sum(1 for r in runs if r["bash_calls"]),
        "rule_enablement": dict(
            sorted(
                {
                    k: sum(1 for r in runs if r["rule_enablement"] == k)
                    for k in {r["rule_enablement"] for r in runs}
                }.items()
            )
        ),
        "metrics": {m: describe([r[m] for r in runs if r[m] is not None]) for m in METRICS},
        "metrics_all_results": {
            m: describe([r["all_results"][m] for r in runs])
            for m in ("cost_usd", "total_tokens", "num_turns", "duration_ms")
        },
    }
    return out


def _vals(runs: list[dict], metric: str) -> list[float]:
    return [r[metric] for r in runs if r[metric] is not None]


def _svals(runs: list[dict], signal: str) -> list[float]:
    return [r["agent"][signal] for r in runs if r["agent"][signal] is not None]


def signal_summary(runs: list[dict]) -> dict:
    """Counts, rates and distributions of the agent.* signals for one group."""
    a = [r["agent"] for r in runs]
    n = len(runs)

    def total(k: str) -> int:
        return sum(x[k] for x in a)

    def runs_with(k: str) -> int:
        return sum(1 for x in a if x[k])

    calls = total("agent.tool.calls")
    failures = total("agent.tool.failures")
    by_tool: dict[str, dict[str, int]] = {}
    for r in runs:
        for name, c in r["tool_stats"].items():
            agg = by_tool.setdefault(name, dict.fromkeys(c, 0))
            for k, v in c.items():
                agg[k] += v
    return {
        "n": n,
        "runs_with": {
            k: runs_with(k)
            for k in (
                "agent.secret.exposed",
                "agent.secret.known_exposed",
                "agent.permission.denials",
                "agent.tool.env_enumeration_calls",
                "agent.fs.outside_workdir_calls",
                "agent.tool.shell_http_calls",
                "agent.tool.failures",
                "agent.tool.hidden_failures",
                "agent.tool.failures_unrecovered",
                "agent.tool.first_attempt_failures",
                "agent.tool.duplicate_calls",
                "agent.harness.backgrounded_calls",
                "agent.harness.timed_out_calls",
                "agent.harness.background_tasks_ended_abnormally",
                "agent.tool.wait_calls",
            )
        }
        | {
            "agent.harness.result_events>1": sum(
                1 for x in a if x["agent.harness.result_events"] > 1
            )
        },
        "totals": {
            k: total(k)
            for k in (
                "agent.model.calls",
                "agent.tool.calls",
                "agent.tool.calls_mcp",
                "agent.tool.calls_shell",
                "agent.tool.calls_file",
                "agent.tool.errors",
                "agent.tool.failures",
                "agent.tool.hidden_failures",
                *(f"agent.tool.failures_{c}" for c in ERROR_CLASSES),
                "agent.tool.first_attempt_failures",
                "agent.tool.retries",
                "agent.tool.failures_recovered",
                "agent.tool.failures_unrecovered",
                "agent.tool.duplicate_calls",
                "agent.tool.shell_http_calls",
                "agent.tool.env_enumeration_calls",
                "agent.permission.denials",
                "agent.secret.exposures",
                *(f"agent.secret.kind_{k}" for k in SECRET_KINDS),
                *(f"agent.secret.location_{k}" for k in SECRET_LOCATIONS),
                "agent.harness.backgrounded_calls",
                "agent.harness.timed_out_calls",
                "agent.tool.wait_calls",
            )
        },
        "rates": {
            "tool_error_rate": total("agent.tool.errors") / calls if calls else None,
            "tool_failure_rate": failures / calls if calls else None,
            "hidden_share_of_failures": total("agent.tool.hidden_failures") / failures
            if failures
            else None,
            "recovered_share_of_failures": total("agent.tool.failures_recovered") / failures
            if failures
            else None,
            "secret_exposure_run_rate": runs_with("agent.secret.exposed") / n if n else None,
        },
        "distributions": {k: describe(_svals(runs, k)) for k in SIGNAL_DESCRIBE},
        "by_tool": dict(sorted(by_tool.items())),
    }


def signal_comparisons(rng: random.Random, strata: list[tuple[list[dict], list[dict]]]) -> dict:
    out: dict = {}
    for m in SIGNAL_COMPARE:
        wg = [_svals(w, m) for w, _ in strata]
        og = [_svals(o, m) for _, o in strata]
        out[m] = compare(rng, wg, og)
    return out


def comparisons(rng: random.Random, strata: list[tuple[list[dict], list[dict]]]) -> dict:
    out: dict = {}
    for subset in ("all_runs", "passing_runs"):
        out[subset] = {}
        for m in METRICS:
            wg, og = [], []
            for w, o in strata:
                if subset == "passing_runs":
                    w = [r for r in w if r["success_strict"]]
                    o = [r for r in o if r["success_strict"]]
                wg.append(_vals(w, m))
                og.append(_vals(o, m))
            out[subset][m] = compare(rng, wg, og)
    return out


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--no-known-secret",
        action="store_true",
        help="skip the exact-match scan for the runs' Kibana API key "
        "(agent.secret.kind_known_secret then reads 0 and meta says so)",
    )
    args = ap.parse_args()
    known = None if args.no_known_secret else load_known_secret()
    if known is None and not args.no_known_secret:
        raise SystemExit(
            "FAIL: the Kibana API key the runs used is not readable from "
            "elastic-start-local/.env.seed (KIBANA_TEST_API_KEY), so the known-secret "
            "exposure scan cannot run. Seed the stack (scripts/stack.sh up && "
            "scripts/stack.sh seed) or pass --no-known-secret."
        )
    root: Path = args.root
    out_path: Path = args.out or root / "analysis.json"

    run_dirs = sorted(
        p.parent
        for p in root.glob("b[0-9]*/*/record.json")
        if (p.parent / "transcript.jsonl").exists()
    )
    runs = [derive_run(d, known) for d in run_dirs]
    runs.sort(key=lambda r: (r["block"], r["model"], r["arm"], r["run_id"]))
    models = sorted({r["model"] for r in runs})

    def cell(model: str, arm: str) -> list[dict]:
        return [r for r in runs if r["model"] == model and r["arm"] == arm]

    rng = random.Random(SEED)
    srng = random.Random(SEED + 1)
    summary = {m: {a: summarise(cell(m, a)) for a in ARMS} for m in models}
    per_model = {m: comparisons(rng, [(cell(m, "with-mcp"), cell(m, "no-mcp"))]) for m in models}
    pooled = {
        "summary": {a: summarise([r for r in runs if r["arm"] == a]) for a in ARMS},
        "comparison": comparisons(rng, [(cell(m, "with-mcp"), cell(m, "no-mcp")) for m in models]),
    }

    analysis = {
        "meta": {
            "source": str(root.relative_to(HERE.parent.parent))
            if root.is_relative_to(HERE.parent.parent)
            else str(root),
            "excluded": ["pilot/"],
            "n_runs": len(runs),
            "primary_measure": "first result event per transcript (model's own end of turn)",
            "sensitivity_measure": "all result events: turns/durations/usage tokens summed; "
            "cost = last total_cost_usd (cumulative in the CLI)",
            "cost_basis": "total_cost_usd as reported by the CLI (list price)",
            "tool_calls": "assistant tool_use blocks before the first result event",
            "success_strict": "record.json success",
            "success_lenient": "strict, or the only failure reason is 'rule disabled'",
            "comparison": "with-mcp minus no-mcp, and with-mcp / no-mcp, of medians; "
            "percentile bootstrap CI95, arms resampled independently; the pooled "
            "line resamples within each model x arm stratum",
            "bootstrap_seed": SEED,
            "bootstrap_resamples": RESAMPLES,
            "quartiles": "statistics.quantiles(method='inclusive')",
            "wall_s": "secondary and flagged: harness wall clock",
        },
        "runs": runs,
        "summary": summary,
        "comparison": per_model,
        "pooled": pooled,
        "signals": {
            "meta": {
                "definitions": "scripts/experiment/SIGNALS.md",
                "scope": "transcript events up to the first result event",
                "known_secret_scan": known is not None,
                "comparison": "as meta.comparison, on all runs; RNG seed bootstrap_seed + 1",
            },
            "summary": {m: {a: signal_summary(cell(m, a)) for a in ARMS} for m in models},
            "comparison": {
                m: signal_comparisons(srng, [(cell(m, "with-mcp"), cell(m, "no-mcp"))])
                for m in models
            },
            "pooled": {
                "summary": {a: signal_summary([r for r in runs if r["arm"] == a]) for a in ARMS},
                "comparison": signal_comparisons(
                    srng, [(cell(m, "with-mcp"), cell(m, "no-mcp")) for m in models]
                ),
            },
        },
    }
    out_path.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out_path} ({len(runs)} runs, models={models})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
