"""live_crosscheck.py: query specs and the transcript facts it explains differences with."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "experiment"))

import live_crosscheck as lc  # noqa: E402


def test_specs_cover_the_study_blocks_only():
    q = lc.specs(("2026-10-11T23:00:00Z", "2026-10-12T12:00:00Z"))
    assert {"q01_roots", "q03_api_request", "q06_interaction", "q08_hook_tool"} <= set(q)
    for spec in q.values():
        assert spec["breakdowns"][:2] == ["study.model", "study.arm"]
        assert {"column": "study.block", "op": "starts-with", "value": "b"} in spec["filters"]
        named = [c for c in spec["calculations"] if c.get("filters")]
        assert all(c.get("name") for c in named)


def test_hc_pct_is_honeycombs_rank_rule():
    assert lc.hc_pct([5, 1, 3, 2], 0.5) == 3
    assert lc.hc_pct([4, 1, 2], 0.5) == 2
    assert lc.hc_pct([], 0.5) is None


def test_transcript_facts_counts_blocked_calls_before_the_first_result(tmp_path):
    def tr(i, content, err=True):
        c = {"type": "tool_result", "tool_use_id": f"t{i}", "content": content, "is_error": err}
        return {"type": "user", "message": {"content": [c]}}

    lines = [
        tr(1, "<tool_use_error>Blocked: sleep 90 followed by: curl"),
        tr(2, "HTTP 404", err=False),
        {"type": "result"},
        tr(3, "<tool_use_error>Blocked: later"),
    ]
    (tmp_path / "transcript.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    assert lc.transcript_facts(tmp_path) == {"blocked_tool_calls": 1}
