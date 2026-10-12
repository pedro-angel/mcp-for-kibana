#!/usr/bin/env python3
"""Analyse the local-model study: pass rates per model x level with Wilson
intervals, medians per step, and the chain-length breakdown.

    uv run python scripts/experiment/local_analyze.py                # full runs
    uv run python scripts/experiment/local_analyze.py --phase pilot
    uv run python scripts/experiment/local_analyze.py queries         # Honeycomb query specs
    uv run python scripts/experiment/local_analyze.py capture q01_runs RUN_PK RESULT_JSON_URL
    uv run python scripts/experiment/local_analyze.py crosscheck

Reads every ``record.json`` under ``<out_dir>/<phase>/`` and writes
``<out_dir>/<phase>/analysis.json``. Success and the step checks come from the
record (they need the Kibana state of the moment); every metric is re-derived
from the stored ``response.json`` and ``events.jsonl`` with
``local_run.parse_run``, and any difference from the record is listed under
``consistency``. Invalid runs (the environment failed, not the model) are
counted and excluded from rates and metrics.

Per model x level: runs, passes, rate, Wilson 95% interval, pass^k (Yao et
al.: the chance that k runs drawn from the cell all pass), which step failed
first, which checks failed, and median / IQR of time, tokens and tool calls,
per run and per chain step. Per model: the pass rate by chain length and
``lose_track_at``, the shortest level whose rate falls below
``analysis.lose_track_threshold``.

The Honeycomb cross-check follows efficiency_crosscheck.py: ``queries``
prints the query specs to run (Honeycomb MCP ``run_query``); ``capture``
stores each result as returned; ``crosscheck`` compares the stored results
with analysis.json and writes ``honeycomb_crosscheck.json``.

Deterministic: sorted inputs, sorted JSON keys. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import efficiency_analyze as ea  # noqa: E402
import local_run as lr  # noqa: E402

Z95 = 1.959964

# run-level metrics summarised per cell (name -> how to read it from a run row)
METRICS = (
    "wall_s",
    "s_per_step",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "output_tokens_per_step",
    "tool_calls",
    "invalid_tool_calls",
    "failed_tool_calls",
    "s_per_tool_call",
    "output_tokens_per_tool_call",
    "model_calls",
    "tokens_per_second",
    "ttft_s",
    "model_load_s",
)

# record fields that the re-derivation must reproduce
CONSISTENCY = (
    ("n_tool_calls", "n_tool_calls"),
    ("n_invalid_tool_calls", "n_invalid_tool_calls"),
    ("n_failed_tool_calls", "n_failed_tool_calls"),
    ("output_tokens", "output_tokens"),
    ("input_tokens", "input_tokens"),
    ("model_calls", "model_calls"),
)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float | None, float | None]:
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def pass_hat_k(c: int, n: int, k: int) -> float | None:
    """Chance that k runs drawn without replacement from the cell all pass."""
    if n < k:
        return None
    return math.comb(c, k) / math.comb(n, k)


def rederive(run_dir: Path) -> dict:
    resp = json.loads((run_dir / "response.json").read_text())
    events = []
    ev_path = run_dir / "events.jsonl"
    if ev_path.exists():
        events = [json.loads(line) for line in ev_path.read_text().splitlines() if line.strip()]
    chat = {"result": resp.get("result"), "events": events, "end_events": resp.get("end_events", 0)}
    return lr.parse_run(chat, [])


def _div(a, b):
    return a / b if a is not None and b else None


def run_row(rec: dict, parsed: dict) -> dict:
    wall_s = rec["wall_ms"] / 1000 if rec.get("wall_ms") is not None else None
    steps = rec["chain_length"]
    calls = parsed["n_tool_calls"]
    return {
        "run_id": rec["run_id"],
        "block": rec["block"],
        "model": rec["model"],
        "level": rec["level"],
        "chain_length": steps,
        "attempt": rec.get("attempt"),
        "valid": bool(rec.get("valid")),
        "invalid_reason": rec.get("invalid_reason"),
        "success": bool(rec.get("success")),
        "end_reason": rec.get("end_reason"),
        "steps_passed": rec.get("steps_passed"),
        "first_failed_step": rec.get("first_failed_step"),
        "failed_checks": sorted(
            f"{c['step']}:{c['check']}" for c in rec.get("criteria") or [] if not c["ok"]
        ),
        "wall_s": wall_s,
        "s_per_step": _div(wall_s, steps),
        "input_tokens": parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "reasoning_tokens": parsed["reasoning_tokens"],
        "output_tokens_per_step": _div(parsed["output_tokens"], steps),
        "tool_calls": calls,
        "invalid_tool_calls": parsed["n_invalid_tool_calls"],
        "failed_tool_calls": parsed["n_failed_tool_calls"],
        "s_per_tool_call": _div(wall_s, calls),
        "output_tokens_per_tool_call": _div(parsed["output_tokens"], calls),
        "model_calls": parsed["model_calls"],
        "tokens_per_second": parsed["tokens_per_second"],
        "ttft_s": parsed["ttft_s"],
        "model_load_s": parsed["model_load_s"],
        "tool_names": parsed["tool_names"],
        "known_secret_exposures": rec.get("known_secret_exposures", 0),
        "context_length": rec.get("model_loaded_context"),
        # Records from before the harness counted the server's tools lack it.
        "server_tools": rec.get("server_tools"),
    }


def _hist(xs) -> dict:
    out: dict = {}
    for x in xs:
        if x is not None:
            out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items()))


def summarise(rows: list[dict], ks: list[int]) -> dict:
    valid = [r for r in rows if r["valid"]]
    n, c = len(valid), sum(1 for r in valid if r["success"])
    lo, hi = wilson(c, n)
    return {
        "n_runs": len(rows),
        "n_valid": n,
        "n_invalid": len(rows) - n,
        "passes": c,
        "rate": c / n if n else None,
        "wilson95": [lo, hi],
        "pass_hat_k": {str(k): pass_hat_k(c, n, k) for k in ks},
        "first_failed_step": _hist(r["first_failed_step"] for r in valid if not r["success"]),
        "failed_checks": _hist(x for r in valid for x in r["failed_checks"]),
        "steps_passed": _hist(r["steps_passed"] for r in valid),
        "end_reasons": _hist(r["end_reason"] for r in valid),
        "known_secret_exposures": sum(r["known_secret_exposures"] for r in valid),
        "metrics": {m: ea.describe([r[m] for r in valid if r[m] is not None]) for m in METRICS},
    }


def analyze(cfg: dict, phase: str = "full") -> dict:
    out = lr.repo_path(cfg["runs"]["out_dir"]) / phase
    rows, mismatches = [], []
    for rec_path in sorted(out.glob("*/*/record.json")):
        rec = json.loads(rec_path.read_text())
        parsed = rederive(rec_path.parent)
        for rk, pk in CONSISTENCY:
            if rec.get(rk) != parsed.get(pk):
                mismatches.append(
                    {
                        "run_id": rec["run_id"],
                        "field": rk,
                        "record": rec.get(rk),
                        "rederived": parsed.get(pk),
                    }
                )
        rows.append(run_row(rec, parsed))
    ks = cfg["analysis"]["pass_hat_k"]
    levels = [lv["id"] for lv in cfg["task"]["levels"]]
    models = sorted(
        {r["model"] for r in rows},
        key=lambda m: (cfg["models"].index(m) if m in cfg["models"] else 1e9, m),
    )
    cells = {
        m: {
            lv: summarise([r for r in rows if r["model"] == m and r["level"] == lv], ks)
            for lv in levels
        }
        for m in models
    }
    pooled = {lv: summarise([r for r in rows if r["level"] == lv], ks) for lv in levels}
    thr = cfg["analysis"]["lose_track_threshold"]
    chain = {}
    for m in models:
        rates = [cells[m][lv]["rate"] for lv in levels]
        lose = next(
            (lv for lv, rate in zip(levels, rates, strict=True) if rate is not None and rate < thr),
            None,
        )
        chain[m] = {
            "levels": levels,
            "rates": rates,
            "wilson95": [cells[m][lv]["wilson95"] for lv in levels],
            "lose_track_at": lose,
            "median_steps_passed": [
                ea.describe(
                    [
                        r["steps_passed"]
                        for r in rows
                        if r["model"] == m
                        and r["level"] == lv
                        and r["valid"]
                        and r["steps_passed"] is not None
                    ]
                ).get("median")
                for lv in levels
            ],
            "median_s_per_step": [
                cells[m][lv]["metrics"]["s_per_step"].get("median") for lv in levels
            ],
            "median_output_tokens_per_step": [
                cells[m][lv]["metrics"]["output_tokens_per_step"].get("median") for lv in levels
            ],
        }
    return {
        "phase": phase,
        "question": cfg["question"],
        "config_sha": hashlib.sha256(Path(cfg["_path"]).read_bytes()).hexdigest()[:12],
        "lose_track_threshold": thr,
        "n_runs": len(rows),
        "n_valid": sum(1 for r in rows if r["valid"]),
        "models": models,
        "levels": levels,
        "cells": cells,
        "pooled": pooled,
        "chain": chain,
        "consistency": {"checked": [k for k, _ in CONSISTENCY], "mismatches": mismatches},
        "runs": rows,
    }


# ---------------------------------------------------------------- Honeycomb


def honeycomb_queries(cfg: dict, phase: str = "full") -> dict:
    """Query specs for the cross-check, in the Honeycomb query JSON the MCP
    ``run_query`` takes. Set start/end time to cover the runs."""
    base = [
        {"column": "gen_ai.operation.name", "op": "=", "value": "invoke_agent"},
        {"column": "study.phase", "op": "=", "value": phase},
        {"column": "study.valid", "op": "=", "value": True},
    ]
    bd = ["study.model", "study.level"]
    return {
        "q01_runs": {
            "breakdowns": bd,
            "filters": base,
            "calculations": [
                {"op": "COUNT", "name": "n"},
                {"op": "COUNT_DISTINCT", "column": "gen_ai.conversation.id", "name": "conv"},
                {
                    "op": "COUNT",
                    "name": "ok",
                    "filters": [{"column": "agent.task.success", "op": "=", "value": True}],
                },
            ],
            "formulas": [
                {"name": "runs", "expression": "$n"},
                {"name": "conversations", "expression": "$conv"},
                {"name": "passes", "expression": "$ok"},
            ],
            "orders": [{"column": "study.model"}, {"column": "study.level"}],
        },
        "q02_sums": {
            "breakdowns": bd,
            "filters": base,
            "calculations": [
                {"op": "SUM", "column": "agent.usage.output_tokens"},
                {"op": "SUM", "column": "agent.usage.input_tokens"},
                {"op": "SUM", "column": "agent.tool.calls_mcp"},
                {"op": "SUM", "column": "agent.tool.invalid_calls"},
            ],
            "orders": [{"column": "study.model"}, {"column": "study.level"}],
        },
        "q03_spans": {
            "breakdowns": ["study.model", "study.level", "gen_ai.operation.name"],
            "filters": [base[1]],
            "calculations": [{"op": "COUNT"}],
        },
    }


def capture(cfg: dict, phase: str, qid: str, run_pk: str, url: str) -> int:
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read())
    data.pop("series", None)
    data.pop("total_by_aggregate_series", None)
    data["run_pk"] = run_pk
    raw = lr.repo_path(cfg["runs"]["out_dir"]) / phase / "honeycomb"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / f"{qid}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(f"wrote {raw / (qid + '.json')}: {len(data.get('results', []))} rows")
    return 0


def crosscheck(cfg: dict, phase: str = "full") -> dict:
    base = lr.repo_path(cfg["runs"]["out_dir"]) / phase
    a = json.loads((base / "analysis.json").read_text())
    raw = base / "honeycomb"

    def rows(qid):
        p = raw / f"{qid}.json"
        return [r["data"] for r in json.loads(p.read_text())["results"]] if p.exists() else None

    def cell(rs, model, level, col):
        for r in rs or []:
            if r.get("study.model") == model and r.get("study.level") == level:
                return r.get(col)
        return None

    checks = []
    q1, q2 = rows("q01_runs"), rows("q02_sums")
    for m in a["models"]:
        for lv in a["levels"]:
            c = a["cells"][m][lv]
            valid = [r for r in a["runs"] if r["model"] == m and r["level"] == lv and r["valid"]]
            ours = {
                ("q01_runs", "runs"): c["n_valid"],
                ("q01_runs", "conversations"): c["n_valid"],
                ("q01_runs", "passes"): c["passes"],
                ("q02_sums", "SUM(agent.usage.output_tokens)"): sum(
                    r["output_tokens"] or 0 for r in valid
                ),
                ("q02_sums", "SUM(agent.usage.input_tokens)"): sum(
                    r["input_tokens"] or 0 for r in valid
                ),
                ("q02_sums", "SUM(agent.tool.calls_mcp)"): sum(r["tool_calls"] for r in valid),
                ("q02_sums", "SUM(agent.tool.invalid_calls)"): sum(
                    r["invalid_tool_calls"] for r in valid
                ),
            }
            for (qid, col), our in ours.items():
                rs = q1 if qid == "q01_runs" else q2
                hc = cell(rs, m, lv, col) if rs is not None else None
                if rs is None:
                    verdict = "not captured"
                elif hc is None and our == 0:
                    verdict = "match"
                else:
                    verdict = "match" if hc is not None and float(hc) == float(our) else "differs"
                checks.append(
                    {
                        "model": m,
                        "level": lv,
                        "query": qid,
                        "column": col,
                        "ours": our,
                        "honeycomb": hc,
                        "verdict": verdict,
                    }
                )
    result = {
        "phase": phase,
        "checks": checks,
        "summary": _hist(c["verdict"] for c in checks),
    }
    (base / "honeycomb_crosscheck.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=lr.CONFIG)
    ap.add_argument("--phase", default="full", choices=["pilot", "full"])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("analyze")
    sub.add_parser("queries")
    c = sub.add_parser("capture")
    c.add_argument("qid")
    c.add_argument("run_pk")
    c.add_argument("url")
    sub.add_parser("crosscheck")
    args = ap.parse_args(argv)
    cfg = lr.load_config(args.config)
    if args.cmd == "queries":
        print(json.dumps(honeycomb_queries(cfg, args.phase), indent=1))
        return 0
    if args.cmd == "capture":
        return capture(cfg, args.phase, args.qid, args.run_pk, args.url)
    if args.cmd == "crosscheck":
        res = crosscheck(cfg, args.phase)
        print(f"cross-check: {res['summary']}")
        return 0 if set(res["summary"]) <= {"match"} else 1
    a = analyze(cfg, args.phase)
    path = lr.repo_path(cfg["runs"]["out_dir"]) / args.phase / "analysis.json"
    path.write_text(json.dumps(a, indent=1, sort_keys=True) + "\n")
    print(f"wrote {path}: {a['n_runs']} runs, {a['n_valid']} valid")
    for m in a["models"]:
        ch = a["chain"][m]
        rates = " ".join(f"{lv}={c['passes']}/{c['n_valid']}" for lv, c in a["cells"][m].items())
        print(f"  {m}: {rates}  lose_track_at={ch['lose_track_at']}")
    if a["consistency"]["mismatches"]:
        print(
            f"WARN: {len(a['consistency']['mismatches'])} record values differ from the re-derivation"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
