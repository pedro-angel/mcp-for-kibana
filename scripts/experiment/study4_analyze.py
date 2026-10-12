#!/usr/bin/env python3
"""Study 4's analysis: cost and time per run, with and without the server.

    uv run python scripts/experiment/study4_analyze.py \
        [--runs scripts/experiment/runs/efficiency/study4/full] [--out analysis.json] \
        [--report scripts/experiment/STUDY4_RESULTS.md]

Reads every run's record.json under --runs (efficiency_run.py --task) and
writes analysis.json beside them:

- per model x arm x task: n, pass count, and the distribution (median,
  quartiles, mean, min, max) of cost (total_cost_usd, list price) and time
  (wall_s, the agent's wall-clock time as the runner measured it), each
  median with a 95% percentile-bootstrap interval;
- per model x task: with / without, the ratio of medians with its interval
  (efficiency_analyze.compare);
- per model: the geometric mean of its four per-task ratios, with an
  interval from resampling the runs of every cell, so each task weighs the
  same however much its runs cost.

It also writes the results page (--report) from analysis.json and, when
present, honeycomb_crosscheck.json beside the runs: every number on the page
comes from those two files. Only valid runs count (no permission denials). Deterministic: fixed seed,
sorted inputs, sorted keys. STUDY_DESIGN.md decision 18.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import efficiency_analyze as ea  # noqa: E402  (sibling script)

DEFAULT_RUNS = HERE / "runs" / "efficiency" / "study4" / "full"
SEED = 20261012
ARMS = ("with-mcp", "no-mcp")
METRICS = {"cost_usd": "total_cost_usd", "time_s": "wall_s"}


def load(runs_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(runs_dir.glob("b*/*/record.json")):
        r = json.loads(path.read_text())
        rows.append({
            "run_id": r["run_id"],
            "block": r["block"],
            "model": r["model_requested"],
            "arm": r["arm"],
            "task": r["task"],
            "valid": bool(r.get("valid")),
            "success": bool(r.get("success")),
            "cost_usd": r.get("total_cost_usd"),
            "time_s": r.get("wall_s"),
            "git_sha": r.get("git_sha"),
            "tasks_sha": r.get("tasks_sha"),
            "claude_cli": r.get("claude_cli"),
            "kibana_version": r.get("kibana_version"),
            "toolboxes": r.get("toolboxes"),
            "ts_start": r.get("ts_start"),
        })
    return rows


def median_ci(rng: random.Random, xs: list[float]) -> list[float] | None:
    if len(xs) < 2:
        return None
    return ea._ci([statistics.median(rng.choices(xs, k=len(xs))) for _ in range(ea.RESAMPLES)])


def geo_ratio(rng: random.Random, pairs: list[tuple[list[float], list[float]]]) -> dict:
    """Geometric mean over tasks of median(with) / median(without), and its
    percentile-bootstrap interval (every cell resampled independently)."""

    def gm(ps):
        logs = [math.log(statistics.median(w) / statistics.median(o)) for w, o in ps]
        return math.exp(statistics.fmean(logs))

    boots = []
    for _ in range(ea.RESAMPLES):
        boots.append(gm([(rng.choices(w, k=len(w)), rng.choices(o, k=len(o))) for w, o in pairs]))
    return {"ratio": gm(pairs), "ratio_ci95": ea._ci(boots), "tasks": len(pairs)}


def analyse(rows: list[dict]) -> dict:
    valid = [r for r in rows if r["valid"]]
    models = sorted({r["model"] for r in valid})
    tasks = sorted({r["task"] for r in valid})

    def values(model, arm, task, metric):
        return [r[metric] for r in valid
                if (r["model"], r["arm"], r["task"]) == (model, arm, task) and r[metric] is not None]

    rng = random.Random(SEED)
    cells, ratios, per_model = [], [], {}
    for model in models:
        for task in tasks:
            for arm in ARMS:
                group = [r for r in valid if (r["model"], r["arm"], r["task"]) == (model, arm, task)]
                cell = {"model": model, "task": task, "arm": arm, "n": len(group),
                        "passed": sum(r["success"] for r in group)}
                for metric in METRICS:
                    xs = values(model, arm, task, metric)
                    cell[metric] = {**ea.describe(xs), "median_ci95": median_ci(rng, xs)}
                cells.append(cell)
            for metric in METRICS:
                c = ea.compare(rng, [values(model, "with-mcp", task, metric)],
                               [values(model, "no-mcp", task, metric)])
                ratios.append({"model": model, "task": task, "metric": metric, **(c or {})})
        per_model[model] = {
            metric: geo_ratio(rng, [(values(model, "with-mcp", t, metric),
                                     values(model, "no-mcp", t, metric)) for t in tasks])
            for metric in METRICS
        }
    return {
        "runs": len(rows),
        "valid_runs": len(valid),
        "passed": sum(r["success"] for r in valid),
        "runner_commits": sorted({r["git_sha"] for r in rows}),
        "tasks_sha": sorted({r["tasks_sha"] for r in rows}),
        "claude_cli": sorted({r["claude_cli"] for r in rows}),
        "kibana_version": sorted({r["kibana_version"] for r in rows}),
        "toolboxes": sorted({r["toolboxes"] for r in rows if r["toolboxes"]}),
        "blocks": sorted({r["block"] for r in rows}),
        "first_start": min(r["ts_start"] for r in rows),
        "last_start": max(r["ts_start"] for r in rows),
        "metrics": METRICS,
        "bootstrap": {"seed": SEED, "resamples": ea.RESAMPLES},
        "cells": cells,
        "ratios": ratios,
        "per_model": per_model,
    }


def headline(a: dict) -> list[str]:
    lines = []
    for model, m in a["per_model"].items():
        parts = []
        for metric, label in (("cost_usd", "cost"), ("time_s", "time")):
            r = m[metric]
            lo, hi = r["ratio_ci95"]
            parts.append(f"{label} x{r['ratio']:.2f} ({lo:.2f}-{hi:.2f})")
        lines.append(f"{model}: with / without the server, {', '.join(parts)}")
    return lines


MODEL_NAMES = {"claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5",
               "claude-opus-5-5": "Opus 5.5"}
TASK_NAMES = {"weblogs": "Web-logs task", "flights": "Web-logs task on flights data",
              "change": "Change existing objects", "lookup": "Look up existing objects"}


def _money(v: float) -> str:
    return f"${v:.4f}" if v < 0.01 else f"${v:.3f}"


def _ratio(r: dict) -> str:
    lo, hi = r["ratio_ci95"]
    return f"{r['ratio']:.2f} ({lo:.2f}–{hi:.2f})"


def report(a: dict, crosscheck: dict | None) -> str:
    """The results page, every number taken from the analysis and the
    Honeycomb cross-check."""
    order = list(MODEL_NAMES)
    models = sorted(a["per_model"], key=lambda m: order.index(m) if m in order else len(order))
    tasks = sorted({c["task"] for c in a["cells"]}, key=list(TASK_NAMES).index)
    cell = {(c["model"], c["task"], c["arm"]): c for c in a["cells"]}
    ratio = {(r["model"], r["task"], r["metric"]): r for r in a["ratios"]}
    out = [
        "# Study 4 results: Claude Code with and without mcp-for-kibana",
        "",
        "Generated by `study4_analyze.py` from `runs/efficiency/study4/full/analysis.json`"
        + (" and `honeycomb_crosscheck.json`" if crosscheck else "")
        + "; do not edit by hand. Design: STUDY_DESIGN.md decisions 12, 14, 17 and 18.",
        "",
        "## What was run",
        "",
        f"- {a['runs']} runs: {len(tasks)} tasks × {len(models)} models × 2 arms × "
        f"{len(a['blocks'])} blocks, one run per cell per block, order rotated by block.",
        f"- Started {a['first_start'][:16].replace('T', ' ')} to "
        f"{a['last_start'][:16].replace('T', ' ')} UTC; runner commit "
        f"{', '.join(a['runner_commits'])}, task definitions {', '.join(a['tasks_sha'])}.",
        f"- Agent: {', '.join(a['claude_cli'])}, headless, with "
        + ", ".join(MODEL_NAMES.get(m, m) for m in models) + ".",
        f"- Kibana {', '.join(a['kibana_version'])} with the web-logs and flights sample data.",
        f"- With the server: mcp-for-kibana with the {', '.join(a['toolboxes'])} toolboxes "
        "at the write tier, the prompt preamble \"Use only the kibana tools\". Without: "
        "a shell and Kibana's API reference. Both arms keep the same built-in tools.",
        "- Tasks: " + "; ".join(TASK_NAMES[t] for t in tasks) + ".",
        "",
        "## What was measured",
        "",
        f"- Success: {a['passed']} of {a['valid_runs']} valid runs passed every check "
        f"({a['runs']} runs, {a['runs'] - a['valid_runs']} invalid).",
        "- Cost per run: Claude Code's list-price cost of the run (`total_cost_usd`).",
        "- Time per run: the agent's wall-clock time as the runner measured it (`wall_s`).",
        f"- Medians with 95% percentile-bootstrap intervals ({a['bootstrap']['resamples']:,} "
        f"resamples, seed {a['bootstrap']['seed']}). A ratio is median with the server ÷ "
        "median without; below 1 means the server is cheaper or faster.",
        "",
        "## With the server ÷ without, per model",
        "",
        "Geometric mean of the four per-task ratios, so each task weighs the same.",
        "",
        "| Model | Cost ratio (95% interval) | Time ratio (95% interval) |",
        "|---|---|---|",
    ]
    for m in models:
        pm = a["per_model"][m]
        out.append(f"| {MODEL_NAMES.get(m, m)} | {_ratio(pm['cost_usd'])} | {_ratio(pm['time_s'])} |")
    for metric, title, fmt in (("cost_usd", "Cost per run", _money),
                               ("time_s", "Time per run", lambda v: f"{v:.1f} s")):
        out += ["", f"## {title}, median per model and task", "",
                "| Model | Task | With the server | Without | Ratio (95% interval) |",
                "|---|---|---|---|---|"]
        for m in models:
            for t in tasks:
                w, o = cell[(m, t, "with-mcp")][metric], cell[(m, t, "no-mcp")][metric]
                out.append(f"| {MODEL_NAMES.get(m, m)} | {TASK_NAMES[t]} | {fmt(w['median'])} | "
                           f"{fmt(o['median'])} | {_ratio(ratio[(m, t, metric)])} |")
    if crosscheck:
        out += ["", "## Cross-check in Honeycomb", "",
                f"The same medians, read from the runner's root spans in Honeycomb "
                f"({crosscheck['source']}; query runs "
                f"{', '.join(crosscheck['query_run_pks'].values())}).", "",
                "| Model | Cost ratio: script | Honeycomb | Time ratio: script | Honeycomb |",
                "|---|---|---|---|---|"]
        for m in models:
            x = crosscheck["per_model"][m]
            out.append(f"| {MODEL_NAMES.get(m, m)} | {x['cost_usd']['ratio_script']:.3f} | "
                       f"{x['cost_usd']['ratio_honeycomb']:.3f} | {x['time_s']['ratio_script']:.3f} | "
                       f"{x['time_s']['ratio_honeycomb']:.3f} |")
        out += ["", f"- Root spans in Honeycomb: {crosscheck['root_spans_found']} of "
                f"{crosscheck['runs']}; {crosscheck['missing']}.",
                f"- {crosscheck['note']} Largest per-cell difference: "
                f"{crosscheck['largest_cell_difference_pct']}%; {crosscheck['difference_cause']}"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--report", type=Path, default=HERE / "STUDY4_RESULTS.md")
    args = ap.parse_args(argv)
    rows = load(args.runs)
    if not rows:
        raise SystemExit(f"FAIL: no record.json under {args.runs} — pass --runs <study dir>")
    a = analyse(rows)
    out = args.out or args.runs / "analysis.json"
    out.write_text(json.dumps(a, indent=2, sort_keys=True) + "\n")
    print(f"{a['valid_runs']} valid runs of {a['runs']}, {a['passed']} passed; wrote {out}")
    for line in headline(a):
        print(line)
    cc_path = args.runs / "honeycomb_crosscheck.json"
    cc = json.loads(cc_path.read_text()) if cc_path.exists() else None
    args.report.write_text(report(a, cc))
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
