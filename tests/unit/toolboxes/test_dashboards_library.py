"""The library-visualization loop (D37): find, read, edit, delete and place the
visualizations create_visualization makes, and keep every dashboard that holds
one by reference editable by the other dashboards tools."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from tests.fakes import FakeGateway

BAR_PANEL = {
    "title": "Avg price by carrier",
    "chart_type": "bar",
    "data_view": "flights",
    "metrics": [{"agg": "average", "field": "AvgTicketPrice"}],
    "group_by": [{"field": "Carrier"}],
}
PIE_SPEC = {
    "title": "Flights by carrier",
    "chart_type": "pie",
    "data_view": "flights",
    "metrics": [{"agg": "count"}],
    "group_by": [{"field": "Carrier", "limit": 5}],
}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    DashboardsToolbox().register(server, deps)
    return server


async def _viz_and_dashboard(client):
    viz = (await client.call_tool("create_visualization", {"spec": BAR_PANEL})).data
    dash = (await client.call_tool("create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]})).data
    return viz["id"], dash["id"]


async def test_tiers_and_annotations(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("search_visualizations", "get_visualization"):
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.openWorldHint is False
    assert tools["update_visualization"].annotations.destructiveHint is False
    assert tools["update_visualization"].annotations.idempotentHint is True
    assert tools["add_library_panel"].annotations.destructiveHint is False
    assert tools["add_library_panel"].annotations.idempotentHint is False  # appends each call
    assert tools["delete_visualization"].annotations.destructiveHint is True
    # tier tags (read/write/destructive) are pinned by test_server_assembly.py


async def test_search_visualizations_lists_and_filters(mcp, gateway):
    async with Client(mcp) as client:
        bar = (await client.call_tool("create_visualization", {"spec": BAR_PANEL})).data
        pie = (await client.call_tool("create_visualization", {"spec": PIE_SPEC})).data
        everything = (await client.call_tool("search_visualizations", {})).data
        only_pie = (await client.call_tool("search_visualizations", {"query": "flights by"})).data
    assert {v["id"] for v in everything} == {bar["id"], pie["id"]}
    assert only_pie == [
        {"id": pie["id"], "title": "Flights by carrier", "description": "", "chart_type": "pie"}
    ]


async def test_search_visualizations_empty(mcp):
    async with Client(mcp) as client:
        assert (await client.call_tool("search_visualizations", {})).data == []


async def test_get_visualization_reports_the_spec_it_was_built_from(mcp):
    spec = dict(BAR_PANEL, filters=[{"field": "Cancelled", "eq": False}])
    async with Client(mcp) as client:
        viz = (await client.call_tool("create_visualization", {"spec": spec})).data
        got = (await client.call_tool("get_visualization", {"visualization_id": viz["id"]})).data
    assert got == {
        "id": viz["id"],
        "title": "Avg price by carrier",
        "description": "",
        "chart_type": "bar",
        "data_view": "kibana_sample_data_flights",  # the name resolved to the pattern
        "time_field": "timestamp",  # auto-filled from the data view
        "metrics": [{"agg": "average", "field": "AvgTicketPrice"}],
        "group_by": [{"field": "Carrier", "kind": "terms", "limit": 10}],
        "query": "Cancelled: false",
    }


@pytest.mark.parametrize("tool,extra", [
    ("get_visualization", {}),
    ("update_visualization", {"spec": BAR_PANEL}),
    ("delete_visualization", {}),
])
@pytest.mark.parametrize("empty", ["", "  "])
async def test_empty_visualization_id_names_where_ids_come_from(mcp, tool, extra, empty):
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError,
            match=r"^visualization_id is empty — pass the id that create_visualization or "
                  r"search_visualizations returns$",
        ):
            await client.call_tool(tool, {"visualization_id": empty, **extra})


async def test_update_visualization_replaces_in_place(mcp, gateway):
    async with Client(mcp) as client:
        viz = (await client.call_tool("create_visualization", {"spec": BAR_PANEL})).data
        result = (await client.call_tool(
            "update_visualization", {"visualization_id": viz["id"], "spec": PIE_SPEC}
        )).data
    assert result == {"id": viz["id"], "title": "Flights by carrier"}
    assert list(gateway.visualizations) == [viz["id"]]  # same id, nothing new
    assert gateway.visualizations[viz["id"]]["type"] == "pie"


async def test_update_visualization_preflights_before_any_write(mcp, gateway):
    bad = dict(BAR_PANEL, group_by=[{"field": "carrier"}])
    async with Client(mcp) as client:
        viz = (await client.call_tool("create_visualization", {"spec": BAR_PANEL})).data
        before = dict(gateway.visualizations[viz["id"]])
        with pytest.raises(ToolError, match=r"(?s)invalid visualization spec:.*did you mean 'Carrier'"):
            await client.call_tool(
                "update_visualization", {"visualization_id": viz["id"], "spec": bad}
            )
    assert gateway.visualizations[viz["id"]] == before


async def test_update_visualization_unknown_id_creates_nothing(mcp, gateway):
    bad = dict(BAR_PANEL, group_by=[{"field": "carrier"}])
    async with Client(mcp) as client:
        # the id is checked first: a wrong id with a bad spec names the id
        with pytest.raises(ToolError, match=r"visualization 'nope' not found"):
            await client.call_tool("update_visualization", {"visualization_id": "nope", "spec": bad})
    assert gateway.visualizations == {}


async def test_add_library_panel_links_and_get_dashboard_reports_it(mcp, gateway):
    async with Client(mcp) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        result = (await client.call_tool(
            "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
        )).data
        detail = (await client.call_tool("get_dashboard", {"dashboard_id": dash_id})).data
    assert result == {
        "id": dash_id, "url": f"http://kb:5601/app/dashboards#/view/{dash_id}", "panel_index": 1,
    }
    panel = gateway.dashboards[dash_id]["panels"][1]
    assert panel["type"] == "vis" and panel["config"] == {"ref_id": viz_id}
    assert detail["panels"] == [
        {"index": 0, "type": "vis", "title": "Avg price by carrier"},  # inline: shape unchanged
        {"index": 1, "type": "vis", "title": "Avg price by carrier", "visualization_id": viz_id},
    ]


async def test_add_library_panel_unknown_visualization_leaves_dashboard_alone(mcp, gateway):
    async with Client(mcp) as client:
        _, dash_id = await _viz_and_dashboard(client)
        before = gateway.dashboards[dash_id]
        with pytest.raises(ToolError, match=r"visualization 'nope' not found"):
            await client.call_tool(
                "add_library_panel", {"dashboard_id": dash_id, "visualization_id": "nope"}
            )
    assert gateway.dashboards[dash_id] is before


async def test_add_library_panel_refuses_unroundtrippable_dashboard(mcp, gateway):
    async with Client(mcp) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        gateway.warnings = ["dropped_panel: map panel not supported"]
        with pytest.raises(ToolError, match="unsupported panels"):
            await client.call_tool(
                "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
            )
    assert len(gateway.dashboards[dash_id]["panels"]) == 1


async def test_dashboard_with_a_library_panel_stays_editable(mcp, gateway):
    async with Client(mcp) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        await client.call_tool(
            "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
        )
        # add_panel and the ES|QL appends keep the by-reference panel as it is
        await client.call_tool("add_panel", {"dashboard_id": dash_id, "panel": PIE_SPEC})
        await client.call_tool("add_esql_metric_panel", {
            "dashboard_id": dash_id, "title": "n", "esql": "FROM x | STATS n = COUNT(*)",
            "column": "n",
        })
        panels = gateway.dashboards[dash_id]["panels"]
        assert [p["config"].get("ref_id") for p in panels] == [None, viz_id, None, None]
        # update_panel still edits an inline panel...
        await client.call_tool(
            "update_panel", {"dashboard_id": dash_id, "panel_index": 0, "panel": PIE_SPEC}
        )
        assert gateway.dashboards[dash_id]["panels"][0]["config"]["type"] == "pie"
        # ...and delete_panel removes the linked one without touching the library
        await client.call_tool("delete_panel", {"dashboard_id": dash_id, "panel_index": 1})
    assert [p["config"].get("ref_id") for p in gateway.dashboards[dash_id]["panels"]] == [
        None, None, None,
    ]
    assert viz_id in gateway.visualizations


async def test_update_panel_on_a_library_panel_names_update_visualization(mcp, gateway):
    async with Client(mcp) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        await client.call_tool(
            "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
        )
        before = gateway.dashboards[dash_id]
        with pytest.raises(ToolError) as exc:
            await client.call_tool(
                "update_panel", {"dashboard_id": dash_id, "panel_index": 1, "panel": PIE_SPEC}
            )
    assert str(exc.value) == (
        f"panel 1 shows library visualization '{viz_id}' — change it with "
        "update_visualization, which updates every dashboard that shows it"
    )
    assert gateway.dashboards[dash_id] is before


async def test_create_dashboard_replace_over_a_library_panel(mcp, gateway):
    async with Client(mcp) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        await client.call_tool(
            "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
        )
        result = (await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [PIE_SPEC]}
        )).data
    assert result["status"] == "replaced"
    assert [p["config"]["type"] for p in gateway.dashboards[dash_id]["panels"]] == ["pie"]
    assert viz_id in gateway.visualizations  # the library item is not the dashboard's


async def test_update_visualization_reaches_linked_panels(mcp, gateway):
    async with Client(mcp) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        await client.call_tool(
            "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
        )
        await client.call_tool(
            "update_visualization", {"visualization_id": viz_id, "spec": PIE_SPEC}
        )
        detail = (await client.call_tool("get_dashboard", {"dashboard_id": dash_id})).data
    assert detail["panels"][1]["title"] == "Flights by carrier"
    assert detail["panels"][0]["title"] == "Avg price by carrier"  # the inline copy did not move


async def test_delete_visualization_leaves_a_dangling_panel_that_stays_editable(mcp, gateway):
    async with Client(mcp) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        await client.call_tool(
            "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
        )
        result = (await client.call_tool("delete_visualization", {"visualization_id": viz_id})).data
        detail = (await client.call_tool("get_dashboard", {"dashboard_id": dash_id})).data
        await client.call_tool("add_panel", {"dashboard_id": dash_id, "panel": PIE_SPEC})
        await client.call_tool("delete_panel", {"dashboard_id": dash_id, "panel_index": 1})
    assert result == {"id": viz_id, "deleted": True}
    assert gateway.visualizations == {}
    assert detail["panels"][1] == {
        "index": 1, "type": "vis", "title": "", "visualization_id": viz_id,
    }
    assert [p["config"].get("ref_id") for p in gateway.dashboards[dash_id]["panels"]] == [None, None]


async def test_delete_visualization_unknown_id(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"visualization 'nope' not found"):
            await client.call_tool("delete_visualization", {"visualization_id": "nope"})


async def test_add_library_panel_in_a_space_scopes_url_and_echo(gateway):
    seen = []

    def factory(space=None):
        seen.append(space)
        return gateway

    server = FastMCP("test")
    DashboardsToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    )
    async with Client(server) as client:
        viz_id, dash_id = await _viz_and_dashboard(client)
        seen.clear()
        result = (await client.call_tool("add_library_panel", {
            "dashboard_id": dash_id, "visualization_id": viz_id, "space": "sales",
        })).data
    assert seen == ["sales"]  # one scoped gateway for the whole read-modify-write
    assert result["url"] == f"http://kb:5601/s/sales/app/dashboards#/view/{dash_id}"
    assert result["space"] == "sales"
