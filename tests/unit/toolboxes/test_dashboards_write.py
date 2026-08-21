from copy import deepcopy

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.dashboards.identity import derive_dashboard_id
from kibana_mcp.core.errors import KibanaAuthError, KibanaNotFound, KibanaUnavailable
from kibana_mcp.core.visualizations.spec import ChartType, GroupBySpec, MetricAgg, MetricSpec, VizSpec
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox, _prepare_config
from tests.fakes import FLIGHTS_DV, FakeGateway

BAR_PANEL = {
    "title": "Avg price by carrier",
    "chart_type": "bar",
    "data_view": "flights",
    "metrics": [{"agg": "average", "field": "AvgTicketPrice"}],
    "group_by": [{"field": "Carrier"}],
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


def test_prepare_config_resolves_data_view_name_to_index_pattern(gateway):
    spec = VizSpec(
        title="Avg ticket price by carrier",
        chart_type=ChartType.BAR,
        data_view=FLIGHTS_DV.name,
        metrics=[MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice")],
        group_by=[GroupBySpec(field="Carrier")],
    )
    cfg = _prepare_config(gateway, spec)
    layer = cfg["layers"][0]
    assert layer["data_source"]["index_pattern"] == FLIGHTS_DV.index_pattern


async def test_create_dashboard_happy_path(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_dashboard", {"title": "Flight ops", "panels": [BAR_PANEL]}
        )
    assert result.data["url"] == f"http://kb:5601/app/dashboards#/view/{result.data['id']}"
    stored = gateway.dashboards[result.data["id"]]
    layer = stored["panels"][0]["config"]["layers"][0]
    # time_field was auto-filled from the data view before translation:
    assert layer["data_source"]["time_field"] == "timestamp"
    assert layer["x"] == {"operation": "terms", "fields": ["Carrier"], "limit": 10}


async def test_add_esql_metric_panel_appends_verified_config(mcp, gateway):
    # A dedicated esql tool (does not touch the VizSpec-based create_dashboard).
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "ESQL ops", "panels": [BAR_PANEL]}
        )
        did = created.data["id"]
        result = await client.call_tool(
            "add_esql_metric_panel",
            {
                "dashboard_id": did,
                "title": "Total flights",
                "esql": "FROM kibana_sample_data_flights | STATS total = COUNT(*)",
                "column": "total",
            },
        )
    assert result.data["id"] == did
    cfg = gateway.dashboards[did]["panels"][-1]["config"]  # appended last
    assert cfg["type"] == "metric"
    assert cfg["data_source"] == {
        "type": "esql",
        "query": "FROM kibana_sample_data_flights | STATS total = COUNT(*)",
    }
    assert cfg["metrics"] == [{"type": "primary", "column": "total"}]
    assert "query" not in cfg  # no kql query field


async def test_add_esql_metric_panel_requires_nonempty_args(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "ESQL ops", "panels": [BAR_PANEL]}
        )
        with pytest.raises(Exception):  # min_length=1 on esql/column/title
            await client.call_tool(
                "add_esql_metric_panel",
                {"dashboard_id": created.data["id"], "title": "t", "esql": "", "column": "total"},
            )


async def test_create_dashboard_bad_field_fails_with_suggestion(mcp, gateway):
    bad = dict(BAR_PANEL, group_by=[{"field": "carrier"}])
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="Carrier"):
            await client.call_tool("create_dashboard", {"title": "x", "panels": [bad]})
    assert gateway.dashboards == {}  # nothing written on validation failure


async def test_add_panel_appends(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]}
        )
        await client.call_tool(
            "add_panel", {"dashboard_id": created.data["id"], "panel": BAR_PANEL}
        )
    assert len(gateway.dashboards[created.data["id"]]["panels"]) == 2


async def test_add_panel_refuses_lossy_roundtrip(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]}
        )
        gateway.warnings = ["dropped_panel: map panel not supported"]
        with pytest.raises(Exception, match="unsupported panels"):
            await client.call_tool(
                "add_panel", {"dashboard_id": created.data["id"], "panel": BAR_PANEL}
            )


async def test_add_panel_refuses_unexpected_fields(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]}
        )
        # simulate a dashboard with a field the update API doesn't round-trip
        gateway.dashboards[created.data["id"]]["sections"] = [{"title": "s1"}]
        with pytest.raises(Exception, match="unsupported"):
            await client.call_tool(
                "add_panel", {"dashboard_id": created.data["id"], "panel": BAR_PANEL}
            )


async def test_create_dashboard_requires_at_least_one_panel(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("create_dashboard", {"title": "x", "panels": []})
    assert gateway.dashboards == {}  # nothing written


async def test_update_panel_refuses_non_vis_panel(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]}
        )
        did = created.data["id"]
        gateway.dashboards[did]["panels"].append(
            {"type": "markdown", "grid": {"x": 0, "y": 12, "w": 24, "h": 10}, "config": {}}
        )
        with pytest.raises(Exception, match="not a visualization"):
            await client.call_tool(
                "update_panel", {"dashboard_id": did, "panel_index": 1, "panel": BAR_PANEL}
            )


async def test_update_and_delete_panel(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL, BAR_PANEL]}
        )
        did = created.data["id"]
        pie = dict(BAR_PANEL, chart_type="pie", metrics=[{"agg": "count"}])
        await client.call_tool(
            "update_panel", {"dashboard_id": did, "panel_index": 1, "panel": pie}
        )
        assert gateway.dashboards[did]["panels"][1]["config"]["type"] == "pie"
        await client.call_tool("delete_panel", {"dashboard_id": did, "panel_index": 0})
        assert len(gateway.dashboards[did]["panels"]) == 1


async def test_delete_panel_refuses_a_section(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]}
        )
        did = created.data["id"]
        gateway.dashboards[did]["panels"].append({"title": "S", "collapsed": False, "panels": []})
        with pytest.raises(Exception, match="section"):
            await client.call_tool("delete_panel", {"dashboard_id": did, "panel_index": 1})
        assert len(gateway.dashboards[did]["panels"]) == 2  # untouched


async def test_delete_dashboard(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]}
        )
        await client.call_tool("delete_dashboard", {"dashboard_id": created.data["id"]})
    assert gateway.dashboards == {}


async def test_create_visualization_library_item(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("create_visualization", {"spec": BAR_PANEL})
    assert gateway.visualizations[result.data["id"]]["type"] == "xy"


async def test_tier_tags_present(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert tools["delete_dashboard"].annotations.destructiveHint is True
        assert tools["create_dashboard"].annotations.destructiveHint is False
        assert tools["create_dashboard"].annotations.idempotentHint is True


async def test_create_dashboard_is_idempotent_by_title(mcp, gateway):
    async with Client(mcp) as client:
        r1 = await client.call_tool("create_dashboard", {"title": "Sales", "panels": [BAR_PANEL]})
        r2 = await client.call_tool(
            "create_dashboard",
            {"title": "Sales", "panels": [BAR_PANEL], "description": "second version"},
        )
    assert r1.data["id"] == r2.data["id"] == derive_dashboard_id("Sales")
    assert r1.data["status"] == "created"
    assert r2.data["status"] == "replaced"
    assert len(gateway.dashboards) == 1  # one dashboard, not two
    # ...and its contents were actually replaced by the second call:
    assert gateway.dashboards[r2.data["id"]]["description"] == "second version"


async def test_create_dashboard_distinct_titles_distinct_ids(mcp, gateway):
    async with Client(mcp) as client:
        a = await client.call_tool("create_dashboard", {"title": "Sales", "panels": [BAR_PANEL]})
        b = await client.call_tool("create_dashboard", {"title": "Costs", "panels": [BAR_PANEL]})
    # Assert the *derived* ids (not just "not equal") so this locks the new
    # code path — it fails pre-change, where the fake returns dash-1/dash-2.
    assert a.data["id"] == derive_dashboard_id("Sales")
    assert b.data["id"] == derive_dashboard_id("Costs")
    assert len(gateway.dashboards) == 2


async def test_create_dashboard_preserves_settings_it_does_not_author(mcp, gateway):
    did = derive_dashboard_id("Ops")
    async with Client(mcp) as client:
        await client.call_tool("create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]})
        gateway.dashboards[did]["tags"] = ["keep-me"]
        gateway.dashboards[did]["query"] = {"language": "kuery", "query": "x"}
        await client.call_tool(
            "create_dashboard",
            {"title": "Ops", "panels": [BAR_PANEL], "description": "updated"},
        )
    assert gateway.dashboards[did]["tags"] == ["keep-me"]
    assert gateway.dashboards[did]["query"] == {"language": "kuery", "query": "x"}


async def test_create_dashboard_refuses_replacing_unroundtrippable(mcp, gateway):
    did = derive_dashboard_id("Ops")
    async with Client(mcp) as client:
        await client.call_tool("create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]})
        before = deepcopy(gateway.dashboards[did])  # snapshot the stored content
        gateway.warnings = ["dashboard contains a Maps panel"]  # non-round-trippable
        with pytest.raises(ToolError):
            # A distinguishing arg: were the refusal to leak into an upsert, the
            # stored content would change and the survival assertion would catch it.
            await client.call_tool(
                "create_dashboard",
                {"title": "Ops", "panels": [BAR_PANEL], "description": "must not land"},
            )
    # The refusal raised before gw.upsert_dashboard — the pre-existing dashboard
    # must survive byte-for-byte, and no duplicate may have been created.
    assert gateway.dashboards[did] == before
    assert len(gateway.dashboards) == 1


@pytest.mark.parametrize("err", [KibanaAuthError("nope"), KibanaUnavailable("down")])
async def test_create_dashboard_does_not_swallow_probe_error(mcp, gateway, err):
    gateway.raise_on_get = err  # not KibanaNotFound → must not be treated as absent
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_dashboard", {"title": "Sales", "panels": [BAR_PANEL]})
    assert gateway.dashboards == {}  # no upsert happened


async def test_create_dashboard_rejects_whitespace_only_title(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_dashboard", {"title": "   ", "panels": [BAR_PANEL]})
    assert gateway.dashboards == {}


# --- #52: esql table + xy panel tools ---


async def test_add_esql_table_panel_appends_config(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_dashboard", {"title": "T", "panels": [BAR_PANEL]})
        did = created.data["id"]
        await client.call_tool(
            "add_esql_table_panel",
            {"dashboard_id": did, "title": "tbl", "esql": "FROM x | STATS c=COUNT(*) BY host",
             "columns": ["host"], "metric_columns": ["c"]},
        )
    cfg = gateway.dashboards[did]["panels"][-1]["config"]
    assert cfg["type"] == "data_table"
    assert cfg["data_source"]["type"] == "esql"
    assert cfg["rows"] == [{"column": "host"}] and cfg["metrics"] == [{"column": "c"}]


async def test_add_esql_xy_panel_appends_config(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_dashboard", {"title": "T", "panels": [BAR_PANEL]})
        did = created.data["id"]
        await client.call_tool(
            "add_esql_xy_panel",
            {"dashboard_id": did, "title": "xy", "esql": "Q", "x_column": "host",
             "y_columns": ["c"], "chart_type": "line"},
        )
    layer = gateway.dashboards[did]["panels"][-1]["config"]["layers"][0]
    assert layer["type"] == "line"
    assert layer["x"] == {"column": "host"} and layer["y"] == [{"column": "c"}]


async def test_add_esql_table_panel_requires_columns(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_dashboard", {"title": "T", "panels": [BAR_PANEL]})
        with pytest.raises(Exception):  # min_length=1 on columns
            await client.call_tool(
                "add_esql_table_panel",
                {"dashboard_id": created.data["id"], "title": "t", "esql": "Q", "columns": []},
            )


async def test_add_esql_xy_panel_rejects_bad_chart_type(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_dashboard", {"title": "T", "panels": [BAR_PANEL]})
        with pytest.raises(Exception):  # Literal["bar","line","area"] rejects "pie"
            await client.call_tool(
                "add_esql_xy_panel",
                {"dashboard_id": created.data["id"], "title": "x", "esql": "Q",
                 "x_column": "h", "y_columns": ["c"], "chart_type": "pie"},
            )


async def test_add_esql_panels_reject_empty_string_columns(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_dashboard", {"title": "T", "panels": [BAR_PANEL]})
        did = created.data["id"]
        with pytest.raises(Exception):  # empty-string element in columns
            await client.call_tool(
                "add_esql_table_panel",
                {"dashboard_id": did, "title": "t", "esql": "Q", "columns": [""]},
            )
        with pytest.raises(Exception):  # empty-string element in y_columns
            await client.call_tool(
                "add_esql_xy_panel",
                {"dashboard_id": did, "title": "x", "esql": "Q", "x_column": "h", "y_columns": [""]},
            )


# --- Task 5: space parameter on all 11 dashboards tools ---


async def test_create_dashboard_in_space_scopes_factory_url_and_echo(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    DashboardsToolbox().register(server, deps)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_dashboard",
            {"title": "Ops", "panels": [BAR_PANEL], "space": "sales"},
        )
    assert calls == ["sales"]
    assert result.data["space"] == "sales"
    assert result.data["url"] == f"http://kb:5601/s/sales/app/dashboards#/view/{result.data['id']}"


async def test_create_dashboard_default_space_url_and_shape(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_dashboard", {"title": "Ops", "panels": [BAR_PANEL]}
        )
    assert "space" not in result.data           # byte-identical shape
    assert result.data["url"].startswith("http://kb:5601/app/")


async def test_create_dashboard_space_default_prefixes_url(gateway):
    # space="default" passes through verbatim: /s/default in the link
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway,
                       public_kibana_url="http://kb:5601")
    DashboardsToolbox().register(server, deps)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_dashboard",
            {"title": "Ops", "panels": [BAR_PANEL], "space": "default"},
        )
    assert "/s/default/app/dashboards#/view/" in result.data["url"]
    assert result.data["space"] == "default"


async def test_factory_space_error_surfaces_as_guidance(gateway):
    def factory(space=None):
        raise KibanaNotFound("space 'sales' not found — check what exists with list_spaces")

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    DashboardsToolbox().register(server, deps)
    async with Client(server) as client:
        with pytest.raises(ToolError, match="space 'sales' not found"):
            await client.call_tool(
                "create_dashboard",
                {"title": "Ops", "panels": [BAR_PANEL], "space": "sales"},
            )


# URL matrix: every URL-emitting tool x {space="sales", space="default", omitted}.
# A copy-paste miss in any one dashboard_url(...) call site fails exactly one cell.
_URL_TOOLS = [
    ("create_dashboard", lambda did: {"title": "URL matrix", "panels": [BAR_PANEL]}),
    ("add_panel", lambda did: {"dashboard_id": did, "panel": BAR_PANEL}),
    ("add_esql_metric_panel",
     lambda did: {"dashboard_id": did, "title": "m", "esql": "Q", "column": "c"}),
    ("add_esql_table_panel",
     lambda did: {"dashboard_id": did, "title": "t", "esql": "Q", "columns": ["c"]}),
    ("add_esql_xy_panel",
     lambda did: {"dashboard_id": did, "title": "x", "esql": "Q", "x_column": "h",
                  "y_columns": ["c"]}),
]


@pytest.mark.parametrize("tool_name,args_builder", _URL_TOOLS)
@pytest.mark.parametrize(
    "space,prefix", [("sales", "/s/sales/"), ("default", "/s/default/"), (None, None)]
)
async def test_url_matrix_space_prefix(mcp, gateway, tool_name, args_builder, space, prefix):
    async with Client(mcp) as client:
        seed = await client.call_tool("create_dashboard", {"title": "Seed", "panels": [BAR_PANEL]})
        args = args_builder(seed.data["id"])
        if space is not None:
            args = {**args, "space": space}
        result = await client.call_tool(tool_name, args)
    if prefix is not None:
        assert prefix in result.data["url"]
        assert result.data["space"] == space
    else:
        assert "/s/" not in result.data["url"]
        assert "space" not in result.data


async def test_create_dashboard_midcall_space_not_found_does_not_upsert(gateway):
    """A KibanaSpaceNotFound from the idempotency probe must surface as the
    space guidance, never be misread as "dashboard does not exist" (which
    would proceed to upsert into an orphan namespace)."""
    from kibana_mcp.core.errors import KibanaSpaceNotFound

    gateway.raise_on_get = KibanaSpaceNotFound("space 'sales' not found — check what exists with list_spaces")
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    DashboardsToolbox().register(server, deps)
    async with Client(server) as client:
        with pytest.raises(ToolError, match=r"^space 'sales' not found"):
            await client.call_tool(
                "create_dashboard",
                {"title": "Mid call", "panels": [BAR_PANEL], "space": "sales"},
            )
    assert gateway.dashboards == {}  # nothing was upserted
