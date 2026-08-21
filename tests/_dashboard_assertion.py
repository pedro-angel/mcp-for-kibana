"""Shared assertion: 'the model built the expected flights dashboard'.

Used by the e2e test (tests/e2e/test_lmstudio.py) against live Kibana, and
unit-tested against a FakeGateway (tests/unit/dashboards/test_model_built_assertion.py)
so its tolerance logic (issue #23) stays protected even though the e2e tier only
runs where LM Studio lives. A loader-parsed helper module, never a test module.
"""

import warnings


def assert_model_built_dashboard(gateway, marker):
    """Assert the model created a valid 'average ticket price by carrier' flights
    dashboard tagged with ``marker``.

    Tolerant of the model creating MORE than one dashboard for the single request
    (small-model stochasticity — issue #23): every one bears the fresh run marker
    and is swept in teardown, so >1 is *warned*, not failed. Picks a deterministic
    target that actually has panels rather than an arbitrary search-order index, so
    the semantic assertions never key off an empty duplicate.
    """
    dashboards = gateway.search_dashboards(marker)
    assert dashboards, f"model created no '{marker}' dashboard"
    if len(dashboards) > 1:
        warnings.warn(
            f"model created {len(dashboards)} dashboards for one request; all bear the "
            f"run marker and are swept in teardown: {[d.id for d in dashboards]}",
            stacklevel=2,
        )
    target = next((d for d in dashboards if gateway.get_dashboard(d.id).panels), dashboards[0])
    detail = gateway.get_dashboard(target.id)
    assert detail.panels, "dashboard has no panels"
    data, _ = gateway.get_dashboard_data(target.id)
    config = data["panels"][0]["config"]
    assert config["type"] == "xy"
    layer = config["layers"][0]
    # Semantic essentials only: Kibana's GET normalizes the stored config (adds
    # e.g. color defaults) and the model may set optional dimension fields (label).
    y0 = layer["y"][0]
    assert (y0["operation"], y0["field"]) == ("average", "AvgTicketPrice")
    assert layer["x"]["fields"] == ["Carrier"]
