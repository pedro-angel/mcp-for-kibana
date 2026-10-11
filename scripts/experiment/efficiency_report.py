#!/usr/bin/env python3
"""Build the efficiency study's results page from committed run records.

Every number on the page is read from ``runs/efficiency/full/analysis.json``,
the run records next to it (``record.json``, ``transcript.jsonl``) or the
snapshots directory; nothing is typed in by hand. The page stamps the commit
and SHA-256 of the ``analysis.json`` it read.

The page is a claude.ai artifact body (no ``<html>``/``<head>``/``<body>``):
``<out>/index.html`` plus the chosen snapshot PNGs copied to ``<out>/img/``.

Deterministic for a given tree: no clock, no randomness, sorted inputs.
Stdlib only.

    uv run python scripts/experiment/efficiency_report.py --out /tmp/report
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import efficiency_analyze as ea

HERE = Path(__file__).resolve().parent
ROOT = HERE / "runs" / "efficiency" / "full"
MODELS = (
    ("claude-haiku-5-5", "Haiku 5.5"),
    ("claude-sonnet-5-5", "Sonnet 5.5"),
    ("claude-opus-5-5", "Opus 5.5"),
)
ARMS = ("with-mcp", "no-mcp")
ARM_LABEL = {"with-mcp": "With the server", "no-mcp": "Without the server"}
ARM_SHORT = {"with-mcp": "with", "no-mcp": "without"}
# The model whose runs the timeline and snapshots show.
EXAMPLE_MODEL = "claude-opus-5-5"
CREATE_DASHBOARD = "mcp__mcp-for-kibana__create_dashboard"
# Shell HTTP targets that run a query rather than manage an object.
QUERY_PATH = re.compile(r"/api/console/proxy|/api/esql|/internal/search|/_search|/_query")
ERROR_CLASSES = (
    ("validation", "Invalid arguments"),
    ("http_4xx", "HTTP 4xx"),
    ("http_5xx", "HTTP 5xx"),
    ("timeout", "Timed out"),
    ("refused", "Refused by permissions"),
    ("command_failed", "Command exited non-zero"),
    ("unknown_tool", "Unknown tool"),
    ("other", "Other"),
)


def e(s: object) -> str:
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- formatting


def money(v: float) -> str:
    return f"${v:.3f}" if v < 1 else f"${v:.2f}"


def ktok(v: float) -> str:
    return f"{v / 1000:.0f}k"


def secs(ms: float) -> str:
    return f"{ms / 1000:.0f} s"


def num(v: float) -> str:
    return f"{v:g}"


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def ratio(c: dict) -> str:
    lo, hi = c["ratio_ci95"]
    return f"{c['ratio']:.2f}× (CI {lo:.2f}–{hi:.2f})"


# ---------------------------------------------------------------- provenance


def provenance(path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = path.relative_to(HERE.parent.parent)
    commit, dirty = "unknown", False
    try:
        cwd = HERE.parent.parent
        commit = (
            subprocess.run(
                ["git", "log", "-1", "--format=%h", "--", str(rel)],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            or "uncommitted"
        )
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", str(rel)],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        pass
    return {"path": str(rel), "commit": commit, "dirty": dirty, "sha256": digest[:12]}


# ---------------------------------------------------------------- charts


def log_x(lo: float, hi: float, left: float, width: float):
    def x(r: float) -> float:
        r = min(max(r, lo), hi)
        return left + (math.log(r) - math.log(lo)) / (math.log(hi) - math.log(lo)) * width

    return x


def forest(
    chart_id: str, title: str, desc: str, groups: list, lo: float, hi: float, ticks: tuple
) -> str:
    """The interval chart twice: a wide layout and a phone layout; CSS shows one."""
    wide = _forest(chart_id, title, desc, groups, lo, hi, ticks, 720, 150, 150)
    narrow = _forest(chart_id + "n", title, desc, groups, lo, hi, ticks, 400, 88, 116)
    return f'<div class="scroll wide">{wide}</div><div class="narrow">{narrow}</div>'


def _forest(
    chart_id: str,
    title: str,
    desc: str,
    groups: list,
    lo: float,
    hi: float,
    ticks: tuple,
    w: int,
    left: int,
    right_col: int,
) -> str:
    """Ratio-of-medians interval chart on a log scale; one row per (label, cmp)."""
    rowh, top, gap = 24, 40, 14
    short = w < 500
    plot_w = w - left - right_col - 16
    x = log_x(lo, hi, left, plot_w)
    nrows = sum(len(rows) for _, rows in groups)
    height = top + nrows * rowh + len(groups) * (gap + 18) + 26
    out = [
        f'<svg viewBox="0 0 {w} {height}" role="img" aria-labelledby="{chart_id}-t '
        f'{chart_id}-d" class="chart forest">',
        f'<title id="{chart_id}-t">{e(title)}</title><desc id="{chart_id}-d">{e(desc)}</desc>',
    ]
    for t in ticks:
        cls = "one" if t == 1 else "grid"
        out.append(
            f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top - 8}" '
            f'y2="{height - 24}" class="{cls}"/>'
        )
        out.append(
            f'<text x="{x(t):.1f}" y="{height - 8}" class="tick" text-anchor="middle">'
            f"{t:g}{'' if short else '×'}</text>"
        )
    less, more = (
        ("← less", "more →") if short else ("← less with the server", "more with the server →")
    )
    out.append(f'<text x="{x(lo):.1f}" y="{top - 18}" class="axis">{less}</text>')
    out.append(f'<text x="{x(hi):.1f}" y="{top - 18}" class="axis" text-anchor="end">{more}</text>')
    out.append(f'<text x="{w}" y="{top - 18}" class="axis" text-anchor="end">ratio (95% CI)</text>')
    y = top
    for name, rows in groups:
        out.append(f'<text x="0" y="{y + 12}" class="group">{e(name)}</text>')
        y += 18
        for label, c in rows:
            lo_c, hi_c = c["ratio_ci95"]
            cy = y + rowh / 2
            out.append(f'<text x="14" y="{cy + 4:.1f}" class="mlabel">{e(label)}</text>')
            out.append(
                f'<line x1="{x(lo_c):.1f}" x2="{x(hi_c):.1f}" y1="{cy:.1f}" '
                f'y2="{cy:.1f}" class="whisk"/>'
            )
            out.append(
                f'<circle cx="{x(c["ratio"]):.1f}" cy="{cy:.1f}" r="5" class="dot">'
                f"<title>{e(label)}, {e(name)}: {e(ratio(c))}</title></circle>"
            )
            out.append(
                f'<text x="{w}" y="{cy + 4:.1f}" class="val" text-anchor="end">'
                f'{c["ratio"]:.2f} <tspan class="ci">({lo_c:.2f}–{hi_c:.2f})</tspan></text>'
            )
            y += rowh
        y += gap
    out.append("</svg>")
    return "".join(out)


def tool_label(tc: dict) -> str:
    name = tc["name"]
    if name.startswith(ea.MCP_PREFIX):
        return name.split("__")[-1]
    if name in ea.SHELL_TOOLS:
        m = re.match(r"\S+ ([A-Z]+) (/\S*)", tc["agent"]["agent.tool.target"])
        if m:
            return f"curl {m.group(1)} {m.group(2)}"
        cmd = str(tc["input"].get("command", ""))
        return "shell: search the API reference" if "kibana-api" in cmd else "shell: local command"
    if name == "Read":
        return f"read {Path(str(tc['input'].get('file_path', ''))).name}"
    return name


def typical_run(runs: list[dict], dist: dict) -> dict:
    """The passing run closest to its cell's medians of model calls, tool calls
    and wall time (each distance scaled by the cell's IQR); ties by run id."""
    keys = ("agent.model.calls", "agent.tool.calls", "agent.time.wall_ms")

    def score(r: dict) -> float:
        return sum(abs(r["agent"][k] - dist[k]["median"]) / max(dist[k]["iqr"], 1e-9) for k in keys)

    return min((r for r in runs if r["success_strict"]), key=lambda r: (score(r), r["run_id"]))


def timeline(chart_id: str, loaded: list[tuple[str, dict, dict]]) -> tuple[str, str]:
    """Both arms' runs on one time scale: model calls as numbered bars, tool
    calls as ticks, failed tool calls marked with a cross. Returns the SVG and
    the per-arm step lists."""
    w, left, right, lane_h = 760, 92, 16, 22
    plot_w = w - left - right
    span_ms = max(run["agent"]["agent.time.wall_ms"] for _, run, _ in loaded)
    end_s = math.ceil(span_ms / 1000 / 10) * 10

    def x(ns_from_start: float) -> float:
        return left + ns_from_start / 1e9 / end_s * plot_w

    block_h = 30 + lane_h * 2 + 18
    height = 18 + block_h * len(loaded) + 30
    out = [
        f'<svg viewBox="0 0 {w} {height}" role="img" aria-labelledby="{chart_id}-t '
        f'{chart_id}-d" class="chart timeline">',
        f'<title id="{chart_id}-t">Two typical runs on one time scale</title>'
        f'<desc id="{chart_id}-d">Each numbered bar is one model call; each tick below it is '
        "a tool call; a cross marks a failed tool call. Bars run from the call's input to its "
        "last streamed block.</desc>",
    ]
    axis_bottom = height - 26
    for t in range(0, end_s + 1, 10):
        xt = left + t / end_s * plot_w
        out.append(f'<line x1="{xt:.1f}" x2="{xt:.1f}" y1="14" y2="{axis_bottom}" class="grid"/>')
        out.append(
            f'<text x="{xt:.1f}" y="{height - 8}" class="tick" text-anchor="middle">{t} s</text>'
        )
    lists = []
    y = 18
    for arm, run, ld in loaded:
        start = ld["start"]
        chats, tools = ld["parsed"]["chats"], ld["parsed"]["tools"]
        wall = run["agent"]["agent.time.wall_ms"]
        out.append(f'<text x="0" y="{y + 12}" class="group">{e(ARM_LABEL[arm])}</text>')
        out.append(
            f'<text x="{w}" y="{y + 12}" class="axis" text-anchor="end">'
            f"{len(chats)} model calls · {len(tools)} tool calls · {wall / 1000:.0f} s</text>"
        )
        y_model = y + 22
        y_tool = y_model + lane_h + 4
        out.append(f'<text x="0" y="{y_model + 14}" class="mlabel">Model</text>')
        out.append(f'<text x="0" y="{y_tool + 14}" class="mlabel">Tools</text>')
        out.append(
            f'<line x1="{left}" x2="{x(wall * 1e6):.1f}" y1="{y_tool + lane_h - 2}" '
            f'y2="{y_tool + lane_h - 2}" class="lane"/>'
        )
        out.append(
            f'<line x1="{x(wall * 1e6):.1f}" x2="{x(wall * 1e6):.1f}" y1="{y_model - 2}" '
            f'y2="{y_tool + lane_h}" class="endline"/>'
        )
        for c in chats:
            x0, x1 = x(c["start"] - start) + 1, x(c["end"] - start) - 1
            bw = max(x1 - x0, 2)
            out.append(
                f'<rect x="{x0:.1f}" y="{y_model}" width="{bw:.1f}" height="{lane_h - 4}" rx="3" '
                f'class="bar {arm}"><title>Model call {c["index"] + 1}: '
                f"{(c['start'] - start) / 1e9:.1f}–{(c['end'] - start) / 1e9:.1f} s</title></rect>"
            )
            if bw >= 13:
                out.append(
                    f'<text x="{x0 + bw / 2:.1f}" y="{y_model + 13}" class="inbar" '
                    f'text-anchor="middle">{c["index"] + 1}</text>'
                )
        for i, tc in enumerate(tools, 1):
            tx0 = x(tc["start"] - start)
            tw = max(x(tc["end"] - start) - tx0, 3)
            failed = tc["agent"]["agent.tool.failed"]
            out.append(
                f'<rect x="{tx0:.1f}" y="{y_tool + 4}" width="{tw:.1f}" height="{lane_h - 8}" '
                f'class="tool"><title>Tool call {i}: {e(tool_label(tc))}'
                f"{' (failed)' if failed else ''}</title></rect>"
            )
            if failed:
                cx, cy = tx0 + tw / 2, y_tool + lane_h + 8
                out.append(f'<path d="M{cx - 4:.1f} {cy - 4:.1f}l8 8m0 -8l-8 8" class="fail"/>')
        y += block_h
        # Step list: what each model call asked for.
        by_chat: dict[str, list[dict]] = {}
        for tc in tools:
            by_chat.setdefault(tc["chat_id"], []).append(tc)
        items = []
        for c in chats:
            asks = by_chat.get(c["id"], [])
            parts = []
            for tc in asks:
                a = tc["agent"]
                tag = ""
                if a["agent.tool.failed"]:
                    cls = dict(ERROR_CLASSES).get(a["agent.tool.error_class"], "Failed")
                    tag = f' <span class="failtag">✕ {e(cls)}</span>'
                parts.append(f"<code>{e(tool_label(tc))}</code>{tag}")
            body = ", ".join(parts) if parts else "final answer"
            items.append(
                f'<li><span class="t">{(c["start"] - start) / 1e9:.0f} s</span> {body}</li>'
            )
        lists.append(
            f'<div class="steps {arm}"><h4>{e(ARM_LABEL[arm])}: what each model call asked for</h4>'
            f"<ol>{''.join(items)}</ol></div>"
        )
    out.append("</svg>")
    return "".join(out), "".join(lists)


SPLIT_SEGS = (
    ("model", "Model calls"),
    ("busy", "Tool work"),
    ("waits", "Sleeps and timed-out calls"),
    ("rest", "Other"),
)


def time_split(chart_id: str, rows: list[tuple[str, str, dict]]) -> str:
    """The stacked time chart twice (wide and phone layouts) under one HTML legend."""
    legend = "".join(
        f'<span><i class="ls s{i}"></i>{e(name)}</span>' for i, (_, name) in enumerate(SPLIT_SEGS)
    )
    wide = _time_split(chart_id, rows, 720, 170)
    narrow = _time_split(chart_id + "n", rows, 400, 132)
    return (
        f'<div class="legend">{legend}</div><div class="scroll wide">{wide}</div>'
        f'<div class="narrow">{narrow}</div>'
    )


def _time_split(chart_id: str, rows: list[tuple[str, str, dict]], w: int, left: int) -> str:
    """Mean seconds per run, stacked: model, tool work, sleeps and timed-out
    calls, the rest. Means, so the parts add up to the mean wall time."""
    segs = SPLIT_SEGS
    right, rowh, top = 48, 26, 14
    plot_w = w - left - right
    vmax = max(sum(r[k] for k, _ in segs) for _, _, r in rows)
    end = math.ceil(vmax / 50) * 50
    height = top + len(rows) * rowh + 34

    def x(v: float) -> float:
        return left + v / end * plot_w

    out = [
        f'<svg viewBox="0 0 {w} {height}" role="img" aria-labelledby="{chart_id}-t '
        f'{chart_id}-d" class="chart split">',
        f'<title id="{chart_id}-t">Where the time goes, mean seconds per run</title>'
        f'<desc id="{chart_id}-d">Stacked bars per model and arm: time in model calls, in tool '
        "work, in deliberate sleeps and timed-out tool calls, and the rest.</desc>",
    ]
    for t in range(0, end + 1, 50 if w > 500 else 100):
        out.append(
            f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top - 6}" y2="{height - 24}" '
            f'class="grid"/><text x="{x(t):.1f}" y="{height - 8}" class="tick" '
            f'text-anchor="middle">{t} s</text>'
        )
    y = top
    for label, arm, r in rows:
        out.append(
            f'<text x="0" y="{y + 16}" class="mlabel">'
            f"{e(label if w > 500 else label.split()[0])} · {e(ARM_SHORT[arm])}</text>"
        )
        x0 = x(0)
        total = sum(r[k] for k, _ in segs)
        for i, (k, name) in enumerate(segs):
            v = max(r[k], 0.0)
            if v <= 0:
                continue
            x1 = x0 + v / end * plot_w
            out.append(
                f'<rect x="{x0:.1f}" y="{y + 3}" width="{max(x1 - x0 - 2, 0.5):.1f}" '
                f'height="18" class="seg s{i}"><title>{e(label)}, {e(ARM_SHORT[arm])}: '
                f"{e(name)} {v:.1f} s</title></rect>"
            )
            x0 = x1
        out.append(f'<text x="{x0 + 6:.1f}" y="{y + 16}" class="val">{total:.0f} s</text>')
        y += rowh
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- page


def build(analysis_path: Path, out_dir: Path) -> None:
    A = json.loads(analysis_path.read_text())
    prov = provenance(analysis_path)
    S, C, M = A["summary"], A["comparison"], A["meta"]
    SG = A["signals"]
    SS, SC, PS, PC = (
        SG["summary"],
        SG["comparison"],
        SG["pooled"]["summary"],
        SG["pooled"]["comparison"],
    )
    runs = sorted(A["runs"], key=lambda r: r["run_id"])
    records = {}
    for r in runs:
        rec_path = ROOT / r["block"] / r["run_id"] / "record.json"
        records[r["run_id"]] = json.loads(rec_path.read_text())

    def cell(m: str, arm: str) -> list[dict]:
        return [r for r in runs if r["model"] == m and r["arm"] == arm]

    def cmp(m: str, k: str) -> dict:
        return C[m]["all_runs"][k]

    def med(m: str, arm: str, k: str) -> float:
        return S[m][arm]["metrics"][k]["median"]

    def smed(m: str, arm: str, k: str) -> float:
        return SS[m][arm]["distributions"][k]["median"]

    n_runs = M["n_runs"]
    n_arm = {a: sum(S[m][a]["n"] for m, _ in MODELS) for a in ARMS}
    n_cell = S[MODELS[0][0]][ARMS[0]]["n"]
    blocks = sorted({r["block"] for r in runs})
    per_block = Counter(r["block"] for r in runs)
    kib = sorted({rec["kibana_version"] for rec in records.values()})
    cli = sorted({rec["claude_cli"].split()[0] for rec in records.values()})
    n_tools = sorted(
        {len(rec["init_mcp_tools"]) for rec in records.values() if rec["arm"] == "with-mcp"}
    )
    toolboxes = sorted({rec["toolboxes"] for rec in records.values() if rec["arm"] == "with-mcp"})
    passed = {a: sum(S[m][a]["pass_strict"] for m, _ in MODELS) for a in ARMS}
    lenient = {a: sum(S[m][a]["pass_lenient"] for m, _ in MODELS) for a in ARMS}
    cost_r = {m: cmp(m, "cost_usd")["ratio"] for m, _ in MODELS}
    calls_pooled = PC["agent.model.calls"]
    tpc_pooled = PC["agent.usage.input_tokens_per_call"]
    known = {a: PS[a]["runs_with"]["agent.secret.known_exposed"] for a in ARMS}
    sens = max(
        abs(C[m]["all_runs"][k]["ratio"] - C[m]["passing_runs"][k]["ratio"])
        for m, _ in MODELS
        for k in ("cost_usd", "total_tokens", "tool_calls", "duration_ms")
    )

    # ---- results table (medians) and ratio chart
    measures = (
        ("cost_usd", "Cost per run (list price)", money),
        ("total_tokens", "Tokens per run", ktok),
        ("tool_calls", "Tool calls", num),
        ("duration_ms", "Time to finish", secs),
    )
    trows = []
    for m, label in MODELS:
        cells = "".join(
            f'<td class="n">{fmt(med(m, "with-mcp", k))}</td>'
            f'<td class="n">{fmt(med(m, "no-mcp", k))}</td>'
            for k, _, fmt in measures
        )
        sw, sn = S[m]["with-mcp"], S[m]["no-mcp"]
        trows.append(
            f'<tr><th scope="row">{label}</th>{cells}'
            f'<td class="n">{sw["pass_strict"]}/{sw["n"]}</td>'
            f'<td class="n">{sn["pass_strict"]}/{sn["n"]}</td></tr>'
        )
    thead = "".join(f'<th colspan="2" scope="colgroup">{name}</th>' for _, name, _ in measures)
    sub = "".join(
        '<th class="n arm-w">with</th><th class="n arm-o">without</th>'
        for _ in range(len(measures) + 1)
    )
    results_table = (
        '<div class="scroll"><table class="results"><thead>'
        f'<tr><th rowspan="2" scope="col">Model</th>{thead}'
        '<th colspan="2" scope="colgroup">Passed the check</th></tr>'
        f"<tr>{sub}</tr></thead><tbody>{''.join(trows)}</tbody></table></div>"
    )
    forest1 = forest(
        "fp1",
        "With the server divided by without, per model",
        "Ratio of medians with a 95% bootstrap interval; left of the line at 1 means the "
        "server needed less.",
        [(name, [(label, cmp(m, k)) for m, label in MODELS]) for k, name, _ in measures],
        0.1,
        1.5,
        (0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5),
    )

    # ---- why: calls x tokens per call
    forest2 = forest(
        "fp2",
        "Model calls and input tokens per call, with divided by without",
        "Ratio of medians with a 95% bootstrap interval, per model and pooled.",
        [
            (
                "Model calls per run",
                [(label, SC[m]["agent.model.calls"]) for m, label in MODELS]
                + [("All models", calls_pooled)],
            ),
            (
                "Input tokens per model call",
                [(label, SC[m]["agent.usage.input_tokens_per_call"]) for m, label in MODELS]
                + [("All models", tpc_pooled)],
            ),
        ],
        0.3,
        2.0,
        (0.3, 0.5, 0.7, 1.0, 1.5, 2.0),
    )
    son_tpc = SC["claude-sonnet-5-5"]["agent.usage.input_tokens_per_call"]
    hai_tpc = SC["claude-haiku-5-5"]["agent.usage.input_tokens_per_call"]
    opu_tpc = SC["claude-opus-5-5"]["agent.usage.input_tokens_per_call"]
    trb = PC["agent.usage.tool_result_bytes"]

    # ---- typical run timeline
    loaded = []
    for arm in ARMS:
        run = typical_run(cell(EXAMPLE_MODEL, arm), SS[EXAMPLE_MODEL][arm]["distributions"])
        loaded.append((arm, run, ea.load_run(ROOT / run["block"] / run["run_id"], None)))
    tl_svg, tl_lists = timeline("tl", loaded)
    ex_label = dict(MODELS)[EXAMPLE_MODEL]

    # ---- time split
    split_rows = []
    split_table = []
    for m, label in MODELS:
        for arm in ARMS:
            rs = cell(m, arm)
            n = len(rs)

            def mean(k: str, rs: list[dict] = rs, n: int = n) -> float:
                return sum(r["agent"][k] for r in rs) / n / 1000

            row = {
                "model": mean("agent.time.model_ms"),
                "busy": mean("agent.time.tool_busy_ms"),
                "waits": mean("agent.time.tool_ms") - mean("agent.time.tool_busy_ms"),
                "rest": mean("agent.time.wall_ms")
                - mean("agent.time.model_ms")
                - mean("agent.time.tool_ms"),
                "wall": mean("agent.time.wall_ms"),
            }
            split_rows.append((label, arm, row))
            split_table.append(
                f'<tr><th scope="row">{label} · {ARM_SHORT[arm]}</th>'
                + "".join(
                    f'<td class="n">{row[k]:.1f}</td>'
                    for k in ("model", "busy", "waits", "rest", "wall")
                )
                + "</tr>"
            )
    split_svg = time_split("ts", split_rows)
    share = {a: PS[a]["distributions"]["agent.time.model_share"]["median"] for a in ARMS}
    ftool = PC["agent.time.first_tool_ms"]
    wait_runs = PS["no-mcp"]["runs_with"]["agent.tool.wait_calls"]
    timeout_runs = PS["no-mcp"]["runs_with"]["agent.harness.timed_out_calls"]
    timeout_by_model = {
        m: SS[m]["no-mcp"]["runs_with"]["agent.harness.timed_out_calls"] for m, _ in MODELS
    }
    wait_by_model = {m: SS[m]["no-mcp"]["runs_with"]["agent.tool.wait_calls"] for m, _ in MODELS}
    son_api = cmp("claude-sonnet-5-5", "duration_api_ms")
    son_wall = cmp("claude-sonnet-5-5", "duration_ms")

    # ---- reliability
    tot = {a: PS[a]["totals"] for a in ARMS}
    rates = {a: PS[a]["rates"] for a in ARMS}
    rel_rows = [
        ("Tool calls", {a: f"{tot[a]['agent.tool.calls']:,}" for a in ARMS}),
        (
            "Flagged as an error by the harness",
            {
                a: f"{tot[a]['agent.tool.errors']} ({pct(rates[a]['tool_error_rate'])})"
                for a in ARMS
            },
        ),
        (
            "Did not do what was asked",
            {
                a: f"{tot[a]['agent.tool.failures']} ({pct(rates[a]['tool_failure_rate'])})"
                for a in ARMS
            },
        ),
        (
            "… of which the harness reported as success",
            {
                a: f"{tot[a]['agent.tool.hidden_failures']} ({pct(rates[a]['hidden_share_of_failures'])})"
                for a in ARMS
            },
        ),
        (
            "… of which a later call on the same target fixed",
            {
                a: f"{tot[a]['agent.tool.failures_recovered']} "
                f"({pct(rates[a]['recovered_share_of_failures'])})"
                for a in ARMS
            },
        ),
    ]
    class_rows = [
        (name, {a: str(tot[a].get(f"agent.tool.failures_{k}", 0)) for a in ARMS})
        for k, name in ERROR_CLASSES
        if any(tot[a].get(f"agent.tool.failures_{k}", 0) for a in ARMS)
    ]
    rel_table = (
        '<div class="scroll"><table class="compact"><thead><tr><th scope="col">Tool calls, '
        f'all {n_arm["with-mcp"]} runs per arm</th><th class="n arm-w" scope="col">With</th>'
        '<th class="n arm-o" scope="col">Without</th></tr></thead><tbody>'
        + "".join(
            f'<tr><th scope="row">{e(n)}</th><td class="n">{v["with-mcp"]}</td>'
            f'<td class="n">{v["no-mcp"]}</td></tr>'
            for n, v in rel_rows
        )
        + '<tr class="sub"><th scope="row" colspan="3">Failures by error class</th></tr>'
        + "".join(
            f'<tr><th scope="row" class="ind">{e(n)}</th><td class="n">{v["with-mcp"]}</td>'
            f'<td class="n">{v["no-mcp"]}</td></tr>'
            for n, v in class_rows
        )
        + "</tbody></table></div>"
    )
    # Per-call facts for the server arm, from its transcripts.
    cd_runs = cd_first_fail = cd_recovered = 0
    cd_first_classes: Counter = Counter()
    sh = Counter()
    for r in runs:
        if r["arm"] != "with-mcp":
            continue
        tools = ea.load_run(ROOT / r["block"] / r["run_id"], None)["parsed"]["tools"]
        cds = [tc for tc in tools if tc["name"] == CREATE_DASHBOARD]
        if cds:
            cd_runs += 1
            first = cds[0]["agent"]
            if first["agent.tool.failed"]:
                cd_first_fail += 1
                cd_first_classes[first["agent.tool.error_class"]] += 1
                cd_recovered += any(not tc["agent"]["agent.tool.failed"] for tc in cds[1:])
        for tc in tools:
            a = tc["agent"]
            if a["agent.tool.shell_http"]:
                sh["calls"] += 1
                sh["failed"] += a["agent.tool.failed"]
                sh["hidden"] += a["agent.tool.hidden_failure"]
                sh["query"] += bool(QUERY_PATH.search(a["agent.tool.target"]))
    bypass = {m: SS[m]["with-mcp"]["runs_with"]["agent.tool.shell_http_calls"] for m, _ in MODELS}
    cd_failed_calls = PS["with-mcp"]["by_tool"][CREATE_DASHBOARD]["failures"]
    if len(cd_first_classes) == 1:
        (only,) = cd_first_classes
        cd_class_text = f"Each was rejected for {dict(ERROR_CLASSES).get(only, only).lower()}"
    else:
        cd_class_text = "Error classes: " + ", ".join(
            f"{dict(ERROR_CLASSES).get(k, k).lower()} {v}"
            for k, v in sorted(cd_first_classes.items())
        )

    ren_rows = []
    for m, label in MODELS:
        re_ = S[m]["with-mcp"]["rule_enablement"]
        ren_rows.append(
            f'<tr><th scope="row">{label}</th>'
            f'<td class="n">{re_.get("enabled_true_on_create", 0)}</td>'
            f'<td class="n">{re_.get("enable_alert_rule_call", 0)}</td>'
            f'<td class="n">{re_.get("left_disabled", 0)}</td></tr>'
        )
    failed_no = [
        (label, S[m]["no-mcp"]["n"] - S[m]["no-mcp"]["pass_strict"]) for m, label in MODELS
    ]
    failed_with = [
        (label, S[m]["with-mcp"]["n"] - S[m]["with-mcp"]["pass_strict"]) for m, label in MODELS
    ]
    denials = (
        PS["no-mcp"]["runs_with"]["agent.permission.denials"]
        + PS["with-mcp"]["runs_with"]["agent.permission.denials"]
    )

    def who(pairs: list[tuple[str, int]]) -> str:
        hit = [f"{n} {label} run{'s' if n != 1 else ''}" for label, n in pairs if n]
        return ", ".join(hit) if hit else "none"

    # ---- safety
    saf_rows = []
    for m, label in MODELS:
        cells = ""
        for a in ARMS:
            rw = SS[m][a]["runs_with"]
            cells += (
                f'<td class="n">{rw["agent.tool.env_enumeration_calls"]}</td>'
                f'<td class="n">{rw["agent.secret.known_exposed"]}</td>'
            )
        saf_rows.append(f'<tr><th scope="row">{label}</th>{cells}</tr>')
    env_runs = {a: PS[a]["runs_with"]["agent.tool.env_enumeration_calls"] for a in ARMS}
    loc_result = tot["no-mcp"]["agent.secret.location_tool_result"]
    loc_args = (
        tot["no-mcp"]["agent.secret.location_tool_arguments"]
        + tot["no-mcp"]["agent.secret.location_model_text"]
    )
    generic = {a: PS[a]["runs_with"]["agent.secret.exposed"] for a in ARMS}
    exposed_runs = [r for r in runs if r["agent"]["agent.secret.known_exposed"]]
    exposed_after_env = sum(
        1 for r in exposed_runs if r["agent"]["agent.tool.env_enumeration_calls"]
    )
    if exposed_runs and exposed_after_env == len(exposed_runs) and not loc_args:
        exposure_how = (
            "Each time, the model had dumped the shell environment and the key came back in "
            "the command's output; it never appeared in the model's own arguments or text."
        )
    else:
        exposure_how = (
            f"In {exposed_after_env} of those {len(exposed_runs)} runs the model had dumped the "
            f"shell environment. Tool results carrying the key: {loc_result}; model arguments "
            f"or text carrying it: {loc_args}."
        )
    env_all = [r for r in runs if r["agent"]["agent.tool.env_enumeration_calls"]]
    env_safe = sum(1 for r in env_all if not r["agent"]["agent.secret.known_exposed"])

    # ---- snapshots
    snap_dir = ROOT / "snapshots"
    img_dir = out_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    snaps = {}
    for arm in ARMS:
        found = sorted(snap_dir.glob(f"*-{EXAMPLE_MODEL}-{arm}-*-dashboard.png"))
        if not found:
            raise SystemExit(
                f"no {EXAMPLE_MODEL} {arm} dashboard snapshot in {snap_dir}; "
                "add one (see its README.md) or change EXAMPLE_MODEL"
            )
        dash = found[0]
        rid = dash.name[: -len("-dashboard.png")]
        dst = f"{EXAMPLE_MODEL.split('-')[1]}-{ARM_SHORT[arm]}-dashboard.png"
        shutil.copyfile(dash, img_dir / dst)
        panels = records[rid]["dashboard_panels"]
        counts = Counter(records[r["run_id"]]["dashboard_panels"] for r in cell(EXAMPLE_MODEL, arm))
        snaps[arm] = {
            "rid": rid,
            "img": dst,
            "panels": panels,
            "same": counts[panels],
            "n": sum(counts.values()),
            "rule": snap_dir / f"{rid}-rule.png",
        }
    rule_img = f"{EXAMPLE_MODEL.split('-')[1]}-with-rule.png"
    shutil.copyfile(snaps["with-mcp"]["rule"], img_dir / rule_img)

    # ---- the page
    tile = '<div class="fact"><b>{}</b><span>{}</span></div>'
    cost_lo, cost_hi = min(cost_r.values()), max(cost_r.values())
    tiles = "".join(
        [
            tile.format(
                f"{cost_lo:.2f}–{cost_hi:.2f}×",
                "cost per run with the server, as a share of without (median, by model)",
            ),
            tile.format(f"{calls_pooled['ratio']:.2f}×", "model calls per run, all models pooled"),
            tile.format(
                f"{passed['with-mcp']} · {passed['no-mcp']}",
                f"of {n_arm['with-mcp']} runs passed the check, with · without",
            ),
            tile.format(
                f"{known['with-mcp']} · {known['no-mcp']}",
                f"of {n_arm['with-mcp']} runs where the Kibana API key reached the model, "
                "with · without",
            ),
        ]
    )
    stamp = (
        f"<code>{e(prov['path'])}</code> at commit <code>{e(prov['commit'])}</code>"
        f"{' (with uncommitted changes)' if prov['dirty'] else ''}, SHA-256 "
        f"<code>{e(prov['sha256'])}</code>"
    )
    page = f"""<title>mcp-for-kibana Efficiency Study</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,600&family=Public+Sans:wght@400;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* Layout: one reading column; tables, charts and the run timeline scroll inside their own boxes. */
:root{{
  --paper:#F6F7F5; --surface:#FCFCFB; --ink:#18201D; --muted:#56625C; --rule:#D8DED9; --band:#ECF0ED;
  --with:#2a78d6; --without:#eb6834; --fail:#d03b3a;
  --s0:#6250d6; --s1:#1baf7a; --s2:#eda100; --s3:#9aa29d;
  --display:"Newsreader","Iowan Old Style",Georgia,serif;
  --body:"Public Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{
  --paper:#131816; --surface:#1A1F1D; --ink:#E7ECE9; --muted:#9DA9A3; --rule:#303A36; --band:#202826;
  --with:#3987e5; --without:#d95926; --fail:#e66767;
  --s0:#9085e9; --s1:#199e70; --s2:#c98500; --s3:#69726e; color-scheme:dark }}}}
:root[data-theme="dark"]{{
  --paper:#131816; --surface:#1A1F1D; --ink:#E7ECE9; --muted:#9DA9A3; --rule:#303A36; --band:#202826;
  --with:#3987e5; --without:#d95926; --fail:#e66767;
  --s0:#9085e9; --s1:#199e70; --s2:#c98500; --s3:#69726e; color-scheme:dark }}
*{{box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);font:16px/1.6 var(--body)}}
main{{max-width:880px;margin:0 auto;padding-inline:18px;padding-block:36px 80px;display:grid;gap:6px}}
main>*{{min-width:0}}
h1,h2,h3,h4{{font-family:var(--display);font-weight:600;line-height:1.15;text-wrap:balance;margin:0}}
h1{{font-size:clamp(1.9rem,5vw,2.8rem)}}
h2{{font-size:1.6rem;margin-top:40px;padding-top:16px;border-top:1px solid var(--rule)}}
h3{{font-size:1.2rem;margin-top:20px}}
h4{{font-size:1.02rem;margin-bottom:6px}}
p,li{{max-width:68ch}}
p{{margin:6px 0}}
.eyebrow{{font:600 .76rem/1.4 var(--body);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}}
.draft{{display:inline-block;font:600 .74rem/1 var(--body);letter-spacing:.06em;text-transform:uppercase;color:var(--fail);border:1.5px solid var(--fail);border-radius:4px;padding:5px 8px}}
.lede{{font-size:1.14rem;line-height:1.55}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr));gap:10px;margin:14px 0 4px}}
.fact{{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:12px 14px}}
.fact b{{display:block;font:500 1.6rem/1.15 var(--mono);font-variant-numeric:tabular-nums}}
.fact span{{color:var(--muted);font-size:.88rem;line-height:1.4;display:block;margin-top:4px}}
.key{{display:inline-block;width:.8em;height:.8em;border-radius:2px;vertical-align:-.05em;margin-right:.3em}}
.key.w{{background:var(--with)}} .key.o{{background:var(--without)}}
.scroll{{overflow-x:auto;margin:12px 0;-webkit-overflow-scrolling:touch}}
table{{border-collapse:collapse;width:100%;font-size:.93rem;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 9px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}}
thead th{{font-size:.76rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:600}}
thead tr:last-child th{{border-bottom:2px solid var(--ink)}}
th[colspan]{{text-align:center}}
td.n,th.n{{text-align:right;white-space:nowrap}} td.n{{font-family:var(--mono);font-size:.86rem}}
tbody th{{font-weight:600;white-space:nowrap}}
th.ind{{font-weight:400;padding-left:22px}}
tr.sub th{{text-align:left;font-size:.76rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);padding-top:14px}}
table.results{{min-width:640px}}
table.compact{{max-width:620px}}
.arm-w{{box-shadow:inset 0 -3px 0 var(--with)}} .arm-o{{box-shadow:inset 0 -3px 0 var(--without)}}
.chart{{width:100%;min-width:600px;height:auto;display:block}}
.chart text{{fill:var(--ink);font-family:var(--body);font-size:13px}}
.chart .grid{{stroke:var(--rule);stroke-width:1}} .chart .one{{stroke:var(--ink);stroke-width:1.5}}
.chart .tick{{font:12px var(--mono);fill:var(--muted)}} .chart .axis{{font-size:12px;fill:var(--muted)}}
.chart .group{{font-weight:700}} .chart .mlabel{{fill:var(--muted)}}
.chart .val{{font:12px var(--mono)}} .chart .ci{{fill:var(--muted)}}
.forest .whisk{{stroke:var(--ink);stroke-width:2;stroke-linecap:round}}
.forest .dot{{fill:var(--ink);stroke:var(--surface);stroke-width:2}}
.timeline .bar.with-mcp{{fill:var(--with)}} .timeline .bar.no-mcp{{fill:var(--without)}}
.timeline .inbar{{fill:#fff;font:600 11px var(--mono)}}
.timeline .tool{{fill:var(--ink)}} .timeline .lane{{stroke:var(--rule);stroke-width:1}}
.timeline .endline{{stroke:var(--ink);stroke-width:1.5}}
.timeline .fail{{stroke:var(--fail);stroke-width:2;stroke-linecap:round;fill:none}}
.split .s0{{fill:var(--s0)}} .split .s1{{fill:var(--s1)}} .split .s2{{fill:var(--s2)}} .split .s3{{fill:var(--s3)}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:.88rem;color:var(--muted);margin:4px 0}}
.legend i{{display:inline-block;vertical-align:-.1em;margin-right:6px}}
.legend .lb{{width:18px;height:12px;border-radius:2px}} .legend .lb.w{{background:var(--with)}} .legend .lb.o{{background:var(--without)}}
.legend .lt{{width:3px;height:12px;background:var(--ink)}}
.legend .ls{{width:12px;height:12px;border-radius:2px}} .legend .ls.s0{{background:var(--s0)}} .legend .ls.s1{{background:var(--s1)}} .legend .ls.s2{{background:var(--s2)}} .legend .ls.s3{{background:var(--s3)}}
.narrow{{display:none;margin:12px 0}} .narrow .chart{{min-width:0}}
@media (max-width:600px){{.wide{{display:none}} .narrow{{display:block}}}}
.legend .lx{{color:var(--fail);font-style:normal;font-weight:700}}
.steps-wrap{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:18px;margin:8px 0}}
.steps{{min-width:0}}
.steps h4{{padding-left:10px}}
.steps.with-mcp h4{{border-left:4px solid var(--with)}} .steps.no-mcp h4{{border-left:4px solid var(--without)}}
.steps ol{{margin:0;padding-left:28px;display:grid;gap:3px;font-size:.88rem}}
.steps li{{line-height:1.45}}
.steps .t{{display:inline-block;min-width:3.2em;color:var(--muted);font:.8rem var(--mono)}}
.failtag{{color:var(--fail);font-size:.82rem;font-weight:600;white-space:nowrap}}
.arms{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr));gap:14px;margin:12px 0}}
.arm{{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:12px 14px;min-width:0}}
.arm h3{{margin:0 0 10px;font-size:1.08rem;padding-left:10px}}
.arm.w h3{{border-left:4px solid var(--with)}} .arm.o h3{{border-left:4px solid var(--without)}}
.flow{{display:grid;grid-template-columns:1fr auto 1.35fr auto 1fr;align-items:center;gap:4px}}
.node{{border:1.5px solid var(--ink);border-radius:6px;padding:7px 6px;text-align:center;font-weight:600;font-size:.9rem;line-height:1.25;background:var(--paper)}}
.arm.w .node.mid{{border-color:var(--with);border-width:2.5px}} .arm.o .node.mid{{border-color:var(--without);border-width:2.5px}}
.node small{{display:block;font-weight:400;font-size:.76rem;color:var(--muted);margin-top:3px}}
.edge{{font:.7rem/1.1 var(--mono);color:var(--muted);text-align:center;min-width:34px}}
.edge::after{{content:"⟷";display:block;font:1.1rem/1 var(--body);color:var(--ink)}}
.arm p{{font-size:.86rem;color:var(--muted);margin:10px 0 0}}
figure{{margin:14px 0;display:grid;gap:8px}}
figure img{{width:100%;max-width:100%;height:auto;border:1px solid var(--rule);border-radius:6px;background:var(--surface)}}
figcaption{{font-size:.88rem;color:var(--muted);max-width:70ch}}
.pair{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:16px}}
.pair figure{{margin:0}}
code{{font:.86em var(--mono);background:var(--band);padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}}
ul{{padding-left:20px;display:grid;gap:6px}}
.note{{font-size:.88rem;color:var(--muted)}}
footer{{margin-top:36px;color:var(--muted);font-size:.86rem}}
a{{color:var(--with)}} a:focus-visible{{outline:2px solid var(--with);outline-offset:2px}}
</style>
<main>
<p><span class="draft">Draft · private until published</span></p>
<p class="eyebrow">mcp-for-kibana · efficiency study · Kibana {e(", ".join(kib))} · {n_runs} runs</p>
<h1>Does mcp-for-kibana make Claude's Kibana work cheaper?</h1>
<p class="lede">Yes, for each of the three Claude models tested. On one end-to-end Kibana task, a run <b>with the server</b> cost {pct(1 - cost_hi)}–{pct(1 - cost_lo)} less at list price than a run <b>without it</b> (median, per model) and finished sooner. The saving comes from making fewer model calls, not from cheaper calls. Both arms nearly always completed the task, and the Kibana API key reached the model only in runs without the server.</p>
<div class="facts">{tiles}</div>

<h2>What was compared</h2>
<p>Each run gave Claude the same prompt: explore the data view of Kibana's web-logs sample data, build a dashboard of at least three panels, and create an alert rule that checks every minute and fires when more than 10 requests with a 5xx response arrive within 5 minutes. The prompt named the environment variables holding Kibana's URL and an API key, and pointed to Kibana's OpenAPI reference in the working directory.</p>
<div class="arms">
  <div class="arm w"><h3>With the server</h3>
    <div class="flow"><span class="node">Claude</span><span class="edge">MCP</span><span class="node mid">mcp-for-kibana<small>{e(", ".join(map(str, n_tools)))} tools · its own copy of the key</small></span><span class="edge">HTTP</span><span class="node">Kibana {e(", ".join(kib))}</span></div>
    <p>Toolboxes {e("; ".join(t.replace(",", ", ") for t in toolboxes))} at the default write tier. The shell, curl and file tools of the other arm were also available.</p></div>
  <div class="arm o"><h3>Without the server</h3>
    <div class="flow"><span class="node">Claude</span><span class="edge">tool call</span><span class="node mid">Shell + curl<small>OpenAPI reference · key in the environment</small></span><span class="edge">HTTP</span><span class="node">Kibana HTTP API</span></div>
    <p>Built-in shell and file tools only. The model reads the API reference and writes the requests itself.</p></div>
</div>
<p>Three models (Haiku 5.5, Sonnet 5.5 and Opus 5.5) ran in both arms, {n_cell} runs per model and arm: {n_runs} runs in {len(blocks)} blocks of {e(", ".join(map(str, sorted(set(per_block.values())))))}, each block running every model × arm once, with the arm order alternating between blocks. All runs used Claude Code {e(", ".join(cli))}. Kibana was reset after every run. A run passed when Kibana held a dashboard with at least three panels on the web-logs data and an enabled alert rule on that data. The check does not grade the rule's query or the dashboard's design.</p>

<h2>Results by model</h2>
<p>Medians over {n_cell} runs per cell. The server arm used less of everything for every model.</p>
{results_table}
<p>The chart divides each median with the server by the median without it. The bars are 95% bootstrap intervals; every interval lies below 1.</p>
{forest1}
<p class="note">Sonnet's time interval is wide because {timeout_by_model["claude-sonnet-5-5"]} of its runs without the server had a shell command hang until the time limit (see <a href="#time">where the time goes</a>). Counting only passing runs changes no ratio in this chart by more than {sens:.2f}.</p>

<h2>Why it is cheaper</h2>
<h3>Fewer model calls, not cheaper calls</h3>
<p>Every model call re-sends the conversation so far, so cost is roughly the number of calls times the tokens in each. With the server, runs made {calls_pooled["ratio"]:.2f}× as many model calls (CI {calls_pooled["ratio_ci95"][0]:.2f}–{calls_pooled["ratio_ci95"][1]:.2f}). Input tokens per call did not fall overall: pooled over all models they were {tpc_pooled["ratio"]:.2f}× (CI {tpc_pooled["ratio_ci95"][0]:.2f}–{tpc_pooled["ratio_ci95"][1]:.2f}). By model they were {hai_tpc["ratio"]:.2f}× for Haiku (CI {hai_tpc["ratio_ci95"][0]:.2f}–{hai_tpc["ratio_ci95"][1]:.2f}), {opu_tpc["ratio"]:.2f}× for Opus (CI {opu_tpc["ratio_ci95"][0]:.2f}–{opu_tpc["ratio_ci95"][1]:.2f}) and {son_tpc["ratio"]:.2f}× for Sonnet (CI {son_tpc["ratio_ci95"][0]:.2f}–{son_tpc["ratio_ci95"][1]:.2f}).</p>
{forest2}
<p>Two effects pull in opposite directions. The server's answers are short: tool output fed back to the model was {trb["ratio"]:.3f}× as large (median {trb["median_with"] / 1000:.1f} KB against {trb["median_without"] / 1000:.1f} KB per run). But the server's {e(", ".join(map(str, n_tools)))} tool definitions travel with every call, which is the likely reason a Sonnet call carries more input with the server than without it.</p>

<h3>A typical run of each arm</h3>
<p>The {ex_label} runs closest to their cell's median number of model calls, tool calls and duration, drawn on one time scale. Each bar is a model call; the gaps between bars are tool calls and harness overhead.</p>
<div class="legend"><span><i class="lb w"></i>model call, with the server</span><span><i class="lb o"></i>model call, without</span><span><i class="lt"></i>tool call</span><span><i class="lx">✕</i>failed tool call</span></div>
<div class="scroll">{tl_svg}</div>
<div class="steps-wrap">{tl_lists}</div>
<p class="note">Runs <code>{e(loaded[0][1]["run_id"])}</code> and <code>{e(loaded[1][1]["run_id"])}</code>. The first bar includes Claude Code's start-up.</p>

<h3 id="time">Where the time goes</h3>
<p>Wall time is mostly model time: the median share is {pct(share["with-mcp"])} with the server and {pct(share["no-mcp"])} without. Fewer model calls is therefore also what makes runs faster. Model time alone fell to {son_api["ratio"]:.2f}× for Sonnet (CI {son_api["ratio_ci95"][0]:.2f}–{son_api["ratio_ci95"][1]:.2f}), a tighter interval than its wall time ({son_wall["ratio_ci95"][0]:.2f}–{son_wall["ratio_ci95"][1]:.2f}). The chart shows means, so the parts add up.</p>
{split_svg}
<div class="scroll"><table class="compact"><thead><tr><th scope="col">Mean seconds per run</th><th class="n" scope="col">Model</th><th class="n" scope="col">Tool work</th><th class="n" scope="col">Sleeps, time-outs</th><th class="n" scope="col">Other</th><th class="n" scope="col">Wall</th></tr></thead><tbody>{"".join(split_table)}</tbody></table></div>
<p>Tool work itself is a few seconds per run in either arm. Without the server, {wait_runs} runs polled with deliberate sleeps (Haiku {wait_by_model["claude-haiku-5-5"]}, Sonnet {wait_by_model["claude-sonnet-5-5"]}, Opus {wait_by_model["claude-opus-5-5"]}) and {timeout_runs} runs had a shell call hit the harness's time limit (Haiku {timeout_by_model["claude-haiku-5-5"]}, Sonnet {timeout_by_model["claude-sonnet-5-5"]}, Opus {timeout_by_model["claude-opus-5-5"]}), for example a command left waiting on input. The server costs time once: runs with it reached their first tool call {ftool["diff"] / 1000:.1f} s later (median), while the server started and listed its tools.</p>

<h2>Reliability</h2>
<h3>Task outcomes</h3>
<p>{passed["with-mcp"]} of {n_arm["with-mcp"]} runs passed with the server and {passed["no-mcp"]} of {n_arm["no-mcp"]} without. Failures with the server: {who(failed_with)}. The server creates alert rules disabled unless asked otherwise, and in those runs Sonnet saw the rule come back disabled and left it that way. Counting a disabled rule as done, the server arm passes {lenient["with-mcp"]} of {n_arm["with-mcp"]}. Failures without the server: {who(failed_no)}, which ended with no rule: a shell command hung, a later command was refused, and the model stopped.</p>
<div class="scroll"><table class="compact"><thead><tr><th scope="col">Rule enablement, with the server</th><th class="n" scope="col">Enabled on create</th><th class="n" scope="col">By a second call</th><th class="n" scope="col">Left disabled</th></tr></thead><tbody>{"".join(ren_rows)}</tbody></table></div>
<p>Enabling by a second call costs one more model call; that is how Sonnet usually did it.</p>

<h3>Tool calls that failed</h3>
<p>The harness flags a tool call as an error only when the tool says so. A shell command that prints an HTTP 404 still exits 0, so the analysis also reads each call's output for HTTP errors, time-outs and refusals. Counted that way, the server arm has the higher failure rate, for two reasons the table cannot show on its own.</p>
{rel_table}
<ul>
<li><b><code>create_dashboard</code> failed on its first call in {cd_first_fail} of {cd_runs} server runs.</b> {cd_class_text}; {cd_recovered} of those runs went on to create the dashboard with a corrected call. These first attempts and their retries account for {cd_failed_calls} of the {tot["with-mcp"]["agent.tool.failures"]} failed calls with the server. A tool that fails on every first attempt has a schema or description problem; the models are not at fault.</li>
<li><b>Models bypassed the server with curl.</b> {PS["with-mcp"]["runs_with"]["agent.tool.shell_http_calls"]} of {n_arm["with-mcp"]} server runs also called Kibana from the shell (Haiku {bypass["claude-haiku-5-5"]}, Sonnet {bypass["claude-sonnet-5-5"]}, Opus {bypass["claude-opus-5-5"]}, of {n_cell} each). {sh["query"]} of those {sh["calls"]} shell HTTP calls went to query endpoints, and the loaded toolboxes have no query tool. {sh["failed"]} of the {sh["calls"]} failed, {sh["hidden"]} of them without the harness flagging an error.</li>
</ul>

<h2>Safety</h2>
<p>The runs were scanned for the exact Kibana API key they used. It reached the model's context in {known["no-mcp"]} of {n_arm["no-mcp"]} runs without the server and {known["with-mcp"]} of {n_arm["with-mcp"]} with it. {exposure_how} A scan for generic credential patterns flagged {generic["no-mcp"] + generic["with-mcp"]} runs, the same ones.</p>
<div class="scroll"><table class="compact"><thead><tr><th rowspan="2" scope="col">Runs out of {n_cell}</th><th colspan="2" scope="colgroup" class="arm-w">With the server</th><th colspan="2" scope="colgroup" class="arm-o">Without</th></tr>
<tr><th class="n" scope="col">Env dumped</th><th class="n" scope="col">Key exposed</th><th class="n" scope="col">Env dumped</th><th class="n" scope="col">Key exposed</th></tr></thead><tbody>{"".join(saf_rows)}</tbody></table></div>
<p>Dumping the environment (<code>env</code>, <code>printenv</code> and similar) is the leading indicator: {env_runs["no-mcp"]} runs without the server and {env_runs["with-mcp"]} with it did so, and in {env_safe} of those {env_runs["no-mcp"] + env_runs["with-mcp"]} runs the key still did not reach the model. The server holds its own copy of the key in its configuration, so the model never needs to read it. In this study the key was also in the shell environment in both arms, because the prompt and the built-in tools were identical; the server arm's zero reflects that the model rarely needed the shell, not that the key was out of reach. Leaving the key out of the model's environment would make the separation structural.</p>

<h2>What the runs produced</h2>
<p>One passing {ex_label} run per arm, re-created from the objects the run stored (Kibana was reset after each run) and captured on Kibana {e(", ".join(kib))}. Panel values reflect the sample data at capture time.</p>
<div class="pair">
<figure><img src="img/{e(snaps["with-mcp"]["img"])}" alt="Web logs dashboard built by {ex_label} with the server, {snaps["with-mcp"]["panels"]} panels." loading="lazy"><figcaption><b>With the server.</b> {snaps["with-mcp"]["panels"]} panels, as in {snaps["with-mcp"]["same"]} of {snaps["with-mcp"]["n"]} runs in this cell.</figcaption></figure>
<figure><img src="img/{e(snaps["no-mcp"]["img"])}" alt="Web logs dashboard built by {ex_label} without the server, {snaps["no-mcp"]["panels"]} panels." loading="lazy"><figcaption><b>Without the server.</b> {snaps["no-mcp"]["panels"]} panels, as in {snaps["no-mcp"]["same"]} of {snaps["no-mcp"]["n"]} runs in this cell.</figcaption></figure>
</div>
<figure><img src="img/{e(rule_img)}" alt="Alert rule details page in Kibana for the rule from the same with-server run." loading="lazy"><figcaption>The alert rule from the same with-server run. It was re-created disabled for the snapshot so it would never fire; the original was enabled.</figcaption></figure>

<h2>Method and caveats</h2>
<ul>
<li><b>Controls.</b> Same prompt, data, success check, Kibana version and built-in tools in both arms; only the server differed.</li>
<li><b>Measures.</b> Every run's numbers are re-derived from its transcript up to the model's own end of turn: list-price cost reported by Claude Code, tokens including cache reads and writes, model calls, tool calls, durations, and the per-call signals defined in <code>scripts/experiment/SIGNALS.md</code>. Comparisons are ratios of medians with percentile bootstrap intervals ({M["bootstrap_resamples"]:,} resamples, seed {M["bootstrap_seed"]}).</li>
<li><b>A logging fault, corrected.</b> In {sum(S[m]["no-mcp"]["runs_with_multiple_result_events"] for m, _ in MODELS)} runs without the server, a hung shell command was killed after the model had finished and Claude Code logged a second, late result. The run records kept the last result, so their turns, time and tokens were wrong; the transcripts are complete, and every number here comes from them. No run with the server was affected ({sum(S[m]["with-mcp"]["runs_with_multiple_result_events"] for m, _ in MODELS)}).</li>
<li><b>Refused commands.</b> {denials} run had a command refused by the permission system, which the run record did not show and the transcript does.</li>
<li><b>Timing.</b> Model-call bars are measured from the transcript's timestamps and are approximate; per-call output tokens are not in the transcript.</li>
<li><b>Scope.</b> One task, one dataset, one Kibana version, Claude models only. Cost is list price and leaves out the cost of running the server.</li>
</ul>

<footer>
<p>Generated by <code>scripts/experiment/efficiency_report.py</code> from {stamp}, and the run records and snapshots under <code>scripts/experiment/runs/efficiency/full/</code>.</p>
</footer>
</main>
"""
    (out_dir / "index.html").write_text(page)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--analysis", type=Path, default=ROOT / "analysis.json")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    build(args.analysis.resolve(), args.out)
    print(f"wrote {args.out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
