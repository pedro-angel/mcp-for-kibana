from kibana_mcp.core.dashboards.compose import append_panel, build_dashboard_data, layout_panels

CFG = {"type": "xy", "title": "c"}


def test_single_panel_full_width():
    panels = layout_panels([CFG])
    assert panels == [{"type": "vis", "grid": {"x": 0, "y": 0, "w": 48, "h": 12}, "config": CFG}]


def test_three_panels_two_per_row():
    grids = [p["grid"] for p in layout_panels([CFG, CFG, CFG])]
    assert grids == [
        {"x": 0, "y": 0, "w": 24, "h": 10},
        {"x": 24, "y": 0, "w": 24, "h": 10},
        {"x": 0, "y": 10, "w": 24, "h": 10},
    ]


def test_build_dashboard_data():
    data = build_dashboard_data(
        "Ops", "overview", layout_panels([CFG]), {"from": "now-1d", "to": "now"}
    )
    assert data["title"] == "Ops"
    assert data["description"] == "overview"
    assert data["time_range"] == {"from": "now-1d", "to": "now"}
    assert len(data["panels"]) == 1


def test_append_panel_goes_below_and_does_not_mutate():
    data = build_dashboard_data("t", "", layout_panels([CFG, CFG]), None)
    before = len(data["panels"])
    new = append_panel(data, {"type": "pie", "title": "p"})
    assert len(data["panels"]) == before  # original untouched
    added = new["panels"][-1]
    assert added["grid"] == {"x": 0, "y": 10, "w": 24, "h": 10}


def test_append_panel_survives_a_section():
    gridded = {"type": "vis", "grid": {"x": 0, "y": 0, "w": 24, "h": 10}, "config": CFG}
    section = {"title": "S", "collapsed": False, "panels": []}
    data = {"title": "t", "description": "", "panels": [gridded, section]}
    new = append_panel(data, {"type": "pie", "title": "p"})
    added = new["panels"][-1]
    assert added["grid"]["y"] == 10  # below the gridded panel, section ignored


def test_replace_panel_config_keeps_grid():
    from kibana_mcp.core.dashboards.compose import replace_panel_config
    data = build_dashboard_data("t", "", layout_panels([CFG, CFG]), None)
    new = replace_panel_config(data, 1, {"type": "pie", "title": "new"})
    assert new["panels"][1]["config"]["type"] == "pie"
    assert new["panels"][1]["grid"] == data["panels"][1]["grid"]
    assert data["panels"][1]["config"] == CFG  # original untouched


def test_remove_panel():
    from kibana_mcp.core.dashboards.compose import remove_panel
    data = build_dashboard_data("t", "", layout_panels([CFG, CFG]), None)
    new = remove_panel(data, 0)
    assert len(new["panels"]) == 1 and len(data["panels"]) == 2
