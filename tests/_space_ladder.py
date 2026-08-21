"""Shared Study L/C scoring for the server-usability study: the frozen
progress ladder (pre-registered before any run; git history archives the
registration document).

Every run is scored to the first missing rung so failures carry information:

    S1 space created  -> S2 data view created -> S3 dashboard created -> S4 panel valid

plus the default-space contamination check (an object bearing the run marker
in the default space separates "wrong space" from "no dashboard").

Used by the live gates (tests/e2e/test_lmstudio_space.py, the Study C runner
in scripts/experiment/) and unit-tested against FakeGateway
(tests/unit/test_space_ladder.py) — a loader-parsed helper, never a test
module.
"""

import json
from pathlib import Path

from tests._dashboard_assertion import assert_model_built_dashboard

RUNGS = ("s1_space", "s2_data_view", "s3_dashboard", "s4_panel")

FLIGHTS_INDEX = "kibana_sample_data_flights"


def score_space_run(root_gateway, scoped_gateway, space_id, marker):
    """Score one run. `root_gateway` is unscoped (default space);
    `scoped_gateway` targets `space_id` and may be None when the space was
    never created (S1 already failed at connect time).

    Returns a dict with one bool per rung, `first_missing` (rung name or
    None), `default_space_contamination`, and `passed`.
    """
    rungs = dict.fromkeys(RUNGS, False)

    try:
        root_gateway.get_space(space_id)
        rungs["s1_space"] = True
    except Exception:
        pass

    if rungs["s1_space"] and scoped_gateway is not None:
        try:
            views = scoped_gateway.list_data_views()
            rungs["s2_data_view"] = any(
                v.index_pattern == FLIGHTS_INDEX for v in views
            )
        except Exception:
            pass
        try:
            rungs["s3_dashboard"] = bool(scoped_gateway.search_dashboards(marker))
        except Exception:
            pass
        if rungs["s3_dashboard"]:
            try:
                assert_model_built_dashboard(scoped_gateway, marker)
                rungs["s4_panel"] = True
            except Exception:
                # not just AssertionError: a hand-authored panel (no-mcp arm)
                # can lack the very keys the assertion indexes (measured:
                # KeyError 'type') — an unreadable shape is a failed rung,
                # never a scorer crash that loses the run record
                pass

    try:
        contamination = bool(root_gateway.search_dashboards(marker))
    except Exception:
        contamination = False

    first_missing = next((r for r in RUNGS if not rungs[r]), None)
    return {
        **rungs,
        "first_missing": first_missing,
        "default_space_contamination": contamination,
        "passed": first_missing is None,
    }


def default_space_data_view_ids(root_gateway):
    """Snapshot the default space's data-view ids before a run."""
    try:
        return {v.id for v in root_gateway.list_data_views()}
    except Exception:
        return set()


def sweep_new_default_space_data_views(root_gateway, before_ids):
    """Delete data views that APPEARED in the default space during a run —
    wrong-space residue (a model that drops the `space` parameter creates
    its data view in default; measured: seven strays broke get_data_view
    resolution for every later consumer). Diff-based, so the sweep binds
    exactly to what the run created; the sample-data view predates every
    run and is never touched."""
    try:
        for v in root_gateway.list_data_views():
            if v.id not in before_ids:
                root_gateway.delete_data_view(v.id)
    except Exception:
        pass  # cleanup best-effort; the run record is already written


def append_jsonl(path, record):
    """Append one run record; parents created; one JSON object per line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
