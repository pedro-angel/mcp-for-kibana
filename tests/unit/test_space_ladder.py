"""Unit coverage for the shared Study L/C ladder scorer (tests/_space_ladder.py).

The scorer runs inside the live gates where a broken rung would silently
mis-classify runs — so every rung's true/false path and the contamination
check are pinned here against FakeGateway.
"""

import json

from kibana_mcp.core.errors import KibanaNotFound
from tests._space_ladder import append_jsonl, score_space_run
from tests.fakes import FakeGateway

MARKER = "E2E cafe0123"


def _passing_pair():
    """A root/scoped FakeGateway pair representing a fully passing run."""
    root = FakeGateway()
    scoped = FakeGateway()  # its default data view IS the flights index
    scoped.dashboards["d1"] = {
        "title": f"{MARKER} flight prices",
        "description": "",
        "panels": [{
            "type": "vis",
            "grid": {"x": 0, "y": 0, "w": 48, "h": 12},
            "config": {
                "type": "xy",
                "layers": [{
                    "data_source": {"index_pattern": "kibana_sample_data_flights"},
                    "x": {"operation": "terms", "fields": ["Carrier"], "limit": 10},
                    "y": [{"operation": "average", "field": "AvgTicketPrice"}],
                }],
            },
        }],
    }
    root.spaces = getattr(root, "spaces", {})
    return root, scoped


def _with_space_present(root, space_id):
    # FakeGateway.get_space raises for unknown ids; create via its own API.
    root.create_space(space_id, space_id, None, None, None, None, None)


def test_full_pass(tmp_path):
    root, scoped = _passing_pair()
    _with_space_present(root, "e2e-x")
    out = score_space_run(root, scoped, "e2e-x", MARKER)
    assert out["passed"] and out["first_missing"] is None
    assert not out["default_space_contamination"]


def test_missing_space_scores_s1(tmp_path):
    root, scoped = _passing_pair()
    out = score_space_run(root, scoped, "never-created", MARKER)
    assert not out["passed"] and out["first_missing"] == "s1_space"


def test_missing_data_view_scores_s2():
    root, scoped = _passing_pair()
    _with_space_present(root, "e2e-x")
    scoped.data_views.clear()
    out = score_space_run(root, scoped, "e2e-x", MARKER)
    assert out["first_missing"] == "s2_data_view"
    # the dashboard rungs are independent of the data-view rung:
    assert out["s3_dashboard"]


def test_missing_dashboard_scores_s3():
    root, scoped = _passing_pair()
    _with_space_present(root, "e2e-x")
    scoped.dashboards.clear()
    out = score_space_run(root, scoped, "e2e-x", MARKER)
    assert out["first_missing"] == "s3_dashboard" and not out["s4_panel"]


def test_wrong_panel_scores_s4():
    root, scoped = _passing_pair()
    _with_space_present(root, "e2e-x")
    layer = scoped.dashboards["d1"]["panels"][0]["config"]["layers"][0]
    layer["y"][0]["field"] = "FlightDelayMin"  # not the required metric
    out = score_space_run(root, scoped, "e2e-x", MARKER)
    assert out["first_missing"] == "s4_panel"


def test_scoped_gateway_none_after_s1_scores_lower_rungs_false():
    root, _ = _passing_pair()
    _with_space_present(root, "e2e-x")
    out = score_space_run(root, None, "e2e-x", MARKER)
    assert out["s1_space"] and out["first_missing"] == "s2_data_view"


def test_default_space_contamination_detected():
    root, scoped = _passing_pair()
    _with_space_present(root, "e2e-x")
    root.dashboards["leak"] = {"title": f"{MARKER} leaked", "description": "", "panels": []}
    out = score_space_run(root, scoped, "e2e-x", MARKER)
    assert out["default_space_contamination"]


def test_append_jsonl_appends_one_object_per_line(tmp_path):
    path = tmp_path / "nested" / "runs.jsonl"
    append_jsonl(path, {"a": 1})
    append_jsonl(path, {"b": 2})
    lines = path.read_text().splitlines()
    assert [json.loads(ln) for ln in lines] == [{"a": 1}, {"b": 2}]


def test_root_gateway_error_does_not_crash_scoring():
    root, scoped = _passing_pair()
    _with_space_present(root, "e2e-x")

    class Exploding:
        def get_space(self, space_id):
            raise KibanaNotFound("gone")

        def search_dashboards(self, q):
            raise KibanaNotFound("gone")

    out = score_space_run(Exploding(), scoped, "e2e-x", MARKER)
    assert out["first_missing"] == "s1_space" and not out["default_space_contamination"]


def test_unreadable_panel_shape_scores_s4_not_crash():
    # Measured in Study C: a no-mcp agent's hand-authored panel can lack the
    # keys the assertion indexes (KeyError 'type') — must score, not raise.
    root, scoped = _passing_pair()
    _with_space_present(root, "e2e-x")
    scoped.dashboards["d1"]["panels"][0]["config"] = {"layers": []}  # no "type"
    out = score_space_run(root, scoped, "e2e-x", MARKER)
    assert out["first_missing"] == "s4_panel" and out["s3_dashboard"]
