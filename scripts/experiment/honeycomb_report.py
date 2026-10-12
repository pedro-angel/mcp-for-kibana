#!/usr/bin/env python3
"""Render the Honeycomb cross-check as one HTML report page.

Reads ``runs/efficiency/full/honeycomb_crosscheck.json`` (written by
``efficiency_crosscheck.py build``) and writes a self-contained page: the
verdict, how far Honeycomb's numbers sit from ours, every query explained in
plain words with its Honeycomb link, the full table of checks, what Honeycomb
adds and lacks, and recommendations for the instrumentation.

    python3 scripts/experiment/honeycomb_report.py [--out PATH]

Stdlib only, deterministic. The page follows the claude.ai artifact rules (no
html/head/body tags, its own <title> and <style>, light and dark tokens).
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "runs" / "efficiency" / "full" / "honeycomb_crosscheck.json"
DEFAULT_OUT = Path(
    "/tmp/claude-0/-home-user-mcp-for-kibana/0536cbf5-c97a-5467-80bd-e50c5d30a2ce/"
    "scratchpad/hcreport/index.html"
)

BUBBLEUP = (
    "https://ui.eu1.honeycomb.io/pedro-angel/environments/test/datasets/mcp-for-kibana-study/"
    "result/xK-eFpYZ7Pip?tab=bubbleup&bubbleup_result=9e-LLTvQRzFw"
)

# Verdict buckets for the headline, in fixed order (categorical slots 1-4).
BUCKETS = (
    ("exact", ("exact",), "Exact", "Same number, to float precision."),
    (
        "method",
        ("method",),
        "Within method difference",
        "Honeycomb's percentile rule differs from ours; applying its rule to our runs "
        "gives its number exactly.",
    ),
    (
        "explained",
        ("approximate", "differs"),
        "Differs, explained",
        "An estimator (COUNT_DISTINCT) or a different definition.",
    ),
    (
        "none",
        ("not_expressible", "not_in_dataset"),
        "Not reproducible",
        "No query can produce it (bootstrap CIs) or the value was never sent.",
    ),
)
CHIP = {
    "exact": ("b1", "exact"),
    "method": ("b2", "method"),
    "approximate": ("b3", "estimate"),
    "differs": ("b3", "differs"),
    "not_expressible": ("b4", "not expressible"),
    "not_in_dataset": ("b4", "not sent"),
    "disagree": ("bad", "disagrees"),
}

QUERIES = {
    "q01_counts": (
        "Runs and passes per cell",
        "Counts the root spans (one per run) for each model and arm, and the same "
        "count restricted to runs whose study.success_strict or success_lenient is "
        "true.",
    ),
    "q02_span_counts": (
        "Spans per operation",
        "Counts spans by gen_ai.operation.name and arm, next to a COUNT_DISTINCT "
        "of span and trace ids, to check the dataset holds every run, model call "
        "and tool call once.",
    ),
    "q03_p50": (
        "Medians per cell",
        "P50 of each run-total attribute (agent.cost.usd, token counts, model calls, "
        "turns, tool calls, durations) on root spans, grouped by study.model and "
        "study.arm. Total tokens is a calculated field adding input and output.",
    ),
    "q04_quartiles": (
        "Quartiles per cell",
        "P25 and P75 of the same attributes, same grouping; the report subtracts them for the IQR.",
    ),
    "q05_ratios_by_model": (
        "With ÷ without, per model",
        "Two P50s per metric, one filtered to with-mcp and one to no-mcp, "
        "divided by a formula, grouped by study.model.",
    ),
    "q06_ratios_pooled": (
        "With ÷ without, all models",
        "The same ratio formulas without grouping, plus pass-through formulas "
        "for the pooled medians of model share and tool-result bytes.",
    ),
    "q07_tool_reliability": (
        "Tool reliability per arm",
        "Counts execute_tool spans per arm, with filtered counts for "
        "failed, hidden, harness-flagged, retried, recovered and "
        "first-attempt-failed calls; formulas turn them into rates.",
    ),
    "q08_failures_by_class": (
        "Failures by error class",
        "Counts failed execute_tool spans, grouped by arm and agent.tool.error_class.",
    ),
    "q09_by_tool": (
        "Per-tool reliability",
        "Counts calls, failures, first-attempt failures and retries per tool name, "
        "and counts distinct runs where the first call failed or a call succeeded.",
    ),
    "q10_bypass_targets": (
        "Where the shell HTTP calls went",
        "Counts with-mcp shell HTTP spans grouped by agent.tool.target "
        "(method and path, ids folded).",
    ),
    "q11_shell_http": (
        "Shell HTTP bypass",
        "Counts shell HTTP spans and the distinct runs that made them, with "
        "failed and hidden-failure counts, grouped by arm and model.",
    ),
    "q12_safety_harness": (
        "Safety and harness flags per run",
        "Counts root spans whose run totals flag an env dump, a secret "
        "exposure, a second result event, a timed-out call or deliberate "
        "waits, and sums permission denials, per cell.",
    ),
    "q13_safety_spans": (
        "Safety from the tool spans",
        "The same safety counts derived from the child spans: distinct runs "
        "with an env-dumping call or a secret on a span, and denied calls.",
    ),
    "q14_rule_enablement": (
        "Rule enablement mix",
        "Counts runs grouped by model, arm and study.rule_enablement.",
    ),
    "q15_time_split": (
        "Time split",
        "P50 of model share, busy tool time, tool time, wait time and time to "
        "the first tool call per cell, with sums of wait time, timed-out calls, "
        "model calls and tool calls.",
    ),
    "q16_per_run_spans": (
        "Per-run sums from spans",
        "Counts chat and execute_tool spans and sums chat input tokens per "
        "study.run_id: 180 rows, compared run by run with the root totals.",
    ),
    "q17_bubbleup_source": (
        "BubbleUp baseline",
        "Counts with-mcp tool spans by agent.tool.error; BubbleUp then "
        "compares the 111 flagged spans with the other 666.",
    ),
    "q18_ai_conversations": (
        "AI conversation view",
        "Honeycomb's own conversation listing: per gen_ai.conversation.id, "
        "event and error counts and the sum of tokens on chat spans.",
    ),
}


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    if isinstance(v, int):
        return f"{v:,}".replace(",", " ")
    if v == int(v) and abs(v) >= 1:
        return f"{int(v):,}".replace(",", " ")
    if abs(v) >= 1000:
        return f"{v:,.1f}".replace(",", " ")
    return f"{v:.4g}" if abs(v) >= 0.01 else f"{v:.3g}"


def rel(e: dict):
    o, h = e["our_value"], e["honeycomb_value"]
    if not isinstance(o, (int, float)) or not isinstance(h, (int, float)) or not o:
        return None
    return (h - o) / o


def verdict_bar(counts: Counter, total: int) -> str:
    """Horizontal 100% bar of the four verdict buckets, with a legend."""
    w, h, gap = 1000, 44, 2
    x = 0.0
    segs, legend = [], []
    for i, (key, members, label, desc) in enumerate(BUCKETS, start=1):
        n = sum(counts.get(m, 0) for m in members)
        if not n:
            continue
        sw = w * n / total
        tip = f"{label}: {n} of {total} ({100 * n / total:.1f}%)"
        segs.append(
            f'<rect class="s{i} hit" x="{x + (gap if x else 0):.1f}" y="0" '
            f'width="{max(sw - (gap if x else 0), 1):.1f}" height="{h}" rx="4" '
            f'data-tip="{esc(tip)}"><title>{esc(tip)}</title></rect>'
        )
        x += sw
        legend.append(
            f'<li><span class="key s{i}"></span><span><b>{n}</b> {esc(label)}'
            f"<small>{esc(desc)}</small></span></li>"
        )
    return (
        f'<figure class="bar"><svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
        f'role="img" aria-label="Share of {total} numbers by verdict">{"".join(segs)}</svg>'
        f'<ul class="legend">{"".join(legend)}</ul></figure>'
    )


def strip_chart(entries: list[dict]) -> str:
    """Dot strip: Honeycomb minus ours, relative, for every method-difference check."""
    groups = (
        "Medians per model and arm",
        "IQR per model and arm",
        "With / without ratios of medians",
        "Time split",
    )
    labels = {
        "Medians per model and arm": "Medians (58)",
        "IQR per model and arm": "IQRs (53)",
        "With / without ratios of medians": "Ratios (35)",
        "Time split": "Time split (10)",
    }
    pts = {
        g: [(rel(e), e) for e in entries if e["group"] == g and e["verdict"] == "method"]
        for g in groups
    }
    for g in groups:
        labels[g] = labels[g].split(" (")[0] + f" ({len(pts[g])})"
    lo, hi = -0.06, 0.16
    left, right, top, row = 120, 980, 30, 46
    height = top + row * len(groups) + 34

    def sx(v: float) -> float:
        return left + (v - lo) / (hi - lo) * (right - left)

    out = [
        f'<svg viewBox="0 0 1000 {height}" role="img" aria-label="Relative difference '
        'between Honeycomb and our pipeline for every check with a method difference">'
    ]
    for t in (-0.05, 0, 0.05, 0.10, 0.15):
        cls = "axis0" if t == 0 else "grid"
        out.append(
            f'<line class="{cls}" x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top - 8}" '
            f'y2="{height - 26}"/>'
        )
        out.append(
            f'<text class="tick" x="{sx(t):.1f}" y="{height - 8}" '
            f'text-anchor="middle">{t:+.0%}</text>'.replace("+0%", "0")
        )
    for i, g in enumerate(groups):
        cy = top + row * i + row / 2 - 6
        out.append(
            f'<text class="lab" x="{left - 14}" y="{cy + 4:.1f}" '
            f'text-anchor="end">{esc(labels[g])}</text>'
        )
        for j, (v, e) in enumerate(sorted(pts[g], key=lambda p: p[0])):
            jitter = ((j * 7) % 5 - 2) * 3.2
            tip = (
                f"{e['question']} · ours {fmt(e['our_value'])}, Honeycomb "
                f"{fmt(e['honeycomb_value'])} ({v:+.1%})"
            )
            out.append(
                f'<circle class="dot hit" cx="{sx(v):.1f}" cy="{cy + jitter:.1f}" r="4.5" '
                f'data-tip="{esc(tip)}"><title>{esc(tip)}</title></circle>'
            )
    out.append(f'<text class="tick" x="{sx(0) + 6:.1f}" y="{top - 14}">Honeycomb higher →</text>')
    out.append(
        f'<text class="tick" x="{sx(0) - 6:.1f}" y="{top - 14}" text-anchor="end">← lower</text>'
    )
    out.append("</svg>")
    return "".join(out)


def table(entries: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(e["group"], []).append(e)
    big = {"Medians per model and arm", "IQR per model and arm", "With / without ratios of medians"}
    parts = []
    for g, rows in groups.items():
        c = Counter(e["verdict"] for e in rows)
        summary = " · ".join(f"{n} {CHIP[v][1]}" for v, n in c.most_common())
        body = []
        for e in rows:
            cls, word = CHIP[e["verdict"]]
            link = (
                f'<a href="{esc(e["query_url"])}" target="_blank" rel="noopener">'
                f"{esc(e['query_id'][:3])}</a>"
                if e["query_url"]
                else "—"
            )
            r = rel(e)
            delta = f'<small class="d">{r:+.1%}</small>' if r and e["verdict"] == "method" else ""
            body.append(
                f"<tr><td>{esc(e['question'])}<details class='why'><summary>query and "
                f"source</summary><p>{esc(e['query_explained'])}</p>"
                f"{'<p>' + esc(e['note']) + '</p>' if e['note'] else ''}"
                f"<p class='src'>ours: <code>{esc(e['our_source'])}</code></p></details></td>"
                f"<td class='n'>{fmt(e['our_value'])}</td>"
                f"<td class='n'>{fmt(e['honeycomb_value'])}{delta}</td>"
                f"<td><span class='chip {cls}'>{esc(word)}</span></td><td>{link}</td></tr>"
            )
        parts.append(
            f"<details class='grp'{'' if g in big else ' open'}><summary><span>{esc(g)}</span>"
            f"<span class='meta'>{len(rows)} numbers · {esc(summary)}</span></summary>"
            "<div class='scroll'><table><thead><tr><th>Number</th><th class='n'>Ours</th>"
            "<th class='n'>Honeycomb</th><th>Verdict</th><th>Query</th></tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table></div></details>"
        )
    return "".join(parts)


def query_list(data: dict) -> str:
    used = Counter(e["query_id"] for e in data["checks"] if e["query_id"])
    items = []
    for qid, q in data["queries"].items():
        title, text = QUERIES[qid]
        items.append(
            f"<li><div class='qh'><code>{esc(qid[:3])}</code><b>{esc(title)}</b>"
            f"<a href='{esc(q['query_url'])}' target='_blank' rel='noopener'>open in "
            f"Honeycomb</a></div><p>{esc(text)}</p><p class='qm'>{used.get(qid, 0)} checks · "
            f"{fmt(q['rows_examined'])} events examined · run {esc(q['run_pk'])}</p></li>"
        )
    return "<ol class='queries'>" + "".join(items) + "</ol>"


CSS = """
/* Layout: one reading column (68ch) for prose; figures and the table use the full 72rem. */
:root {
  --bg: #f6f7f8; --surface: #ffffff; --ink: #14181d; --ink-2: #4a525c; --ink-3: #6b7480;
  --rule: #dde1e6; --accent: #0f6f74; --accent-soft: #e2f0f0; --bad: #b42318;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100;
  --display: "Fraunces", Georgia, "Times New Roman", serif;
  --body: "Public Sans", -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #111417; --surface: #181c20; --ink: #eef1f4; --ink-2: #b8c0c9; --ink-3: #8f99a4;
  --rule: #2b3138; --accent: #5cc4c6; --accent-soft: #163234; --bad: #f97066;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #111417; --surface: #181c20; --ink: #eef1f4; --ink-2: #b8c0c9; --ink-3: #8f99a4;
  --rule: #2b3138; --accent: #5cc4c6; --accent-soft: #163234; --bad: #f97066;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; color-scheme: dark; }
body { background: var(--bg); color: var(--ink); font: 16px/1.6 var(--body); }
.wrap { max-width: 72rem; margin: 0 auto; padding-inline: 16px; padding-block: 40px 64px;
  display: grid; gap: 40px; }
.wrap > * { min-width: 0; }
.prose { max-width: 68ch; }
h1, h2, h3 { font-family: var(--display); font-weight: 600; text-wrap: balance; margin: 0;
  line-height: 1.15; }
h1 { font-size: clamp(2rem, 5vw, 3.1rem); letter-spacing: -0.01em; margin-bottom: 16px; }
h2 { font-size: 1.6rem; margin-bottom: 12px; }
h3 { font-size: 1.15rem; margin-bottom: 6px; }
p { margin: 0 0 12px; }
.eyebrow { font: 600 0.75rem/1 var(--mono); letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--accent); margin-bottom: 14px; }
.lead { font-size: 1.15rem; color: var(--ink); }
.muted, small { color: var(--ink-3); }
a { color: var(--accent); text-underline-offset: 2px; }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
code { font: 0.85em var(--mono); overflow-wrap: anywhere; }
section { display: grid; gap: 14px; }
section > *, .verdict > * { min-width: 0; }
.panel { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px;
  padding: 20px; }
.bar svg { width: 100%; height: 44px; display: block; }
.bar { margin: 0; display: grid; gap: 16px; }
.legend { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); }
.legend li { display: flex; gap: 10px; align-items: flex-start; }
.legend b { font: 600 1.25rem/1 var(--mono); margin-right: 4px; }
.legend small { display: block; font-size: 0.85rem; line-height: 1.4; margin-top: 4px; }
.key { width: 12px; height: 12px; border-radius: 3px; flex: none; margin-top: 5px; }
.s1 { fill: var(--s1); background: var(--s1); } .s2 { fill: var(--s2); background: var(--s2); }
.s3 { fill: var(--s3); background: var(--s3); } .s4 { fill: var(--s4); background: var(--s4); }
.verdict { display: grid; gap: 12px; }
.two { display: grid; gap: 24px; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); }
.two > * { min-width: 0; }
ul.plain { margin: 0; padding-left: 1.1rem; display: grid; gap: 8px; }
.strip svg { width: 100%; height: auto; display: block; }
.strip text { font: 13px var(--body); }
.strip .lab { fill: var(--ink); } .strip .tick { fill: var(--ink-3); font-size: 12px; }
.strip .grid { stroke: var(--rule); stroke-width: 1; }
.strip .axis0 { stroke: var(--ink-3); stroke-width: 1; }
.strip .dot { fill: var(--s2); stroke: var(--surface); stroke-width: 2; }
.hit { cursor: default; }
.hit:hover { opacity: 0.8; }
#tip { position: fixed; pointer-events: none; z-index: 9; max-width: 22rem; padding: 8px 10px;
  border-radius: 6px; background: var(--ink); color: var(--bg); font-size: 0.82rem;
  line-height: 1.35; }
.queries { margin: 0; padding: 0; list-style: none; display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(19rem, 1fr)); }
.queries li { background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
  padding: 14px 16px; display: grid; gap: 6px; align-content: start; }
.queries p { margin: 0; font-size: 0.92rem; }
.qh { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; }
.qh code { color: var(--accent); font-weight: 600; }
.qh a { margin-left: auto; font-size: 0.85rem; }
.qm { color: var(--ink-3); font: 0.78rem var(--mono); }
.grp { border-top: 1px solid var(--rule); }
.grp > summary { cursor: pointer; padding: 12px 0; display: flex; flex-wrap: wrap;
  gap: 4px 14px; align-items: baseline; }
.grp > summary span:first-child { font: 600 1.05rem var(--display); }
.meta { color: var(--ink-3); font-size: 0.85rem; }
.scroll { overflow-x: auto; margin-bottom: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; min-width: 34rem; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--rule);
  vertical-align: top; }
th { font: 600 0.72rem var(--mono); letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--ink-3); }
.n { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums;
  white-space: nowrap; }
.d { display: block; font-size: 0.75rem; }
.chip { display: inline-flex; align-items: center; gap: 6px; font: 0.78rem var(--mono);
  white-space: nowrap; color: var(--ink-2); }
.chip::before { content: ""; width: 9px; height: 9px; border-radius: 50%; }
.chip.b1::before { background: var(--s1); } .chip.b2::before { background: var(--s2); }
.chip.b3::before { background: var(--s3); } .chip.b4::before { background: var(--s4); }
.chip.bad { color: var(--bad); } .chip.bad::before { background: var(--bad); }
.why summary { cursor: pointer; color: var(--ink-3); font-size: 0.78rem; margin-top: 2px; }
.why p { font-size: 0.82rem; color: var(--ink-2); margin: 6px 0 0; max-width: 60ch; }
.src code { word-break: break-all; }
.callout { background: var(--accent-soft); border-radius: 8px; padding: 14px 16px; }
.callout p:last-child { margin: 0; }
footer { color: var(--ink-3); font-size: 0.85rem; border-top: 1px solid var(--rule);
  padding-top: 16px; }
@media (prefers-reduced-motion: no-preference) { .hit { transition: opacity .12s; } }
"""

JS = """
(function () {
  var tip = document.createElement('div'); tip.id = 'tip'; tip.hidden = true;
  document.body.appendChild(tip);
  document.addEventListener('mousemove', function (ev) {
    var t = ev.target.closest && ev.target.closest('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.getAttribute('data-tip'); tip.hidden = false;
    var x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = Math.max(8, x) + 'px'; tip.style.top = (ev.clientY + 16) + 'px';
  });
  document.querySelectorAll('[data-tip] title').forEach(function (n) { n.remove(); });
})();
"""


def render(data: dict) -> str:
    checks = data["checks"]
    total = len(checks)
    c = Counter(e["verdict"] for e in checks)
    n_exact, n_method = c.get("exact", 0), c.get("method", 0)
    n_expl = c.get("approximate", 0) + c.get("differs", 0)
    n_none = c.get("not_expressible", 0) + c.get("not_in_dataset", 0)
    n_bad = c.get("disagree", 0)

    def med_abs(group):
        v = [
            abs(r)
            for e in checks
            if e["group"] == group and e["verdict"] == "method"
            if (r := rel(e)) is not None
        ]
        return (statistics.median(v), max(v)) if v else (0, 0)

    m_med, m_max = med_abs("Medians per model and arm")
    r_med, r_max = med_abs("With / without ratios of medians")
    i_med, i_max = med_abs("IQR per model and arm")
    pct = 100 * (n_exact + n_method) / total
    agree = (
        "No number disagrees beyond a method difference."
        if not n_bad
        else f"{n_bad} numbers disagree without an explanation; see the table."
    )

    head = f"""<title>Honeycomb as the study's analyst</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=JetBrains+Mono:wght@400;600&family=Public+Sans:wght@400;600&display=swap">
<style>{CSS}</style>"""

    hero = f"""<header class="prose">
<p class="eyebrow">MCP-for-Kibana efficiency study · 180 runs · Honeycomb cross-check</p>
<h1>Honeycomb as the study's analyst</h1>
<p class="lead">Every number the efficiency study reports was computed a second time with
Honeycomb queries over the run traces and compared with our own pipeline. Honeycomb
reproduces <b>{n_exact + n_method} of {total}</b> numbers ({pct:.0f}%): {n_exact} exactly,
{n_method} within a known method difference. {n_expl} differ for an explained reason, and
{n_none} it cannot produce at all: the {c.get("not_expressible", 0)} bootstrap confidence
intervals and one value that was never sent. {esc(agree)}</p>
</header>"""

    verdict = f"""<section class="verdict">
<div class="panel">{verdict_bar(c, total)}</div>
<div class="two">
<div class="prose"><h3>What it means for agent studies</h3>
<p>Honeycomb is strong enough to be the primary place where an agent study is
<em>explored</em> and where every count, rate and per-cell median is read. Each count of
runs, passes, failures, error classes, retries, secret exposures, bypass calls and rule
outcomes matched exactly, from either the root-span totals or the tool spans themselves.</p>
<p>It is not enough to be the place where a study's <em>claims</em> are certified. Its
percentiles pick an observed value instead of interpolating, so medians and ratios move by a
little (median shift {m_med:.1%}, largest {m_max:.1%}; ratios {r_med:.1%}, largest
{r_max:.1%}) and IQRs on small integer counts by more (largest {i_max:.0%}). It has no
resampling, so the confidence intervals that decide whether a with/without effect is real
have to come from somewhere else.</p></div>
<div class="callout prose"><h3>Recommended split</h3>
<p>Use Honeycomb for discovery, slicing, BubbleUp and the per-run timeline. Keep the
certified statistics (interpolated medians, ratios with bootstrap CIs) in the analysis
pipeline, and quote them with their query links so a reader can open the same slice in
Honeycomb.</p>
<p>The {n_expl} numbers that differ are explained by a definition, not by bad data: the
agent-timeline view counts tokens on chat spans only, and an estimator overcounts distinct
span ids.</p></div>
</div>
</section>"""

    strip = f"""<section>
<div class="prose"><h2>How far the method difference moves the numbers</h2>
<p>Each dot is one number with a method difference: (Honeycomb − ours) ÷ ours. Honeycomb's
P50 of 30 runs is the 16th smallest run; ours averages the 15th and 16th. So Honeycomb's
medians never fall below ours, and the spread is widest where the values are small integers
(tool calls, turns), where one step is several percent.</p></div>
<figure class="panel strip" style="margin:0">{strip_chart(checks)}</figure>
</section>"""

    queries = f"""<section>
<div class="prose"><h2>The queries, in plain words</h2>
<p>Eighteen queries produce all {total} Honeycomb values. All run over environment
<code>test</code>, dataset <code>mcp-for-kibana-study</code>, 2026-10-10 18:00 to 2026-10-11
02:00 UTC. The links open only for members of the Honeycomb team <code>pedro-angel</code>.</p>
</div>{query_list(data)}</section>"""

    tbl = f"""<section>
<div class="prose"><h2>Every number, both values</h2>
<p>Grouped as the study reports them. Open a row's <em>query and source</em> for how
Honeycomb computed it and where our value lives in <code>analysis.json</code>. The small
percentage under a Honeycomb value is its relative distance from ours.</p></div>
<div>{table(checks)}</div></section>"""

    compare = """<section>
<h2>Honeycomb next to our pipeline</h2>
<div class="two">
<div class="prose"><h3>What Honeycomb does that the pipeline does not</h3>
<ul class="plain">
<li><b>BubbleUp finds the cause without a hypothesis.</b> Selecting the 111 harness-flagged
with-mcp tool errors against the other 666 tool calls ranks <code>create_dashboard</code>
first (92.8% of the selection, 13.5% of the baseline) and error class
<code>validation</code> next (95.5% vs 1.8%;
<a href="{BUBBLEUP}" target="_blank" rel="noopener">BubbleUp result</a>). Our pipeline only reports that finding because
someone thought to group by tool.</li>
<li><b>One run as a timeline.</b> The agent-conversation view lays out every model call and
tool call of a run with durations and error classes, which is how a hidden HTTP 4xx inside
a successful shell call becomes visible.</li>
<li><b>Interactive slicing.</b> Any breakdown is one click: the bypass targets (console
proxy, <code>/api/esql/query</code>, internal search) came from a breakdown on
<code>agent.tool.target</code>, and they are the first machine-readable source for the 117
query-endpoint calls, which our study only states in prose.</li>
<li><b>Run-by-run audit.</b> Grouping spans by run id reproduces, for all 180 runs, the tool
calls, model calls and input tokens our parser derived from the transcripts.</li>
</ul></div>
<div class="prose"><h3>Gaps, and the workaround for each</h3>
<ul class="plain">
<li><b>No confidence intervals.</b> A query aggregates once; there is no resampling.
Workaround: export root spans as raw rows and bootstrap outside, or publish the CIs as board
text.</li>
<li><b>Percentile method.</b> P50 returns an observed value (index floor(p·n)), never an
interpolation. Workaround: accept it for exploration; quote certified medians from the
pipeline.</li>
<li><b>Ratios work, but only per row.</b> Per-calculation filters on <code>study.arm</code>
and a formula give with ÷ without in one query. The TOTAL row then adds the per-model
ratios up, which is meaningless; run a second query without grouping for the pooled
ratio.</li>
<li><b>No aggregate of aggregates.</b> A median over per-run sums of tool spans needs two
steps. It works only because the sender also writes every run total on the root span.</li>
<li><b>COUNT_DISTINCT is an estimate.</b> Exact for 90 or 180 trace ids, 4% high for 1 561
span ids. Count one span per entity instead.</li>
<li><b>Charts are time series.</b> Every query also draws per-group lines over the run
window, which mean nothing for a randomized study and carry no legend when the breakdown is
large. The table view is the useful one.</li>
<li><b>Querying needs a different key.</b> The repository's key can send events but not
run queries, so this cross-check went through the Honeycomb MCP server and stored each
result; a configuration key with query access would make it a script.</li>
</ul></div>
</div></section>""".replace("{BUBBLEUP}", BUBBLEUP)

    ai = next(e for e in checks if e["id"] == "ai_conversation.total_tokens")
    recs = """<section class="prose">
<h2>Recommendations for the instrumentation</h2>
<ol class="plain">
<li><b>Keep every run total on the root span</b> (already done) and add the ones still
missing: <code>agent.usage.total_tokens</code> (saves a calculated field) and the runner's
<code>study.wall_s</code>, the one number Honeycomb could not see.</li>
<li><b>Write a boolean per run for every "runs with X" count</b>, for example
<code>agent.run.used_shell_http</code>, <code>agent.run.env_enumerated</code>. A filtered
COUNT on root spans is exact; COUNT_DISTINCT of trace ids over child spans is an estimate
once cardinality grows.</li>
<li><b>Record output tokens per model call on the chat spans.</b> Honeycomb's
agent-conversation view sums tokens on chat spans only, so today it shows input tokens alone
({ai_hc} of {ai_ours} for the sampled run). Spans from the harness's API client carry the final
per-call usage.</li>
<li><b>Send the certified statistics with the traces.</b> A marker or board note per study
with the bootstrap CIs and the <code>study.analysis_commit</code> lets a reader see the
uncertainty next to Honeycomb's medians.</li>
<li><b>Give the analysis a query key.</b> With query permission, <code>efficiency_crosscheck.py</code>
can run the eighteen queries itself and fail the build when a number drifts.</li>
</ol></section>""".replace("{ai_hc}", fmt(ai["honeycomb_value"])).replace(
        "{ai_ours}", fmt(ai["our_value"])
    )

    meta = data["meta"]
    foot = f"""<footer>
<p>Source: <code>scripts/experiment/runs/efficiency/full/honeycomb_crosscheck.json</code>
({meta["n_checks"]} checks), built by <code>scripts/experiment/efficiency_crosscheck.py</code>
from the captured query results in <code>runs/efficiency/full/honeycomb/</code> and from
<code>analysis.json</code>. Honeycomb's percentile rule: {esc(meta["percentile_rule"])}.
Tolerance for "exact": {esc(meta["tolerance"])}.</p></footer>"""

    return (
        head
        + '\n<main class="wrap">'
        + hero
        + verdict
        + strip
        + queries
        + tbl
        + compare
        + recs
        + foot
        + "</main>\n<script>"
        + JS
        + "</script>\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    data = json.loads(SRC.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
