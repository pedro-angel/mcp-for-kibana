"""Build report.html: what the mcp-for-kibana server saves an AI agent doing Kibana work.
Every number comes from saved Honeycomb query results in data/*.json (and the two Honeycomb heatmap PNGs)."""

import base64
import html
import json
import math
import re
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
D = HERE / "data"

UI = "https://ui.eu1.honeycomb.io/pedro-angel/environments/test"
DS = UI + "/datasets/mcp-for-kibana-study4/result/"
BOARD1 = UI + "/board/5q-7tCefHafG"
BOARD2 = UI + "/board/25-8QuCXHevA"
B1 = "mcp-for-kibana server: cost and time saved"
B2 = "mcp-for-kibana server: where the savings come from"
BU_COST = DS + "pW-124hpWVWU?tab=bubbleup&bubbleup_result=hu-SnVUb5w6E"
BU_TIME = DS + "ux-3jHLdSd4p?tab=bubbleup&bubbleup_result=pR-LVwRS9LyQ"
TR_WITH = UI + "/result/zK-SboX4hbVv/trace?trace_id=7fa57d25dbf2c6162d7aa063bf6d1263"
TR_WITHOUT = UI + "/result/JE-FA7MgXLB2/trace?trace_id=1354027cee52babf25aa9d333e3a5613"

MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
MNAME = {
    "claude-haiku-5-5": "Haiku 5.5",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-opus-5-5": "Opus 5.5",
}
MSHORT = {"claude-haiku-5-5": "Haiku", "claude-sonnet-5-5": "Sonnet", "claude-opus-5-5": "Opus"}
TASKS = ["weblogs", "flights", "change", "lookup"]
TNAME = {
    "weblogs": "Build on web logs",
    "flights": "Build on flights",
    "change": "Change existing",
    "lookup": "Look up",
}
ARMS = ("with-mcp", "no-mcp")

esc = html.escape


def load(name):
    return json.loads((D / f"{name}.json").read_text())


def rows(name):
    return [r["data"] for r in load(name)["results"]]


def img64(name):
    return "data:image/png;base64," + base64.b64encode((D / f"{name}.png").read_bytes()).decode()


def qlink(pk):
    return DS + pk


def money(v):
    return f"${v:.3f}" if v >= 0.01 else f"${v:.4f}"


# ------------------------------------------------------------------ Honeycomb results
def pct3(name, col):
    out = {}
    for r in rows(name):
        out[(r["study.model"], r["study.task"], r["study.arm"])] = tuple(
            r[f"P{p}({col})"] for p in (25, 50, 75)
        )
    return out


COST = pct3("v2_fig1_cost", "study.total_cost_usd")  # run rQ-XCLthePHQ
TIME = pct3("v2_fig2_time", "study.wall_s")  # run jr-K1QutEy5g


def geo(vals):
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


TASK_RATIO = {
    kind: {
        m: {t: data[(m, t, "with-mcp")][1] / data[(m, t, "no-mcp")][1] for t in TASKS}
        for m in MODELS
    }
    for kind, data in (("cost", COST), ("time", TIME))
}
MODEL_RATIO = {
    kind: {m: geo(list(TASK_RATIO[kind][m].values())) for m in MODELS} for kind in TASK_RATIO
}
PCT = {kind: {m: round(100 * (1 - MODEL_RATIO[kind][m])) for m in MODELS} for kind in MODEL_RATIO}


def separated(data):
    return [
        (m, t)
        for m in MODELS
        for t in TASKS
        if data[(m, t, "with-mcp")][2] < data[(m, t, "no-mcp")][0]
    ]


SEP_COST, SEP_TIME = separated(COST), separated(TIME)

CHECKS = {r["study.arm"]: r for r in rows("fig6_checks")}  # rN-8QrRgr7pR
RUNS = {(r["study.task"], r["study.arm"]): r["COUNT"] for r in rows("fig7_runs")}  # Jo-6V7ALFxvJ
N_ARM = {a: sum(v for (t, aa), v in RUNS.items() if aa == a) for a in ARMS}


def mech(name, col):
    return {(r["study.model"], r["study.arm"]): r[col] for r in rows(name)}


BYTES = mech("fig8_bytes", "AVG(agent.usage.tool_result_bytes)")  # 52-VMv7CZGJc
OUT = mech("fig9_output", "AVG(study.output_tokens)")  # 6r-cVxmt8Mwn
CACHE = mech("fig10_cache", "AVG(study.cache_creation_input_tokens)")  # wa-w7TiDZeXD
TURNS = mech("fig11_turns", "AVG(study.num_turns)")  # 84-hBWXwHUxZ
TS_M = mech("fig12_timesplit", "AVG(model_time_s)")  # 9u-toPPjoMyU
TS_O = mech("fig12_timesplit", "AVG(other_time_s)")
SERVER = rows("fig13_server")  # pH-JB1yihmCd
AVG_BYTES = {m: BYTES[(m, "no-mcp")] / BYTES[(m, "with-mcp")] for m in MODELS}

GAP = rows("v2_gap")[0]  # EJ-KfWL9NUNG
ROOTS = {r["study.arm"]: r for r in rows("v2_trace_roots")}  # jp-YjDUjFVgu
FAIL = {"true": (31, 26), "false": (41, 4)}  # DU-1WXWjPGEk (runs, with a failed call)
N_SLOW, FAIL_N_SLOW = FAIL["true"]
N_REST, FAIL_N_REST = FAIL["false"]

IMG_URL = {
    "fig3_heatall": "https://ui.eu1.honeycomb.io/img/GX1li2iCpfgW8eNTKWrhten8S1ePNUag2wyDOngeFBTC",
    "fig4_timeheat": "https://ui.eu1.honeycomb.io/img/hO57EtuT9va5WWp76MYqjBBUCIW0EwM2uBTt6noCZCK",
}


# ------------------------------------------------------------------ figure helpers
def figure(
    n,
    title,
    sub,
    body,
    note="",
    board=None,
    bname=None,
    pk=None,
    kind="redrawn",
    cls="",
    src_override=None,
):
    badge = (
        '<span class="src-k">▣ Honeycomb chart</span>'
        if kind == "real"
        else '<span class="src-k">↻ Redrawn from Honeycomb</span>'
    )
    if src_override:
        src = f'<p class="src">{badge} {src_override}</p>'
    else:
        src = (
            f'<p class="src">{badge} <a href="{qlink(pk)}">Open in Honeycomb</a> · board panel '
            f'<a href="{board}">“Fig {n} · {esc(title)}”</a> on {esc(bname)}</p>'
        )
    note_html = f'<p class="fig-note">{note}</p>' if note else ""
    return f"""<div class="figblock {cls}">
<div class="fc"><span class="fc-num">Figure {n}</span><h3 class="fc-title">{esc(title)}</h3><p class="fc-sub">{sub}</p></div>
<figure id="fig-{n}" class="fig-slot" aria-label="Figure {n}: {esc(title)}">{body}</figure>
{note_html}{src}
</div>"""


def bar(cls, pct, tip, label, iqr=None):
    whisk = ""
    if iqr:
        a, b = iqr
        whisk = f'<i class="pb-iqr" style="left:{a:.2f}%;width:{max(b - a, 0.3):.2f}%"></i>'
    return (
        f'<div class="pb-row" data-tip="{esc(tip)}"><span class="pb-track">'
        f'<i class="pb-bar {cls}" style="width:{max(pct, 0.4):.2f}%"></i>{whisk}</span>'
        f'<span class="pb-v">{label}</span></div>'
    )


def pair_group(label, w, o, vmax, fmt, tipname, ratio_txt, wq=None, oq=None):
    def one(cls, v, q, who):
        if q:
            tip = f"{tipname} · {who}: P50 {fmt(q[1])} (P25 {fmt(q[0])}, P75 {fmt(q[2])})"
            return bar(
                cls, 100 * q[1] / vmax, tip, fmt(q[1]), (100 * q[0] / vmax, 100 * q[2] / vmax)
            )
        return bar(cls, 100 * v / vmax, f"{tipname} · {who}: {fmt(v)}", fmt(v))

    return f"""<div class="pb-group">
  <div class="pb-lab">{esc(label)}</div>
  <div class="pb-bars">
    {one("w", w, wq, "with the server")}
    {one("o", o, oq, "without")}
  </div>
  <div class="pb-ratio">{ratio_txt}</div>
</div>"""


def nice_max(v):
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def nice_scale(v, n=4):
    best = None
    for kexp in range(-4, 6):
        for m in (1, 2, 2.5, 5):
            st = m * 10**kexp
            if math.ceil(v / st) <= n and (best is None or st < best):
                best = st
    cnt = math.ceil(v / best)
    return best * cnt, [best * i for i in range(cnt + 1)]


def tick_html(ticks, vmax, fmt):
    return "".join(f'<span style="left:{100 * t / vmax:.2f}%">{fmt(t)}</span>' for t in ticks)


LEGEND = '<div class="legend"><span><i class="k-with"></i>With the server</span><span><i class="k-without"></i>Without</span></div>'
LEGEND_IQR = (
    '<div class="legend"><span><i class="k-with"></i>With the server</span><span><i class="k-without"></i>Without</span>'
    '<span><i class="k-iqr"></i>P25 to P75</span></div>'
)


def facet_fig(data, fmt):
    out = []
    for m in MODELS:
        vmax = nice_max(max(data[(m, t, a)][2] for t in TASKS for a in ARMS))
        gs = []
        for t in TASKS:
            wq, oq = data[(m, t, "with-mcp")], data[(m, t, "no-mcp")]
            gs.append(
                pair_group(
                    TNAME[t],
                    None,
                    None,
                    vmax,
                    fmt,
                    f"{MNAME[m]} · {TNAME[t]}",
                    f"{wq[1] / oq[1]:.2f}×",
                    wq,
                    oq,
                )
            )
        out.append(
            f'<div class="facet"><div class="facet-h"><b>{MNAME[m]}</b><span>scale 0–{fmt(vmax)}</span></div>{"".join(gs)}</div>'
        )
    return f'<div class="fig-top">{LEGEND_IQR}<span class="rh">bar: P50 · right: with ÷ without</span></div><div class="facets">{"".join(out)}</div>'


def model_fig(data, fmt, tfmt=None):
    vmax, tks = nice_scale(max(data.values()))
    gs = "".join(
        pair_group(
            MNAME[m],
            data[(m, "with-mcp")],
            data[(m, "no-mcp")],
            vmax,
            fmt,
            MNAME[m],
            f"{data[(m, 'no-mcp')] / data[(m, 'with-mcp')]:.1f}×",
        )
        for m in MODELS
    )
    return (
        f'<div class="fig-top">{LEGEND}<span class="rh">right: without ÷ with</span></div>'
        f'<div class="single"><div class="rows">{gs}</div><div class="pb-axis"><div></div><div class="ticks">{tick_html(tks, vmax, tfmt or fmt)}</div><div></div></div></div>'
    )


def pair_list(pairs):
    return ", ".join(f"{MSHORT[m]} {TNAME[t].lower()}" for m, t in pairs)


# ------------------------------------------------------------------ figures 1-3: cost and time
c_lo = min(TASK_RATIO["cost"][m][t] for m in MODELS for t in TASKS)
c_hi = max(TASK_RATIO["cost"][m][t] for m in MODELS for t in TASKS)
fig1 = figure(
    1,
    "Cost per run by model, task and setup",
    "Median (P50) list-price cost of a run, with a line from P25 to P75: the middle half of the 12 runs. Each model has its own scale.",
    facet_fig(COST, money),
    f"All 12 model-and-task pairs are cheaper with the server, from {c_lo:.2f}× (Haiku, build on web logs) to {c_hi:.2f}× (Opus, look up). "
    + (
        "In all 12 pairs the with-server P75 sits below the without P25, so the middle halves of the runs do not overlap."
        if len(SEP_COST) == 12
        else f"In {len(SEP_COST)} of 12 pairs the middle halves of the runs do not overlap."
    ),
    BOARD1,
    B1,
    "rQ-XCLthePHQ",
    cls="bh-m",
)
ol = [(m, t) for m in MODELS for t in TASKS if (m, t) not in SEP_TIME]
ol_txt = ""
if ol:
    m, t = ol[0]
    w, o = TIME[(m, t, "with-mcp")], TIME[(m, t, "no-mcp")]
    ol_txt = (
        f" In {len(SEP_TIME)} of 12 pairs the middle halves of the runs do not overlap. The exception is {MSHORT[m]} looking up, where the server saves "
        f"under a second (P50 {w[1]:.1f} s against {o[1]:.1f} s) and the ranges touch (with-server P75 {w[2]:.1f} s, without P25 {o[0]:.1f} s)."
    )
fig2 = figure(
    2,
    "Time per run by model, task and setup",
    "Median (P50) wall-clock seconds of a run, with a line from P25 to P75. Each model has its own scale.",
    facet_fig(TIME, lambda v: f"{v:.1f} s"),
    "Building saves the most time." + ol_txt,
    BOARD1,
    B1,
    "jr-K1QutEy5g",
    cls="bh-m",
)
SPEND = {
    "with-mcp": rows("fig3_spend_with")[0]["SUM(study.total_cost_usd)"],  # run 4K-e8XBSJh5x
    "no-mcp": rows("fig3_spend_without")[0]["SUM(study.total_cost_usd)"],  # run jW-hcFJn2Hg2
}
_r = SPEND["with-mcp"] / SPEND["no-mcp"]
fig3 = figure(
    3,
    "Total spend by setup",
    "Sum of every run's list-price cost over the three-hour window, for each setup.",
    f"""<div class="spend">
  <div class="spend-n"><span class="spend-lab"><i class="k-with"></i>Total spend with the server</span><span class="spend-val">${SPEND["with-mcp"]:.2f}</span><span class="spend-sub">{N_ARM["with-mcp"]} runs</span></div>
  <div class="spend-n"><span class="spend-lab"><i class="k-without"></i>Total spend without the server</span><span class="spend-val">${SPEND["no-mcp"]:.2f}</span><span class="spend-sub">{N_ARM["no-mcp"]} runs</span></div>
  <p class="spend-ratio">With ÷ without: {_r:.2f}× · {100 * (1 - _r):.0f}% less with the server</p>
</div>""",
    "The same work cost about a third as much with the server.",
    src_override=(
        f'<a href="{qlink("4K-e8XBSJh5x")}">Open “with the server” in Honeycomb</a> · '
        f'<a href="{qlink("jW-hcFJn2Hg2")}">Open “without the server” in Honeycomb</a> · board panels '
        f'<a href="{BOARD1}">“Fig 3 · Total spend, with the server” and “Fig 3 · Total spend, without the server”</a> on {esc(B1)}'
    ),
)


# ------------------------------------------------------------------ figure 4: waterfall
def spans(name):
    d = load(name)
    out = []
    for r in d["results"]:
        x = r["data"]
        if not x.get("trace.span_id") or x.get("meta.annotation_type"):
            continue
        ts = r["time"].rstrip("Z")
        base, _, frac = ts.partition(".")
        t = datetime.fromisoformat(base + "." + (frac + "000000")[:6] + "+00:00")
        out.append(
            {
                "name": x["name"],
                "t": t.timestamp(),
                "dur": x["duration_ms"] / 1000,
                "err": x.get("error"),
            }
        )
    return out


SIDE = []
for r in load("trace_rows")["results"]:
    x = r["data"]
    if x["name"] == "claude_code.llm_request" and (x.get("input_tokens") or 0) > 100:
        ts = r["time"].rstrip("Z")
        base, _, frac = ts.partition(".")
        SIDE.append(
            datetime.fromisoformat(base + "." + (frac + "000000")[:6] + "+00:00").timestamp()
        )

STEP_WITHOUT = [
    "List API files, fetch data views",
    "Read data view fields, grep the API",
    "Read 475 lines of API reference",
    "Grep reference, read 200 lines",
    "Read two more reference slices",
    "POST the dashboard JSON",
    "Look up codes, rule types",
    "POST the rule JSON",
    "Rewrite the rule, PUT it back",
    "Rename panel, read both back",
]


def waterfall(name, arm, steps, total):
    sp = spans(name)
    t0 = min(s["t"] for s in sp if s["name"] == "efficiency.run")
    keep = [
        s
        for s in sp
        if s["name"]
        in (
            "efficiency.run",
            "claude_code.llm_request",
            "claude_code.tool",
            "efficiency.score",
            "efficiency.reset",
        )
        or s["name"].startswith("tools/call")
    ]
    keep.sort(key=lambda s: (s["t"], 0 if s["name"] == "claude_code.tool" else 1))
    tool_names = {
        s["t"]: s["name"].split("__")[-1] for s in sp if s["name"].startswith("agent.tool mcp__")
    }
    out, ti, li, nmodel = [], 0, 0, 0
    for s in keep:
        n, x0, w = s["name"], s["t"] - t0, s["dur"]
        err = False
        if n == "efficiency.run":
            lab, cls, ind = "Whole run, with scoring and reset", "root", 0
        elif n == "claude_code.llm_request":
            if any(abs(s["t"] - z) < 0.01 for z in SIDE):
                lab, cls, ind = "Side call (Claude Code's own check)", "llm side", 1
            else:
                li += 1
                nmodel += 1
                lab, cls, ind = f"Model call {li}", "llm", 1
        elif n == "claude_code.tool":
            if arm == "with-mcp":
                lab = "Tool: " + min(tool_names.items(), key=lambda kv: abs(kv[0] - s["t"]))[1]
            else:
                lab = "Shell: " + steps[ti]
                err = abs(w - 0.304) < 0.002
            ti += 1
            cls, ind = ("tool-w" if arm == "with-mcp" else "tool-o"), 1
        elif n.startswith("tools/call"):
            lab, cls, ind = "server: " + n.replace("tools/call ", ""), "srv", 2
        elif n == "efficiency.score":
            lab, cls, ind = "Check: read Kibana back", "aux", 1
        else:
            lab, cls, ind = "Reset Kibana", "aux", 1
        dur = f"{w * 1000:.0f} ms" if w < 1 else f"{w:.1f} s"
        mark = '<b class="err" title="failed">✕ failed</b> ' if err else ""
        out.append(
            f'<div class="wf-row ind{ind}" data-tip="{esc(lab)} · starts {x0:.1f} s · {dur}{" · failed" if err else ""}">'
            f'<span class="wf-lab">{mark}{esc(lab)}</span><span class="wf-track"><i class="wf-bar {cls}" style="left:{100 * x0 / total:.3f}%;width:{max(100 * w / total, 0.35):.3f}%"></i></span>'
            f'<span class="wf-d">{dur}</span></div>'
        )
    return "".join(out), nmodel, ti


WF_T = 64.0
wf_ticks = "".join(
    f'<span style="left:{100 * s / WF_T:.2f}%">{s} s</span>' for s in range(0, 61, 20)
)
wf_with, mw, tw = waterfall("trace_with", "with-mcp", [], WF_T)
wf_without, mo, to = waterfall("trace_without", "no-mcp", STEP_WITHOUT, WF_T)
RW, RO = ROOTS["with-mcp"], ROOTS["no-mcp"]
fig4_body = f"""<div class="wf-pair">
  <div class="wf"><div class="wf-h"><b><i class="k-with"></i>With the server</b><span>${RW["study.total_cost_usd"]:.3f} · {RW["study.wall_s"]:.1f} s · {mw} model calls · {tw} tool calls</span></div>{wf_with}
    <div class="wf-axis"><span></span><div class="ticks">{wf_ticks}</div><span></span></div></div>
  <div class="wf"><div class="wf-h"><b><i class="k-without"></i>Without</b><span>${RO["study.total_cost_usd"]:.3f} · {RO["study.wall_s"]:.1f} s · {mo} model calls · {to} tool calls</span></div>{wf_without}
    <div class="wf-axis"><span></span><div class="ticks">{wf_ticks}</div><span></span></div></div>
</div>"""
assert (
    RW["study.checks_passed"] == RW["study.checks_total"] == 10
    and RO["study.checks_passed"] == RO["study.checks_total"] == 10
)
fig4 = figure(
    4,
    "One build, two traces",
    "Opus building the web-logs dashboard and alert rule in the first round, with and without the server, on one time scale. Each row is a span from Honeycomb's trace view: grey bars are model calls, coloured bars are tool calls.",
    fig4_body,
    f"Both runs passed all 10 checks. With the server, Opus made {tw} short tool calls. Without it, Opus spent its first 20 seconds reading the API reference before it created anything, and one shell call failed. Each trace also holds one short side call that Claude Code makes on its own. The runs took {RW['study.num_turns']} and {RO['study.num_turns']} turns.",
    cls="wide",
    src_override=f'Open in Honeycomb: <a href="{TR_WITH}">trace with the server</a> · <a href="{TR_WITHOUT}">trace without</a>. '
    f'Run summaries: <a href="{qlink("jp-YjDUjFVgu")}">query</a>; shell-step labels from the spans: <a href="{qlink("gX-3uoFdZNdg")}">query</a>.',
)

# ------------------------------------------------------------------ figures 5-10: where the savings come from
fig5 = figure(
    5,
    "Tool output read back per run (average)",
    "Average bytes of tool output fed back into the model per run, over all four tasks.",
    model_fig(BYTES, lambda v: f"{v / 1000:.1f} kB", lambda v: f"{v / 1000:.0f} kB"),
    "Without the server the agent reads raw API JSON and slices of Kibana's API reference; with it, each tool returns a short answer.",
    BOARD2,
    B2,
    "52-VMv7CZGJc",
    cls="bh-l",
)
fig6 = figure(
    6,
    "Output tokens per run (average)",
    "Average tokens the model wrote per run: its replies, commands and request bodies.",
    model_fig(OUT, lambda v: f"{v:,.0f}"),
    "Writing curl commands and JSON bodies by hand costs output tokens; calling a tool with a few arguments does not.",
    BOARD2,
    B2,
    "6r-cVxmt8Mwn",
    cls="bh-l",
)
fig7 = figure(
    7,
    "New context written to cache per run (average)",
    "Average tokens written to the prompt cache per run. What the model reads back becomes new context, and cache writes are billed above normal input.",
    model_fig(CACHE, lambda v: f"{v / 1000:.1f}k", lambda v: f"{v / 1000:.0f}k"),
    "This moves with the cost gap in Figure 1.",
    BOARD2,
    B2,
    "wa-w7TiDZeXD",
    cls="bh-l",
)
fig8 = figure(
    8,
    "Model turns per run (average)",
    "Average number of model turns per run.",
    model_fig(TURNS, lambda v: f"{v:.1f}", lambda v: f"{v:.0f}"),
    "Only Haiku needs far more turns without the server. Sonnet and Opus take about as many steps either way; each step carries more text.",
    BOARD2,
    B2,
    "84-hBWXwHUxZ",
    cls="bh-l",
)

ts_max, ts_t = nice_scale(max(TS_M[x] + TS_O[x] for x in TS_M))
ts_rows = []
for m in MODELS:
    ts_rows.append(f'<div class="ts-model">{MNAME[m]}</div>')
    for a, nm, c in (("with-mcp", "With the server", "w"), ("no-mcp", "Without", "o")):
        mt, ot = TS_M[(m, a)], TS_O[(m, a)]
        ts_rows.append(
            f'<div class="ts-row" data-tip="{MNAME[m]} · {nm}: model {mt:.1f} s + other {ot:.1f} s">'
            f'<span class="ts-lab">{nm}</span><span class="ts-track"><i class="ts-api {c}" style="width:{100 * mt / ts_max:.2f}%"></i>'
            f'<i class="ts-rest" style="width:{100 * ot / ts_max:.2f}%"></i></span><span class="ts-val">{mt:.1f} + {ot:.1f} s</span></div>'
        )
fig9 = figure(
    9,
    "Model time and other time per run (average)",
    "Average seconds per run spent waiting on the model, plus everything else (start-up and running the tools).",
    f'<div class="legend"><span><i class="k-with"></i>Model time, with the server</span><span><i class="k-without"></i>Model time, without</span><span><i class="k-rest"></i>Everything else</span></div>'
    f'<div class="ts">{"".join(ts_rows)}<div class="ts-axis"><span></span><div class="ticks">{tick_html(ts_t, ts_max, lambda v: f"{v:.0f} s")}</div><span></span></div></div>',
    f"The time saved is model time. Everything else averages {min(TS_O.values()):.1f} to {max(TS_O.values()):.1f} s in both setups.",
    BOARD2,
    B2,
    "9u-toPPjoMyU",
)

srv_max = nice_max(max(r["COUNT"] for r in SERVER))
srv_rows = "".join(
    f'<div class="sv-row" data-tip="{esc(r["name"])}: {r["COUNT"]} calls, median {r["P50(duration_ms)"]:.0f} ms">'
    f'<span class="sv-lab mono">{esc(r["name"].replace("tools/call ", ""))}</span>'
    f'<span class="sv-track"><i style="width:{100 * r["COUNT"] / srv_max:.2f}%"></i></span>'
    f'<span class="sv-n">{r["COUNT"]}</span><span class="sv-ms">{r["P50(duration_ms)"]:,.0f} ms</span></div>'
    for r in SERVER
)
fig10 = figure(
    10,
    "Kibana tool calls served by the MCP server",
    f"The server's own spans, one per Kibana tool call, from the {N_ARM['with-mcp']} runs with the server: how often each tool was called, and its median time inside the server.",
    f'<div class="sv"><div class="sv-row sv-head"><span>Tool</span><span></span><span class="r">Calls</span><span class="r">Median</span></div>{srv_rows}</div>',
    "Reads take 27 to 54 ms inside the server; writes take 0.7 to 1.5 s.",
    BOARD2,
    B2,
    "pH-JB1yihmCd",
)


# ------------------------------------------------------------------ figures 11-14: across models and tasks
def heat(name, alt):
    return (
        f'<div class="hc-frame"><a href="{IMG_URL[name]}" class="hc-link" aria-label="Open the full-size Honeycomb chart">'
        f'<img src="{img64(name)}" alt="{esc(alt)}" width="800" height="344"></a></div>'
    )


fig11 = figure(
    11,
    "Cost of every run (heatmap)",
    f'Each cell counts runs at that cost (vertical, US dollars) and start time (horizontal). Colour is the number of runs in the cell, darker for more. {GAP["runs_at_or_above_015"]} runs sit between ${GAP["min_at_or_above_015"]:.2f} and ${GAP["max_cost"]:.2f}, and no run falls between ${GAP["max_below_015"]:.2f} and ${GAP["min_at_or_above_015"]:.2f} (<a href="{qlink("EJ-KfWL9NUNG")}">query</a>).',
    heat(
        "fig3_heatall",
        f"Honeycomb heatmap of run cost from 17:10 to 20:10 UTC: most runs below $0.15, and a separate band of {GAP['runs_at_or_above_015']} runs between ${GAP['min_at_or_above_015']:.2f} and ${GAP['max_cost']:.2f}.",
    ),
    "",
    BOARD1,
    B1,
    "pW-124hpWVWU",
    kind="real",
)
fig12 = figure(
    12,
    "Time of every run (heatmap)",
    f"Each cell counts runs at that wall-clock time (vertical, seconds) and start time (horizontal). Colour is the number of runs in the cell, darker for more. Most runs finish within 40 s; {GAP['runs_over_60s']} run past 60 s, up to {GAP['max_wall_s']:.0f} s.",
    heat(
        "fig4_timeheat",
        f"Honeycomb heatmap of run time: most runs between 8 and 40 seconds, and a tail of {GAP['runs_over_60s']} runs above 60 seconds, up to {GAP['max_wall_s']:.0f} seconds.",
    ),
    "",
    BOARD1,
    B1,
    "ux-3jHLdSd4p",
    kind="real",
)
chk_rows = "".join(
    f'<div class="meter"><span class="m-lab"><i class="{"k-with" if a == "with-mcp" else "k-without"}"></i>{"With the server" if a == "with-mcp" else "Without"}</span>'
    f'<span class="m-track"><i class="{"w" if a == "with-mcp" else "o"}" style="width:{CHECKS[a]["pct_checks_passed"]:.0f}%"></i></span>'
    f'<span class="m-val"><b>{CHECKS[a]["pct_checks_passed"]:.0f}%</b> · {CHECKS[a]["checks_passed"]:,.0f} of {CHECKS[a]["checks_run"]:,.0f}</span></div>'
    for a in ARMS
)
tot_checks = sum(CHECKS[a]["checks_run"] for a in ARMS)
fig13 = figure(
    13,
    "Checks passed by setup",
    "Share of checks passed, summed over runs. Each check compares Kibana's real state after the run with the expected result.",
    f'<div class="meters">{chk_rows}</div>',
    f"All {tot_checks:,} checks passed in both setups: the server's savings came with no loss of correctness.",
    BOARD1,
    B1,
    "rN-8QrRgr7pR",
)
fig14_groups = "".join(
    pair_group(
        TNAME[t], RUNS[(t, "with-mcp")], RUNS[(t, "no-mcp")], 40, lambda v: f"{v:.0f}", TNAME[t], ""
    )
    for t in TASKS
)
fig14 = figure(
    14,
    "Runs per task and setup",
    "Count of runs per task and setup. The design is 36 per cell: 3 models × 12 rounds. The dashed line marks 36.",
    f'{LEGEND}<div class="single compact"><div class="rows target">{fig14_groups}</div></div>',
    f'Change existing, with the server: 35 of 36. One Opus run did not reach Honeycomb (<a href="{qlink("kf-BkAYjcxRk")}">query</a>).',
    BOARD1,
    B1,
    "Jo-6V7ALFxvJ",
    cls="bh-s",
)


# ------------------------------------------------------------------ Honeycomb screenshots after each redrawn figure
def png_size(path):
    b = path.read_bytes()[16:24]
    return int.from_bytes(b[:4], "big"), int.from_bytes(b[4:], "big")


# figure number -> [(screenshot in data/hc/, what it shows, link)]
SHOTS = {
    1: [("fig1", "Board panel “Fig 1 · Cost per run by model, task and setup”", qlink("rQ-XCLthePHQ"))],
    2: [("fig2", "Board panel “Fig 2 · Time per run by model, task and setup”", qlink("jr-K1QutEy5g"))],
    3: [
        ("fig3_with", "Board panel “Fig 3 · Total spend, with the server”", qlink("4K-e8XBSJh5x")),
        ("fig3_without", "Board panel “Fig 3 · Total spend, without the server”", qlink("jW-hcFJn2Hg2")),
    ],
    4: [
        ("fig4_with", "Trace view, with the server", TR_WITH),
        ("fig4_without", "Trace view, without the server", TR_WITHOUT),
    ],
    5: [("fig5", "Board panel “Fig 5 · Tool output read back per run (average)”", qlink("52-VMv7CZGJc"))],
    6: [("fig6", "Board panel “Fig 6 · Output tokens per run (average)”", qlink("6r-cVxmt8Mwn"))],
    7: [("fig7", "Board panel “Fig 7 · New context written to cache per run (average)”", qlink("wa-w7TiDZeXD"))],
    8: [("fig8", "Board panel “Fig 8 · Model turns per run (average)”", qlink("84-hBWXwHUxZ"))],
    9: [("fig9", "Board panel “Fig 9 · Model time and other time per run (average)”", qlink("9u-toPPjoMyU"))],
    10: [("fig10", "Board panel “Fig 10 · Kibana tool calls served by the MCP server”", qlink("pH-JB1yihmCd"))],
    13: [("fig13", "Board panel “Fig 13 · Checks passed by setup”", qlink("rN-8QrRgr7pR"))],
    14: [("fig14", "Board panel “Fig 14 · Runs per task and setup”", qlink("Jo-6V7ALFxvJ"))],
}


def shot(n):
    items = []
    for name, what, link in SHOTS[n]:
        f = D / "hc" / f"{name}.png"
        w, h = (v // 2 for v in png_size(f))  # captured at device pixel ratio 2
        data = "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode()
        items.append(
            f'<figure class="shot"><a href="{link}" aria-label="Open in Honeycomb: {esc(what)}">'
            f'<img src="{data}" alt="Honeycomb screenshot: {esc(what)}" width="{w}" height="{h}" loading="lazy"></a>'
            f"<figcaption>{esc(what)}</figcaption></figure>"
        )
    return f"""<div class="figblock hc-shot">
<div class="fc"><span class="fc-num">Figure {n} in Honeycomb</span><p class="fc-sub">The same data as Figure {n}, as Honeycomb shows it. Select an image to open it in Honeycomb.</p></div>
<div class="shots">{"".join(items)}</div>
</div>"""


# ------------------------------------------------------------------ tables
def bu_table(rows_, caption):
    tr = "".join(
        f'<tr class="{"design" if d else ""}"><th scope="row">{a}{" <span class=tag>setup</span>" if d else ""}</th><td class="n">{b}</td><td class="n"><b>{s}</b></td></tr>'
        for a, b, s, d in rows_
    )
    return f'<div class="tbl-wrap bu-wrap"><table class="bu"><caption>{caption}</caption><thead><tr><th>What the selected runs have in common</th><th class="r">Other runs</th><th class="r">Selected</th></tr></thead><tbody>{tr}</tbody></table></div>'


BU1 = bu_table(
    [
        ("Model is Opus", "27.0%", "100%", True),
        ("Setup is without the server", "45.6%", "100%", True),
        ("Task is a build (web logs or flights)", "45.6%", "100%", True),
    ],
    f"BubbleUp on the {GAP['runs_at_or_above_015']} runs above $0.15, against the 263 others. Rows tagged “setup” are the model, setup and task each run was given.",
)
BU2 = bu_table(
    [
        ("Setup is without the server", "44.1%", "100%", True),
        ("Task is a build", "44.1%", "100%", True),
        ("Model is Haiku", "29.7%", "64.5%", True),
        ("At least one failed tool call", "18.4%", "83.9%", False),
        ("At least one HTTP 4xx error from Kibana", "17.6%", "71.0%", False),
        ("A failed call the agent never retried", "4.7%", "67.7%", False),
    ],
    f"BubbleUp on the {GAP['runs_over_60s']} runs over 60 s, against the 256 others.",
)

hdr = "".join(f'<th class="r">{TNAME[t]}</th>' for t in TASKS)
body = ""
for kind, label in (("cost", "Cost"), ("time", "Time")):
    for m in MODELS:
        cells = "".join(f'<td class="n">{TASK_RATIO[kind][m][t]:.2f}</td>' for t in TASKS)
        body += (
            f'<tr><th scope="row">{label} · {MNAME[m]}</th>{cells}<td class="n"><b>{MODEL_RATIO[kind][m]:.2f}</b></td>'
            f'<td class="n">{PCT[kind][m]}% less</td></tr>'
        )
RATIO_TABLE = (
    f'<div class="tbl-wrap"><table class="ratio"><caption>With the server ÷ without, from Honeycomb P50s (<a href="{qlink("rQ-XCLthePHQ")}">cost query</a>, '
    f'<a href="{qlink("jr-K1QutEy5g")}">time query</a>). Per model: geometric mean of the four task ratios.</caption>'
    f'<thead><tr><th>Measure · model</th>{hdr}<th class="r">Per model</th><th class="r">Saving</th></tr></thead><tbody>{body}</tbody></table></div>'
)


def by_model(kind):
    return ", ".join(f"{MSHORT[m]} {PCT[kind][m]}%" for m in MODELS)


# ------------------------------------------------------------------ page
css = (HERE / "page.tmpl.html").read_text()
css = css[css.index("<style>") + len("<style>") : css.index("</style>")]
css = re.sub(r"/\* Layout: client question first.*?\*/\n", "", css)
css = re.sub(
    r"/\* Layout: one reading column.*?\*/",
    "/* Layout: one reading column (~44rem) for prose; figures widen to ~64rem, each a self-contained 16:9 slot. Answer first (cost, time), then where the savings come from, then whether they hold, then method collapsed. */",
    css,
)
css = css.replace(
    "padding-inline: 20px; padding-block: 40px 56px;",
    "padding-left: max(20px, env(safe-area-inset-left, 0px)); padding-right: max(20px, env(safe-area-inset-right, 0px)); padding-block: 40px 56px;",
)
css = css.replace(
    ".page { padding-inline: 16px; padding-block: 24px 40px; gap: 48px; }",
    ".page { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 24px 40px; gap: 48px; }",
)
css += """
/* P25-P75 line across each P50 bar */
.pb-iqr { position: absolute; top: 50%; height: 2px; margin-top: -1px; background: var(--ink); opacity: .75; }
.pb-iqr::before, .pb-iqr::after { content: ""; position: absolute; top: -3px; width: 2px; height: 8px; background: var(--ink); }
.pb-iqr::before { left: 0; } .pb-iqr::after { right: 0; }
.k-iqr { display: inline-block; width: 16px; height: 2px; background: var(--ink); vertical-align: 4px; margin-right: 6px; position: relative; }
.pb-row { overflow: visible; }
.pb-track { overflow: visible; }
.facets .pb-row { grid-template-columns: minmax(0, 1fr) 4.2rem; }

/* setup diagram (not a figure) */
.diagram { padding: 18px 20px; display: flex; flex-direction: column; gap: 14px; max-width: 64rem; }
.lane { display: grid; grid-template-columns: 9rem minmax(0, 1fr) 2rem minmax(0, 1.3fr) 2rem minmax(0, 1fr); align-items: stretch; gap: 8px; }
.lane-lab { font-size: 13px; font-weight: 600; color: var(--ink-2); align-self: center; }
.node { border: 1px solid var(--rule-strong); border-radius: 8px; padding: 10px 12px; font-family: var(--font-display); font-stretch: 85%; font-weight: 700; font-size: 1rem; display: flex; flex-direction: column; justify-content: center; gap: 2px; min-width: 0; background: var(--bg); }
.node small { font-family: var(--font-body); font-stretch: 100%; font-weight: 400; font-size: 12.5px; color: var(--ink-2); overflow-wrap: anywhere; }
.node-with { border-color: var(--with); box-shadow: inset 3px 0 0 var(--with); }
.node-without { border-color: var(--without); box-shadow: inset 3px 0 0 var(--without); }
.arrow { position: relative; align-self: center; height: 2px; background: var(--rule-strong); }
.arrow::after { content: ""; position: absolute; right: -1px; top: -4px; border: 5px solid transparent; border-left: 7px solid var(--rule-strong); border-right: 0; }
.diagram .after { font-size: 13.5px; color: var(--ink-2); border-top: 1px solid var(--rule); padding-top: 12px; max-width: 52rem; }
@media (max-width: 760px) {
  .lane { grid-template-columns: minmax(0, 1fr); gap: 6px; }
  .arrow { justify-self: center; width: 2px; height: 16px; }
  .arrow::after { right: auto; left: -4px; top: auto; bottom: -1px; border: 5px solid transparent; border-top: 7px solid var(--rule-strong); border-bottom: 0; }
  .lane + .lane { border-top: 1px solid var(--rule); padding-top: 12px; }
}
table.ratio { min-width: 38rem; }
/* Fig 3: two numbers */
.fig-slot > .spend { flex: 1 1 auto; }
.spend { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 40px; align-content: center; padding-inline: 4%; }
.spend-n { display: flex; flex-direction: column; gap: 6px; min-width: 0; border-top: 1px solid var(--rule); padding-top: 14px; }
.spend-lab { font-size: 15px; font-weight: 600; color: var(--ink-2); }
.spend-val { font-family: var(--font-display); font-stretch: 80%; font-weight: 750; font-size: clamp(3rem, 9vw, 6.5rem); line-height: 1; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.spend-sub { font-family: var(--font-mono); font-size: 12px; color: var(--muted); }
.spend-ratio { grid-column: 1 / -1; font-family: var(--font-mono); font-size: 14px; color: var(--ink-2); margin-top: 8px; }
@media (max-width: 520px) { .spend { grid-template-columns: minmax(0, 1fr); padding-inline: 0; gap: 18px; } }
"""

head = f"""<title>mcp-observability-study</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75..100,500..800&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap">
<style>{css}</style>
"""
tmpl = head + (HERE / "body.tmpl.html").read_text()
subs = {
    "FIG1": fig1,
    "FIG2": fig2,
    "FIG3": fig3,
    "FIG4": fig4,
    "FIG5": fig5,
    "FIG6": fig6,
    "FIG7": fig7,
    "FIG8": fig8,
    "FIG9": fig9,
    "FIG10": fig10,
    "FIG11": fig11,
    "FIG12": fig12,
    "FIG13": fig13,
    "FIG14": fig14,
    "BU1": BU1,
    "BU2": BU2,
    "RATIO_TABLE": RATIO_TABLE,
    "BOARD1": BOARD1,
    "BOARD2": BOARD2,
    "B1": esc(B1),
    "B2": esc(B2),
    "BU_COST": BU_COST,
    "BU_TIME": BU_TIME,
    "SPEND_W": f"{SPEND['with-mcp']:.2f}",
    "SPEND_O": f"{SPEND['no-mcp']:.2f}",
    "Q_COUNT": qlink("Be-3ftX86jNK"),
    "Q_FAIL": qlink("DU-1WXWjPGEk"),
    "Q_MISSING": qlink("kf-BkAYjcxRk"),
    "Q_ROOTS": qlink("jp-YjDUjFVgu"),
    "Q_SPANS": qlink("gX-3uoFdZNdg"),
    "COST_LO": str(min(PCT["cost"].values())),
    "COST_HI": str(max(PCT["cost"].values())),
    "TIME_LO": str(min(PCT["time"].values())),
    "TIME_HI": str(max(PCT["time"].values())),
    "COST_BY_MODEL": by_model("cost"),
    "TIME_BY_MODEL": by_model("time"),
    "AVGB_LO": f"{min(AVG_BYTES.values()):.0f}",
    "AVGB_HI": f"{max(AVG_BYTES.values()):.0f}",
    "FAIL_SLOW": f"{100 * FAIL_N_SLOW / N_SLOW:.0f}",
    "FAIL_REST": f"{100 * FAIL_N_REST / N_REST:.0f}",
    "FAIL_N_SLOW": str(FAIL_N_SLOW),
    "FAIL_N_REST": str(FAIL_N_REST),
    "N_SLOW": str(N_SLOW),
    "N_REST": str(N_REST),
}
for n in SHOTS:
    subs[f"FIG{n}"] += "\n" + shot(n)
for kk, v in subs.items():
    tmpl = tmpl.replace("{{" + kk + "}}", v)
assert "{{" not in tmpl, re.findall(r"\{\{\w+\}\}", tmpl)
(HERE / "report.html").write_text(tmpl)
print(
    "ok",
    len(tmpl),
    "PCT",
    PCT,
    "ratios",
    {k: {m: round(v, 3) for m, v in d.items()} for k, d in MODEL_RATIO.items()},
    "avg bytes",
    {m: round(v, 1) for m, v in AVG_BYTES.items()},
    "sep",
    len(SEP_COST),
    len(SEP_TIME),
    "calls",
    mw,
    mo,
    tw,
    to,
)
