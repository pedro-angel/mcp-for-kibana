"""Study 4's analysis (scripts/experiment/study4_analyze.py): cells, ratios,
the per-model geometric mean and the generated results page, on synthetic
records. No network."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "experiment"))

sa = importlib.import_module("study4_analyze")

COST = {"with-mcp": {"weblogs": 1.0, "lookup": 2.0}, "no-mcp": {"weblogs": 4.0, "lookup": 2.0}}


def _write(root: Path, valid: bool = True) -> None:
    i = 0
    for arm, tasks in COST.items():
        for task, cost in tasks.items():
            for b in range(1, 4):
                i += 1
                d = root / f"b{b:02d}" / f"r{i}"
                d.mkdir(parents=True)
                (d / "record.json").write_text(json.dumps({
                    "run_id": f"r{i}", "block": f"b{b:02d}", "model_requested": "claude-haiku-5-5",
                    "arm": arm, "task": task, "valid": valid or not (arm == "with-mcp" and b == 1), "success": True,
                    "total_cost_usd": cost * (1 + 0.01 * b), "wall_s": 10 * cost,
                    "git_sha": "abc", "tasks_sha": "t1", "claude_cli": "2.1.296 (Claude Code)",
                    "kibana_version": "9.5.5", "toolboxes": "dashboards" if arm == "with-mcp" else None,
                    "ts_start": f"2026-10-12T17:0{b}:00+00:00",
                }))


def test_ratios_and_geometric_mean(tmp_path):
    _write(tmp_path)
    a = sa.analyse(sa.load(tmp_path))
    assert (a["runs"], a["valid_runs"], a["passed"]) == (12, 12, 12)
    r = {(x["task"], x["metric"]): x for x in a["ratios"]}
    assert round(r[("weblogs", "cost_usd")]["ratio"], 6) == 0.25
    assert r[("lookup", "time_s")]["ratio"] == 1.0
    pm = a["per_model"]["claude-haiku-5-5"]
    assert round(pm["cost_usd"]["ratio"], 6) == 0.5  # sqrt(0.25 * 1.0)
    assert pm["cost_usd"]["tasks"] == 2
    lo, hi = pm["cost_usd"]["ratio_ci95"]
    assert lo <= pm["cost_usd"]["ratio"] <= hi


def test_invalid_runs_are_left_out(tmp_path):
    _write(tmp_path, valid=False)
    a = sa.analyse(sa.load(tmp_path))
    assert (a["runs"], a["valid_runs"]) == (12, 10)
    assert sum(c["n"] for c in a["cells"]) == 10


def test_analysis_is_deterministic(tmp_path):
    _write(tmp_path)
    rows = sa.load(tmp_path)
    assert json.dumps(sa.analyse(rows), sort_keys=True) == json.dumps(sa.analyse(rows), sort_keys=True)


def test_report_takes_every_number_from_the_analysis(tmp_path):
    _write(tmp_path)
    a = sa.analyse(sa.load(tmp_path))
    page = sa.report(a, None)
    assert "| Haiku 5.5 | 0.50 (" in page
    assert "12 of 12 valid runs passed" in page
    assert "Cross-check" not in page
    out = tmp_path / "page.md"
    assert sa.main(["--runs", str(tmp_path), "--report", str(out)]) == 0
    assert out.read_text().startswith("# Study 4 results")
