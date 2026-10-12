#!/usr/bin/env python3
"""Local-model study runner (study 5): drive LM Studio models through
mcp-for-kibana on the shared study tasks, and keep the evidence of every run.

    uv run python scripts/experiment/local_run.py preflight [--load]
    uv run python scripts/experiment/local_run.py install-sample
    uv run python scripts/experiment/local_run.py pilot
    uv run python scripts/experiment/local_run.py stage1
    uv run python scripts/experiment/local_run.py stage2
    uv run python scripts/experiment/local_run.py status

Every study value (models, stages, pass mark, runs per cell, Honeycomb
dataset) lives in ``local_study.json``; ``--config`` points at another file.
The tasks (prompts, seeded starting state, success checks) live in the
shared ``tasks.json`` that ``study_tasks.py`` reads, the same definition
study 4 uses. Stage 1 runs the stage-1 tasks for every model; stage 2 runs
the stage-2 tasks only for the models whose stage-1 successes reach the
pass mark, and refuses to start before stage 1 is complete. The runbook is
``LOCAL_STUDY.md``.

One run sends one task's prompt to LM Studio's ``/api/v1/chat`` with the
mcp.json server entry named in the config as the only integration. The
request carries no ``context_length`` and no sampling parameters: the model
runs as loaded, with its own defaults (docs/e2e-setup.md). The Kibana API key
lives in ``~/.lmstudio/mcp.json`` (user-owned, read here, never written) and
reaches only the MCP server; the prompt never names it.

Each run writes ``<out_dir>/<phase>/<block>/<run_id>/``: ``cell.json`` and
``before.json`` (the Kibana snapshot) at the start, then ``prompt.txt``,
``seed.json`` (seeded tasks: the restored seed), ``request.json``, ``events.jsonl`` (the SSE stream with receive times),
``response.json`` (LM Studio's final result), ``artifacts.json``,
``spans.json`` (the redacted OTLP payload) and, last, ``record.json``.
A directory without ``record.json`` is an interrupted run: the next start
resets Kibana to its ``before.json`` and runs the cell again. Kibana is reset
to the pre-run snapshot after every run; a seeded task restores the seeded
dashboard and rule before it starts.

Each run is sent live to Honeycomb as one OpenTelemetry GenAI trace
(``invoke_agent`` / ``chat`` / ``execute_tool``, the agent.* and study.*
names of SIGNALS.md, plus every check's result as ``study.check.*``). The payload is redacted (the Kibana key, the LM Studio
token, the Honeycomb key, generic credential patterns) and scanned; the send
is refused if any of them remains.

Stdlib plus the repo's own dependencies (fastmcp, for listing the server's
tools in preflight).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import efficiency_analyze as ea  # noqa: E402  (sibling script)
import efficiency_honeycomb as eh  # noqa: E402
import efficiency_run as er  # noqa: E402
import study_tasks as st  # noqa: E402

CONFIG = HERE / "local_study.json"
PLACEHOLDER = "REPLACE"
AGENT_NAME = "lmstudio server"
ARM = "server"


# ---------------------------------------------------------------- config


def load_config(path: Path = CONFIG) -> dict:
    cfg = json.loads(Path(path).read_text())
    cfg["_path"] = str(path)
    return cfg


def repo_path(p: str) -> Path:
    q = Path(os.path.expanduser(p))
    return q if q.is_absolute() else REPO / q


def config_problems(cfg: dict) -> list[str]:
    out = []
    models = cfg.get("models") or []
    if not models:
        out.append("no models listed")
    bad = [m for m in models if PLACEHOLDER in m]
    if bad:
        out.append(f"placeholder model entries: {bad}")
    if len(set(models)) != len(models):
        out.append("a model is listed twice")
    try:
        known = st.task_ids(tasks_cfg(cfg))
    except (OSError, ValueError) as e:
        return [*out, f"tasks file {cfg['tasks']['file']} unreadable: {e}"]
    for stage in ("stage1", "stage2"):
        bad = [t for t in cfg["tasks"][stage] if t not in known]
        if bad:
            out.append(f"{stage} names unknown tasks {bad}; tasks.json has {known}")
    if not 1 <= cfg["tasks"]["pass_mark"] <= cfg["runs"]["per_cell"]:
        out.append(
            f"pass_mark {cfg['tasks']['pass_mark']} is outside 1..{cfg['runs']['per_cell']} (runs per cell)"
        )
    return out


def tasks_cfg(cfg: dict) -> dict:
    return st.load(repo_path(cfg["tasks"]["file"]))


def samples(cfg: dict) -> list[str]:
    """The sample data sets the tasks use."""
    return sorted({d["sample"] for d in tasks_cfg(cfg)["data"].values()})


def phase_tasks(cfg: dict, phase: str) -> list[str]:
    return list(cfg["tasks"]["stage2" if phase == "stage2" else "stage1"])


def env_value(name: str) -> str | None:
    """The shell environment first, then .env.local (user-owned, read only)."""
    if os.environ.get(name):
        return os.environ[name]
    local = REPO / ".env.local"
    try:
        for line in local.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k == name and v:
                return v
    except OSError:
        pass
    return None


def load_seed(cfg: dict) -> tuple[str, str]:
    values = {}
    for line in repo_path(cfg["kibana"]["seed_file"]).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values["KIBANA_URL"], values["KIBANA_TEST_API_KEY"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- HTTP


def http(method: str, url: str, body=None, headers=None, timeout: float = 60):
    """(status, parsed JSON or text). Status 0 means no HTTP answer."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, repr(e)
    try:
        return status, json.loads(raw) if raw.strip() else None
    except ValueError:
        return status, raw.decode(errors="replace")


class LMStudio:
    def __init__(self, url: str, token: str | None):
        self.url = url.rstrip("/")
        self.token = token

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def models(self, auth: bool = True) -> tuple[int, list[dict]]:
        """Normalised model list: key, loaded, max_context, loaded_context,
        tool_use, raw. Accepts the native /api/v1 shape and the OpenAI-style
        ``data`` list."""
        status, body = http(
            "GET", f"{self.url}/api/v1/models", headers=self.headers() if auth else {}
        )
        if status != 200 or not isinstance(body, dict):
            return status, []
        items = body.get("models") or body.get("data") or []
        out = []
        for m in items:
            inst = m.get("loaded_instances") or []
            ctx = None
            for i in inst:
                ctx = (i.get("config") or {}).get("context_length") or ctx
            caps = m.get("capabilities") or {}
            out.append(
                {
                    "key": m.get("key") or m.get("id"),
                    "type": m.get("type"),
                    "loaded": bool(inst) or m.get("state") == "loaded",
                    "max_context": m.get("max_context_length"),
                    "loaded_context": ctx or m.get("loaded_context_length"),
                    "tool_use": caps.get("trained_for_tool_use")
                    if isinstance(caps, dict)
                    else None,
                    "raw": m,
                }
            )
        return status, out

    def load(self, model: str, context_length: int | None) -> tuple[int, object]:
        """POST /api/v1/models/load; the context length is the model's maximum."""
        body = {"model": model}
        if context_length:
            body["context_length"] = context_length
        return http(
            "POST", f"{self.url}/api/v1/models/load", body, headers=self.headers(), timeout=600
        )

    def unload(self, instance_id: str) -> int:
        status, _ = http(
            "POST",
            f"{self.url}/api/v1/models/unload",
            {"instance_id": instance_id},
            headers=self.headers(),
            timeout=120,
        )
        return status

    def chat(self, payload: dict, stream: bool, run_timeout: float, idle_timeout: float) -> dict:
        """POST /api/v1/chat. Returns http_status, error_body, result (the
        final response object), events (stream events with receive time) and
        timing. Streaming: the FIRST ``chat.end`` event is the result; any
        later one is counted, not used."""
        url = f"{self.url}/api/v1/chat"
        body = {**payload, "stream": True} if stream else dict(payload)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", **self.headers()},
        )
        t0 = time.monotonic()
        out = {
            "http_status": 0,
            "error_body": None,
            "result": None,
            "events": [],
            "end_events": 0,
            "timed_out": False,
            "transport_error": None,
        }
        try:
            resp = urllib.request.urlopen(req, timeout=idle_timeout if stream else run_timeout)
        except urllib.error.HTTPError as e:
            out["http_status"] = e.code
            out["error_body"] = e.read().decode(errors="replace")
            out["wall_ms"] = (time.monotonic() - t0) * 1000
            return out
        except (TimeoutError, socket.timeout):
            out["timed_out"] = True
            out["wall_ms"] = (time.monotonic() - t0) * 1000
            return out
        except (urllib.error.URLError, OSError) as e:
            out["transport_error"] = repr(e)
            out["wall_ms"] = (time.monotonic() - t0) * 1000
            return out
        out["http_status"] = resp.status
        try:
            with resp:
                if not stream:
                    out["result"] = json.loads(resp.read())
                else:
                    for etype, data in _sse(resp, t0, run_timeout):
                        t = (time.monotonic() - t0) * 1000
                        out["events"].append({"t_ms": round(t, 1), "type": etype, "data": data})
                        if etype == "chat.end":
                            out["end_events"] += 1
                            if out["result"] is None and isinstance(data, dict):
                                out["result"] = data.get("result", data)
        except (TimeoutError, socket.timeout):
            out["timed_out"] = True
        except _Deadline:
            out["timed_out"] = True
        except (OSError, ValueError) as e:
            out["transport_error"] = repr(e)
        out["wall_ms"] = (time.monotonic() - t0) * 1000
        return out


class _Deadline(Exception):
    pass


def _sse(resp, t0: float, run_timeout: float):
    """Yield (event type, data) from a server-sent-event stream. The type is
    the ``event:`` field, else the data's own ``type``."""
    etype, data_lines = None, []
    for raw in resp:
        if time.monotonic() - t0 > run_timeout:
            raise _Deadline
        line = raw.decode(errors="replace").rstrip("\r\n")
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            etype = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line == "" and data_lines:
            text = "\n".join(data_lines)
            try:
                data = json.loads(text)
            except ValueError:
                data = {"raw": text}
            yield (
                (etype or (data.get("type") if isinstance(data, dict) else None) or "message"),
                data,
            )
            etype, data_lines = None, []
    if data_lines:
        text = "\n".join(data_lines)
        try:
            data = json.loads(text)
        except ValueError:
            data = {"raw": text}
        yield (etype or (data.get("type") if isinstance(data, dict) else None) or "message"), data


# ---------------------------------------------------------------- parsing

_REFUSED = re.compile(r"(?i)permission denied|not allowed|has been denied|permission to use")
_UNKNOWN = re.compile(r"(?i)unknown tool|tool .{0,40}not found|no such tool")
_INVALID_TOOL_TYPES = ("invalid_tool_call",)


def _tool_text(output) -> str:
    """The text of an MCP tool output as LM Studio returns it: a JSON string of
    content parts, a list of parts, or plain text."""
    if output is None:
        return ""
    val = output
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except ValueError:
            return val
    if isinstance(val, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in val)
    if isinstance(val, dict):
        if "text" in val and isinstance(val["text"], str):
            return val["text"]
        return json.dumps(val)
    return str(val)


def classify_tool(text: str) -> tuple[bool, str | None]:
    """(ok, error class). The server answers every successful call with JSON;
    a failed call returns its error as plain text (the guidance a model
    recovers from). So: JSON is success, anything else is a failure, classed
    with the efficiency study's rules."""
    stripped = text.strip()
    if stripped:
        try:
            json.loads(stripped)
            return True, None
        except ValueError:
            pass
    if not stripped:
        return False, "other"
    if _REFUSED.search(stripped):
        return False, "refused"
    if _UNKNOWN.search(stripped):
        return False, "unknown_tool"
    status, detail = ea.http_error(stripped)
    if status:
        return False, ea._status_class(status, detail)
    if ea._VALIDATION.search(stripped) or "not found" in stripped:
        return False, "validation"
    return False, "other"


def _event_tool_name(d: dict) -> str | None:
    if not isinstance(d, dict):
        return None
    return d.get("tool") or d.get("name") or (d.get("metadata") or {}).get("tool_name")


def stream_tool_timings(events: list[dict]) -> list[dict]:
    """Tool intervals from the stream: tool_call.start -> success/failure."""
    out, open_ = [], []
    for e in events:
        t, d = e["type"], e["data"]
        if t == "tool_call.start":
            open_.append({"name": _event_tool_name(d), "start_ms": e["t_ms"]})
        elif t in ("tool_call.success", "tool_call.failure"):
            name = _event_tool_name(d)
            idx = next((i for i, o in enumerate(open_) if o["name"] in (name, None)), None)
            o = open_.pop(idx) if idx is not None else {"name": name, "start_ms": e["t_ms"]}
            out.append(
                {
                    "name": o["name"] or name,
                    "start_ms": o["start_ms"],
                    "end_ms": e["t_ms"],
                    "outcome": t.split(".")[-1],
                    "data": d,
                }
            )
    return out


def output_from_stream(events: list[dict]) -> list[dict]:
    """Rebuild the output items from the stream when no final event arrived
    (a timed-out run): executed calls, rejected calls and the text so far."""
    items: list[dict] = []
    buf = {"reasoning": "", "message": ""}

    def flush(kind):
        if buf[kind]:
            items.append({"type": kind, "content": buf[kind]})
            buf[kind] = ""

    for e in events:
        t, d = e["type"], e["data"] if isinstance(e["data"], dict) else {}
        if t == "reasoning.delta":
            flush("message")
            buf["reasoning"] += d.get("content", "")
        elif t == "message.delta":
            flush("reasoning")
            buf["message"] += d.get("content", "")
        elif t == "tool_call.success":
            flush("reasoning")
            flush("message")
            items.append(
                {
                    "type": "tool_call",
                    "tool": _event_tool_name(d),
                    "arguments": d.get("arguments"),
                    "output": d.get("output"),
                }
            )
        elif t == "tool_call.failure":
            flush("reasoning")
            flush("message")
            items.append(
                {
                    "type": "invalid_tool_call",
                    "reason": d.get("reason"),
                    "metadata": {"tool_name": _event_tool_name(d), "arguments": d.get("arguments")},
                }
            )
    flush("reasoning")
    flush("message")
    return items


_DELTAS = ("reasoning.delta", "message.delta", "prompt_processing.progress", "model_load.progress")


def compact_events(events: list[dict]) -> list[dict]:
    """Merge each run of consecutive same-type delta/progress events into one
    entry (content concatenated, first and last receive time, count), so a
    long streamed run stays a small file. Every other event is kept as is;
    parse_run reads the compacted list exactly as the raw one."""
    out: list[dict] = []
    for e in events:
        prev = out[-1] if out else None
        if e["type"] in _DELTAS and prev is not None and prev["type"] == e["type"]:
            d = e["data"] if isinstance(e["data"], dict) else {}
            pd = prev["data"]
            if "content" in d:
                pd["content"] = pd.get("content", "") + d["content"]
            else:
                pd.update(d)
            prev["t_end_ms"] = e["t_ms"]
            prev["n"] = prev.get("n", 1) + 1
            continue
        out.append({**e, "data": dict(e["data"]) if isinstance(e["data"], dict) else e["data"]})
    return out


def parse_run(chat: dict, known: list[str]) -> dict:
    """Metrics of one run from LM Studio's final result and stream events."""
    result = chat.get("result") or {}
    events = chat.get("events") or []
    output = result.get("output")
    rebuilt = False
    if output is None:
        output = output_from_stream(events)
        rebuilt = bool(events)
    timings = stream_tool_timings(events)
    pool = list(timings)
    tools = []
    for i, item in enumerate(output):
        typ = item.get("type")
        if typ not in ("tool_call", *_INVALID_TOOL_TYPES):
            continue
        invalid = typ != "tool_call"
        name = item.get("tool") if not invalid else (item.get("metadata") or {}).get("tool_name")
        args = (
            item.get("arguments") if not invalid else (item.get("metadata") or {}).get("arguments")
        )
        text = _tool_text(item.get("output")) if not invalid else str(item.get("reason") or "")
        if invalid:
            ok, cls = False, "unknown_tool" if _UNKNOWN.search(text) else "validation"
        else:
            ok, cls = classify_tool(text)
        j = next((k for k, t in enumerate(pool) if t["name"] == name), None)
        timing = pool.pop(j) if j is not None else None
        tools.append(
            {
                "index": len(tools),
                "item_index": i,
                "name": name or "?",
                "arguments": args,
                "result": text,
                "invalid": invalid,
                "ok": ok,
                "error_class": cls,
                "start_ms": timing["start_ms"] if timing else None,
                "end_ms": timing["end_ms"] if timing else None,
                "secrets": {
                    "tool_arguments": ea.scan_secrets(
                        json.dumps(args, ensure_ascii=False), _k(known)
                    ),
                    "tool_result": ea.scan_secrets(text, _k(known)),
                },
            }
        )
    messages = [_as_str(i.get("content")) for i in output if i.get("type") == "message"]
    reasoning = [_as_str(i.get("content")) for i in output if i.get("type") == "reasoning"]
    text = "\n".join(messages)
    stats = result.get("stats") or {}
    executed = [t for t in tools if not t["invalid"]]
    failed = [t for t in tools if not t["ok"]]
    tool_ms = sum(
        (t["end_ms"] - t["start_ms"])
        for t in tools
        if t["start_ms"] is not None and t["end_ms"] is not None
    )
    first_tool = min((t["start_ms"] for t in tools if t["start_ms"] is not None), default=None)
    classes: dict[str, int] = {}
    for t in failed:
        classes[t["error_class"]] = classes.get(t["error_class"], 0) + 1
    model_text_secrets = ea.scan_secrets(text + "\n".join(reasoning), _k(known))
    for extra in known[1:]:  # the LM Studio token and Honeycomb key count as known too
        for t in tools:
            for loc, blob in (
                ("tool_arguments", json.dumps(t["arguments"])),
                ("tool_result", t["result"]),
            ):
                if extra and extra in blob:
                    t["secrets"][loc]["known_secret"] = t["secrets"][loc].get("known_secret", 0) + 1
        if extra and extra in text:
            model_text_secrets["known_secret"] = model_text_secrets.get("known_secret", 0) + 1
    denial_events = sum(
        1
        for e in events
        if e["type"] == "tool_call.failure" and _REFUSED.search(json.dumps(e["data"]))
    )
    return {
        "output": output,
        "output_rebuilt_from_stream": rebuilt,
        "tools": tools,
        "tool_names": [t["name"] for t in tools],
        "final_text": messages[-1] if messages else "",
        "message_text": text,
        "model_calls": model_calls(output),
        "n_tool_calls": len(executed),
        "n_invalid_tool_calls": len(tools) - len(executed),
        "n_failed_tool_calls": len(failed),
        "n_distinct_tools": len({t["name"] for t in tools}),
        "failure_classes": classes,
        "tool_result_bytes": sum(len(t["result"].encode()) for t in executed),
        "tool_ms": round(tool_ms, 1),
        "first_tool_ms": first_tool,
        "input_tokens": stats.get("input_tokens"),
        "output_tokens": stats.get("total_output_tokens"),
        "reasoning_tokens": stats.get("reasoning_output_tokens"),
        "tokens_per_second": stats.get("tokens_per_second"),
        "ttft_s": stats.get("time_to_first_token_seconds"),
        "model_load_s": stats.get("model_load_time_seconds"),
        "stats": stats,
        "response_model": result.get("model_instance_id") or result.get("model"),
        "response_id": result.get("response_id"),
        "end_events": chat.get("end_events", 0),
        "stream_events": len(events),
        "permission_denials": denial_events + classes.get("refused", 0),
        "secrets_model_text": model_text_secrets,
    }


def _k(known: list[str]) -> str | None:
    return known[0] if known else None


def _as_str(x) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in x)
    return "" if x is None else str(x)


def _is_tool(item: dict) -> bool:
    return item.get("type") in ("tool_call", *_INVALID_TOOL_TYPES)


def model_calls(output: list[dict]) -> int:
    """Model generations, inferred: one at the start and one after every
    batch of tool calls that the model continued from."""
    calls = 1 if output else 0
    for i, item in enumerate(output):
        if _is_tool(item) and i + 1 < len(output) and not _is_tool(output[i + 1]):
            calls += 1
    return calls


# ---------------------------------------------------------------- scoring


# ---------------------------------------------------------------- evidence


def scrub(obj, known: list[str]):
    """Replace every known secret in every string (files on disk)."""
    if isinstance(obj, str):
        for s in known:
            if s:
                obj = obj.replace(s, "[REDACTED_KNOWN_SECRET]")
        return obj
    if isinstance(obj, list):
        return [scrub(v, known) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub(v, known) for k, v in obj.items()}
    return obj


def write_json(path: Path, obj, known: list[str]) -> None:
    path.write_text(json.dumps(scrub(obj, known), indent=1, ensure_ascii=False) + "\n")


class MultiRedactor(eh.Redactor):
    """efficiency_honeycomb's redactor, with every known secret replaced first."""

    def __init__(self, known: list[str]):
        super().__init__(known[0] if known else None)
        self.extra = [k for k in known[1:] if k]

    def __call__(self, text: str) -> str:
        for k in self.extra:
            text = text.replace(k, "[REDACTED_KNOWN_SECRET]")
        return super().__call__(text)


def _id(*parts: str, n: int) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[: n * 2]


def _secret_attrs(found: dict[str, dict[str, int]]) -> dict:
    return eh._secret_attrs(found)


def build_spans(record: dict, parsed: dict, redact, caps: tuple[int, int]) -> list[dict]:
    """One trace: invoke_agent (run totals), chat (one per inferred model
    generation), execute_tool (one per tool call, LM Studio-rejected calls
    included and marked failed)."""
    msg_cap, tool_cap = caps
    rid = record["run_id"]
    trace_id, root_id = _id(rid, n=16), _id(rid, "root", n=8)
    start = int(datetime.fromisoformat(record["ts_start"]).timestamp() * 1e9)
    wall_ns = int((record.get("wall_ms") or 0) * 1e6)
    end = start + wall_ns
    common = {
        "gen_ai.conversation.id": rid,
        "gen_ai.agent.name": AGENT_NAME,
        "gen_ai.provider.name": "lmstudio",
        "gen_ai.request.model": record["model"],
        "study.model": record["model"],
        "study.arm": ARM,
        "study.phase": record["phase"],
        "study.block": record["block"],
        "study.task": record["task"],
        "study.steps_total": record["steps_total"],
        "study.run_id": rid,
    }
    tools = parsed["tools"]
    run_secret_locs: dict[str, dict[str, int]] = {"model_text": parsed["secrets_model_text"]}
    for t in tools:
        for loc, f in t["secrets"].items():
            agg = run_secret_locs.setdefault(loc, {})
            for k, v in f.items():
                agg[k] = agg.get(k, 0) + v
    ok = bool(record.get("success"))
    n_calls = len(tools)
    root = {
        **common,
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.id": "lmstudio/" + ARM,
        "gen_ai.response.model": parsed["response_model"],
        "gen_ai.response.id": parsed["response_id"],
        # LM Studio reports usage per run only: both token sides sit here.
        "gen_ai.usage.input_tokens": parsed["input_tokens"],
        "gen_ai.usage.output_tokens": parsed["output_tokens"],
        "gen_ai.usage.reasoning.output_tokens": parsed["reasoning_tokens"],
        "gen_ai.response.time_to_first_chunk": parsed["ttft_s"],
        "agent.task.success": ok,
        "agent.run.end_reason": record.get("end_reason"),
        "agent.model.calls": parsed["model_calls"],
        "agent.tool.calls": n_calls,
        "agent.tool.calls_mcp": parsed["n_tool_calls"],
        "agent.tool.invalid_calls": parsed["n_invalid_tool_calls"],
        "agent.tool.distinct_tools": parsed["n_distinct_tools"],
        "agent.tool.failures": parsed["n_failed_tool_calls"],
        "agent.tool.failure_rate": (parsed["n_failed_tool_calls"] / n_calls) if n_calls else 0.0,
        **{f"agent.tool.failures_{k}": v for k, v in sorted(parsed["failure_classes"].items())},
        "agent.usage.input_tokens": parsed["input_tokens"],
        "agent.usage.output_tokens": parsed["output_tokens"],
        "agent.usage.reasoning_tokens": parsed["reasoning_tokens"],
        "agent.usage.tool_result_bytes": parsed["tool_result_bytes"],
        "agent.time.wall_ms": record.get("wall_ms"),
        "agent.time.ttft_ms": parsed["ttft_s"] * 1000 if parsed["ttft_s"] is not None else None,
        "agent.time.tool_ms": parsed["tool_ms"],
        "agent.time.model_ms": (record.get("wall_ms") or 0) - parsed["tool_ms"],
        "agent.time.first_tool_ms": parsed["first_tool_ms"],
        "agent.model.tokens_per_second": parsed["tokens_per_second"],
        "agent.model.load_ms": parsed["model_load_s"] * 1000
        if parsed["model_load_s"] is not None
        else None,
        "agent.permission.denials": parsed["permission_denials"],
        "agent.harness.result_events": parsed["end_events"],
        "agent.harness.timed_out": bool(record.get("timed_out")),
        "agent.secret.exposures": sum(1 for f in run_secret_locs.values() if any(f.values())),
        "agent.secret.known_exposed": any(f.get("known_secret") for f in run_secret_locs.values()),
        "study.success_strict": ok,
        "study.steps_passed": record.get("steps_passed"),
        "study.first_failed_step": record.get("first_failed_step"),
        "study.failure_reason": record.get("failure_reason"),
        "study.valid": record.get("valid"),
        "study.invalid_reason": record.get("invalid_reason"),
        "study.kibana_version": record.get("kibana_version"),
        "study.toolboxes": record.get("toolboxes"),
        "study.server_entry": record.get("server_entry"),
        "study.context_length": record.get("model_loaded_context"),
        "study.runner_commit": record.get("git_sha"),
        "study.attempt": record.get("attempt"),
        **(st.span_attributes(record) if record.get("criteria") else {}),
    }
    if not ok:
        root["error.type"] = "task_failed" if record.get("valid") else "invalid_run"
    spans = [
        {
            "traceId": trace_id,
            "spanId": root_id,
            "name": f"invoke_agent {AGENT_NAME}",
            "kind": 1,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(end),
            "attributes": eh.attrs(root),
            "status": {"code": 1 if ok else 2},
        }
    ]

    def ns(ms):
        return start + int(ms * 1e6) if ms is not None else None

    # chat spans: split the output at every tool batch the model continued from
    output = parsed["output"]
    segments, cur = [], []
    for i, item in enumerate(output):
        cur.append(item)
        if _is_tool(item) and (i + 1 == len(output) or not _is_tool(output[i + 1])):
            segments.append(cur)
            cur = []
    if cur:
        segments.append(cur)
    by_item = {t["item_index"]: t for t in tools}
    seg_start_ms = 0.0
    item_pos = 0
    for n, seg in enumerate(segments):
        idxs = list(range(item_pos, item_pos + len(seg)))
        item_pos += len(seg)
        seg_tools = [by_item[i] for i in idxs if i in by_item]
        first_start = next((t["start_ms"] for t in seg_tools if t["start_ms"] is not None), None)
        last_end = max((t["end_ms"] for t in seg_tools if t["end_ms"] is not None), default=None)
        s_ns = ns(seg_start_ms) if seg_start_ms is not None else start
        e_ns = ns(first_start) if first_start is not None else end
        parts = []
        for it in seg:
            if it.get("type") in ("message", "reasoning"):
                parts.append({"type": it["type"], "content": _as_str(it.get("content"))})
            elif _is_tool(it):
                parts.append(
                    {
                        "type": "tool_call",
                        "name": it.get("tool") or (it.get("metadata") or {}).get("tool_name"),
                        "arguments": it.get("arguments")
                        or (it.get("metadata") or {}).get("arguments"),
                    }
                )
        finish = "tool_call" if seg_tools else "stop"
        text = "\n".join(p.get("content", "") for p in parts if p["type"] != "tool_call")
        d = {
            **common,
            "gen_ai.operation.name": "chat",
            "gen_ai.response.model": parsed["response_model"],
            "gen_ai.response.finish_reasons": [finish],
            "gen_ai.output.messages": eh.fit(
                [{"role": "assistant", "parts": parts, "finish_reason": finish}], redact, msg_cap
            ),
            "agent.model.tool_calls": len(seg_tools),
            "agent.model.thinking": any(p["type"] == "reasoning" for p in parts),
            "agent.model.finish_reason_inferred": True,
            **_secret_attrs({"model_text": ea.scan_secrets(text, getattr(redact, "known", None))}),
        }
        if n == 0:
            d["gen_ai.input.messages"] = eh.fit(
                [{"role": "user", "parts": [{"type": "text", "content": record["prompt"]}]}],
                redact,
                msg_cap,
            )
            if parsed["ttft_s"] is not None:
                d["gen_ai.response.time_to_first_chunk"] = parsed["ttft_s"]
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _id(rid, "chat", str(n), n=8),
                "parentSpanId": root_id,
                "name": f"chat {record['model']}",
                "kind": 3,
                "startTimeUnixNano": str(s_ns),
                "endTimeUnixNano": str(max(e_ns, s_ns)),
                "attributes": eh.attrs(d),
                "status": {"code": 0},
            }
        )
        seg_start_ms = last_end if last_end is not None else None

    seen: dict[str, str] = {}
    for t in tools:
        canon = json.dumps([t["name"], t["arguments"]], sort_keys=True)
        dup = seen.get(canon)
        seen.setdefault(canon, str(t["index"]))
        d = {
            **common,
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": t["name"],
            "gen_ai.tool.call.id": f"{rid}-{t['index']}",
            "gen_ai.tool.type": "extension",
            "gen_ai.tool.call.arguments": eh.clip(
                json.dumps(t["arguments"], ensure_ascii=False), redact, tool_cap
            ),
            "gen_ai.tool.call.result": eh.clip(t["result"], redact, tool_cap),
            "agent.tool.failed": not t["ok"],
            "agent.tool.error_class": t["error_class"],
            "agent.tool.error_source": "harness"
            if t["invalid"]
            else ("output" if not t["ok"] else None),
            "agent.tool.rejected_by_runtime": t["invalid"],
            "agent.tool.result_bytes": len(t["result"].encode()),
            "agent.tool.duration_ms": (t["end_ms"] - t["start_ms"])
            if t["start_ms"] is not None and t["end_ms"] is not None
            else None,
            "agent.tool.duplicate_of": dup,
            "agent.tool.permission_denied": t["error_class"] == "refused",
            **_secret_attrs(t["secrets"]),
        }
        if not t["ok"]:
            d["error.type"] = t["error_class"]
        s = ns(t["start_ms"]) or start
        e = ns(t["end_ms"]) or s
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _id(rid, "tool", str(t["index"]), n=8),
                "parentSpanId": root_id,
                "name": f"execute_tool {t['name']}",
                "kind": 1,
                "startTimeUnixNano": str(s),
                "endTimeUnixNano": str(max(e, s)),
                "attributes": eh.attrs(d),
                "status": {"code": 0 if t["ok"] else 2},
            }
        )
    return spans


def otlp_body(spans: list[dict], dataset: str) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [eh.attr("service.name", dataset)]},
                "scopeSpans": [{"scope": {"name": "local_run"}, "spans": spans}],
            }
        ]
    }


def payload_problems(blob: str, known: list[str]) -> list[str]:
    """Why the payload must not be sent: a known secret, or any credential
    pattern, left after redaction. Names the kind, never the value."""
    out = [f"known secret #{i + 1} present" for i, s in enumerate(known) if s and s in blob]
    left = eh.leftover_secrets(blob, None)
    out += [f"credential pattern {k} x{n}" for k, n in left.items()]
    return out


def send_spans(cfg: dict, spans: list[dict], known: list[str]) -> str:
    return send_body(cfg, otlp_body(spans, cfg["honeycomb"]["dataset"]), known)


def send_body(cfg: dict, body: dict, known: list[str]) -> str:
    hc = cfg["honeycomb"]
    n_spans = sum(len(ss["spans"]) for rs in body["resourceSpans"] for ss in rs["scopeSpans"])
    blob = json.dumps(body, ensure_ascii=False)
    problems = payload_problems(blob, known)
    if problems:
        return "refused: " + "; ".join(problems)
    key = env_value(hc["key_env"])
    if not key:
        return f"skipped: {hc['key_env']} is not set"
    status, resp = http(
        "POST",
        hc["endpoint"],
        body,
        headers={"x-honeycomb-team": key, "x-honeycomb-dataset": hc["dataset"]},
        timeout=60,
    )
    if 200 <= status < 300:
        return f"sent: HTTP {status}, {n_spans} spans"
    return f"failed: HTTP {status} {str(resp)[:200]}"


# ---------------------------------------------------------------- one run

_ENV_FAIL = re.compile(
    r"(?i)insufficient system resources|failed to load model|out of memory|permission denied to use plugin|"
    r"model .{0,60}not found|no models? loaded"
)


def classify_validity(chat: dict) -> tuple[bool, str | None, str]:
    """(valid, invalid reason, end reason). Invalid means the environment
    failed, so the model was never fairly exercised; such cells are retried."""
    if chat.get("transport_error"):
        return False, f"transport: {chat['transport_error'][:200]}", "error"
    st = chat.get("http_status")
    if st and st >= 400:
        body = chat.get("error_body") or ""
        if "tool_format_generation_error" in body:
            return True, None, "model_error"  # the model's own malformed tool call
        # 401/403 (token, mcp.json permission), load and memory failures and
        # anything else the runtime refused: the environment, not the model.
        return False, f"HTTP {st}: {body[:300]}", "error"
    if chat.get("timed_out"):
        return True, None, "timeout"
    if chat.get("result") is None and not chat.get("events"):
        return False, "no response", "error"
    errs = [e for e in chat.get("events") or [] if e["type"] == "error"]
    if chat.get("result") is None and errs:
        body = json.dumps(errs[-1]["data"])
        if _ENV_FAIL.search(body):
            return False, f"stream error: {body[:300]}", "error"
        return True, None, "model_error"
    return True, None, "completed"


# runs.jsonl is a slim index (one line per run); record.json holds the rest
_INDEX_KEYS = (
    "run_id",
    "cell",
    "phase",
    "block",
    "model",
    "task",
    "attempt",
    "valid",
    "success",
    "steps_passed",
    "first_failed_step",
    "end_reason",
    "wall_ms",
    "input_tokens",
    "output_tokens",
    "n_tool_calls",
    "n_invalid_tool_calls",
    "otel",
)


class Ctx:
    """What every run needs, built once per invocation."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.out = repo_path(cfg["runs"]["out_dir"])
        self.kibana_url, self.kibana_key = load_seed(cfg)
        self.kb = er.Kibana(self.kibana_url, self.kibana_key)
        self.token = env_value(cfg["lmstudio"]["token_env"])
        self.lms = LMStudio(cfg["lmstudio"]["url"], self.token)
        hc_key = env_value(cfg["honeycomb"]["key_env"])
        # Order matters: the Kibana key first (it is the "known" secret of the
        # signal catalogue), then the other credentials the harness holds.
        self.known = [s for s in (self.kibana_key, self.token, hc_key) if s]
        self.git_sha = git_sha()
        self.tasks = tasks_cfg(cfg)
        # The checker's Elasticsearch (stack .env); None when the stack's
        # .env is unreadable, which preflight reports.
        try:
            self.es = st.Elastic.from_stack()
        except (OSError, SystemExit):
            self.es = None


def ensure_loaded(cfg: dict, lms: LMStudio, model: str) -> dict | None:
    """``lmstudio.load: harness`` (for an LM Studio with JIT loading off): load
    ``model`` at its maximum context unless it is resident, after unloading
    the other study models. Models the study does not use are never touched.
    Returns what was done, or None when nothing was (JIT mode, or resident).
    A failed load is not raised: the chat that follows fails and the run is
    recorded invalid, then retried."""
    if cfg["lmstudio"].get("load", "jit") != "harness":
        return None
    _, models = lms.models()
    info = next((m for m in models if m["key"] == model), None)
    if info and info["loaded"]:
        return None
    unloaded = []
    for m in models:
        if m["key"] in cfg["models"] and m["key"] != model and m["loaded"]:
            for inst in m["raw"].get("loaded_instances") or [{"id": m["key"]}]:
                lms.unload(inst.get("id") or m["key"])
                unloaded.append(inst.get("id") or m["key"])
    t0 = time.monotonic()
    status, body = lms.load(model, info["max_context"] if info else None)
    return {
        "status": status,
        "context_length": info["max_context"] if info else None,
        "load_s": round(time.monotonic() - t0, 1),
        "unloaded": unloaded,
        "error": None if status == 200 else str(body)[:300],
    }


def run_once(ctx: Ctx, cell: dict, attempt: int, harness_load: dict | None = None) -> dict:
    cfg = ctx.cfg
    task_id = cell["task"]
    task = st.task(ctx.tasks, task_id)
    model = cell["model"]
    marker = f"{cfg['kibana']['marker_prefix']}-{secrets.token_hex(4)}"
    prompt = st.build_prompt(ctx.tasks, task_id, cfg["tasks"]["arm"], marker)
    if any(s in prompt for s in ctx.known):
        raise SystemExit(
            "FAIL: a secret would reach the model through the prompt; check local_study.json"
        )
    run_id = (
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{slug(model)}-{task_id}-{secrets.token_hex(3)}"
    )
    run_dir = ctx.out / cell["phase"] / cell["block"] / run_id
    run_dir.mkdir(parents=True)
    seed_before = st.restore_seed(ctx.kb, ctx.tasks) if task["seeded"] else None
    if seed_before is not None:
        (run_dir / "seed.json").write_text(json.dumps(seed_before))
    before = ctx.kb.snapshot()
    (run_dir / "before.json").write_text(json.dumps(before))
    (run_dir / "cell.json").write_text(json.dumps({**cell, "attempt": attempt, "marker": marker}))
    (run_dir / "prompt.txt").write_text(prompt)
    entry = cfg["server"]["entry"]
    payload = {"model": model, "input": prompt, "integrations": [f"mcp/{entry}"]}
    (run_dir / "request.json").write_text(
        json.dumps(
            {**payload, "stream": cfg["lmstudio"]["stream"], "url": f"{ctx.lms.url}/api/v1/chat"},
            indent=1,
        )
    )
    record = {
        "study": cfg["study"],
        "run_id": run_id,
        "cell": cell["key"],
        "phase": cell["phase"],
        "block": cell["block"],
        "model": model,
        "task": task_id,
        "steps_total": len(task["steps"]),
        "arm": ARM,
        "attempt": attempt,
        "marker": marker,
        "prompt": prompt,
        "kibana_version": cfg["kibana"]["version"],
        "toolboxes": cfg["server"]["toolboxes"],
        "tier": cfg["server"]["tier"],
        "server_entry": entry,
        "stream": cfg["lmstudio"]["stream"],
        "git_sha": ctx.git_sha,
        "config_sha": hashlib.sha256(Path(cfg["_path"]).read_bytes()).hexdigest()[:12],
        "ts_start": now_iso(),
        "harness_load": harness_load,
    }
    chat = ctx.lms.chat(
        payload,
        stream=cfg["lmstudio"]["stream"],
        run_timeout=cfg["lmstudio"]["run_timeout_s"],
        idle_timeout=cfg["lmstudio"]["idle_timeout_s"],
    )
    record["wall_ms"] = round(chat["wall_ms"], 1)
    record["ts_end_model"] = now_iso()
    with (run_dir / "events.jsonl").open("w") as f:
        for e in compact_events(chat["events"]):
            f.write(json.dumps(scrub(e, ctx.known), ensure_ascii=False) + "\n")
    write_json(
        run_dir / "response.json",
        {
            "http_status": chat["http_status"],
            "error_body": chat["error_body"],
            "timed_out": chat["timed_out"],
            "transport_error": chat["transport_error"],
            "end_events": chat["end_events"],
            "result": chat["result"],
        },
        ctx.known,
    )
    valid, invalid_reason, end_reason = classify_validity(chat)
    record.update(
        {
            "http_status": chat["http_status"],
            "timed_out": chat["timed_out"],
            "valid": valid,
            "invalid_reason": scrub(invalid_reason, ctx.known),
            "end_reason": end_reason,
        }
    )
    parsed = parse_run(chat, ctx.known)
    record.update(
        {
            k: parsed[k]
            for k in (
                "tool_names",
                "model_calls",
                "n_tool_calls",
                "n_invalid_tool_calls",
                "n_failed_tool_calls",
                "n_distinct_tools",
                "failure_classes",
                "tool_result_bytes",
                "tool_ms",
                "first_tool_ms",
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "tokens_per_second",
                "ttft_s",
                "model_load_s",
                "stats",
                "response_model",
                "end_events",
                "stream_events",
                "permission_denials",
                "output_rebuilt_from_stream",
            )
        }
    )
    record["final_text"] = scrub(parsed["final_text"], ctx.known)
    record["stream_final_event"] = (
        "n/a" if not cfg["lmstudio"]["stream"] else ("ok" if chat["end_events"] else "missing")
    )
    known_hits = sum(
        f.get("known_secret", 0)
        for f in [
            parsed["secrets_model_text"],
            *[x for t in parsed["tools"] for x in t["secrets"].values()],
        ]
    )
    record["known_secret_exposures"] = known_hits
    try:
        state = st.collect_state(
            ctx.kb, ctx.es, ctx.tasks, task_id, marker, before, seed_before
        )
        outcome = st.score(ctx.tasks, task_id, marker, state, parsed["message_text"])
        artifacts = {k: state[k] for k in ("dashboards", "rules", "seeded", "filters")}
        if not valid:
            outcome["success"] = False
            outcome["failure_reason"] = f"invalid run: {record['invalid_reason']}"
        record.update(outcome)
        write_json(run_dir / "artifacts.json", artifacts, ctx.known)
    except Exception as e:  # a scoring fault is data, never a lost record
        record["harness_error"] = repr(e)
        record["success"] = False
        record["valid"] = False
        record["invalid_reason"] = f"scoring failed: {e!r}"
    try:
        record["reset_removed"] = ctx.kb.reset_to(before)
    except Exception as e:
        record["reset_error"] = repr(e)
    _, models = ctx.lms.models()
    info = next((m for m in models if m["key"] == model), None)
    record["model_loaded_context"] = info["loaded_context"] if info else None
    record["model_max_context"] = info["max_context"] if info else None
    record["ts_end"] = now_iso()
    redact = MultiRedactor(ctx.known)
    spans = build_spans(
        record,
        parsed,
        redact,
        (cfg["honeycomb"]["message_cap_bytes"], cfg["honeycomb"]["tool_cap_bytes"]),
    )
    (run_dir / "spans.json").write_text(
        json.dumps(otlp_body(spans, cfg["honeycomb"]["dataset"]), ensure_ascii=False)
    )
    record["n_spans"] = len(spans)
    record["otel"] = send_spans(cfg, spans, ctx.known)
    record["complete"] = True
    write_json(run_dir / "record.json", record, ctx.known)
    index = {k: record.get(k) for k in _INDEX_KEYS}
    with (ctx.out / "runs.jsonl").open("a") as f:
        f.write(json.dumps(scrub(index, ctx.known)) + "\n")
    return record


# ---------------------------------------------------------------- the matrix


def plan(cfg: dict, phase: str, models: list[str] | None = None) -> list[dict]:
    """Deterministic cell order. Block b rotates the model order by b and the
    task order by b, so every model and task takes every position. The pilot
    and stage 1 run the stage-1 tasks; stage 2 runs the stage-2 tasks for the
    `models` given (those that passed stage 1)."""
    models = list(cfg["models"] if models is None else models)
    tasks = phase_tasks(cfg, phase)
    if not models or not tasks:
        return []
    if phase == "pilot":
        reps, names = cfg["runs"]["pilot_per_cell"], ["pilot"]
    else:
        reps = cfg["runs"]["per_cell"]
        names = [f"b{b + 1:02d}" for b in range(reps)]
        reps = 1
    cells = []
    for b, block in enumerate(names):
        ms = (
            models[b % len(models) :] + models[: b % len(models)]
            if cfg["runs"]["order"] == "rotate"
            else models
        )
        ts = (
            tasks[b % len(tasks) :] + tasks[: b % len(tasks)]
            if cfg["runs"]["order"] == "rotate"
            else tasks
        )
        for m in ms:
            for t in ts:
                for r in range(reps):
                    rep = f"-r{r + 1}" if reps > 1 else ""
                    cells.append(
                        {
                            "phase": phase,
                            "block": block,
                            "model": m,
                            "task": t,
                            "key": f"{phase}/{block}/{slug(m)}/{t}{rep}",
                        }
                    )
    return cells


def load_records(out: Path, phase: str | None = None) -> list[dict]:
    root = out / phase if phase else out
    recs = []
    for p in sorted(root.glob("*/*/record.json") if phase else root.glob("*/*/*/record.json")):
        try:
            recs.append(json.loads(p.read_text()))
        except ValueError:
            continue
    return recs


def recover_interrupted(ctx: Ctx, phase: str) -> list[str]:
    """Runs that started but never wrote record.json: reset Kibana to their
    pre-run snapshot and mark them abandoned. Returns their directories."""
    done = []
    for cell_file in sorted((ctx.out / phase).glob("*/*/cell.json")):
        d = cell_file.parent
        if (d / "record.json").exists() or (d / "abandoned.json").exists():
            continue
        note = {"ts": now_iso(), "reason": "interrupted before record.json"}
        before = d / "before.json"
        if before.exists():
            try:
                note["reset_removed"] = ctx.kb.reset_to(json.loads(before.read_text()))
            except Exception as e:
                note["reset_error"] = repr(e)
        (d / "abandoned.json").write_text(json.dumps(note, indent=1))
        done.append(str(d))
    return done


@contextlib.contextmanager
def run_lock(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            os.kill(pid, 0)
            raise SystemExit(f"FAIL: another runner (pid {pid}) holds {lock}; stop it or wait")
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale lock from a crash
    lock.write_text(str(os.getpid()))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


def write_progress(ctx: Ctx, phase: str, cells: list[dict], recs: list[dict], state: dict) -> None:
    valid = [r for r in recs if r.get("valid")]
    done = sum(1 for c in cells if cell_done(c, recs, ctx.cfg))
    walls = [r["wall_ms"] / 1000 for r in recs if r.get("wall_ms")]
    mean = sum(walls) / len(walls) if walls else None
    prog = {
        "phase": phase,
        "updated": now_iso(),
        "cells_total": len(cells),
        "cells_done": done,
        "runs_recorded": len(recs),
        "runs_valid": len(valid),
        "runs_passed": sum(1 for r in valid if r.get("success")),
        "eta_hours": round((len(cells) - done) * mean / 3600, 2) if mean else None,
        **state,
    }
    (ctx.out / f"progress-{phase}.json").write_text(json.dumps(prog, indent=1) + "\n")


def cell_done(cell: dict, recs: list[dict], cfg: dict) -> bool:
    mine = [r for r in recs if r.get("cell") == cell["key"]]
    return any(r.get("valid") for r in mine) or len(mine) >= cfg["runs"]["max_attempts"]


def cell_success(cell: dict, recs: list[dict]) -> bool:
    """A done cell succeeds when its valid run succeeded; a cell that used
    up its attempts without a valid run counts as a failure."""
    return any(r.get("valid") and r.get("success") for r in recs if r.get("cell") == cell["key"])


def stage1_verdict(cfg: dict, recs: list[dict]) -> dict:
    """Per model: stage-1 successes, cells done, cells planned, and whether
    the model passed (successes on every stage-1 task reach the pass mark;
    decided only when all its cells are done)."""
    mark = cfg["tasks"]["pass_mark"]
    out = {}
    for m in cfg["models"]:
        cells = [c for c in plan(cfg, "stage1") if c["model"] == m]
        done = [c for c in cells if cell_done(c, recs, cfg)]
        per_task = {
            t: sum(1 for c in done if c["task"] == t and cell_success(c, recs))
            for t in phase_tasks(cfg, "stage1")
        }
        complete = len(done) == len(cells)
        out[m] = {
            "successes": per_task,
            "done": len(done),
            "cells": len(cells),
            "complete": complete,
            "passed": complete and all(n >= mark for n in per_task.values()),
        }
    return out


def run_matrix(cfg: dict, phase: str, limit: int | None = None) -> int:
    problems = config_problems(cfg)
    if problems:
        raise SystemExit("FAIL: local_study.json: " + "; ".join(problems))
    if cfg["honeycomb"]["required"] and not env_value(cfg["honeycomb"]["key_env"]):
        raise SystemExit(
            f"FAIL: {cfg['honeycomb']['key_env']} is not set and honeycomb.required is true; "
            "export the key (it is never written to disk) or set required to false in local_study.json"
        )
    ctx = Ctx(cfg)
    if not sample_installed(ctx.kb, cfg):
        raise SystemExit(
            f"FAIL: the sample data the tasks use ({', '.join(samples(cfg))}) is not installed; "
            "run `uv run python scripts/experiment/local_run.py install-sample`"
        )
    models = None
    if phase == "stage2":
        verdict = stage1_verdict(cfg, load_records(ctx.out, "stage1"))
        open_ = [m for m, v in verdict.items() if not v["complete"]]
        if open_:
            raise SystemExit(
                f"FAIL: stage 1 is not complete for {open_}; finish it first: "
                "`uv run python scripts/experiment/local_run.py stage1`"
            )
        models = [m for m, v in verdict.items() if v["passed"]]
        print(f"stage 2 models (passed stage 1): {models or 'none'}", flush=True)
        if not models:
            return 0
    cells = plan(cfg, phase, models)
    with run_lock(ctx.out):
        for d in recover_interrupted(ctx, phase):
            print(f"recovered interrupted run: {d}", flush=True)
        recs = load_records(ctx.out, phase)  # kept in memory; each run appends
        n = 0
        for i, cell in enumerate(cells):
            while not cell_done(cell, recs, cfg):
                attempt = 1 + sum(1 for r in recs if r.get("cell") == cell["key"])
                write_progress(
                    ctx, phase, cells, recs, {"current": cell["key"], "attempt": attempt}
                )
                loaded = ensure_loaded(cfg, ctx.lms, cell["model"])
                if loaded:
                    print(f"loaded {cell['model']}: {loaded}", flush=True)
                r = run_once(ctx, cell, attempt, loaded)
                recs.append(r)
                n += 1
                verdict = (
                    "PASS" if r.get("success") else ("INVALID" if not r.get("valid") else "FAIL")
                )
                print(
                    f"[{i + 1}/{len(cells)}] {cell['key']} {verdict} "
                    f"steps={r.get('steps_passed')}/{r['steps_total']} tools={r.get('n_tool_calls')} "
                    f"invalid={r.get('n_invalid_tool_calls')} out_tok={r.get('output_tokens')} "
                    f"{r['wall_ms'] / 1000:.1f}s otel={str(r.get('otel'))[:40]}"
                    + (f" ({r.get('failure_reason')})" if verdict != "PASS" else ""),
                    flush=True,
                )
                write_progress(ctx, phase, cells, recs, {"current": None, "last": r["run_id"]})
                if limit and n >= limit:
                    return 0
        write_progress(ctx, phase, cells, recs, {"current": None})
    return 0


# ---------------------------------------------------------------- status


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def estimate(cfg: dict, pilot: list[dict]) -> dict:
    """Hours for stage 1 from the pilot's wall times: the median run of each
    model x stage-1 task, times runs per cell. A cell without a valid pilot
    run takes the longest pilot run."""
    valid = [r for r in pilot if r.get("valid") and r.get("wall_ms")]
    longest = max((r["wall_ms"] for r in valid), default=None)
    per_round = 0.0
    missing = []
    for m in cfg["models"]:
        for t in phase_tasks(cfg, "stage1"):
            w = _median([r["wall_ms"] for r in valid if r["model"] == m and r["task"] == t])
            if w is None:
                missing.append(f"{m} {t}")
                w = longest or 0
            per_round += w / 1000
    n = cfg["runs"]["per_cell"]
    budget = cfg["runs"]["budget_hours"]
    return {
        "hours_per_round": round(per_round / 3600, 2),
        "runs_per_cell": n,
        "hours_total": round(per_round * n / 3600, 2),
        "budget_hours": budget,
        "max_runs_per_cell_in_budget": int(budget * 3600 // per_round) if per_round else None,
        "cells_without_pilot": missing,
    }


def cmd_status(cfg: dict) -> int:
    out = repo_path(cfg["runs"]["out_dir"])
    ok_cfg = not config_problems(cfg)
    verdict = stage1_verdict(cfg, load_records(out, "stage1")) if ok_cfg else {}
    for phase in ("pilot", "stage1", "stage2"):
        if not ok_cfg:
            cells = []
        elif phase == "stage2":
            cells = plan(cfg, phase, [m for m, v in verdict.items() if v["passed"]])
        else:
            cells = plan(cfg, phase)
        recs = load_records(out, phase)
        done = sum(1 for c in cells if cell_done(c, recs, cfg))
        print(f"== {phase}: {done}/{len(cells)} cells done, {len(recs)} runs recorded")
        if phase == "stage1":
            mark = cfg["tasks"]["pass_mark"]
            for m, v in verdict.items():
                state = (
                    ("PASSED" if v["passed"] else "did not pass")
                    if v["complete"]
                    else f"open ({v['done']}/{v['cells']} cells)"
                )
                print(f"   stage 1 {m}: successes {v['successes']}, pass mark {mark}: {state}")
        prog = out / f"progress-{phase}.json"
        if prog.exists():
            p = json.loads(prog.read_text())
            print(
                f"   progress {p.get('updated')}: current={p.get('current')} eta_hours={p.get('eta_hours')}"
            )
        if not recs:
            continue
        print(
            f"   {'model':40} {'task':8} {'valid':>5} {'pass':>4} {'med s':>7} {'med out tok':>11} {'med tools':>9}"
        )
        for m in cfg["models"]:
            for t in phase_tasks(cfg, phase):
                rs = [r for r in recs if r["model"] == m and r["task"] == t]
                if not rs:
                    continue
                v = [r for r in rs if r.get("valid")]
                ms = _median([r["wall_ms"] / 1000 for r in v if r.get("wall_ms")])
                print(
                    f"   {m[:40]:40} {t:8} {len(v):>5} {sum(1 for r in v if r.get('success')):>4} "
                    f"{(f'{ms:.0f}' if ms is not None else '-'):>7} "
                    f"{str(_median([r.get('output_tokens') for r in v])):>11} "
                    f"{str(_median([r.get('n_tool_calls') for r in v])):>9}"
                )
        bad = [r["run_id"] for r in recs if r.get("stream_final_event") == "missing"]
        if bad:
            print(
                f"   WARN: {len(bad)} runs have no chat.end stream event; set lmstudio.stream to false if the pilot shows it"
            )
        refused = [
            r["run_id"] for r in recs if str(r.get("otel", "")).startswith(("refused", "failed"))
        ]
        if refused:
            print(f"   WARN: {len(refused)} runs not sent to Honeycomb: {refused[:3]}")
    pilot = load_records(out, "pilot")
    if pilot and not config_problems(cfg):
        est = estimate(cfg, pilot)
        print(
            f"== stage-1 estimate from the pilot: {est['hours_per_round']} h per round of all cells; "
            f"{est['runs_per_cell']} runs per cell = {est['hours_total']} h; "
            f"budget {est['budget_hours']} h fits {est['max_runs_per_cell_in_budget']} runs per cell"
        )
        if est["cells_without_pilot"]:
            print(
                f"   cells without a valid pilot run (longest pilot run assumed): {est['cells_without_pilot']}"
            )
    return 0


# ---------------------------------------------------------------- preflight


def sample_installed(kb, cfg: dict) -> bool:
    """Every sample data set the tasks use is installed."""
    status, body = kb.call("GET", "/api/sample_data", internal=True)
    if status != 200 or not isinstance(body, list):
        return False
    have = {d.get("id") for d in body if d.get("status") == "installed"}
    return set(samples(cfg)) <= have


def list_server_tools(entry: dict, timeout: float = 120) -> list[str]:
    """Start the mcp.json entry as LM Studio would (stdio) and list its tools.
    The server's stderr goes to a temporary file; its tail is in the error."""
    import asyncio  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from fastmcp import Client  # noqa: PLC0415
    from fastmcp.client.transports import StdioTransport  # noqa: PLC0415

    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env.update(entry.get("env") or {})
    with tempfile.TemporaryDirectory(prefix="local-study-") as tmp:
        log = Path(tmp) / "server.log"

        async def go():
            t = StdioTransport(
                command=entry["command"],
                args=list(entry.get("args") or []),
                env=env,
                log_file=log,
            )
            async with Client(t) as c:
                return sorted(x.name for x in await c.list_tools())

        try:
            return asyncio.run(asyncio.wait_for(go(), timeout))
        except Exception as e:
            tail = log.read_text(errors="replace")[-300:] if log.exists() else ""
            raise RuntimeError(f"{e}; server stderr: {tail.strip()}") from e


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, level: str, name: str, detail: str, fix: str = "") -> None:
        self.rows.append((level, name, detail, fix))

    def ok(self, name, detail):
        self.add("PASS", name, detail)

    def fail(self, name, detail, fix):
        self.add("FAIL", name, detail, fix)

    def warn(self, name, detail, fix=""):
        self.add("WARN", name, detail, fix)

    def print(self) -> int:
        for level, name, detail, fix in self.rows:
            print(f"{level:4}  {name}: {detail}")
            if fix and level != "PASS":
                print(f"      fix: {fix}")
        fails = sum(1 for r in self.rows if r[0] == "FAIL")
        print(
            f"\npreflight: {'FAIL' if fails else 'PASS'} ({fails} failing, {sum(1 for r in self.rows if r[0] == 'WARN')} warnings)"
        )
        return 1 if fails else 0


def cmd_preflight(cfg: dict, load: bool, list_tools=list_server_tools) -> int:
    rep = Report()
    problems = config_problems(cfg)
    if problems:
        rep.fail(
            "config",
            "; ".join(problems),
            "list the models to study in local_study.json (keys from `lms ls`)",
        )
    else:
        rep.ok(
            "config",
            f"{len(cfg['models'])} models; stage 1 {cfg['tasks']['stage1']}, stage 2 {cfg['tasks']['stage2']}; "
            f"{cfg['runs']['per_cell']} runs per cell, pass mark {cfg['tasks']['pass_mark']}",
        )

    # Kibana
    seed_ok = False
    try:
        url, key = load_seed(cfg)
        seed_ok = True
        rep.ok("seed", f"{cfg['kibana']['seed_file']} has KIBANA_URL and KIBANA_TEST_API_KEY")
    except (OSError, KeyError):
        url = key = None
        rep.fail(
            "seed",
            f"{cfg['kibana']['seed_file']} missing or incomplete",
            "scripts/stack.sh up && scripts/stack.sh seed",
        )
    kb = er.Kibana(url, key) if seed_ok else None
    if kb:
        # Authenticated: an anonymous /api/status answers 200 without the version.
        status, body = kb.call("GET", "/api/status")
        version = (
            ((body or {}).get("version") or {}).get("number") if isinstance(body, dict) else None
        )
        if status != 200:
            rep.fail(
                "kibana_up",
                f"{url}/api/status answered {status}",
                f"ES_LOCAL_VERSION={cfg['kibana']['version']} scripts/stack.sh up",
            )
        elif version != cfg["kibana"]["version"]:
            rep.fail(
                "kibana_version",
                f"Kibana is {version}, the study pins {cfg['kibana']['version']}",
                f"scripts/stack.sh down && ES_LOCAL_VERSION={cfg['kibana']['version']} scripts/stack.sh up && scripts/stack.sh seed",
            )
        else:
            rep.ok("kibana_up", f"Kibana {version} at {url}")
        status, _ = kb.call("GET", "/api/features")
        if status == 200:
            rep.ok("kibana_key", "the seed API key authenticates")
        else:
            rep.fail(
                "kibana_key",
                f"/api/features with the seed key answered {status}",
                "scripts/stack.sh seed (re-mints an invalid key)",
            )
        if sample_installed(kb, cfg):
            rep.ok(
                "sample_data",
                f"sample data {samples(cfg)} installed (GET /api/sample_data)",
            )
        else:
            rep.fail(
                "sample_data",
                f"sample data {samples(cfg)} not all installed",
                "uv run python scripts/experiment/local_run.py install-sample",
            )
        prefix = cfg["kibana"]["marker_prefix"] + "-"
        _, rules = kb.call("GET", "/api/alerting/rules/_find?per_page=1000")
        stray = (
            [
                r["name"]
                for r in (rules or {}).get("data", [])
                if r.get("name", "").startswith(prefix)
            ]
            if isinstance(rules, dict)
            else []
        )
        if stray:
            rep.warn(
                "kibana_clean",
                f"{len(stray)} rules from earlier runs ({stray[:2]})",
                "delete them in Kibana > Stack Management > Rules; runs reset only what they created",
            )
        else:
            rep.ok("kibana_clean", f"no rules named {prefix}*")

    # The checker's Elasticsearch: rule filters are evaluated there.
    try:
        es = st.Elastic.from_stack()
        status, _ = es.call("GET", "/")
    except (OSError, SystemExit) as e:
        status = repr(e)
    if status == 200:
        rep.ok("checker_es", "the stack's Elasticsearch answers as the elastic user")
    else:
        rep.fail(
            "checker_es",
            f"the checker cannot reach the stack's Elasticsearch ({status})",
            f"ES_LOCAL_VERSION={cfg['kibana']['version']} scripts/stack.sh up && scripts/stack.sh seed",
        )

    # LM Studio
    lm = cfg["lmstudio"]
    token = env_value(lm["token_env"])
    client = LMStudio(lm["url"], token)
    status, _ = client.models(auth=False)
    reachable = status != 0
    if not reachable:
        rep.fail(
            "lmstudio_up",
            f"no answer from {lm['url']}",
            "start the server: `lms server start` (or LM Studio > Developer > Start)",
        )
    else:
        rep.ok("lmstudio_up", f"{lm['url']} answers")
    models: list[dict] = []
    if reachable:
        if not token:
            rep.fail(
                "lmstudio_token",
                f"{lm['token_env']} is not set",
                f"create a token with 'Use MCP Servers' (docs/e2e-setup.md step 3) and export {lm['token_env']} or put it in .env.local",
            )
        status, models = client.models()
        if token and status == 200:
            rep.ok("lmstudio_token", "the token is accepted")
        elif token:
            rep.fail(
                "lmstudio_token",
                f"/api/v1/models with the token answered {status}",
                "create a new token (Developer > Settings > Manage Tokens) with 'Use MCP Servers'",
            )
    if reachable and models and not problems:
        keys = {m["key"]: m for m in models}
        for m in cfg["models"]:
            info = keys.get(m)
            if not info:
                rep.fail(
                    f"model {m}",
                    "not in LM Studio's model list",
                    "use the exact key from `lms ls` (or download it: `lms get <key>`)",
                )
                continue
            detail = f"max context {info['max_context']}" + (
                f", loaded at {info['loaded_context']}" if info["loaded"] else ", not loaded"
            )
            mc = lm["min_context_length"]
            if info["max_context"] and info["max_context"] < mc:
                rep.warn(
                    f"model {m}",
                    detail + f" (< {mc})",
                    "the study assumes every model takes at least min_context_length",
                )
            elif info["loaded"] and info["loaded_context"] and info["loaded_context"] < mc:
                rep.warn(
                    f"model {m}",
                    detail,
                    f"reload it with the maximum context: `lms unload {m}` and let the run load it (defaultContextLength max)",
                )
            elif info["tool_use"] is False:
                rep.warn(
                    f"model {m}",
                    detail + "; LM Studio does not mark it trained for tool use",
                    "keep it if the owner wants it; expect invalid tool calls",
                )
            else:
                rep.ok(f"model {m}", detail)

    # mcp.json (read only)
    path = Path(os.path.expanduser(lm["mcp_json"]))
    entry = None
    try:
        entry = json.loads(path.read_text()).get("mcpServers", {}).get(cfg["server"]["entry"])
    except (OSError, ValueError) as e:
        rep.fail(
            "mcp_json",
            f"cannot read {path}: {e.__class__.__name__}",
            "create it as in docs/e2e-setup.md step 5 (the file is yours; no script writes it)",
        )
    if path.exists() and entry is None:
        rep.fail(
            "mcp_json_entry",
            f"no '{cfg['server']['entry']}' server in {path}",
            "add the entry from LOCAL_STUDY.md (step 3) by hand",
        )
    if entry is not None:
        env = entry.get("env") or {}
        issues = []
        if env.get("KIBANA_MCP_TOOLBOXES") != cfg["server"]["toolboxes"]:
            issues.append(
                f"KIBANA_MCP_TOOLBOXES is {env.get('KIBANA_MCP_TOOLBOXES')!r}, want {cfg['server']['toolboxes']!r}"
            )
        if env.get("KIBANA_MCP_TIER", "write") != cfg["server"]["tier"]:
            issues.append(
                f"KIBANA_MCP_TIER is {env.get('KIBANA_MCP_TIER')!r}, want {cfg['server']['tier']!r}"
            )
        if seed_ok:
            env_file = env.get("KIBANA_MCP_ENV_FILE")
            if env_file:
                if Path(env_file).resolve() != repo_path(cfg["kibana"]["seed_file"]).resolve():
                    issues.append("KIBANA_MCP_ENV_FILE does not point at this checkout's seed file")
            else:
                if env.get("KIBANA_URL", "").rstrip("/") != url.rstrip("/"):
                    issues.append(f"KIBANA_URL is {env.get('KIBANA_URL')!r}, the stack is {url!r}")
                if env.get("KIBANA_API_KEY") != key:
                    issues.append(
                        "KIBANA_API_KEY differs from the seed's KIBANA_TEST_API_KEY (value not shown)"
                    )
        if str(env.get("KIBANA_MCP_OTEL_ENABLED", "")).lower() in ("1", "true"):
            issues.append(
                "KIBANA_MCP_OTEL_ENABLED is on; the Claude study kept the server's own spans off"
            )
        if issues:
            rep.fail(
                "mcp_json_entry",
                "; ".join(issues),
                f"edit the '{cfg['server']['entry']}' entry in {path} by hand (LOCAL_STUDY.md step 3)",
            )
        else:
            rep.ok(
                "mcp_json_entry",
                f"'{cfg['server']['entry']}' targets this stack with {cfg['server']['toolboxes']} at tier {cfg['server']['tier']}",
            )
        try:
            tools = list_tools(entry)
            missing = sorted(set(cfg["server"]["required_tools"]) - set(tools))
            if len(tools) != cfg["server"]["expected_tools"] or missing:
                rep.fail(
                    "server_tools",
                    f"{len(tools)} tools (want {cfg['server']['expected_tools']}), missing {missing}",
                    "check the entry's KIBANA_MCP_TOOLBOXES and KIBANA_MCP_TIER",
                )
            else:
                rep.ok("server_tools", f"the entry starts and lists {len(tools)} tools")
        except Exception as e:
            rep.fail(
                "server_tools",
                f"the entry did not start: {e.__class__.__name__}: {scrub(str(e), [key or ''])[:400]}",
                "run the entry's command by hand; `uv sync` in this checkout",
            )

    # load check: one short chat per model through the integration
    if load and reachable and token and not problems and entry is not None:
        for m in cfg["models"]:
            loaded = ensure_loaded(cfg, client, m)
            if loaded and loaded["status"] != 200:
                rep.fail(
                    f"load {m}",
                    f"POST /api/v1/models/load answered {loaded['status']}: {loaded['error']}",
                    "free memory (`lms ps`), or pick a model that fits at its maximum context",
                )
                continue
            chat = client.chat(
                {
                    "model": m,
                    "input": "Reply with the single word OK. Do not call any tool.",
                    "integrations": [f"mcp/{cfg['server']['entry']}"],
                },
                stream=False,
                run_timeout=lm["run_timeout_s"],
                idle_timeout=lm["idle_timeout_s"],
            )
            valid, why, _ = classify_validity(chat)
            if chat["http_status"] == 200 and valid:
                stats = (chat["result"] or {}).get("stats") or {}
                rep.ok(
                    f"load {m}",
                    f"answered in {chat['wall_ms'] / 1000:.1f}s (load {stats.get('model_load_time_seconds')}s)",
                )
            else:
                body = why or ""
                fix = "free memory: `lms ps`, then `lms unload <identifier>` for models other work does not need"
                if "Permission denied to use plugin" in body:
                    fix = "turn on 'Allow calling servers from mcp.json' and give the token 'Use MCP Servers' (docs/e2e-setup.md steps 3-4)"
                rep.fail(f"load {m}", scrub(body, [token or ""])[:300], fix)
    elif not load:
        rep.warn(
            "load", "models not loaded (pass --load to load each once through the integration)"
        )

    # Honeycomb
    hc = cfg["honeycomb"]
    key_hc = env_value(hc["key_env"])
    if not key_hc:
        level = rep.fail if hc["required"] else rep.warn
        level(
            "honeycomb_key",
            f"{hc['key_env']} is not set",
            f"export {hc['key_env']}=<an ingest key for the environment> (never written to disk)",
        )
    else:
        parts = urllib.parse.urlsplit(hc["endpoint"])
        status, body = http(
            "GET",
            f"{parts.scheme}://{parts.netloc}/1/auth",
            headers={"x-honeycomb-team": key_hc},
            timeout=15,
        )
        if status == 200:
            env_slug = (
                ((body or {}).get("environment") or {}).get("slug")
                if isinstance(body, dict)
                else None
            )
            rep.ok(
                "honeycomb_key", f"key accepted (environment {env_slug}); dataset {hc['dataset']}"
            )
        else:
            rep.fail(
                "honeycomb_key",
                f"{parts.netloc}/1/auth answered {status}",
                "check the key and the endpoint's region (EU: api.eu1.honeycomb.io)",
            )

    out = repo_path(cfg["runs"]["out_dir"])
    try:
        out.mkdir(parents=True, exist_ok=True)
        rep.ok("out_dir", str(out.relative_to(REPO)) if out.is_relative_to(REPO) else str(out))
    except OSError as e:
        rep.fail("out_dir", repr(e), "pick a writable runs.out_dir")
    if shutil.which("caffeinate") is None and sys.platform == "darwin":
        rep.warn("caffeinate", "not found", "run the matrix under `caffeinate -dims`")
    return rep.print()


def cmd_send_pending(cfg: dict) -> int:
    """Send the stored spans.json of every run whose send was skipped because
    the Honeycomb key was missing at the time. The same secret scan applies;
    record.json keeps the result in ``otel`` and the time in ``otel_sent_late``."""
    key_name = cfg["honeycomb"]["key_env"]
    if not env_value(key_name):
        print(f"FAIL: {key_name} is not set (shell or .env.local)")
        return 1
    _, kibana_key = load_seed(cfg)
    known = [
        s for s in (kibana_key, env_value(cfg["lmstudio"]["token_env"]), env_value(key_name)) if s
    ]
    out = repo_path(cfg["runs"]["out_dir"])
    sent = failed = 0
    for rec_path in sorted(out.glob("*/*/*/record.json")):
        rec = json.loads(rec_path.read_text())
        if not str(rec.get("otel", "")).startswith("skipped"):
            continue
        result = send_body(cfg, json.loads((rec_path.parent / "spans.json").read_text()), known)
        rec["otel"] = result
        rec["otel_sent_late"] = now_iso()
        write_json(rec_path, rec, known)
        ok = result.startswith("sent")
        sent += ok
        failed += not ok
        print(f"{rec['run_id']}: {result}", flush=True)
    print(f"send-pending: {sent} sent, {failed} not sent")
    return 0 if failed == 0 else 1


def cmd_install_sample(cfg: dict) -> int:
    url, key = load_seed(cfg)
    kb = er.Kibana(url, key)
    if sample_installed(kb, cfg):
        print(f"sample data {samples(cfg)} already installed")
        return 0
    try:
        st.ensure_samples(kb, tasks_cfg(cfg))
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 1
    print(f"installed sample data {samples(cfg)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight", help="read-only checks with a fix for each failure")
    p.add_argument(
        "--load", action="store_true", help="also load each model once through the integration"
    )
    sub.add_parser("install-sample", help="install the sample data the tasks use, if missing")
    for name, text in (
        ("pilot", "pilot_per_cell runs per model x stage-1 task"),
        ("stage1", "stage 1: every model x stage-1 task (resumable)"),
        ("stage2", "stage 2: the stage-2 tasks for the models that passed stage 1 (resumable)"),
    ):
        q = sub.add_parser(name, help=text)
        q.add_argument(
            "--limit", type=int, default=None, help="stop after this many runs (testing)"
        )
    sub.add_parser("status", help="progress, pilot timings and the time estimate")
    sub.add_parser(
        "send-pending", help="send the stored spans of runs made without the Honeycomb key"
    )
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.cmd == "preflight":
        return cmd_preflight(cfg, args.load)
    if args.cmd == "install-sample":
        return cmd_install_sample(cfg)
    if args.cmd in ("pilot", "stage1", "stage2"):
        return run_matrix(cfg, args.cmd, args.limit)
    if args.cmd == "send-pending":
        return cmd_send_pending(cfg)
    return cmd_status(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
