#!/usr/bin/env python3
"""Cross-check the live rerun's numbers: transcripts versus live telemetry.

The rerun (LIVE_RERUN.md) computes every number twice (R7):

1. **Transcripts.** ``efficiency_analyze.py --root runs/efficiency/live``
   re-derives every run from ``transcript.jsonl`` into
   ``runs/efficiency/live/analysis.json``.
2. **Live telemetry in Honeycomb** (team pedro-angel, environment ``test``,
   dataset ``mcp-for-kibana-live``): what Claude Code, the agent-signals hook,
   the MCP server and the runner emitted while the runs happened. Nothing in
   this dataset was rebuilt from transcripts.

The Honeycomb key can send events but not run queries, so queries run through
the Honeycomb MCP server's ``run_query`` and are captured as in
``efficiency_crosscheck.py``:

    python3 scripts/experiment/live_crosscheck.py specs            # the query specs to run
    python3 scripts/experiment/live_crosscheck.py capture q01_roots RUN_PK 'https://mcp…'
    python3 scripts/experiment/live_crosscheck.py build

``build`` writes ``runs/efficiency/live/honeycomb_crosscheck.json``: for each
check our value, Honeycomb's value, the query, a verdict and, where they
differ, the per-run evidence of why. It also compares, run by run and without
Honeycomb, the hook's live rollups (``record.json`` ``agent_live``) with the
transcript pipeline's ``agent.*`` values.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE / "runs" / "efficiency" / "live"
ANALYSIS = LIVE / "analysis.json"
RAW = LIVE / "honeycomb"
OUT = LIVE / "honeycomb_crosscheck.json"
DATASET = "mcp-for-kibana-live"
UI = f"https://ui.eu1.honeycomb.io/pedro-angel/environments/test/datasets/{DATASET}"

MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
ARMS = ("with-mcp", "no-mcp", "with-mcp-es")
SHORT = {"claude-haiku-5-5": "Haiku", "claude-sonnet-5-5": "Sonnet", "claude-opus-5-5": "Opus"}
BY_CELL = ["study.model", "study.arm"]
# Study blocks are b01..b30; the smoke runs (block "smoke") are left out.
STUDY = [{"column": "study.block", "op": "starts-with", "value": "b"}]
TOL = 1e-6  # relative; Honeycomb stores float attributes with ~7 significant digits
CDASH = "mcp__mcp-for-kibana__create_dashboard"


def _count_if(name: str, column: str, value=True) -> dict:
    return {"op": "COUNT", "name": name, "filters": [{"column": column, "op": "=", "value": value}]}


def _sum(column: str) -> dict:
    return {"op": "SUM", "column": column}


def specs(window: tuple[str, str]) -> dict[str, dict]:
    """The Honeycomb queries, as run through run_query (dataset DATASET)."""
    frm, to = window
    base = {"breakdowns": BY_CELL, "from": frm, "to": to, "limit": 100}
    root = [{"column": "name", "op": "=", "value": "efficiency.run"}, *STUDY]
    hook = [{"column": "name", "op": "starts-with", "value": "agent.tool "}, *STUDY]
    return {
        "q01_roots": {
            **base,
            "filters": root,
            "calculations": [
                {"op": "COUNT"},
                _count_if("pass", "study.success"),
                *(
                    _sum(c)
                    for c in (
                        "study.total_cost_usd",
                        "study.num_turns",
                        "agent.tool.calls",
                        "agent.tool.calls_mcp",
                        "agent.tool.calls_shell",
                        "agent.tool.failures",
                        "agent.tool.hidden_failures",
                        "agent.tool.first_attempt_failures",
                        "agent.tool.retries",
                        "agent.tool.shell_http_calls",
                        "agent.tool.env_enumeration_calls",
                        "agent.secret.exposures",
                        "agent.permission.denials",
                        "agent.fs.outside_workdir_calls",
                        "study.n_es_mcp_calls",
                    )
                ),
                _count_if("secret_exposed_runs", "agent.secret.exposed"),
                _count_if("known_secret_runs", "agent.secret.known_exposed"),
            ],
        },
        "q02_p50": {
            **base,
            "filters": root,
            "calculations": [
                {"op": "P50", "column": c}
                for c in ("study.total_cost_usd", "study.num_turns", "agent.tool.calls")
            ],
        },
        "q03_api_request": {
            **base,
            "filters": [{"column": "event.name", "op": "=", "value": "api_request"}, *STUDY],
            "calculations": [
                {"op": "COUNT"},
                *(
                    _sum(c)
                    for c in (
                        "cost_usd",
                        "input_tokens",
                        "output_tokens",
                        "cache_read_tokens",
                        "cache_creation_tokens",
                    )
                ),
            ],
        },
        "q04_llm_request": {
            **base,
            "filters": [{"column": "name", "op": "=", "value": "claude_code.llm_request"}, *STUDY],
            "calculations": [{"op": "COUNT"}],
        },
        "q05_llm_request_in_run_trace": {
            **base,
            "filters": [
                {"column": "name", "op": "=", "value": "claude_code.llm_request"},
                {"column": "root.name", "op": "=", "value": "efficiency.run"},
                *STUDY,
            ],
            "calculations": [{"op": "COUNT"}],
        },
        "q06_interaction": {
            **base,
            "filters": [
                {"column": "name", "op": "=", "value": "claude_code.interaction"},
                {"column": "root.name", "op": "=", "value": "efficiency.run"},
                *STUDY,
            ],
            "calculations": [{"op": "COUNT"}],
        },
        "q07_cc_tool": {
            **base,
            "filters": [{"column": "name", "op": "=", "value": "claude_code.tool"}, *STUDY],
            "calculations": [{"op": "COUNT"}],
        },
        "q08_hook_tool": {
            **base,
            "filters": hook,
            "calculations": [
                {"op": "COUNT"},
                _count_if("failed", "agent.tool.failed"),
                _count_if("hidden_failure", "agent.tool.hidden_failure"),
                _count_if("first_attempt_failed", "agent.tool.first_attempt_failed"),
                _count_if("shell_http", "agent.tool.shell_http"),
                _count_if("es_calls", "agent.tool.mcp_server", "elasticsearch"),
            ],
        },
        "q09_create_dashboard_first": {
            **base,
            "filters": [
                *hook,
                {"column": "gen_ai.tool.name", "op": "=", "value": CDASH},
                {"column": "agent.tool.first_attempt", "op": "=", "value": True},
            ],
            "calculations": [
                {"op": "COUNT"},
                _count_if("first_attempt_failed", "agent.tool.first_attempt_failed"),
            ],
        },
    }


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


# ---------------------------------------------------------------- helpers


def hc_rows(qid: str) -> list[dict]:
    return [r["data"] for r in json.loads((RAW / f"{qid}.json").read_text())["results"]]


def hc_meta(qid: str) -> dict:
    d = json.loads((RAW / f"{qid}.json").read_text())
    return {"run_pk": d["run_pk"], "query_url": d["query_url"], "spec": d["template"]}


def hc_cell(qid: str, col: str, model: str, arm: str, absent=0):
    for row in hc_rows(qid):
        if row.get("study.model") == model and row.get("study.arm") == arm:
            v = row.get(col)
            return absent if v is None else v
    return absent


def hc_pct(values: list, p: float):
    """Honeycomb's percentile: sorted[floor(p*n)] (0-based), no interpolation
    (verified on the first study's captured percentiles)."""
    v = sorted(values)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def same(x, y) -> bool:
    if x is None or y is None:
        return False
    return abs(x - y) <= TOL * max(1.0, abs(x), abs(y))


def transcript_facts(run_dir: Path) -> dict:
    """Per-run facts the live side cannot see, read from the transcript up to
    the first result event: tool calls the harness blocked before running
    (no hook event, no tool span), and model calls by assistant message id."""
    blocked = 0
    results: dict[str, dict] = {}
    for line in (run_dir / "transcript.jsonl").read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "result":
            break
        if e.get("type") == "user" and isinstance(e.get("message", {}).get("content"), list):
            for c in e["message"]["content"]:
                if c.get("type") == "tool_result":
                    results[c["tool_use_id"]] = c
    for c in results.values():
        content = c.get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        if c.get("is_error") and "<tool_use_error>Blocked" in text:
            blocked += 1
    return {"blocked_tool_calls": blocked}


# ---------------------------------------------------------------- the checks


def checks(a: dict, run_dirs: dict[str, Path]) -> list[dict]:
    out: list[dict] = []
    runs = a["runs"]

    def cell(m, arm):
        return [r for r in runs if r["model"] == m and r["arm"] == arm]

    facts = {r["run_id"]: transcript_facts(run_dirs[r["run_id"]]) for r in runs}

    def add(group, cid, question, our, our_source, hc, qid, explain, expected=None, why=""):
        meta = hc_meta(qid)
        if same(our, hc):
            verdict = "exact"
        elif expected is not None and same(expected, hc):
            verdict = "differs_explained"
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
                "expected_from_explanation": expected,
                "explanation": why if verdict == "differs_explained" else "",
                "query_id": qid,
                "query_explained": explain,
                "query_spec": meta["spec"],
                "query_url": meta["query_url"],
                "verdict": verdict,
            }
        )

    for m in MODELS:
        for arm in ARMS:
            rs = cell(m, arm)
            if not rs:
                continue
            tag = f"{SHORT[m]} {arm}"
            s = f"summary/{m}/{arm}"
            add("Runs", f"n.{m}.{arm}", f"{tag}: runs", len(rs), f"{s}/n",
                hc_cell("q01_roots", "COUNT", m, arm), "q01_roots",
                "COUNT of efficiency.run root spans (runner, sent at run end).")
            add("Runs", f"pass.{m}.{arm}", f"{tag}: strict passes",
                sum(r["success_strict"] for r in rs), f"{s}/pass_strict",
                hc_cell("q01_roots", "pass", m, arm), "q01_roots",
                "COUNT of root spans with study.success = true (the runner's scorer).")
            add("Trace", f"interaction.{m}.{arm}", f"{tag}: runs whose Claude Code "
                "interaction span sits in the run's trace", len(rs), f"{s}/n",
                hc_cell("q06_interaction", "COUNT", m, arm), "q06_interaction",
                "COUNT of claude_code.interaction spans whose trace root is efficiency.run "
                "(one per run when Claude Code joined the runner's TRACEPARENT).")

            # Cost and tokens: transcript result event vs Claude Code's api_request events.
            cost = sum(r["cost_usd"] for r in rs)
            add("Cost and tokens", f"cost.{m}.{arm}", f"{tag}: total cost (USD)", cost,
                "sum of runs[].cost_usd (first result event)",
                hc_cell("q03_api_request", "SUM(cost_usd)", m, arm), "q03_api_request",
                "SUM(cost_usd) of Claude Code's api_request log events (one per API call).")
            for ours, col, label in (
                ("output_tokens", "SUM(output_tokens)", "output tokens"),
                ("cache_read_input_tokens", "SUM(cache_read_tokens)", "cache-read tokens"),
                ("cache_creation_input_tokens", "SUM(cache_creation_tokens)", "cache-write tokens"),
                ("input_tokens", "SUM(input_tokens)", "uncached input tokens"),
            ):
                add("Cost and tokens", f"{ours}.{m}.{arm}", f"{tag}: {label}",
                    sum(r[ours] for r in rs), f"sum of runs[].{ours}",
                    hc_cell("q03_api_request", col, m, arm), "q03_api_request",
                    f"{col} of api_request log events.")

            calls = sum(r["agent"]["agent.model.calls"] for r in rs)
            add("Model calls", f"model_calls_api.{m}.{arm}", f"{tag}: model calls",
                calls, "sum of runs[].agent['agent.model.calls'] (distinct assistant message ids)",
                hc_cell("q03_api_request", "COUNT", m, arm), "q03_api_request",
                "COUNT of api_request log events.")
            add("Model calls", f"model_calls_span.{m}.{arm}", f"{tag}: model calls (spans)",
                calls, "sum of runs[].agent['agent.model.calls']",
                hc_cell("q04_llm_request", "COUNT", m, arm), "q04_llm_request",
                "COUNT of claude_code.llm_request spans.")
            add("Trace", f"llm_in_trace.{m}.{arm}", f"{tag}: model-call spans inside the "
                "run's trace", hc_cell("q04_llm_request", "COUNT", m, arm),
                "q04_llm_request (all llm_request spans)",
                hc_cell("q05_llm_request_in_run_trace", "COUNT", m, arm),
                "q05_llm_request_in_run_trace",
                "COUNT of llm_request spans whose trace root is efficiency.run.")

            # Tools: transcript tool_use blocks vs Claude Code's tool spans and the hook.
            tools = sum(r["tool_calls"] for r in rs)
            blocked = sum(facts[r["run_id"]]["blocked_tool_calls"] for r in rs)
            why = (f"{blocked} tool call(s) the harness blocked before running (tool_result "
                   "'<tool_use_error>Blocked…'): in the transcript, but no tool span and no "
                   "PostToolUse hook event")
            add("Tool calls", f"tools_cc.{m}.{arm}", f"{tag}: tool calls (Claude Code spans)",
                tools, "sum of runs[].tool_calls", hc_cell("q07_cc_tool", "COUNT", m, arm),
                "q07_cc_tool", "COUNT of claude_code.tool spans.", tools - blocked, why)
            add("Tool calls", f"tools_hook.{m}.{arm}", f"{tag}: tool calls (hook spans)",
                tools, "sum of runs[].tool_calls", hc_cell("q08_hook_tool", "COUNT", m, arm),
                "q08_hook_tool", "COUNT of the hook's agent.tool spans.", tools - blocked, why)
            for ours, col, label in (
                ("agent.tool.failures", "failed", "failed tool calls"),
                ("agent.tool.hidden_failures", "hidden_failure", "hidden failures (exit 0, HTTP error)"),
                ("agent.tool.first_attempt_failures", "first_attempt_failed", "first-attempt failures"),
                ("agent.tool.shell_http_calls", "shell_http", "shell HTTP calls (curl to Kibana or ES)"),
            ):
                add("Tool reliability", f"{ours}.{m}.{arm}", f"{tag}: {label}",
                    sum(r["agent"][ours] for r in rs), f"sum of runs[].agent['{ours}']",
                    hc_cell("q08_hook_tool", col, m, arm), "q08_hook_tool",
                    f"COUNT of hook spans with the flag set ({col}).")
            for ours, col, label in (
                ("agent.secret.exposures", "SUM(agent.secret.exposures)", "secret exposures"),
                ("agent.tool.env_enumeration_calls", "SUM(agent.tool.env_enumeration_calls)",
                 "environment listings"),
                ("agent.permission.denials", "SUM(agent.permission.denials)", "permission denials"),
            ):
                add("Safety", f"{ours}.{m}.{arm}", f"{tag}: {label}",
                    sum(r["agent"][ours] for r in rs), f"sum of runs[].agent['{ours}']",
                    hc_cell("q01_roots", col, m, arm), "q01_roots",
                    f"{col} on root spans (the hook's per-run rollup).")

            # Medians, Honeycomb's percentile rule applied to our per-run values.
            for ours, col, label in (
                ("cost_usd", "P50(study.total_cost_usd)", "median cost"),
                ("num_turns", "P50(study.num_turns)", "median turns"),
                ("tool_calls", "P50(agent.tool.calls)", "median tool calls"),
            ):
                vals = [r[ours] for r in rs]
                add("Medians", f"median.{ours}.{m}.{arm}", f"{tag}: {label}",
                    hc_pct(vals, 0.5), f"runs[].{ours}, rank floor(n/2) of the sorted values",
                    hc_cell("q02_p50", col, m, arm, None), "q02_p50",
                    f"{col} on root spans.")
    return out


def live_vs_transcript(a: dict, run_dirs: dict[str, Path]) -> dict:
    """Per signal, how many runs have the hook's live rollup (record.json
    agent_live) equal to the transcript pipeline's value."""
    agree: dict[str, list[int]] = {}
    diffs: dict[str, list[dict]] = {}
    for r in a["runs"]:
        rec = json.loads((run_dirs[r["run_id"]] / "record.json").read_text())
        live = rec.get("agent_live") or {}
        for k, v in live.items():
            if k.startswith("agent.hook.") or k not in r["agent"]:
                continue
            t = r["agent"][k]
            ok = (v == t) if not isinstance(v, (int, float)) or isinstance(v, bool) else same(v, t)
            a_ = agree.setdefault(k, [0, 0])
            a_[0 if ok else 1] += 1
            if not ok and len(diffs.setdefault(k, [])) < 5:
                diffs[k].append({"run_id": r["run_id"], "live": v, "transcript": t})
    return {
        k: {"agree": v[0], "differ": v[1], "examples": diffs.get(k, [])}
        for k, v in sorted(agree.items())
    }


def build() -> int:
    a = json.loads(ANALYSIS.read_text())
    run_dirs = {p.parent.name: p.parent for p in LIVE.glob("b[0-9]*/*/record.json")}
    entries = checks(a, run_dirs)
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
            "analysis": "scripts/experiment/runs/efficiency/live/analysis.json",
            "honeycomb": {"team": "pedro-angel", "environment": "test", "dataset": DATASET},
            "raw_results": "scripts/experiment/runs/efficiency/live/honeycomb/",
            "procedure": "queries from `live_crosscheck.py specs` run through the Honeycomb "
            "MCP server's run_query; each result captured with `live_crosscheck.py capture`; "
            "this file written by `live_crosscheck.py build`",
            "percentile_rule": "Honeycomb P-p of n values = sorted[floor(p*n)] (0-based)",
            "tolerance": f"relative {TOL}",
            "verdicts": {
                "exact": "Equal",
                "differs_explained": "Differs; the per-run explanation predicts Honeycomb's value",
                "disagree": "Disagrees (see note in the report)",
            },
            "counts": dict(sorted(counts.items())),
            "n_checks": len(entries),
        },
        "queries": queries,
        "live_vs_transcript_per_run": live_vs_transcript(a, run_dirs),
        "checks": entries,
    }
    head = json.dumps({k: v for k, v in out.items() if k != "checks"}, indent=1, ensure_ascii=False)
    lines = ",\n".join(
        " " + json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries
    )
    OUT.write_text(head[:-2] + ',\n "checks": [\n' + lines + "\n ]\n}\n")
    print(f"wrote {OUT.relative_to(HERE.parent.parent)}: {len(entries)} checks {counts}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("specs", help="print the query specs for run_query")
    sp.add_argument("--from", dest="frm", required=True, help="RFC3339 start of the study")
    sp.add_argument("--to", required=True, help="RFC3339 end of the study")
    c = sub.add_parser("capture", help="store a Honeycomb query result")
    c.add_argument("qid")
    c.add_argument("run_pk")
    c.add_argument("url")
    sub.add_parser("build", help="write honeycomb_crosscheck.json")
    args = ap.parse_args()
    if args.cmd == "specs":
        print(json.dumps(specs((args.frm, args.to)), indent=1))
        return 0
    if args.cmd == "capture":
        return capture(args.qid, args.run_pk, args.url)
    return build()


if __name__ == "__main__":
    sys.exit(main())
