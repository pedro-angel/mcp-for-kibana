"""The read-tool sweep covers every read tool (D60).

tests/contract/test_read_tool_sweep.py calls each read-tier tool once against
the seeded stack with the arguments in tests/_read_sweep.py. Checked here as
well, without a stack, so a read tool added without a sweep row fails
unit_green first.
"""

from tests._read_sweep import NON_EMPTY, SWEEP_ROWS, coverage_problem
from tests._registry import registry


def _read_tools():
    return {name for name, t in registry().items() if t.tier == "read"}


def test_sweep_rows_are_exactly_the_read_tools():
    problem = coverage_problem(_read_tools())
    assert problem is None, problem


def test_a_missing_or_stale_row_names_the_fix():
    tools = _read_tools()
    missing = coverage_problem(tools | {"get_new_thing"})
    assert "['get_new_thing'] have no sweep row: add one to SWEEP_ROWS" in missing
    stale = coverage_problem(tools - {"get_case"})
    assert "['get_case'] name no read tool: remove them from SWEEP_ROWS" in stale


def test_rows_build_arguments_from_the_sweep_ids():
    ids = {key: f"<{key}>" for key in (
        "alert_rule", "connector", "case", "dashboard", "visualization", "data_view",
        "short_url_slug", "agent", "agent_policy", "enrollment_key", "output", "package",
        "package_policy", "apm_service", "synthetic_monitor", "role", "exception_list",
        "value_list", "detection_rule", "stream")}
    for tool, row in SWEEP_ROWS.items():
        assert isinstance(row(ids), dict), tool


def test_non_empty_names_only_swept_tools():
    assert sorted(NON_EMPTY - SWEEP_ROWS.keys()) == []
