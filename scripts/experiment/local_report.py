#!/usr/bin/env python3
"""Build the earlier local-model study's results page (chain-length levels,
``runs/local/``) from committed run records. Study 5 has its own config,
``local_study.json``; this page reads the config those runs ran with.

Every number on the page is read from ``runs/local/<phase>/analysis.json``,
``environment.json`` and ``honeycomb_crosscheck.json`` next to it (when
present) and ``local_study_levels.json``; nothing is typed in by hand. The page
stamps the commit and SHA-256 of the ``analysis.json`` it read.

The page is a claude.ai artifact body (no ``<html>``/``<head>``/``<body>``):
``<out>/index.html``.

Deterministic for a given tree: no clock, no randomness. Stdlib only.

    uv run python scripts/experiment/local_report.py --out /tmp/local-report
    uv run python scripts/experiment/local_report.py --phase pilot --out /tmp/pilot-report
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path

from efficiency_report import provenance

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs" / "local"
CONFIG = HERE / "local_study_levels.json"  # the config runs/local ran with
SERIES = ("s1", "s2", "s3", "s4")  # categorical slots, in model order


def e(s: object) -> str:
    return html.escape(str(s), quote=True)


def pct(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def num(v: float | None, nd: int = 0) -> str:
    if v is None:
        return "–"
    return f"{v:,.{nd}f}"


def med(cell: dict, metric: str) -> float | None:
    return (cell.get("metrics", {}).get(metric) or {}).get("median")


def short(model: str) -> str:
    return model.split("/", 1)[-1]


# ---------------------------------------------------------------- charts


def chain_panel(i: int, model: str, chain: dict, cells: dict, threshold: float) -> str:
    """Pass rate by level for one model: Wilson whiskers, dots and a line."""
    w, h, left, right, top, bottom = 320, 210, 44, 12, 18, 34
    pw, ph = w - left - right, h - top - bottom
    levels = chain["levels"]
    n = len(levels)

    def x(j: int) -> float:
        return left + (j + 0.5) * pw / n

    def y(r: float) -> float:
        return top + (1 - r) * ph

    s = SERIES[i % len(SERIES)]
    cid = f"chain{i}"
    out = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-labelledby="{cid}-t" class="chart small {s}">',
        f'<title id="{cid}-t">{e(short(model))}: pass rate by chain length, 95% Wilson intervals</title>',
    ]
    for t in (0, 0.25, 0.5, 0.75, 1):
        cls = "thr" if t == threshold else "grid"
        out.append(
            f'<line x1="{left}" x2="{w - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="{cls}"/>'
        )
        out.append(
            f'<text x="{left - 6}" y="{y(t) + 4:.1f}" class="tick" text-anchor="end">{t * 100:.0f}%</text>'
        )
    pts = []
    for j, lv in enumerate(levels):
        c = cells[lv]
        out.append(
            f'<text x="{x(j):.1f}" y="{h - 12}" class="tick" text-anchor="middle">{e(lv)}</text>'
        )
        if not c.get("n_valid"):
            continue
        lo, hi = c["wilson95"]
        out.append(
            f'<line x1="{x(j):.1f}" x2="{x(j):.1f}" y1="{y(hi):.1f}" y2="{y(lo):.1f}" class="whisk"/>'
        )
        pts.append((x(j), y(c["rate"]), lv, c))
    if len(pts) > 1:
        d = " ".join(
            f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py, _, _) in enumerate(pts)
        )
        out.append(f'<path d="{d}" class="ln"/>')
    for px, py, lv, c in pts:
        lo, hi = c["wilson95"]
        out.append(
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" class="dot"><title>{e(short(model))} {e(lv)}: '
            f"{c['passes']}/{c['n_valid']} passed ({pct(c['rate'])}, 95% CI {pct(lo)}–{pct(hi)})</title></circle>"
        )
    out.append("</svg>")
    lt = chain.get("lose_track_at")
    verdict = f"loses track at {e(lt)}" if lt else "stays above the threshold at every level"
    return (
        f'<figure class="panel"><figcaption><i class="sw {s}"></i><b>{e(short(model))}</b>'
        f"<span>{verdict}</span></figcaption>{''.join(out)}</figure>"
    )


# ---------------------------------------------------------------- page


def build(phase: str, out_dir: Path) -> None:
    base = RUNS / phase
    apath = base / "analysis.json"
    A = json.loads(apath.read_text())
    cfg = json.loads(CONFIG.read_text())
    env_path = RUNS / "full" / "environment.json"
    env = json.loads(env_path.read_text()) if env_path.exists() else {}
    hc_path = base / "honeycomb_crosscheck.json"
    hc = json.loads(hc_path.read_text()) if hc_path.exists() else None
    prov = provenance(apath)

    models, levels = A["models"], A["levels"]
    cells, chain, runs = A["cells"], A["chain"], A["runs"]
    thr = A["lose_track_threshold"]
    valid = [r for r in runs if r["valid"]]
    invalid = [r for r in runs if not r["valid"]]
    per_cell = Counter((r["model"], r["level"]) for r in valid)
    n_cell = sorted(set(per_cell.values()))
    ctx = sorted({r["context_length"] for r in runs if r.get("context_length")})
    blocks = sorted({r["block"] for r in runs})
    kib = cfg["kibana"]["version"]
    steps = cfg["task"]["steps"]
    last = levels[-1]

    # Lede, from the numbers.
    words = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}
    clauses = []
    for m in models:
        c = cells[m][last]
        lt = chain[m]["lose_track_at"]
        clauses.append(
            f"<b>{e(short(m))}</b> {c['passes']} of {c['n_valid']}"
            + (f" (below {pct(thr)} from {e(lt)})" if lt else "")
        )
    lede = (
        f"{words.get(len(models), len(models))} local models ran mcp-for-kibana through LM Studio on "
        f"one Kibana task, asked for 1 to {len(steps)} chained steps in a single prompt. Runs that "
        f"completed all {len(steps)} steps: " + "; ".join(clauses) + "."
    )

    tile = '<div class="fact"><b>{}</b><span>{}</span></div>'
    tiles = "".join(
        [
            tile.format(f"{len(valid)}", f"valid runs of {len(runs)}"),
            tile.format(
                len(models),
                "models, each loaded at " + ", ".join(f"{c:,}" for c in ctx) + " tokens of context",
            ),
            tile.format(
                ", ".join(map(str, n_cell)), f"runs per model and level ({len(blocks)} blocks)"
            ),
            tile.format(e(kib), "Kibana version"),
            tile.format(
                e(env.get("lmstudio_app_version", "–")),
                f"LM Studio, on {e(env.get('cpu', '?'))} with {e(env.get('memory_gb', '?'))} GB",
            ),
        ]
    )

    # The task, from the config.
    task_rows = []
    for k, st in enumerate(steps):
        lv = levels[k]
        text = st["text"].format(
            marker="<marker>",
            data_view=cfg["kibana"]["data_view"],
            logs_index=cfg["kibana"]["logs_index"],
        )
        task_rows.append(
            f'<tr><th scope="row">{e(lv)}</th><td>{"Steps 1–" + str(k + 1) + ", adding: " if k else ""}{e(text)}</td>'
            f"<td><code>{e(', '.join(st['checks']))}</code></td></tr>"
        )

    panels = "".join(chain_panel(i, m, chain[m], cells[m], thr) for i, m in enumerate(models))

    # Results table.
    res_rows = []
    for i, m in enumerate(models):
        res_rows.append(
            f'<tr class="sub"><th colspan="9" scope="colgroup"><i class="sw {SERIES[i % 4]}"></i>{e(m)}</th></tr>'
        )
        for lv in levels:
            c = cells[m][lv]
            lo, hi = c["wilson95"] if c.get("n_valid") else (None, None)
            pk = c.get("pass_hat_k") or {}
            res_rows.append(
                f'<tr><th scope="row" class="ind">{e(lv)}</th>'
                f'<td class="n">{c["passes"]}/{c["n_valid"]}</td><td class="n">{pct(c["rate"])}</td>'
                f'<td class="n">{pct(lo)}–{pct(hi)}</td>'
                f'<td class="n">{pct(pk.get("2"))}</td><td class="n">{pct(pk.get("3"))}</td>'
                f'<td class="n">{num(med(c, "wall_s"))}</td><td class="n">{num(med(c, "output_tokens"))}</td>'
                f'<td class="n">{num(med(c, "tool_calls"))}</td></tr>'
            )

    # Per step costs.
    step_rows = []
    for i, m in enumerate(models):
        ch = chain[m]
        cellsm = cells[m]
        step_rows.append(
            f'<tr><th scope="row"><i class="sw {SERIES[i % 4]}"></i>{e(short(m))}</th>'
            + "".join(f'<td class="n">{num(v)}</td>' for v in ch["median_s_per_step"])
            + "".join(f'<td class="n">{num(v)}</td>' for v in ch["median_output_tokens_per_step"])
            + f'<td class="n">{num(med_pool(runs, m, "tokens_per_second"), 1)}</td>'
            + f'<td class="n">{num(med_pool(runs, m, "ttft_s"), 1)}</td></tr>'
        )
        del cellsm

    # Where chains break.
    step_ids = [st["id"] for st in steps]
    first_rows = []
    for i, m in enumerate(models):
        cnt = Counter()
        fails = 0
        for lv in levels:
            c = cells[m][lv]
            cnt.update(c.get("first_failed_step") or {})
            fails += c["n_valid"] - c["passes"]
        first_rows.append(
            f'<tr><th scope="row"><i class="sw {SERIES[i % 4]}"></i>{e(short(m))}</th>'
            + "".join(f'<td class="n">{cnt.get(s, 0)}</td>' for s in step_ids)
            + f'<td class="n">{fails}</td></tr>'
        )
    check_rows = []
    for lv in levels:
        p = A["pooled"][lv]
        top = sorted((p.get("failed_checks") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        failed_runs = p["n_valid"] - p["passes"]
        check_rows.append(
            f'<tr><th scope="row">{e(lv)}</th><td class="n">{failed_runs}/{p["n_valid"]}</td><td>'
            + ("; ".join(f"<code>{e(k)}</code> {v}" for k, v in top) or "–")
            + "</td></tr>"
        )

    # Tool-call behaviour and run endings.
    tool_rows = []
    for i, m in enumerate(models):
        rs = [r for r in valid if r["model"] == m]
        calls = sum(r["tool_calls"] or 0 for r in rs)
        bad = sum(r["failed_tool_calls"] or 0 for r in rs)
        inv = sum(r["invalid_tool_calls"] or 0 for r in rs)
        ends = Counter(r["end_reason"] for r in rs)
        tool_rows.append(
            f'<tr><th scope="row"><i class="sw {SERIES[i % 4]}"></i>{e(short(m))}</th>'
            f'<td class="n">{calls:,}</td><td class="n">{bad:,}</td><td class="n">{pct(bad / calls if calls else None)}</td>'
            f'<td class="n">{inv:,}</td><td class="n">{ends.get("timeout", 0)}</td>'
            f'<td class="n">{num(med_pool(runs, m, "tool_calls"))}</td></tr>'
        )

    secrets = sum(r.get("known_secret_exposures") or 0 for r in runs)
    inv_reasons = Counter((r.get("invalid_reason") or "unknown")[:120] for r in invalid)
    inv_text = (
        "No run was invalid."
        if not invalid
        else f"{len(invalid)} runs were invalid and excluded, then retried: "
        + "; ".join(f"{e(k)} ({v})" for k, v in inv_reasons.most_common())
        + "."
    )
    cons = A.get("consistency") or {}
    mism = cons.get("mismatches") or []
    cons_text = (
        f"Every run's {', '.join(cons.get('checked', []))} were re-derived from its stored response "
        f"and match its record ({len(mism)} differences)."
    )

    if hc:
        hc_section = (
            f"<p>Each run was sent as one trace to the Honeycomb dataset <code>{e(cfg['honeycomb']['dataset'])}</code>. "
            f"{e(hc.get('summary', ''))}</p>"
            f"<p>The cross-check compared {e(hc.get('n_compared', '–'))} values from Honeycomb queries with "
            f"<code>analysis.json</code>: {e(hc.get('n_mismatches', '–'))} differ.</p>"
        )
    else:
        sent = sum(1 for r in runs if str(r.get("otel", "")).startswith("sent"))
        hc_section = (
            f"<p>Each run's trace is stored with it (<code>spans.json</code>). The Honeycomb cross-check "
            f"has not been run for this page{f'; {sent} runs were sent' if sent else ''}.</p>"
        )

    stamp = (
        f"<code>{e(prov['path'])}</code> at commit <code>{e(prov['commit'])}</code>"
        f"{' (with uncommitted changes)' if prov['dirty'] else ''}, SHA-256 "
        f"<code>{e(prov['sha256'])}</code>"
    )
    legend = "".join(
        f'<span><i class="sw {SERIES[i % 4]}"></i>{e(short(m))}</span>'
        for i, m in enumerate(models)
    )
    phase_badge = (
        '<p><span class="draft">Pilot · one run per cell</span></p>' if phase == "pilot" else ""
    )
    step_heads = "".join(f'<th class="n" scope="col">{e(lv)}</th>' for lv in levels)

    page = f"""<title>mcp-for-kibana Local Model Study</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,600&family=Public+Sans:wght@400;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* Layout: one reading column, as the efficiency study's page; small multiples in a 2-up grid; tables scroll in their own boxes. */
:root{{
  --paper:#F6F7F5; --surface:#FCFCFB; --ink:#18201D; --muted:#56625C; --rule:#D8DED9; --band:#ECF0ED; --link:#2a78d6; --fail:#d03b3a;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --display:"Newsreader","Iowan Old Style",Georgia,serif;
  --body:"Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{
  --paper:#131816; --surface:#1A1F1D; --ink:#E7ECE9; --muted:#9DA9A3; --rule:#303A36; --band:#202826; --link:#3987e5; --fail:#e66767;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; color-scheme:dark }}}}
:root[data-theme="dark"]{{
  --paper:#131816; --surface:#1A1F1D; --ink:#E7ECE9; --muted:#9DA9A3; --rule:#303A36; --band:#202826; --link:#3987e5; --fail:#e66767;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; color-scheme:dark }}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);font:16px/1.6 var(--body)}}
main{{max-width:880px;margin:0 auto;padding-inline:18px;padding-block:36px 80px;display:grid;gap:6px}}
main>*{{min-width:0}}
h1,h2,h3{{font-family:var(--display);font-weight:600;line-height:1.15;text-wrap:balance;margin:0}}
h1{{font-size:clamp(1.9rem,5vw,2.8rem)}}
h2{{font-size:1.6rem;margin-top:40px;padding-top:16px;border-top:1px solid var(--rule)}}
h3{{font-size:1.2rem;margin-top:20px}}
p,li{{max-width:68ch}} p{{margin:6px 0}}
.eyebrow{{font:600 .76rem/1.4 var(--body);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}}
.draft{{display:inline-block;font:600 .74rem/1 var(--body);letter-spacing:.06em;text-transform:uppercase;color:var(--fail);border:1.5px solid var(--fail);border-radius:4px;padding:5px 8px}}
.lede{{font-size:1.14rem;line-height:1.55}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:10px;margin:14px 0 4px}}
.fact{{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:12px 14px}}
.fact b{{display:block;font:500 1.5rem/1.15 var(--mono);font-variant-numeric:tabular-nums}}
.fact span{{color:var(--muted);font-size:.86rem;line-height:1.4;display:block;margin-top:4px}}
.scroll{{overflow-x:auto;margin:12px 0;-webkit-overflow-scrolling:touch}}
table{{border-collapse:collapse;width:100%;font-size:.93rem;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 9px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}}
thead th{{font-size:.76rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:600}}
thead tr:last-child th{{border-bottom:2px solid var(--ink)}}
th[colspan]{{text-align:center}}
td.n,th.n{{text-align:right;white-space:nowrap}} td.n{{font-family:var(--mono);font-size:.86rem}}
tbody th{{font-weight:600;white-space:nowrap}}
th.ind{{font-weight:400;padding-left:22px}}
tr.sub th{{text-align:left;font-size:.8rem;letter-spacing:.02em;color:var(--ink);padding-top:14px}}
table.wide{{min-width:620px}} table.task td{{font-size:.88rem}}
.sw{{display:inline-block;width:.8em;height:.8em;border-radius:2px;vertical-align:-.05em;margin-right:.45em}}
.sw.s1{{background:var(--s1)}} .sw.s2{{background:var(--s2)}} .sw.s3{{background:var(--s3)}} .sw.s4{{background:var(--s4)}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:.88rem;color:var(--muted);margin:4px 0}}
.panels{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:14px;margin:12px 0}}
.panel{{margin:0;background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:10px 12px;min-width:0}}
.panel figcaption{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;font-size:.92rem}}
.panel figcaption span{{color:var(--muted);font-size:.84rem}}
.chart{{width:100%;height:auto;display:block}}
.chart text{{fill:var(--ink);font-family:var(--body);font-size:12px}}
.chart .tick{{font:11px var(--mono);fill:var(--muted)}}
.chart .grid{{stroke:var(--rule);stroke-width:1}}
.chart .thr{{stroke:var(--muted);stroke-width:1.2;stroke-dasharray:4 3}}
.chart .whisk{{stroke:var(--ink);stroke-width:2;stroke-linecap:round;opacity:.55}}
.chart .ln{{fill:none;stroke-width:2}}
.chart .dot{{stroke:var(--surface);stroke-width:2}}
.small.s1 .ln{{stroke:var(--s1)}} .small.s1 .dot{{fill:var(--s1)}}
.small.s2 .ln{{stroke:var(--s2)}} .small.s2 .dot{{fill:var(--s2)}}
.small.s3 .ln{{stroke:var(--s3)}} .small.s3 .dot{{fill:var(--s3)}}
.small.s4 .ln{{stroke:var(--s4)}} .small.s4 .dot{{fill:var(--s4)}}
code{{font:.86em var(--mono);background:var(--band);padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}}
ul{{padding-left:20px;display:grid;gap:6px}}
.note{{font-size:.88rem;color:var(--muted)}}
footer{{margin-top:36px;color:var(--muted);font-size:.86rem}}
a{{color:var(--link)}} a:focus-visible{{outline:2px solid var(--link);outline-offset:2px}}
</style>
<main>
{phase_badge}
<p class="eyebrow">mcp-for-kibana · local-model study · Kibana {e(kib)} · LM Studio {e(env.get("lmstudio_app_version", "?"))} · {len(runs)} runs</p>
<h1>Can local models drive mcp-for-kibana?</h1>
<p class="lede">{lede}</p>
<div class="facts">{tiles}</div>

<h2>What was run</h2>
<p>Each run sent one prompt to LM Studio's chat API with the server <code>{e(cfg["server"]["entry"])}</code> as its only integration: {e(cfg["server"]["expected_tools"])} tools from the toolboxes <code>{e(cfg["server"]["toolboxes"])}</code> at the <code>{e(cfg["server"]["tier"])}</code> tier, on Kibana's web-logs sample data. Level <i>k</i> asks for steps 1 to <i>k</i> in one prompt and is scored against every check of those steps. A run passes only when all of them pass. Kibana was reset after every run. The request carried no sampling parameters, so each model ran with its own defaults, loaded at its maximum context.</p>
<div class="scroll"><table class="task wide"><thead><tr><th scope="col">Level</th><th scope="col">The prompt asks for</th><th scope="col">Checks</th></tr></thead><tbody>{"".join(task_rows)}</tbody></table></div>
<p>Runs went in {len(blocks)} blocks, each running every model and level once, with the model order and the level order rotated from block to block.</p>

<h2>Where each model loses track</h2>
<p>The pass rate at each chain length, with 95% Wilson intervals. The dashed line is the {pct(thr)} threshold: a model <i>loses track</i> at the shortest level whose rate falls below it.</p>
<div class="legend">{legend}</div>
<div class="panels">{panels}</div>
<div class="scroll"><table class="wide"><thead><tr><th scope="col">Model, level</th><th class="n" scope="col">Passed</th><th class="n" scope="col">Rate</th><th class="n" scope="col">95% CI</th><th class="n" scope="col">pass^2</th><th class="n" scope="col">pass^3</th><th class="n" scope="col">Median s</th><th class="n" scope="col">Median out tokens</th><th class="n" scope="col">Median tool calls</th></tr></thead><tbody>{"".join(res_rows)}</tbody></table></div>
<p class="note">pass^k is the chance that k runs drawn from the cell all pass: a measure of consistency rather than of the best case.</p>

<h2>What breaks</h2>
<h3>The first step that failed</h3>
<p>For each failed run, the first step whose checks did not all pass, counted over all levels.</p>
<div class="scroll"><table class="wide"><thead><tr><th scope="col">Model</th>{"".join(f'<th class="n" scope="col">{e(s)}</th>' for s in step_ids)}<th class="n" scope="col">Failed runs</th></tr></thead><tbody>{"".join(first_rows)}</tbody></table></div>
<h3>The checks that failed most</h3>
<div class="scroll"><table class="wide"><thead><tr><th scope="col">Level</th><th class="n" scope="col">Failed runs</th><th scope="col">Failing checks (runs), all models</th></tr></thead><tbody>{"".join(check_rows)}</tbody></table></div>
<h3>Tool calls</h3>
<p>A tool call counts as failed when the server's answer is not JSON (the server reports errors as plain-text guidance). Invalid calls are the ones LM Studio's own schema check rejected; they never reached the server. A run that hits the {e(cfg["lmstudio"]["run_timeout_s"])} s limit is a valid, failed run.</p>
<div class="scroll"><table class="wide"><thead><tr><th scope="col">Model</th><th class="n" scope="col">Tool calls</th><th class="n" scope="col">Failed</th><th class="n" scope="col">Failed share</th><th class="n" scope="col">Invalid</th><th class="n" scope="col">Timed-out runs</th><th class="n" scope="col">Median per run</th></tr></thead><tbody>{"".join(tool_rows)}</tbody></table></div>

<h2>What a step costs</h2>
<p>Medians per level of the run's wall time and output tokens, divided by its chain length. LM Studio reports token usage per run only, so these are run totals spread over the steps.</p>
<div class="scroll"><table class="wide"><thead><tr><th rowspan="2" scope="col">Model</th><th colspan="{len(levels)}" scope="colgroup">Seconds per step</th><th colspan="{len(levels)}" scope="colgroup">Output tokens per step</th><th rowspan="2" class="n" scope="col">Tokens/s</th><th rowspan="2" class="n" scope="col">TTFT s</th></tr><tr>{step_heads}{step_heads}</tr></thead><tbody>{"".join(step_rows)}</tbody></table></div>

<h2>Evidence and checks</h2>
<ul>
<li><b>Validity.</b> {inv_text}</li>
<li><b>Re-derivation.</b> {e(cons_text)}</li>
<li><b>Secrets.</b> The Kibana API key appeared in model-visible text or tool output {secrets} times across all runs; files and traces carry a redaction marker in its place.</li>
</ul>
<h3>Honeycomb</h3>
{hc_section}

<h2>Method and caveats</h2>
<ul>
<li><b>Scope.</b> One task, one dataset, one Kibana version, four models on one machine, through one runtime. The results describe each model with LM Studio's tool loop and the server's tool descriptions, not the model alone.</li>
<li><b>Loading.</b> The harness loaded each model through LM Studio's REST API at its maximum context before its runs, one study model at a time. Load time is outside the run times.</li>
<li><b>Checks after the pilot.</b> The pilot showed two checks rejecting correct results: an answer line in bold Markdown (<code>**RULE_ENABLED:** yes</code>) and a 5xx regular expression written as <code>5[0-9][0-9]</code>. Both patterns were widened before the full run.</li>
<li><b>Per-step figures.</b> Seconds and tokens per step are run totals divided by the chain length. LM Studio does not report per-call usage.</li>
</ul>

<footer>
<p>Generated by <code>scripts/experiment/local_report.py</code> from {stamp}, with the configuration <code>scripts/experiment/local_study_levels.json</code> (analysis config hash <code>{e(A["config_sha"])}</code>) and the run records under <code>scripts/experiment/runs/local/{e(phase)}/</code>.</p>
</footer>
</main>
"""
    (out_dir / "index.html").write_text(page)


def med_pool(runs: list[dict], model: str, metric: str) -> float | None:
    xs = sorted(
        r[metric] for r in runs if r["valid"] and r["model"] == model and r.get(metric) is not None
    )
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--phase", default="full", choices=("full", "pilot"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    build(args.phase, args.out)
    print(f"wrote {args.out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
