"""Efficiency study runner: drive `claude -p` through one (model, arm) cell of
the end-to-end web-logs chain and keep the evidence of every run.

    uv run python scripts/experiment/efficiency_run.py \
        --model claude-haiku-5-5 --arm with-mcp --runs 1 --block pilot

The question: does mcp-for-kibana make Claude's Kibana work cheaper or
shorter? Two arms, one identical prompt:

    with-mcp     mcp-for-kibana registered (dashboards, data-management,
                 alerting at the write tier)
    no-mcp       no MCP server

and a third, added for the live rerun (additive; the two above are unchanged):

    with-mcp-es  mcp-for-kibana plus Elastic's Elasticsearch MCP server
                 (ES_MCP_IMAGE, stdio in Docker) on a read-only API key minted
                 per run for the web-logs index and invalidated after it

All arms get the same built-in tools, confined to a fresh working directory
that holds Kibana's pinned API reference (kibana-api/): Bash, Read, Write,
Glob and Grep, with settings, skills and inherited MCP servers off. Only the
servers vary.

Every run writes a directory under --out: the prompt, the full stream-json
transcript, metrics, the success check and the dashboard and rule it
produced. Kibana is reset after every run: whatever the run created
(saved objects, rules, connectors) is deleted, so the next run starts clean.
The runner never touches objects that existed before the run.

When HONEYCOMB_API_KEY is set, each run is also sent as one OpenTelemetry
trace (OTLP/HTTP JSON): a root span carrying the outcome and metrics, and one
child span per tool call. Without the key the trace is skipped and the
record says so. HONEYCOMB_OTLP_ENDPOINT picks the region (default: EU,
https://api.eu1.honeycomb.io/v1/traces; US is https://api.honeycomb.io/v1/traces).
The server's own span export stays off in the with-mcp arm, so the arms
differ only by the server.

--live-telemetry (off by default; the first study's behaviour is unchanged
without it) replaces that after-the-fact trace with signals emitted at run
time: Claude Code's own spans, log events and metrics, the server's tool-call
spans and a runner root span, all in one trace per run, through a local
scrubbing relay to Honeycomb (live_telemetry.py, LIVE_RERUN.md). The mode also
switches on the fixes parked after the first study: the first result event is
read, permission denials are counted from the whole stream, the sample data is
checked before every run, and the prompt (version 2) says the rule must be
enabled.

--task (study 4, off by default; without it the web-logs chain above runs
unchanged) runs one of the four shared tasks of studies 4 and 5 instead. The
prompt, the seeded starting state and the success check come from
tasks.json through study_tasks.py, never from this file: the with-mcp arm
gets the "server" preamble, the no-mcp arm the "api" one. Before every task
run the seeded dashboard and rule are restored; after it, every check's
expected and actual value goes to outcome.json and, through
study_tasks.span_attributes, onto the run's root span. The first result event
is read (the parked fix), the transcript is written as it streams, a run is
cut at 30 minutes, the Kibana key stays out of the with-mcp model's
environment (the no-mcp arm needs it to reach Kibana), and TMPDIR points at
tmp/ inside the working directory in both arms.

--blocks FIRST LAST drives a whole plan: every (task, model, arm) cell once
per block, the order rotated by block (tasks by one place, models by one
place, arms swapped every other block), with a stall alert when nothing has
progressed for --stall-minutes (default 40): no transcript line, no finished
run. The alert is a STALL line on stderr and an entry in <out>/stalls.jsonl;
the plan keeps going. STUDY_DESIGN.md decision 14.
"""

import argparse
import contextlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED = _REPO_ROOT / "elastic-start-local" / ".env.seed"
_DEFAULT_OUT = Path(__file__).resolve().parent / "runs" / "efficiency"
_DEFAULT_LIVE_OUT = _DEFAULT_OUT / "live"
_TIMEOUT_S = 1200
TASK_TIMEOUT_S = 1800  # --task runs: 30 minutes, the studies' limit (board d16)
_DEFAULT_STUDY4_OUT = _DEFAULT_OUT / "study4"
STALL_MINUTES = 40
STUDY4_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
STUDY4_ARMS = ("with-mcp", "no-mcp")
# The tasks.json preamble each arm gets in --task mode.
TASK_ARM = {"with-mcp": "server", "no-mcp": "api", "with-mcp-es": "server"}

KIBANA_VERSION = "9.5.5"
LOGS_INDEX = "kibana_sample_data_logs"
TOOLBOXES = "dashboards,data-management,alerting"

# Pinned API reference, identical in both arms. Kibana's bundled spec carries
# only stubs for the Dashboards and Visualizations APIs and points to a
# separate spec repo, which has no version tags: pinned by commit (the 9.5 GA
# spec, 2026-09-17).
SPECS = {
    "kibana.yaml": (
        f"https://raw.githubusercontent.com/elastic/kibana/v{KIBANA_VERSION}"
        "/oas_docs/output/kibana.yaml"
    ),
    "dashboards-api.yaml": (
        "https://raw.githubusercontent.com/elastic/dashboards-api-spec/"
        "9b2f365a35470793df7a3caf52c24453022cf7c7/openapi/kibana-openapi.yaml"
    ),
}
_SPEC_CACHE = Path.home() / ".cache" / "mcp-for-kibana-efficiency" / "specs"

ARMS = ("with-mcp", "no-mcp", "with-mcp-es")
SERVER_ARMS = ("with-mcp", "with-mcp-es")  # arms that run mcp-for-kibana

# The third arm's Elasticsearch MCP server: elastic/mcp-server-elasticsearch
# v0.4.6 (the latest tag; the repository is deprecated in favour of Kibana's
# Agent Builder MCP endpoint, which needs an Enterprise licence the local
# stack's basic licence lacks). Pinned by digest; `latest` was the same image
# on 2026-10-11. Tools: list_indices, get_mappings, search, esql, get_shards.
ES_MCP_IMAGE = (
    "docker.elastic.co/mcp/elasticsearch:0.4.6"
    "@sha256:d57ea11dcb3451ca332cb6d3bb8c4bb1ea29f15e498937ad6c2eada9f88eb003"
)
ES_MCP_SERVER = "elasticsearch"  # tools are mcp__elasticsearch__<tool>
# Read-only on the web-logs data stream (and its backing indices): search and
# ES|QL (read), mappings (view_index_metadata), _cat indices/shards (monitor).
# Nothing that writes data, manages indices or mints keys.
ES_KEY_PRIVILEGES = ("read", "view_index_metadata", "monitor")
# _cat/indices and _cat/shards (list_indices, get_shards) also need the
# read-only `monitor` cluster privilege; without it both tools answer 403.
ES_KEY_CLUSTER_PRIVILEGES = ("monitor",)
ES_KEY_EXPIRATION = "2h"  # a backstop; the runner invalidates the key after the run
_STACK_ENV = _REPO_ROOT / "elastic-start-local" / ".env"

BUILTIN_TOOLS = "Bash,Read,Write,Glob,Grep"
ALLOWED_TOOLS = ["Bash", "Read", "Write", "Glob", "Grep"]

SAVED_OBJECT_TYPES = ("dashboard", "lens", "visualization", "index-pattern", "search", "map")

# Variables of the parent Claude Code session that would leak its identity or
# extra context into the measured run.
_STRIP_ENV = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_ADDITIONAL_DIRECTORIES",
    "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD",
)


def task_prompt(marker: str, version: int = 1) -> str:
    """The task, identical in both arms. Version 2 (the live rerun's default)
    states that the rule must be enabled, which the success check requires."""
    finish = (
        "Finish when the dashboard and the rule both exist."
        if version == 1
        else "Finish when the dashboard and the rule both exist and the rule is enabled."
    )
    return (
        "A local Kibana 9.5 is available. Its URL is in the environment "
        "variable KIBANA_URL and an API key in KIBANA_TEST_API_KEY. Kibana's "
        "API reference is in ./kibana-api/ (kibana.yaml for the whole API, "
        "dashboards-api.yaml for the Dashboards and Visualizations APIs).\n\n"
        "Using Kibana's web-logs sample data (data view 'Kibana Sample Data "
        f"Logs', index {LOGS_INDEX}):\n"
        "1. Explore the data view's fields to understand what the data holds.\n"
        f"2. Build a dashboard titled '{marker} Web logs overview' with at "
        "least three panels showing the key metrics of this traffic.\n"
        f"3. Create an alert rule named '{marker} 5xx spike' that checks every "
        "minute and fires when more than 10 requests with a 5xx response code "
        "arrive within 5 minutes.\n" + finish
    )


# ---------------------------------------------------------------- Kibana HTTP


class Kibana:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.headers = {
            "Authorization": f"ApiKey {api_key}",
            "kbn-xsrf": "true",
            "Content-Type": "application/json",
        }

    def call(self, method: str, path: str, body: dict | None = None, internal=False):
        headers = dict(self.headers)
        if internal:
            headers["x-elastic-internal-origin"] = "Kibana"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode(errors="replace")

    def snapshot(self) -> dict:
        """Ids of everything a run could create."""
        objects = []
        for so_type in SAVED_OBJECT_TYPES:
            page = 1
            while True:
                status, body = self.call(
                    "GET", f"/api/saved_objects/_find?type={so_type}&per_page=1000&page={page}"
                )
                if status != 200:
                    break
                batch = body.get("saved_objects", [])
                objects += [(so_type, o["id"]) for o in batch]
                if len(batch) < 1000:
                    break
                page += 1
        _, rules = self.call("GET", "/api/alerting/rules/_find?per_page=1000")
        _, connectors = self.call("GET", "/api/actions/connectors")
        return {
            "objects": sorted(objects),
            "rules": sorted(r["id"] for r in (rules or {}).get("data", [])),
            "connectors": sorted(c["id"] for c in (connectors or [])),
        }

    def reset_to(self, before: dict) -> dict:
        """Delete what appeared since `before`. Returns what was deleted."""
        after = self.snapshot()
        removed = {"objects": [], "rules": [], "connectors": []}
        for rule_id in set(after["rules"]) - set(before["rules"]):
            self.call("DELETE", f"/api/alerting/rule/{rule_id}")
            removed["rules"].append(rule_id)
        for conn_id in set(after["connectors"]) - set(before["connectors"]):
            self.call("DELETE", f"/api/actions/connector/{conn_id}")
            removed["connectors"].append(conn_id)
        before_objects = {tuple(o) for o in before["objects"]}
        for so_type, so_id in set(map(tuple, after["objects"])) - before_objects:
            if so_type == "dashboard":
                status, _ = self.call("DELETE", f"/api/dashboards/{so_id}")
            else:
                status = 0
            if status not in (200, 204):
                self.call("DELETE", f"/api/saved_objects/{so_type}/{so_id}?force=true")
            removed["objects"].append([so_type, so_id])
        return removed


def load_seed() -> tuple[str, str]:
    values = {}
    for line in _SEED.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values["KIBANA_URL"], values["KIBANA_TEST_API_KEY"]


def load_es_admin(env_file: Path = _STACK_ENV) -> tuple[str, str]:
    """Elasticsearch URL and the elastic user's password from the local
    stack's .env (as scripts/stack.sh seed reads them). The password only
    mints and invalidates the third arm's read-only key; it reaches no model,
    server or telemetry."""
    values = {}
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    port, password = values.get("ES_LOCAL_PORT"), values.get("ES_LOCAL_PASSWORD")
    if not port or not password:
        raise SystemExit(
            f"FAIL: ES_LOCAL_PORT or ES_LOCAL_PASSWORD missing from {env_file} — the "
            "with-mcp-es arm needs the local stack: scripts/stack.sh up && scripts/stack.sh seed"
        )
    return f"http://localhost:{port}", password


def es_key_request(name: str) -> dict:
    """Body of POST /_security/api_key for the third arm's read-only key."""
    return {
        "name": name,
        "expiration": ES_KEY_EXPIRATION,
        "role_descriptors": {
            "efficiency-es-read": {
                "cluster": list(ES_KEY_CLUSTER_PRIVILEGES),
                "indices": [{"names": [LOGS_INDEX], "privileges": list(ES_KEY_PRIVILEGES)}],
            }
        },
        "metadata": {"purpose": "mcp-for-kibana efficiency study, with-mcp-es arm"},
    }


def _es_call(url: str, password: str, method: str, path: str, body: dict) -> tuple[int, dict]:
    import base64

    auth = base64.b64encode(f"elastic:{password}".encode()).decode()
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def mint_es_key(url: str, password: str, name: str) -> tuple[str, str]:
    """(key id, encoded key) of a fresh read-only key (es_key_request)."""
    status, body = _es_call(url, password, "POST", "/_security/api_key", es_key_request(name))
    if status != 200 or not body.get("encoded"):
        reason = (body.get("error") or {}).get("reason") if isinstance(body, dict) else None
        raise SystemExit(
            f"FAIL: could not mint the with-mcp-es arm's read-only Elasticsearch key "
            f"(HTTP {status}: {reason}) — check the stack with scripts/stack.sh status"
        )
    return body["id"], body["encoded"]


def invalidate_es_key(url: str, password: str, key_id: str) -> bool:
    status, body = _es_call(url, password, "DELETE", "/_security/api_key", {"ids": [key_id]})
    return status == 200 and key_id in (body.get("invalidated_api_keys") or [])


def es_mcp_server(es_url: str, es_key: str, container: str) -> dict:
    """The Elasticsearch MCP server's entry in the MCP config: stdio in Docker
    on the host network (the stack publishes Elasticsearch on loopback). The
    key goes only into this entry's env, which `docker run -e NAME` forwards
    to the container: it is in no argv and not in the model's environment."""
    # The image rewrites a 'localhost' host to host.docker.internal; on the
    # host network the loopback address is the right one.
    es_url = es_url.replace("://localhost", "://127.0.0.1")
    return {
        "command": "docker",
        "args": [
            *("run", "-i", "--rm", "--name", container, "--network", "host"),
            *("-e", "ES_URL", "-e", "ES_API_KEY", ES_MCP_IMAGE, "stdio"),
        ],
        "env": {"ES_URL": es_url, "ES_API_KEY": es_key},
    }


TRACE_ANCHOR_SERVER = "trace-anchor"


def trace_anchor_server() -> dict:
    """The no-mcp arm's tool-less MCP server entry (live mode). Without any
    MCP server Claude Code 2.1.296 starts its first query before its tracer
    is ready: no claude_code.interaction span, TRACEPARENT ignored, every
    span its own trace (LIVE_RERUN.md, "The no-mcp trace anchor")."""
    return {
        "command": sys.executable,
        "args": ["-I", "-S", str(Path(__file__).resolve().parent / "trace_anchor_server.py")],
    }


def ensure_logs_sample(kb: Kibana) -> None:
    status, _ = kb.call("GET", f"/api/index_management/indices/{LOGS_INDEX}", internal=True)
    if status == 200:
        return
    status, body = kb.call("POST", "/api/sample_data/logs", {}, internal=True)
    if status not in (200, 201):
        raise SystemExit(f"FAIL: could not load the web-logs sample data ({status}): {body}")


def sample_data_installed(kb: Kibana) -> bool:
    """The web-logs sample set's status from Kibana's own sample-data API
    (read only; nothing is reinstalled)."""
    status, body = kb.call("GET", "/api/sample_data", internal=True)
    if status != 200 or not isinstance(body, list):
        return False
    return any(d.get("id") == "logs" and d.get("status") == "installed" for d in body)


def ensure_specs() -> Path:
    _SPEC_CACHE.mkdir(parents=True, exist_ok=True)
    for name, url in SPECS.items():
        target = _SPEC_CACHE / name
        if not target.exists() or target.stat().st_size == 0:
            subprocess.run(["curl", "-fsSL", "-o", str(target), url], check=True)
    return _SPEC_CACHE


# ---------------------------------------------------------------- scoring


def score(kb: Kibana, marker: str, before: dict) -> tuple[dict, dict]:
    """The success check. Returns (outcome, produced artifacts)."""
    outcome = {
        "dashboard_found": False,
        "dashboard_panels": 0,
        "dashboard_uses_logs": False,
        "rule_found": False,
        "rule_enabled": False,
        "rule_uses_logs": False,
        "rule_type_id": None,
    }
    artifacts: dict = {"dashboards": [], "rules": []}

    # New dashboards come from the snapshot diff, not a title search: Kibana's
    # search tokenises the marker, and 9.5 wraps results in data/meta.
    existing = {tuple(o) for o in before["objects"]}
    new_ids = [
        so_id
        for so_type, so_id in kb.snapshot()["objects"]
        if so_type == "dashboard" and (so_type, so_id) not in existing
    ]
    for dash_id in new_ids:
        status, full = kb.call("GET", f"/api/dashboards/{dash_id}")
        if status == 200 and marker in json.dumps(full):
            artifacts["dashboards"].append(full)
    if artifacts["dashboards"]:
        best = max(artifacts["dashboards"], key=lambda d: len(_panels(d)))
        outcome["dashboard_found"] = True
        outcome["dashboard_panels"] = len(_panels(best))
        outcome["dashboard_uses_logs"] = _mentions_logs(kb, best, before)

    _, rules = kb.call("GET", "/api/alerting/rules/_find?per_page=1000")
    for rule in (rules or {}).get("data", []):
        if marker in rule.get("name", ""):
            artifacts["rules"].append(rule)
    if artifacts["rules"]:
        rule = artifacts["rules"][0]
        outcome["rule_found"] = True
        outcome["rule_enabled"] = bool(rule.get("enabled"))
        outcome["rule_type_id"] = rule.get("rule_type_id")
        outcome["rule_uses_logs"] = _mentions_logs(kb, rule, before)

    reasons = []
    if not outcome["dashboard_found"]:
        reasons.append("no dashboard")
    else:
        if outcome["dashboard_panels"] < 3:
            reasons.append(f"dashboard has {outcome['dashboard_panels']} panels")
        if not outcome["dashboard_uses_logs"]:
            reasons.append("dashboard not on web-logs data")
    if not outcome["rule_found"]:
        reasons.append("no rule")
    else:
        if not outcome["rule_enabled"]:
            reasons.append("rule disabled")
        if not outcome["rule_uses_logs"]:
            reasons.append("rule not on web-logs data")
    outcome["success"] = not reasons
    outcome["failure_reason"] = "; ".join(reasons) or None
    return outcome, artifacts


def _panels(dashboard: dict) -> list:
    data = (dashboard or {}).get("data", dashboard or {})
    return data.get("panels") or []


def _mentions_logs(kb: Kibana, obj: dict, before: dict) -> bool:
    """True when the object points at the web-logs data: by index name, by
    the sample data view's id, or by a data view the run created on it."""
    text = json.dumps(obj)
    if LOGS_INDEX in text:
        return True
    status, body = kb.call("GET", "/api/data_views")
    for dv in (body or {}).get("data_view", []) if status == 200 else []:
        if LOGS_INDEX in (dv.get("title") or "") and dv.get("id") and dv["id"] in text:
            return True
    return False


def score_task(st, cfg: dict, task: str, kb: Kibana, marker: str, before: dict,
               seed_before: dict | None, record: dict, run_dir: Path) -> dict:
    """study_tasks' check of a --task run: Kibana's and Elasticsearch's state
    collected once, scored against the model's final text. Writes
    outcome.json (every check with expected and actual) and artifacts.json
    (what the run left), updates the record, returns the root-span
    attributes (study_tasks.span_attributes)."""
    state = st.collect_state(kb, st.Elastic.from_stack(), cfg, task, marker, before, seed_before)
    outcome = st.score(cfg, task, marker, state, record.get("final_text") or "")
    (run_dir / "outcome.json").write_text(json.dumps(outcome, indent=2, default=str))
    keep = ("dashboards", "rules", "seeded", "seed_before", "filters", "time_fields")
    (run_dir / "artifacts.json").write_text(
        json.dumps({k: state.get(k) for k in keep}, indent=2, default=str)
    )
    record.update(
        {
            "success": outcome["success"],
            "failure_reason": outcome["failure_reason"],
            "first_failed_step": outcome["first_failed_step"],
            "steps_passed": outcome["steps_passed"],
            "steps_total": outcome["steps_total"],
            "checks_total": len(outcome["criteria"]),
            "checks_passed": sum(c["ok"] for c in outcome["criteria"]),
            "n_dashboards": outcome["n_dashboards"],
            "n_rules": outcome["n_rules"],
        }
    )
    return st.span_attributes(outcome)


# ---------------------------------------------------------------- one run


def mcp_config(
    path: Path,
    kibana_url: str,
    api_key: str,
    extra_env: dict | None = None,
    extra_servers: dict | None = None,
) -> Path:
    cfg = {
        "mcpServers": {
            "mcp-for-kibana": {
                "command": "uv",
                "args": ["--directory", str(_REPO_ROOT), "run", "mcp-for-kibana"],
                "env": {
                    "KIBANA_URL": kibana_url,
                    "KIBANA_API_KEY": api_key,
                    "KIBANA_MCP_TOOLBOXES": TOOLBOXES,
                    "KIBANA_MCP_TIER": "write",
                    **(extra_env or {}),
                },
            },
            **(extra_servers or {}),
        }
    }
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)
    return path


def arm_mcp(
    arm: str,
    path: Path,
    kibana_url: str,
    api_key: str,
    server_otel: dict | None = None,
    es: dict | None = None,
    trace_anchor: bool = False,
) -> tuple[list[str], list[str]]:
    """(--mcp-config arguments, MCP entries for --allowedTools) of an arm.
    es (with-mcp-es only): url, api_key and container of the Elasticsearch
    MCP server. trace_anchor (no-mcp only, live mode): connect the tool-less
    trace_anchor_server.py so Claude Code's spans join the run's trace; it
    adds no tools and nothing to --allowedTools."""
    if arm == "no-mcp":
        if not trace_anchor:
            return [], []
        path.write_text(json.dumps({"mcpServers": {TRACE_ANCHOR_SERVER: trace_anchor_server()}}))
        path.chmod(0o600)
        return ["--mcp-config", str(path)], []
    if arm not in SERVER_ARMS:
        raise ValueError(f"unknown arm {arm!r}; one of {', '.join(ARMS)}")
    allowed, extra = ["mcp__mcp-for-kibana"], None
    if arm == "with-mcp-es":
        if not es:
            raise ValueError("the with-mcp-es arm needs the Elasticsearch url, key and container")
        extra = {ES_MCP_SERVER: es_mcp_server(es["url"], es["api_key"], es["container"])}
        allowed.append(f"mcp__{ES_MCP_SERVER}")
    cfg = mcp_config(path, kibana_url, api_key, server_otel, extra)
    return ["--mcp-config", str(cfg)], allowed


def parse_stream(lines: list[str], first_result: bool = False) -> dict:
    """Metrics of one stream-json transcript. first_result=True reads the
    first result event (the model's own end of turn); the default, kept for
    the first study, reads the last one, which a late CLI cleanup result can
    replace."""
    events = []
    for line in lines:
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    init = next((e for e in events if e.get("subtype") == "init"), {})
    results = [e for e in events if e.get("type") == "result"]
    result = (results[0] if first_result else results[-1]) if results else {}
    tool_uses = [
        b
        for e in events
        if e.get("type") == "assistant"
        for b in (e.get("message") or {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    names = [t.get("name", "?") for t in tool_uses]
    usage = result.get("usage") or {}
    return {
        "events": events,
        "init_tools": init.get("tools"),
        "init_mcp_tools": sorted(n for n in (init.get("tools") or []) if n.startswith("mcp__")),
        "init_mcp_servers": init.get("mcp_servers"),
        "tool_calls": names,
        "n_tool_calls": len(names),
        "n_mcp_calls": sum(1 for n in names if n.startswith("mcp__")),
        "n_bash_calls": names.count("Bash"),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "model_usage": result.get("modelUsage"),
        "models_reported": sorted((result.get("modelUsage") or {}).keys()),
        "permission_denials": result.get("permission_denials") or [],
        "n_result_events": len(results),
        "permission_denials_stream": _stream_denials(events, results),
        "is_error": result.get("is_error"),
        "terminal_reason": result.get("terminal_reason") or result.get("subtype"),
        "final_text": result.get("result"),
    }


def _stream_denials(events: list[dict], results: list[dict]) -> list[str]:
    """Every denied tool_use id in the stream: permission_denied system events
    unioned with the lists of all result events."""
    denied = {
        e.get("tool_use_id") or e.get("uuid")
        for e in events
        if e.get("type") == "system" and e.get("subtype") == "permission_denied"
    }
    for r in results:
        denied.update(d.get("tool_use_id") for d in r.get("permission_denials") or [])
    denied.discard(None)
    return sorted(denied)


def task_tmpdir(workdir: Path) -> str:
    """TMPDIR for a --task run: tmp/ inside the run's working directory, so
    `$TMPDIR/x` is a writable path the model's tools may touch. With TMPDIR
    empty, `cat > $TMPDIR/x` became `cat > /x`: it hung on stdin and the
    model's `rm /x` was refused (first study b20, study 4 pilot)."""
    tmp = workdir / "tmp"
    tmp.mkdir(exist_ok=True)
    return str(tmp)


def key_in_model_env(arm: str, task: str | None, live: dict | None) -> bool:
    """Whether KIBANA_TEST_API_KEY goes into the model's environment. --task
    runs: never for a server arm (the study's running rule); the no-mcp arm
    needs it to reach Kibana. Otherwise only --key-out-of-model-env drops it."""
    if arm not in SERVER_ARMS:
        return True
    return not (task or (live or {}).get("key_out_of_model_env"))


def run_captured(cmd: list[str], workdir: Path, env: dict) -> tuple[str, str, bool]:
    """(stdout, stderr, timed_out) of the claude call, output kept in memory
    (the first study's way)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        return proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return stdout, stderr, True


def run_streaming(
    cmd: list[str], workdir: Path, env: dict, transcript: Path, timeout: float = _TIMEOUT_S
) -> tuple[str, str, bool]:
    """(stdout, stderr, timed_out) of the claude call, stdout written to
    `transcript` line by line as it streams, so PROGRESS sees a live run;
    stderr goes to stderr.txt beside it, removed when empty."""
    err_path = transcript.with_name("stderr.txt")
    PROGRESS.mark(f"agent {transcript.parent.name}", transcript)
    with transcript.open("w") as out, err_path.open("w") as err:
        proc = subprocess.Popen(
            cmd, cwd=workdir, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL
        )
        try:
            proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            timed_out = True
    PROGRESS.mark(f"scoring {transcript.parent.name}")
    stderr = err_path.read_text(errors="replace")
    if not stderr.strip():
        err_path.unlink()
    return transcript.read_text(errors="replace"), stderr, timed_out


def tasks_sha() -> str:
    """Short SHA-256 of tasks.json: which task definitions a run used."""
    import hashlib

    return hashlib.sha256((Path(__file__).resolve().parent / "tasks.json").read_bytes()).hexdigest()[:12]


def server_revision() -> dict:
    """The server source the with-mcp arm runs: commit and whether src/ has
    uncommitted changes (recorded on the root span; decision R3)."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args], capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "server_rev": git("rev-parse", "--short", "HEAD"),
        "server_dirty": bool(git("status", "--porcelain", "--", "src")),
    }


def run_once(
    model: str,
    arm: str,
    block: str,
    out_root: Path,
    live: dict | None = None,
    task: str | None = None,
) -> dict:
    """One run. live=None is the first study's runner, unchanged. A dict turns
    on live telemetry and the parked fixes: keys dataset, endpoint (None keeps
    the relay's copy only), api_key, keep_identity, key_out_of_model_env,
    server_label, prompt_version. task (study 4) runs that tasks.json task:
    prompt, seed and checks from study_tasks.py."""
    kibana_url, api_key = load_seed()
    kb = Kibana(kibana_url, api_key)
    specs = ensure_specs()
    st = tasks_cfg = None
    if task:
        import study_tasks as st

        tasks_cfg = st.load()
        marker = f"S4-{secrets.token_hex(4)}"
        prompt_version = None
        prompt = st.build_prompt(tasks_cfg, task, TASK_ARM[arm], marker)
    else:
        marker = f"EFF-{secrets.token_hex(4)}"
        prompt_version = live.get("prompt_version", 2) if live else 1
        prompt = task_prompt(marker, prompt_version)
    cell = f"{model}-{arm}-{task}" if task else f"{model}-{arm}"
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{cell}-{uuid.uuid4().hex[:6]}"
    run_dir = out_root / block / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.txt").write_text(prompt)

    record = {
        "run_id": run_id,
        "block": block,
        "model_requested": model,
        "arm": arm,
        "marker": marker,
        "kibana_version": KIBANA_VERSION,
        "toolboxes": TOOLBOXES if arm in SERVER_ARMS else None,
        "claude_cli": subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "git_sha": subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip(),
        "ts_start": datetime.now(UTC).isoformat(),
    }
    if task:
        record.update({"task": task, "tasks_sha": tasks_sha()})
    es = None
    if arm == "with-mcp-es":
        es_url, es_password = load_es_admin()
        es_key_id, es_key = mint_es_key(es_url, es_password, f"eff-{run_id}")
        es = {
            "url": es_url,
            "api_key": es_key,
            "container": f"eff-es-mcp-{uuid.uuid4().hex[:8]}",
            "password": es_password,
            "key_id": es_key_id,
        }
        record.update(
            {
                "es_mcp_image": ES_MCP_IMAGE,
                "es_key_id": es_key_id,
                "es_key_privileges": f"{LOGS_INDEX}:{','.join(ES_KEY_PRIVILEGES)}",
            }
        )
    key_in_env = key_in_model_env(arm, task, live)
    if task:
        record["key_in_model_env"] = key_in_env
    lt = tel = None
    if live:
        import live_telemetry as lt

        record.update(
            {
                "prompt_version": prompt_version,
                "live_telemetry": True,
                "dataset": live["dataset"],
                "key_in_model_env": key_in_env,
                "trace_anchor": arm == "no-mcp" and live.get("trace_anchor", True),
                "sample_data_installed": sample_data_installed(kb),
                **(server_revision() if arm in SERVER_ARMS else {}),
            }
        )
        if live.get("server_label") and arm in SERVER_ARMS:
            record["server_label"] = live["server_label"]
        trace_id, root_id = lt.new_trace()
        attrs = lt.resource_attributes(run_id, model, arm, block)
        if task:  # every component's spans carry the task
            attrs["study.task"] = task
        scrubber = lt.Scrubber(
            [api_key, live.get("api_key") or "", (es or {}).get("api_key") or ""],
            drop_keys=() if live.get("keep_identity") else lt.IDENTITY_ATTRS,
        )
        relay = lt.Relay(
            run_dir / "telemetry.jsonl",
            scrubber,
            upstream=live.get("endpoint"),
            api_key=live.get("api_key"),
            dataset=live["dataset"],
            agent_name=f"claude-code {arm}",
            known=api_key,
        ).start()
        tel = {"trace_id": trace_id, "root_id": root_id, "attrs": attrs, "relay": relay}
        record["trace_id"] = trace_id
    phases: dict[str, tuple[float, float]] = {}
    seed_before = None
    if task:  # every task run starts from the seed as tasks.json defines it
        seeded = st.restore_seed(kb, tasks_cfg)
        if st.task(tasks_cfg, task)["seeded"]:
            seed_before = seeded
    before = kb.snapshot()
    started = time.monotonic()
    t_agent = time.time()
    with tempfile.TemporaryDirectory(prefix="eff-") as tmpdir:
        tmp = Path(tmpdir)
        workdir = tmp / "work"
        (workdir / "kibana-api").mkdir(parents=True)
        for name in SPECS:
            shutil.copy(specs / name, workdir / "kibana-api" / name)
        cmd = [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
            "--restricted",
            "--disable-slash-commands",
            "--tools",
            BUILTIN_TOOLS,
            "--strict-mcp-config",
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            *ALLOWED_TOOLS,
        ]
        server_otel = (
            lt.server_env(tel["relay"].url, live["dataset"], tel["attrs"]) if tel else None
        )
        hook_cfg = None
        if tel and live.get("agent_hook", True):
            hook_cfg = write_hook_config(
                tmp, tel["relay"].url, live["dataset"], tel["attrs"], api_key, workdir
            )
            cmd += ["--settings", json.dumps(hook_settings(hook_cfg))]
        mcp_cfg = tmp / "mcp.json"
        mcp_args, mcp_allowed = arm_mcp(
            arm, mcp_cfg, kibana_url, api_key, server_otel, es, trace_anchor=record.get("trace_anchor", False)
        )
        cmd += mcp_args
        at = cmd.index("--allowedTools") + 1
        cmd[at:at] = mcp_allowed
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        env.update({"KIBANA_URL": kibana_url, "KIBANA_TEST_API_KEY": api_key})
        if task:  # a scratch dir the model may use: TMPDIR is empty otherwise (decision 17)
            env["TMPDIR"] = task_tmpdir(workdir)
        if not key_in_env:
            env.pop("KIBANA_TEST_API_KEY")
        if tel:
            env = lt.claude_env(
                env,
                tel["relay"].url,
                live["dataset"],
                tel["attrs"],
                lt.traceparent(tel["trace_id"], tel["root_id"]),
                metrics=live.get("metrics", False),
            )
        if task:  # written as it streams: the stall watchdog reads its mtime
            stdout, stderr, timed_out = run_streaming(
                cmd, workdir, env, run_dir / "transcript.jsonl", TASK_TIMEOUT_S
            )
        else:
            stdout, stderr, timed_out = run_captured(cmd, workdir, env)
        if es:  # a stdio container normally ends with claude; never leave one behind
            subprocess.run(
                ["docker", "rm", "-f", es["container"]], capture_output=True, check=False
            )
        if hook_cfg:
            record["agent_live"] = read_hook_rollups(hook_cfg, api_key, run_dir)
    record["wall_s"] = round(time.monotonic() - started, 1)
    phases["agent"] = (t_agent, time.time())
    if not task:
        (run_dir / "transcript.jsonl").write_text(stdout)
        if stderr.strip():
            (run_dir / "stderr.txt").write_text(stderr)

    parsed = parse_stream(stdout.splitlines(), first_result=bool(live or task))
    parsed.pop("events")
    if not (live or task):  # the first study's record shape, unchanged
        parsed.pop("n_result_events")
        parsed.pop("permission_denials_stream")
    record.update(parsed)
    record["timed_out"] = timed_out
    if es:
        es_prefix = f"mcp__{ES_MCP_SERVER}__"
        record["n_es_mcp_calls"] = sum(n.startswith(es_prefix) for n in parsed["tool_calls"])
        record["es_tools_listed"] = [t for t in parsed["init_mcp_tools"] if t.startswith(es_prefix)]
        record["es_key_in_output"] = es["api_key"] in stdout or es["api_key"] in stderr
    if live or task:
        record["n_permission_denials"] = len(parsed["permission_denials_stream"])
        record["valid"] = not parsed["permission_denials_stream"]
    else:
        record["valid"] = not parsed["permission_denials"]
    t_score = time.time()

    study_attrs = None
    try:
        if task:
            study_attrs = score_task(
                st, tasks_cfg, task, kb, marker, before, seed_before, record, run_dir
            )
        else:
            outcome, artifacts = score(kb, marker, before)
            record.update(outcome)
            (run_dir / "artifacts.json").write_text(json.dumps(artifacts, indent=2))
    except Exception as e:  # a scoring fault is data, never a lost record
        record["harness_error"] = repr(e)
        record["success"] = False
    phases["score"] = (t_score, time.time())
    try:
        record["reset_removed"] = kb.reset_to(before)
    except Exception as e:
        record["reset_error"] = repr(e)
    if es:
        try:
            key_id = es["key_id"]
            record["es_key_invalidated"] = invalidate_es_key(es["url"], es["password"], key_id)
        except Exception as e:
            record["es_key_invalidated"] = False
            record["reset_error"] = (record.get("reset_error", "") + f" es key: {e!r}").strip()
    phases["reset"] = (phases["score"][1], time.time())
    record["ts_end"] = datetime.now(UTC).isoformat()
    if tel:
        record["otel"] = finish_live(record, tel, phases, live["dataset"], study_attrs)
    else:
        record["otel"] = send_trace(record, study_attrs)
    (run_dir / "record.json").write_text(json.dumps(record, indent=2))
    with (out_root / "runs.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


# ---------------------------------------------------------------- OTel

_HOOK = Path(__file__).resolve().parent / "agent_signals_hook.py"
_HOOK_EVENTS = ("PostToolUse", "PostToolUseFailure", "PermissionDenied", "Stop")


def write_hook_config(
    tmp: Path, relay_url: str, dataset: str, attrs: dict, known: str, workdir: Path | None = None
) -> Path:
    """The agent-signals hook's per-run config, outside the model's working
    directory and readable by the owner only (it holds the Kibana key for
    exact-match exposure detection, as mcp.json next to it does). workdir is
    the run's working directory as the runner created it: the hook judges
    outside-workdir paths against it, not against the first payload's cwd,
    which a leading `cd sub` moves."""
    cfg = tmp / "agent_hook.json"
    body = {
        "relay": relay_url,
        "service": dataset,
        "resource": attrs,
        "state": str(tmp / "agent_hook_state.json"),
        "known_secret": known,
    }
    if workdir is not None:
        body["workdir"] = os.path.realpath(workdir)
    cfg.write_text(json.dumps(body))
    cfg.chmod(0o600)
    return cfg


def hook_settings(cfg: Path) -> dict:
    """--settings for the measured run: the silent agent-signals hook on every
    tool call and at the end of the turn, nothing else."""
    import shlex

    # -I -S: isolated, no site packages; the hook needs the standard library only.
    command = " ".join(shlex.quote(str(x)) for x in (sys.executable, "-I", "-S", _HOOK, cfg))
    entry = [{"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}]
    return {"hooks": {event: entry for event in _HOOK_EVENTS}}


def read_hook_rollups(cfg: Path, known: str, run_dir: Path) -> dict:
    """Run-level agent.* rollups from the hook's state, before the temporary
    directory goes. The state itself (tool results) is not kept; hook errors
    are, for diagnosis."""
    import agent_signals_hook as hook

    errors = Path(str(cfg) + ".errors")
    if errors.exists():
        (run_dir / "hook_errors.txt").write_text(errors.read_text())
    state_path = Path(json.loads(cfg.read_text())["state"])
    if not state_path.exists():
        return {"agent.hook.invocations": 0}
    try:
        return hook.rollups(json.loads(state_path.read_text()), known)
    except Exception as e:  # rollups are data; a fault is recorded, not raised
        return {"agent.hook.rollup_error": repr(e)}


def finish_live(
    record: dict, tel: dict, phases: dict, dataset: str, study: dict | None = None
) -> str:
    """Send the runner's root span through the relay, give the server's batch
    exporter a moment, stop the relay and report what went through."""
    import live_telemetry as lt

    relay = tel["relay"]
    body = lt.runner_spans(
        record, tel["trace_id"], tel["root_id"], phases, dataset, tel["attrs"],
        extra={**(record.get("agent_live") or {}), **(study or {})},
    )
    status = relay.post_json("/v1/traces", body)
    time.sleep(1.0)  # late server flushes (OTEL_BSP_SCHEDULE_DELAY=500 ms)
    with contextlib.suppress(Exception):
        relay.stop()
    st = relay.stats
    record["telemetry"] = {
        **st,
        "secrets_redacted": relay.scrubber.replaced,
        "identity_attrs_dropped": relay.scrubber.dropped,
    }
    return (
        f"live: root span HTTP {status}, {st['requests']} requests "
        f"({st['forwarded']} forwarded, {st['failed']} failed), trace {tel['trace_id']}"
    )


def send_trace(record: dict, study: dict | None = None) -> str:
    key = os.environ.get("HONEYCOMB_API_KEY")
    if not key:
        return "skipped: no HONEYCOMB_API_KEY"
    endpoint = os.environ.get("HONEYCOMB_OTLP_ENDPOINT", "https://api.eu1.honeycomb.io/v1/traces")
    dataset = os.environ.get("HONEYCOMB_DATASET", "kibana-efficiency")
    trace_id = secrets.token_hex(16)
    root_id = secrets.token_hex(8)
    start = int(datetime.fromisoformat(record["ts_start"]).timestamp() * 1e9)
    end = int(datetime.fromisoformat(record["ts_end"]).timestamp() * 1e9)

    def attr(k, v):
        if isinstance(v, bool):
            return {"key": k, "value": {"boolValue": v}}
        if isinstance(v, int):
            return {"key": k, "value": {"intValue": str(v)}}
        if isinstance(v, float):
            return {"key": k, "value": {"doubleValue": v}}
        return {"key": k, "value": {"stringValue": str(v)}}

    keys = (
        "run_id",
        "block",
        "arm",
        "model_requested",
        "marker",
        "kibana_version",
        "success",
        "failure_reason",
        "num_turns",
        "n_tool_calls",
        "n_mcp_calls",
        "n_bash_calls",
        "total_cost_usd",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "wall_s",
        "duration_api_ms",
        "timed_out",
        "valid",
        "steps_passed",
        "first_failed_step",
        "tasks_sha",
    )
    attrs = [attr(k, record.get(k)) for k in keys if record.get(k) is not None]
    attrs += [attr(k, v) for k, v in (study or {}).items()]  # --task: study_tasks.span_attributes
    attrs.append(attr("models_reported", ",".join(record.get("models_reported") or [])))
    spans = [
        {
            "traceId": trace_id,
            "spanId": root_id,
            "name": "efficiency.run",
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(end),
            "kind": 1,
            "attributes": attrs,
        }
    ]
    for i, name in enumerate(record.get("tool_calls") or []):
        spans.append(
            {
                "traceId": trace_id,
                "spanId": secrets.token_hex(8),
                "parentSpanId": root_id,
                "name": f"tool_call {name}",
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(start),
                "kind": 1,
                "attributes": [attr("tool.name", name), attr("tool.index", i)],
            }
        )
    body = {
        "resourceSpans": [
            {
                "resource": {"attributes": [attr("service.name", dataset)]},
                "scopeSpans": [{"scope": {"name": "efficiency_run"}, "spans": spans}],
            }
        ]
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode(),
        method="POST",
        # Classic keys need the dataset header; ingest keys ignore it and name
        # the dataset after service.name, which is the same value.
        headers={
            "Content-Type": "application/json",
            "x-honeycomb-team": key,
            "x-honeycomb-dataset": dataset,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return f"sent: HTTP {resp.status}, trace {trace_id}"
    except Exception as e:
        return f"failed: {e!r}"


# ---------------------------------------------------------------- study 4 plan


class Progress:
    """When the plan last moved: a mark at every phase, or a line written to
    the transcript of the run in flight (its mtime)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.t = time.time()
        self.doing = "starting"
        self.transcript: Path | None = None

    def mark(self, doing: str, transcript: Path | None = None) -> None:
        with self._lock:
            self.t, self.doing, self.transcript = time.time(), doing, transcript

    def last(self) -> tuple[float, str]:
        with self._lock:
            t, doing, transcript = self.t, self.doing, self.transcript
        with contextlib.suppress(OSError):
            if transcript is not None:
                t = max(t, transcript.stat().st_mtime)
        return t, doing


PROGRESS = Progress()


class StallWatch(threading.Thread):
    """Alerts once when nothing has progressed for `minutes`, and once more
    when progress resumes: a STALL/RESUMED line on stderr and an entry in
    <out>/stalls.jsonl. It never stops the plan."""

    def __init__(self, out: Path, minutes: float, progress: Progress = PROGRESS,
                 every_s: float = 30.0, clock=time.time) -> None:
        super().__init__(daemon=True, name="stall-watch")
        self.out, self.limit_s, self.progress = out, minutes * 60, progress
        self.every_s, self.clock = every_s, clock
        self.stalled = False
        self._stop = threading.Event()

    def check(self) -> str | None:
        """One look; returns the event written ("stall", "resumed") or None."""
        last, doing = self.progress.last()
        idle = self.clock() - last
        if not self.stalled and idle >= self.limit_s:
            self.stalled = True
            return self._emit("stall", idle, doing)
        if self.stalled and idle < self.limit_s:
            self.stalled = False
            return self._emit("resumed", idle, doing)
        return None

    def _emit(self, event: str, idle: float, doing: str) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "idle_min": round(idle / 60, 1),
            "doing": doing,
        }
        self.out.mkdir(parents=True, exist_ok=True)
        with (self.out / "stalls.jsonl").open("a") as f:
            f.write(json.dumps(entry) + "\n")
        word = "STALL" if event == "stall" else "RESUMED"
        print(f"{word}: no progress for {entry['idle_min']} min while {doing} "
              f"(limit {self.limit_s / 60:g} min; see {self.out / 'stalls.jsonl'})",
              file=sys.stderr, flush=True)
        return event

    def run(self) -> None:
        while not self._stop.wait(self.every_s):
            self.check()

    def stop(self) -> None:
        self._stop.set()


def _rotate(items, k: int) -> list:
    items = list(items)
    k %= len(items)
    return items[k:] + items[:k]


def block_plan(b: int, tasks, models, arms) -> list[tuple[str, str, str]]:
    """(task, model, arm) cells of block b (1-based), each once: tasks and
    models each rotated by b-1 places and the arm order reversed on even
    blocks, so over 12 blocks every task, model and arm leads equally often."""
    arm_order = list(arms) if b % 2 else list(arms)[::-1]
    return [
        (t, m, a)
        for t in _rotate(tasks, b - 1)
        for m in _rotate(models, b - 1)
        for a in arm_order
    ]


def block_name(b: int) -> str:
    return f"b{b:02d}"


def done_cells(out: Path, block: str) -> set[tuple[str, str, str]]:
    """(task, model, arm) of the block's runs that wrote their record."""
    done = set()
    for rec in (out / block).glob("*/record.json"):
        with contextlib.suppress(ValueError, KeyError, OSError):
            r = json.loads(rec.read_text())
            done.add((r["task"], r["model_requested"], r["arm"]))
    return done


def run_plan(first: int, last: int, tasks, models, arms, out: Path, live: dict | None,
             skip_done: bool, stall_minutes: float) -> int:
    """Every block from first to last; failures are data, the plan goes on."""
    watch = StallWatch(out, stall_minutes)
    watch.start()
    failures = 0
    try:
        for b in range(first, last + 1):
            block = block_name(b)
            cells = block_plan(b, tasks, models, arms)
            skip = done_cells(out, block) if skip_done else set()
            print(f"=== block {block} ({datetime.now(UTC):%FT%TZ}): {len(cells)} cells, "
                  f"{len(skip & set(cells))} already done", flush=True)
            for i, (task, model, arm) in enumerate(cells, 1):
                if (task, model, arm) in skip:
                    continue
                PROGRESS.mark(f"{block} {task} {model} {arm}")
                try:
                    r = run_once(model, arm, block, out, live, task)
                except Exception as e:  # a harness fault: record it, keep going
                    failures += 1
                    print(f"[{block} {i}/{len(cells)}] {task} {model} {arm} HARNESS ERROR {e!r}",
                          file=sys.stderr, flush=True)
                    continue
                PROGRESS.mark(f"{block} done {task} {model} {arm}")
                print(_summary(f"{block} {i}/{len(cells)}", r), flush=True)
    finally:
        watch.stop()
    return 1 if failures else 0


def _summary(where: str, r: dict) -> str:
    status = "PASS" if r.get("success") else f"FAIL ({r.get('failure_reason')})"
    steps = f" steps={r.get('steps_passed')}/{r.get('steps_total')}" if r.get("task") else ""
    return (
        f"[{where}] {r.get('task') or 'weblogs-v1'} {r['model_requested']} {r['arm']} {status}"
        f"{steps} turns={r.get('num_turns')} tools={r.get('n_tool_calls')} "
        f"cost=${r.get('total_cost_usd')} {r.get('wall_s')}s "
        f"denials={len(r.get('permission_denials_stream', r.get('permission_denials')) or [])}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--model", help="required unless --blocks")
    ap.add_argument("--arm", choices=list(ARMS), help="required unless --blocks")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--block", default="adhoc")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"study directory (default {_DEFAULT_OUT}; with --live-telemetry {_DEFAULT_LIVE_OUT})",
    )
    s4 = ap.add_argument_group("study 4: the four shared tasks (off by default)")
    s4.add_argument(
        "--task",
        help="a tasks.json task id (weblogs, flights, change, lookup), or 'all' with --blocks",
    )
    s4.add_argument(
        "--blocks",
        nargs=2,
        type=int,
        metavar=("FIRST", "LAST"),
        help="run every (task, model, arm) cell once per block FIRST..LAST, order rotated "
        "by block; needs --task",
    )
    s4.add_argument("--models", default=",".join(STUDY4_MODELS), help="with --blocks")
    s4.add_argument("--arms", default=",".join(STUDY4_ARMS), help="with --blocks")
    s4.add_argument("--skip-done", action="store_true",
                    help="with --blocks: skip cells whose record.json exists (resume)")
    s4.add_argument("--stall-minutes", type=float, default=STALL_MINUTES,
                    help="with --blocks: alert after this long without progress")
    live_g = ap.add_argument_group("live telemetry (off by default)")
    live_g.add_argument(
        "--live-telemetry",
        action="store_true",
        help="emit Claude Code, server and runner telemetry at run time (LIVE_RERUN.md)",
    )
    live_g.add_argument("--dataset", default="mcp-for-kibana-live", help="Honeycomb dataset")
    live_g.add_argument(
        "--telemetry-endpoint",
        default=os.environ.get("HONEYCOMB_OTLP_BASE", "https://api.eu1.honeycomb.io"),
        help="OTLP/HTTP base URL the relay forwards to; 'none' keeps only the local copy",
    )
    live_g.add_argument(
        "--keep-identity",
        action="store_true",
        help="keep Claude Code's user.email/account/organization attributes",
    )
    live_g.add_argument(
        "--key-out-of-model-env",
        action="store_true",
        help="with-mcp and with-mcp-es arms: do not put KIBANA_TEST_API_KEY in the model's "
        "environment (R4)",
    )
    live_g.add_argument(
        "--server-label",
        default=None,
        help="free-text label of the server build, recorded on the root span (R3)",
    )
    live_g.add_argument("--prompt-version", type=int, choices=[1, 2], default=2)
    live_g.add_argument(
        "--with-metrics",
        action="store_true",
        help="also export Claude Code's metrics (they land in the environment's shared "
        "'metrics' dataset, not --dataset)",
    )
    live_g.add_argument(
        "--no-trace-anchor",
        action="store_true",
        help="no-mcp arm: leave out the tool-less MCP server that makes Claude Code's spans "
        "join the run's trace (without it they are orphan traces)",
    )
    live_g.add_argument(
        "--no-agent-hook",
        action="store_true",
        help="leave out the silent hook that emits the agent.* signals per tool call",
    )
    args = ap.parse_args()
    tasks = check_task_args(ap, args)

    live = None
    if args.live_telemetry:
        endpoint = None if args.telemetry_endpoint == "none" else args.telemetry_endpoint
        hc_key = os.environ.get("HONEYCOMB_API_KEY")
        if endpoint and not hc_key:
            raise SystemExit(
                "FAIL: --live-telemetry forwards to Honeycomb but HONEYCOMB_API_KEY is not "
                "set — export it, or pass --telemetry-endpoint none for a local-only run"
            )
        if args.dataset in ("claude-code", "kibana-efficiency", "mcp-for-kibana-study"):
            raise SystemExit(
                f"FAIL: dataset {args.dataset!r} already holds other data — pick a new one "
                "with --dataset"
            )
        live = {
            "dataset": args.dataset,
            "endpoint": endpoint,
            "api_key": hc_key if endpoint else None,
            "keep_identity": args.keep_identity,
            "key_out_of_model_env": args.key_out_of_model_env,
            "server_label": args.server_label,
            "prompt_version": args.prompt_version,
            "agent_hook": not args.no_agent_hook,
            "trace_anchor": not args.no_trace_anchor,
            "metrics": args.with_metrics,
        }
    elif args.key_out_of_model_env or args.server_label or args.keep_identity:
        raise SystemExit("FAIL: these options need --live-telemetry")
    if tasks:
        out = args.out or _DEFAULT_STUDY4_OUT
    else:
        out = args.out or (_DEFAULT_LIVE_OUT if live else _DEFAULT_OUT)

    kibana_url, api_key = load_seed()
    kb = Kibana(kibana_url, api_key)
    if tasks:  # samples and seed: study_tasks owns both
        import study_tasks as st

        st.restore_seed(kb, st.load())
        if args.blocks:
            return run_plan(
                *args.blocks, tasks, args.models.split(","), args.arms.split(","), out, live,
                args.skip_done, args.stall_minutes,
            )
    elif not live:
        ensure_logs_sample(kb)
    elif not sample_data_installed(kb):  # load once, then only check
        status, body = kb.call("POST", "/api/sample_data/logs", {}, internal=True)
        if status not in (200, 201) or not sample_data_installed(kb):
            raise SystemExit(
                f"FAIL: the web-logs sample data is not installed and loading it failed "
                f"({status}: {str(body)[:200]}) — check Kibana with scripts/stack.sh status"
            )
    for i in range(args.runs):
        r = run_once(args.model, args.arm, args.block, out, live, tasks[0] if tasks else None)
        print(
            _summary(f"{i + 1}/{args.runs}", r)
            + (f" telemetry={r.get('otel')}" if live else ""),
            flush=True,
        )
    return 0


def check_task_args(ap: argparse.ArgumentParser, args) -> list[str]:
    """The --task ids to run ([] without --task); argparse errors name the fix."""
    if args.blocks and not args.task:
        ap.error("--blocks needs --task (a task id, or all)")
    if not args.blocks and not (args.model and args.arm):
        ap.error("--model and --arm are required unless --blocks is given")
    if not args.task:
        return []
    import study_tasks as st

    ids = st.task_ids(st.load())
    wanted = ids if args.task == "all" else args.task.split(",")
    unknown = [t for t in wanted if t not in ids]
    if unknown:
        ap.error(f"--task {','.join(unknown)} is not in tasks.json (have: {', '.join(ids)}, all)")
    if not args.blocks and len(wanted) != 1:
        ap.error("without --blocks, --task takes one task id")
    if args.blocks:
        first, last = args.blocks
        if not 1 <= first <= last:
            ap.error("--blocks FIRST LAST needs 1 <= FIRST <= LAST")
        arms = args.arms.split(",")
    else:
        arms = [args.arm]
    bad = [a for a in arms if a not in STUDY4_ARMS]
    if bad:
        ap.error(f"--task runs only the arms {', '.join(STUDY4_ARMS)} (not {', '.join(bad)}): "
                 "the with-mcp-es key reads only the web-logs index")
    return wanted


if __name__ == "__main__":
    raise SystemExit(main())
