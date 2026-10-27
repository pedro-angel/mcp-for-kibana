"""The live-model summary names a run whose cleanup raised (CLAUDE_E2E.md),
and the live test reports a key leak ahead of a cleanup failure."""

import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "claude_e2e_report", _ROOT / "scripts" / "experiment" / "claude_e2e_report.py"
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _run(run_id, **extra):
    return {"run_id": run_id, "task": "delete_one_dashboard", "valid": True, "passed": True, **extra}


def test_a_passed_run_whose_cleanup_raised_is_listed():
    text = report.render([_run("r1", cleanup_error="RuntimeError('gone')")], "b")
    assert "## Cleanup failures" in text
    assert "- `r1` (delete_one_dashboard): RuntimeError('gone')" in text


def test_no_cleanup_section_without_a_cleanup_failure():
    assert "Cleanup failures" not in report.render([_run("r1")], "b")


def test_the_key_leak_is_asserted_before_the_cleanup_failure():
    source = (_ROOT / "tests" / "e2e" / "test_claude_live.py").read_text()
    leak = re.search(r'assert record\.get\("key_in_transcript"\)', source)
    cleanup = re.search(r'assert not record\.get\("cleanup_error"\)', source)
    assert leak and cleanup and leak.start() < cleanup.start()
