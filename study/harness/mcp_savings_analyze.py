#!/usr/bin/env python3
"""MCP savings study: the confirmatory comparison fixed before any run,
worked from the runs' rows in Honeycomb.

    # merge the result pages downloaded by the pull (docs/PULL_ROWS.md)
    uv run python scripts/experiment/mcp_savings_analyze.py rows --out rows.jsonl page-*.json
    # the confirmatory analysis
    uv run python scripts/experiment/mcp_savings_analyze.py analyze --phase study --rows rows.jsonl

Honeycomb is the single source of truth: every number here is computed from
the rows file, one JSON object per span pulled from dataset mcp-savings (each
run's root span ``efficiency.run``, and any ``efficiency.rescore`` span).
Run folders are never read for a number; ``--audit-runs`` only compares them
with the rows, and a mismatch stops the analysis. What it computes is the
board's "Savings study" pad, "Methodology to remember":

- Which runs count: the root spans of the given config sha and phase, all
  from one harness commit (study.harness_sha): ``--harness-sha`` picks it,
  and without it rows from more than one commit stop the analysis. Rows
  without a sha (older data) pass only when no row has one. For each run
  id, the newest ``efficiency.rescore`` row (by study.rescored_at)
  replaces its checks and success. Every planned run (blocks × models ×
  setups, from mcp_savings.json) must appear exactly once among the valid
  root spans, or the analysis stops with the missing, duplicate and invalid
  runs. Invalid and aborted runs are listed; their cost counts toward the
  spend, not toward the results.
- Price check: each run's cost recomputed from its root span's token totals
  (input, output, cache read, cache write) and the plan's price table must
  reproduce the CLI's cost (study.total_cost_usd), or the mismatches are
  listed and the analysis stops.
- Per model and setup: the median cost, time (study.wall_s, start to the
  model's result), tool calls and failed tool calls per run, each with a 95%
  percentile-bootstrap interval; and how many runs passed every check.
- Per model: median with the server ÷ median without, for cost and time,
  with its interval; each setup's runs are resampled independently.
- Success: per model, both pass counts and the pass-rate difference (with
  − without) with a 95% bootstrap interval that resamples blocks (paired by
  block). Success holds unless the whole interval lies below 0 (the server
  credibly lowers success); where it does not hold, the model's savings are
  shown but marked "not counted".
- Spend: the cost over every run of the config and phase, counted, invalid
  and aborted.

Caches are not controlled: each run's cost is taken as it came. The
bootstrap draws come from the plan's analysis seed, one stream per model,
metric and quantity, so a result does not depend on which others are
computed. Everything else about the runs is exploratory
(mcp_savings_explore.py).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mcp_savings as ms  # noqa: E402  (sibling script)

RUN_SPAN = "efficiency.run"
RESCORE_SPAN = "efficiency.rescore"
WITH, WITHOUT = "with-mcp", "no-mcp"

# A run's fields: (Honeycomb column, kind). The rows file is keyed by the
# columns; a column the span lacks (an older harness, an aborted run) is absent.
FIELDS = {
    "run_id": ("study.run_id", "str"),
    "config_sha": ("study.config_sha", "str"),
    "phase": ("study.phase", "str"),
    "block": ("study.block", "str"),
    "model": ("study.model_requested", "str"),
    "arm": ("study.arm", "str"),
    "worker": ("study.worker", "str"),
    "attempt": ("study.attempt", "num"),
    "valid": ("study.valid", "bool"),
    "invalid_reason": ("study.invalid_reason", "str"),
    "aborted": ("study.aborted", "bool"),
    "timed_out": ("study.timed_out", "bool"),
    "success": ("study.success", "bool"),
    "checks_passed": ("study.checks_passed", "num"),
    "checks_total": ("study.checks_total", "num"),
    "steps_passed": ("study.steps_passed", "num"),
    "first_failed_step": ("study.first_failed_step", "str"),
    "failure_reason": ("study.failure_reason", "str"),
    "cost_usd": ("study.total_cost_usd", "num"),
    "input_tokens": ("study.input_tokens", "num"),
    "output_tokens": ("study.output_tokens", "num"),
    "cache_read_tokens": ("study.cache_read_input_tokens", "num"),
    "cache_write_tokens": ("study.cache_creation_input_tokens", "num"),
    "time_s": ("study.wall_s", "num"),
    "cli_ms": ("study.duration_ms", "num"),
    "tool_calls": ("study.n_tool_calls", "num"),
    "failed_tool_calls": ("agent.tool.failures", "num"),
    "mcp_calls": ("study.n_mcp_calls", "num"),
    "bash_calls": ("study.n_bash_calls", "num"),
    "turns": ("study.num_turns", "num"),
    "kibana_version": ("study.kibana_version", "str"),
    "server_version": ("study.server_version", "str"),
    "claude_cli": ("study.claude_cli", "str"),
    "harness_sha": ("study.harness_sha", "str"),
    "trace_id": ("trace.trace_id", "str"),
    "rescored_at": ("study.rescored_at", "num"),
}
# What a rescore replaces: the root span's check outcome.
SCORE_FIELDS = (
    "success",
    "checks_passed",
    "checks_total",
    "steps_passed",
    "first_failed_step",
    "failure_reason",
)
# The pull's raw columns (docs/PULL_ROWS.md). The span name, config sha and
# phase are the pull's filters: `rows` writes them into every row.
FILTER_COLUMNS = ("name", "study.config_sha", "study.phase")
ROOT_COLUMNS = [c for c, _ in FIELDS.values() if c not in FILTER_COLUMNS + ("study.rescored_at",)]
RESCORE_COLUMNS = ["study.run_id", "study.rescored_at", "study.harness_sha"] + [
    FIELDS[k][0] for k in SCORE_FIELDS
]

METRICS = ("cost_usd", "time_s", "tool_calls", "failed_tool_calls")
RATIOS = ("cost_usd", "time_s")
# The price table must reproduce the CLI's cost: it sums per-request costs in
# floating point, so allow a hair of rounding and nothing more.
PRICE_REL, PRICE_ABS = 1e-6, 1e-9


# ---------------------------------------------------------------- rows


def _bool(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        return {"true": True, "false": False}.get(v.strip().lower())
    return bool(v)


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v) if not isinstance(v, int) else v
    except (TypeError, ValueError):
        return None


def _str(v):
    return None if v is None or v == "" else str(v)


_KIND = {"bool": _bool, "num": _num, "str": _str}


def merge_pages(pages: list[dict]) -> list[dict]:
    """The rows of downloaded Honeycomb raw-row results (the query_result_json
    of each run_query page), one object per span: its columns, ``time``, and
    the pull's equality filters (span name, config sha, phase) written in.
    A row seen on two pages (same row_id) is kept once."""
    out, seen = [], set()
    for page in pages:
        fixed = {
            f["column"]: f["value"]
            for f in (page.get("template") or {}).get("filters") or []
            if f.get("op") == "=" and f.get("column") in FILTER_COLUMNS
        }
        for r in page.get("results") or []:
            rid = r.get("row_id")
            if rid is not None and rid in seen:
                continue
            seen.add(rid)
            row = {k: v for k, v in (r.get("data") or {}).items() if v is not None}
            out.append({**fixed, **row, "time": r.get("time"), "row_id": rid})
    return sorted(out, key=lambda r: (r.get("time") or "", r.get("row_id") or ""))


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def to_run(row: dict) -> dict:
    """A span's row as a run: the study's short field names, parsed."""
    r = {k: _KIND[kind](row.get(col)) for k, (col, kind) in FIELDS.items()}
    r["model"] = r["model"] or _str(row.get("study.model"))
    r["name"] = row.get("name")
    r["time"] = row.get("time")
    return r


def apply_rescores(runs: list[dict], rescores: list[dict]) -> list[str]:
    """Replace each run's checks and success with its newest rescore (by
    study.rescored_at, then row time). The worker's outcome is kept under
    ``original``. Returns the runs whose success changed."""
    newest: dict[str, dict] = {}
    for x in rescores:
        rid = x.get("run_id")
        if rid is None:
            continue
        key = (x.get("rescored_at") or 0, x.get("time") or "")
        old = newest.get(rid)
        if old is None or key > (old.get("rescored_at") or 0, old.get("time") or ""):
            newest[rid] = x
    changed = set()
    for r in runs:
        x = newest.get(r["run_id"])
        if x is None:
            continue
        r["original"] = {k: r.get(k) for k in SCORE_FIELDS}
        r.update({k: x.get(k) for k in SCORE_FIELDS})
        r["rescored"] = True
        r["rescored_at"] = x.get("rescored_at")
        r["rescore_harness_sha"] = x.get("harness_sha")
        if bool(r["original"]["success"]) != bool(r.get("success")):
            changed.add(r["run_id"])
    return sorted(changed)


HARNESS_SHA_HELP = (
    "count only the root spans of this harness commit (study.harness_sha); "
    "required when the rows hold more than one"
)


class HarnessMix(ValueError):
    """The root spans of a config and phase come from more than one harness
    commit, and no --harness-sha says which one to analyze."""


MIN_SHA_LEN = 7


def same_commit(a: str | None, b: str | None) -> bool:
    """Two abbreviated shas of one commit: git's %h can abbreviate the same
    commit to different lengths in different clones, so one is a prefix of
    the other."""
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def harness_counts(runs: list[dict]) -> Counter:
    """How many root spans carry each harness commit (None: no sha); shas
    of one commit abbreviated to different lengths count as one, under the
    longest."""
    shas = sorted({r.get("harness_sha") for r in runs if r.get("harness_sha")}, key=len, reverse=True)
    canon: dict[str, str] = {}
    for s in shas:
        canon[s] = next((c for c in canon.values() if same_commit(c, s)), s)
    return Counter(canon.get(r.get("harness_sha")) if r.get("harness_sha") else None for r in runs)


def pick_harness(runs: list[dict], harness_sha: str | None) -> list[dict]:
    """The root spans of one harness commit. With ``harness_sha`` only the
    spans that carry it (same_commit). Without it every span must carry the
    same commit, or none may (older data, from before the harness sent it);
    anything else raises HarnessMix naming each sha and its count."""
    if harness_sha:
        if len(harness_sha) < MIN_SHA_LEN:
            raise HarnessMix(f"--harness-sha {harness_sha}: give at least {MIN_SHA_LEN} characters")
        return [r for r in runs if same_commit(r.get("harness_sha"), harness_sha)]
    counts = harness_counts(runs)
    if len(counts) > 1:
        listed = ", ".join(
            f"{sha or '(none)'}: {n}" for sha, n in sorted(counts.items(), key=lambda x: str(x[0]))
        )
        raise HarnessMix(
            f"the root spans come from more than one harness commit ({listed}); "
            "pass --harness-sha <sha> to analyze one of them"
        )
    return runs


def split(
    rows: list[dict], config_sha: str, phase: str, harness_sha: str | None = None
) -> tuple[list[dict], list[dict]]:
    """(the root spans of this config, phase and harness commit
    (pick_harness), every rescore row), as runs. A rescore applies to its
    run by study.run_id whatever its own harness_sha, which names the
    rescoring code."""
    runs, rescores = [], []
    for row in rows:
        r = to_run(row)
        if r["name"] == RUN_SPAN and r["config_sha"] == config_sha and r["phase"] == phase:
            runs.append(r)
        elif r["name"] == RESCORE_SPAN:
            rescores.append(r)
    return pick_harness(runs, harness_sha), rescores


# ---------------------------------------------------------------- completeness


def planned_cells(cfg: dict, phase: str) -> list[tuple[str, str, str]]:
    cells = []
    for w in range(1, len(cfg["study"]["workers"]) + 1):
        for block in ms.worker_blocks(cfg, phase, w):
            cells += [(block, m, a) for m, a in ms.block_order(cfg, block)]
    return cells


def _brief(r: dict) -> dict:
    return {
        "run_id": r["run_id"],
        "cell": [r["block"], r["model"], r["arm"]],
        "cost_usd": r.get("cost_usd"),
    }


def select(cfg: dict, phase: str, runs: list[dict]) -> dict:
    """The run that counts for every planned cell, and every reason the
    plan is not met: cells without a valid run, cells with more than one
    valid span (a run sent twice, or two valid runs), run ids seen on more
    than one root span, and valid runs outside the plan. Invalid and aborted
    runs are listed; they do not count."""
    plan = planned_cells(cfg, phase)
    planned = set(plan)
    by_cell: dict[tuple, list[dict]] = {}
    invalid, aborted, outside = [], [], []
    for r in runs:
        if r.get("aborted"):
            aborted.append(_brief(r))
        elif r.get("valid") is not True:
            invalid.append(
                {
                    **_brief(r),
                    "reason": r.get("invalid_reason")
                    or ("no study.valid on the span" if r.get("valid") is None else None),
                }
            )
        elif (r["block"], r["model"], r["arm"]) not in planned:
            outside.append(_brief(r))
        else:
            by_cell.setdefault((r["block"], r["model"], r["arm"]), []).append(r)
    counted, missing, duplicate = [], [], []
    for cell in sorted(planned):
        got = by_cell.get(cell, [])
        if len(got) == 1:
            counted.append(got[0])
        elif not got:
            missing.append(list(cell))
        else:
            duplicate.append({"cell": list(cell), "run_ids": sorted(r["run_id"] for r in got)})
    ids = Counter(r["run_id"] for r in runs)
    return {
        "planned": len(plan),
        "counted": counted,
        "missing": missing,
        "duplicate_cells": duplicate,
        "duplicate_run_ids": sorted(k for k, n in ids.items() if n > 1),
        "invalid": sorted(invalid, key=lambda x: x["run_id"] or ""),
        "aborted": sorted(aborted, key=lambda x: x["run_id"] or ""),
        "outside_plan": sorted(outside, key=lambda x: x["run_id"] or ""),
    }


def complete(sel: dict) -> bool:
    return not (sel["missing"] or sel["duplicate_cells"] or sel["duplicate_run_ids"])


def unique_runs(runs: list[dict]) -> list[dict]:
    """One span per run id (the first): a span sent twice is one run."""
    seen, out = set(), []
    for r in runs:
        if r["run_id"] not in seen:
            seen.add(r["run_id"])
            out.append(r)
    return out


# ---------------------------------------------------------------- price check


def price(cfg: dict, r: dict) -> float | None:
    p = cfg["study"]["prices"]["usd_per_mtok"].get(r.get("model"))
    toks = [r.get(k) for k in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")]
    if p is None or any(t is None for t in toks):
        return None
    i, o, w, c = toks
    return (i * p["input"] + o * p["output"] + w * p["cache_write_5m"] + c * p["cache_read"]) / 1e6


def price_check(cfg: dict, runs: list[dict]) -> dict:
    """Every run's cost recomputed from its token totals and the plan's price
    table, against the CLI's own cost. A valid run without its tokens or
    cost is a mismatch; an invalid or aborted run may have stopped before
    the CLI reported either, and is listed as unchecked. The table prices
    five-minute cache writes only, so a run that wrote for an hour shows here
    too."""
    mismatches, unchecked, checked = [], [], 0
    for r in unique_runs(runs):
        est, cost = price(cfg, r), r.get("cost_usd")
        if est is None or cost is None:
            if r.get("aborted") or r.get("valid") is not True:
                unchecked.append(r["run_id"])
            else:
                mismatches.append({"run_id": r["run_id"], "cost_usd": cost, "priced_usd": est})
            continue
        checked += 1
        if abs(est - cost) > max(PRICE_ABS, PRICE_REL * abs(cost)):
            mismatches.append({"run_id": r["run_id"], "cost_usd": cost, "priced_usd": est})
    return {"checked": checked, "mismatches": mismatches, "unchecked": unchecked}


# ---------------------------------------------------------------- statistics


def _ci(values: list[float]) -> list[float]:
    s = sorted(values)
    lo = s[int(0.025 * (len(s) - 1))]
    hi = s[int(round(0.975 * (len(s) - 1)))]
    return [lo, hi]


def _rng(seed: int, *parts: str) -> random.Random:
    return random.Random(":".join([str(seed), *parts]))


def median_ci(xs: list[float], rng: random.Random, resamples: int) -> dict:
    meds = [statistics.median(rng.choices(xs, k=len(xs))) for _ in range(resamples)]
    return {"n": len(xs), "median": statistics.median(xs), "median_ci95": _ci(meds)}


def ratio_ci(w: list[float], o: list[float], rng: random.Random, resamples: int) -> dict:
    ratios = []
    for _ in range(resamples):
        a = statistics.median(rng.choices(w, k=len(w)))
        b = statistics.median(rng.choices(o, k=len(o)))
        ratios.append(a / b if b else float("nan"))
    mo = statistics.median(o)
    return {
        "median_with": statistics.median(w),
        "median_without": mo,
        "ratio": statistics.median(w) / mo if mo else None,
        "ratio_ci95": _ci(ratios),
    }


def _values(runs: list[dict], model: str, arm: str, field: str) -> list[float]:
    return [
        r[field]
        for r in runs
        if r["model"] == model and r["arm"] == arm and r.get(field) is not None
    ]


def success_difference(runs: list[dict], model: str, rng: random.Random, resamples: int) -> dict:
    """The model's pass rate with the server minus without, with a 95%
    percentile-bootstrap interval that resamples blocks: each block holds
    one run of each setup, so a draw keeps the pair together. Success holds
    unless the whole interval lies below 0 (the server credibly lowers
    success); with fewer than two paired blocks there is no interval and it
    holds."""
    pairs: dict[str, dict[str, bool]] = {}
    for r in runs:
        if r["model"] == model and r["arm"] in (WITH, WITHOUT):
            pairs.setdefault(r["block"], {})[r["arm"]] = bool(r.get("success"))
    paired = [(p[WITH], p[WITHOUT]) for _, p in sorted(pairs.items()) if len(p) == 2]
    if not paired:
        return {"paired_blocks": 0, "difference": None, "difference_ci95": None, "holds": True}

    def diff(sample):
        return (sum(a for a, _ in sample) - sum(b for _, b in sample)) / len(sample)

    point = diff(paired)
    if len(paired) < 2:
        return {"paired_blocks": 1, "difference": point, "difference_ci95": None, "holds": True}
    ci = _ci([diff(rng.choices(paired, k=len(paired))) for _ in range(resamples)])
    return {
        "paired_blocks": len(paired),
        "difference": point,
        "difference_ci95": ci,
        "holds": not ci[1] < 0,
    }


def statistics_of(cfg: dict, runs: list[dict]) -> dict:
    s = cfg["study"]
    seed, n = s["analysis_seed"], s["resamples"]
    per_model_arm, ratios, success, by_model = {}, {}, {}, {}
    for model in s["models"]:
        for arm in s["arms"]:
            group = [r for r in runs if r["model"] == model and r["arm"] == arm]
            key = f"{model}/{arm}"
            success[key] = {"passed": sum(bool(r.get("success")) for r in group), "runs": len(group)}
            per_model_arm[key] = {}
            for metric in METRICS:
                xs = _values(runs, model, arm, metric)
                if len(xs) >= 2:
                    per_model_arm[key][metric] = median_ci(xs, _rng(seed, model, arm, metric), n)
        w, o = success[f"{model}/{WITH}"], success[f"{model}/{WITHOUT}"]
        diff = success_difference(runs, model, _rng(seed, model, "success"), n)
        holds = diff["holds"]
        by_model[model] = {
            "passed_with": w["passed"],
            "runs_with": w["runs"],
            "passed_without": o["passed"],
            "runs_without": o["runs"],
            **diff,
        }
        for metric in RATIOS:
            wv, ov = _values(runs, model, WITH, metric), _values(runs, model, WITHOUT, metric)
            if len(wv) >= 2 and len(ov) >= 2:
                ratios.setdefault(model, {})[metric] = {
                    **ratio_ci(wv, ov, _rng(seed, model, "ratio", metric), n),
                    "saving_counts": holds,
                }
    return {
        "per_model_arm": per_model_arm,
        "ratios": ratios,
        "success": success,
        "success_by_model": by_model,
    }


# ---------------------------------------------------------------- analysis


def prepare(
    cfg: dict, phase: str, rows: list[dict], config_sha: str, harness_sha: str | None = None
) -> dict:
    """The rows of this config, phase and harness commit, rescored, and the
    selection. Raises HarnessMix (split)."""
    runs, rescores = split(rows, config_sha, phase, harness_sha)
    changed = apply_rescores(runs, rescores)
    return {"runs": runs, "rescore_changed": changed, "selection": select(cfg, phase, runs)}


def _spend(runs: list[dict], sel: dict) -> dict:
    uniq = unique_runs(runs)
    counted = {r["run_id"] for r in sel["counted"]}

    def total(rs):
        return round(sum(r.get("cost_usd") or 0 for r in rs), 4)

    return {
        "all_runs": total(uniq),
        "counted": total([r for r in uniq if r["run_id"] in counted]),
        "invalid": total([r for r in uniq if r["run_id"] in {x["run_id"] for x in sel["invalid"]}]),
        "aborted": total([r for r in uniq if r.get("aborted")]),
        "aborted_without_cost": sum(1 for r in uniq if r.get("aborted") and r.get("cost_usd") is None),
    }


def _traces(runs: list[dict]) -> dict:
    """One Honeycomb trace per run: runs without a trace id, and trace ids
    shared by more than one run."""
    uniq = unique_runs(runs)
    by_trace: dict[str, set] = {}
    for r in uniq:
        if r.get("trace_id"):
            by_trace.setdefault(r["trace_id"], set()).add(r["run_id"])
    return {
        "without_trace_id": sorted(r["run_id"] for r in uniq if not r.get("trace_id")),
        "shared": sorted(t for t, ids in by_trace.items() if len(ids) > 1),
    }


def _seen(runs: list[dict], key: str) -> dict:
    """The distinct values of a field over the runs, and how many lack it."""
    return {
        "values": sorted({r[key] for r in runs if r.get(key) is not None}, key=str),
        "absent": sum(1 for r in runs if r.get(key) is None),
    }


def analyze(
    cfg: dict,
    phase: str,
    rows: list[dict],
    config_sha: str,
    allow_incomplete: bool = False,
    harness_sha: str | None = None,
) -> dict:
    s = cfg["study"]
    prep = prepare(cfg, phase, rows, config_sha, harness_sha)
    runs, sel = prep["runs"], prep["selection"]
    counted = sel["counted"]
    prices = price_check(cfg, runs)
    ok = complete(sel) and not prices["mismatches"]
    a = {
        "phase": phase,
        "config_sha": config_sha,
        "harness_sha": harness_sha or next(iter(harness_counts(runs)), None),
        "complete": complete(sel),
        "ok": ok,
        "root_spans": len(runs),
        "planned": sel["planned"],
        "counted": len(counted),
        "missing": sel["missing"],
        "duplicate_cells": sel["duplicate_cells"],
        "duplicate_run_ids": sel["duplicate_run_ids"],
        "invalid": sel["invalid"],
        "aborted": sel["aborted"],
        "outside_plan": sel["outside_plan"],
        "price_check": prices,
        "spend_usd": _spend(runs, sel),
        "traces": _traces(runs),
        "rescored_runs": sum(bool(r.get("rescored")) for r in counted),
        "rescore_changed": prep["rescore_changed"],
        "harness_shas": _seen(counted, "harness_sha"),
        "claude_cli": _seen(counted, "claude_cli"),
        "kibana_versions": _seen(counted, "kibana_version"),
        "server_versions": {
            arm: _seen([r for r in counted if r["arm"] == arm], "server_version")
            for arm in s["arms"]
        },
        "first_row": min((r["time"] for r in runs if r.get("time")), default=None),
        "last_row": max((r["time"] for r in runs if r.get("time")), default=None),
        "bootstrap": {"resamples": s["resamples"], "seed": s["analysis_seed"]},
    }
    if complete(sel) or allow_incomplete:
        a.update(statistics_of(cfg, counted))
    return a


# ---------------------------------------------------------------- audit


def load_records(root: Path) -> list[dict]:
    """Every run's local record.json, for the audit only; when
    ``rescored.json`` is present (mcp_savings.py rescore), its outcome
    replaces the one the worker wrote, which is kept under ``original``.
    No number is computed from these."""
    rescored = {}
    if (root / "rescored.json").exists():
        rescored = json.loads((root / "rescored.json").read_text()).get("runs", {})
    out = []
    for path in sorted(root.glob("s*/*/*/record.json")):
        r = json.loads(path.read_text())
        if r["run_id"] in rescored:
            r["original"] = {k: r.get(k) for k in ms.SCORE_KEYS}
            r.update({k: rescored[r["run_id"]][k] for k in ms.SCORE_KEYS})
            r["rescored"] = True
        out.append(r)
    return out


def audit(runs: list[dict], records: list[dict]) -> list[str]:
    """Where the local records and the Honeycomb rows disagree: a run in one
    and not the other (aborted runs have no record), or a run whose cost,
    validity or success differs. Empty means they agree."""
    hc = {r["run_id"]: r for r in unique_runs(runs) if not r.get("aborted")}
    loc = {r["run_id"]: r for r in records}
    out = [f"{k}: in Honeycomb, no local record" for k in sorted(hc.keys() - loc.keys())]
    out += [f"{k}: local record, not in Honeycomb" for k in sorted(loc.keys() - hc.keys())]
    for k in sorted(hc.keys() & loc.keys()):
        h, rec = hc[k], loc[k]
        if h.get("cost_usd") is not None and rec.get("total_cost_usd") is not None:
            if abs(h["cost_usd"] - rec["total_cost_usd"]) > 1e-9:
                out.append(f"{k}: cost {h['cost_usd']} in Honeycomb, {rec['total_cost_usd']} local")
        elif h.get("cost_usd") != rec.get("total_cost_usd"):
            out.append(f"{k}: cost {h.get('cost_usd')} in Honeycomb, {rec.get('total_cost_usd')} local")
        for field, local in (("valid", "valid"), ("success", "success")):
            if bool(h.get(field)) != bool(rec.get(local)):
                out.append(f"{k}: {field} {h.get(field)} in Honeycomb, {rec.get(local)} local")
    return out


# ---------------------------------------------------------------- page


def _name(model: str) -> str:
    return model.removeprefix("claude-").replace("-5-5", " 5.5").capitalize()


def _iv(d: dict | None, key: str, fmt: str) -> str:
    if not d:
        return "n/a"
    lo, hi = d[f"{key}_ci95"]
    return f"{format(d[key], fmt)} ({format(lo, fmt)}–{format(hi, fmt)})"


def _diff(sm: dict) -> str:
    if sm["difference"] is None:
        return "n/a"
    if sm["difference_ci95"] is None:
        return f"{sm['difference']:+.2f}"
    lo, hi = sm["difference_ci95"]
    return f"{sm['difference']:+.2f} ({lo:+.2f} to {hi:+.2f})"


def _vals(seen: dict) -> str:
    v = ", ".join(map(str, seen["values"])) or "n/a"
    return v + (f" ({seen['absent']} runs without)" if seen["absent"] else "")


def render(a: dict, cfg: dict) -> str:
    s = cfg["study"]
    sp = a["spend_usd"]
    pc = a["price_check"]
    lines = [
        f"# MCP savings study results ({a['phase']})",
        "",
        "Generated by `mcp_savings_analyze.py` from the runs' rows pulled from Honeycomb "
        "(dataset mcp-savings, docs/PULL_ROWS.md); do not edit by hand.",
        "",
    ]
    if not a["ok"]:
        lines += [
            "**NOT COMPLETE: these are not results.** "
            + ("The plan is not met (see below). " if not a["complete"] else "")
            + (f"{len(pc['mismatches'])} costs the price table does not reproduce." if pc["mismatches"] else ""),
            "",
        ]
    lines += [
        "## What was run",
        "",
        f"- {a['counted']} of {a['planned']} planned runs counted, from {a['root_spans']} root spans "
        f"of config {a['config_sha']}; {len(a['missing'])} without a valid run, "
        f"{len(a['duplicate_cells'])} with more than one, {len(a['duplicate_run_ids'])} run ids "
        f"sent more than once; {len(a['invalid'])} invalid and {len(a['aborted'])} aborted runs "
        "listed, not counted.",
        f"- Spend: {sp['all_runs']:.2f} USD over every run ({sp['counted']:.2f} counted, "
        f"{sp['invalid']:.2f} invalid, {sp['aborted']:.2f} aborted"
        + (f", {sp['aborted_without_cost']} aborted without a cost" if sp["aborted_without_cost"] else "")
        + ").",
        f"- Rows from {a['first_row']} to {a['last_row']}.",
        f"- Harness {_vals(a['harness_shas'])}; Claude Code {_vals(a['claude_cli'])}; "
        f"Kibana {_vals(a['kibana_versions'])}; mcp-for-kibana "
        + "; ".join(f"{arm} {_vals(v)}" for arm, v in a["server_versions"].items())
        + ".",
        *(
            [
                f"- {a['rescored_runs']} counted runs rescored (efficiency.rescore); "
                f"{len(a['rescore_changed'])} changed outcome"
                + (f": {', '.join(a['rescore_changed'])}." if a["rescore_changed"] else ".")
            ]
            if a["rescored_runs"]
            else []
        ),
        f"- Prices: {s['prices']['source']}. Recomputed from each run's token totals, they "
        + (
            f"do not reproduce the CLI's cost of {len(pc['mismatches'])} runs (listed below)."
            if pc["mismatches"]
            else f"reproduce the CLI's cost of every run ({pc['checked']} priced"
            + (
                f"; {len(pc['unchecked'])} invalid or aborted runs without a cost or tokens)."
                if pc["unchecked"]
                else ")."
            )
        ),
        f"- Medians with 95% percentile-bootstrap intervals ({a['bootstrap']['resamples']:,} "
        f"resamples, seed {a['bootstrap']['seed']}). A ratio is the median with the server ÷ the "
        "median without; below 1 means the server is cheaper or faster. Cost is as each run "
        "came: caches are not controlled.",
    ]
    if "ratios" in a:
        lines += [
            "",
            "## With the server ÷ without, per model",
            "",
            "A saving counts only where success holds. The pass-rate difference is with the "
            "server minus without, with a 95% bootstrap interval over blocks (each block "
            "pairs the two setups). Success holds unless the whole interval lies below 0; "
            "where it does not, the savings are shown but not counted.",
            "",
            "| Model | Cost ratio (95% interval) | Time ratio (95% interval) | Passed with / without "
            "| Pass-rate difference (95% interval) | Savings |",
            "|---|---|---|---|---|---|",
        ]
        for model in s["models"]:
            r = a["ratios"].get(model, {})
            sm = a["success_by_model"][model]
            lines.append(
                f"| {_name(model)} | {_iv(r.get('cost_usd'), 'ratio', '.2f')} | "
                f"{_iv(r.get('time_s'), 'ratio', '.2f')} | "
                f"{sm['passed_with']} of {sm['runs_with']} / {sm['passed_without']} of {sm['runs_without']} | "
                f"{_diff(sm)} | {'counted' if sm['holds'] else 'not counted'} |"
            )
        lines += [
            "",
            "## Per model and setup",
            "",
            "| Model | Setup | Cost per run (USD) | Time per run (s) | Tool calls per run "
            "| Failed tool calls per run | Passed |",
            "|---|---|---|---|---|---|---|",
        ]
        for model in s["models"]:
            for arm in s["arms"]:
                key = f"{model}/{arm}"
                m = a["per_model_arm"].get(key, {})
                sc = a["success"][key]
                lines.append(
                    f"| {_name(model)} | {arm} | {_iv(m.get('cost_usd'), 'median', '.4f')} | "
                    f"{_iv(m.get('time_s'), 'median', '.1f')} | {_iv(m.get('tool_calls'), 'median', 'g')} | "
                    f"{_iv(m.get('failed_tool_calls'), 'median', 'g')} | {sc['passed']} of {sc['runs']} |"
                )
        lines += [
            "",
            "A tool call is one call the agent made: one MCP tool call, or one shell command, "
            "which can bundle several Kibana requests.",
        ]
    if pc["mismatches"]:
        lines += ["", "## Costs the price table does not reproduce", "", "| Run | CLI cost | Priced |", "|---|---|---|"]
        lines += [f"| `{m['run_id']}` | {m['cost_usd']} | {m['priced_usd']} |" for m in pc["mismatches"]]
    if a["missing"]:
        lines += ["", "## Planned runs without a valid run", ""]
        lines += [f"- {b} {m} {arm}" for b, m, arm in a["missing"]]
    if a["duplicate_cells"]:
        lines += ["", "## Planned runs with more than one valid span", ""]
        lines += [f"- {' '.join(d['cell'])}: {', '.join(d['run_ids'])}" for d in a["duplicate_cells"]]
    if a["duplicate_run_ids"]:
        lines += ["", "## Run ids on more than one root span", ""]
        lines += [f"- `{k}`" for k in a["duplicate_run_ids"]]
    if a["invalid"]:
        lines += ["", "## Invalid runs", "", "| Run | Cause | Cost |", "|---|---|---|"]
        lines += [f"| `{i['run_id']}` | {i['reason']} | {i['cost_usd']} |" for i in a["invalid"]]
    if a["aborted"]:
        lines += ["", "## Aborted runs", "", "| Run | Cost so far |", "|---|---|"]
        lines += [f"| `{x['run_id']}` | {x['cost_usd']} |" for x in a["aborted"]]
    if a["outside_plan"]:
        lines += ["", "## Valid runs outside the plan", ""]
        lines += [f"- `{x['run_id']}` {' '.join(map(str, x['cell']))}" for x in a["outside_plan"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI


def _cmd_rows(args) -> int:
    pages = [json.loads(Path(p).read_text()) for p in args.pages]
    rows = merge_pages(pages)
    Path(args.out).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    names = Counter(r.get("name") for r in rows)
    print(f"{len(rows)} rows from {len(pages)} pages ({dict(names)}); wrote {args.out}")
    return 0


def _cmd_columns(args) -> int:
    print(json.dumps(ROOT_COLUMNS if args.span == "run" else RESCORE_COLUMNS))
    return 0


def _cmd_analyze(args) -> int:
    cfg = ms.load()
    sha = args.config_sha or ms.config_sha()
    rows = load_rows(Path(args.rows))
    try:
        a = analyze(
            cfg,
            args.phase,
            rows,
            sha,
            allow_incomplete=args.allow_incomplete,
            harness_sha=args.harness_sha,
        )
    except HarnessMix as e:
        print(f"FAIL: {e}")
        return 2
    problems = []
    if args.audit_runs:
        runs, _ = split(rows, sha, args.phase, args.harness_sha)
        a["audit"] = audit(runs, load_records(Path(args.audit_runs)))
        problems += a["audit"]
        a["ok"] = a["ok"] and not a["audit"]
    out = Path(args.out) if args.out else ms.OUT / args.phase
    out.mkdir(parents=True, exist_ok=True)
    ms.write_clean(out / "analysis.json", json.dumps(a, indent=2))
    ms.write_clean(out / "RESULTS.md", render(a, cfg))
    pc = a["price_check"]
    print(
        f"{a['counted']}/{a['planned']} planned runs counted from {a['root_spans']} root spans; "
        f"{len(a['missing'])} missing, {len(a['duplicate_cells'])} duplicate cells, "
        f"{len(a['duplicate_run_ids'])} duplicate run ids, {len(a['invalid'])} invalid, "
        f"{len(a['aborted'])} aborted; price check {pc['checked']} priced, "
        f"{len(pc['mismatches'])} mismatches; wrote {out / 'analysis.json'} and {out / 'RESULTS.md'}"
    )
    if problems:
        print(f"AUDIT: {len(problems)} mismatches with the local records, e.g. {problems[0]}")
    if not a["ok"]:
        print("NOT COMPLETE: no number from this analysis is a result")
    return 0 if a["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rows", help="merge downloaded Honeycomb result pages into a rows file")
    r.add_argument("--out", required=True, help="the rows file to write (JSON lines)")
    r.add_argument("pages", nargs="+", help="query_result_json files, one per run_query page")
    r.set_defaults(fn=_cmd_rows)
    c = sub.add_parser("columns", help="print the pull's raw_row_columns")
    c.add_argument("--span", choices=["run", "rescore"], default="run")
    c.set_defaults(fn=_cmd_columns)
    an = sub.add_parser("analyze", help="the confirmatory analysis of a rows file")
    an.add_argument("--phase", choices=["study", "pilot"], required=True)
    an.add_argument("--rows", required=True, help="the rows file (JSON lines)")
    an.add_argument("--config-sha", help="default: the sha of mcp_savings.json as it stands")
    an.add_argument("--harness-sha", help=HARNESS_SHA_HELP)
    an.add_argument("--out", help="output folder (default runs/mcp-savings/<phase>/)")
    an.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="compute the statistics even when the plan is not met (a machinery check, not results)",
    )
    an.add_argument("--audit-runs", help="a phase's run folders: compare their records with the rows")
    an.set_defaults(fn=_cmd_analyze)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
