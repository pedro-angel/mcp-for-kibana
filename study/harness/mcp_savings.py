#!/usr/bin/env python3
"""MCP savings study: runs of the long task with and without mcp-for-kibana.

    uv run python scripts/experiment/mcp_savings.py setup
    uv run python scripts/experiment/mcp_savings.py prompt with-mcp
    uv run python scripts/experiment/mcp_savings.py plan --phase study
    uv run python scripts/experiment/mcp_savings.py run --model claude-haiku-5-5 --arm with-mcp
    uv run python scripts/experiment/mcp_savings.py worker --worker 3 --phase study --push
    uv run python scripts/experiment/mcp_savings.py probe
    uv run python scripts/experiment/mcp_savings.py rescore --phase pilot

Every value the study holds fixed lives in ``mcp_savings.json``; how to run
it is docs/RUNBOOK.md. One run: the harness gives the model a space id, the model
creates that space and does the six steps inside it, the harness checks
Kibana's state, then deletes the space and everything in it. Anything the
model created outside the space is recorded and deleted too.

``setup`` prepares a worker's machine once: the pinned server in its own
virtual environment, Kibana's pinned API reference, and the sample data.
``worker`` runs that worker's blocks one run at a time, in each block's
seeded order, then re-runs invalid runs; with ``--push`` it commits and
pushes the worker's own folder after every block. Telemetry goes to
Honeycomb through the scrubbing relay of live_telemetry.py; ``--telemetry
none`` keeps it local (a check run).

Honeycomb is the record (dataset ``mcp-savings``): every run sends one root
span ``efficiency.run``, an aborted run included (``study.aborted``);
``rescore`` sends one ``efficiency.rescore`` span per run, keyed by
``study.run_id``, whose checks replace the root span's; ``probe`` sends one
``study.probe`` span. Run folders are a raw backup only.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import gzip
import hashlib
import itertools
import json
import os
import random
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import efficiency_run as er  # noqa: E402  (sibling script)
import study_tasks as st  # noqa: E402

CONFIG = HERE / "mcp_savings.json"
OUT = HERE / "runs" / "mcp-savings"
CACHE = Path.home() / ".cache" / "mcp-savings"
PHASES = ("study", "pilot", "check")
TASK_ARM = {"with-mcp": "server", "no-mcp": "api"}
SERVER = "mcp-for-kibana"
DEFAULT_ENDPOINT = "https://api.eu1.honeycomb.io"

# A run whose agent stopped for one of these reasons failed outside the model:
# it is invalid and runs again (see ``validity``).
_API_FAULT = re.compile(
    r"(?i)(api error|overloaded|rate.?limit|usage limit|limit reached|"
    r"\b429\b|\b5\d\d\b.*error|credit balance|authentication_error)"
)


def load(path: Path = CONFIG) -> dict:
    return json.loads(path.read_text())


def config_sha(path: Path = CONFIG) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


# ---------------------------------------------------------------- plan


def block_name(phase: str, b: int) -> str:
    return f"p{b}" if phase == "pilot" else f"b{b:03d}"


def block_order(cfg: dict, block: str) -> list[tuple[str, str]]:
    """The block's 6 (model, arm) runs in the order drawn from the plan's
    seed: the same block always gets the same order, on any machine."""
    s = cfg["study"]
    cells = list(itertools.product(s["models"], s["arms"]))
    random.Random(f"{s['order_seed']}:{block}").shuffle(cells)
    return cells


def worker_blocks(cfg: dict, phase: str, worker: int) -> list[str]:
    s = cfg["study"]
    if not 1 <= worker <= len(s["workers"]):
        raise SystemExit(f"FAIL: --worker {worker} is not one of 1..{len(s['workers'])}")
    if phase == "pilot":
        n = s["pilot_blocks_per_worker"]
        return [block_name("pilot", (worker - 1) * n + i) for i in range(1, n + 1)]
    lo, hi = s["workers"][worker - 1]
    return [block_name("study", b) for b in range(lo, hi + 1)]


def check_split(cfg: dict) -> None:
    """The workers' ranges cover blocks 1..N exactly once."""
    s = cfg["study"]
    got = [b for lo, hi in s["workers"] for b in range(lo, hi + 1)]
    if sorted(got) != list(range(1, s["blocks"] + 1)):
        raise SystemExit(
            "FAIL: mcp_savings.json workers do not cover blocks 1..blocks exactly once"
        )


# ---------------------------------------------------------------- prompt


def run_names(block: str, model: str, arm: str) -> tuple[str, str]:
    """(marker, space id) of one run; the space id fits Kibana's ^[a-z0-9_-]+$."""
    tag = secrets.token_hex(3)
    short = model.removeprefix("claude-").split("-")[0]
    return f"MS-{tag}", f"ms-{block}-{short}-{arm}-{tag}"


def build_prompt(cfg: dict, arm: str, marker: str, space: str) -> str:
    """The arm's preamble, the task's intro, its numbered steps, its closing."""
    t, ctx = cfg["task"], {"marker": marker, "space": space}
    lines = [cfg["arms"][TASK_ARM[arm]], "", t["intro"].format(**ctx)]
    lines += [f"{i}. {step['text'].format(**ctx)}" for i, step in enumerate(t["steps"], 1)]
    lines += ["", t["closing"].format(**ctx)]
    return "\n".join(lines)


# ---------------------------------------------------------------- Kibana


class SpaceKibana(er.Kibana):
    """The Kibana client with every /api path sent to one space."""

    def __init__(self, url: str, api_key: str, space: str):
        super().__init__(url, api_key)
        self.space = space

    def call(self, method: str, path: str, body: dict | None = None, internal=False):
        if path.startswith("/api/") and not path.startswith("/api/spaces"):
            path = f"/s/{self.space}{path}"
        return super().call(method, path, body, internal)


def space_ids(kb: er.Kibana) -> set[str]:
    status, body = kb.call("GET", "/api/spaces/space")
    return {s["id"] for s in body} if status == 200 and isinstance(body, list) else set()


def delete_space(kb: er.Kibana, space: str) -> int:
    status, _ = kb.call("DELETE", f"/api/spaces/space/{space}")
    return status


def health(kb: er.Kibana) -> tuple[bool, str]:
    """Kibana answers and reports itself available; its version comes back."""
    try:
        status, body = kb.call("GET", "/api/status")
    except Exception as e:  # unreachable: the run cannot start
        return False, repr(e)
    level = (
        ((body or {}).get("status") or {}).get("overall", {}).get("level")
        if status == 200
        else None
    )
    version = ((body or {}).get("version") or {}).get("number")
    return status == 200 and level == "available", f"HTTP {status}, {level}, {version}"


def kibana_version(kb: er.Kibana) -> str | None:
    status, body = kb.call("GET", "/api/status")
    return ((body or {}).get("version") or {}).get("number") if status == 200 else None


# ---------------------------------------------------------------- setup


def server_dir(cfg: dict) -> Path:
    return CACHE / f"server-{cfg['study']['server_version']}"


def server_bin(cfg: dict) -> Path:
    return server_dir(cfg) / "bin" / SERVER


def ensure_server(cfg: dict) -> str:
    """The pinned server from PyPI in its own virtual environment; returns
    the installed version, which must equal the pin."""
    venv = server_dir(cfg)
    if not server_bin(cfg).exists():
        subprocess.run(["uv", "venv", "--quiet", str(venv)], check=True)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--quiet",
                "--python",
                str(venv / "bin" / "python"),
                cfg["study"]["server_package"],
            ],
            check=True,
        )
    got = installed_server_version(cfg)
    if got != cfg["study"]["server_version"]:
        raise SystemExit(
            f"FAIL: {venv} holds {SERVER} {got}, not {cfg['study']['server_version']} — "
            f"delete {venv} and run setup again"
        )
    return got


def installed_server_version(cfg: dict) -> str | None:
    py = server_dir(cfg) / "bin" / "python"
    if not py.exists():
        return None
    proc = subprocess.run(
        [str(py), "-c", f"import importlib.metadata as m; print(m.version('{SERVER}'))"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None


def specs(cfg: dict) -> dict[str, str]:
    """Kibana's API reference for the pinned version, and the Dashboards API
    spec at the commit study 4 pinned (that repository has no version tags)."""
    return {
        "kibana.yaml": (
            f"https://raw.githubusercontent.com/elastic/kibana/v{cfg['study']['kibana_version']}"
            "/oas_docs/output/kibana.yaml"
        ),
        "dashboards-api.yaml": er.SPECS["dashboards-api.yaml"],
    }


def ensure_specs(cfg: dict) -> Path:
    cache = CACHE / f"specs-{cfg['study']['kibana_version']}"
    cache.mkdir(parents=True, exist_ok=True)
    for name, url in specs(cfg).items():
        target = cache / name
        if not target.exists() or target.stat().st_size == 0:
            subprocess.run(["curl", "-fsSL", "-o", str(target), url], check=True)
    return cache


def installed_cli_version() -> str:
    """What `claude --version` prints, such as "2.1.302 (Claude Code)"; ""
    when the CLI is missing or fails."""
    try:
        out = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()


def cli_version_number(text: str) -> str | None:
    """The leading version number of `claude --version`'s output."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)+)", text or "")
    return m.group(1) if m else None


def check_cli_pin(cfg: dict) -> str:
    """Stop unless the installed CLI is the version the study pins
    (study.claude_cli). Returns what `claude --version` printed."""
    pin = cfg["study"]["claude_cli"]
    printed = installed_cli_version()
    have = cli_version_number(printed)
    if have != pin:
        raise SystemExit(
            f"HARNESS: the installed Claude Code CLI is {have or 'missing'} "
            f"({printed or 'claude --version printed nothing'}), the study pins {pin} "
            "(study.claude_cli in mcp_savings.json); no run started"
        )
    return printed


def setup(cfg: dict) -> dict:
    kb = er.Kibana(*er.load_seed())
    ok, detail = health(kb)
    if not ok:
        raise SystemExit(
            f"FAIL: Kibana is not available ({detail}) — scripts/server.sh up && scripts/server.sh seed"
        )
    have = kibana_version(kb)
    if have != cfg["study"]["kibana_version"]:
        raise SystemExit(
            f"FAIL: Kibana is {have}, the study pins {cfg['study']['kibana_version']} — "
            f"ES_LOCAL_VERSION={cfg['study']['kibana_version']} scripts/server.sh up"
        )
    st.ensure_samples(kb, cfg)
    return {
        "server": ensure_server(cfg),
        "specs": str(ensure_specs(cfg)),
        "kibana": have,
    }


# ---------------------------------------------------------------- state


def data_view_for(data_views: list[dict], index: str) -> dict | None:
    """The data view whose pattern covers the index (exact title first)."""
    exact = [dv for dv in data_views if dv.get("title") == index]
    if exact:
        return exact[0]
    return next((dv for dv in data_views if fnmatch.fnmatch(index, dv.get("title") or "")), None)


def _saved(kb: er.Kibana, so_type: str) -> list[dict]:
    status, body = kb.call("GET", f"/api/saved_objects/_find?type={so_type}&per_page=1000")
    return (body or {}).get("saved_objects", []) if status == 200 else []


def attach_library(kb: er.Kibana, dash_id: str, dash: dict) -> None:
    """Give each by-reference panel the library object it shows, under
    ``library``: a panel saved by reference holds only the object's id, while
    the object holds the data view and the fields. The dashboard's saved
    object links the two (reference ``<panel id>:savedObjectRef``)."""
    status, so = kb.call("GET", f"/api/saved_objects/dashboard/{dash_id}")
    refs = (so or {}).get("references", []) if status == 200 else []
    for panel in st._panels(dash):
        ref_id = (panel.get("config") or {}).get("ref_id")
        ref = next(
            (
                r
                for r in refs
                if r.get("name") == f"{panel.get('id')}:savedObjectRef"
                or (ref_id and r.get("id") == ref_id and r.get("type") != "index-pattern")
            ),
            None,
        )
        if not ref:
            continue
        s, obj = kb.call("GET", f"/api/saved_objects/{ref['type']}/{ref['id']}")
        if s == 200:
            panel["library"] = {
                "type": ref["type"],
                "id": ref["id"],
                "title": (obj.get("attributes") or {}).get("title"),
                "attributes": obj.get("attributes"),
                "references": obj.get("references", []),
            }


def collect_state(
    kb: SpaceKibana,
    es: st.Elastic,
    cfg: dict,
    before_default: dict,
    spaces_before: set[str],
    default_kb: er.Kibana,
) -> dict:
    """Everything the checks read, gathered once after the run: the space,
    its data views, dashboards and rules, each rule's filter evaluated on the
    real and the probe index, and what appeared outside the space."""
    space = kb.space
    spaces_after = space_ids(default_kb)
    status, sp = default_kb.call("GET", f"/api/spaces/space/{space}")
    _, dvs = kb.call("GET", "/api/data_views")
    data_views = (dvs or {}).get("data_view", []) if isinstance(dvs, dict) else []
    meta = {o["id"]: o for o in _saved(kb, "dashboard")}
    dashboards = []
    for dash_id in meta:
        s, full = kb.call("GET", f"/api/dashboards/{dash_id}")
        if s == 200:
            full = dict(full)
            attach_library(kb, dash_id, full)
            full["_meta"] = {
                k: meta[dash_id].get(k) for k in ("created_at", "updated_at", "version")
            }
            dashboards.append(full)
    _, rules_body = kb.call("GET", "/api/alerting/rules/_find?per_page=1000")
    rules = []
    for r in (rules_body or {}).get("data", []) if isinstance(rules_body, dict) else []:
        s, rule = kb.call("GET", f"/api/alerting/rule/{r['id']}")
        if s == 200:
            rules.append(rule)
    after_default = default_kb.snapshot()
    outside = {
        "spaces": sorted(spaces_after - spaces_before - {space}),
        "default_objects": sorted(
            map(
                list,
                {tuple(o) for o in after_default["objects"]}
                - {tuple(o) for o in before_default["objects"]},
            )
        ),
        "default_rules": sorted(set(after_default["rules"]) - set(before_default["rules"])),
    }
    state = {
        "space": {
            "id": space,
            "exists": status == 200,
            "name": (sp or {}).get("name") if status == 200 else None,
        },
        "data_views": data_views,
        "dashboards": dashboards,
        "rules": rules,
        "outside": outside,
        "by_data": {},
    }
    for key, d in cfg["data"].items():
        props = st._properties(es, d["index"])
        dv = data_view_for(data_views, d["index"])
        mine = [r for r in rules if any(d["index"] in i for i in st._rule_index(r, data_views))]
        fields = {
            "timestamp",
            "@timestamp",
            "utc_time",
            (dv or {}).get("timeFieldName"),
            *[st.rule_time_field(r, data_views) for r in mine],
        }
        filters = {}
        if mine:
            with st.probe_index(es, cfg, key) as probe:
                for rule in mine:
                    filters[rule["id"]] = st.evaluate_filter(es, cfg, rule, key, probe, data_views)
        state["by_data"][key] = {
            "data_view": dv,
            "time_fields": {f: st.resolve_field(props, f) for f in fields if f},
            "date_fields": sorted(st.date_fields(props)),
            "filters": filters,
        }
    return state


# ---------------------------------------------------------------- scoring


def _named(items: list[dict], name: str, key: str) -> list[dict]:
    if key == "title":
        return [o for o in items if st._norm(st._title(o)) == st._norm(name)]
    return [o for o in items if st._norm(o.get("name")) == st._norm(name)]


def _dashboard_reasons(dash: dict, c: dict, d: dict, data_views: list[dict]) -> list[str]:
    reasons = []
    n = len(st._panels(dash))
    if "panels" in c and n != c["panels"]:
        reasons.append(f"{n} panels, expected {c['panels']}")
    if not st.uses_data(dash, d, data_views):
        reasons.append(f"a panel does not read {d['index']}")
    for word in c.get("panel_mentions", []):
        if word.casefold() not in json.dumps(st._panels(dash)).casefold():
            reasons.append(f"no panel mentions {word!r}")
    return reasons


def score(cfg: dict, marker: str, space: str, state: dict, final_text: str) -> dict:
    """One criterion per check of every step, each with its expected and
    actual value. A step passes when all its checks pass; ``steps_passed``
    counts the leading passed steps."""
    ctx = {"marker": marker, "space": space}
    criteria = []
    # The rule each rule check scored, by name: when several rules share the
    # name, the lookup answers are compared with that same rule, never with
    # whichever one Kibana happens to list first.
    chosen: dict[str, dict] = {}

    def add(step, check, ok, expected, actual, detail=None):
        criteria.append(
            {
                "step": step,
                "check": check,
                "ok": bool(ok),
                "expected": expected,
                "actual": actual,
                "detail": "ok"
                if ok
                else f"{check}: " + (detail or f"expected {expected!r}, got {actual!r}"),
            }
        )

    dvs = state["data_views"]
    for step in cfg["task"]["steps"]:
        sid = step["id"]
        for c in step["checks"]:
            kind = c["kind"]
            d = cfg["data"].get(c.get("data", ""), {})
            slice_ = state["by_data"].get(c.get("data", ""), {})
            if kind == "space":
                add(
                    sid,
                    "space",
                    state["space"]["exists"],
                    space,
                    state["space"]["id"] if state["space"]["exists"] else None,
                    f"no space {space!r}",
                )
            elif kind == "data_view":
                dv = slice_.get("data_view")
                tf = (dv or {}).get("timeFieldName")
                ok = bool(dv) and tf in slice_.get("date_fields", [])
                add(
                    sid,
                    f"data_view:{c['data']}",
                    ok,
                    f"a data view on {d['index']} with a date time field",
                    None if not dv else {"title": dv.get("title"), "time_field": tf},
                    f"no data view on {d['index']} in the space"
                    if not dv
                    else f"time field {tf!r} is not a date field",
                )
            elif kind == "answer":
                got = st.answers(final_text, c["line"])
                want = _expected_answer(c, ctx, state, chosen)
                add(
                    sid,
                    f"answer:{c['line']}",
                    _answer_ok(c["expect"], want, got),
                    want,
                    got,
                    None if got is not None else f"no {c['line']}: line in the reply",
                )
            elif kind == "dashboard":
                title = c["title"].format(**ctx)
                cands = _named(state["dashboards"], title, "title")
                if not cands:
                    add(
                        sid,
                        f"dashboard:{c['data']}",
                        False,
                        title,
                        [st._title(x) for x in state["dashboards"]],
                        f"no dashboard titled {title!r} in the space",
                    )
                    continue
                why = _dashboard_reasons(cands[0], c, d, dvs)
                want = (
                    f"{c['panels']} panels on {d['index']}"
                    if "panels" in c
                    else f"panels on {d['index']}"
                )
                add(
                    sid,
                    f"dashboard:{c['data']}",
                    not why,
                    want,
                    st.panel_titles(cands[0]),
                    "; ".join(why),
                )
            elif kind == "rule":
                name = c["name"].format(**ctx)
                cands = _named(state["rules"], name, "name")
                if not cands:
                    add(
                        sid,
                        f"rule:{c['data']}",
                        False,
                        name,
                        [r.get("name") for r in state["rules"]],
                        f"no rule named {name!r} in the space",
                    )
                    continue
                tf = (slice_.get("data_view") or {}).get("timeFieldName")
                rstate = {
                    "data_views": dvs,
                    "time_fields": slice_.get("time_fields", {}),
                    "filters": slice_.get("filters", {}),
                }
                results = [st.rule_checks({**c, "kind": "new_rule"}, r, rstate, tf) for r in cands]
                i = max(range(len(results)), key=lambda k: sum(ok for _, ok, _, _ in results[k]))
                chosen[st._norm(name)] = cands[i]
                for check, ok, want, got in results[i]:
                    add(sid, f"rule:{c['data']}.{check}", ok, want, got)
            elif kind == "dashboard_changed":
                title = c["title"].format(**ctx)
                cands = _named(state["dashboards"], title, "title")
                add(
                    sid,
                    "dashboard.one",
                    len(cands) == 1,
                    f"one dashboard titled {title!r}",
                    len(cands),
                )
                if not cands:
                    continue
                dash = cands[0]
                why = _dashboard_reasons(dash, c, d, dvs)
                add(
                    sid,
                    "dashboard.panels",
                    not why,
                    f"{c['panels']} panels, one by host",
                    st.panel_titles(dash),
                    "; ".join(why),
                )
                titles = st.panel_titles(dash)
                add(
                    sid,
                    "dashboard.panel_title",
                    any(st._norm(x) == st._norm(c["panel_title"]) for x in titles),
                    c["panel_title"],
                    titles,
                )
                m = dash.get("_meta") or {}
                add(
                    sid,
                    "dashboard.in_place",
                    bool(m.get("updated_at")) and m.get("updated_at") != m.get("created_at"),
                    "updated after it was created",
                    {"created_at": m.get("created_at"), "updated_at": m.get("updated_at")},
                    "the dashboard was never updated after its creation: it was rebuilt, not changed",
                )
            else:
                raise SystemExit(f"FAIL: unknown check kind {kind!r} in mcp_savings.json")

    passed = 0
    for step in cfg["task"]["steps"]:
        if all(c["ok"] for c in criteria if c["step"] == step["id"]):
            passed += 1
        else:
            break
    failed = [f"{c['step']}: {c['detail']}" for c in criteria if not c["ok"]]
    out = state["outside"]
    return {
        "success": not failed,
        "failure_reason": "; ".join(failed) or None,
        "first_failed_step": next((c["step"] for c in criteria if not c["ok"]), None),
        "steps_passed": passed,
        "steps_total": len(cfg["task"]["steps"]),
        "criteria": criteria,
        "n_dashboards": len(state["dashboards"]),
        "n_rules": len(state["rules"]),
        "n_data_views": len(state["data_views"]),
        "n_outside": len(out["spaces"]) + len(out["default_objects"]) + len(out["default_rules"]),
        "outside": out,
    }


def _expected_answer(c: dict, ctx: dict, state: dict, chosen: dict | None = None):
    expect = c["expect"]
    if expect == "time_field":
        return (state["by_data"][c["data"]].get("data_view") or {}).get("timeFieldName")
    if expect == "panels":
        ds = _named(state["dashboards"], c["dashboard"].format(**ctx), "title")
        return len(st._panels(ds[0])) if ds else None
    name = c["rule"].format(**ctx)
    rs = _named(state["rules"], name, "name")
    if not rs:
        return None
    rs = [(chosen or {}).get(st._norm(name)) or rs[0]]
    if expect == "rule_name":
        return rs[0].get("name")
    if expect == "rule_interval":
        return (rs[0].get("schedule") or {}).get("interval")
    if expect == "rule_enabled":
        return st._yes(rs[0].get("enabled"))
    raise SystemExit(f"FAIL: unknown answer expectation {expect!r} in mcp_savings.json")


def _answer_ok(expect: str, want, got: str | None) -> bool:
    mapped = {
        "panels": "seeded_panels",
        "rule_interval": "seeded_interval",
        "rule_enabled": "rule_enabled",
    }.get(expect, expect)
    return st._answer_ok(mapped, want, got)


def span_attributes(outcome: dict, extra: dict) -> dict:
    """The checks' study.* attributes (study_tasks.span_attributes) and the
    outcome's success tally, under the names the root span and the rescore
    span share, plus ``extra`` as study.<key>."""
    attrs = st.span_attributes({**outcome, "task": "mcp-savings"})
    attrs.update({f"study.{k}": outcome[k] for k in SCORE_KEYS if outcome.get(k) is not None})
    attrs.update({f"study.{k}": v for k, v in extra.items() if v is not None})
    return attrs


# The run's identity and what produced it, on every span a run sends (its
# root, an aborted run's root). server_version is the pinned version under
# test, on both setups.
RUN_KEYS = ("worker", "phase", "order", "attempt", "space", "config_sha")
HARNESS_KEYS = ("harness_sha", "harness_dirty", "git_sha")


def run_extras(cfg: dict, record: dict) -> dict:
    extras = {f"study.{k}": record.get(k) for k in (*RUN_KEYS, *HARNESS_KEYS)}
    extras["study.server_version"] = cfg["study"]["server_version"]
    return {k: v for k, v in extras.items() if v is not None and v != ""}


# ---------------------------------------------------------------- validity


def validity(
    parsed: dict, timed_out: bool, healthy: bool, harness_error: str | None
) -> tuple[bool, str | None]:
    """(valid, reason). Invalid only through a fault outside the model:
    an unhealthy stack, a runner exception, or an agent that ended without a
    result event or on an API error, an overload or a usage-limit stop. A run
    cut at the time limit is a valid failure."""
    if not healthy:
        return False, "stack unhealthy before the run"
    if harness_error:
        return False, f"runner exception: {harness_error}"
    if timed_out:
        return True, None
    if not parsed.get("n_result_events"):
        return False, "no result event"
    if parsed.get("is_error") and _API_FAULT.search(str(parsed.get("final_text") or "")):
        return False, f"API stop: {str(parsed.get('final_text'))[:120]}"
    return True, None


def is_usage_stop(reason: str | None) -> bool:
    return bool(reason) and bool(
        re.search(r"(?i)usage limit|limit reached|rate.?limit|\b429\b", reason)
    )


# ---------------------------------------------------------------- one run


def mcp_entry(cfg: dict, kibana_url: str, api_key: str, otel_env: dict | None) -> dict:
    s = cfg["study"]
    return {
        "command": str(server_bin(cfg)),
        "args": [],
        "env": {
            "KIBANA_URL": kibana_url,
            "KIBANA_API_KEY": api_key,
            "KIBANA_MCP_TOOLBOXES": s["toolboxes"],
            "KIBANA_MCP_TIER": s["tier"],
            **(otel_env or {}),
        },
    }


def git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


# What a run executes: the experiment scripts and their config, not the run
# folders (a worker's own commits move HEAD but change none of these).
HARNESS_PATHS = (":(glob)scripts/experiment/*.py", ":(glob)scripts/experiment/*.json")


def harness_sha() -> tuple[str, bool]:
    """(the last commit that changed the harness, whether the tree holds
    uncommitted changes to it). Every study run must name the frozen commit
    with no local changes."""

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(HERE.parents[1]), *args],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    sha = git("log", "-1", "--format=%h", "--", *HARNESS_PATHS)
    return sha, bool(git("status", "--porcelain", "--", *HARNESS_PATHS))


STARTED = "started.json"  # in a run folder until its record.json is written
STARTED_KEYS = (
    "run_id", "phase", "worker", "block", "order", "attempt", "model_requested", "arm",
    "marker", "space", "config_sha", "git_sha", "harness_sha", "harness_dirty", "ts_start",
)  # fmt: skip


def send_spans(telemetry: dict, dataset: str, body: dict, agent_name: str | None = None) -> dict:
    """Send a runner-built OTLP/JSON traces body to Honeycomb through the
    same scrubbing relay a run uses (the telemetry key, and the stack's
    Kibana key when this machine has one, are removed from every string).
    Returns the relay's stats; with telemetry 'none' nothing leaves."""
    import live_telemetry as lt

    known = ""
    with contextlib.suppress(Exception):  # no stack here (e.g. rescoring elsewhere)
        known = er.load_seed()[1]
    with tempfile.TemporaryDirectory(prefix="ms-send-") as d:
        relay = lt.Relay(
            Path(d) / "telemetry.jsonl",
            lt.Scrubber([known, telemetry.get("api_key") or ""], drop_keys=lt.IDENTITY_ATTRS),
            upstream=telemetry.get("endpoint"),
            api_key=telemetry.get("api_key"),
            dataset=dataset,
            agent_name=agent_name,
            known=known or None,
        ).start()
        relay.post_json("/v1/traces", body)
        relay.stop()
        return {**relay.stats, "secrets_redacted": relay.scrubber.replaced}


def span_body(dataset: str, component: str, spans: list[dict], resource: dict | None = None):
    """One OTLP/JSON traces body with service.name = the dataset."""
    import live_telemetry as lt

    res = {"service.name": dataset, **(resource or {}), "study.component": component}
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [lt._attr(k, v) for k, v in res.items()]},
                "scopeSpans": [{"scope": {"name": "mcp_savings"}, "spans": spans}],
            }
        ]
    }


def one_span(name: str, start: float, end: float, attrs: dict, ok: bool | None = None) -> dict:
    """A root span of its own new trace; ``attrs`` without None values."""
    import live_telemetry as lt

    trace_id, span_id = lt.new_trace()
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(int(start * 1e9)),
        "endTimeUnixNano": str(int(end * 1e9)),
        "attributes": [lt._attr(k, v) for k, v in attrs.items() if v is not None],
    }
    if ok is not None:
        span["status"] = {"code": 1 if ok else 2}
    return span


def worker_dir(phase: str, worker: int) -> Path:
    """The worker's own folder: runs/mcp-savings/<phase>/s<N>/. No two
    workers write to the same folder, so their pushes never conflict."""
    return OUT / phase / f"s{worker}"


def run_one(
    cfg: dict,
    model: str,
    arm: str,
    block: str,
    phase: str,
    worker: int,
    order: int,
    telemetry: dict,
    attempt: int = 1,
) -> dict:
    """One run of the long task. Writes record.json, outcome.json and
    transcript.jsonl.gz under <worker dir>/<block>/<run id> and returns the
    record."""
    import live_telemetry as lt

    s = cfg["study"]
    kibana_url, api_key = er.load_seed()
    kb = er.Kibana(kibana_url, api_key)
    marker, space = run_names(block, model, arm)
    prompt = build_prompt(cfg, arm, marker, space)
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{block}-{model}-{arm}-{marker[3:]}"
    run_dir = worker_dir(phase, worker) / block / run_id
    run_dir.mkdir(parents=True)
    healthy, health_detail = health(kb)
    record = {
        "run_id": run_id,
        "phase": phase,
        "worker": worker,
        "block": block,
        "order": order,
        "attempt": attempt,
        "model_requested": model,
        "arm": arm,
        "marker": marker,
        "space": space,
        "prompt": prompt,
        "config_sha": config_sha(),
        "git_sha": git_sha(),
        **dict(zip(("harness_sha", "harness_dirty"), harness_sha(), strict=True)),
        "kibana_version": kibana_version(kb) if healthy else None,
        "server_version": installed_server_version(cfg) if arm == "with-mcp" else None,
        "toolboxes": s["toolboxes"] if arm == "with-mcp" else None,
        "claude_cli": installed_cli_version(),
        "key_in_model_env": arm == "no-mcp",
        "ts_start": datetime.now(UTC).isoformat(),
        "health": health_detail,
    }
    spaces_before = space_ids(kb) if healthy else set()
    before_default = kb.snapshot() if healthy else {"objects": [], "rules": [], "connectors": []}
    phases: dict[str, tuple[float, float]] = {}
    parsed, timed_out, harness_error, study_attrs, stderr = {}, False, None, None, ""
    trace_id, root_id = lt.new_trace()
    attrs = lt.resource_attributes(run_id, model, arm, block)
    attrs.update({"study.task": "mcp-savings", "study.worker": str(worker), "study.phase": phase})
    # What an aborted run's span needs if the worker dies before record.json
    # is written (record_aborted); no prompt, no secret.
    write_clean(
        run_dir / STARTED,
        json.dumps(
            {
                **{k: record.get(k) for k in STARTED_KEYS},
                "trace_id": trace_id,
                "root_id": root_id,
                "t0": time.time(),
            },
            indent=2,
        ),
    )
    with tempfile.TemporaryDirectory(prefix="ms-") as tmpdir:
        tmp = Path(tmpdir)
        scrubber = lt.Scrubber(
            [api_key, telemetry.get("api_key") or ""], drop_keys=lt.IDENTITY_ATTRS
        )
        relay = lt.Relay(
            run_dir / "telemetry.jsonl",  # read only if the run is aborted
            scrubber,
            upstream=telemetry.get("endpoint"),
            api_key=telemetry.get("api_key"),
            dataset=s["dataset"],
            agent_name=f"claude-code {arm}",
            known=api_key,
        ).start()
        record["trace_id"] = trace_id
        t0 = time.time()
        try:
            if not healthy:
                raise RuntimeError(f"stack unhealthy: {record['health']}")
            workdir = tmp / "work"
            (workdir / "kibana-api").mkdir(parents=True)
            spec_dir = ensure_specs(cfg)
            for name in specs(cfg):
                shutil.copy(spec_dir / name, workdir / "kibana-api" / name)
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
                er.BUILTIN_TOOLS,
                "--strict-mcp-config",
                "--no-session-persistence",
                "--permission-mode",
                "dontAsk",
                "--allowedTools",
                *er.ALLOWED_TOOLS,
            ]
            hook_cfg = er.write_hook_config(tmp, relay.url, s["dataset"], attrs, api_key, workdir)
            cmd += ["--settings", json.dumps(er.hook_settings(hook_cfg))]
            mcp_path = tmp / "mcp.json"
            if arm == "with-mcp":
                servers = {
                    SERVER: mcp_entry(
                        cfg, kibana_url, api_key, lt.server_env(relay.url, s["dataset"], attrs)
                    )
                }
                cmd[cmd.index("--allowedTools") + 1 : cmd.index("--allowedTools") + 1] = [
                    f"mcp__{SERVER}"
                ]
            else:  # the tool-less anchor makes Claude Code's spans join the run's trace
                servers = {er.TRACE_ANCHOR_SERVER: er.trace_anchor_server()}
            mcp_path.write_text(json.dumps({"mcpServers": servers}))
            mcp_path.chmod(0o600)
            cmd += ["--mcp-config", str(mcp_path)]
            env = child_env(os.environ, tmp / "home", s["cli_env"])
            env.update({"KIBANA_URL": kibana_url, "TMPDIR": er.task_tmpdir(workdir)})
            if arm == "no-mcp":
                env["KIBANA_TEST_API_KEY"] = api_key
            env = lt.claude_env(
                env, relay.url, s["dataset"], attrs, lt.traceparent(trace_id, root_id)
            )
            record["child_env"] = sorted(env)  # names only: values hold keys
            agent = run_agent(cmd, workdir, env, run_dir / "transcript.jsonl", s["run_timeout_s"])
            stdout, stderr, timed_out = agent["stdout"], agent["stderr"], agent["timed_out"]
            record.update({k: agent[k] for k in ("wall_s", "cli_exit_s", "killed_after_result")})
            phases["agent"] = (t0, time.time())
            lines = up_to_first_result(stdout)
            parsed = er.parse_stream(lines, first_result=True)
            record["first_request_usage"] = first_request_usage(lines)
            parsed.pop("events")
            record.update(parsed)
            record["n_permission_denials"] = len(parsed["permission_denials_stream"])
            record["agent_live"] = er.read_hook_rollups(hook_cfg, api_key, run_dir)
            t1 = time.time()
            # Scored at the result: seconds from the model's result event to
            # the start of scoring (the CLI's exit or kill, then parsing).
            record["score_started_s"] = score_started_s(agent.get("result_ts"), t1)
            skb = SpaceKibana(kibana_url, api_key, space)
            state = collect_state(
                skb, st.Elastic.from_stack(), cfg, before_default, spaces_before, kb
            )
            outcome = score(cfg, marker, space, state, parsed.get("final_text") or "")
            with gzip.open(run_dir / "state.json.gz", "wt", compresslevel=9) as f:
                f.write(redact(json.dumps(state, default=str), api_key)[0])
            write_clean(run_dir / "outcome.json", json.dumps(outcome, indent=2, default=str))
            record.update(
                {
                    k: outcome[k]
                    for k in (
                        "success",
                        "failure_reason",
                        "first_failed_step",
                        "steps_passed",
                        "steps_total",
                        "n_dashboards",
                        "n_rules",
                        "n_data_views",
                        "n_outside",
                    )
                }
            )
            record["checks_total"] = len(outcome["criteria"])
            record["checks_passed"] = sum(c["ok"] for c in outcome["criteria"])
            study_attrs = span_attributes(outcome, {})
            phases["score"] = (t1, time.time())
        except Exception as e:  # a harness fault is data, never a lost record
            harness_error = repr(e)
            record["harness_error"] = harness_error
            record.setdefault("success", False)
        record["timed_out"] = timed_out
        record["valid"], record["invalid_reason"] = validity(
            parsed, timed_out, healthy, harness_error
        )
        t2 = time.time()
        record["cleanup"] = cleanup(kb, space, before_default, spaces_before) if healthy else None
        phases["reset"] = (t2, time.time())
        phases.setdefault("agent", (t0, t0))
        record["ts_end"] = datetime.now(UTC).isoformat()
        try:
            record["otel"] = er.finish_live(
                record,
                {"trace_id": trace_id, "root_id": root_id, "attrs": attrs, "relay": relay},
                phases,
                s["dataset"],
                root_extras(cfg, record, study_attrs),
            )
        except Exception as e:
            record["otel"] = f"failed: {e!r}"
    transcript = run_dir / "transcript.jsonl"
    if transcript.exists():
        record["key_in_transcript"] = save_transcript(transcript, api_key)
    if stderr.strip():
        write_clean(run_dir / "stderr.txt", redact(stderr[-20000:], api_key)[0])
    write_clean(run_dir / "record.json", json.dumps(record, indent=2, default=str))
    (run_dir / "telemetry.jsonl").unlink(missing_ok=True)
    (run_dir / STARTED).unlink(missing_ok=True)
    return record


def root_extras(cfg: dict, record: dict, study_attrs: dict | None) -> dict:
    """The study.* attributes a finished run adds to its root span: its
    identity, the harness and server that produced it, the checks (absent
    when the harness failed before scoring), and its validity. Every run's
    span carries study.valid and study.aborted."""
    return {
        **run_extras(cfg, record),
        **(study_attrs or {}),
        "study.success": bool(record.get("success")),
        "study.aborted": False,
        "study.valid": bool(record.get("valid")),
        "study.invalid_reason": record.get("invalid_reason"),
    }


# The only variables the measured CLI inherits: the runner's own session
# (a Claude Code cloud session) sets many CLAUDE_* variables that change the
# CLI it starts (remote mode, effort, background shells, side calls), and its
# HOME holds user settings and hooks. The proxy and CA variables are how this
# machine reaches the API; nothing else is needed.
CHILD_ENV_KEEP = (
    "PATH",
    "TERM",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def child_env(parent: Mapping[str, str], home: Path, pinned: Mapping[str, str]) -> dict[str, str]:
    """The measured CLI's environment: CHILD_ENV_KEEP from the parent, a
    fresh, empty HOME, so no user setting, hook or session variable reaches
    the run, and the study's pinned CLI settings (cli_env: the effort level;
    the five-minute cache lifetime API-key users get, which list prices
    describe, where on a subscription the CLI would write for an hour; and no
    self-update, so the pinned CLI version holds through a run)."""
    home.mkdir(parents=True, exist_ok=True)
    env = {k: parent[k] for k in CHILD_ENV_KEEP if k in parent}
    env["HOME"] = str(home)
    env.update(pinned)
    return env


def first_request_usage(lines: list[str]) -> dict | None:
    """The token usage of the run's first model request (its first assistant
    event): how much of the prompt it read from the cache and how much it
    wrote. Its input and cache counts are exact; its output count is a
    snapshot taken mid-reply and is not used."""
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError):
            e = json.loads(line)
            if e.get("type") == "assistant":
                u = (e.get("message") or {}).get("usage") or {}
                return {
                    "input_tokens": u.get("input_tokens"),
                    "cache_read_input_tokens": u.get("cache_read_input_tokens"),
                    "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
                    "cache_creation": u.get("cache_creation"),
                }
    return None


EXIT_GRACE_S = 15  # how long the CLI may take to exit after its result


def _is_result(line: str) -> bool:
    if '"type":"result"' not in line:
        return False
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(line).get("type") == "result"
    return False


class _ResultWatch:
    """Reads the growing transcript's complete lines and says once a result
    event has been written."""

    def __init__(self, transcript: Path):
        self.path, self.offset, self.partial = transcript, 0, ""

    def seen(self) -> bool:
        with self.path.open(errors="replace") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()
        *lines, self.partial = (self.partial + chunk).split("\n")
        return any(_is_result(line) for line in lines)


def _kill_group(proc: subprocess.Popen) -> None:
    """End the CLI and everything it started (shells the model left in the
    background), so nothing acts on Kibana once the run is over."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def run_agent(cmd: list[str], workdir: Path, env: dict, transcript: Path, timeout: float) -> dict:
    """Run the CLI with its stream written to ``transcript``; the run ends at
    the first result event (the model's end of turn). wall_s is the time to
    that event: a CLI still waiting afterwards (e.g. on a background shell)
    is given EXIT_GRACE_S and then killed with its whole process group, which
    is killed in every case once the CLI is done."""
    err_path = transcript.with_name("stderr.raw")  # unredacted: never committed
    er.PROGRESS.mark(f"agent {transcript.parent.name}", transcript)
    started = time.monotonic()
    result_at = None
    timed_out = killed = False
    with transcript.open("w") as out, err_path.open("w") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=workdir,
            env=env,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        watch = _ResultWatch(transcript)
        while proc.poll() is None:
            now = time.monotonic()
            if result_at is None and watch.seen():
                result_at = now
            if result_at is not None and now - result_at > EXIT_GRACE_S:
                killed = True
                break
            if result_at is None and now - started > timeout:
                timed_out = True
                break
            time.sleep(0.2)
        ended = time.monotonic()
        out.flush()
        if result_at is None and not timed_out and (watch.seen() or _is_result(watch.partial)):
            result_at = ended
        _kill_group(proc)
    er.PROGRESS.mark(f"scoring {transcript.parent.name}")
    stderr = err_path.read_text(errors="replace")
    err_path.unlink()
    since_result = time.monotonic() - result_at if result_at is not None else None
    return {
        "stdout": transcript.read_text(errors="replace"),
        "stderr": stderr,
        "timed_out": timed_out,
        "wall_s": round((result_at or ended) - started, 1),
        "cli_exit_s": round(ended - result_at, 1) if result_at is not None else None,
        "killed_after_result": killed,
        # epoch seconds of the result event: what score_started_s counts from
        "result_ts": time.time() - since_result if since_result is not None else None,
    }


def score_started_s(result_ts: float | None, score_start: float) -> float | None:
    """Seconds from the result event (epoch) to the start of scoring; None
    for a run without a result (timed out)."""
    return round(score_start - result_ts, 1) if result_ts is not None else None


def up_to_first_result(stdout: str) -> list[str]:
    """The stream's lines up to and including the first result event: every
    measure of a run (cost, time, tool calls) stops where the model ended its
    turn."""
    lines = []
    for line in stdout.splitlines():
        lines.append(line)
        if _is_result(line):
            break
    return lines


def write_clean(path: Path, text: str) -> None:
    """Write the text as the repo's file hooks want it: no trailing
    whitespace, one final newline. A file the hooks rewrite aborts the
    worker's commit."""
    path.write_text("\n".join(line.rstrip() for line in text.splitlines()) + "\n")


def redact(text: str, secret: str) -> tuple[str, bool]:
    """The text with the Kibana key replaced, and whether it was there."""
    if not secret or secret not in text:
        return text, False
    return text.replace(secret, "[REDACTED]"), True


def save_transcript(path: Path, secret: str) -> bool:
    """Gzip the transcript with the Kibana key removed (a model without the
    server can print its environment); returns whether the key was in it."""
    text, found = redact(path.read_text(errors="replace"), secret)
    with gzip.open(str(path) + ".gz", "wt", compresslevel=9) as dst:
        dst.write(text)
    path.unlink()
    return found


def cleanup(kb: er.Kibana, space: str, before_default: dict, spaces_before: set[str]) -> dict:
    """Delete the run's space and everything in it, any other space the run
    created, and whatever the run left in the default space."""
    result = {"space_status": delete_space(kb, space), "other_spaces": {}}
    for other in sorted(space_ids(kb) - spaces_before):
        result["other_spaces"][other] = delete_space(kb, other)
    result["default_removed"] = kb.reset_to(before_default)
    result["space_gone"] = space not in space_ids(kb)
    return result


# ---------------------------------------------------------------- worker


def done_runs(out: Path, block: str) -> dict[tuple[str, str], list[dict]]:
    found: dict[tuple[str, str], list[dict]] = {}
    for rec in sorted((out / block).glob("*/record.json")):
        with contextlib.suppress(ValueError, KeyError, OSError):
            r = json.loads(rec.read_text())
            found.setdefault((r["model_requested"], r["arm"]), []).append(r)
    return found


RUN_DIR = re.compile(
    r"^(?P<ts>\d{8}T\d{6}Z)-(?P<block>[a-z]\d+)-(?P<model>.+)-(?P<arm>with-mcp|no-mcp)-[0-9a-f]{6}$"
)


def _otlp_value(v: dict):
    for k in ("doubleValue", "intValue", "stringValue"):
        if k in v:
            return v[k]
    return None


# Claude Code's api_request event attributes -> the record's token names.
USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_creation_tokens": "cache_creation_input_tokens",
}


def telemetry_usage(tee: Path) -> dict | None:
    """What the relay's copy of a run's telemetry says the run used: the
    list-price cost and token counts summed over every api_request log
    record Claude Code exported (one per model call), the number of those
    calls, and the time of the last request the relay saw; None without a
    copy."""
    if not tee.exists():
        return None
    out = {"total_cost_usd": 0.0, "api_requests": 0, **dict.fromkeys(USAGE_KEYS.values(), 0)}
    last = None
    for line in tee.read_text(errors="replace").splitlines():
        with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError, AttributeError):
            req = json.loads(line)
            if isinstance(req.get("ts"), int | float):
                last = max(last or 0.0, float(req["ts"]))
            if req.get("path") != "/v1/logs":
                continue
            for rl in (req.get("body") or {}).get("resourceLogs", []):
                for sl in rl.get("scopeLogs", []):
                    for rec in sl.get("logRecords", []):
                        a = {
                            x["key"]: _otlp_value(x.get("value", {}))
                            for x in rec.get("attributes", [])
                        }
                        if a.get("event.name") != "api_request":
                            continue
                        out["api_requests"] += 1
                        if a.get("cost_usd") is not None:
                            out["total_cost_usd"] += float(a["cost_usd"])
                        for src, dst in USAGE_KEYS.items():
                            if a.get(src) is not None:
                                out[dst] += int(float(a[src]))
    out["total_cost_usd"] = round(out["total_cost_usd"], 6)
    out["last_ts"] = last
    return out


def telemetry_cost(tee: Path) -> tuple[float, int] | None:
    """(list-price cost, API requests) of telemetry_usage."""
    u = telemetry_usage(tee)
    return (u["total_cost_usd"], u["api_requests"]) if u else None


ABORTED = "the worker stopped before the run's record was written"


def record_aborted(
    wdir: Path,
    cfg: dict | None = None,
    telemetry: dict | None = None,
    phase: str | None = None,
    worker: int | None = None,
) -> list[dict]:
    """Write aborted.json into every run folder that has no record: a run the
    worker never finished (its container restarted mid-run). The CLI reports
    cost only in its result, which such a run never wrote; its cost and
    tokens are read from the relay's copy of its telemetry (one api_request
    event per model call), and the tool calls from its stream. With
    ``telemetry`` the run also sends its root span efficiency.run
    (study.aborted=true, study.valid=false, what it used so far), in the
    run's own trace when started.json names it. Its cell is run again."""
    found = []
    for run_dir in sorted(p for p in wdir.glob("*/*") if p.is_dir()):
        m = RUN_DIR.match(run_dir.name)
        if not m or (run_dir / "record.json").exists() or (run_dir / "aborted.json").exists():
            continue
        started: dict = {}
        with contextlib.suppress(OSError, ValueError):
            started = json.loads((run_dir / STARTED).read_text())
        tool_calls = 0
        transcript = run_dir / "transcript.jsonl"
        if transcript.exists():
            for line in transcript.read_text(errors="replace").splitlines():
                with contextlib.suppress(json.JSONDecodeError):
                    e = json.loads(line)
                    if e.get("type") != "assistant":
                        continue
                    msg = e.get("message") or {}
                    tool_calls += sum(
                        1
                        for b in msg.get("content", [])
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    )
        entry = {
            "run_id": run_dir.name,
            "block": m["block"],
            "model_requested": m["model"],
            "arm": m["arm"],
            "aborted": ABORTED,
            "n_tool_calls": tool_calls,
        }
        for k in STARTED_KEYS:
            if started.get(k) is not None and k not in entry:
                entry[k] = started[k]
        if phase is not None:
            entry.setdefault("phase", phase)
        if worker is not None:
            entry.setdefault("worker", worker)
        usage = telemetry_usage(run_dir / "telemetry.jsonl")
        entry["total_cost_usd"] = usage["total_cost_usd"] if usage else None
        entry["api_requests"] = usage["api_requests"] if usage else None
        if usage:
            entry.update({k: usage[k] for k in USAGE_KEYS.values()})
        t0 = started.get("t0")
        if not isinstance(t0, int | float):
            t0 = datetime.strptime(m["ts"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp()
        ends = [(usage or {}).get("last_ts")]
        if transcript.exists():
            ends.append(transcript.stat().st_mtime)
        t_end = max([t for t in ends if t] + [t0])
        entry["wall_s"] = round(t_end - t0, 1)
        if telemetry is not None and cfg is not None:
            try:
                body = aborted_span(cfg, entry, started, t0, t_end)
                stats = send_spans(
                    telemetry, cfg["study"]["dataset"], body, agent_name=f"claude-code {m['arm']}"
                )
                entry["otel"] = {k: stats.get(k) for k in ("requests", "forwarded", "failed")}
            except Exception as e:  # the record stays; the span is lost
                entry["otel"] = f"failed: {e!r}"
        write_clean(run_dir / "aborted.json", json.dumps(entry, indent=2))
        found.append(entry)
    return found


def aborted_span(cfg: dict, entry: dict, started: dict, t0: float, t_end: float) -> dict:
    """The root span efficiency.run of an aborted run: the attributes a
    finished run's root carries, as far as they are known, with
    study.aborted=true, study.valid=false and study.success=false."""
    import live_telemetry as lt

    record = {
        **{k: entry.get(k) for k in ("run_id", "block", "arm", "model_requested", "marker")},
        **{k: entry.get(k) for k in ("n_tool_calls", "total_cost_usd", "wall_s")},
        **{k: entry.get(k) for k in USAGE_KEYS.values()},
        "success": False,
        "valid": False,
    }
    attrs = lt.resource_attributes(
        entry["run_id"], entry["model_requested"], entry["arm"], entry["block"]
    )
    attrs["study.task"] = "mcp-savings"
    if entry.get("worker") is not None:
        attrs["study.worker"] = str(entry["worker"])
    if entry.get("phase"):
        attrs["study.phase"] = entry["phase"]
    extra = {
        **run_extras(cfg, entry),
        "study.success": False,
        "study.aborted": True,
        "study.valid": False,
        "study.invalid_reason": f"aborted: {ABORTED}",
        "study.api_requests": entry.get("api_requests"),
    }
    trace_id, root_id = started.get("trace_id"), started.get("root_id")
    if not (trace_id and root_id):
        trace_id, root_id = lt.new_trace()
    return lt.runner_spans(
        record, trace_id, root_id, {"agent": (t0, t_end)}, cfg["study"]["dataset"], attrs, extra
    )


def summary(r: dict) -> str:
    status = "PASS" if r.get("success") else f"FAIL ({r.get('first_failed_step')})"
    valid = "" if r.get("valid") else f" INVALID ({r.get('invalid_reason')})"
    return (
        f"[{r['block']} #{r['order']}] {r['model_requested']} {r['arm']} {status}{valid} "
        f"steps={r.get('steps_passed')}/{r.get('steps_total')} tools={r.get('n_tool_calls')} "
        f"cost=${r.get('total_cost_usd')} {r.get('wall_s')}s outside={r.get('n_outside')}"
    )


# What a run folder pushes to branch study-runs, the raw backup that lets a
# worker in a new container skip the runs already recorded. Only these
# names, each written redacted. Never the raw stream (transcript.jsonl) or
# stderr.raw, which an aborted run leaves unredacted, nor the relay's
# telemetry copy or started.json.
BACKUP_FILES = frozenset(
    {
        "record.json",
        "outcome.json",
        "aborted.json",
        "state.json.gz",
        "transcript.jsonl.gz",
        "stderr.txt",
        "hook_errors.txt",
        "stalls.jsonl",
    }
)
_SECRET_NAME = re.compile(r"(?i)(key|token|secret|password|passwd|credential)")
MIN_SECRET_LEN = 8


def _looks_secret(value: str) -> bool:
    """A variable's value that reads like a credential: long, no spaces,
    letters and digits, not a path (UV_KEYRING_PROVIDER=subprocess and
    SSH_KEY_FILE=/root/.ssh/id are not secrets, and holding back every file
    that says "subprocess" would lose the backup)."""
    return (
        len(value) >= 16
        and not value.startswith(("/", "~", "."))
        and not any(c.isspace() for c in value)
        and any(c.isdigit() for c in value)
        and any(c.isalpha() for c in value)
    )


def known_secrets(environ: Mapping[str, str] | None = None) -> set[str]:
    """Every secret this machine holds that a run file could echo: the
    stack's Kibana key, the elastic user's password, and the value of any
    environment variable named like a key, token, secret or password whose
    value reads like one (_looks_secret)."""
    env = os.environ if environ is None else environ
    found = {v for k, v in env.items() if _SECRET_NAME.search(k) and _looks_secret(v)}
    with contextlib.suppress(Exception):  # no stack on this machine
        found.add(er.load_seed()[1])
    with contextlib.suppress(Exception, SystemExit):  # no stack .env on this machine
        found.add(er.load_es_admin()[1])
    return {v for v in found if v and len(v) >= MIN_SECRET_LEN}


def _holds_secret(path: Path, secret_values: set[str]) -> bool:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", errors="replace") as f:
                text = f.read()
        else:
            text = path.read_text(errors="replace")
    except (OSError, EOFError):  # unreadable (gzip.BadGzipFile is an OSError): held back
        return True
    return any(s in text for s in secret_values)


def backup_files(paths: list[Path], secret_values: set[str]) -> tuple[list[Path], list[Path]]:
    """(the files under ``paths`` to push, the ones held back because they
    hold a secret). Only BACKUP_FILES names, and nothing outside ``paths``."""
    keep, held = [], []
    for root in paths:
        for f in [root] if root.is_file() else sorted(root.rglob("*")):
            if f.name not in BACKUP_FILES or f.is_symlink() or not f.is_file():
                continue
            (held if _holds_secret(f, secret_values) else keep).append(f)
    return keep, held


def push(paths: list[Path], message: str, secret_values: set[str] | None = None) -> str:
    """Commit the worker's own run files and push, rebasing first; the
    folders of different workers never overlap, so the rebase does not
    conflict. The run folders are ignored (.gitignore keeps main clean), so
    the files backup_files picks are force-added, and the commit takes only
    those files, whatever else the index holds."""

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(HERE), *args], capture_output=True, text=True, check=False
        )

    files, held = backup_files(paths, known_secrets() if secret_values is None else secret_values)
    note = ""
    if held:
        note = f" (held back {len(held)} files holding a secret: {', '.join(map(str, held))[:300]})"
    names = [str(f) for f in files]
    if not names:
        return "nothing to commit" + note
    a = git("add", "-f", "--", *names)
    if a.returncode:
        return f"git add failed: {a.stderr.strip()[:300]}" + note
    if not git("diff", "--cached", "--quiet", "--", *names).returncode:
        return "nothing to commit" + note
    body = f"{message}\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
    c = git("commit", "-q", "-m", body, "--", *names)
    if c.returncode:  # a hook fixed a file in place: stage its fix and commit once more
        git("add", "-f", "--", *names)
        c = git("commit", "-q", "-m", body, "--", *names)
    if c.returncode:
        return f"commit failed: {c.stderr.strip()[:300]}" + note
    for _ in range(5):  # --autostash: changes outside the run files never block the rebase
        pulled = git("pull", "-q", "--rebase", "--autostash").returncode == 0
        if pulled and git("push", "-q").returncode == 0:
            return "pushed" + note
        time.sleep(10)
    return "push failed after 5 tries; the commit stays local and goes with the next push" + note


def run_worker(cfg: dict, worker: int, phase: str, telemetry: dict, do_push: bool) -> int:
    s = cfg["study"]
    check_split(cfg)
    wdir = worker_dir(phase, worker)
    blocks = worker_blocks(cfg, phase, worker)
    check_cli_pin(cfg)
    er.PROGRESS.mark(f"worker {worker} starting")
    for a in record_aborted(wdir, cfg, telemetry, phase, worker):
        print(f"[{a['block']}] aborted run recorded: {a['run_id']} ({a.get('otel')})", flush=True)
    watch = er.StallWatch(wdir, s["stall_minutes"])
    watch.start()
    backoff = 0
    try:
        for attempt_round in range(1 + s["max_retries"]):
            pending = []
            for block in blocks:
                have = done_runs(wdir, block)
                for i, (model, arm) in enumerate(block_order(cfg, block), 1):
                    runs = have.get((model, arm), [])
                    if attempt_round == 0 and runs:
                        continue
                    if attempt_round > 0 and (not runs or any(r.get("valid") for r in runs)):
                        continue
                    pending.append((block, i, model, arm, len(runs) + 1))
            if not pending:
                break
            print(
                f"=== worker {worker} {phase} round {attempt_round}: {len(pending)} runs",
                flush=True,
            )
            last_block = None
            for block, i, model, arm, attempt in pending:
                if do_push and last_block and block != last_block:
                    print(
                        f"push {last_block}: {push([wdir], f'chore(experiment): record MCP savings {phase} {last_block} (worker {worker})')}",
                        flush=True,
                    )
                if block != last_block:  # an image update mid-study stops the worker here
                    check_cli_pin(cfg)
                last_block = block
                if backoff:
                    print(f"usage or rate limit: waiting {backoff} min", flush=True)
                    er.PROGRESS.mark(f"waiting {backoff} min after a usage stop")
                    time.sleep(backoff * 60)
                er.PROGRESS.mark(f"{block} {model} {arm}")
                r = run_one(cfg, model, arm, block, phase, worker, i, telemetry, attempt)
                er.PROGRESS.mark(f"{block} done {model} {arm}")
                print(summary(r), flush=True)
                backoff = (
                    min(max(backoff * 2, 5), 30) if is_usage_stop(r.get("invalid_reason")) else 0
                )
            if do_push and last_block:
                print(
                    f"push {last_block}: {push([wdir], f'chore(experiment): record MCP savings {phase} {last_block} (worker {worker})')}",
                    flush=True,
                )
        if do_push:  # runs an earlier push left uncommitted go now
            left_over = push(
                [wdir], f"chore(experiment): record MCP savings {phase} runs (worker {worker})"
            )
            if left_over != "nothing to commit":
                print(f"push left-over runs: {left_over}", flush=True)
    finally:
        watch.stop()
    left = [
        (b, m, a)
        for b in blocks
        for (m, a) in block_order(cfg, b)
        if not any(r.get("valid") for r in done_runs(wdir, b).get((m, a), []))
    ]
    print(f"worker {worker} {phase}: {len(left)} cells without a valid run", flush=True)
    return 1 if left else 0


# ---------------------------------------------------------------- rescore


SCORE_KEYS = ("success", "failure_reason", "first_failed_step", "steps_passed", "steps_total")
SPANS_PER_REQUEST = 50


def rescore(cfg: dict, root: Path, telemetry: dict | None = None) -> dict:
    """Score every run under ``root`` again from the state it was scored on
    (state.json.gz) with the checks of this tree. With ``telemetry`` each
    run sends one span efficiency.rescore (rescore_span), which is the
    record: the analysis takes a run's newest rescore span over its root
    span's checks. ``root/rescored.json`` keeps a local copy; the workers'
    own files are never changed. A run without a saved state is not
    rescored (it is listed under without_state)."""
    runs, missing, spans = {}, [], []
    sha, dirty = harness_sha()
    at = time.time()
    for rec_path in sorted(root.glob("s*/*/*/record.json")):
        r = json.loads(rec_path.read_text())
        state_path = rec_path.parent / "state.json.gz"
        if not state_path.exists():
            missing.append(r["run_id"])
            continue
        with gzip.open(state_path, "rt") as f:
            state = json.load(f)
        out = score(cfg, r["marker"], r["space"], state, r.get("final_text") or "")
        runs[r["run_id"]] = {
            **{k: out[k] for k in SCORE_KEYS},
            "checks_passed": sum(c["ok"] for c in out["criteria"]),
            "checks_total": len(out["criteria"]),
            "original": {k: r.get(k) for k in SCORE_KEYS},
        }
        spans.append(rescore_span(r, out, sha, dirty, at))
    result = {
        "harness_sha": sha,
        "harness_dirty": dirty,
        "config_sha": config_sha(),
        "rescored_at": at,
        "changed": sorted(k for k, v in runs.items() if v["success"] != v["original"]["success"]),
        "without_state": missing,
        "runs": runs,
    }
    if telemetry is not None and spans:
        ds = cfg["study"]["dataset"]
        sent = [
            send_spans(telemetry, ds, span_body(ds, "rescore", spans[i : i + SPANS_PER_REQUEST]))
            for i in range(0, len(spans), SPANS_PER_REQUEST)
        ]
        result["otel"] = {
            "spans": len(spans),
            "requests": len(sent),
            "forwarded": sum(s.get("forwarded", 0) for s in sent),
            "failed": sum(s.get("failed", 0) for s in sent),
        }
    write_clean(root / "rescored.json", json.dumps(result, indent=2))
    return result


def rescore_span(record: dict, outcome: dict, sha: str, dirty: bool, at: float) -> dict:
    """One run's efficiency.rescore span, keyed by study.run_id. Its checks
    and success carry the root span's attribute names (span_attributes);
    study.harness_sha and study.rescore_config_sha name the code and config
    that rescored it, study.rescored_at when (unix seconds). study.config_sha
    stays the run's own, so a filter on it finds the run's rescore spans."""
    attrs = {
        "study.run_id": record["run_id"],
        "study.model": record.get("model_requested"),
        "study.arm": record.get("arm"),
        "study.block": record.get("block"),
        **{f"study.{k}": record.get(k) for k in ("phase", "worker", "attempt", "config_sha")},
        **span_attributes(outcome, {}),
        "study.success": bool(outcome["success"]),
        "study.harness_sha": sha,
        "study.harness_dirty": dirty,
        "study.rescore_config_sha": config_sha(),
        "study.rescored_at": float(at),
        "study.original_success": record.get("success"),
        "study.original_harness_sha": record.get("harness_sha"),
        "study.changed": bool(outcome["success"]) != bool(record.get("success")),
    }
    return one_span("efficiency.rescore", at, at, attrs, ok=bool(outcome["success"]))


# ---------------------------------------------------------------- probe

# The pilot's must-show "the server refuses a time field the index lacks":
# through the server's MCP tools, as the with-server setup calls them,
# create_data_view over the flights index with a time field it does not have
# must be refused and create nothing, and the same call with its real date
# field must succeed (so the refusal is about the field, not the call).
PROBE_NAME = "create_data_view_refuses_missing_time_field"
PROBE_DATA = "flights"
PROBE_MISSING_FIELD = "@timestamp"  # the field a study run on 0.3.0 used here


class McpStdio:
    """A minimal MCP client over stdio (newline-delimited JSON-RPC 2.0), the
    transport Claude Code uses with the server in the with-server setup."""

    PROTOCOL = "2025-06-18"

    def __init__(self, cmd: list[str], env: dict, timeout: float = 120.0):
        import queue
        import threading

        self.timeout, self._id = timeout, 0
        self._err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._err,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        import queue

        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no answer to {method} in {self.timeout:.0f} s")
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError(f"the server exited during {method}: {self.stderr_tail()}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if "method" in msg:
                if "id" in msg:  # a request from the server (ping): answer it
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result") or {}

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def start(self) -> McpStdio:
        self.request(
            "initialize",
            {
                "protocolVersion": self.PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "mcp-savings-probe", "version": "1"},
            },
        )
        self.notify("notifications/initialized")
        return self

    def tools(self) -> list[str]:
        names, cursor = [], None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            names += [t.get("name") for t in page.get("tools", [])]
            cursor = page.get("nextCursor")
            if not cursor:
                return names

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def stderr_tail(self, n: int = 400) -> str:
        self._err.flush()
        self._err.seek(0)
        return self._err.read()[-n:].decode(errors="replace")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            assert self.proc.stdin is not None
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_group(self.proc)
        self._err.close()

    def __enter__(self) -> McpStdio:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def tool_text(result: dict) -> str:
    return " ".join(
        c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
    ).strip()


def _views_over(kb: er.Kibana, index: str) -> list[dict]:
    """The space's data views over ``index``, each read in full (the list
    call does not return the time field)."""
    _, body = kb.call("GET", "/api/data_views")
    views = []
    for v in (body or {}).get("data_view", []) if isinstance(body, dict) else []:
        if v.get("title") != index:
            continue
        s, full = kb.call("GET", f"/api/data_views/data_view/{v['id']}")
        views.append((full or {}).get("data_view", v) if s == 200 else v)
    return views


def probe_time_field(cfg: dict, kb: er.Kibana, es, client, space_kb) -> tuple[bool, str]:
    """(passed, detail) of the time-field must-show. ``kb`` is the default
    space's Kibana client, ``space_kb(space)`` one scoped to a space,
    ``client`` an MCP client of the pinned server. The probe works in a
    space of its own, deleted at the end with everything in it."""
    index = cfg["data"][PROBE_DATA]["index"]
    missing = PROBE_MISSING_FIELD
    props = st._properties(es, index)
    if not props:
        return False, f"no mapping for {index}: is the sample data installed (setup)?"
    if st.resolve_field(props, missing):
        return False, f"premise wrong: {index} has a field {missing!r}"
    dates = sorted(st.date_fields(props))
    if not dates:
        return False, f"premise wrong: {index} has no date field"
    if "create_data_view" not in client.tools():
        return False, "the server lists no create_data_view tool"
    space = f"ms-probe-{secrets.token_hex(3)}"
    status, _ = kb.call("POST", "/api/spaces/space", {"id": space, "name": f"MS probe {space}"})
    if status != 200:
        return False, f"could not create the probe space: HTTP {status}"
    try:
        skb = space_kb(space)
        bad = client.call_tool(
            "create_data_view",
            {
                "index_pattern": index,
                "name": f"{space} missing",
                "time_field": missing,
                "space": space,
            },
        )
        bad_text = " ".join(tool_text(bad).split())
        left = _views_over(skb, index)
        good = client.call_tool(
            "create_data_view",
            {
                "index_pattern": index,
                "name": f"{space} control",
                "time_field": dates[0],
                "space": space,
            },
        )
        made = [v for v in _views_over(skb, index) if v.get("timeFieldName") == dates[0]]
    finally:
        delete_space(kb, space)
    problems = []
    if not bad.get("isError"):
        problems.append(f"time_field {missing!r} was accepted")
    elif missing not in bad_text:
        problems.append(f"the refusal does not name {missing!r}")
    if left:
        problems.append(f"{len(left)} data view(s) over {index} left after the refused call")
    if good.get("isError") or not made:
        problems.append(f"control time_field {dates[0]!r} failed: {tool_text(good)[:120]}")
    said = f"server: {bad_text[:160]}"
    if problems:
        return False, "; ".join(problems) + f" ({said})"
    return True, f"refused {missing!r} on {index}, created nothing; accepted {dates[0]!r} ({said})"


def probe_attributes(cfg: dict, passed: bool, detail: str, versions: dict) -> dict:
    sha, dirty = harness_sha()
    return {
        "study.probe.name": PROBE_NAME,
        "study.probe.passed": bool(passed),
        "study.probe.detail": detail,
        "study.probe.index": cfg["data"][PROBE_DATA]["index"],
        "study.probe.time_field": PROBE_MISSING_FIELD,
        "study.harness_sha": sha,
        "study.harness_dirty": dirty,
        "study.git_sha": git_sha(),
        "study.config_sha": config_sha(),
        "study.server_version": versions.get("server") or cfg["study"]["server_version"],
        "study.kibana_version": versions.get("kibana"),
    }


def run_probe(cfg: dict, telemetry: dict) -> dict:
    """Run the time-field probe against the running stack and the pinned
    server (installed by setup), send its study.probe span and return what
    it found."""
    t0 = time.time()
    versions: dict = {}
    api_key = ""
    try:
        kibana_url, api_key = er.load_seed()
        kb = er.Kibana(kibana_url, api_key)
        ok, health_detail = health(kb)
        if not ok:
            raise RuntimeError(f"Kibana is not available ({health_detail})")
        versions["kibana"] = kibana_version(kb)
        versions["server"] = ensure_server(cfg)
        entry = mcp_entry(cfg, kibana_url, api_key, None)
        with tempfile.TemporaryDirectory(prefix="ms-probe-") as tmp:
            env = {**child_env(os.environ, Path(tmp) / "home", {}), **entry["env"]}
            with McpStdio([entry["command"], *entry["args"]], env) as client:
                client.start()
                passed, detail = probe_time_field(
                    cfg,
                    kb,
                    st.Elastic.from_stack(),
                    client,
                    lambda sp: SpaceKibana(kibana_url, api_key, sp),
                )
    except (Exception, SystemExit) as e:  # a probe that cannot run fails, and says why
        passed, detail = False, f"probe error: {e!r}"
    detail = redact(" ".join(detail.split()), api_key)[0][:300]
    attrs = probe_attributes(cfg, passed, detail, versions)
    ds = cfg["study"]["dataset"]
    stats = send_spans(
        telemetry,
        ds,
        span_body(ds, "probe", [one_span("study.probe", t0, time.time(), attrs, passed)]),
    )
    return {**attrs, "otel": {k: stats.get(k) for k in ("requests", "forwarded", "failed")}}


# ---------------------------------------------------------------- CLI


def telemetry_from(arg: str) -> dict:
    if arg == "none":
        return {"endpoint": None, "api_key": None}
    key = os.environ.get("HONEYCOMB_API_KEY")
    if not key:
        raise SystemExit(
            "FAIL: HONEYCOMB_API_KEY is not set — export it, or pass --telemetry none "
            "for a local-only run"
        )
    return {"endpoint": arg, "api_key": key}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="install the pinned server, the API reference and the sample data")
    p = sub.add_parser("prompt", help="print the prompt an arm gets")
    p.add_argument("arm", choices=list(TASK_ARM))
    pl = sub.add_parser("plan", help="print every block's seeded order and each worker's blocks")
    pl.add_argument("--phase", choices=["study", "pilot"], default="study")
    rs = sub.add_parser(
        "rescore",
        help="score a phase's runs again from their saved state; one efficiency.rescore span each",
    )
    rs.add_argument("--phase", choices=["study", "pilot"], required=True)
    rs.add_argument(
        "--root", type=Path, help="the phase's run folders (default: runs/mcp-savings/<phase>)"
    )
    sub.add_parser(
        "probe",
        help="check the pinned server refuses a time field the index lacks; one study.probe span",
    )
    sub.add_parser(
        "run",
        help="one check run (phase check, worker 0); --model and --arm are required, "
        "e.g. run --model claude-haiku-5-5 --arm with-mcp",
    )
    sub.add_parser("worker", help="run a worker's blocks")
    for name in ("run", "worker", "rescore", "probe"):
        q = sub.choices[name]
        q.add_argument(
            "--telemetry",
            default=os.environ.get("HONEYCOMB_OTLP_BASE", DEFAULT_ENDPOINT),
            help="OTLP/HTTP base URL the relay forwards to; 'none' keeps telemetry local",
        )
        if name == "run":
            q.add_argument(
                "--model", required=True, help=f"required: one of {', '.join(load()['study']['models'])}"
            )
            q.add_argument("--arm", required=True, choices=list(TASK_ARM), help="required")
            q.add_argument("--block", default="t1")
        elif name == "worker":
            q.add_argument("--worker", type=int, required=True)
            q.add_argument("--phase", choices=["study", "pilot"], required=True)
            q.add_argument("--push", action="store_true", help="commit and push after every block")
    args = ap.parse_args(argv)
    cfg = load()
    if args.cmd in ("setup", "run", "probe"):  # worker checks in run_worker
        check_cli_pin(cfg)
    if args.cmd == "setup":
        print(json.dumps(setup(cfg), indent=2))
        return 0
    if args.cmd == "prompt":
        print(build_prompt(cfg, args.arm, "MS-000000", "ms-b000-haiku-with-mcp-000000"))
        return 0
    if args.cmd == "plan":
        check_split(cfg)
        for w in range(1, len(cfg["study"]["workers"]) + 1):
            for b in worker_blocks(cfg, args.phase, w):
                print(
                    f"s{w} {b}: "
                    + ", ".join(f"{m.removeprefix('claude-')}/{a}" for m, a in block_order(cfg, b))
                )
        return 0
    telemetry = telemetry_from(args.telemetry)
    if args.cmd == "rescore":
        res = rescore(cfg, args.root or OUT / args.phase, telemetry)
        print(
            f"rescored {len(res['runs'])} runs with harness {res['harness_sha']}: "
            f"{len(res['changed'])} changed outcome, {len(res['without_state'])} without state; "
            f"spans: {res.get('otel')}"
        )
        return 1 if (res.get("otel") or {}).get("failed") else 0
    if args.cmd == "probe":
        res = run_probe(cfg, telemetry)
        print(json.dumps(res, indent=2))
        print("PASS" if res["study.probe.passed"] else "FAIL", res["study.probe.detail"])
        if res["otel"].get("failed"):
            print("FAIL: the study.probe span did not reach Honeycomb", res["otel"])
            return 1
        return 0 if res["study.probe.passed"] else 1
    if args.cmd == "run":
        if args.model not in cfg["study"]["models"]:
            raise SystemExit(
                f"FAIL: --model {args.model} is not in mcp_savings.json "
                f"({', '.join(cfg['study']['models'])})"
            )
        r = run_one(cfg, args.model, args.arm, args.block, "check", 0, 1, telemetry)
        print(summary(r))
        return 0 if r.get("valid") else 1
    return run_worker(cfg, args.worker, args.phase, telemetry, args.push)


if __name__ == "__main__":
    raise SystemExit(main())
