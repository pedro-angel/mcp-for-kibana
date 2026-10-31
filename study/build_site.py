#!/usr/bin/env python3
"""Build index.html, the public page of the MCP savings study.

Every number on the page is read from the files in study/data/ (pulled from
Honeycomb and analysed by study/harness/mcp_savings_analyze.py and
mcp_savings_explore.py). Nothing numeric is typed by hand here. Standard
library only:

    python3 study/build_site.py
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE.parent / "index.html"

WITH, WITHOUT = "with-mcp", "no-mcp"
SETUP = {WITH: "MCP", WITHOUT: "Direct API"}
REPO = "https://github.com/pedro-angel/mcp-for-kibana"


def load_json(name: str):
    return json.loads((DATA / name).read_text())


def load_lines(name: str) -> list[dict]:
    return [json.loads(x) for x in (DATA / name).read_text().splitlines() if x.strip()]


A = load_json("analysis.json")
X = load_json("exploratory.json")
ROWS = load_lines("rows.jsonl")
CFG = json.loads((HERE / "harness" / "mcp_savings.json").read_text())
MODELS = CFG["study"]["models"]

e = html.escape


def model_name(m: str) -> str:
    """claude-haiku-5-5 -> Haiku 5.5"""
    parts = m.split("-")
    return parts[1].capitalize() + " " + ".".join(parts[2:])


def r2(v: float) -> str:
    return f"{v:.2f}"


def ci(lo: float, hi: float, fmt) -> str:
    return f"{fmt(lo)}–{fmt(hi)}"


def pct_saved(ratio: float) -> int:
    return round((1 - ratio) * 100)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s[:26].rstrip("Z") + "+00:00")


# ---------------------------------------------------------------- derived facts

counted_rows = [r for r in ROWS if r.get("study.valid") and not r.get("study.aborted")]
assert len(counted_rows) == A["counted"], (len(counted_rows), A["counted"])
workers = sorted({r["study.worker"] for r in counted_rows})
blocks = sorted({r["study.block"] for r in counted_rows})
checks_total = sorted({r["study.checks_total"] for r in counted_rows})
first_t, last_t = ts(A["first_row"]), ts(A["last_row"])

ratios = A["ratios"]
cost_saved = {m: pct_saved(ratios[m]["cost_usd"]["ratio"]) for m in MODELS}
time_saved = {m: pct_saved(ratios[m]["time_s"]["ratio"]) for m in MODELS}
all_below_1 = all(ratios[m][k]["ratio_ci95"][1] < 1 for m in MODELS for k in ("cost_usd", "time_s"))
passed = sum(v["passed"] for v in A["success"].values())
runs_total = sum(v["runs"] for v in A["success"].values())

cold = X["cold_start"]
cache_life_min = cold["cache_life_s"] // 60
cold_runs = []  # (cell, run_id, reason)
for cell, c in cold["cells"].items():
    if c["cold"]:
        cold_runs.append((cell, c["first"]["run_id"], "first"))
    for rid in c["others_at_floor"]:
        cold_runs.append((cell, rid, "later"))
warm_n = A["counted"] - len(cold_runs)
warm_pct = round(100 * warm_n / A["counted"])
later_cold = [x for x in cold_runs if x[2] == "later"]

# gap before each later cold run: its start minus the latest end of an earlier
# run of the same model and setup (end = start + wall_s), from the rows
by_cell = defaultdict(list)
for r in counted_rows:
    by_cell[f'{r["study.model_requested"]}/{r["study.arm"]}'].append(r)
later_cold_gaps = []
for cell, rid, _ in later_cold:
    runs = sorted(by_cell[cell], key=lambda r: r["time"])
    i = next(k for k, r in enumerate(runs) if r["study.run_id"] == rid)
    last_end = max(ts(r["time"]).timestamp() + r["study.wall_s"] for r in runs[:i])
    later_cold_gaps.append((cell, round(ts(runs[i]["time"]).timestamp() - last_end)))

# MCP tool definitions in the cached prompt: the least cache read per request
# of a warm run, MCP minus Direct API, per model
tool_def_tokens = sorted(
    {
        cold["cells"][f"{m}/{WITH}"]["others_median_min_request_cache_read"]
        - cold["cells"][f"{m}/{WITHOUT}"]["others_median_min_request_cache_read"]
        for m in MODELS
    }
)
cold_ratios = cold["ratios"]
cold_costlier = [m for m in MODELS if cold_ratios[m]["cold_cost_usd"] > 1]
cold_cheaper = [m for m in MODELS if cold_ratios[m]["cold_cost_usd"] <= 1]
cold_faster_all = all(cold_ratios[m]["cold_time_s"] < 1 for m in MODELS)

tb = X["time_breakdown"]

# ---------------------------------------------------------------- charts


def fmt_ms(v: float) -> str:
    return f"{v:,.0f}"


def names(ms: list[str]) -> str:
    n = [model_name(m) for m in ms]
    return n[0] if len(n) == 1 else ", ".join(n[:-1]) + " and " + n[-1]


DOMAIN = 1.5  # shared ratio scale for every ratio chart: 0 to 1.5


def ratio_panel(rows: list[dict], aria: str) -> str:
    """Dot-and-whisker panel on the shared 0-1.5 ratio scale, reference at 1.

    rows: [{"label": str, "marks": [{"v", "lo"?, "hi"?, "kind": "interval"|"point", "tip"}]}]
    """
    W, left, right = 360, 86, 344
    top, row_h, mark_h = 30, 0, 26
    heights = [18 + mark_h * len(r["marks"]) for r in rows]
    H = top + sum(heights) + 30

    def x(v: float) -> float:
        return left + (min(v, DOMAIN) / DOMAIN) * (right - left)

    out = [
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{e(aria)}" class="chart">',
    ]
    plot_bottom = H - 26
    for t in (0, 0.5, 1, 1.5):
        cls = "ref" if t == 1 else "grid"
        out.append(f'<line class="{cls}" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top - 8}" y2="{plot_bottom}"/>')
        out.append(f'<text class="tick" x="{x(t):.1f}" y="{plot_bottom + 16}" text-anchor="middle">{t:g}</text>')
    out.append(f'<text class="note" x="{x(1) - 6:.1f}" y="{top - 14}" text-anchor="end">← MCP lower</text>')
    out.append(f'<text class="note" x="{x(1) + 6:.1f}" y="{top - 14}">MCP higher →</text>')
    y = top
    for r, h in zip(rows, heights):
        cy0 = y + 18 + mark_h / 2
        out.append(
            f'<text class="lab" x="0" y="{cy0 + 4 + (mark_h * (len(r["marks"]) - 1)) / 2:.1f}">{e(r["label"])}</text>'
        )
        for k, mk in enumerate(r["marks"]):
            cy = cy0 + k * mark_h
            cx = x(mk["v"])
            out.append(f"<g><title>{e(mk['tip'])}</title>")
            if mk["kind"] == "interval":
                cls = "good" if mk["hi"] < 1 else "acc"
                out.append(
                    f'<line class="whisk {cls}" x1="{x(mk["lo"]):.1f}" x2="{x(mk["hi"]):.1f}" y1="{cy:.1f}" y2="{cy:.1f}"/>'
                )
                for end in (mk["lo"], mk["hi"]):
                    out.append(
                        f'<line class="whisk {cls}" x1="{x(end):.1f}" x2="{x(end):.1f}" y1="{cy - 5:.1f}" y2="{cy + 5:.1f}"/>'
                    )
                out.append(f'<circle class="dot {cls}" cx="{cx:.1f}" cy="{cy:.1f}" r="5"/>')
                label = f'{r2(mk["v"])} ({ci(mk["lo"], mk["hi"], r2)})'
            else:
                d = 6
                out.append(
                    f'<path class="hollow" d="M{cx:.1f} {cy - d:.1f} L{cx + d:.1f} {cy:.1f} L{cx:.1f} {cy + d:.1f} L{cx - d:.1f} {cy:.1f} Z"/>'
                )
                label = r2(mk["v"])
            # value label above the mark, kept inside the plot
            lx = min(max(cx, left + 40), right - 40)
            out.append(f'<text class="val" x="{lx:.1f}" y="{cy - 9:.1f}" text-anchor="middle">{e(label)}</text>')
            out.append("<rect class='hit' x='0' y='%.1f' width='%d' height='%d'/>" % (cy - mark_h / 2, W, mark_h))
            out.append("</g>")
        y += h
    out.append("</svg>")
    return "\n".join(out)


def savings_panel(key: str, what: str) -> str:
    rows = []
    for m in MODELS:
        r = ratios[m][key]
        lo, hi = r["ratio_ci95"]
        tip = (
            f"{model_name(m)}, {what}: MCP ÷ Direct API = {r2(r['ratio'])}, "
            f"95% interval {ci(lo, hi, r2)}"
        )
        rows.append({"label": model_name(m), "marks": [{"kind": "interval", "v": r["ratio"], "lo": lo, "hi": hi, "tip": tip}]})
    return ratio_panel(rows, f"{what} ratio MCP ÷ Direct API per model")


def cache_panel(key_warm: str, key_cold: str, what: str) -> str:
    rows = []
    for m in MODELS:
        w = ratios[m][key_warm]
        lo, hi = w["ratio_ci95"]
        c = cold_ratios[m][key_cold]
        rows.append(
            {
                "label": model_name(m),
                "marks": [
                    {
                        "kind": "interval",
                        "v": w["ratio"],
                        "lo": lo,
                        "hi": hi,
                        "tip": f"{model_name(m)}, {what}, warm: median ÷ median = {r2(w['ratio'])} ({ci(lo, hi, r2)})",
                    },
                    {
                        "kind": "point",
                        "v": c,
                        "tip": f"{model_name(m)}, {what}, cold: first MCP run ÷ first Direct API run = {r2(c)} (one run each)",
                    },
                ],
            }
        )
    return ratio_panel(rows, f"{what} ratio, warm and cold, per model")


def time_chart(W: int) -> str:
    """Stacked bars: median model requests + tool time outside them + idle, per model and setup."""
    lw, bar_h, gap, head = 82, 16, 7, 20
    right = W - 58
    cells = [(m, a) for m in MODELS for a in (WITH, WITHOUT)]
    vmax = max(tb["by_model_arm"][f"{m}/{a}"]["wall_s"]["median"] for m, a in cells)
    dmax = 30 * (int(vmax // 30) + 1)
    H = 8 + len(MODELS) * (head + 2 * bar_h + gap + 14) + 22

    def x(v: float) -> float:
        return lw + v / dmax * (right - lw)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Where a median run’s time goes, per model and setup" class="chart">']
    y0 = 8
    axis_bottom = H - 22
    for t in range(0, dmax + 1, 30):
        out.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{y0}" y2="{axis_bottom}"/>')
        out.append(f'<text class="tick" x="{x(t):.1f}" y="{axis_bottom + 15}" text-anchor="middle">{t} s</text>')
    y = y0
    segs = (("model_clock_s", "seg-model", "model requests"), ("tool_outside_model_s", "seg-tool", "tools outside requests"), ("idle_s", "seg-idle", "idle"))
    for m in MODELS:
        out.append(f'<text class="lab" x="0" y="{y + 14}">{e(model_name(m))}</text>')
        y += head
        for a in (WITH, WITHOUT):
            c = tb["by_model_arm"][f"{m}/{a}"]
            out.append(f'<text class="lab2" x="{lw - 8}" y="{y + bar_h - 4}" text-anchor="end">{SETUP[a]}</text>')
            xx = 0.0
            for key, cls, label in segs:
                v = c[key]["median"]
                w = max(x(xx + v) - x(xx) - 1.5, 0.5)
                lo, hi = c[key]["median_ci95"]
                out.append(
                    f'<rect class="{cls}" x="{x(xx):.1f}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="2">'
                    f"<title>{e(model_name(m))}, {SETUP[a]}: {label} {v:.1f} s (95% interval {lo:.1f}–{hi:.1f})</title></rect>"
                )
                xx += v
            wall = c["wall_s"]["median"]
            out.append(f'<text class="val" x="{x(xx) + 6:.1f}" y="{y + bar_h - 4}">{wall:.1f} s</text>')
            y += bar_h + gap
        y += 14 - gap + gap
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- tables


def cell_table() -> str:
    pm = A["per_model_arm"]
    head = (
        "<thead><tr><th>Setup</th><th class='num'>Cost per run</th>"
        "<th class='num'>Time per run</th><th class='num'>Tool calls</th></tr></thead>"
    )
    body = []
    for m in MODELS:
        body.append(f"<tr class='gh'><th colspan='4' scope='rowgroup'>{e(model_name(m))}</th></tr>")
        for a in (WITH, WITHOUT):
            c = pm[f"{m}/{a}"]
            cost, tm, tc = c["cost_usd"], c["time_s"], c["tool_calls"]
            body.append(
                f"<tr><td>{SETUP[a]}</td>"
                f"<td class='num'>${cost['median']:.4f}<span class='ci'>{ci(*cost['median_ci95'], lambda v: f'{v:.4f}')}</span></td>"
                f"<td class='num'>{tm['median']:.1f} s<span class='ci'>{ci(*tm['median_ci95'], lambda v: f'{v:.1f}')}</span></td>"
                f"<td class='num'>{tc['median']:.0f}<span class='ci'>{ci(*tc['median_ci95'], lambda v: f'{v:g}')}</span></td></tr>"
            )
    return f"<div class='tw'><table>{head}<tbody>{''.join(body)}</tbody></table></div>"


def call_table() -> str:
    sc = tb["single_call_ms"]
    rows = []
    for m in MODELS:
        a = sc[f"{m}/{WITH}/mcp"]
        b = sc[f"{m}/{WITHOUT}/bash"]
        rows.append(
            f"<tr><th scope='row'>{e(model_name(m))}</th>"
            f"<td class='num'>{fmt_ms(a['median'])} ms<span class='ci'>{ci(*a['median_ci95'], fmt_ms)} · n = {a['n']:,}</span></td>"
            f"<td class='num'>{fmt_ms(b['median'])} ms<span class='ci'>{ci(*b['median_ci95'], fmt_ms)} · n = {b['n']:,}</span></td></tr>"
        )
    return (
        "<div class='tw'><table><thead><tr><th>Model</th><th class='num'>MCP: one tool call</th>"
        "<th class='num'>Direct API: one shell command</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def cold_table() -> str:
    rows = []
    for m in MODELS:
        rows.append(f"<tr class='gh'><th colspan='5' scope='rowgroup'>{e(model_name(m))}</th></tr>")
        for a in (WITH, WITHOUT):
            f = cold["cells"][f"{m}/{a}"]["first"]
            med = cold["cells"][f"{m}/{a}"]["median"]
            rows.append(
                f"<tr><td>{SETUP[a]}</td>"
                f"<td class='num'>${f['cost_usd']:.4f}</td><td class='num'>${med['cost_usd']:.4f}</td>"
                f"<td class='num'>{f['cache_write_tokens']:,}</td><td class='num'>{med['cache_write_tokens']:,.0f}</td></tr>"
            )
    return (
        "<div class='tw'><table class='small'><thead><tr><th>Setup</th><th class='num'>Cost, first run</th>"
        "<th class='num'>Cost, median</th><th class='num'>Cache write, first run (tokens)</th><th class='num'>Cache write, median (tokens)</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


# ---------------------------------------------------------------- page

lo_cost = min(MODELS, key=lambda m: cost_saved[m])
hi_cost = max(MODELS, key=lambda m: cost_saved[m])
lo_time = min(MODELS, key=lambda m: time_saved[m])
hi_time = max(MODELS, key=lambda m: time_saved[m])
spend = A["spend_usd"]["all_runs"]
cli = A["claude_cli"]["values"][0].split(" ")[0]
kib = A["kibana_versions"]["values"][0]
srv = A["server_versions"][WITH]["values"][0]
harness = A["harness_sha"]
boot = A["bootstrap"]
n_cell = A["per_model_arm"][f"{MODELS[0]}/{WITH}"]["cost_usd"]["n"]
steps = CFG["task"]["steps"]
spans = tb["spans"]
mt = {m: (tb["by_model_arm"][f"{m}/{WITH}"]["model_clock_s"]["median"], tb["by_model_arm"][f"{m}/{WITHOUT}"]["model_clock_s"]["median"]) for m in MODELS}
tool_out = [tb["by_model_arm"][f"{m}/{a}"]["tool_outside_model_s"]["median"] for m in MODELS for a in (WITH, WITHOUT)]
overlap_with = [tb["by_model_arm"][f"{m}/{WITH}"]["overlap_s_median"] for m in MODELS]
call_x = [tb["single_call_ms"][f"{m}/{WITH}/mcp"]["median"] / tb["single_call_ms"][f"{m}/{WITHOUT}/bash"]["median"] for m in MODELS]
cli_ratio = X["time_ratio_cli"]
gap_txt = "; ".join(
    f"{model_name(c.split('/')[0])} on {SETUP[c.split('/')[1]]}, started {g} s after the previous run of its model and setup had ended"
    for c, g in later_cold_gaps
)
date_txt = f"{first_t:%-d} to {last_t:%-d %B %Y}, {first_t:%H:%M} to {last_t:%H:%M} UTC"
aborted_n = len(A["aborted"])

STEP_LABELS = {
    "space": "create a Kibana space and work inside it",
    "data_views": "create data views on the web-logs and flights indices, each with its time field",
    "weblogs": "build a two-panel web-logs dashboard and a server-errors alert rule",
    "flights": "build a two-panel flights dashboard and a cancelled-flights alert rule",
    "change": "change the web-logs dashboard in place: retitle a panel, add a third",
    "lookup": "read back the dashboard's panel count and the rule's settings",
}
step_items = "".join(f"<li>{e(STEP_LABELS.get(s['id'], s['id']))}</li>" for s in steps)

CSS = """
:root{color-scheme:light dark;--bg:#fbfbf9;--card:#ffffff;--fg:#1c1d1f;--muted:#55595f;--faint:#868b92;--rule:#e2e2de;--grid:#ebebe7;
--accent:#2d5bc8;--good:#16794a;--seg-tool:#9097a0;--seg-idle:#cfd3d8;--code:#f1f1ee}
@media (prefers-color-scheme:dark){:root{--bg:#131416;--card:#1a1b1e;--fg:#e7e7e4;--muted:#a9adb3;--faint:#7c818a;--rule:#2d2f33;--grid:#24262a;
--accent:#86a8ff;--good:#4cc48a;--seg-tool:#7c838c;--seg-idle:#3e4248;--code:#1f2124}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:40px 16px 64px}
h1{font-size:2rem;line-height:1.2;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 28px}
h2{font-size:1.3rem;margin:48px 0 10px;padding-top:20px;border-top:1px solid var(--rule)}
h3{font-size:1.02rem;margin:28px 0 6px}
p,li{max-width:68ch}
.lead{font-size:1.14rem;line-height:1.5}
.tag{display:inline-block;font-size:.72rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);border:1px solid var(--rule);border-radius:4px;padding:1px 6px;margin-left:8px;vertical-align:3px}
.defs{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:12px 16px;margin:20px 0}
.defs p{margin:6px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px 28px;margin:12px 0 4px}
.panel h4{margin:0 0 2px;font-size:.92rem;color:var(--muted);font-weight:600}
figure{margin:20px 0 8px}
figcaption{font-size:.84rem;color:var(--muted);margin-top:8px;max-width:72ch}
figcaption code{font-size:.8rem}
.chart{width:100%;height:auto;display:block;overflow:visible}
.chart text{font-family:inherit}
.chart .tick{fill:var(--faint);font-size:11px}
.chart .note{fill:var(--faint);font-size:11px}
.chart .lab{fill:var(--fg);font-size:13px;font-weight:600}
.chart .lab2{fill:var(--muted);font-size:12px}
.chart .val{fill:var(--fg);font-size:12px;font-variant-numeric:tabular-nums}
.chart .val,.chart .note,.chart .lab{paint-order:stroke;stroke:var(--bg);stroke-width:4px;stroke-linejoin:round}
.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .ref{stroke:var(--fg);stroke-width:1.25;stroke-dasharray:4 3;opacity:.7}
.chart .whisk{stroke-width:2;stroke-linecap:round}
.chart .whisk.good{stroke:var(--good)}.chart .dot.good{fill:var(--good);stroke:var(--card);stroke-width:2}
.chart .whisk.acc{stroke:var(--accent)}.chart .dot.acc{fill:var(--accent);stroke:var(--card);stroke-width:2}
.chart .hollow{fill:var(--card);stroke:var(--accent);stroke-width:2}
.chart .hit{fill:transparent}
.chart .seg-model{fill:var(--accent)}.chart .seg-tool{fill:var(--seg-tool)}.chart .seg-idle{fill:var(--seg-idle)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.84rem;color:var(--muted);margin:10px 0 0;padding:0;list-style:none}
.legend li{display:flex;align-items:center;gap:6px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px}
.sw.model{background:var(--accent)}.sw.tool{background:var(--seg-tool)}.sw.idle{background:var(--seg-idle)}
.sw.good{background:var(--good);border-radius:50%;width:10px;height:10px}
.sw.cold{border:2px solid var(--accent);transform:rotate(45deg);width:9px;height:9px;border-radius:1px;margin:0 2px}
.tw{overflow-x:auto;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:.92rem;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--rule);vertical-align:top}
thead th{font-size:.8rem;color:var(--muted);font-weight:600;border-bottom:1px solid var(--faint)}
tbody th{font-weight:600}
tr.gh th{padding-top:14px;border-bottom:none;font-size:.95rem}
td{white-space:nowrap}.ci{white-space:nowrap}
.num{text-align:right}
.ci{display:block;color:var(--faint);font-size:.78rem}
table.small{font-size:.86rem}
.only-narrow{display:none}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
code{font-size:.88em;background:var(--code);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}
pre{background:var(--code);border:1px solid var(--rule);border-radius:8px;padding:12px 14px;font-size:.8rem;line-height:1.5;overflow-x:auto;white-space:pre}
pre code{background:none;padding:0}
ul.facts{padding-left:20px}
ul.facts li{margin:4px 0}
a{color:var(--accent)}
footer{margin-top:56px;padding-top:16px;border-top:1px solid var(--rule);font-size:.84rem;color:var(--muted)}
@media (max-width:640px){
 main{padding-top:28px}
 h1{font-size:1.6rem}
 .lead{font-size:1.05rem}
 .grid2{grid-template-columns:1fr;gap:18px}
 .only-wide{display:none}.only-narrow{display:block}
 th,td{padding:6px 5px}
 table{font-size:.86rem}
 table.small{font-size:.8rem}
}
"""


def caption(text: str) -> str:
    return f"<figcaption>{text}</figcaption>"


def data_link(path: str) -> str:
    return f'<a href="study/data/{path}"><code>study/data/{path}</code></a>'


cost_rng = f"{cost_saved[lo_cost]}% ({model_name(lo_cost)}) to {cost_saved[hi_cost]}% ({model_name(hi_cost)})"
time_rng = f"{time_saved[lo_time]}% ({model_name(lo_time)}) to {time_saved[hi_time]}% ({model_name(hi_time)})"

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCP savings study</title>
<meta name="description" content="Claude Code doing one Kibana job through the mcp-for-kibana MCP server versus Kibana's REST API: cost and time over {A['counted']} runs.">
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<h1>MCP savings study</h1>
<p class="sub">Claude Code on one Kibana job, {A['counted']} runs, three models: through the mcp-for-kibana MCP server, or through Kibana's REST API.</p>
</header>

<p class="lead">With MCP, Claude Code did the same Kibana job cheaper and faster than with Direct API, with each of the three models. Median cost per run fell {cost_rng}; median time per run fell {time_rng}.{' All six 95% intervals lie wholly below 1.' if all_below_1 else ''}</p>
<p>These are warm-cache numbers: {warm_n} of {A['counted']} runs ({warm_pct}%) started with the prompt already cached. On a cold cache, with one run each, MCP cost more for {names(cold_costlier)}{', and still saved for ' + names(cold_cheaper) if cold_cheaper else ''}.</p>

<div class="defs">
<p><strong>The two setups.</strong> <strong>MCP</strong>: Claude Code reaches Kibana through the tools of the <a href="{REPO}">mcp-for-kibana</a> server ({srv}). <strong>Direct API</strong>: Claude Code calls Kibana's REST API itself from the shell, with the pinned Kibana API reference. Everything else is the same.</p>
<p>Results describe Claude Code with each model, not the model alone. A ratio is the median on MCP ÷ the median on Direct API: below 1, MCP is cheaper or faster.</p>
</div>

<h2>Savings per model</h2>
<p>{model_name(hi_cost)} saved the most on both measures. With {n_cell} runs per model and setup, the intervals are narrow.</p>
<figure>
<div class="grid2">
<div class="panel"><h4>Cost per run, MCP ÷ Direct API</h4>{savings_panel('cost_usd', 'cost')}</div>
<div class="panel"><h4>Time per run, MCP ÷ Direct API</h4>{savings_panel('time_s', 'time')}</div>
</div>
<ul class="legend"><li><span class="sw good"></span>median ratio, 95% interval wholly below 1</li></ul>
{caption(f"Medians with 95% percentile-bootstrap intervals ({boot['resamples']:,} resamples). Shared scale; dashed line at 1 = same as Direct API. Data: {data_link('analysis.json')} (<code>ratios</code>).")}
</figure>

<h2>Per model and setup</h2>
<p>On Direct API, {model_name(MODELS[0])} took the longest of all six combinations; on MCP it was the cheapest by far. All {passed} of {runs_total} counted runs passed every check.</p>
<figure>
{cell_table()}
{caption(f"Median per run, 95% interval below it. Cost is list-price cost (Anthropic's published prices, not a bill), as each run came; caches were not controlled. Time runs from the CLI's start to its result. A tool call is one MCP tool call on MCP, or one shell command on Direct API (a command can bundle several Kibana requests). Data: {data_link('analysis.json')} (<code>per_model_arm</code>).")}
</figure>

<h2>Where the time goes <span class="tag">exploratory</span></h2>
<p>Not planned before the runs: it suggests where to look, not what holds. On MCP, the time saved is the model's time. A median run spent {mt[MODELS[0]][0]:.1f} s in model requests instead of {mt[MODELS[0]][1]:.1f} s with {model_name(MODELS[0])}, {mt[MODELS[1]][0]:.1f} s instead of {mt[MODELS[1]][1]:.1f} s with {model_name(MODELS[1])}, and {mt[MODELS[2]][0]:.1f} s instead of {mt[MODELS[2]][1]:.1f} s with {model_name(MODELS[2])}. Tool time outside those requests stayed between {min(tool_out):.1f} and {max(tool_out):.1f} s.</p>
<figure>
<ul class="legend"><li><span class="sw model"></span>model requests</li><li><span class="sw tool"></span>tools, outside requests</li><li><span class="sw idle"></span>idle</li></ul>
<div class="only-wide">{time_chart(680)}</div>
<div class="only-narrow">{time_chart(360)}</div>
{caption(f"Median seconds per run on the clock, without double counting (model requests + tool time outside them + idle = wall time); each part is its own median, so the parts need not add up exactly to the median wall time printed at the end. From {spans['attached']:,} of {spans['pulled']:,} CLI spans (<code>claude_code.llm_request</code>, <code>claude_code.tool.execution</code>) attached to the counted runs. Data: {data_link('exploratory.json')} (<code>time_breakdown</code>), from {data_link('spans/spans.jsonl')}.")}
</figure>
<p>One MCP tool call takes {min(call_x):.0f} to {max(call_x):.0f} times as long as one shell command, yet tool time does not grow: the CLI starts a tool while the model's response is still streaming, so on MCP a median {min(overlap_with):.1f} to {max(overlap_with):.1f} s of tool time per run runs under the model's requests.</p>
<figure>
{call_table()}
{caption(f"Median duration of one call, 95% interval and number of calls below it. The interval resamples calls as if independent, so it is narrower than a run-level one. Data: {data_link('exploratory.json')} (<code>time_breakdown.single_call_ms</code>).")}
</figure>
<p>The time saving is not harness overhead: by the CLI's own duration the ratios are {r2(cli_ratio[MODELS[0]]['ratio'])} ({model_name(MODELS[0])}), {r2(cli_ratio[MODELS[1]]['ratio'])} ({model_name(MODELS[1])}) and {r2(cli_ratio[MODELS[2]]['ratio'])} ({model_name(MODELS[2])}), each interval {'wholly below 1' if all(cli_ratio[m]['ratio_ci95'][1] < 1 for m in MODELS) else 'shown in the data file'} (<code>time_ratio_cli</code> in {data_link('exploratory.json')}).</p>

<h2>Cache: cold against warm <span class="tag">exploratory</span></h2>
<p>On MCP, the cached prompt carries the server's tool definitions: a warm run's smallest cache read per request is {tool_def_tokens[0]:,} tokens larger than on Direct API. On a warm cache they cost little; a cold run first has to write them to the cache. {warm_n} of {A['counted']} runs started warm. The {len(cold_runs)} cold ones are the first run of each model and setup, and one more ({gap_txt}).</p>
<figure>
<div class="grid2">
<div class="panel"><h4>Cost, MCP ÷ Direct API</h4>{cache_panel('cost_usd', 'cold_cost_usd', 'cost')}</div>
<div class="panel"><h4>Time, MCP ÷ Direct API</h4>{cache_panel('time_s', 'cold_time_s', 'time')}</div>
</div>
<ul class="legend"><li><span class="sw good"></span>warm: median ÷ median, {n_cell} runs each, 95% interval</li><li><span class="sw cold"></span>cold: first run ÷ first run, one run each</li></ul>
{caption(f"Same scale as above; dashed line at 1. A first run counts as cold when its least-read model request read no more than the lowest per-request cache read of any run of its model and setup. Data: {data_link('exploratory.json')} (<code>cold_start</code>), from {data_link('cache/min-request-cache-read.json')} and {data_link('cache/pilot-root-spans.json')}; warm ratios from {data_link('analysis.json')}.")}
</figure>
<p>Cold against cold, MCP cost {names(cold_costlier)} more{', and still saved for ' + names(cold_cheaper) if cold_cheaper else ''}{'; all three cold runs finished sooner on MCP' if cold_faster_all else ''}. One run per model and setup is an observation, not a result.</p>
<figure>
{cold_table()}
{caption(f"The first run of each model and setup against the median of its {n_cell} runs. Data: {data_link('exploratory.json')} (<code>cold_start.cells</code>).")}
</figure>

<h2>How it ran</h2>
<ul class="facts">
<li><strong>Design:</strong> {len(blocks)} blocks × {len(MODELS)} models × 2 setups = {A['planned']} runs. A block is one run of each model in each setup, in a seeded order, so each block pairs the two setups.</li>
<li><strong>When:</strong> {date_txt}, on {len(workers)} workers running whole blocks one run at a time.</li>
<li><strong>The job:</strong> one long Kibana job on the web-logs and flights sample data, {len(steps)} steps in order, then {checks_total[0]} checks against Kibana:<ol>{step_items}</ol></li>
<li><strong>Held fixed:</strong> harness frozen at commit <code>{harness}</code>, mcp-for-kibana {srv} (write tier), Kibana {kib}, Claude Code CLI {cli}, effort {CFG['study']['cli_env']['CLAUDE_CODE_EFFORT_LEVEL']}. Each run worked in its own Kibana space.</li>
<li><strong>Measurement:</strong> each run sent one root span, <code>efficiency.run</code>, to Honeycomb, with its time, cost, tokens, turns, tool calls, checks and versions. Every number on this page comes from those spans and the CLI's own spans.</li>
<li><strong>Analysis, fixed before any run:</strong> medians per run with 95% percentile-bootstrap intervals ({boot['resamples']:,} resamples, seed {boot['seed']}). A saving counts only if the pass-rate difference (MCP minus Direct API, bootstrapped over blocks) does not lie wholly below 0.</li>
<li><strong>Integrity:</strong> {A['counted']} of {A['planned']} planned runs counted, each exactly once, from {A['root_spans']} root spans ({aborted_n} aborted run, rerun). Recomputed from each run's tokens, the price table reproduced the CLI's cost of all {A['price_check']['checked']} priced runs. One trace per run. Every run recorded the same harness, CLI and Kibana version.</li>
<li><strong>Spend:</strong> ${spend:.2f} at list prices for every run.</li>
</ul>

<h2>Limits</h2>
<ul class="facts">
<li><strong>Warm cache.</strong> {warm_pct}% of runs started on a warm prompt cache, so the ratios describe repeated use. A first, cold run can come out the other way, as it did for {names(cold_costlier)}.</li>
<li><strong>Success does not separate the setups.</strong> All {passed} runs passed all {checks_total[0]} checks, so the study cannot say whether either setup does the job better; it compares cost and time at equal success.</li>
<li><strong>One job, one stack.</strong> One task, one Kibana version ({kib}), one Claude Code CLI version ({cli}), one server version ({srv}), one effort level. Other jobs, versions or agents may differ.</li>
<li><strong>List prices.</strong> Cost is computed at Anthropic's published list prices, not what any account was billed.</li>
<li><strong>Exploratory sections</strong> (where the time goes, cache) were not planned before the runs.</li>
</ul>

<h2>Reproduce</h2>
<p>The rows, spans, analysis output and the frozen harness are in <a href="study/"><code>study/</code></a> on this branch. To recompute every number from the rows, with <a href="https://docs.astral.sh/uv/">uv</a>:</p>
<pre><code>git clone -b gh-pages {REPO}.git mcp-savings-study
cd mcp-savings-study/study/harness
uv run python mcp_savings_analyze.py analyze --phase study \\
  --rows ../data/rows.jsonl --harness-sha {harness} --out /tmp/mcp-savings
uv run python mcp_savings_explore.py --phase study \\
  --rows ../data/rows.jsonl --harness-sha {harness} --out /tmp/mcp-savings \\
  --spans ../data/spans/spans.jsonl \\
  --min-reads ../data/cache/min-request-cache-read.json \\
  --pilot-rows ../data/cache/pilot-root-spans.json
cmp /tmp/mcp-savings/analysis.json ../data/analysis.json
cmp /tmp/mcp-savings/exploratory.json ../data/exploratory.json
cd ../.. &amp;&amp; python3 study/build_site.py   # rebuilds this page</code></pre>
<p>How the rows were pulled from Honeycomb: <a href="study/docs/PULL_ROWS.md"><code>study/docs/PULL_ROWS.md</code></a>. What each file is: <a href="study/README.md"><code>study/README.md</code></a>.</p>

<footer>
<p>Every number and chart on this page is generated by <a href="study/build_site.py"><code>study/build_site.py</code></a> from <code>study/data/</code>. Licensed under the Apache License 2.0, as is <a href="{REPO}">mcp-for-kibana</a>.</p>
</footer>
</main>
</body>
</html>
"""

OUT.write_text(page)
print(f"wrote {OUT} ({len(page.encode()):,} bytes)")
