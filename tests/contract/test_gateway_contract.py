"""The authority on payload shapes: every golden translation must be accepted
by a real Kibana 9.4.3. If a test here fails while unit goldens pass, fix
translate.py AND the golden test together."""

import contextlib
import os
import uuid

import pytest
from fastmcp import Client, FastMCP

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.dashboards.compose import build_dashboard_data, layout_panels
from kibana_mcp.core.dashboards.identity import derive_dashboard_id
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected
from kibana_mcp.core.saved_objects import read_export, to_ndjson, write_export
from kibana_mcp.core.visualizations.spec import (
    ChartType, FilterSpec, GroupBySpec, MetricAgg, MetricSpec, VizSpec,
)
from kibana_mcp.core.visualizations.translate import (
    esql_metric_config,
    esql_table_config,
    esql_xy_config,
    to_lens_config,
)
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from kibana_mcp.toolboxes.data_management.toolbox import DataManagementToolbox
from kibana_mcp.toolboxes.platform_admin.toolbox import PlatformAdminToolbox
from kibana_mcp.toolboxes.security_detections.toolbox import SecurityDetectionsToolbox

pytestmark = pytest.mark.contract

FLIGHTS = "kibana_sample_data_flights"


def flights_spec(**overrides):
    base = dict(
        title="contract-test", chart_type=ChartType.BAR, data_view=FLIGHTS,
        time_field="timestamp",
        metrics=[MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice")],
        group_by=[GroupBySpec(field="Carrier")],
    )
    base.update(overrides)
    return VizSpec(**base)


def test_data_views_shape(gateway):
    views = gateway.list_data_views()
    assert any(v.index_pattern == FLIGHTS for v in views)
    dv = gateway.get_data_view(FLIGHTS)
    assert dv.time_field == "timestamp"
    assert dv.fields["AvgTicketPrice"] == "number"
    assert dv.fields["Carrier"] == "string"


@pytest.mark.parametrize(
    "spec",
    [
        flights_spec(),
        flights_spec(chart_type=ChartType.LINE,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="timestamp", kind="date_histogram")]),
        flights_spec(chart_type=ChartType.AREA,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="timestamp", kind="date_histogram"),
                               GroupBySpec(field="Carrier", limit=4)]),
        flights_spec(chart_type=ChartType.PIE,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="Carrier")]),
        flights_spec(chart_type=ChartType.METRIC,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)], group_by=[]),
        flights_spec(chart_type=ChartType.TABLE,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT),
                              MetricSpec(agg=MetricAgg.AVG, field="AvgTicketPrice")],
                     group_by=[GroupBySpec(field="Carrier", limit=20)]),
        flights_spec(filters=[FilterSpec(field="Cancelled", eq=False)]),
        # new #11 chart types (dimension keys pinned to live probes):
        flights_spec(chart_type=ChartType.GAUGE,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)], group_by=[]),
        flights_spec(chart_type=ChartType.HEATMAP,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="Carrier"), GroupBySpec(field="DestCountry")]),
        flights_spec(chart_type=ChartType.HEATMAP, time_field="timestamp",  # time-heatmap
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="timestamp", kind="date_histogram"),
                               GroupBySpec(field="Carrier")]),
        flights_spec(chart_type=ChartType.TAG_CLOUD,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="Carrier")]),
        flights_spec(chart_type=ChartType.REGION_MAP,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="DestCountry")]),
        flights_spec(chart_type=ChartType.MOSAIC,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="Carrier")]),
        flights_spec(chart_type=ChartType.TREEMAP,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="Carrier")]),
        flights_spec(chart_type=ChartType.WAFFLE,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)],
                     group_by=[GroupBySpec(field="Carrier")]),
    ],
    ids=["bar", "line-time", "area-breakdown", "pie", "metric", "table", "bar-filtered",
         "gauge", "heatmap", "heatmap-time", "tag_cloud", "region_map", "mosaic", "treemap",
         "waffle"],
)
def test_kibana_accepts_every_chart_type_as_library_item(gateway, spec):
    viz_id = gateway.create_visualization(to_lens_config(spec))
    try:
        assert viz_id
    finally:
        gateway.delete_visualization(viz_id)


def test_dashboard_full_lifecycle(gateway):
    configs = [to_lens_config(flights_spec()), to_lens_config(
        flights_spec(chart_type=ChartType.METRIC,
                     metrics=[MetricSpec(agg=MetricAgg.COUNT)], group_by=[])
    )]
    data = build_dashboard_data(
        "mcp-for-kibana contract lifecycle", "created by contract tests",
        layout_panels(configs), {"from": "now-7d", "to": "now"},
    )
    dash_id = gateway.create_dashboard(data)
    try:
        detail = gateway.get_dashboard(dash_id)
        assert detail.title == "mcp-for-kibana contract lifecycle"
        assert len(detail.panels) == 2
        roundtrip, warnings = gateway.get_dashboard_data(dash_id)
        assert warnings == []  # our own dashboards must round-trip losslessly
        found = gateway.search_dashboards("contract lifecycle")
        assert any(d.id == dash_id for d in found)
    finally:
        gateway.delete_dashboard(dash_id)


_PANEL = {
    "title": "avg price", "chart_type": "bar", "data_view": FLIGHTS,
    "metrics": [{"agg": "average", "field": "AvgTicketPrice"}],
    "group_by": [{"field": "Carrier"}],
}


def _delete_if_exists(gateway, dashboard_id):
    """Best-effort pre-clean / teardown: delete a dashboard, tolerating absence
    (a prior run may have left it, or a failure path may have skipped creation)."""
    try:
        gateway.delete_dashboard(dashboard_id)
    except KibanaNotFound:
        pass


def _tool_server():
    # Fresh live gateway per tool call (the tool closes it on __exit__).
    server = FastMCP("contract")
    DashboardsToolbox().register(
        server,
        ToolboxDeps(
            gateway_factory=lambda space=None: KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space
            ),
            public_kibana_url="http://kb:5601",
        ),
    )
    return server


async def test_create_dashboard_tool_is_idempotent_live(gateway):
    title = "contract idempotent dashboard"
    did = derive_dashboard_id(title)
    _delete_if_exists(gateway, did)  # pre-clean a surviving id from a prior run
    try:
        async with Client(_tool_server()) as client:
            r1 = await client.call_tool("create_dashboard", {"title": title, "panels": [_PANEL]})
            r2 = await client.call_tool(
                "create_dashboard",
                {"title": title, "panels": [_PANEL], "description": "v2"},
            )
        assert r1.data["id"] == did == r2.data["id"]
        assert r2.data["status"] == "replaced"
        exact = [d for d in gateway.search_dashboards(title) if d.title == title]
        assert len(exact) == 1  # de-duplicated, not two
    finally:
        _delete_if_exists(gateway, did)


async def test_long_title_derives_acceptable_id_live(gateway):
    title = "Regional " + "sales " * 20 + "overview"  # >64-char slug
    did = derive_dashboard_id(title)
    _delete_if_exists(gateway, did)
    try:
        async with Client(_tool_server()) as client:
            r = await client.call_tool("create_dashboard", {"title": title, "panels": [_PANEL]})
        assert r.data["id"] == did
    finally:
        _delete_if_exists(gateway, did)


def test_get_dashboard_data_missing_raises_notfound(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.get_dashboard_data("no-such-dashboard-id-xyz")


def test_kibana_status_live(gateway):
    s = gateway.get_kibana_status()
    assert s.overall_level == "available"  # seeded stack is healthy
    assert s.version.startswith("9.")
    assert s.unhealthy == ()  # nothing degraded on a clean stack


def test_kibana_stats_live(gateway):
    st = gateway.get_kibana_stats()
    assert st.heap_used_bytes > 0
    assert st.heap_total_bytes >= st.heap_used_bytes
    assert st.heap_size_limit_bytes > 0
    # event_loop_delay is always >0 on a live Kibana; a path drift would silently
    # default to 0.0, so assert it's populated (catches a field rename/relocation).
    assert st.event_loop_delay_ms > 0
    assert isinstance(st.concurrent_connections, int)


def test_task_manager_health_live(gateway):
    h = gateway.get_task_manager_health()
    assert h.status  # non-empty, e.g. "OK"
    assert h.timestamp


# --- data-management toolbox (#28) live paths ---


def test_create_and_delete_data_view_live(gateway):
    # A unique throwaway title (= index pattern) — future-proof against any
    # unique-title enforcement and can't collide with the seeded sample view.
    pattern = f"{FLIGHTS}-probe-{uuid.uuid4().hex[:8]}"
    created = gateway.create_data_view(pattern, "probe-dv", "timestamp")
    try:
        assert created.id
        assert created.index_pattern == pattern
        assert any(v.id == created.id for v in gateway.list_data_views())
    finally:
        gateway.delete_data_view(created.id)
    assert not any(v.id == created.id for v in gateway.list_data_views())  # gone


def test_short_url_lifecycle_live(gateway):
    created = gateway.create_short_url("LEGACY_SHORT_URL_LOCATOR", {"url": "/app/dashboards"})
    try:
        assert created.id and created.slug
        assert created.locator_id == "LEGACY_SHORT_URL_LOCATOR"
        assert gateway.resolve_short_url(created.slug).id == created.id
    finally:
        gateway.delete_short_url(created.id)


# --- alerting toolbox (#10) live paths ---

_ESQ_PARAMS = {
    "searchType": "esQuery",
    "esQuery": '{"query":{"match_all":{}}}',
    "index": [FLIGHTS],
    "timeField": "timestamp",
    "threshold": [0],
    "thresholdComparator": ">",
    "timeWindowSize": 5,
    "timeWindowUnit": "m",
    "size": 100,
}


def test_alerting_health_live(gateway):
    h = gateway.get_alerting_health()
    assert h.status in ("ok", "warn", "error", "unknown")
    assert h.has_permanent_encryption_key is True  # seeded stack has an encryption key


def test_alert_rule_lifecycle_live(gateway):
    rule = gateway.create_alert_rule(
        f"contract-esq-{uuid.uuid4().hex[:8]}", ".es-query", "stackAlerts", "1m",
        _ESQ_PARAMS, ["contract"], False,  # enabled=False: inert
    )
    try:
        assert rule.id and rule.enabled is False and rule.rule_type_id == ".es-query"
        gateway.enable_alert_rule(rule.id)
        assert gateway.get_alert_rule(rule.id).enabled is True  # enable transition observed
        assert any(r.id == rule.id for r in gateway.list_alert_rules("contract-esq"))
        gateway.disable_alert_rule(rule.id)
        assert gateway.get_alert_rule(rule.id).enabled is False  # disable transition observed
    finally:
        gateway.delete_alert_rule(rule.id)
    assert not any(r.id == rule.id for r in gateway.list_alert_rules("contract-esq"))


def test_connector_execute_lifecycle_live(gateway):
    # .server-log only writes to the Kibana log — no external side effect.
    conn = gateway.create_connector(f"contract-log-{uuid.uuid4().hex[:8]}", ".server-log", None, None)
    try:
        assert conn.id and conn.connector_type_id == ".server-log"
        result = gateway.execute_connector(conn.id, {"message": "mcp-for-kibana contract test", "level": "info"})
        assert result["status"] == "ok"
        assert any(c.id == conn.id for c in gateway.list_connectors())
    finally:
        gateway.delete_connector(conn.id)


# --- cases toolbox (#29) live paths ---


def test_case_lifecycle_live(gateway):
    marker = f"mcp-for-kibana contract case {uuid.uuid4().hex[:8]}"
    case = gateway.create_case(marker, "created by contract tests", ["contract"], "low")
    try:
        assert case.id and case.status == "open" and case.severity == "low"
        commented = gateway.add_case_comment(case.id, "a contract comment")
        assert commented.total_comments >= 1
        # update AFTER commenting + transition both status and severity
        # (read-modify-write version): the returned Case must still report the
        # comment, guarding total_comments being dropped from the PATCH response.
        updated = gateway.update_case(case.id, "in-progress", "high", None, None)
        assert updated.status == "in-progress" and updated.severity == "high"
        assert updated.total_comments >= 1
        assert any(c.id == case.id for c in gateway.list_cases(marker))
        assert gateway.get_case(case.id).status == "in-progress"
    finally:
        gateway.delete_case(case.id)


# --- observability toolbox (Wave 3): synthetics + uptime + apm-config reads ---
# All read-only, HARD assertions (no skip: the DoD gate treats skipped>0 as
# NO-GO). The Basic stack carries no synthetics/apm data, so most are
# empty-but-typed. Every outcome below was confirmed against a live stack
# before being asserted here.


def test_synthetic_monitors_empty_live(gateway):
    assert gateway.list_synthetic_monitors() == []


def test_get_synthetic_monitor_missing_raises_live(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.get_synthetic_monitor("does-not-exist-monitor-id-xyz")


def test_synthetic_params_empty_live(gateway):
    assert gateway.list_synthetic_params() == []


def test_synthetic_private_locations_empty_live(gateway):
    assert gateway.list_synthetic_private_locations() == []


def test_uptime_settings_live(gateway):
    s = gateway.get_uptime_settings()
    assert s.heartbeat_indices == "heartbeat-*"
    assert isinstance(s.cert_expiration_threshold, (int, float))
    assert isinstance(s.cert_age_threshold, (int, float))


def test_apm_agent_configs_empty_live(gateway):
    assert gateway.list_apm_agent_configs() == []


def test_get_apm_agent_config_missing_raises_live(gateway):
    # No config present -> /view 404 -> KibanaNotFound (probed live).
    with pytest.raises(KibanaNotFound):
        gateway.get_apm_agent_config(None, None)


def test_apm_environments_has_all_option_sentinel_live(gateway):
    envs = gateway.list_apm_environments("mcp-for-kibana-acceptance")
    assert any(e.name == "ALL_OPTION_VALUE" for e in envs)


def test_apm_sourcemaps_empty_live(gateway):
    assert gateway.list_apm_sourcemaps() == []


def test_search_apm_annotations_empty_live(gateway):
    annotations = gateway.search_apm_annotations(
        "mcp-for-kibana-acceptance",
        "2026-07-11T00:00:00.000Z",
        "2026-07-13T00:00:00.000Z",
        "ENVIRONMENT_ALL",
    )
    assert annotations == []


# --- security-detections toolbox (Wave 3): detection-engine reads ---
# All read-only, HARD assertions (no skip: DoD treats skipped>0 as NO-GO). The
# Basic stack carries no rules/alerts/exceptions/lists/timelines, so most are
# empty-but-typed. Every outcome was confirmed against a live stack before
# being asserted here.


def test_detection_rules_empty_live(gateway):
    assert gateway.find_detection_rules() == []


def test_get_detection_rule_missing_raises_live(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.get_detection_rule("does-not-exist-rule-xyz", None)


def test_prepackaged_rules_status_live(gateway):
    s = gateway.get_prepackaged_rules_status()
    assert isinstance(s.rules_installed, int)
    assert isinstance(s.rules_not_installed, int)
    assert isinstance(s.timelines_installed, int)


def test_detection_rule_tags_live(gateway):
    tags = gateway.list_detection_rule_tags()
    assert isinstance(tags, list)
    assert all(isinstance(t, str) for t in tags)


def test_search_detection_alerts_live(gateway):
    alerts = gateway.search_detection_alerts(1)
    assert isinstance(alerts, list)  # empty on the fresh alerts index


def test_exception_lists_empty_live(gateway):
    assert gateway.find_exception_lists() == []


def test_get_exception_list_missing_raises_live(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.get_exception_list(None, "does-not-exist-list-xyz")


def test_find_exception_items_missing_list_raises_live(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.find_exception_items("does-not-exist-list-xyz")


def test_value_lists_empty_live(gateway):
    assert gateway.find_value_lists() == []


def test_timelines_empty_live(gateway):
    assert gateway.find_timelines() == []


# --- ES|QL panels (#40): esql metric via inline dashboard; library route rejects esql ---


def _esql_metric_config():
    return esql_metric_config(
        "esql total flights", f"FROM {FLIGHTS} | STATS total = COUNT(*)", "total"
    )


def test_esql_metric_panel_roundtrips_live(gateway):
    # An esql metric panel embedded inline in a dashboard round-trips losslessly.
    data = build_dashboard_data(
        "mcp-for-kibana esql contract", "esql panel contract test",
        layout_panels([_esql_metric_config()]), {"from": "now-7d", "to": "now"},
    )
    dash_id = gateway.create_dashboard(data)
    try:
        detail = gateway.get_dashboard(dash_id)
        assert len(detail.panels) == 1
        assert detail.panels[0].type == "vis"
        roundtrip, warnings = gateway.get_dashboard_data(dash_id)
        assert warnings == []  # esql vis panels round-trip like any vis panel
        cfg = roundtrip["panels"][0]["config"]
        assert cfg["type"] == "metric"
        assert cfg["data_source"] == {"type": "esql", "query": f"FROM {FLIGHTS} | STATS total = COUNT(*)"}
        # Kibana accepts the esql metric and normalizes it (adds styling such as
        # color:{type:auto}); assert the load-bearing fields, not exact equality.
        assert cfg["metrics"][0]["type"] == "primary"
        assert cfg["metrics"][0]["column"] == "total"
        assert cfg["title"] == "esql total flights"  # the config title round-trips
    finally:
        gateway.delete_dashboard(dash_id)


def test_esql_library_visualization_rejected_live(gateway):
    # The library route (POST /api/visualizations) rejects an esql data_source —
    # this is why esql goes through the dedicated add_esql_metric_panel tool, not
    # a VizSpec library visualization.
    with pytest.raises(KibanaRejected):
        gateway.create_visualization(_esql_metric_config())


async def test_add_esql_metric_panel_tool_live(gateway):
    title = "contract esql tool dashboard"
    did = derive_dashboard_id(title)
    _delete_if_exists(gateway, did)
    try:
        async with Client(_tool_server()) as client:
            await client.call_tool("create_dashboard", {"title": title, "panels": [_PANEL]})
            r = await client.call_tool(
                "add_esql_metric_panel",
                {
                    "dashboard_id": did, "title": "esql total",
                    "esql": f"FROM {FLIGHTS} | STATS total = COUNT(*)", "column": "total",
                },
            )
        assert r.data["id"] == did
        roundtrip, warnings = gateway.get_dashboard_data(did)
        assert warnings == []
        esql_panels = [
            p for p in roundtrip["panels"] if p.get("config", {}).get("data_source", {}).get("type") == "esql"
        ]
        assert len(esql_panels) == 1
        assert esql_panels[0]["config"]["metrics"][0]["column"] == "total"
    finally:
        _delete_if_exists(gateway, did)


# --- saved_objects export/import (#37): handle-based round-trip + confinement ---


def test_saved_objects_export_import_roundtrip_live(gateway, tmp_path):
    # export the flights data view -> NDJSON on disk (handle) -> import as a clone.
    dv = next(v for v in gateway.list_data_views() if v.index_pattern == FLIGHTS)
    body = gateway.export_saved_objects(None, [{"type": "index-pattern", "id": dv.id}], False)
    assert isinstance(body, list) and any(o.get("id") == dv.id for o in body)
    handle = write_export(tmp_path, to_ndjson(body))
    result = gateway.import_saved_objects(read_export(tmp_path, handle), False)  # clone
    created = []
    try:
        assert result.success is True
        assert result.imported_count >= 1
        clone = next(o for o in result.objects if o.type == "index-pattern")
        assert clone.destination_id and clone.destination_id != dv.id  # regenerated id (a clone)
        created = [o.destination_id for o in result.objects]
    finally:
        for cid in created:
            _swallow(lambda: gateway.delete_data_view(cid))


def _swallow(fn):
    try:
        fn()
    except Exception:
        pass


def test_read_export_confines_to_export_dir(tmp_path):
    # The security boundary holds against traversal/absolute handles.
    for bad in ("../etc/passwd", "/etc/passwd", "so-../../x", "so-not-hex-000"):
        with pytest.raises(ValueError):
            read_export(tmp_path, bad)


# --- #52: esql table + xy chart types (round-trip + structural validation) ---

_ESQL_CT_Q = f"FROM {FLIGHTS} | STATS count = COUNT(*), avg_price = AVG(AvgTicketPrice) BY Carrier"


def test_esql_table_and_xy_panels_roundtrip_live(gateway):
    table = esql_table_config("esql table", _ESQL_CT_Q, ["Carrier"], ["count", "avg_price"])
    xy = esql_xy_config("esql bar", _ESQL_CT_Q, "bar", "Carrier", ["count"], "avg_price")
    data = build_dashboard_data(
        "mcp-for-kibana esql charttypes contract", "esql table+xy",
        layout_panels([table, xy]), {"from": "now-7d", "to": "now"},
    )
    dash_id = gateway.create_dashboard(data)
    try:
        roundtrip, warnings = gateway.get_dashboard_data(dash_id)
        assert warnings == []  # Kibana accepts + normalizes both esql panels
        assert {p["config"]["type"] for p in roundtrip["panels"]} == {"data_table", "xy"}
        for p in roundtrip["panels"]:
            cfg = p["config"]
            ds = cfg.get("data_source") or cfg["layers"][0]["data_source"]
            assert ds["type"] == "esql"
    finally:
        gateway.delete_dashboard(dash_id)


def test_malformed_esql_config_is_rejected_live(gateway):
    # The structural validation that makes the round-trip a meaningful check: a
    # data_table with neither metrics nor rows, and an xy with no layers, are
    # both rejected on write (not silently stored).
    bad_table = {"type": "data_table", "title": "bad", "data_source": {"type": "esql", "query": _ESQL_CT_Q}}
    bad_xy = {"type": "xy", "title": "bad", "data_source": {"type": "esql", "query": _ESQL_CT_Q}}
    for bad in (bad_table, bad_xy):
        data = build_dashboard_data("mcp-for-kibana esql bad", "", layout_panels([bad]), None)
        with pytest.raises(KibanaRejected):
            gateway.create_dashboard(data)


# --- platform-admin: spaces + roles + upgrade readiness (read-first, live) ---


def test_list_spaces_contains_default_live(gateway):
    spaces = gateway.list_spaces()
    by_id = {s.id: s for s in spaces}
    assert "default" in by_id
    assert by_id["default"].reserved is True  # the default space is _reserved


def test_get_space_default_live(gateway):
    s = gateway.get_space("default")
    assert s.id == "default"
    assert s.name  # non-empty
    assert s.solution  # the default space returns a solution (e.g. 'es')
    assert isinstance(s.disabled_features, tuple)
    assert s.reserved is True


def test_get_space_missing_raises_live(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.get_space("does-not-exist-space-xyz")


def test_list_roles_has_reserved_system_role_live(gateway):
    roles = gateway.list_roles()
    assert roles  # a fresh stack ships many reserved roles
    assert any(r.reserved for r in roles)
    by_name = {r.name: r for r in roles}
    assert "kibana_system" in by_name


def test_get_role_kibana_system_live(gateway):
    r = gateway.get_role("kibana_system")
    assert r.name == "kibana_system"
    assert r.reserved is True  # metadata._reserved
    # Shape-only: kibana_system's exact privilege set is Elastic-controlled and can
    # shift between minors — assert a non-empty tuple of strings, not a pinned name.
    assert r.cluster_privileges
    assert all(isinstance(p, str) for p in r.cluster_privileges)


def test_get_role_missing_raises_live(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.get_role("does-not-exist-role-xyz")


def test_get_upgrade_status_shape_live(gateway):
    # readyForUpgrade + deprecation counts vary with cluster usage; assert the
    # SHAPE only (never exact counts — they flake as the stack is exercised).
    u = gateway.get_upgrade_status()
    assert isinstance(u.ready_for_upgrade, bool)
    assert isinstance(u.es_deprecation_count, int)
    assert u.es_deprecation_count >= 0
    assert isinstance(u.api_deprecations, tuple)
    for dep in u.api_deprecations:
        assert isinstance(dep.title, str)
        assert isinstance(dep.level, str)


# --- platform-admin write/destructive: spaces + roles CRUD (live) ---


def test_create_update_delete_space_live(gateway):
    space_id = f"mcp-for-kibana-ct-{uuid.uuid4().hex[:8]}"
    created = gateway.create_space(
        space_id, "mcp-for-kibana ct space", "created by a contract test", None, None,
        ["uptime"], None,
    )
    try:
        assert created.id == space_id
        assert created.disabled_features == ("uptime",)
        # THE RMW authority (live T1): updating ONLY the name must preserve the
        # previously-set disabled_features (the gateway re-sends the current
        # camelCase disabledFeatures when the arg is omitted).
        updated = gateway.update_space(space_id, "renamed ct space", None, None, None, None, None)
        assert updated.name == "renamed ct space"
        assert updated.disabled_features == ("uptime",)  # NOT wiped by the partial update
    finally:
        gateway.delete_space(space_id, force=True)  # always force-gated
    with pytest.raises(KibanaNotFound):
        gateway.get_space(space_id)


def test_create_or_update_role_es_kibana_roundtrip_live(gateway):
    name = f"mcp-for-kibana-ct-role-{uuid.uuid4().hex[:8]}"
    gateway.create_or_update_role(
        name, ["monitor"], [{"names": ["logs-*"], "privileges": ["read"]}],
        ["read"], ["*"], "created by a contract test", True,
    )
    try:
        r = gateway.get_role(name)
        assert r.name == name
        assert "monitor" in r.cluster_privileges
        assert r.index_privileges[0].names == ("logs-*",)
        assert r.index_privileges[0].privileges == ("read",)
        assert r.kibana_privileges[0].base == ("read",)
        assert r.kibana_privileges[0].spaces == ("*",)
        # create_only=True on an existing role must be refused (no silent full-replace).
        with pytest.raises(KibanaRejected):
            gateway.create_or_update_role(name, ["monitor"], [], None, None, None, True)
    finally:
        gateway.delete_role(name)
    with pytest.raises(KibanaNotFound):
        gateway.get_role(name)


def test_delete_space_default_guard_live(gateway):
    # Client-side guard refuses BEFORE any delete call — the default space is never
    # touched (safe to run against a shared stack).
    with pytest.raises(KibanaRejected):
        gateway.delete_space("default", force=True)


def test_delete_role_reserved_guard_live(gateway):
    with pytest.raises(KibanaRejected):
        gateway.delete_role("kibana_system")


def test_create_or_update_role_refuses_reserved_live(gateway):
    # A deliberate overwrite (create_only=False) of a reserved role is refused
    # client-side BEFORE any PUT — kibana_system's grants are never touched.
    with pytest.raises(KibanaRejected):
        gateway.create_or_update_role("kibana_system", ["monitor"], [], None, None, None, False)


def _platform_admin_tool_server():
    # Registers ALL tiers (register() bypasses tier gating) so the destructive
    # delete_* tools are reachable for self-cleanup.
    server = FastMCP("contract-platform-admin")
    PlatformAdminToolbox().register(
        server,
        ToolboxDeps(
            gateway_factory=lambda space=None: KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space
            ),
            public_kibana_url="http://kb:5601",
        ),
    )
    return server


async def test_create_or_update_role_tool_maps_index_privileges_live(gateway):
    # Drives the MCP TOOL surface: the tool builds es indices from the IndexPrivilege
    # model, so a wiring bug (wrong key mapping) would fail the round-trip here.
    name = f"mcp-for-kibana-ct-tool-role-{uuid.uuid4().hex[:8]}"
    async with Client(_platform_admin_tool_server()) as client:
        try:
            r = await client.call_tool("create_or_update_role", {
                "name": name, "cluster_privileges": ["monitor"],
                "index_privileges": [{"names": ["metrics-*"], "privileges": ["read", "view_index_metadata"]}],
                "kibana_base": ["read"], "kibana_spaces": ["*"],
            })
            assert r.data["name"] == name
            fetched = gateway.get_role(name)
            assert fetched.index_privileges[0].names == ("metrics-*",)
            assert set(fetched.index_privileges[0].privileges) == {"read", "view_index_metadata"}
        finally:
            await client.call_tool("delete_role", {"name": name})
    with pytest.raises(KibanaNotFound):
        gateway.get_role(name)


# --- streams: stream reads (read-first, Tech-Preview API, live) ---


def test_list_streams_contains_wired_root_live(gateway):
    streams = gateway.list_streams()
    by_name = {s.name: s for s in streams}
    assert "logs.ecs" in by_name  # a wired root stream ships on this stack
    assert by_name["logs.ecs"].type == "wired"
    # Classic apm streams appear only while APM data is ingesting and age out
    # (env-research P0/P14), so their presence is NOT required here; the classic
    # shape is verified deterministically by the golden unit test. Assert only
    # that any classic streams that happen to be present are typed correctly.
    for s in streams:
        assert s.type in {"wired", "classic", "query"}


def test_get_stream_wired_live(gateway):
    s = gateway.get_stream("logs.ecs")
    assert s.name == "logs.ecs"
    assert s.type == "wired"
    assert s.field_count > 0  # a wired stream has a managed field schema
    assert isinstance(s.lifecycle, str)


def test_get_stream_ingest_wired_has_fields_live(gateway):
    # Shape-only: a wired stream has a non-empty name->type field map. The exact
    # field set is Tech-Preview + stream-specific, so assert the shape, not names.
    ing = gateway.get_stream_ingest("logs.ecs")
    assert ing.fields  # non-empty for a wired stream
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in ing.fields.items())


def test_get_stream_classic_shape_live(gateway):
    # A classic stream carries ingest.classic{} and no wired.fields. Classic apm
    # streams age out with APM ingest (env-research P0/P14), so assert the shape
    # for any that are present rather than hard-requiring one (the golden unit
    # test is the deterministic authority; this stays green with zero classic
    # streams — no skip, since the DoD gate reads skipped>0 as NO-GO).
    for s in gateway.list_streams():
        if s.type == "classic":
            assert gateway.get_stream(s.name).field_count == 0
            assert gateway.get_stream_ingest(s.name).fields == {}


def test_get_stream_missing_raises_live(gateway):
    with pytest.raises(KibanaNotFound):
        gateway.get_stream("does-not-exist-stream-xyz")


# --- security-detections v2 writes: rule + exception create/delete (live) ---


def test_create_and_delete_detection_rule_live(gateway):
    rule_id = f"mcp-for-kibana-ct-rule-{uuid.uuid4().hex[:8]}"
    rule = gateway.create_detection_rule(
        name="mcp-for-kibana contract rule", description="created by a contract test",
        query="*:*", index=["logs-*"], severity="low", risk_score=21,
        rule_id=rule_id, tags=["mcp-for-kibana-ct"], interval="5m", language="kuery",
        enabled=False,  # enabling is privilege-gated; a new rule stays inactive
    )
    try:
        assert rule.rule_id == rule_id
        assert rule.type == "query"
        assert rule.enabled is False
        # it is now findable by its stable id
        assert gateway.get_detection_rule(rule_id, None).name == "mcp-for-kibana contract rule"
    finally:
        gateway.delete_detection_rule(rule_id, None)
    # gone after delete
    with pytest.raises(KibanaNotFound):
        gateway.get_detection_rule(rule_id, None)


def test_create_and_delete_exception_list_and_item_live(gateway):
    list_id = f"mcp-for-kibana-ct-exc-{uuid.uuid4().hex[:8]}"
    item_id = f"mcp-for-kibana-ct-item-{uuid.uuid4().hex[:8]}"
    el = gateway.create_exception_list(
        name="mcp-for-kibana contract exceptions", description="ct", type="detection",
        list_id=list_id, namespace_type="single", tags=["mcp-for-kibana-ct"],
    )
    try:
        assert el.list_id == list_id
        item = gateway.create_exception_item(
            list_id=list_id, name="allow trusted host", description="ct",
            entries=[{"field": "host.name", "operator": "included", "type": "match", "value": "trusted"}],
            item_id=item_id, namespace_type="single", tags=[],
        )
        assert item.item_id == item_id
        # the item is listed under its container
        assert any(i.item_id == item_id for i in gateway.find_exception_items(list_id))
        gateway.delete_exception_item(None, item_id, "single")
    finally:
        gateway.delete_exception_list(None, list_id, "single")
    # container gone
    with pytest.raises(KibanaNotFound):
        gateway.get_exception_list(None, list_id)


def test_delete_detection_rule_exactly_one_guard_live(gateway):
    with pytest.raises(KibanaRejected):
        gateway.delete_detection_rule(None, None)


def _sec_tool_server():
    # Registers ALL tiers (register() bypasses build_server's tier gating), so the
    # delete_* destructive tools are reachable for self-cleanup.
    server = FastMCP("contract-sec")
    SecurityDetectionsToolbox().register(
        server,
        ToolboxDeps(
            gateway_factory=lambda space=None: KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space
            ),
            public_kibana_url="http://kb:5601",
        ),
    )
    return server


async def test_create_detection_rule_tool_live(gateway):
    # Drives the MCP TOOL surface (not just the gateway) end-to-end vs real Kibana.
    rule_id = f"mcp-for-kibana-ct-tool-rule-{uuid.uuid4().hex[:8]}"
    async with Client(_sec_tool_server()) as client:
        try:
            r = await client.call_tool("create_detection_rule", {
                "name": "mcp-for-kibana tool rule", "description": "ct", "query": "*:*",
                "index": ["logs-*"], "severity": "medium", "risk_score": 42,
                "rule_id": rule_id, "tags": ["mcp-for-kibana-ct"],
            })
            assert r.data["rule_id"] == rule_id
            assert r.data["type"] == "query"
            assert r.data["enabled"] is False
        finally:
            await client.call_tool("delete_detection_rule", {"rule_id": rule_id})
    with pytest.raises(KibanaNotFound):
        gateway.get_detection_rule(rule_id, None)


async def test_create_exception_item_tool_maps_entry_type_live(gateway):
    # The regression guard: the tool builds the entry from ExceptionEntry (which
    # has no `type`), so if the toolbox omitted type:"match" Kibana would 400 here.
    list_id = f"mcp-for-kibana-ct-tool-exc-{uuid.uuid4().hex[:8]}"
    async with Client(_sec_tool_server()) as client:
        try:
            await client.call_tool("create_exception_list", {
                "name": "mcp-for-kibana tool exceptions", "description": "ct", "list_id": list_id,
            })
            item = await client.call_tool("create_exception_item", {
                "list_id": list_id, "name": "allow trusted", "description": "ct",
                "entries": [{"field": "host.name", "value": "trusted", "operator": "included"}],
            })
            assert item.data["list_id"] == list_id
            # Kibana accepted the toolbox-built entry -> it is retrievable.
            assert any(i.item_id == item.data["item_id"] for i in gateway.find_exception_items(list_id))
        finally:
            await client.call_tool("delete_exception_list", {"list_id": list_id})
    with pytest.raises(KibanaNotFound):
        gateway.get_exception_list(None, list_id)


def _dm_tool_server(export_dir):
    server = FastMCP("contract-dm")
    DataManagementToolbox().register(
        server,
        ToolboxDeps(
            gateway_factory=lambda space=None: KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space
            ),
            public_kibana_url="http://kb:5601",
            export_dir=export_dir,
        ),
    )
    return server


async def test_export_overwrite_restores_in_place_tool_live(gateway, tmp_path):
    # Drives export -> overwrite_saved_objects through the MCP TOOL surface vs real
    # Kibana. Isolated + self-cleaning: a throwaway data view, exported, restored
    # in place (destination id == source id), then deleted.
    async with Client(_dm_tool_server(tmp_path)) as client:
        created = await client.call_tool(
            "create_data_view",
            {"index_pattern": f"mcp-for-kibana-ct-{uuid.uuid4().hex[:8]}-*", "name": "ct-overwrite"},
        )
        view_id = created.data["id"]
        try:
            exp = await client.call_tool(
                "export_saved_objects", {"objects": [{"type": "index-pattern", "id": view_id}]}
            )
            imp = await client.call_tool("overwrite_saved_objects", {"handle": exp.data["handle"]})
            assert imp.data["success"] is True
            restored = [o for o in imp.data["objects"] if o["source_id"] == view_id]
            assert restored, "the exported data view should be among the restored objects"
            # in-place restore: destination id == source id (not a clone)
            assert restored[0]["destination_id"] == view_id
        finally:
            await client.call_tool("delete_data_view", {"view_id": view_id})


# --- streams: write/destructive tier (live; Tech-Preview API) ---
# Serial-execution assumption: the delta routing assertions read logs.ecs's
# shared routing_count; run the contract suite serially (the default — no xdist).


@pytest.fixture()
def ephemeral_child(gateway):
    name = f"logs.ecs.mcpc{uuid.uuid4().hex[:8]}"
    yield name
    try:
        gateway.delete_stream(name, force=True)  # finalizer: tolerant of absence
    except KibanaNotFound:
        pass


def test_fork_set_retention_delete_roundtrip_live(gateway, ephemeral_child):
    before = gateway.get_stream("logs.ecs").routing_count
    gateway.fork_stream("logs.ecs", ephemeral_child, "service.name", "mcp-contract")
    assert gateway.get_stream("logs.ecs").routing_count == before + 1  # delta (P3)
    assert gateway.get_stream(ephemeral_child).type == "wired"
    ing = gateway.set_stream_retention(ephemeral_child, "7d")
    assert ing.lifecycle == "dsl" and ing.data_retention == "7d"
    gateway.delete_stream(ephemeral_child, force=False)
    assert gateway.get_stream("logs.ecs").routing_count == before  # cascade removed the rule (P7)
    with pytest.raises(KibanaNotFound):
        gateway.get_stream(ephemeral_child)


def test_enable_streams_idempotent_live(gateway):
    # Self-contained: ensure the wired framework is enabled first (a fresh stack
    # returns 'created' on the first enable), then assert a SECOND enable is the
    # idempotent no-op — the actual property under test, independent of stack state.
    gateway.enable_streams()
    assert gateway.enable_streams().result == "noop"


def test_resync_streams_live(gateway):
    assert gateway.resync_streams().result == "updated"


def test_delete_root_refused_live(gateway):
    with pytest.raises(KibanaRejected):
        gateway.delete_stream("logs.ecs", force=False)


def test_delete_parent_with_children_refused_live(gateway):
    parent = f"logs.ecs.mcpp{uuid.uuid4().hex[:8]}"
    child = f"{parent}.gc"
    gateway.fork_stream("logs.ecs", parent, "service.name", "mcp-a")
    gateway.fork_stream(parent, child, "service.name", "mcp-b")
    try:
        with pytest.raises(KibanaRejected):
            gateway.delete_stream(parent, force=False)  # children guard
    finally:
        gateway.delete_stream(child, force=True)
        gateway.delete_stream(parent, force=True)


# --- streams: #71 follow-ups — activate/deactivate fork + processing edit (live) ---


def test_activate_deactivate_fork_routing_status_live(gateway):
    gateway.enable_streams()  # idempotent (a fresh stack returns 'created', not asserted here)
    child = f"logs.ecs.sw71{uuid.uuid4().hex[:8]}"
    gateway.fork_stream("logs.ecs", child, "service.name", "sw71")
    try:
        # The gateway methods take no confirm — that's the toolbox layer (D3);
        # call the gateway directly and re-read the PARENT's raw ingest body to
        # inspect the routing entry the RMW helper flips (StreamIngest only
        # surfaces counts, not the entries themselves).
        gateway.activate_fork(parent="logs.ecs", child=child)
        ingest = gateway._client.streams.get_ingest(name="logs.ecs").body["ingest"]
        entry = next((r for r in ingest["wired"]["routing"] if r.get("destination") == child), None)
        assert entry is not None, f"'{child}' missing from logs.ecs routing after activate_fork"
        assert entry["status"] == "enabled"

        gateway.deactivate_fork(parent="logs.ecs", child=child)
        ingest = gateway._client.streams.get_ingest(name="logs.ecs").body["ingest"]
        entry = next((r for r in ingest["wired"]["routing"] if r.get("destination") == child), None)
        assert entry is not None, f"'{child}' missing from logs.ecs routing after deactivate_fork"
        assert entry["status"] == "disabled"
    finally:
        gateway.delete_stream(child, force=True)


def test_set_stream_processing_adds_step_live(gateway):
    gateway.enable_streams()  # idempotent
    child = f"logs.ecs.sw71{uuid.uuid4().hex[:8]}"
    gateway.fork_stream("logs.ecs", child, "service.name", "sw71")
    try:
        gateway.set_stream_processing(
            name=child, steps=[{"action": "set", "to": "sw71.marker", "value": "1"}]
        )
        ingest = gateway._client.streams.get_ingest(name=child).body["ingest"]
        assert any(s.get("to") == "sw71.marker" for s in ingest["processing"]["steps"]), ingest
    finally:
        gateway.delete_stream(child, force=True)


# --- security-detections write extras (#60): update rule + value lists (live) ---


@pytest.fixture(scope="module", autouse=True)
def _preclean_contract_value_list(gateway):
    # A deterministic-id value-list roundtrip runs below; force-clean a leak from a
    # crashed prior run BEFORE test_value_lists_empty_live (defined earlier in this
    # file) can trip over it. Tolerant of absence.
    try:
        gateway.delete_value_list("mcp-contract-vl", force=True)
    except KibanaNotFound:
        pass
    yield


def _delete_rule_if_exists(gateway, rule_id):
    try:
        gateway.delete_detection_rule(rule_id, None)
    except KibanaNotFound:
        pass


def test_update_detection_rule_preserves_omitted_live(gateway):
    rid = f"mcp-ct-upd-{uuid.uuid4().hex[:8]}"
    _delete_rule_if_exists(gateway, rid)
    gateway.create_detection_rule(
        name="ct upd", description="orig desc", query="host.name:foo", index=["logs-*"],
        severity="low", risk_score=21, rule_id=rid, tags=["ct-a"], interval="5m",
        language="kuery", enabled=False)
    try:
        # patch NAME only -> other create-time fields must survive (PATCH is partial)
        gateway.update_detection_rule(rid, None, "ct upd renamed", None, None, None, None, None, None)
        got = gateway.get_detection_rule(rid, None)
        assert got.name == "ct upd renamed"
        assert got.risk_score == 21 and got.tags == ("ct-a",) and got.severity == "low"
        assert got.enabled is False
        # query/description aren't on the DTO -> read the raw body to prove they survived
        raw = gateway._client.detection_engine.get_rule(rule_id=rid).body
        assert raw["query"] == "host.name:foo" and raw["description"] == "orig desc"
        # patch query + description + interval + severity + risk_score + tags -> applied; name preserved
        gateway.update_detection_rule(
            rid, None, None, "new desc", ["ct-b"], "high", 70, "host.name:bar", "10m")
        got2 = gateway.get_detection_rule(rid, None)
        assert got2.severity == "high" and got2.risk_score == 70 and got2.tags == ("ct-b",)
        assert got2.name == "ct upd renamed"  # omitted this time -> preserved
        # the non-DTO fields (query/description/interval) all applied — read the raw body
        raw2 = gateway._client.detection_engine.get_rule(rule_id=rid).body
        assert raw2["query"] == "host.name:bar" and raw2["description"] == "new desc"
        assert raw2["interval"] == "10m"
    finally:
        gateway.delete_detection_rule(rid, None)


def test_value_list_create_delete_roundtrip_live(gateway):
    vid = "mcp-contract-vl"  # deterministic + pre-cleaned (module fixture) so a leak can't
    try:                     # break test_value_lists_empty_live; uuid can't be pre-cleaned
        gateway.delete_value_list(vid, force=True)
    except KibanaNotFound:
        pass
    gateway.create_value_list("ct list", "d", "keyword", vid)
    try:
        assert any(v.id == vid for v in gateway.find_value_lists())
    finally:
        try:
            gateway.delete_value_list(vid, force=True)  # tolerant finalizer
        except KibanaNotFound:
            pass
    assert not any(v.id == vid for v in gateway.find_value_lists())


def test_delete_value_list_force_semantics_live(gateway):
    # The whole point of `force`: a referenced value list refuses a plain delete (409),
    # and force=True overrides it (env-research P6). Prove BOTH against live Kibana.
    vid, elid = "mcp-contract-vl-ref", "mcp-contract-el-ref"
    try:
        gateway.delete_value_list(vid, force=True)
    except KibanaNotFound:
        pass
    try:
        gateway.delete_exception_list(None, elid, "single")
    except KibanaNotFound:
        pass
    gateway.create_value_list("ref target", "d", "keyword", vid)
    try:
        gateway.create_exception_list("holder", "d", "detection", elid, "single", [])
        gateway.create_exception_item(
            elid, "ref item", "references the value list",
            [{"field": "host.name", "operator": "included", "type": "list",
              "list": {"id": vid, "type": "keyword"}}],
            None, "single", [])
        with pytest.raises(KibanaRejected):  # referenced -> 409 blocks the plain delete
            gateway.delete_value_list(vid, force=False)
        gateway.delete_value_list(vid, force=True)  # force overrides (orphans the reference)
        assert not any(v.id == vid for v in gateway.find_value_lists())
    finally:
        try:
            gateway.delete_value_list(vid, force=True)
        except KibanaNotFound:
            pass
        try:
            gateway.delete_exception_list(None, elid, "single")
        except KibanaNotFound:
            pass


# --- security-detections write follow-ups (#73 task 5): RMW replace,
# enable/disable, value-list items (live) ---


def test_value_list_item_crud_live(gateway):
    vid = f"sd73-ct-vl-{uuid.uuid4().hex[:8]}"
    gateway.create_value_list("sd73 ct vl", "d", "keyword", vid)
    try:
        item = gateway.create_value_list_item(list_id=vid, value="probe-val")
        assert item.list_id == vid
        assert item.value == "probe-val"
        assert any(
            i.id == item.id and i.value == "probe-val"
            for i in gateway.find_value_list_items(list_id=vid)
        )
        gateway.delete_value_list_item(item_id=item.id)
        assert not any(i.id == item.id for i in gateway.find_value_list_items(list_id=vid))
    finally:
        gateway.delete_value_list(vid, force=True)


def test_replace_detection_rule_preserves_omitted_live(gateway):
    # THE RMW proof (SD-P2/P6): a bare update_rule PUT is a full replace that
    # wipes anything omitted. replace_detection_rule must echo interval/tags/
    # from (the get_rule "from" -> update_rule "from_" translation included)
    # while applying only the caller's changes.
    rid = f"sd73-ct-rmw-{uuid.uuid4().hex[:8]}"
    created = gateway._client.detection_engine.create_rule(
        type="query", name="sd73 ct rmw", description="orig desc", severity="low",
        risk_score=21, query="*:*", index=["logs-*"], language="kuery",
        enabled=False, interval="10m", tags=["orig"], rule_id=rid, from_="now-30m",
    ).body
    uid = created["id"]
    try:
        gateway.replace_detection_rule(rule_id="", id=uid, changes={"name": "renamed"})
        raw = gateway._client.detection_engine.get_rule(id=uid).body
        assert raw["interval"] == "10m"
        assert raw["tags"] == ["orig"]
        assert raw["from"] == "now-30m"
        assert raw["name"] == "renamed"
    finally:
        gateway.delete_detection_rule(None, uid)


def test_enable_disable_detection_rule_live(gateway):
    rid = f"sd73-ct-endis-{uuid.uuid4().hex[:8]}"
    rule = gateway.create_detection_rule(
        name="sd73 ct endis", description="ct", query="*:*", index=["logs-*"],
        severity="low", risk_score=21, rule_id=rid, tags=["sd73"], interval="5m",
        language="kuery", enabled=False,
    )
    uid = rule.id
    try:
        gateway.enable_detection_rule(rule_id="", id=uid)
        assert gateway._client.detection_engine.get_rule(id=uid).body["enabled"] is True
        gateway.disable_detection_rule(rule_id="", id=uid)
        assert gateway._client.detection_engine.get_rule(id=uid).body["enabled"] is False
    finally:
        gateway.delete_detection_rule(None, uid)


# --- space targeting: scoped gateways against a real Kibana ---
# Resource convention (the file's own): a uuid-suffixed space per test,
# force-deleted in a finally block — deleting the space removes every object
# created inside it, so per-object teardown is unnecessary; KibanaNotFound is
# tolerated so a failure before creation surfaces its own error.


def _live_gateway(space=None):
    """A fresh live gateway, optionally scoped to `space` (validated on connect)."""
    return KibanaPyGateway.connect(
        os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space
    )


@contextlib.contextmanager
def _temp_space(gateway, prefix="mcp-for-kibana-ct-space"):
    space_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
    gateway.create_space(
        space_id, f"mcp-for-kibana {space_id}", "created by a contract test",
        None, None, None, None,
    )
    try:
        yield space_id
    finally:
        try:
            gateway.delete_space(space_id, force=True)  # force: the space is non-empty
        except KibanaNotFound:
            pass


def test_scoped_gateway_full_chain_live(gateway):
    # create space -> scoped connect -> data view -> dashboard -> scoped search.
    # The default-space gateway sees none of it: the isolation that makes P7's
    # silent orphan-write failure mode observable.
    marker = uuid.uuid4().hex[:8]
    title = f"mcp-for-kibana scoped chain {marker}"
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            created = gw.create_data_view(FLIGHTS, f"scoped-flights-{marker}", "timestamp")
            assert created.index_pattern == FLIGHTS
            detail = gw.get_data_view(FLIGHTS)  # resolves by index pattern INSIDE the space
            assert detail.id == created.id
            assert detail.time_field == "timestamp"
            assert detail.fields["AvgTicketPrice"] == "number"  # fields resolve through /s/<id>
            assert not any(v.id == created.id for v in gateway.list_data_views())
            dash_id = gw.create_dashboard(
                build_dashboard_data(title, "created by a contract test", [], None)
            )
            assert [d.id for d in gw.search_dashboards(marker)] == [dash_id]
            assert gw.get_dashboard(dash_id).title == title
        assert gateway.search_dashboards(marker) == []  # invisible from the default space


def test_scoped_connect_nonexistent_space_guidance():
    # Fail-closed at construction (P7: Kibana itself would have accepted the write
    # into an invisible orphan namespace).
    with pytest.raises(KibanaNotFound, match="not found — check what exists"):
        _live_gateway(f"no-such-space-{uuid.uuid4().hex[:8]}")


def test_scoped_not_found_message_shape(gateway):
    # kibana-py prefixes "[Space: <id>] " on errors from scoped paths; the adapter
    # appends its own " (in space '<id>')". Assert OUR suffix and tolerate (not
    # require) the vendor prefix — hence `in`/`endswith`, never equality.
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            with pytest.raises(KibanaNotFound) as exc:
                gw.get_dashboard("no-such-dashboard-id-xyz")
    assert exc.value.message.endswith(f" (in space '{space_id}')")
    assert "no-such-dashboard-id-xyz" in exc.value.message


def test_twin_safety_scoped_delete(gateway):
    """A delete aimed at the wrong space can never reach another space's dashboard.

    Live correction to the twin setup: a dashboard id is GLOBALLY unique (the
    saved-object type is multiple-isolated), so the same derived id cannot exist
    in two spaces at once — Kibana 409s the second create, asserted below. The
    safety property is therefore pinned in the form reality permits: neither the
    sibling space nor the default space can see or delete the dashboard, and only
    its own space's delete removes it.
    """
    marker = uuid.uuid4().hex[:8]
    title = f"mcp-for-kibana twin {marker}"
    dash_id = derive_dashboard_id(title)
    data = build_dashboard_data(title, "created by a contract test", [], None)
    with _temp_space(gateway, "mcp-for-kibana-ct-twin-a") as space_a:
        with _temp_space(gateway, "mcp-for-kibana-ct-twin-b") as space_b:
            with _live_gateway(space_a) as gw_a, _live_gateway(space_b) as gw_b:
                gw_a.upsert_dashboard(dash_id, data)
                with pytest.raises(KibanaRejected):  # ids are global: no same-id twin
                    gw_b.upsert_dashboard(dash_id, data)
                with pytest.raises(KibanaNotFound):  # sibling space cannot delete it
                    gw_b.delete_dashboard(dash_id)
                with pytest.raises(KibanaNotFound):  # nor can the default space
                    gateway.delete_dashboard(dash_id)
                assert gw_a.get_dashboard(dash_id).title == title  # survived both
                gw_a.delete_dashboard(dash_id)  # only the owning space's delete removes it
                with pytest.raises(KibanaNotFound):
                    gw_a.get_dashboard(dash_id)


def test_visualization_and_short_url_scoped_live(gateway):
    # The scoped namespaces P8 did not probe: visualizations and short_urls.
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            viz_id = gw.create_visualization(to_lens_config(flights_spec()))
            assert viz_id
            short = gw.create_short_url("LEGACY_SHORT_URL_LOCATOR", {"url": "/app/dashboards"})
            assert short.id and short.slug
            assert gw.resolve_short_url(short.slug).id == short.id  # resolves inside the space
            with pytest.raises(KibanaNotFound):
                gateway.resolve_short_url(short.slug)  # the slug lives in the space only
            gw.delete_short_url(short.id)
            gw.delete_visualization(viz_id)


def test_cross_space_import_clones(gateway, tmp_path):
    # export from space a -> import into space b WITHOUT overwrite = a clone:
    # every id is regenerated and space a keeps its originals.
    marker = uuid.uuid4().hex[:8]
    title = f"mcp-for-kibana clone {marker}"
    with _temp_space(gateway, "mcp-for-kibana-ct-clone-a") as space_a:
        with _temp_space(gateway, "mcp-for-kibana-ct-clone-b") as space_b:
            with _live_gateway(space_a) as gw_a, _live_gateway(space_b) as gw_b:
                dv = gw_a.create_data_view(
                    f"{FLIGHTS}-clone-{marker}", f"clone-dv-{marker}", "timestamp"
                )
                dash_id = gw_a.create_dashboard(
                    build_dashboard_data(title, "created by a contract test", [], None)
                )
                body = gw_a.export_saved_objects(
                    None,
                    [{"type": "index-pattern", "id": dv.id}, {"type": "dashboard", "id": dash_id}],
                    True,  # include_references_deep — the tool's default
                )
                handle = write_export(tmp_path, to_ndjson(body))
                result = gw_b.import_saved_objects(read_export(tmp_path, handle), False)
                assert result.success is True
                assert result.imported_count >= 2
                by_type = {o.type: o for o in result.objects}
                assert set(by_type) == {"index-pattern", "dashboard"}
                for obj in result.objects:
                    assert obj.destination_id and obj.destination_id != obj.source_id
                assert gw_b.get_dashboard(by_type["dashboard"].destination_id).title == title
                assert any(
                    v.id == by_type["index-pattern"].destination_id for v in gw_b.list_data_views()
                )
                assert gw_a.get_dashboard(dash_id).title == title  # source space untouched
                assert any(v.id == dv.id for v in gw_a.list_data_views())


def test_cross_space_overwrite_replaces_twin(gateway, tmp_path):
    """A cross-space overwrite replaces the earlier restore in place, never duplicating.

    Live correction: because ids are globally unique, a cross-space restore cannot
    land under the SOURCE id (space a still holds it) — the first overwrite mints a
    destination id in space b, and the second overwrite replaces THAT object
    (matched by originId), leaving exactly one copy. `destination_id == source_id`
    holds only for a same-space restore, which
    test_export_overwrite_restores_in_place_tool_live already pins.
    """
    marker = uuid.uuid4().hex[:8]
    title = f"mcp-for-kibana overwrite {marker}"
    with _temp_space(gateway, "mcp-for-kibana-ct-ovw-a") as space_a:
        with _temp_space(gateway, "mcp-for-kibana-ct-ovw-b") as space_b:
            with _live_gateway(space_a) as gw_a, _live_gateway(space_b) as gw_b:
                dash_id = gw_a.create_dashboard(
                    build_dashboard_data(title, "created by a contract test", [], None)
                )
                body = gw_a.export_saved_objects(
                    None, [{"type": "dashboard", "id": dash_id}], True
                )
                handle = write_export(tmp_path, to_ndjson(body))
                first = gw_b.import_saved_objects(read_export(tmp_path, handle), True)
                (created,) = [o for o in first.objects if o.type == "dashboard"]
                assert created.source_id == dash_id
                assert created.destination_id != dash_id  # source id is taken by space a
                second = gw_b.import_saved_objects(read_export(tmp_path, handle), True)
                (replaced,) = [o for o in second.objects if o.type == "dashboard"]
                assert replaced.destination_id == created.destination_id  # in place, not a copy
                twins = [d for d in gw_b.search_dashboards(marker) if d.title == title]
                assert len(twins) == 1
                assert gw_a.get_dashboard(dash_id).title == title  # source untouched


# --- space extension: alerting + cases + security-detections scoped live ---
# Same convention as the space section above: the _temp_space force-delete
# wipes every saved object created inside the space, so per-object teardown is
# unnecessary. Value lists are the one exception — their backing
# .lists-<space> index is ES-level, so that test deletes its list explicitly.


def test_alerting_rule_scoped_lifecycle_live(gateway):
    # A rule created in a space is invisible to the root gateway AND to a
    # sibling space; scoped get resolves it; scoped not-found carries the
    # adapter's space suffix (OUR suffix only, via endswith — see the
    # message-shape test above for why never equality).
    with _temp_space(gateway, "mcp-for-kibana-ct-alrt-a") as space_a:
        with _temp_space(gateway, "mcp-for-kibana-ct-alrt-b") as space_b:
            with _live_gateway(space_a) as gw_a, _live_gateway(space_b) as gw_b:
                rule = gw_a.create_alert_rule(
                    f"contract-esq-scoped-{uuid.uuid4().hex[:8]}", ".es-query", "stackAlerts",
                    "1m", _ESQ_PARAMS, ["contract"], False,  # enabled=False: inert
                )
                assert rule.id and rule.enabled is False and rule.rule_type_id == ".es-query"
                assert not any(
                    r.id == rule.id for r in gateway.list_alert_rules("contract-esq-scoped")
                )
                assert not any(
                    r.id == rule.id for r in gw_b.list_alert_rules("contract-esq-scoped")
                )
                assert gw_a.get_alert_rule(rule.id).id == rule.id
                with pytest.raises(KibanaNotFound) as exc:
                    gw_a.get_alert_rule("no-such-rule-id-xyz")
                assert exc.value.message.endswith(f" (in space '{space_a}')")


def test_alerting_connector_scoped_execute_live(gateway):
    # .server-log only writes to the Kibana log — no external side effect.
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            conn = gw.create_connector(
                f"contract-log-scoped-{uuid.uuid4().hex[:8]}", ".server-log", None, None
            )
            assert conn.id and conn.connector_type_id == ".server-log"
            result = gw.execute_connector(
                conn.id, {"message": "mcp-for-kibana scoped contract test", "level": "info"}
            )
            assert result["status"] == "ok"
            # Assert the ID's absence, never list emptiness: preconfigured
            # (kibana.yml) connectors are instance-global and appear in every
            # space, so the root list need not be empty.
            assert not any(c.id == conn.id for c in gateway.list_connectors())


def test_case_scoped_lifecycle_live(gateway):
    marker = f"mcp-for-kibana scoped case {uuid.uuid4().hex[:8]}"
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            case = gw.create_case(marker, "created by a contract test", ["contract"], "low")
            assert case.id and case.status == "open" and case.severity == "low"
            assert not any(c.id == case.id for c in gateway.list_cases(marker))
            assert gw.get_case(case.id).title == marker


def test_detection_rule_scoped_live(gateway):
    rid = f"mcp-for-kibana-ct-sp-rule-{uuid.uuid4().hex[:8]}"
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            rule = gw.create_detection_rule(
                name="mcp-for-kibana scoped rule", description="created by a contract test",
                query="*:*", index=["logs-*"], severity="low", risk_score=21,
                rule_id=rid, tags=["mcp-for-kibana-ct"], interval="5m", language="kuery",
                enabled=False,  # enabling is privilege-gated; a new rule stays inactive
            )
            assert rule.rule_id == rid
            assert not any(r.rule_id == rid for r in gateway.find_detection_rules())
            assert gw.get_detection_rule(rid, None).rule_id == rid
            with pytest.raises(KibanaNotFound) as exc:
                gw.get_detection_rule("no-such-rule-id-xyz", None)
            assert exc.value.message.endswith(f" (in space '{space_id}')")


def test_value_list_first_write_in_fresh_space_live(gateway):
    # Pins the per-space backing-index auto-creation: the FIRST value-list
    # write in a brand-new space must create .lists-<space>/.items-<space>
    # itself (_ensure_value_list_index) — nothing pre-seeds a fresh space.
    vid = f"mcp-for-kibana-ct-sp-vl-{uuid.uuid4().hex[:8]}"
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            created = gw.create_value_list("scoped ct list", "d", "keyword", vid)
            try:
                assert created.id == vid
                assert any(v.id == vid for v in gw.find_value_lists())
            finally:
                # The .lists-<space> index is ES-level: deleting the space
                # won't reach it, so remove the list via the scoped gateway.
                gw.delete_value_list(vid, force=True)


def test_search_detection_alerts_uninitialized_space_live(gateway):
    # Measured on 9.4.3: in a fresh space, where the detection engine has
    # never initialized its per-space alerts index, the search returns an
    # empty list — not a 404.
    with _temp_space(gateway) as space_id:
        with _live_gateway(space_id) as gw:
            assert gw.search_detection_alerts(1) == []
