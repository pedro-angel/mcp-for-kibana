"""Generate the server-usability SVG charts from the run JSONL.

    uv run python scripts/experiment/charts.py

Dependency-free, deterministic (no timestamps): the same records produce the
same bytes, so the committed SVGs are diffable evidence like the tables.
Colors are the skill-validated categorical slots (light + dark steps); each
SVG carries an internal prefers-color-scheme block so it stays legible in
both docs themes. The adjacent tables are the accessible data view.
"""

import json
import statistics
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_RUNS = _HERE / "runs"
_OUT = _HERE.parents[1] / "docs" / "assets" / "server-usability"

# Validated categorical slots (light, dark) — arms keep fixed hue order.
_SLOTS = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70")]
_ARMS = ["with-mcp", "no-mcp", "with-mcp-directed"]

_STYLE = """
  <style>
    text { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
    .t1 { fill: #0b0b0b; } .t2 { fill: #52514e; }
    .grid { stroke: #d8d7d2; stroke-width: 1; }
    .track { fill: #e8e7e2; }
    .s0 { fill: %s; } .s1 { fill: %s; } .s2 { fill: %s; }
    @media (prefers-color-scheme: dark) {
      .t1 { fill: #ffffff; } .t2 { fill: #c3c2b7; }
      .grid { stroke: #3a3a38; }
      .track { fill: #2c2c2a; }
      .s0 { fill: %s; } .s1 { fill: %s; } .s2 { fill: %s; }
    }
  </style>
""" % (_SLOTS[0][0], _SLOTS[1][0], _SLOTS[2][0], _SLOTS[0][1], _SLOTS[1][1], _SLOTS[2][1])


def _load(name):
    return [json.loads(x) for x in (_RUNS / name).read_text().splitlines() if x.strip()]


def _hbar(x, y, w, h, cls, r=4):
    if w <= r:
        return f'<rect x="{x}" y="{y}" width="{max(w, 1)}" height="{h}" class="{cls}"/>'
    return (f'<path d="M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} '
            f'V{y + h - r} Q{x + w},{y + h} {x + w - r},{y + h} H{x} Z" class="{cls}"/>')


def _vbar(x, y_base, h, w, cls, r=4):
    y = y_base - h
    if h <= r:
        return f'<rect x="{x}" y="{y}" width="{w}" height="{max(h, 1)}" class="{cls}"/>'
    return (f'<path d="M{x},{y_base} V{y + r} Q{x},{y} {x + r},{y} H{x + w - r} '
            f'Q{x + w},{y} {x + w},{y + r} V{y_base} Z" class="{cls}"/>')


def study_l():
    runs = [r for r in _load("study-l.jsonl") if r.get("study") == "L"]
    loads = {r["model"]: r["ts"] for r in _load("study-l.jsonl") if r.get("study") == "L-load"}
    per = {}
    for r in runs:
        if r["model"] in loads and r["ts_start"] >= loads[r["model"]]:
            per.setdefault(r["model"], []).append(bool(r.get("passed")))
    rows = sorted(per.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
    lab_w, unit, bar_h, gap, top = 250, 66, 20, 12, 56
    width = lab_w + 5 * unit + 70
    height = top + len(rows) * (bar_h + gap) + 16
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'font-size="13" role="img" aria-label="Passed runs out of five per local model">',
             _STYLE,
             '<text x="16" y="24" font-size="15" font-weight="600" class="t1">Study L — passed runs out of 5</text>',
             '<text x="16" y="42" class="t2">space task through mcp-for-kibana, one bar per model</text>']
    for i, (model, results) in enumerate(rows):
        y = top + i * (bar_h + gap)
        k = sum(results)
        parts.append(f'<text x="{lab_w - 10}" y="{y + bar_h - 6}" text-anchor="end" class="t2" '
                     f'font-family="ui-monospace, Menlo, monospace" font-size="12">{model.split("/")[-1]}</text>')
        parts.append(f'<rect x="{lab_w}" y="{y}" width="{5 * unit}" height="{bar_h}" rx="4" class="track"/>')
        if k:
            parts.append(_hbar(lab_w, y, k * unit, bar_h, "s0"))
        parts.append(f'<text x="{lab_w + 5 * unit + 10}" y="{y + bar_h - 6}" class="t1" '
                     f'font-weight="600" font-size="12">{k}/5</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _cells():
    runs = [r for r in _load("study-c.jsonl") if str(r.get("block", "")).startswith("b")]
    cells = {}
    for r in runs:
        cells.setdefault((r["model"], r["arm"]), []).append(r)
    return cells


def _grouped(title, subtitle, values, fmt, y_max, y_ticks, aria):
    """3 model groups x 3 arm bars; values[(model, arm)] -> number."""
    models = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"]
    short = {"claude-haiku-4-5": "Haiku 4.5", "claude-sonnet-5": "Sonnet 5", "claude-opus-5": "Opus 5"}
    bar_w, in_gap, grp_gap, left, top, plot_h = 40, 2, 42, 64, 92, 190
    grp_w = 3 * bar_w + 2 * in_gap
    width = left + 3 * grp_w + 2 * grp_gap + 28
    height = top + plot_h + 40
    base = top + plot_h
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'font-size="13" role="img" aria-label="{aria}">', _STYLE,
             f'<text x="16" y="24" font-size="15" font-weight="600" class="t1">{title}</text>',
             f'<text x="16" y="42" class="t2">{subtitle}</text>']
    lx = 16
    for i, arm in enumerate(_ARMS):  # legend: fixed arm order = fixed hues
        parts.append(f'<rect x="{lx}" y="56" width="11" height="11" rx="3" class="s{i}"/>')
        parts.append(f'<text x="{lx + 16}" y="66" class="t2" font-size="12">{arm}</text>')
        lx += 16 + 8.2 * len(arm) + 22
    for t in y_ticks:
        y = base - plot_h * t / y_max
        parts.append(f'<line x1="{left}" y1="{y}" x2="{width - 20}" y2="{y}" class="grid"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4}" text-anchor="end" class="t2" font-size="11">{fmt(t)}</text>')
    for mi, model in enumerate(models):
        gx = left + mi * (grp_w + grp_gap)
        for ai, arm in enumerate(_ARMS):
            v = values[(model, arm)]
            h = plot_h * v / y_max
            x = gx + ai * (bar_w + in_gap)
            parts.append(_vbar(x, base, h, bar_w, f"s{ai}"))
            parts.append(f'<text x="{x + bar_w / 2}" y="{base - h - 5}" text-anchor="middle" '
                         f'class="t1" font-size="11" font-weight="600">{fmt(v)}</text>')
        parts.append(f'<text x="{gx + grp_w / 2}" y="{base + 20}" text-anchor="middle" class="t2">{short[model]}</text>')
    parts.append(f'<line x1="{left}" y1="{base}" x2="{width - 20}" y2="{base}" class="grid"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / "study-l-passes.svg").write_text(study_l() + "\n")
    cells = _cells()
    passes = {k: sum(1 for r in v if r.get("passed")) for k, v in cells.items()}
    (_OUT / "study-c-passes.svg").write_text(_grouped(
        "Study C — passed runs out of 5", "per model and arm",
        passes, lambda v: str(int(v)), 5, [0, 1, 2, 3, 4, 5],
        "Passed runs out of five per Claude model and arm") + "\n")
    cost = {k: statistics.median([r["total_cost_usd"] for r in v if isinstance(r.get("total_cost_usd"), (int, float))])
            for k, v in cells.items()}
    (_OUT / "study-c-cost.svg").write_text(_grouped(
        "Study C — median cost per run (USD)", "per model and arm, list price",
        cost, lambda v: f"${v:.2f}", 1.5, [0, 0.5, 1.0, 1.5],
        "Median cost per run in dollars per Claude model and arm") + "\n")
    print(f"wrote 3 SVGs to {_OUT}")


if __name__ == "__main__":
    main()
