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


def test_a_block_committed_with_its_records_is_not_called_pre_registered():
    text = report.render([_run("r1")], "counted-2026-10-27-toolboxes")
    assert "**Not pre-registered**" in text and "the pre-registration is" not in text
    assert "the pre-registration is `scripts/experiment/CLAUDE_E2E.md`" in report.render([_run("r1")], "b")


def test_the_toolboxes_summary_is_the_generator_output():
    block = _ROOT / "scripts" / "experiment" / "runs" / "claude-e2e" / "counted-2026-10-27-toolboxes"
    assert (block / "SUMMARY.md").read_text() == report.render(report.load(block), block.name)
