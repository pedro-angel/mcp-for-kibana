#!/usr/bin/env python3
"""Cross-check the efficiency study's numbers against Honeycomb queries.

Every headline number in ``runs/efficiency/full/analysis.json`` is computed a
second time with a Honeycomb query over the traces that
``efficiency_honeycomb.py`` sent (team pedro-angel, environment ``test``,
dataset ``mcp-for-kibana-study``). The output,
``runs/efficiency/full/honeycomb_crosscheck.json``, holds for each number our
value, Honeycomb's value, the query and a verdict.

Two steps, because the Honeycomb key in this repository can send events but
not run queries (``/1/auth``: ``queries: false``). Queries run through the
Honeycomb MCP server's ``run_query``; its result carries a short-lived
``query_result_json`` link.

1. ``capture ID RUN_PK RESULT_JSON_URL``: fetch that link at once and store the
   raw result (spec template, rows, totals; the per-bucket time series are
   dropped) under ``runs/efficiency/full/honeycomb/ID.json``. Honeycomb values
   are therefore read from the stored query results, never typed in.
2. ``build``: for every check in ``CHECKS``, read our value from
   ``analysis.json`` (or derive it from its per-run records), read Honeycomb's
   value from the captured result, compare, and write the cross-check file.

    python3 scripts/experiment/efficiency_crosscheck.py capture q01 2g-Q9jDJEWzk 'https://mcp…'
    python3 scripts/experiment/efficiency_crosscheck.py build

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FULL = HERE / "runs" / "efficiency" / "full"
ANALYSIS = FULL / "analysis.json"
RAW = FULL / "honeycomb"
OUT = FULL / "honeycomb_crosscheck.json"
UI = "https://ui.eu1.honeycomb.io/pedro-angel/environments/test/datasets/mcp-for-kibana-study"


def capture(qid: str, run_pk: str, url: str) -> int:
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read())
    data.pop("series", None)
    data.pop("total_by_aggregate_series", None)
    data["run_pk"] = run_pk
    data["query_url"] = f"{UI}/result/{run_pk}"
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{qid}.json"
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(f"wrote {path.relative_to(HERE.parent.parent)}: {len(data.get('results', []))} rows")
    return 0


# ---------------------------------------------------------------- our side

MODELS = ("claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5")
ARMS = ("no-mcp", "with-mcp")
SHORT = {"claude-haiku-5-5": "Haiku", "claude-opus-5-5": "Opus", "claude-sonnet-5-5": "Sonnet"}

# analysis.json metric -> (root-span column in Honeycomb, per-run accessor)
METRICS = {
    "cost_usd": ("agent.cost.usd", "cost"),
    "total_tokens": ("total_tokens", "total tokens"),
    "input_tokens": ("agent.usage.uncached_input_tokens", "uncached input tokens"),
    "output_tokens": ("agent.usage.output_tokens", "output tokens"),
    "cache_read_input_tokens": ("agent.usage.cache_read_tokens", "cache-read tokens"),
    "cache_creation_input_tokens": ("agent.usage.cache_creation_tokens", "cache-write tokens"),
    "agent.model.calls": ("agent.model.calls", "model calls"),
    "num_turns": ("agent.harness.turns", "turns"),
    "tool_calls": ("agent.tool.calls", "tool calls"),
    "mcp_calls": ("agent.tool.calls_mcp", "MCP tool calls"),
    "bash_calls": ("agent.tool.calls_shell", "shell tool calls"),
    "duration_ms": ("agent.time.wall_ms", "duration (ms)"),
    "duration_api_ms": ("agent.time.model_ms", "model time (ms)"),
    "agent.usage.input_tokens_per_call": (
        "agent.usage.input_tokens_per_call",
        "input tokens per model call",
    ),
}
SIGNAL_METRICS = {"agent.model.calls", "agent.usage.input_tokens_per_call"}


def run_value(run: dict, metric: str):
    if metric in run:
        return run[metric]
    return run["agent"][metric]


def get(a: dict, path: str):
    """A '/'-separated path into analysis.json (keys may contain dots)."""
    x = a
    for k in path.split("/"):
        x = x[k]
    return x


def hc_pct(values: list, p: float):
    """Honeycomb's percentile on these runs: the sorted value at 0-based index
    floor(p * n), no interpolation (observed on the captured P25/P50/P75)."""
    v = sorted(values)
    return v[min(len(v) - 1, int(p * len(v)))]


def cell_runs(a: dict, model: str | None, arm: str) -> list[dict]:
    return [r for r in a["runs"] if r["arm"] == arm and (model is None or r["model"] == model)]


def dist_path(model: str, arm: str, metric: str, stat: str) -> str:
    if metric in SIGNAL_METRICS:
        return f"signals/summary/{model}/{arm}/distributions/{metric}/{stat}"
    return f"summary/{model}/{arm}/metrics/{metric}/{stat}"


# ---------------------------------------------------------------- Honeycomb side


def hc_rows(qid: str) -> list[dict]:
    return [r["data"] for r in json.loads((RAW / f"{qid}.json").read_text())["results"]]


def hc_meta(qid: str) -> dict:
    d = json.loads((RAW / f"{qid}.json").read_text())
    return {"run_pk": d["run_pk"], "query_url": d["query_url"], "spec": d["template"]}


def hc_cell(qid: str, col: str, absent=None, **sel):
    for row in hc_rows(qid):
        if all(row.get(k) == v for k, v in sel.items()):
            return row.get(col)
    return absent


# ---------------------------------------------------------------- the checks

TOL = 1e-6  # relative; Honeycomb stores float attributes with ~7 significant digits


def same(x, y) -> bool:
    if x is None or y is None:
        return False
    if isinstance(x, (list, tuple)) or isinstance(y, (list, tuple)):
        return list(x) == list(y)
    return abs(x - y) <= TOL * max(1.0, abs(x), abs(y))


def checks(a: dict) -> list[dict]:
    out: list[dict] = []

    def add(
        group,
        cid,
        question,
        our,
        our_source,
        hc,
        qid,
        explain,
        verdict=None,
        hc_method=None,
        note="",
        method_note="",
    ):
        meta = hc_meta(qid) if qid else None
        if verdict is None:
            if same(our, hc):
                verdict = "exact"
            elif hc_method is not None and same(hc_method, hc):
                verdict = "method"
                note = (note + " " if note else "") + method_note
            else:
                verdict = "disagree"
        out.append(
            {
                "id": cid,
                "group": group,
                "question": question,
                "our_value": our,
                "our_source": our_source,
                "honeycomb_value": hc,
                "honeycomb_method_on_our_runs": hc_method,
                "query_id": qid,
                "query_explained": explain,
                "query_spec": meta["spec"] if meta else None,
                "query_url": meta["query_url"] if meta else None,
                "verdict": verdict,
                "note": note.strip(),
            }
        )

    pct_note = (
        "Honeycomb's percentile is the observation at rank floor(p*n)+1 with no "
        "interpolation; our pipeline interpolates (statistics.median, quantiles "
        "method='inclusive'). Recomputing Honeycomb's rule on our per-run records gives "
        "Honeycomb's value exactly."
    )

    # n, strict and lenient pass per cell
    for m in MODELS:
        for arm in ARMS:
            base = f"summary/{m}/{arm}"
            for key, col, label in (
                ("n", "runs", "runs"),
                ("pass_strict", "pass_strict", "strict passes"),
                ("pass_lenient", "pass_lenient", "lenient passes"),
            ):
                add(
                    "Runs and success",
                    f"{key}.{m}.{arm}",
                    f"{SHORT[m]} {arm}: {label}",
                    get(a, f"{base}/{key}"),
                    f"{base}/{key}",
                    hc_cell("q01_counts", col, **{"study.model": m, "study.arm": arm}),
                    "q01_counts",
                    "COUNT of invoke_agent (root) spans grouped by study.model and study.arm; "
                    "the pass counts are the same COUNT with a per-calculation filter "
                    "study.success_strict = true (or success_lenient).",
                )

    # medians and IQRs per cell
    for metric, (col, label) in METRICS.items():
        for m in MODELS:
            for arm in ARMS:
                vals = [run_value(r, metric) for r in cell_runs(a, m, arm)]
                sel = {"study.model": m, "study.arm": arm}
                path = dist_path(m, arm, metric, "median")
                add(
                    "Medians per model and arm",
                    f"median.{metric}.{m}.{arm}",
                    f"{SHORT[m]} {arm}: median {label}",
                    get(a, path),
                    path,
                    hc_cell("q03_p50", f"P50({col})", **sel),
                    "q03_p50",
                    f"P50 of {col} on root spans (trace.parent_id does not exist), grouped by "
                    "study.model and study.arm."
                    + (
                        " total_tokens is a calculated field: SUM($agent.usage.input_tokens, "
                        "$agent.usage.output_tokens)."
                        if col == "total_tokens"
                        else ""
                    ),
                    hc_method=hc_pct(vals, 0.5),
                    method_note=pct_note,
                )
                if metric == "agent.usage.input_tokens_per_call":
                    continue
                path = dist_path(m, arm, metric, "iqr")
                q1, q3 = (hc_cell("q04_quartiles", f"{p}({col})", **sel) for p in ("P25", "P75"))
                add(
                    "IQR per model and arm",
                    f"iqr.{metric}.{m}.{arm}",
                    f"{SHORT[m]} {arm}: IQR of {label}",
                    get(a, path),
                    path,
                    None if q1 is None or q3 is None else q3 - q1,
                    "q04_quartiles",
                    f"P75 minus P25 of {col} on root spans, grouped by study.model and "
                    "study.arm (the subtraction is done on the two returned columns).",
                    hc_method=hc_pct(vals, 0.75) - hc_pct(vals, 0.25),
                    method_note=pct_note,
                )

    # ratios of medians, per model and pooled
    ratio_map = {
        "ratio_cost": ("cost_usd", "comparison/{m}/all_runs/cost_usd/ratio"),
        "ratio_total_tokens": ("total_tokens", "comparison/{m}/all_runs/total_tokens/ratio"),
        "ratio_model_calls": (
            "agent.model.calls",
            "signals/comparison/{m}/agent.model.calls/ratio",
        ),
        "ratio_turns": ("num_turns", "comparison/{m}/all_runs/num_turns/ratio"),
        "ratio_tool_calls": ("tool_calls", "comparison/{m}/all_runs/tool_calls/ratio"),
        "ratio_wall": ("duration_ms", "comparison/{m}/all_runs/duration_ms/ratio"),
        "ratio_model_ms": ("duration_api_ms", "comparison/{m}/all_runs/duration_api_ms/ratio"),
        "ratio_tokens_per_call": (
            "agent.usage.input_tokens_per_call",
            "signals/comparison/{m}/agent.usage.input_tokens_per_call/ratio",
        ),
        "ratio_tool_result_bytes": (
            "agent.usage.tool_result_bytes",
            "signals/comparison/{m}/agent.usage.tool_result_bytes/ratio",
        ),
        "ratio_first_tool_ms": (
            "agent.time.first_tool_ms",
            "signals/comparison/{m}/agent.time.first_tool_ms/ratio",
        ),
    }
    pooled_paths = {
        "ratio_cost": "pooled/comparison/all_runs/cost_usd/ratio",
        "ratio_total_tokens": "pooled/comparison/all_runs/total_tokens/ratio",
        "ratio_model_calls": "signals/pooled/comparison/agent.model.calls/ratio",
        "ratio_model_ms": "signals/pooled/comparison/agent.time.model_ms/ratio",
        "ratio_tokens_per_call": "signals/pooled/comparison/agent.usage.input_tokens_per_call/ratio",
        "ratio_tool_result_bytes": "signals/pooled/comparison/agent.usage.tool_result_bytes/ratio",
        "ratio_first_tool_ms": "signals/pooled/comparison/agent.time.first_tool_ms/ratio",
    }
    ratio_explain = (
        "Two P50 calculations of {col} on root spans, each with a per-calculation "
        "filter (study.arm = with-mcp, study.arm = no-mcp), divided by a formula; "
        "{by}."
    )
    for name, (metric, tmpl) in ratio_map.items():
        col = METRICS.get(metric, (metric,))[0]
        for m in MODELS:

            def med(arm, m=m, metric=metric):
                return hc_pct([run_value(r, metric) for r in cell_runs(a, m, arm)], 0.5)

            path = tmpl.format(m=m)
            add(
                "With / without ratios of medians",
                f"{name}.{m}",
                f"{SHORT[m]}: with ÷ without, median {name[6:].replace('_', ' ')}",
                get(a, path),
                path,
                hc_cell("q05_ratios_by_model", name, **{"study.model": m}),
                "q05_ratios_by_model",
                ratio_explain.format(col=col, by="grouped by study.model"),
                hc_method=med("with-mcp") / med("no-mcp"),
                method_note="Both medians use Honeycomb's percentile rule (see medians); the "
                "ratio of those two observations is Honeycomb's value exactly.",
            )
        if name in pooled_paths:

            def pmed(arm, metric=metric):
                return hc_pct([run_value(r, metric) for r in cell_runs(a, None, arm)], 0.5)

            path = pooled_paths[name]
            add(
                "With / without ratios of medians",
                f"{name}.pooled",
                f"All models: with ÷ without, median {name[6:].replace('_', ' ')}",
                get(a, path),
                path,
                hc_cell("q06_ratios_pooled", name),
                "q06_ratios_pooled",
                ratio_explain.format(col=col, by="no grouping (90 runs per arm)"),
                hc_method=pmed("with-mcp") / pmed("no-mcp"),
                method_note="Honeycomb's percentile rule on 90 runs per arm, then the ratio.",
            )
    for m in (*MODELS, None):
        for metric, path_t in (
            ("cost", "comparison/{m}/all_runs/cost_usd/ratio_ci95"),
            ("model calls", "signals/comparison/{m}/agent.model.calls/ratio_ci95"),
        ):
            if m is None:
                path = (
                    "pooled/comparison/all_runs/cost_usd/ratio_ci95"
                    if metric == "cost"
                    else "signals/pooled/comparison/agent.model.calls/ratio_ci95"
                )
            else:
                path = path_t.format(m=m)
            add(
                "Confidence intervals",
                f"ci95.{metric.replace(' ', '_')}.{m or 'pooled'}",
                f"{SHORT.get(m, 'All models')}: 95% CI of the with ÷ without ratio of median "
                f"{metric}",
                get(a, path),
                path,
                None,
                None,
                "Not expressible: a percentile bootstrap needs 10 000 resamples of the runs "
                "within each arm and a median per resample; a Honeycomb query aggregates the "
                "stored events once and has no resampling.",
                verdict="not_expressible",
                note="Workaround: export the root spans (raw-row query) and bootstrap outside "
                "Honeycomb, or send precomputed CIs as markers or board text.",
            )

    # pooled medians of shares and bytes (q06 passthrough formulas)
    for name, path, metric in (
        (
            "model_share_with",
            "signals/pooled/summary/with-mcp/distributions/agent.time.model_share/median",
            "agent.time.model_share",
        ),
        (
            "model_share_without",
            "signals/pooled/summary/no-mcp/distributions/agent.time.model_share/median",
            "agent.time.model_share",
        ),
        (
            "median_trb_with",
            "signals/pooled/comparison/agent.usage.tool_result_bytes/median_with",
            "agent.usage.tool_result_bytes",
        ),
        (
            "median_trb_without",
            "signals/pooled/comparison/agent.usage.tool_result_bytes/median_without",
            "agent.usage.tool_result_bytes",
        ),
    ):
        arm = "with-mcp" if name.endswith("_with") else "no-mcp"
        label = "model share of wall time" if "share" in name else "tool-result bytes fed back"
        add(
            "Time split",
            f"pooled_median.{metric}.{arm}",
            f"All models {arm}: median {label}",
            get(a, path),
            path,
            hc_cell("q06_ratios_pooled", name),
            "q06_ratios_pooled",
            f"P50 of {metric} on root spans with a per-calculation filter on study.arm, "
            "surfaced through a pass-through formula.",
            hc_method=hc_pct([run_value(r, metric) for r in cell_runs(a, None, arm)], 0.5),
            method_note=pct_note,
        )

    # time split per cell
    for m in MODELS:
        for arm in ARMS:
            sel = {"study.model": m, "study.arm": arm}
            vals = [run_value(r, "agent.time.tool_busy_ms") for r in cell_runs(a, m, arm)]
            path = f"signals/summary/{m}/{arm}/distributions/agent.time.tool_busy_ms/median"
            add(
                "Time split",
                f"median.tool_busy_ms.{m}.{arm}",
                f"{SHORT[m]} {arm}: median busy tool time (ms)",
                get(a, path),
                path,
                hc_cell("q15_time_split", "P50(agent.time.tool_busy_ms)", **sel),
                "q15_time_split",
                "P50 of agent.time.tool_busy_ms on invoke_agent spans, grouped by study.model "
                "and study.arm.",
                hc_method=hc_pct(vals, 0.5),
                method_note=pct_note,
            )
    wait = sum(r["agent"]["agent.time.wait_ms"] for r in cell_runs(a, "claude-haiku-5-5", "no-mcp"))
    add(
        "Time split",
        "sum.wait_ms.haiku.no-mcp",
        "Haiku no-mcp: total deliberate-wait time (ms)",
        wait,
        "runs[model=claude-haiku-5-5, arm=no-mcp]/agent/agent.time.wait_ms (sum)",
        hc_cell(
            "q15_time_split",
            "SUM(agent.time.wait_ms)",
            **{"study.model": "claude-haiku-5-5", "study.arm": "no-mcp"},
        ),
        "q15_time_split",
        "SUM of agent.time.wait_ms on invoke_agent spans, grouped by model and arm.",
    )
    tot = sum(
        get(a, f"signals/summary/{m}/{arm}/totals/agent.harness.timed_out_calls")
        for m in MODELS
        for arm in ARMS
    )
    add(
        "Harness anomalies",
        "sum.timed_out_calls",
        "Calls that hit the 120 s harness timeout",
        tot,
        "signals/summary/*/*/totals/agent.harness.timed_out_calls (sum)",
        sum(r.get("SUM(agent.harness.timed_out_calls)", 0) for r in hc_rows("q15_time_split")),
        "q15_time_split",
        "SUM of agent.harness.timed_out_calls on invoke_agent spans, summed "
        "over the six model x arm rows.",
    )

    # tool reliability per arm (span level)
    rel = (
        ("tool_calls", "totals/agent.tool.calls", "tool calls"),
        ("failures", "totals/agent.tool.failures", "failed tool calls"),
        (
            "hidden_failures",
            "totals/agent.tool.hidden_failures",
            "hidden failures (is_error false)",
        ),
        ("harness_errors", "totals/agent.tool.errors", "harness-flagged errors (is_error)"),
        ("retries", "totals/agent.tool.retries", "retries"),
        ("failures_recovered", "totals/agent.tool.failures_recovered", "failures later recovered"),
        (
            "first_attempt_failures",
            "totals/agent.tool.first_attempt_failures",
            "first-attempt failures",
        ),
        ("failure_rate", "rates/tool_failure_rate", "tool failure rate"),
        ("error_rate", "rates/tool_error_rate", "harness error rate"),
        ("hidden_share", "rates/hidden_share_of_failures", "hidden share of failures"),
        ("recovered_share", "rates/recovered_share_of_failures", "recovered share of failures"),
    )
    for arm in ARMS:
        for col, sub, label in rel:
            path = f"signals/pooled/summary/{arm}/{sub}"
            add(
                "Tool reliability",
                f"tools.{col}.{arm}",
                f"{arm}: {label}",
                get(a, path),
                path,
                hc_cell("q07_tool_reliability", col, **{"study.arm": arm}),
                "q07_tool_reliability",
                "COUNT of execute_tool spans grouped by study.arm, with per-calculation filters "
                "(agent.tool.failed, hidden_failure, error, retry, failed AND recovered, "
                "first_attempt_failed = true); rates and shares are formulas over those counts.",
            )
        for cls in (
            "http_4xx",
            "validation",
            "command_failed",
            "timeout",
            "http_5xx",
            "refused",
            "unknown_tool",
        ):
            path = f"signals/pooled/summary/{arm}/totals/agent.tool.failures_{cls}"
            add(
                "Tool reliability",
                f"tools.class.{cls}.{arm}",
                f"{arm}: failures of class {cls}",
                get(a, path),
                path,
                hc_cell(
                    "q08_failures_by_class",
                    "COUNT",
                    absent=0,
                    **{"study.arm": arm, "agent.tool.error_class": cls},
                ),
                "q08_failures_by_class",
                "COUNT of execute_tool spans where agent.tool.failed = true, grouped by "
                "study.arm and agent.tool.error_class.",
                note="A class with no failing span returns no row; it reads as 0.",
            )

    # create_dashboard
    cd = "mcp__mcp-for-kibana__create_dashboard"
    sel = {"study.arm": "with-mcp", "gen_ai.tool.name": cd}
    for col, sub, label in (
        ("calls", "calls", "calls"),
        ("failures", "failures", "failures"),
        ("first_attempt_failures", "first_attempt_failures", "first-attempt failures"),
        ("retries", "retries", "retries"),
    ):
        path = f"signals/pooled/summary/with-mcp/by_tool/{cd}/{sub}"
        add(
            "create_dashboard",
            f"create_dashboard.{col}",
            f"create_dashboard: {label}",
            get(a, path),
            path,
            hc_cell("q09_by_tool", col, **sel),
            "q09_by_tool",
            "COUNT of execute_tool spans grouped by study.arm and gen_ai.tool.name, with "
            "per-calculation filters on agent.tool.failed / first_attempt_failed / retry.",
        )
    wm = cell_runs(a, None, "with-mcp")
    add(
        "create_dashboard",
        "create_dashboard.runs_first_attempt_failed",
        "create_dashboard: runs whose first call failed",
        sum(1 for r in wm if r["tool_stats"].get(cd, {}).get("first_attempt_failures", 0) > 0),
        "runs[arm=with-mcp]/tool_stats/create_dashboard/first_attempt_failures > 0 (count)",
        hc_cell("q09_by_tool", "runs_first_attempt_failed", **sel),
        "q09_by_tool",
        "COUNT_DISTINCT of trace.trace_id over create_dashboard spans with "
        "agent.tool.first_attempt_failed = true.",
    )
    add(
        "create_dashboard",
        "create_dashboard.runs_recovered",
        "create_dashboard: runs with a later successful call",
        sum(1 for r in wm if (s := r["tool_stats"].get(cd)) and s["calls"] > s["failures"]),
        "runs[arm=with-mcp]/tool_stats/create_dashboard calls > failures (count)",
        hc_cell("q09_by_tool", "runs_with_a_success", **sel),
        "q09_by_tool",
        "COUNT_DISTINCT of trace.trace_id over create_dashboard spans with "
        "agent.tool.failed = false.",
    )
    add(
        "create_dashboard",
        "create_dashboard.harness_errors",
        "create_dashboard: harness-flagged errors (of 111 in with-mcp)",
        get(a, f"signals/pooled/summary/with-mcp/by_tool/{cd}/failures"),
        f"signals/pooled/summary/with-mcp/by_tool/{cd}/failures (all harness-flagged)",
        hc_cell("q09_by_tool", "harness_errors", **sel),
        "q09_by_tool",
        "COUNT of create_dashboard spans with agent.tool.error = true. BubbleUp on the 111 "
        "with-mcp spans with agent.tool.error = true against the other 666 finds the same "
        "thing without a hypothesis: create_dashboard 92.8% of the selection vs 13.5% of "
        "the baseline, error class validation 95.5% vs 1.8%.",
    )

    # shell HTTP bypass
    sh = {"study.arm": "with-mcp"}
    shell_rows = [r for r in hc_rows("q11_shell_http") if r["study.arm"] == "with-mcp"]
    p = "signals/pooled/summary/with-mcp"
    for cid, label, our, src, col in (
        (
            "runs",
            "with-mcp runs that also called Kibana through the shell",
            get(a, f"{p}/runs_with/agent.tool.shell_http_calls"),
            f"{p}/runs_with/agent.tool.shell_http_calls",
            "runs_with_shell_http",
        ),
        (
            "calls",
            "with-mcp shell HTTP calls",
            get(a, f"{p}/totals/agent.tool.shell_http_calls"),
            f"{p}/totals/agent.tool.shell_http_calls",
            "shell_http_calls",
        ),
    ):
        add(
            "Shell HTTP bypass",
            f"shell_http.{cid}",
            label,
            our,
            src,
            sum(r[col] for r in shell_rows),
            "q11_shell_http",
            "COUNT (calls) and COUNT_DISTINCT of trace.trace_id (runs) over execute_tool spans "
            "with agent.tool.shell_http = true, grouped by study.arm and study.model; the "
            "with-mcp rows are summed.",
        )
    for m in MODELS:
        add(
            "Shell HTTP bypass",
            f"shell_http.runs.{m}",
            f"{SHORT[m]} with-mcp: bypass runs",
            get(a, f"signals/summary/{m}/with-mcp/runs_with/agent.tool.shell_http_calls"),
            f"signals/summary/{m}/with-mcp/runs_with/agent.tool.shell_http_calls",
            hc_cell("q11_shell_http", "runs_with_shell_http", absent=0, **{**sh, "study.model": m}),
            "q11_shell_http",
            "Same query, the row for this model.",
            note="No row means no span: reads as 0.",
        )
    for cid, label, col, our in (
        ("failed", "failed shell HTTP calls (with-mcp)", "failed", 113),
        ("hidden", "of those, invisible to is_error", "hidden", 109),
    ):
        add(
            "Shell HTTP bypass",
            f"shell_http.{cid}",
            label,
            our,
            "SIGNALS.md prose (not a field in analysis.json)",
            sum(r[col] for r in shell_rows),
            "q11_shell_http",
            "Per-calculation filters agent.tool.failed = true and agent.tool.hidden_failure = "
            "true on the shell HTTP spans, with-mcp rows summed.",
            note="Our figure is quoted in SIGNALS.md; analysis.json does not break failures "
            "down by shell_http, so Honeycomb is the first machine-readable source for it.",
        )
    q = (
        "Bash POST /api/console/proxy",
        "Bash POST /api/esql/query",
        "Bash POST /internal/search/es",
        "Bash POST /internal/search/esql",
    )
    add(
        "Shell HTTP bypass",
        "shell_http.query_endpoints",
        "with-mcp shell HTTP calls to query endpoints",
        117,
        "SIGNALS.md prose (not a field in analysis.json)",
        sum(r["COUNT"] for r in hc_rows("q10_bypass_targets") if r["agent.tool.target"] in q),
        "q10_bypass_targets",
        "COUNT of with-mcp shell HTTP spans grouped by agent.tool.target; the four query "
        "endpoints (console proxy, /api/esql/query, /internal/search/es, "
        "/internal/search/esql) are summed.",
        note="One more call went to /internal/bsearch; counting it as a query endpoint gives "
        "118. The breakdown makes that definitional choice visible.",
    )

    # safety
    for m, arm in (
        ("claude-haiku-5-5", "no-mcp"),
        ("claude-haiku-5-5", "with-mcp"),
        ("claude-opus-5-5", "no-mcp"),
    ):
        path = f"signals/summary/{m}/{arm}/runs_with/agent.tool.env_enumeration_calls"
        add(
            "Safety",
            f"env_enumeration.runs.{m}.{arm}",
            f"{SHORT[m]} {arm}: runs that dumped the environment",
            get(a, path),
            path,
            hc_cell(
                "q12_safety_harness", "runs_env_enumeration", **{"study.model": m, "study.arm": arm}
            ),
            "q12_safety_harness",
            "COUNT of invoke_agent spans with a per-calculation filter "
            "agent.tool.env_enumeration_calls > 0, grouped by model and arm.",
        )
    env_total = sum(
        get(a, f"signals/summary/{m}/{arm}/runs_with/agent.tool.env_enumeration_calls")
        for m in MODELS
        for arm in ARMS
    )
    src = "signals/summary/*/*/runs_with/agent.tool.env_enumeration_calls (sum)"
    add(
        "Safety",
        "env_enumeration.runs.total.root",
        "Runs that dumped the environment (root path)",
        env_total,
        src,
        sum(r["runs_env_enumeration"] for r in hc_rows("q12_safety_harness")),
        "q12_safety_harness",
        "Same root-span COUNT, summed over the six rows.",
    )
    add(
        "Safety",
        "env_enumeration.runs.total.spans",
        "Runs that dumped the environment (span path)",
        env_total,
        src,
        sum(r["runs_env_enumeration"] for r in hc_rows("q13_safety_spans")),
        "q13_safety_spans",
        "COUNT_DISTINCT of trace.trace_id over child spans with agent.tool.env_enumeration = "
        "true: the run count is derived from the tool spans themselves, not the root total.",
    )
    for arm in ARMS:
        path = f"signals/pooled/summary/{arm}/runs_with/agent.secret.exposed"
        add(
            "Safety",
            f"secret.runs.{arm}",
            f"{arm}: runs that exposed a secret",
            get(a, path),
            path,
            sum(
                r["runs_secret_exposed"]
                for r in hc_rows("q12_safety_harness")
                if r["study.arm"] == arm
            ),
            "q12_safety_harness",
            "COUNT of invoke_agent spans with a per-calculation filter agent.secret.exposed = "
            "true, grouped by model and arm, summed per arm.",
        )
    path = "signals/pooled/summary/no-mcp/runs_with/agent.secret.known_exposed"
    add(
        "Safety",
        "secret.known.no-mcp",
        "no-mcp: runs that exposed the registered Kibana key",
        get(a, path),
        path,
        sum(
            r["runs_known_secret_exposed"]
            for r in hc_rows("q12_safety_harness")
            if r["study.arm"] == "no-mcp"
        ),
        "q12_safety_harness",
        "COUNT with a per-calculation filter agent.secret.known_exposed = true.",
    )
    path = "signals/summary/claude-haiku-5-5/no-mcp/totals/agent.secret.location_tool_result"
    add(
        "Safety",
        "secret.spans.tool_result",
        "Tool-result spans carrying a secret",
        get(a, path),
        path,
        sum(r["secret_spans"] for r in hc_rows("q13_safety_spans")),
        "q13_safety_spans",
        "COUNT of child spans with agent.secret.exposed = true (both are "
        "execute_tool spans whose agent.secret.location is tool_result).",
    )
    tot_den = sum(
        get(a, f"signals/summary/{m}/{arm}/totals/agent.permission.denials")
        for m in MODELS
        for arm in ARMS
    )
    src = "signals/summary/*/*/totals/agent.permission.denials (sum)"
    add(
        "Safety",
        "permission_denials.root",
        "Permission denials (root path)",
        tot_den,
        src,
        sum(r["permission_denials"] for r in hc_rows("q12_safety_harness")),
        "q12_safety_harness",
        "SUM of agent.permission.denials on invoke_agent spans.",
    )
    add(
        "Safety",
        "permission_denials.spans",
        "Permission denials (span path)",
        tot_den,
        src,
        sum(r["permission_denied_calls"] for r in hc_rows("q13_safety_spans")),
        "q13_safety_spans",
        "COUNT of child spans with agent.tool.permission_denied = true.",
    )

    # harness anomalies (Sonnet no-mcp)
    for col, key, label in (
        (
            "runs_result_events_gt1",
            "agent.harness.result_events>1",
            "runs with a second result event",
        ),
        ("runs_timed_out_call", "agent.harness.timed_out_calls", "runs with a timed-out call"),
    ):
        m, arm = "claude-sonnet-5-5", "no-mcp"
        path = f"signals/summary/{m}/{arm}/runs_with/{key}"
        add(
            "Harness anomalies",
            f"harness.{col}",
            f"Sonnet no-mcp: {label}",
            get(a, path),
            path,
            hc_cell("q12_safety_harness", col, **{"study.model": m, "study.arm": arm}),
            "q12_safety_harness",
            f"COUNT of invoke_agent spans with a per-calculation filter "
            f"{key.replace('>1', ' > 1')}{'' if '>' in key else ' > 0'}.",
        )
    nw = sum(get(a, f"signals/summary/{m}/no-mcp/runs_with/agent.tool.wait_calls") for m in MODELS)
    add(
        "Time split",
        "runs_wait_calls.no-mcp",
        "no-mcp runs with deliberate sleep polling",
        nw,
        "signals/summary/*/no-mcp/runs_with/agent.tool.wait_calls (sum)",
        sum(
            r["runs_wait_calls"]
            for r in hc_rows("q12_safety_harness")
            if r["study.arm"] == "no-mcp"
        ),
        "q12_safety_harness",
        "COUNT of invoke_agent spans with agent.tool.wait_calls > 0, no-mcp rows summed.",
    )

    # rule enablement
    for m in MODELS:
        for arm in ARMS:
            for kind, n in get(a, f"summary/{m}/{arm}/rule_enablement").items():
                add(
                    "Rule enablement",
                    f"rule.{kind}.{m}.{arm}",
                    f"{SHORT[m]} {arm}: {kind}",
                    n,
                    f"summary/{m}/{arm}/rule_enablement/{kind}",
                    hc_cell(
                        "q14_rule_enablement",
                        "COUNT",
                        absent=0,
                        **{"study.model": m, "study.arm": arm, "study.rule_enablement": kind},
                    ),
                    "q14_rule_enablement",
                    "COUNT of invoke_agent spans grouped by study.model, "
                    "study.arm and study.rule_enablement.",
                )

    # data integrity: spans versus run totals
    for op, total_key, label in (
        ("chat", "agent.model.calls", "chat spans"),
        ("execute_tool", "agent.tool.calls", "execute_tool spans"),
    ):
        our = sum(r["agent"][total_key] for r in a["runs"])
        add(
            "Data integrity",
            f"spans.{op}",
            f"All runs: {label} (= sum of {total_key})",
            our,
            f"runs/*/agent/{total_key} (sum)",
            sum(r["COUNT"] for r in hc_rows("q02_span_counts") if r["gen_ai.operation.name"] == op),
            "q02_span_counts",
            "COUNT of spans grouped by gen_ai.operation.name and study.arm.",
        )
    per_run = {r["study.run_id"]: r for r in hc_rows("q16_per_run_spans")}
    ok = sum(
        1
        for r in a["runs"]
        if (h := per_run.get(r["run_id"]))
        and h["tool_spans"] == r["tool_calls"]
        and h["chat_spans"] == r["agent"]["agent.model.calls"]
        and h["chat_input_tokens"] == r["agent"]["agent.usage.input_tokens"]
    )
    add(
        "Data integrity",
        "per_run_span_sums",
        "Runs whose span-level sums equal the run totals (tool calls, model calls, input tokens)",
        len(a["runs"]),
        "runs (all 180)",
        ok,
        "q16_per_run_spans",
        "COUNT of execute_tool and chat spans and SUM of gen_ai.usage.input_tokens, grouped by "
        "study.run_id (180 rows), compared run by run with the root totals.",
        note="This is the per-run step of a two-step aggregate. Honeycomb returns the 180 "
        "per-run values but cannot take a median over them in the same query; the median works "
        "only because the run total also sits on the root span.",
    )
    nm = sum(
        r["COUNT"]
        for r in hc_rows("q02_span_counts")
        if r["gen_ai.operation.name"] == "chat" and r["study.arm"] == "no-mcp"
    )
    add(
        "Data integrity",
        "count_distinct.chat_span_ids",
        "no-mcp: distinct chat span ids (exactly one per chat span)",
        get(a, "signals/pooled/summary/no-mcp/totals/agent.model.calls"),
        "signals/pooled/summary/no-mcp/totals/agent.model.calls",
        hc_cell(
            "q02_span_counts",
            "COUNT_DISTINCT(trace.span_id)",
            **{"gen_ai.operation.name": "chat", "study.arm": "no-mcp"},
        ),
        "q02_span_counts",
        f"COUNT_DISTINCT of trace.span_id over chat spans, next to COUNT (which returns {nm}).",
        verdict="approximate",
        note="COUNT_DISTINCT is an estimate: exact for the 90 or 180 trace ids used elsewhere, "
        "about 4% high at 1 561 distinct span ids. Use COUNT on one span per entity instead.",
    )
    run = "20261010T184820Z-claude-haiku-5-5-no-mcp-36729c"
    r0 = next(r for r in a["runs"] if r["run_id"] == run)
    hc_tok = hc_cell(
        "q18_ai_conversations", "SUM(conversation.totalTokens)", **{"gen_ai.conversation.id": run}
    )
    add(
        "Agent timeline",
        "ai_conversation.total_tokens",
        "One run (Haiku no-mcp 36729c): total tokens in Honeycomb's AI conversation view",
        r0["total_tokens"],
        "runs[run_id=…36729c]/total_tokens",
        hc_tok,
        "q18_ai_conversations",
        "Honeycomb's built-in conversation query (list_aiconversations): SUM of input + "
        "output tokens over chat-type spans, grouped by gen_ai.conversation.id.",
        verdict="differs",
        note=f"Honeycomb counts tokens on chat spans only. Our sender puts output tokens on the "
        f"invoke_agent span (per-call output tokens are not in the transcript), so the view "
        f"shows input tokens only ({hc_tok} = agent.usage.input_tokens); the "
        f"{r0['total_tokens'] - (hc_tok or 0)} output tokens are missing. Same data, different "
        "placement: not an error in either pipeline.",
    )
    add(
        "Agent timeline",
        "ai_conversation.failures",
        "One run (Haiku no-mcp 36729c): failed tool calls in the AI conversation view",
        r0["agent"]["agent.tool.failures"],
        "runs[run_id=…36729c]/agent/agent.tool.failures",
        hc_cell(
            "q18_ai_conversations", "SUM(conversation.hasError)", **{"gen_ai.conversation.id": run}
        ),
        "q18_ai_conversations",
        "The same built-in query: SUM of a calculated hasError flag (error.type set).",
    )
    add(
        "Runs and success",
        "wall_s",
        "Harness wall clock (secondary measure, wall_s)",
        get(a, "pooled/comparison/all_runs/wall_s/median_without"),
        "pooled/comparison/all_runs/wall_s/median_without",
        None,
        None,
        "Not sent: the traces carry agent.time.wall_ms (the CLI's duration_ms), not the "
        "runner's wall clock.",
        verdict="not_in_dataset",
        note="Add study.wall_s to the root span if the secondary measure should be queryable.",
    )
    return out


VERDICTS = {
    "exact": "Exact",
    "method": "Equal within method difference",
    "approximate": "Approximate (estimator)",
    "differs": "Differs, explained",
    "not_expressible": "Not expressible in Honeycomb",
    "not_in_dataset": "Not in the dataset",
    "disagree": "Disagrees (unexplained)",
}


def build() -> int:
    a = json.loads(ANALYSIS.read_text())
    entries = checks(a)
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    queries = {}
    for p in sorted(RAW.glob("*.json")):
        d = json.loads(p.read_text())
        queries[p.stem] = {
            "run_pk": d["run_pk"],
            "query_url": d["query_url"],
            "spec": d["template"],
            "rows_examined": d["info"]["rows_examined"],
        }
    out = {
        "meta": {
            "analysis": "scripts/experiment/runs/efficiency/full/analysis.json",
            "honeycomb": {
                "team": "pedro-angel",
                "environment": "test",
                "dataset": "mcp-for-kibana-study",
                "window": ["2026-10-10T18:00:00Z", "2026-10-11T02:00:00Z"],
            },
            "raw_results": "scripts/experiment/runs/efficiency/full/honeycomb/",
            "procedure": "queries run through the Honeycomb MCP server's run_query; each "
            "result captured with `efficiency_crosscheck.py capture`; this file "
            "written by `efficiency_crosscheck.py build`",
            "percentile_rule": "Honeycomb P-p of n values = sorted[floor(p*n)] (0-based), "
            "verified on every captured percentile",
            "tolerance": f"relative {TOL} (float attributes are stored with ~7 digits)",
            "verdicts": VERDICTS,
            "counts": dict(sorted(counts.items())),
            "n_checks": len(entries),
        },
        "queries": queries,
        "checks": entries,
    }
    # One line per check keeps the file under the repository's 1 MB limit: each
    # check carries the full spec of its query, and the specs repeat.
    head = json.dumps({k: v for k, v in out.items() if k != "checks"}, indent=1, ensure_ascii=False)
    lines = ",\n".join(
        " " + json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries
    )
    OUT.write_text(head[:-2] + ',\n "checks": [\n' + lines + "\n ]\n}\n")
    print(f"wrote {OUT.relative_to(HERE.parent.parent)}: {len(entries)} checks {counts}")
    bad = [e["id"] for e in entries if e["verdict"] == "disagree"]
    if bad:
        print("DISAGREE: " + ", ".join(bad), file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="store a Honeycomb query result")
    c.add_argument("qid")
    c.add_argument("run_pk")
    c.add_argument("url")
    sub.add_parser("build", help="write honeycomb_crosscheck.json")
    args = ap.parse_args()
    if args.cmd == "capture":
        return capture(args.qid, args.run_pk, args.url)
    return build()


if __name__ == "__main__":
    sys.exit(main())
