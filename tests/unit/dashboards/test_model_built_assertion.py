"""Unit coverage for the e2e success assertion (issue #23) — the flake fix's
tolerance logic, exercised against a FakeGateway so it is protected in CI even
though the e2e tier only runs where LM Studio lives."""

import pytest

from tests._dashboard_assertion import assert_model_built_dashboard
from tests.fakes import FakeGateway

MARKER = "E2E abc12345"


def _good_data(title):
    """A stored dashboard whose panel matches what the e2e prompt asks for."""
    return {
        "title": title,
        "panels": [
            {
                "type": "vis",
                "config": {
                    "type": "xy",
                    "layers": [
                        {
                            "y": [{"operation": "average", "field": "AvgTicketPrice"}],
                            "x": {"fields": ["Carrier"]},
                        }
                    ],
                },
            }
        ],
    }


def test_passes_for_one_valid_dashboard():
    gw = FakeGateway()
    gw.dashboards["d1"] = _good_data(f"{MARKER} flight prices")
    assert_model_built_dashboard(gw, MARKER)  # no raise


def test_raises_when_no_dashboard():
    gw = FakeGateway()
    with pytest.raises(AssertionError, match="no '.*' dashboard"):
        assert_model_built_dashboard(gw, MARKER)


def test_tolerates_duplicates_and_selects_a_paneled_target():
    # The model created two: an empty one FIRST (so dashboards[0] is empty) and a
    # valid one. The fix must warn (not fail) and pick the paneled target — the
    # old `dashboards[0]` behavior would IndexError on the empty panel list.
    gw = FakeGateway()
    gw.dashboards["empty"] = {"title": f"{MARKER} empty", "panels": []}
    gw.dashboards["good"] = _good_data(f"{MARKER} flight prices")
    with pytest.warns(UserWarning, match="created 2 dashboards"):
        assert_model_built_dashboard(gw, MARKER)


def test_still_fails_when_config_is_wrong():
    # Tolerating duplicates must not weaken the semantic check.
    gw = FakeGateway()
    bad = _good_data(f"{MARKER} flight prices")
    bad["panels"][0]["config"]["layers"][0]["x"]["fields"] = ["DestCountry"]  # wrong dimension
    gw.dashboards["d1"] = bad
    with pytest.raises(AssertionError):
        assert_model_built_dashboard(gw, MARKER)
