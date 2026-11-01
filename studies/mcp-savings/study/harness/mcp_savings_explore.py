#!/usr/bin/env python3
"""MCP savings study: the exploratory look, labelled so, beside the
confirmatory comparison of mcp_savings_analyze.py.

    uv run python scripts/experiment/mcp_savings_explore.py --phase study --rows rows.jsonl

Reads the same rows file pulled from Honeycomb (docs/PULL_ROWS.md) and the
same counted runs, rescores applied, and writes exploratory.json and
EXPLORATORY.md beside the analysis. Nothing here was fixed before the runs:

- Time as the CLI measures it (study.duration_ms) next to the runner's
  study.wall_s, with the with ÷ without ratio per model, and every run whose
  wall_s exceeds the CLI's duration by more than two minutes (the agent had
  finished; the CLI was still waiting, e.g. on a background shell the model
  left behind).
- The cause of every failed run, from its failure reason and first failed
  step.
- Tokens, turns, and calls per tool family per model and setup.
- With --spans: where the time goes. Per run, the summed duration of the
  CLI's claude_code.tool.execution spans (tool time), of its
  claude_code.llm_request spans (model time), and the rest of wall_s; per
  model and setup the median of each, and the median single tool call by
  tool family (MCP tool, Bash, other), each with the analysis's 95%
  percentile-bootstrap interval. The spans come from the runs' traces in
  Honeycomb (docs/PULL_ROWS.md, "The CLI spans"):

    uv run python scripts/experiment/mcp_savings_explore.py spans --out spans.jsonl pull/page-*.json

- With --min-reads (and --pilot-rows): the first run of each model and setup.
  Per cell the earliest run by start time across all workers (they share one
  account, hence one prompt cache), its cost, time and cache tokens, and
  whether it started cold: its lowest cache read of any model request
  (claude_code.llm_request, one aggregate query: MIN(cache_read_tokens) by
  study.run_id) is at the cell's lowest level, the part of the prompt every
  run reads (0, or a prefix shared with other prompts of the account), while
  later runs read the whole cached prefix. Then per model the cold ratio
  (first with ÷ first without) beside the warm one (median ÷ median), and the
  gap between the cell's last pilot run and its first study run. One run per
  cell: it shows where to look, not a result. The two inputs are downloaded
  query results (docs/results/study/cache/).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mcp_savings as ms  # noqa: E402  (sibling script)
import mcp_savings_analyze as ana  # noqa: E402

WAIT_S = 120  # wall_s beyond the CLI's own duration that counts as a wait
COUNTS = {
    "input_tokens_total": ("input_tokens", "cache_read_tokens", "cache_write_tokens"),
    "output_tokens": ("output_tokens",),
    "turns": ("turns",),
    "mcp_calls": ("mcp_calls",),
    "bash_calls": ("bash_calls",),
    "failed_tool_calls": ("failed_tool_calls",),
}


# The span pull (docs/PULL_ROWS.md, "The CLI spans") joins these columns with
# "|" into one calculated column ``r``, so that a page holds 500 spans.
SPAN_FIELDS = ("name", "study.run_id", "tool_use_id", "duration_ms", "tool_name", "trace.trace_id")
TOOL_EXEC = "claude_code.tool.execution"
TOOL = "claude_code.tool"
LLM = "claude_code.llm_request"
FAMILIES = ("mcp", "bash", "other")
CLOCK_FIELDS = (
    "model_s",  # summed llm_request durations
    "tool_s",  # summed tool.execution durations
    "rest_s",  # wall_s - model_s - tool_s (below the truth by the overlap)
    "model_clock_s",  # the union of the llm_request spans
    "tool_outside_model_s",  # tool execution not under any llm_request
    "idle_s",  # wall_s - the union of both: model_clock + tool_outside + idle = wall
)


def merge_span_pages(pages: list[dict]) -> list[dict]:
    """The spans of downloaded Honeycomb raw-row pages, one object per span:
    SPAN_FIELDS (split from the joined column ``r``, or taken as columns),
    ``time`` (the span's start) and ``row_id``. Empty fields are left out;
    duration_ms is a number. A row seen twice (same row_id) is kept once."""
    out, seen = [], set()
    for page in pages:
        for r in page.get("results") or []:
            rid = r.get("row_id")
            if rid is not None and rid in seen:
                continue
            seen.add(rid)
            data = r.get("data") or {}
            if "r" in data:
                parts = str(data["r"]).split("|")
                if len(parts) != len(SPAN_FIELDS):
                    raise ValueError(f"row {rid}: {len(parts)} fields, expected {len(SPAN_FIELDS)}")
                data = dict(zip(SPAN_FIELDS, parts))
            row = {k: data[k] for k in SPAN_FIELDS if data.get(k) not in (None, "")}
            if "duration_ms" in row:
                row["duration_ms"] = float(row["duration_ms"])
            out.append({**row, "time": r.get("time"), "row_id": rid})
    return sorted(out, key=lambda r: (r.get("time") or "", r.get("row_id") or ""))


def _family(tool_name: str | None) -> str:
    if tool_name and tool_name.startswith("mcp__"):
        return "mcp"
    if tool_name == "Bash":
        return "bash"
    return "other"


def _epoch_ms(t: str) -> float:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000


def _union_ms(intervals: list[tuple[float, float]]) -> float:
    total, end = 0.0, None
    for a, b in sorted(intervals):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def time_breakdown(cfg: dict, runs: list[dict], spans: list[dict]) -> dict:
    """Where each counted run's time goes, from its CLI spans. Spans attach
    to a run by study.run_id (every CLI span carries it, also the ones whose
    trace context was lost and that start a trace of their own); spans of
    runs that do not count, or without a run id, are left out and counted."""
    s = cfg["study"]
    seed, n = s["analysis_seed"], s["resamples"]
    by_id = {r["run_id"]: r for r in runs}
    kept = [x for x in spans if x.get("name") in (TOOL_EXEC, TOOL, LLM)]
    attached = [x for x in kept if x.get("study.run_id") in by_id]
    left_out = [x for x in kept if x.get("study.run_id") not in by_id]
    names = {}
    for x in attached:
        if x["name"] == TOOL and x.get("tool_use_id"):
            names[x["tool_use_id"]] = x.get("tool_name")
    per_run: dict[str, dict] = {
        rid: {"tool_ms": 0.0, "model_ms": 0.0, "tool_calls": 0, "llm_requests": 0, "iv": [], "llm_iv": []}
        for rid in by_id
    }
    calls: dict[str, list[float]] = {}
    unnamed, other_trace = 0, 0
    for x in attached:
        r, pr = by_id[x["study.run_id"]], per_run[x["study.run_id"]]
        if r.get("trace_id") and x.get("trace.trace_id") != r["trace_id"]:
            other_trace += 1
        if x["name"] == TOOL:
            continue
        d = x.get("duration_ms") or 0.0
        if x.get("time"):
            a = _epoch_ms(x["time"])
            pr["iv"].append((a, a + d))
            if x["name"] == LLM:
                pr["llm_iv"].append((a, a + d))
        if x["name"] == LLM:
            pr["model_ms"] += d
            pr["llm_requests"] += 1
        else:
            pr["tool_ms"] += d
            pr["tool_calls"] += 1
            if x.get("tool_use_id") not in names:
                unnamed += 1
            fam = _family(names.get(x.get("tool_use_id")))
            calls.setdefault(f"{r['model']}/{r['arm']}/{fam}", []).append(d)
    for rid, pr in per_run.items():
        r = by_id[rid]
        pr["tool_s"] = pr["tool_ms"] / 1000
        pr["model_s"] = pr["model_ms"] / 1000
        busy, llm_busy = _union_ms(pr.pop("iv")), _union_ms(pr.pop("llm_iv"))
        pr["overlap_s"] = (pr["tool_ms"] + pr["model_ms"] - busy) / 1000
        # On the clock: the model's requests, tool time outside them (the CLI
        # starts a tool while the response that asked for it still streams),
        # and the rest. The three add up to wall_s.
        pr["model_clock_s"] = llm_busy / 1000
        pr["tool_outside_model_s"] = (busy - llm_busy) / 1000
        wall = r.get("time_s")
        pr["rest_s"] = wall - pr["tool_s"] - pr["model_s"] if wall is not None else None
        pr["idle_s"] = wall - busy / 1000 if wall is not None else None
    groups, single = {}, {}
    for model in s["models"]:
        for arm in s["arms"]:
            key = f"{model}/{arm}"
            ids = [rid for rid, r in by_id.items() if r["model"] == model and r["arm"] == arm]
            g = {}
            for f in CLOCK_FIELDS:
                v = [per_run[i][f] for i in ids if per_run[i][f] is not None]
                if v:
                    g[f] = ana.median_ci(v, ana._rng(seed, model, arm, "explore", f), n)
            wall = [by_id[i]["time_s"] for i in ids if by_id[i].get("time_s") is not None]
            if wall:
                g["wall_s"] = ana.median_ci(wall, ana._rng(seed, model, arm, "explore", "wall_s"), n)
            ov = [per_run[i]["overlap_s"] for i in ids]
            g["overlap_s_median"] = round(statistics.median(ov), 3) if ov else None
            g["overlap_s_max"] = round(max(ov), 3) if ov else None
            g["runs_with_overlap"] = sum(1 for o in ov if o > 0.001)
            timed = [i for i in ids if by_id[i].get("time_s")]
            g["share_of_wall_median"] = {
                f: statistics.median(per_run[i][f] / by_id[i]["time_s"] for i in timed)
                for f in ("model_s", "tool_s")
            } if timed else {}
            groups[key] = g
            for fam in FAMILIES:
                v = calls.get(f"{key}/{fam}")
                if v:
                    single[f"{key}/{fam}"] = ana.median_ci(
                        v, ana._rng(seed, model, arm, "explore", "call_ms", fam), n
                    )
    return {
        "label": "exploratory: not planned before the runs",
        "source": "claude_code.tool.execution (tool time, one span per tool call) and "
        "claude_code.llm_request (model time) spans of the counted runs' traces; rest = "
        "study.wall_s minus both",
        "spans": {
            "pulled": len(kept),
            "by_name": {k: sum(1 for x in kept if x["name"] == k) for k in (TOOL_EXEC, TOOL, LLM)},
            "attached": len(attached),
            "left_out": len(left_out),
            "left_out_runs": sorted({x.get("study.run_id") or "(none)" for x in left_out}),
            "attached_outside_run_trace": other_trace,
            "tool_executions_without_tool_name": unnamed,
        },
        "runs": len(by_id),
        "by_model_arm": groups,
        "single_call_ms": single,
        "call_bootstrap_note": "single-call intervals resample calls as if independent; "
        "calls of one run are not, so these intervals are narrower than a run-level one",
    }


CACHE_LIFE_S = 300  # the prompt cache's default life without a read


def load_min_reads(page: dict) -> dict[str, float]:
    """study.run_id -> its lowest per-request cache read, from a downloaded
    aggregate result (MIN(cache_read_tokens) broken down by study.run_id)."""
    out = {}
    for r in page.get("results") or []:
        d = r.get("data") or {}
        rid, v = d.get("study.run_id"), d.get("MIN(cache_read_tokens)")
        if rid and v is not None:
            out[rid] = v
    return out


def load_pilot_runs(page: dict) -> list[dict]:
    """The pilot's root spans from a downloaded raw-row page: run id, model,
    setup, start and wall_s; aborted runs left out."""
    out = []
    for r in page.get("results") or []:
        d = r.get("data") or {}
        if d.get("study.aborted"):
            continue
        out.append(
            {
                "run_id": d.get("study.run_id"),
                "model": d.get("study.model_requested"),
                "arm": d.get("study.arm"),
                "time_s": d.get("study.wall_s"),
                "time": r.get("time"),
            }
        )
    return out


def _end(r: dict) -> float:
    return _epoch_ms(r["time"]) / 1000 + (r.get("time_s") or 0)


def cold_start(
    cfg: dict, runs: list[dict], min_reads: dict[str, float], pilot: list[dict] | None = None
) -> dict:
    """The first run of each model and setup against the cell's other runs,
    and the cold (first ÷ first) and warm (median ÷ median) ratios."""
    s = cfg["study"]
    cells, ratios = {}, {}
    for model in s["models"]:
        for arm in s["arms"]:
            group = sorted(
                (r for r in runs if r["model"] == model and r["arm"] == arm and r.get("time")),
                key=lambda r: (_epoch_ms(r["time"]), r["run_id"]),
            )
            if not group:
                continue
            first, rest = group[0], group[1:]
            reads = [min_reads[r["run_id"]] for r in group if r["run_id"] in min_reads]
            fr = min_reads.get(first["run_id"])
            shared = min(reads) if reads else None
            rest_reads = [min_reads[r["run_id"]] for r in rest if r["run_id"] in min_reads]
            warm_level = statistics.median(rest_reads) if rest_reads else None
            # Cold: the first run's least-read request read no more than the
            # cell's floor, below what the other runs typically read.
            cold = (
                None
                if fr is None or warm_level is None
                else fr <= shared and fr < warm_level
            )
            at_floor = [r["run_id"] for r in rest if min_reads.get(r["run_id"]) == shared]
            cell = {
                "first": {
                    k: first.get(k)
                    for k in (
                        "run_id",
                        "worker",
                        "block",
                        "time",
                        "cost_usd",
                        "time_s",
                        "cache_read_tokens",
                        "cache_write_tokens",
                    )
                },
                "first_min_request_cache_read": fr,
                "cell_floor_request_cache_read": shared,
                "others_median_min_request_cache_read": warm_level,
                "others_at_floor": at_floor,
                "cold": cold,
                "n": len(group),
                "median": {
                    f: statistics.median(v)
                    for f in ("cost_usd", "time_s", "cache_read_tokens", "cache_write_tokens")
                    if (v := [r[f] for r in group if r.get(f) is not None])
                },
            }
            if pilot is not None:
                prior = [p for p in pilot if p["model"] == model and p["arm"] == arm and p.get("time")]
                if prior:
                    last = max(prior, key=_end)
                    gap = _epoch_ms(first["time"]) / 1000 - _end(last)
                    cell["pilot_last"] = {"run_id": last["run_id"], "time": last["time"], "time_s": last.get("time_s")}
                    cell["gap_after_pilot_s"] = round(gap, 1)
                    cell["gap_exceeds_cache_life"] = gap > CACHE_LIFE_S
            cells[f"{model}/{arm}"] = cell
        w, o = cells.get(f"{model}/{ana.WITH}"), cells.get(f"{model}/{ana.WITHOUT}")
        if w and o:
            ratios[model] = {
                f"{kind}_{f}": (a / b if b else None)
                for f in ("cost_usd", "time_s")
                for kind, a, b in (
                    ("cold", w["first"][f], o["first"][f]),
                    ("warm", w["median"][f], o["median"][f]),
                )
            }
            ratios[model]["both_first_runs_cold"] = bool(w["cold"] and o["cold"])
    return {
        "label": "exploratory: n = 1 per cell, not planned before the runs",
        "source": "the counted efficiency.run root spans (first run = earliest start, all "
        "workers); per-request cache reads from claude_code.llm_request spans "
        "(MIN(cache_read_tokens) by study.run_id); the pilot's root spans for the gap",
        "cache_life_s": CACHE_LIFE_S,
        "cells": cells,
        "ratios": ratios,
    }


def failure_cause(r: dict) -> str:
    """A short cause for a failed run, from its failure reason and step."""
    reason = r.get("failure_reason") or ""
    step = r.get("first_failed_step")
    if r.get("timed_out"):
        return "timed out"
    if step == "data_views" and "is not a date field" in reason:
        return "a data view's time field is not a date field of the index"
    if step == "change" and "dashboard.one" in reason:
        return "the dashboard was rebuilt as a second one, not changed in place"
    return reason[:160] or "no failure reason on the span"


def _sum(r: dict, fields: tuple[str, ...]) -> float | None:
    vals = [r.get(f) for f in fields]
    return None if any(v is None for v in vals) else sum(vals)


def explore(
    cfg: dict,
    phase: str,
    runs: list[dict],
    spans: list[dict] | None = None,
    min_reads: dict[str, float] | None = None,
    pilot: list[dict] | None = None,
) -> dict:
    """The exploratory look over the counted runs (mcp_savings_analyze.select)."""
    s = cfg["study"]
    seed, n = s["analysis_seed"], s["resamples"]
    for r in runs:
        if r.get("cli_ms") is not None:
            r["cli_s"] = r["cli_ms"] / 1000
    cli_ratio, counts = {}, {}
    for model in s["models"]:
        w = ana._values(runs, model, ana.WITH, "cli_s")
        o = ana._values(runs, model, ana.WITHOUT, "cli_s")
        if len(w) >= 2 and len(o) >= 2:
            cli_ratio[model] = ana.ratio_ci(w, o, ana._rng(seed, model, "explore", "cli_s"), n)
        for arm in s["arms"]:
            group = [r for r in runs if r["model"] == model and r["arm"] == arm]
            counts[f"{model}/{arm}"] = {
                name: statistics.median(v)
                for name, fields in COUNTS.items()
                if (v := [x for r in group if (x := _sum(r, fields)) is not None])
            }
    waits = sorted(
        (
            {
                "run_id": r["run_id"],
                "model": r["model"],
                "arm": r["arm"],
                "wall_s": r["time_s"],
                "cli_s": round(r["cli_s"], 1),
            }
            for r in runs
            if "cli_s" in r and r.get("time_s") is not None and r["time_s"] - r["cli_s"] > WAIT_S
        ),
        key=lambda x: x["run_id"],
    )
    failures = [
        {
            "run_id": r["run_id"],
            "model": r["model"],
            "arm": r["arm"],
            "first_failed_step": r.get("first_failed_step"),
            "cause": failure_cause(r),
        }
        for r in sorted(runs, key=lambda r: r["run_id"])
        if not r.get("success")
    ]
    out = {
        "phase": phase,
        "label": "exploratory: not fixed before the runs",
        "counted": len(runs),
        "time_ratio_cli": cli_ratio,
        "waits_over_s": WAIT_S,
        "waits": waits,
        "failures": failures,
        "medians": counts,
    }
    if spans is not None:
        out["time_breakdown"] = time_breakdown(cfg, runs, spans)
    if min_reads is not None:
        out["cold_start"] = cold_start(cfg, runs, min_reads, pilot)
    return out


def render(x: dict, cfg: dict) -> str:
    s = cfg["study"]
    name = ana._name
    lines = [
        f"# MCP savings study: exploratory ({x['phase']})",
        "",
        "Generated by `mcp_savings_explore.py` from the runs' rows pulled from Honeycomb; "
        "do not edit by hand. Nothing on this page was fixed before the runs: the "
        "confirmatory comparison is RESULTS.md beside it.",
        "",
        "## Time as the CLI measures it",
        "",
        "With ÷ without per model, using the CLI's own `duration_ms` instead of the "
        "runner's `wall_s` (95% bootstrap interval, the plan's seed).",
        "",
        "| Model | Ratio (95% interval) |",
        "|---|---|",
    ]
    for model in s["models"]:
        r = x["time_ratio_cli"].get(model)
        if r:
            lo, hi = r["ratio_ci95"]
            lines.append(f"| {name(model)} | {r['ratio']:.2f} ({lo:.2f}–{hi:.2f}) |")
    lines += [
        "",
        f"Runs whose `wall_s` exceeds the CLI's duration by more than {x['waits_over_s']} s "
        f"({len(x['waits'])}):",
        "",
        "| Run | Model | Setup | wall_s | CLI s |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| `{w['run_id']}` | {name(w['model'])} | {w['arm']} | {w['wall_s']} | {w['cli_s']} |"
        for w in x["waits"]
    ]
    lines += [
        "",
        f"## Failed runs ({len(x['failures'])})",
        "",
        "| Run | Model | Setup | Step | Cause |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| `{f['run_id']}` | {name(f['model'])} | {f['arm']} | {f['first_failed_step']} | {f['cause']} |"
        for f in x["failures"]
    ]
    lines += [
        "",
        "## Medians per run",
        "",
        "| Model | Setup | Input tokens (incl. cache) | Output tokens | Turns | MCP calls "
        "| Bash calls | Failed tool calls |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model in s["models"]:
        for arm in s["arms"]:
            m = x["medians"].get(f"{model}/{arm}", {})
            lines.append(
                f"| {name(model)} | {arm} | {m.get('input_tokens_total', 0):,.0f} | "
                f"{m.get('output_tokens', 0):,.0f} | {m.get('turns', 0):g} | "
                f"{m.get('mcp_calls', 0):g} | {m.get('bash_calls', 0):g} | "
                f"{m.get('failed_tool_calls', 0):g} |"
            )
    if "time_breakdown" in x:
        lines += render_time(x["time_breakdown"], cfg)
    if "cold_start" in x:
        lines += render_cold(x["cold_start"], cfg)
    return "\n".join(lines) + "\n"


def _ci(m: dict | None, fmt: str) -> str:
    if not m:
        return "–"
    lo, hi = m["median_ci95"]
    return f"{m['median']:{fmt}} ({lo:{fmt}}–{hi:{fmt}})"


def render_time(t: dict, cfg: dict) -> list[str]:
    s, name, sp = cfg["study"], ana._name, t["spans"]
    lines = [
        "",
        "## Where the time goes (exploratory, not planned before the runs)",
        "",
        f"From the CLI's spans in each counted run's trace: {sp['by_name'][TOOL_EXEC]:,} "
        f"`{TOOL_EXEC}` (one per tool call), {sp['by_name'][LLM]:,} `{LLM}` and "
        f"{sp['by_name'][TOOL]:,} `{TOOL}` (for the tool's name). {sp['attached']:,} of "
        f"{sp['pulled']:,} spans attach to a counted run by `study.run_id`; "
        f"{sp['left_out']:,} are left out (runs that do not count: "
        f"{', '.join(f'`{r}`' for r in sp['left_out_runs']) or 'none'}). "
        f"{sp['attached_outside_run_trace']} attached spans sit in a trace other than their "
        "run's root span (the CLI lost the trace context); their run id attaches them. "
        f"Tool executions without a tool name: {sp['tool_executions_without_tool_name']}.",
        "",
        "Per run: model time = sum of `llm_request` durations, tool time = sum of "
        "`tool.execution` durations, rest = `study.wall_s` minus both (CLI start-up, "
        "the harness's own steps, gaps between steps). Median per model and setup in "
        "seconds (95% bootstrap interval, the plan's seed and resamples).",
        "",
        "| Model | Setup | Wall | Model time | Tool time | Rest | Model share | Tool share "
        "| Overlap, median (max) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for model in s["models"]:
        for arm in s["arms"]:
            g = t["by_model_arm"].get(f"{model}/{arm}", {})
            sh = g.get("share_of_wall_median", {})
            lines.append(
                f"| {name(model)} | {arm} | {_ci(g.get('wall_s'), '.1f')} | "
                f"{_ci(g.get('model_s'), '.1f')} | {_ci(g.get('tool_s'), '.1f')} | "
                f"{_ci(g.get('rest_s'), '.1f')} | {sh.get('model_s', 0):.0%} | "
                f"{sh.get('tool_s', 0):.0%} | {g.get('overlap_s_median') or 0:.2f} "
                f"({g.get('overlap_s_max') or 0:.2f}) |"
            )
    lines += [
        "",
        "Overlap: the CLI starts a tool as soon as the model has written the call, "
        "while the rest of the response still streams, so tool and model spans run "
        "at the same time (overlap = summed durations minus their union on the "
        "clock; requests never overlap requests). Rest above is low by that much "
        "and can go below zero. On the clock, without double counting (model "
        "requests + tool time outside them + idle = wall):",
        "",
        "| Model | Setup | Model requests | Tool outside model | Idle |",
        "|---|---|---|---|---|",
    ]
    for model in s["models"]:
        for arm in s["arms"]:
            g = t["by_model_arm"].get(f"{model}/{arm}", {})
            lines.append(
                f"| {name(model)} | {arm} | {_ci(g.get('model_clock_s'), '.1f')} | "
                f"{_ci(g.get('tool_outside_model_s'), '.1f')} | {_ci(g.get('idle_s'), '.1f')} |"
            )
    lines += [
        "",
        "Median duration of one tool call, ms (95% bootstrap interval over calls; "
        + t["call_bootstrap_note"]
        + "):",
        "",
        "| Model | Setup | MCP tool call (n) | Bash command (n) | Other tool (n) |",
        "|---|---|---|---|---|",
    ]
    for model in s["models"]:
        for arm in s["arms"]:
            cells = []
            for fam in FAMILIES:
                m = t["single_call_ms"].get(f"{model}/{arm}/{fam}")
                cells.append(f"{_ci(m, '.0f')} ({m['n']})" if m else "–")
            lines.append(f"| {name(model)} | {arm} | " + " | ".join(cells) + " |")
    return lines


def render_cold(c: dict, cfg: dict) -> list[str]:
    s, name = cfg["study"], ana._name
    lines = [
        "",
        "## The first run of each model and setup (exploratory, n = 1 per cell)",
        "",
        "The earliest run by start time across all six workers (one account, one prompt "
        "cache). Cold = its least-read model request read no more than the cell's floor "
        "(the lowest per-request cache read of any run in the cell: 0, or a prefix shared "
        "with other prompts of the account), while the cell's other runs read the whole "
        "cached prompt. Cache tokens are the run's totals over all its requests.",
        "",
        "| Model | Setup | First run | Cost $ | Time s | Cache read | Cache write "
        "| Least request read (cell floor; others' median) | Cold | Gap after pilot s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model in s["models"]:
        for arm in s["arms"]:
            g = c["cells"].get(f"{model}/{arm}")
            if not g:
                continue
            f = g["first"]
            cold = {True: "yes", False: "no", None: "unknown"}[g["cold"]]
            gap = g.get("gap_after_pilot_s")
            lines.append(
                f"| {name(model)} | {arm} | `{f['run_id']}` (worker {f['worker']}) | "
                f"{f['cost_usd']:.4f} | {f['time_s']:.1f} | {f['cache_read_tokens']:,.0f} | "
                f"{f['cache_write_tokens']:,.0f} | {g['first_min_request_cache_read']:,.0f} "
                f"({g['cell_floor_request_cache_read']:,.0f}; "
                f"{g['others_median_min_request_cache_read']:,.0f}) | {cold} | "
                f"{'–' if gap is None else f'{gap:,.0f}'} |"
            )
    lines += [
        "",
        "With ÷ without per model: cold = first run ÷ first run, warm = median ÷ median "
        "(the 100 runs of each cell).",
        "",
        "| Model | Cost, cold | Cost, warm | Time, cold | Time, warm | Both first runs cold |",
        "|---|---|---|---|---|---|",
    ]
    for model in s["models"]:
        r = c["ratios"].get(model)
        if r:
            lines.append(
                f"| {name(model)} | {r['cold_cost_usd']:.2f} | {r['warm_cost_usd']:.2f} | "
                f"{r['cold_time_s']:.2f} | {r['warm_time_s']:.2f} | "
                f"{'yes' if r['both_first_runs_cold'] else 'no'} |"
            )
    return lines


def spans_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="mcp_savings_explore.py spans",
        description="merge downloaded span pages into one spans file (JSON lines)",
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("pages", nargs="+")
    args = ap.parse_args(argv)
    spans = merge_span_pages([json.loads(Path(p).read_text()) for p in args.pages])
    Path(args.out).write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in spans))
    by = {}
    for x in spans:
        by[x.get("name")] = by.get(x.get("name"), 0) + 1
    print(f"{len(spans)} spans: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["spans"]:
        return spans_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--phase", choices=["study", "pilot"], required=True)
    ap.add_argument("--rows", required=True, help="the rows file (JSON lines)")
    ap.add_argument("--config-sha", help="default: the sha of mcp_savings.json as it stands")
    ap.add_argument("--harness-sha", help=ana.HARNESS_SHA_HELP)
    ap.add_argument("--out", help="output folder (default runs/mcp-savings/<phase>/)")
    ap.add_argument("--spans", help="the spans file (`spans` merges it): adds where the time goes")
    ap.add_argument(
        "--min-reads",
        help="downloaded aggregate result, MIN(cache_read_tokens) of claude_code.llm_request "
        "by study.run_id: adds the first run of each model and setup",
    )
    ap.add_argument("--pilot-rows", help="downloaded raw-row page of the pilot's root spans (with --min-reads)")
    args = ap.parse_args(argv)
    cfg = ms.load()
    rows = ana.load_rows(Path(args.rows))
    try:
        prep = ana.prepare(
            cfg, args.phase, rows, args.config_sha or ms.config_sha(), args.harness_sha
        )
    except ana.HarnessMix as e:
        print(f"FAIL: {e}")
        return 2
    if not ana.complete(prep["selection"]):
        print("WARNING: the plan is not met (mcp_savings_analyze.py lists why); exploring the runs that count")
    spans = ana.load_rows(Path(args.spans)) if args.spans else None
    min_reads = load_min_reads(json.loads(Path(args.min_reads).read_text())) if args.min_reads else None
    pilot = load_pilot_runs(json.loads(Path(args.pilot_rows).read_text())) if args.pilot_rows else None
    x = explore(cfg, args.phase, prep["selection"]["counted"], spans, min_reads, pilot)
    out = Path(args.out) if args.out else ms.OUT / args.phase
    out.mkdir(parents=True, exist_ok=True)
    ms.write_clean(out / "exploratory.json", json.dumps(x, indent=2))
    ms.write_clean(out / "EXPLORATORY.md", render(x, cfg))
    print(
        f"exploratory over {x['counted']} counted runs: {len(x['failures'])} failed, "
        f"{len(x['waits'])} waits; wrote {out / 'EXPLORATORY.md'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
