"""Cross-cutting space-surface tests: threading, schema, echo, error path.

One row per tool. args_builder returns minimal valid arguments; seed()
prepares FakeGateway state (a dashboard with a vis panel at index 0 whose
entry has a grid key; an export handle in a tmp export_dir; a short URL;
an alert rule + connector, a case, and a value-list item — the stores
FakeGateway starts empty; detections fixtures come pre-seeded).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.dashboards.identity import derive_dashboard_id
from kibana_mcp.core.errors import KibanaAuthError, KibanaNotFound
from kibana_mcp.core.models import (
    AlertRule,
    Case,
    Connector,
    DetectionAlert,
    ShortUrl,
    ValueListItem,
)
from kibana_mcp.core.saved_objects import to_ndjson, write_export
from kibana_mcp.config import Settings
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from kibana_mcp.toolboxes.alerting.toolbox import AlertingToolbox
from kibana_mcp.toolboxes.base import SPACE_ID_PATTERN, ToolboxDeps
from kibana_mcp.toolboxes.cases.toolbox import CasesToolbox
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from kibana_mcp.toolboxes.data_management.toolbox import DataManagementToolbox
from kibana_mcp.toolboxes.platform_admin.toolbox import PlatformAdminToolbox
from kibana_mcp.toolboxes.security_detections.toolbox import SecurityDetectionsToolbox
from tests.fakes import FakeGateway

BAR_PANEL = {
    "title": "Avg price by carrier",
    "chart_type": "bar",
    "data_view": "flights",
    "metrics": [{"agg": "average", "field": "AvgTicketPrice"}],
    "group_by": [{"field": "Carrier"}],
}


@dataclass(frozen=True)
class Seed:
    dashboard_id: str
    handle: str
    short_url_id: str
    short_url_slug: str
    rule_id: str
    connector_id: str
    case_id: str
    value_list_item_id: str


def _seed(gateway: FakeGateway, export_dir: Path) -> Seed:
    """Pre-populate a fresh FakeGateway + export_dir with the fixtures the
    tools' minimal args need: a dashboard whose only panel is a vis panel at
    index 0 (grid key present, no "panels" sub-list — not a section), a
    written export handle, a short URL, an alert rule, a connector, a case,
    and a value-list item (the FakeGateway stores that start empty — the
    detections fixtures rule-1/exc-1/item-1/vl-1/t-1 come pre-seeded)."""
    dashboard_id = derive_dashboard_id("Seed")
    gateway.dashboards[dashboard_id] = {
        "title": "Seed",
        "description": "",
        "panels": [{"type": "vis", "grid": {"x": 0, "y": 0, "w": 48, "h": 12}, "config": {}}],
    }
    handle = write_export(export_dir, to_ndjson(gateway.export_body))
    su = ShortUrl(
        id="su-seed", slug="seed-slug", locator_id="LEGACY_SHORT_URL_LOCATOR",
        url="/app/dashboards",
    )
    gateway.short_urls[su.id] = su
    rule = AlertRule(
        id="rule-seed", name="Seed rule", rule_type_id=".es-query", consumer="stackAlerts",
        enabled=False, schedule_interval="1m", status="pending", tags=(),
    )
    gateway.alert_rules[rule.id] = rule
    conn = Connector(
        id="conn-seed", name="Seed log", connector_type_id=".server-log",
        is_missing_secrets=False, is_preconfigured=False,
    )
    gateway.connectors[conn.id] = conn
    case = Case(
        id="case-seed", title="Seed case", status="open", severity="low",
        owner="cases", tags=(), total_comments=0,
    )
    gateway.cases[case.id] = case
    vli = ValueListItem(
        id="vli-seed", list_id="vl-1", value="10.0.0.1", type="ip",
        timestamp="2026-07-18T00:00:00Z",
    )
    gateway.value_list_items[vli.id] = vli
    gateway.detection_alerts.append(
        DetectionAlert(
            id="al-seed", rule_name="Seed rule", severity="low", status="open",
            timestamp="2026-07-18T00:00:00Z",
        )
    )
    return Seed(
        dashboard_id=dashboard_id, handle=handle,
        short_url_id=su.id, short_url_slug=su.slug,
        rule_id=rule.id, connector_id=conn.id, case_id=case.id,
        value_list_item_id=vli.id,
    )


# TOOLS: (name, args_builder, kind) — kind in {"dict", "list"}
# dashboards (11): search_dashboards(list), get_dashboard, create_dashboard,
#   create_visualization, add_panel, add_esql_metric_panel,
#   add_esql_table_panel, add_esql_xy_panel, update_panel,
#   delete_dashboard, delete_panel
# data-management (10): list_data_views(list), describe_data_view,
#   resolve_short_url, export_saved_objects, import_saved_objects,
#   create_data_view, create_short_url, delete_data_view,
#   delete_short_url, overwrite_saved_objects
# alerting (11), cases (6), security-detections (25): the extension rows
#   below — 63 space-aware tools in all
_TOOLS: list[tuple[str, Callable[[Seed], dict[str, Any]], str]] = [
    ("search_dashboards", lambda s: {}, "list"),
    ("get_dashboard", lambda s: {"dashboard_id": s.dashboard_id}, "dict"),
    ("create_dashboard", lambda s: {"title": "New", "panels": [BAR_PANEL]}, "dict"),
    ("create_visualization", lambda s: {"spec": BAR_PANEL}, "dict"),
    ("add_panel", lambda s: {"dashboard_id": s.dashboard_id, "panel": BAR_PANEL}, "dict"),
    (
        "add_esql_metric_panel",
        lambda s: {"dashboard_id": s.dashboard_id, "title": "m", "esql": "Q", "column": "c"},
        "dict",
    ),
    (
        "add_esql_table_panel",
        lambda s: {"dashboard_id": s.dashboard_id, "title": "t", "esql": "Q", "columns": ["c"]},
        "dict",
    ),
    (
        "add_esql_xy_panel",
        lambda s: {
            "dashboard_id": s.dashboard_id, "title": "x", "esql": "Q",
            "x_column": "h", "y_columns": ["c"],
        },
        "dict",
    ),
    (
        "update_panel",
        lambda s: {"dashboard_id": s.dashboard_id, "panel_index": 0, "panel": BAR_PANEL},
        "dict",
    ),
    ("delete_dashboard", lambda s: {"dashboard_id": s.dashboard_id}, "dict"),
    ("delete_panel", lambda s: {"dashboard_id": s.dashboard_id, "panel_index": 0}, "dict"),
    ("list_data_views", lambda s: {}, "list"),
    ("describe_data_view", lambda s: {"data_view": "flights"}, "dict"),
    ("resolve_short_url", lambda s: {"slug": s.short_url_slug}, "dict"),
    (
        "export_saved_objects",
        lambda s: {"objects": [{"type": "index-pattern", "id": "dv1"}]},
        "dict",
    ),
    ("import_saved_objects", lambda s: {"handle": s.handle}, "dict"),
    ("create_data_view", lambda s: {"index_pattern": "logs-*"}, "dict"),
    (
        "create_short_url",
        lambda s: {"locator_id": "LEGACY_SHORT_URL_LOCATOR", "params": {"url": "/app/dashboards"}},
        "dict",
    ),
    ("delete_data_view", lambda s: {"view_id": "dv1"}, "dict"),
    ("delete_short_url", lambda s: {"short_url_id": s.short_url_id}, "dict"),
    ("overwrite_saved_objects", lambda s: {"handle": s.handle}, "dict"),
    # alerting (11): list_alert_rules(list), list_connectors(list), the rest dict
    ("list_alert_rules", lambda s: {}, "list"),
    ("get_alert_rule", lambda s: {"rule_id": s.rule_id}, "dict"),
    ("get_alerting_health", lambda s: {}, "dict"),
    ("list_connectors", lambda s: {}, "list"),
    (
        "create_alert_rule",
        lambda s: {
            "name": "r", "rule_type_id": ".es-query", "consumer": "stackAlerts",
            "params": {},
        },
        "dict",
    ),
    ("enable_alert_rule", lambda s: {"rule_id": s.rule_id}, "dict"),
    ("disable_alert_rule", lambda s: {"rule_id": s.rule_id}, "dict"),
    (
        "create_connector",
        lambda s: {"name": "c", "connector_type_id": ".server-log"},
        "dict",
    ),
    ("delete_alert_rule", lambda s: {"rule_id": s.rule_id}, "dict"),
    ("delete_connector", lambda s: {"connector_id": s.connector_id}, "dict"),
    (
        "execute_connector",
        lambda s: {"connector_id": s.connector_id, "params": {"message": "hi"}},
        "dict",
    ),
    # cases (6): list_cases(list), the rest dict; update_case args pass its guard
    ("list_cases", lambda s: {}, "list"),
    ("get_case", lambda s: {"case_id": s.case_id}, "dict"),
    ("create_case", lambda s: {"title": "T", "description": "D"}, "dict"),
    ("update_case", lambda s: {"case_id": s.case_id, "status": "closed"}, "dict"),
    ("add_case_comment", lambda s: {"case_id": s.case_id, "comment": "note"}, "dict"),
    ("delete_case", lambda s: {"case_id": s.case_id}, "dict"),
    # security-detections (25): args pass the exactly-one/at-least-one guards;
    # list_detection_rule_tags returns a bare string array (the "list" echo
    # check degrades to substring-absence on its items — seeded tags carry no
    # "space" substring)
    ("find_detection_rules", lambda s: {}, "list"),
    ("get_detection_rule", lambda s: {"rule_id": "rule-1"}, "dict"),
    ("get_prepackaged_rules_status", lambda s: {}, "dict"),
    ("list_detection_rule_tags", lambda s: {}, "list"),
    ("search_detection_alerts", lambda s: {}, "list"),
    ("find_exception_lists", lambda s: {}, "list"),
    ("get_exception_list", lambda s: {"list_id": "exc-1"}, "dict"),
    ("find_exception_items", lambda s: {"list_id": "exc-1"}, "list"),
    ("find_value_lists", lambda s: {}, "list"),
    ("find_value_list_items", lambda s: {"list_id": "vl-1"}, "list"),
    ("find_timelines", lambda s: {}, "list"),
    (
        "create_detection_rule",
        lambda s: {"name": "n", "description": "d", "query": "*", "index": ["logs-*"]},
        "dict",
    ),
    ("create_exception_list", lambda s: {"name": "n", "description": "d"}, "dict"),
    (
        "create_exception_item",
        lambda s: {
            "list_id": "exc-1", "name": "n", "description": "d",
            "entries": [{"field": "host.name", "value": "h"}],
        },
        "dict",
    ),
    ("update_detection_rule", lambda s: {"rule_id": "rule-1", "name": "n2"}, "dict"),
    ("replace_detection_rule", lambda s: {"rule_id": "rule-1", "name": "n2"}, "dict"),
    ("enable_detection_rule", lambda s: {"rule_id": "rule-1"}, "dict"),
    ("disable_detection_rule", lambda s: {"rule_id": "rule-1"}, "dict"),
    (
        "create_value_list",
        lambda s: {"name": "n", "description": "d", "type": "keyword"},
        "dict",
    ),
    ("create_value_list_item", lambda s: {"list_id": "vl-1", "value": "v"}, "dict"),
    ("delete_detection_rule", lambda s: {"rule_id": "rule-1"}, "dict"),
    ("delete_exception_list", lambda s: {"list_id": "exc-1"}, "dict"),
    ("delete_exception_item", lambda s: {"item_id": "item-1"}, "dict"),
    ("delete_value_list", lambda s: {"id": "vl-1"}, "dict"),
    ("delete_value_list_item", lambda s: {"item_id": s.value_list_item_id}, "dict"),
]

assert len(_TOOLS) == 63


def _make_server(gateway_factory: Any, export_dir: Path, *, with_platform_admin: bool = False) -> FastMCP:
    server = FastMCP("test")
    deps = ToolboxDeps(
        gateway_factory=gateway_factory, public_kibana_url="http://kb:5601", export_dir=export_dir
    )
    DashboardsToolbox().register(server, deps)
    DataManagementToolbox().register(server, deps)
    AlertingToolbox().register(server, deps)
    CasesToolbox().register(server, deps)
    SecurityDetectionsToolbox().register(server, deps)
    if with_platform_admin:
        PlatformAdminToolbox().register(server, deps)
    return server


# --- Group 1: threading — the recording factory sees exactly the caller's space ---


@pytest.mark.parametrize("name,args_builder,kind", _TOOLS)
@pytest.mark.parametrize("space", ["x", None])
async def test_space_threads_to_the_factory(tmp_path, name, args_builder, kind, space):
    gateway = FakeGateway()
    seed = _seed(gateway, tmp_path)
    calls: list[str | None] = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = _make_server(factory, tmp_path)
    args = args_builder(seed)
    if space is not None:
        args = {**args, "space": space}
    async with Client(server) as client:
        await client.call_tool(name, args)
    assert calls == [space]


# --- Group 2: schema — exactly the 63 tools expose `space`, correctly shaped ---


async def test_schema_exactly_63_tools_expose_space(tmp_path):
    gateway = FakeGateway()
    server = _make_server(lambda space=None: gateway, tmp_path, with_platform_admin=True)
    async with Client(server) as client:
        tools = await client.list_tools()
    with_space = {t.name: t for t in tools if "space" in t.inputSchema.get("properties", {})}
    assert set(with_space) == {name for name, _, _ in _TOOLS}
    # the "no other toolbox has a space parameter" half, over the FULL
    # registry at the destructive tier (the widest surface there is):
    full = build_server(
        Settings(toolboxes=sorted(TOOLBOXES), tier="destructive"),
        lambda space=None: FakeGateway(),
    )
    async with Client(full) as client:
        all_tools = await client.list_tools()
    outsiders = {
        t.name for t in all_tools
        if "space" in t.inputSchema.get("properties", {})
    } - {name for name, _, _ in _TOOLS}
    assert not outsiders, f"tools outside the 63 gained a space param: {sorted(outsiders)}"
    for tool in with_space.values():
        prop = tool.inputSchema["properties"]["space"]
        # Annotated[str, Field(pattern=...)] | None renders as an anyOf of
        # {type: string, pattern: ...} and {type: null} — a naive
        # prop["pattern"] KeyErrors by design; navigate the branches instead.
        string_branch = next(b for b in prop["anyOf"] if b.get("type") == "string")
        assert string_branch["pattern"] == SPACE_ID_PATTERN
        assert "space" not in tool.inputSchema.get("required", [])


async def test_schema_300_char_space_id_passes_a_live_call(tmp_path):
    # The length P8 actually measured live; pins the no-max_length decision.
    gateway = FakeGateway()
    server = _make_server(lambda space=None: gateway, tmp_path)
    async with Client(server) as client:
        result = await client.call_tool("list_data_views", {"space": "a" * 300})
    assert result.data  # schema accepted the 300-char id; the call completed


# --- Group 3: echo — dict rows carry "space" only when the caller set it ---


@pytest.mark.parametrize("name,args_builder,kind", _TOOLS)
async def test_echo_reflects_the_caller_choice(tmp_path, name, args_builder, kind):
    gw_with = FakeGateway()
    seed_with = _seed(gw_with, tmp_path)
    server_with = _make_server(lambda space=None: gw_with, tmp_path)
    async with Client(server_with) as client:
        with_space = await client.call_tool(name, {**args_builder(seed_with), "space": "x"})

    gw_without = FakeGateway()
    seed_without = _seed(gw_without, tmp_path)
    server_without = _make_server(lambda space=None: gw_without, tmp_path)
    async with Client(server_without) as client:
        without_space = await client.call_tool(name, args_builder(seed_without))

    if kind == "dict":
        assert with_space.data["space"] == "x"
        assert "space" not in without_space.data
    else:
        assert all("space" not in item for item in with_space.data)
        assert all("space" not in item for item in without_space.data)


# --- Group 4: error path — gateway_errors() is the outer context manager everywhere ---


@pytest.mark.parametrize("name,args_builder,kind", _TOOLS)
async def test_factory_not_found_surfaces_as_space_guidance(tmp_path, name, args_builder, kind):
    gateway = FakeGateway()
    seed = _seed(gateway, tmp_path)

    def factory(space=None):
        raise KibanaNotFound("space 'x' not found — check what exists with list_spaces")

    server = _make_server(factory, tmp_path)
    args = {**args_builder(seed), "space": "x"}
    async with Client(server) as client:
        with pytest.raises(ToolError, match=r"^space 'x' not found"):
            await client.call_tool(name, args)


@pytest.mark.parametrize("name,args_builder,kind", _TOOLS)
async def test_factory_auth_error_passes_through_unmodified(tmp_path, name, args_builder, kind):
    gateway = FakeGateway()
    seed = _seed(gateway, tmp_path)

    def factory(space=None):
        raise KibanaAuthError("denied")

    server = _make_server(factory, tmp_path)
    args = {**args_builder(seed), "space": "x"}
    async with Client(server) as client:
        with pytest.raises(ToolError, match=r"^denied$"):
            await client.call_tool(name, args)
