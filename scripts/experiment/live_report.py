#!/usr/bin/env python3
"""Build the live-telemetry rerun's results page from committed files.

The rerun (LIVE_RERUN.md) runs Claude Code headless (``claude -p``) with three
Claude models in three arms: without a Kibana MCP server, with mcp-for-kibana,
and with mcp-for-kibana plus Elastic's Elasticsearch MCP server. Every number on
the page is read from

- ``runs/efficiency/live/analysis.json`` (``efficiency_analyze.py``: the
  transcripts),
- ``runs/efficiency/live/honeycomb_crosscheck.json`` (``live_crosscheck.py``:
  Honeycomb queries on the live telemetry, captured, and the hook's per-run
  rollups against the transcripts),
- the run records next to them (``record.json``).

Nothing is typed in by hand. The page stamps the commit and SHA-256 of both
JSON files. Same page conventions as ``efficiency_report.py`` (artifact body,
no ``<html>``), and it reuses that module's helpers.

Deterministic for a given tree. Stdlib only.

    uv run python scripts/experiment/live_report.py --out /tmp/live-report
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import efficiency_report as er

HERE = Path(__file__).resolve().parent
ROOT = HERE / "runs" / "efficiency" / "live"
MODELS = er.MODELS
ARMS = ("no-mcp", "with-mcp", "with-mcp-es")
ARM_LABEL = {
    "no-mcp": "Shell only",
    "with-mcp": "mcp-for-kibana",
    "with-mcp-es": "mcp-for-kibana + Elasticsearch MCP",
}
ARM_CLASS = {"no-mcp": "o", "with-mcp": "w", "with-mcp-es": "x"}
# (A, B, the keys in analysis.json above "comparison" and "signals")
PAIRS = (
    ("with-mcp", "no-mcp", ()),
    ("with-mcp-es", "no-mcp", ("pairs", "with-mcp-es/no-mcp")),
    ("with-mcp-es", "with-mcp", ("pairs", "with-mcp-es/with-mcp")),
)
ES_PREFIX = "mcp__elasticsearch__"
CREATE_DASHBOARD = "mcp__mcp-for-kibana__create_dashboard"

e, money, pct = er.e, er.money, er.pct


def get(a: dict, keys: tuple):
    x = a
    for k in keys:
        x = x[k]
    return x


def forest(chart_id: str, title: str, groups: list, lo: float, hi: float, ticks: tuple) -> str:
    """efficiency_report's interval chart with arm-neutral axis words."""
    desc = "Ratio of medians A / B with 95% bootstrap intervals, log scale."
    wide = er._forest(chart_id, title, desc, groups, lo, hi, ticks, 720, 210, 150)
    narrow = er._forest(chart_id + "n", title, desc, groups, lo, hi, ticks, 400, 120, 116)
    for a, b in (
        ("← less with the server", "← A uses less"),
        ("more with the server →", "A uses more →"),
    ):
        wide = wide.replace(a, b)
        narrow = narrow.replace(a, b)
    return f'<div class="scroll wide">{wide}</div><div class="narrow">{narrow}</div>'


def build(root: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    a_path, x_path = root / "analysis.json", root / "honeycomb_crosscheck.json"
    A = json.loads(a_path.read_text())
    X = json.loads(x_path.read_text())
    repo = HERE.parent.parent
    prov = [
        er.provenance(p)
        if p.resolve().is_relative_to(repo)
        else {"path": str(p), "commit": "outside the repository", "dirty": True, "sha256": "-"}
        for p in (a_path, x_path)
    ]
    runs = A["runs"]
    recs = {
        p.parent.name: json.loads(p.read_text()) for p in sorted(root.glob("b[0-9]*/*/record.json"))
    }
    S, SG = A["summary"], A["signals"]["summary"]
    n_runs = len(runs)
    blocks = sorted({r["block"] for r in runs})
    n_cell = sorted({S[m][a]["n"] for m, _ in MODELS for a in ARMS})
    cli = sorted({str(r["study"]["study.claude_code_version"]) for r in runs})
    kib = sorted({str(r["study"]["study.kibana_version"]) for r in runs})

    def cell(m: str, arm: str) -> list[dict]:
        return [r for r in runs if r["model"] == m and r["arm"] == arm]

    def med(m: str, arm: str, k: str) -> float:
        return S[m][arm]["metrics"][k]["median"]

    def smed(m: str, arm: str, k: str) -> float:
        return SG[m][arm]["distributions"][k]["median"]

    def arm_runs(arm: str) -> list[dict]:
        return [r for r in runs if r["arm"] == arm]

    def cmp(path: tuple, m: str, k: str, subset: str = "all_runs") -> dict:
        return get(A, (*path, "comparison"))[m][subset][k]

    def scmp(path: tuple, m: str, k: str) -> dict:
        return get(A, (*path, "signals", "comparison"))[m][k]

    # ------------------------------------------------------------ headline numbers
    cost_ratio = {
        (wa, oa): [cmp(p, m, "cost_usd")["ratio"] for m, _ in MODELS] for wa, oa, p in PAIRS
    }
    passed = {arm: sum(r["success_strict"] for r in arm_runs(arm)) for arm in ARMS}
    n_arm = {arm: len(arm_runs(arm)) for arm in ARMS}
    known = {arm: SG_pool(A, arm)["runs_with"]["agent.secret.known_exposed"] for arm in ARMS}
    lo_w, hi_w = min(cost_ratio[("with-mcp", "no-mcp")]), max(cost_ratio[("with-mcp", "no-mcp")])
    lo_x, hi_x = (
        min(cost_ratio[("with-mcp-es", "no-mcp")]),
        max(cost_ratio[("with-mcp-es", "no-mcp")]),
    )
    es_vs_w = cost_ratio[("with-mcp-es", "with-mcp")]

    def less_more(lo: float, hi: float) -> str:
        if hi < 1:
            return f"{pct(1 - hi)}–{pct(1 - lo)} less"
        if lo > 1:
            return f"{pct(lo - 1)}–{pct(hi - 1)} more"
        return f"between {pct(1 - lo)} less and {pct(hi - 1)} more"

    xc = X["meta"]["counts"]
    n_checks = X["meta"]["n_checks"]

    tiles = "".join(
        f'<div class="fact"><b>{v}</b><span>{e(t)}</span></div>'
        for v, t in (
            (
                f"{ratio_short(lo_w)}–{ratio_short(hi_w)}",
                "median cost with mcp-for-kibana relative to shell only, by model",
            ),
            (
                f"{ratio_short(lo_x)}–{ratio_short(hi_x)}",
                "median cost with mcp-for-kibana + Elasticsearch MCP relative to shell only",
            ),
            (
                " · ".join(str(passed[a]) for a in ARMS),
                f"passed of {n_arm['no-mcp']} runs each: shell · server · server + ES",
            ),
            (
                f"{xc.get('exact', 0) + xc.get('differs_explained', 0)} / {n_checks}",
                "numbers that Honeycomb's live telemetry reproduces (exactly or with the "
                "difference explained run by run)",
            ),
        )
    )

    # ------------------------------------------------------------ results table
    metrics = (
        ("cost_usd", "Cost", money),
        ("num_turns", "Turns", lambda v: f"{v:g}"),
        ("tool_calls", "Tool calls", lambda v: f"{v:g}"),
        ("duration_ms", "Duration", er.secs),
    )
    head = "".join(f'<th colspan="3" scope="colgroup">{e(lbl)}</th>' for _, lbl, _ in metrics)
    sub = "".join(
        f'<th class="n arm-{ARM_CLASS[a]}" scope="col">{e(["shell", "server", "+ES"][i])}</th>'
        for _ in metrics
        for i, a in enumerate(ARMS)
    )
    rows = []
    for m, label in MODELS:
        tds = "".join(f'<td class="n">{fmt(med(m, a, k))}</td>' for k, _, fmt in metrics for a in ARMS)
        rows.append(f'<tr><th scope="row">{e(label)}</th>{tds}</tr>')
    pass_row = "".join(
        f'<td class="n">{S[m][a]["pass_strict"]}/{S[m][a]["n"]}</td>' for m, _ in MODELS for a in ARMS
    )
    results_table = (
        '<div class="scroll"><table class="results"><thead><tr><th rowspan="2" scope="col">'
        f"Median per run</th>{head}</tr><tr>{sub}</tr></thead><tbody>{''.join(rows)}"
        "</tbody></table></div>"
    )
    pass_table = (
        '<div class="scroll"><table class="compact"><thead><tr><th scope="col">Passed</th>'
        + "".join(f'<th class="n" scope="col">{e(lbl)}</th>' for _, lbl in MODELS)
        + "</tr></thead><tbody>"
        + "".join(
            f'<tr><th scope="row">{e(ARM_LABEL[a])}</th>'
            + "".join(f'<td class="n">{S[m][a]["pass_strict"]} of {S[m][a]["n"]}</td>' for m, _ in MODELS)
            + "</tr>"
            for a in ARMS
        )
        + "</tbody></table></div>"
    )
    del pass_row

    # ------------------------------------------------------------ forests
    def groups_for(k: str, signal: bool = False) -> list:
        out = []
        for wa, oa, p in PAIRS:
            rows_ = []
            for m, label in MODELS:
                c = scmp(p, m, k) if signal else cmp(p, m, k)
                if c:
                    rows_.append((label, c))
            out.append((f"{ARM_LABEL[wa]} / {ARM_LABEL[oa]}", rows_))
        return out

    forest_cost = forest("fcost", "Median cost ratio", groups_for("cost_usd"), 0.1, 4, (0.1, 0.25, 0.5, 1, 2, 4))
    forest_calls = forest(
        "fcalls", "Median model-call ratio", groups_for("agent.model.calls", True), 0.1, 4, (0.1, 0.25, 0.5, 1, 2, 4)
    )
    forest_time = forest(
        "ftime", "Median duration ratio", groups_for("duration_ms"), 0.1, 4, (0.1, 0.25, 0.5, 1, 2, 4)
    )

    # ------------------------------------------------------------ tools and reliability
    def tot(arm: str, k: str) -> int:
        return SG_pool(A, arm)["totals"][k]

    def runs_with(arm: str, k: str) -> int:
        return SG_pool(A, arm)["runs_with"][k]

    curl_runs = {a: runs_with(a, "agent.tool.shell_http_calls") for a in ARMS}
    curl_calls = {a: tot(a, "agent.tool.shell_http_calls") for a in ARMS}
    es_runs = arm_runs("with-mcp-es")
    es_tool_calls: Counter = Counter()
    es_tool_fail: Counter = Counter()
    es_used = 0
    for r in es_runs:
        used = False
        for name, st in r["tool_stats"].items():
            if name.startswith(ES_PREFIX):
                es_tool_calls[name[len(ES_PREFIX) :]] += st["calls"]
                es_tool_fail[name[len(ES_PREFIX) :]] += st["failures"]
                used = used or st["calls"] > 0
        es_used += used
    es_rows = "".join(
        f'<tr><th scope="row"><code>{e(t)}</code></th><td class="n">{c}</td>'
        f'<td class="n">{es_tool_fail[t]}</td></tr>'
        for t, c in sorted(es_tool_calls.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    cd = {"runs": 0, "first_fail": 0}
    for r in runs:
        st = r["tool_stats"].get(CREATE_DASHBOARD)
        if st:
            cd["runs"] += 1
            cd["first_fail"] += st["first_attempt_failures"] > 0
    rel_rows = "".join(
        f'<tr><th scope="row">{e(ARM_LABEL[a])}</th>'
        f'<td class="n">{tot(a, "agent.tool.calls")}</td>'
        f'<td class="n">{tot(a, "agent.tool.failures")}</td>'
        f'<td class="n">{tot(a, "agent.tool.hidden_failures")}</td>'
        f'<td class="n">{curl_runs[a]} of {n_arm[a]}</td>'
        f'<td class="n">{curl_calls[a]}</td></tr>'
        for a in ARMS
    )

    # ------------------------------------------------------------ safety
    env_runs = {a: runs_with(a, "agent.tool.env_enumeration_calls") for a in ARMS}
    key_in_env = {
        a: sum(1 for r in arm_runs(a) if recs[r["run_id"]].get("key_in_model_env")) for a in ARMS
    }
    es_key_seen = sum(1 for r in es_runs if recs[r["run_id"]].get("es_key_in_output"))
    es_key_inval = sum(1 for r in es_runs if recs[r["run_id"]].get("es_key_invalidated"))
    outside = {a: runs_with(a, "agent.fs.outside_workdir_calls") for a in ARMS}
    saf_rows = "".join(
        f'<tr><th scope="row">{e(ARM_LABEL[a])}</th><td class="n">{key_in_env[a]} of {n_arm[a]}</td>'
        f'<td class="n">{env_runs[a]}</td><td class="n">{known[a]}</td><td class="n">{outside[a]}</td></tr>'
        for a in ARMS
    )

    # ------------------------------------------------------------ live telemetry
    tel = [recs[r["run_id"]].get("telemetry") or {} for r in runs]
    forwarded = sum(t.get("forwarded", 0) for t in tel)
    failed_fw = sum(t.get("failed", 0) for t in tel)
    redacted = sum(t.get("secrets_redacted", 0) for t in tel)
    runs_redacted = sum(1 for t in tel if t.get("secrets_redacted"))
    ident = sum(t.get("identity_attrs_dropped", 0) for t in tel)
    anchors = sum(1 for r in arm_runs("no-mcp") if recs[r["run_id"]].get("trace_anchor"))
    hook_p50 = statistics.median(
        recs[r["run_id"]]["agent_live"]["agent.hook.p50_ms"]
        for r in runs
        if (recs[r["run_id"]].get("agent_live") or {}).get("agent.hook.p50_ms") is not None
    )
    groups: dict[str, Counter] = {}
    for c in X["checks"]:
        groups.setdefault(c["group"], Counter())[c["verdict"]] += 1
    xc_rows = "".join(
        f'<tr><th scope="row">{e(g)}</th><td class="n">{v.get("exact", 0)}</td>'
        f'<td class="n">{v.get("differs_explained", 0)}</td><td class="n">{v.get("disagree", 0)}</td></tr>'
        for g, v in groups.items()
    )
    explained = [c for c in X["checks"] if c["verdict"] == "differs_explained"]
    disagree = [c for c in X["checks"] if c["verdict"] == "disagree"]
    expl_kinds = Counter(c["explanation"].split(":")[0].split(" (")[0] for c in explained)
    dis_list = "".join(
        f"<li>{e(c['question'])}: transcripts {e(fmt_v(c['our_value']))}, Honeycomb "
        f"{e(fmt_v(c['honeycomb_value']))}.</li>"
        for c in disagree[:12]
    )
    lvt = X["live_vs_transcript_per_run"]
    lvt_full = sum(1 for v in lvt.values() if v["differ"] == 0)
    cause_label = {
        "blocked_call": "a call the harness blocked before running (no hook event)",
        "clock": "timing: the hook sums Claude Code's per-call durations, the transcript "
        "measures between event timestamps",
        "hook_workdir": "the hook took the work dir from the cwd after a leading cd",
        "result_representation": "the hook sizes the raw tool response, the transcript "
        "the text the model was shown",
        "unexplained": "unexplained",
    }
    lvt_rows = "".join(
        f'<tr><th scope="row"><code>{e(k)}</code></th><td class="n">{v["agree"]}</td>'
        f'<td class="n">{v["differ"]}</td><td>'
        + "; ".join(f"{e(cause_label.get(c, c))} ({n})" for c, n in v.get("causes", {}).items())
        + "</td></tr>"
        for k, v in lvt.items()
        if v["differ"]
    )
    title_calls = sum(
        c["honeycomb_value"] for c in X["checks"] if c["id"].startswith("title_calls.")
    )
    q_urls = {k: v["query_url"] for k, v in X["queries"].items()}

    stamp = "; ".join(
        f"<code>{e(p['path'])}</code> at commit <code>{e(p['commit'])}</code>"
        f"{' (with uncommitted changes)' if p['dirty'] else ''}, SHA-256 <code>{e(p['sha256'])}</code>"
        for p in prov
    )
    css = er_css()
    page = f"""<title>Kibana MCP Live Rerun</title>
{css}
<main>
<p><span class="draft">Draft · private until published</span></p>
<p class="eyebrow">mcp-for-kibana · efficiency study, live-telemetry rerun · Kibana {e(", ".join(kib))} · Claude Code {e(", ".join(cli))} · {n_runs} runs</p>
<h1>Claude Code on a Kibana task: shell only, mcp-for-kibana, or both MCP servers</h1>
<p class="lede">This page measures <b>Claude Code</b>, run headless (<code>claude -p</code>), with each of three Claude models: Haiku 5.5, Sonnet 5.5 and Opus 5.5. The numbers describe the agent and the model together (Claude Code's system prompt, built-in tools, tool loop and prompt caching), not the bare models. On one end-to-end Kibana task, a run with <b>mcp-for-kibana</b> cost {less_more(lo_w, hi_w)} at list price than a run with <b>the shell only</b> (median, per model). With <b>mcp-for-kibana and Elastic's Elasticsearch MCP server</b> together it cost {less_more(lo_x, hi_x)} than the shell only, and {less_more(min(es_vs_w), max(es_vs_w))} than with mcp-for-kibana alone. Every number was computed twice: from the run transcripts, and from telemetry that Claude Code, a silent hook, the server and the runner sent to Honeycomb while the runs happened.</p>
<div class="facts">{tiles}</div>

<h2>What was compared</h2>
<p>Each run gave Claude Code the same prompt: explore the data view of Kibana's web-logs sample data, build a dashboard of at least three panels, and create an alert rule that checks every minute and fires when more than 10 requests with a 5xx response arrive within 5 minutes; finish when the dashboard and the rule exist <b>and the rule is enabled</b>. The prompt named the environment variables for Kibana's URL and API key and pointed to Kibana's OpenAPI reference in the working directory.</p>
<div class="arms">
  <div class="arm o"><h3>{e(ARM_LABEL["no-mcp"])}</h3><p>Built-in shell and file tools. The model reads the API reference and writes the requests itself; the Kibana key is in its environment.</p></div>
  <div class="arm w"><h3>{e(ARM_LABEL["with-mcp"])}</h3><p>The mcp-for-kibana server (dashboards, data management and alerting at the default write tier) next to the same built-in tools. The server holds the key; the model's environment does not ({key_in_env["with-mcp"]} of {n_arm["with-mcp"]} runs had it).</p></div>
  <div class="arm x"><h3>{e(ARM_LABEL["with-mcp-es"])}</h3><p>Both MCP servers side by side: mcp-for-kibana and Elastic's Elasticsearch MCP server (0.4.6, deprecated upstream, pinned by digest) on a per-run read-only key for the web-logs index, minted and invalidated by the runner.</p></div>
</div>
<p>{len(n_cell) == 1 and n_cell[0] or n_cell} runs per model and arm: {n_runs} runs in {len(blocks)} blocks, each block running every model × arm once, with the arm order rotated so each arm takes each position equally often. Kibana {e(", ".join(kib))} was reset after every run. A run passed when Kibana held a dashboard with at least three panels on the web-logs data and an enabled alert rule on that data.</p>

<h2>Results by model</h2>
<p>Medians per cell. Shell = shell only; server = mcp-for-kibana; +ES = both MCP servers.</p>
{results_table}
{pass_table}
<p>Ratios of medians with 95% bootstrap intervals ({A["meta"]["bootstrap_resamples"]:,} resamples). A ratio below 1 means arm A used less than arm B.</p>
<h3>Cost</h3>
{forest_cost}
<h3>Model calls</h3>
{forest_calls}
<h3>Duration</h3>
{forest_time}

<h2>Tools, detours and failures</h2>
<div class="scroll"><table class="results"><thead><tr><th scope="col">All runs</th><th class="n" scope="col">Tool calls</th><th class="n" scope="col">Failed</th><th class="n" scope="col">Hidden failures</th><th class="n" scope="col">Runs that curled Kibana or ES</th><th class="n" scope="col">Shell HTTP calls</th></tr></thead><tbody>{rel_rows}</tbody></table></div>
<p>A hidden failure is a shell call that exited 0 while its output carried an HTTP error. <code>create_dashboard</code>'s first call failed in {cd["first_fail"]} of the {cd["runs"]} runs that called it (in the first study it failed in all 90 server runs; the server's fix, commit <code>48b865b</code>, ran in this rerun).</p>
<h3>The Elasticsearch MCP server</h3>
<p>{es_used} of {len(es_runs)} runs in the third arm called it. Calls by tool:</p>
<div class="scroll"><table class="compact"><thead><tr><th scope="col">Tool</th><th class="n" scope="col">Calls</th><th class="n" scope="col">Failed</th></tr></thead><tbody>{es_rows}</tbody></table></div>

<h2>Safety</h2>
<div class="scroll"><table class="results"><thead><tr><th scope="col">Runs</th><th class="n" scope="col">Kibana key in the model's environment</th><th class="n" scope="col">Listed the environment</th><th class="n" scope="col">Kibana key reached the model</th><th class="n" scope="col">Read outside the work dir</th></tr></thead><tbody>{saf_rows}</tbody></table></div>
<p>The Elasticsearch key appeared in the transcript or stderr of {es_key_seen} of {len(es_runs)} runs and was invalidated after {es_key_inval} of them.</p>

<h2>Live telemetry</h2>
<p>Each run is one trace in the Honeycomb dataset <code>mcp-for-kibana-live</code> (environment <code>test</code>, EU), kept apart from the owner's <code>claude-code</code> dataset. Every component sent its own signals while the run happened, through a loopback relay the runner started per run; the relay dropped the account identity attributes ({ident:,} of them), scrubbed the run's keys from every string, and kept a copy (<code>telemetry.jsonl.gz</code> in each run directory).</p>
<ul>
<li><b>Claude Code</b>: spans (<code>claude_code.interaction</code>, one <code>llm_request</code> per model call, <code>tool</code> spans) and log events per call: the prompt and each response as text, <code>api_request</code> with tokens and cost, tool decisions and inputs. Raw API bodies were not logged.</li>
<li><b>The agent-signals hook</b>: a silent command hook that sent one <code>agent.tool</code> span per tool call with the <code>agent.*</code> signals (failure class, hidden failure, retries, secret exposure, curl detours). Median cost {hook_p50:.0f} ms per call.</li>
<li><b>mcp-for-kibana</b>: its own spans per MCP request, in the run's trace. The Elasticsearch MCP server has no OpenTelemetry; its calls appear through Claude Code's and the hook's spans.</li>
<li><b>The runner</b>: the root span <code>efficiency.run</code> with the outcome and the hook's per-run rollups.</li>
</ul>
<p>The relay forwarded {forwarded:,} OTLP requests; {failed_fw} failed. It replaced a key in {redacted} strings across {runs_redacted} runs.</p>
<h3>What Honeycomb showed</h3>
<p>{n_checks} numbers were computed a second time with Honeycomb queries on the live data (<a href="{e(q_urls.get("q03_api_request", "#"))}">for example the per-call cost query</a>).</p>
<div class="scroll"><table class="compact"><thead><tr><th scope="col">Checks</th><th class="n" scope="col">Equal</th><th class="n" scope="col">Differ, explained</th><th class="n" scope="col">Disagree</th></tr></thead><tbody>{xc_rows}</tbody></table></div>
<ul>
<li><b>Claude Code makes a call the transcript does not show.</b> Every run had {e(fmt_v(title_calls / n_runs if n_runs else 0))} <code>generate_session_title</code> call on Haiku on average ({title_calls} in all). Its cost is inside the run's reported total; its tokens are not in the result's token counts.</li>
<li><b>Without any MCP server, Claude Code's spans were orphans.</b> Claude Code {e(", ".join(cli))} starts its first query before its tracer is ready when no MCP server is configured: no interaction span, and every model and tool span became its own trace. The shell-only arm therefore connected a tool-less MCP server ({anchors} of {n_arm["no-mcp"]} runs); the model's tool list and first prompt were unchanged.</li>
{"".join(f"<li><b>{e(k)}</b>: {v} check(s).</li>" for k, v in expl_kinds.items())}
</ul>
{"<p>Checks that disagree:</p><ul>" + dis_list + "</ul>" if disagree else ""}
<h3>The hook against the transcripts, run by run</h3>
<p>The hook classifies each call live with the same function the transcript pipeline uses. {lvt_full} of {len(lvt)} run-level signals agree in every run.{" Where they differ:" if lvt_rows else ""}</p>
{'<div class="scroll"><table class="results"><thead><tr><th scope="col">Signal</th><th class="n" scope="col">Runs equal</th><th class="n" scope="col">Runs differing</th><th scope="col">Cause (runs)</th></tr></thead><tbody>' + lvt_rows + "</tbody></table></div>" if lvt_rows else ""}

<h2>Method and caveats</h2>
<ul>
<li><b>Agent, not model.</b> Every result is Claude Code with a model. Another agent on the same models could come out differently.</li>
<li><b>One task, one dataset, one Kibana version.</b> Cost is list price as Claude Code reports it and leaves out running the servers.</li>
<li><b>Measures.</b> Transcript numbers run up to the model's own end of turn; comparisons are ratios of medians with percentile bootstrap intervals (seed {A["meta"]["bootstrap_seed"]}). Honeycomb's percentile has no interpolation; the cross-check applies its rule to our per-run values.</li>
<li><b>The Elasticsearch MCP server is deprecated</b> (security fixes only) in favour of Kibana's Agent Builder MCP endpoint, which needs an Enterprise licence. Its <code>get_mappings</code> fails on the web-logs data stream.</li>
</ul>
<footer><p>Generated by <code>scripts/experiment/live_report.py</code> from {stamp}, and the run records under <code>scripts/experiment/runs/efficiency/live/</code>.</p></footer>
</main>
"""
    (out_dir / "index.html").write_text(page)
    print(f"wrote {out_dir / 'index.html'}")


def SG_pool(A: dict, arm: str) -> dict:
    return A["signals"]["pooled"]["summary"][arm]


def ratio_short(r: float) -> str:
    return f"{r:.2f}×"


def fmt_v(v) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def er_css() -> str:
    """efficiency_report's stylesheet, with a third arm colour."""
    src = (HERE / "efficiency_report.py").read_text()
    start = src.index('<link rel="preconnect"')
    end = src.index("</style>", start) + len("</style>")
    css = src[start:end].replace("{{", "{").replace("}}", "}")
    extra = (
        ":root{--es:#1baf7a}"
        ".arm.x h3{border-left:4px solid var(--es)} .arm-x{box-shadow:inset 0 -3px 0 var(--es)}"
    )
    return css.replace("</style>", extra + "</style>")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args()
    build(args.root, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
