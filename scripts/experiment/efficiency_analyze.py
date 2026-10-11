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

Deterministic: fixed bootstrap seed, sorted inputs, sorted JSON keys.
Stdlib only.

    uv run python scripts/experiment/efficiency_analyze.py
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
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


def derive_run(run_dir: Path) -> dict:
    record = json.loads((run_dir / "record.json").read_text())
    events = _events(run_dir / "transcript.jsonl")
    result_idx = [i for i, e in enumerate(events) if e.get("type") == "result"]
    results = [events[i] for i in result_idx]
    first = results[0] if results else {}
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

    # Denials: the stream's permission_denied events, unioned with every
    # result event's list (record.json keeps only the last result's).
    denied = {
        e.get("tool_use_id") or e.get("uuid")
        for e in events
        if e.get("type") == "system" and e.get("subtype") == "permission_denied"
    }
    for r in results:
        denied.update(d.get("tool_use_id") for d in r.get("permission_denials") or [])
    denied.discard(None)

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
    args = ap.parse_args()
    root: Path = args.root
    out_path: Path = args.out or root / "analysis.json"

    run_dirs = sorted(
        p.parent
        for p in root.glob("b[0-9]*/*/record.json")
        if (p.parent / "transcript.jsonl").exists()
    )
    runs = [derive_run(d) for d in run_dirs]
    runs.sort(key=lambda r: (r["block"], r["model"], r["arm"], r["run_id"]))
    models = sorted({r["model"] for r in runs})

    def cell(model: str, arm: str) -> list[dict]:
        return [r for r in runs if r["model"] == model and r["arm"] == arm]

    rng = random.Random(SEED)
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
    }
    out_path.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out_path} ({len(runs)} runs, models={models})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
