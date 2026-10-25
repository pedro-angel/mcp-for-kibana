"""The authority on payload shapes: every golden translation must be accepted
by a real Kibana on each supported line. If a test here fails while unit goldens pass, fix
translate.py AND the golden test together."""

import asyncio
import contextlib
import os
import uuid

import kibana
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

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
from kibana_mcp.toolboxes.alerting.toolbox import AlertingToolbox
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from kibana_mcp.toolboxes.data_management.toolbox import DataManagementToolbox
from kibana_mcp.toolboxes.observability.toolbox import ObservabilityToolbox
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


async def test_date_histogram_without_time_field_auto_fills_live(gateway):
    # The efficiency study's first-call shape (90/90 runs): a date_histogram
    # panel that omits time_field. Kibana must accept the auto-filled panel
    # and round-trip it losslessly.
    title = "contract date histogram auto time field"
    did = derive_dashboard_id(title)
    _delete_if_exists(gateway, did)
    panel = {
        "title": "Flights over time", "chart_type": "line", "data_view": FLIGHTS,
        "metrics": [{"agg": "count"}],
        "group_by": [{"field": "timestamp", "kind": "date_histogram"}],
    }
    try:
        async with Client(_tool_server()) as client:
            r = await client.call_tool("create_dashboard", {"title": title, "panels": [panel]})
        assert r.data["status"] == "created"
        data, warnings = gateway.get_dashboard_data(did)
        assert warnings == []
        layer = data["panels"][0]["config"]["layers"][0]
        assert layer["data_source"]["time_field"] == "timestamp"
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


def test_create_data_view_time_field_guard_live(gateway):
    # Kibana's data-view create stores any time field — flights has no
    # `@timestamp`, yet the create succeeds (probed on 9.5.4) — and no public
    # route lists a pattern's fields first, so the adapter checks the fields
    # the create returns and deletes the view on a mismatch.
    pattern = _dv_pattern()
    name = f"ct-create-dv {uuid.uuid4().hex[:8]}"
    with pytest.raises(KibanaRejected) as exc:
        gateway.create_data_view(pattern, name, "@timestamp")
    assert exc.value.message == (
        f"time field '@timestamp' is not a field of '{pattern}' — pass time_field as one "
        "of its date fields: timestamp (no data view was created)"
    )
    with pytest.raises(KibanaRejected, match=r"is a string field, not a date field"):
        gateway.create_data_view(pattern, name, "Carrier")
    assert not any(v.name == name or v.index_pattern == pattern
                   for v in gateway.list_data_views())  # nothing left behind
    created = gateway.create_data_view(pattern, name, "timestamp")
    try:
        # A second create under the same name is Kibana's duplicate 400, which
        # names the view that holds the name and update_data_view.
        with pytest.raises(KibanaRejected) as dup:
            gateway.create_data_view(pattern, name, None)
        assert dup.value.detail == (
            f"Duplicate data view: {name} — a data view named '{name}' (id '{created.id}') "
            f"already exists: use it, change it in place with "
            f"update_data_view(view_id='{created.id}'), or pass a different name"
        )
    finally:
        gateway.delete_data_view(created.id)


def _dv_pattern():
    # Unique (no two runs share a title) yet carries the flights fields: the
    # second, never-matching member keeps it unique.
    return f"{FLIGHTS},mcp-for-kibana-ct-{uuid.uuid4().hex[:8]}-*"


def test_update_data_view_partial_and_time_field_guard_live(gateway):
    # Kibana's data-view POST merges what it is sent but checks nothing: it
    # stores a missing or non-date time field (probed on 9.5.4). The adapter
    # guards that, before the write or, when the pattern changes, by writing
    # the pattern back.
    pattern = _dv_pattern()
    created = gateway.create_data_view(pattern, "ct-update-dv", "timestamp")
    try:
        renamed = gateway.update_data_view(created.id, "ct-update-dv renamed", None, None)
        assert (renamed.name, renamed.index_pattern, renamed.time_field) == (
            "ct-update-dv renamed", pattern, "timestamp"
        )
        assert renamed.fields["AvgTicketPrice"] == "number"
        with pytest.raises(KibanaRejected) as exc:
            gateway.update_data_view(created.id, None, None, "Carrier")
        assert exc.value.message.startswith(
            f"time field 'Carrier' is a string field, not a date field, of '{pattern}' — "
            "pass time_field as one of its date fields: timestamp"
        )
        other = _dv_pattern()
        with pytest.raises(KibanaRejected, match=r"\(nothing was changed\)$"):
            gateway.update_data_view(created.id, None, other, "Carrier")
        with _raw_kibana() as raw:
            kept = raw.data_views.get(view_id=created.id).body["data_view"]
        assert (kept["title"], kept["timeFieldName"], kept["name"]) == (
            pattern, "timestamp", "ct-update-dv renamed"
        )
        # A pattern that matches no index yet is accepted; its fields are empty.
        future = f"mcp-for-kibana-ct-{uuid.uuid4().hex[:8]}-*"
        moved = gateway.update_data_view(created.id, None, future, None)
        assert (moved.index_pattern, moved.time_field, moved.fields) == (future, "timestamp", {})
        back = gateway.update_data_view(created.id, None, pattern, "timestamp")
        assert back.index_pattern == pattern and back.fields["timestamp"] == "date"
        with pytest.raises(KibanaNotFound, match=r"call list_data_views to find it"):
            gateway.update_data_view("ct-update-dv renamed", "x", None, None)
    finally:
        gateway.delete_data_view(created.id)


def test_data_view_edit_leaves_built_panels_alone_live(gateway):
    # Panels this server builds embed the pattern and time field inline
    # (data_view_spec), not the data view's id, so an edit does not reach them.
    pattern = _dv_pattern()
    created = gateway.create_data_view(pattern, "ct-panel-dv", "timestamp")
    data = build_dashboard_data(
        f"mcp-for-kibana ct dv edit {uuid.uuid4().hex[:8]}", "created by a contract test",
        layout_panels([to_lens_config(flights_spec(data_view=pattern))]), None,
    )
    dash_id = gateway.create_dashboard(data)
    try:
        before, _ = gateway.get_dashboard_data(dash_id)
        gateway.update_data_view(created.id, "ct-panel-dv renamed", _dv_pattern(), None)
        after, warnings = gateway.get_dashboard_data(dash_id)
        assert after == before and warnings == []
    finally:
        gateway.delete_dashboard(dash_id)
        gateway.delete_data_view(created.id)


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


def test_list_rule_types_live(gateway):
    types = {t.id: t for t in gateway.list_rule_types()}
    es = types[".es-query"]  # the type create_alert_rule's lifecycle test uses
    assert "stackAlerts" in es.consumers
    assert es.enabled_in_license is True and es.minimum_license_required == "basic"
    # a Basic-licence stack lists types it cannot create, flagged rather than hidden
    assert any(not t.enabled_in_license for t in types.values())


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


@contextlib.contextmanager
def _raw_kibana(space=None):
    """A raw kibana-py client for reading Kibana's stored state (a rule's full
    body) apart from the mapping under test: the assertion side only, never the
    code path."""
    client = kibana.Kibana(os.environ["KIBANA_URL"], api_key=os.environ["KIBANA_TEST_API_KEY"])
    try:
        yield client.space(space) if space else client
    finally:
        client.close()


def test_update_alert_rule_preserves_what_it_was_not_given_live(gateway):
    # PUT /api/alerting/rule/{id} wipes omitted actions/tags and rejects omitted
    # params (probed on 9.5.4); the RMW must keep all three, keep `enabled`,
    # and merge params key-by-key.
    conn = gateway.create_connector(f"contract-log-{uuid.uuid4().hex[:8]}", ".server-log", None, None)
    rule = gateway.create_alert_rule(
        f"contract-esq-upd-{uuid.uuid4().hex[:8]}", ".es-query", "stackAlerts", "1m",
        _ESQ_PARAMS, ["contract"], False,
    )
    action = {
        "id": conn.id, "group": "query matched", "params": {"message": "ct {{rule.name}}"},
        "frequency": {"summary": False, "notify_when": "onActionGroupChange", "throttle": None},
    }
    try:
        gateway.update_alert_rule(rule.id, None, None, None, None, [action])
        with _raw_kibana() as raw:
            before = raw.alerting.rule.get(id=rule.id).body
        assert [a["id"] for a in before["actions"]] == [conn.id]
        gateway.enable_alert_rule(rule.id)

        updated = gateway.update_alert_rule(rule.id, None, None, None, {"threshold": [5]}, None)
        with _raw_kibana() as raw:
            after = raw.alerting.rule.get(id=rule.id).body
        assert after["params"]["threshold"] == [5]
        assert after["params"]["size"] == 100 and after["params"]["index"] == [FLIGHTS]  # merged
        assert [a["uuid"] for a in after["actions"]] == [a["uuid"] for a in before["actions"]]
        assert after["tags"] == ["contract"] and after["name"] == rule.name
        assert updated.enabled is True and after["enabled"] is True  # never toggled

        renamed = gateway.update_alert_rule(rule.id, "contract renamed", ["x", "y"], "5m", None, None)
        assert (renamed.name, renamed.tags, renamed.schedule_interval) == (
            "contract renamed", ("x", "y"), "5m"
        )
        cleared = gateway.update_alert_rule(rule.id, None, [], None, None, [])
        assert cleared.tags == ()
        with _raw_kibana() as raw:
            assert raw.alerting.rule.get(id=rule.id).body["actions"] == []
    finally:
        gateway.delete_alert_rule(rule.id)
        gateway.delete_connector(conn.id)


def test_update_alert_rule_per_action_frequency_replaces_rule_level_timing_live(gateway):
    # Kibana refuses per-action frequency while the rule carries a rule-level
    # notify_when ("Cannot specify per-action frequency params when notify_when
    # or throttle are defined at the rule level", probed on 9.5.4): actions
    # that bring a frequency must move the rule to per-action timing.
    conn = gateway.create_connector(f"contract-log-{uuid.uuid4().hex[:8]}", ".server-log", None, None)
    with _raw_kibana() as raw:  # the adapter never writes rule-level timing itself
        rule_id = raw.alerting.rule.create(
            name=f"contract-esq-legacy-{uuid.uuid4().hex[:8]}", consumer="stackAlerts",
            rule_type_id=".es-query", schedule={"interval": "1m"}, params=_ESQ_PARAMS,
            tags=["contract"], enabled=False, notify_when="onActiveAlert", throttle=None,
            actions=[{"id": conn.id, "group": "query matched", "params": {"message": "a"}}],
        ).body["id"]
    try:
        gateway.update_alert_rule(rule_id, "contract kept timing", None, None, None, None)
        with _raw_kibana() as raw:
            assert raw.alerting.rule.get(id=rule_id).body["notify_when"] == "onActiveAlert"
        freq = {"summary": False, "notify_when": "onActionGroupChange", "throttle": None}
        gateway.update_alert_rule(rule_id, None, None, None, None, [{
            "id": conn.id, "group": "query matched", "params": {"message": "b"}, "frequency": freq,
        }])
        with _raw_kibana() as raw:
            after = raw.alerting.rule.get(id=rule_id).body
        assert after["notify_when"] is None and after["throttle"] is None
        assert after["actions"][0]["frequency"] == freq
    finally:
        gateway.delete_alert_rule(rule_id)
        gateway.delete_connector(conn.id)


def test_update_alert_rule_rejections_name_the_fix_live(gateway):
    with pytest.raises(KibanaNotFound, match=r"call list_alert_rules to find it"):
        gateway.update_alert_rule("no-such-rule-id-xyz", "x", None, None, None, None)
    rule = gateway.create_alert_rule(
        f"contract-esq-rej-{uuid.uuid4().hex[:8]}", ".es-query", "stackAlerts", "1m",
        _ESQ_PARAMS, ["contract"], False,
    )
    try:
        with pytest.raises(KibanaRejected) as exc:
            gateway.update_alert_rule(rule.id, None, None, "banana", None, None)
        assert exc.value.message == "Kibana rejected the payload"
        assert "not a valid duration" in (exc.value.detail or "")
    finally:
        gateway.delete_alert_rule(rule.id)


def test_connector_get_and_update_live(gateway):
    # .index: the Basic type with REQUIRED config — a PUT without config 400s,
    # so a rename that keeps the connector valid proves the config RMW.
    index = f"contract-idx-{uuid.uuid4().hex[:8]}"
    conn = gateway.create_connector(
        f"contract-idx-{uuid.uuid4().hex[:8]}", ".index", {"index": index, "refresh": False}, None
    )
    try:
        got = gateway.get_connector(conn.id)
        assert got.connector_type_id == ".index" and got.config["index"] == index
        assert got.is_preconfigured is False and got.is_missing_secrets is False
        renamed = gateway.update_connector(conn.id, "contract renamed idx", None, None)
        assert renamed.name == "contract renamed idx" and renamed.config["index"] == index
        merged = gateway.update_connector(conn.id, None, {"refresh": True}, None)
        assert merged.config["refresh"] is True and merged.config["index"] == index
        assert merged.name == "contract renamed idx"
        assert gateway.get_connector(conn.id).config == merged.config
        with pytest.raises(KibanaRejected) as exc:  # .index has no secrets schema
            gateway.update_connector(conn.id, None, None, {"token": "x"})
        assert exc.value.message == "Kibana rejected the payload"
        with pytest.raises(KibanaNotFound, match=r"call list_connectors to find it"):
            gateway.get_connector("no-such-connector-xyz")
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


def _delete_viz_if_exists(gateway, viz_id):
    if viz_id is None:
        return
    with contextlib.suppress(KibanaNotFound):
        gateway.delete_visualization(viz_id)


def test_library_visualization_read_update_delete_live(gateway):
    # The gateway half of D37: search finds by title word, get reads the stored
    # config back in VizSpec terms, update replaces in place and refuses an id
    # that matches nothing (Kibana's PUT would have created it), delete names
    # the fix for a miss.
    marker = uuid.uuid4().hex[:8]
    viz_id = None
    try:
        viz_id = gateway.create_visualization(
            to_lens_config(flights_spec(title=f"ct library {marker}"))
        )
        assert [v.id for v in gateway.search_visualizations(marker)] == [viz_id]
        found = gateway.search_visualizations(marker)[0]
        assert (found.title, found.chart_type) == (f"ct library {marker}", "bar")
        got = gateway.get_visualization(viz_id)
        assert (got.chart_type, got.data_view, got.time_field) == ("bar", FLIGHTS, "timestamp")
        assert got.metrics == ({"agg": "average", "field": "AvgTicketPrice"},)
        assert got.group_by == ({"field": "Carrier", "kind": "terms", "limit": 10},)
        line = flights_spec(
            title=f"ct library {marker} v2", chart_type=ChartType.LINE,
            metrics=[MetricSpec(agg=MetricAgg.COUNT)],
            group_by=[GroupBySpec(field="timestamp", kind="date_histogram")],
        )
        gateway.update_visualization(viz_id, to_lens_config(line))
        after = gateway.get_visualization(viz_id)
        assert (after.id, after.title, after.chart_type) == (viz_id, f"ct library {marker} v2", "line")
        stray = f"ct-no-such-viz-{marker}"
        with pytest.raises(KibanaNotFound, match="call search_visualizations to find it"):
            gateway.update_visualization(stray, to_lens_config(line))
        with pytest.raises(KibanaNotFound):
            gateway.get_visualization(stray)  # the refused update created nothing
        gateway.delete_visualization(viz_id)
        with pytest.raises(KibanaNotFound, match="call search_visualizations to find it"):
            gateway.delete_visualization(viz_id)
        viz_id = None
    finally:
        _delete_viz_if_exists(gateway, viz_id)


async def test_library_panel_keeps_the_dashboard_editable_live(gateway):
    # D37 on the live stack, through the MCP tools: a by-reference panel
    # round-trips with zero warnings, the other dashboards tools keep working
    # around it, update_panel refuses it naming update_visualization, an edit to
    # the library item reaches the panel, and deleting the item (which Kibana
    # allows) leaves a dangling panel the tools can still edit around and remove.
    marker = uuid.uuid4().hex[:8]
    title = f"ct library panel {marker}"
    dash_id = derive_dashboard_id(title)
    viz_id = None
    _delete_if_exists(gateway, dash_id)
    try:
        async with Client(_tool_server()) as client:
            viz_id = (await client.call_tool("create_visualization", {
                "spec": dict(_PANEL, title=f"ct library viz {marker}"),
            })).data["id"]
            found = (await client.call_tool("search_visualizations", {"query": marker})).data
            assert [v["id"] for v in found] == [viz_id]
            await client.call_tool("create_dashboard", {"title": title, "panels": [_PANEL]})
            placed = (await client.call_tool(
                "add_library_panel", {"dashboard_id": dash_id, "visualization_id": viz_id}
            )).data
            assert placed["panel_index"] == 1
            data, warnings = gateway.get_dashboard_data(dash_id)
            assert warnings == []  # a by-reference panel round-trips losslessly
            assert data["panels"][1]["config"] == {"ref_id": viz_id}

            await client.call_tool("add_panel", {"dashboard_id": dash_id, "panel": _PANEL})
            data, warnings = gateway.get_dashboard_data(dash_id)
            assert warnings == [] and len(data["panels"]) == 3
            assert data["panels"][1]["config"] == {"ref_id": viz_id}  # kept through the RMW

            with pytest.raises(ToolError, match="change it with update_visualization"):
                await client.call_tool("update_panel", {
                    "dashboard_id": dash_id, "panel_index": 1, "panel": _PANEL,
                })
            assert gateway.get_dashboard_data(dash_id)[0]["panels"][1]["config"] == {
                "ref_id": viz_id
            }

            await client.call_tool("update_visualization", {
                "visualization_id": viz_id, "spec": dict(_PANEL, title=f"ct renamed {marker}"),
            })
            panels = (await client.call_tool("get_dashboard", {"dashboard_id": dash_id})).data[
                "panels"
            ]
            assert panels[1] == {
                "index": 1, "type": "vis", "title": f"ct renamed {marker}",
                "visualization_id": viz_id,
            }
            assert "visualization_id" not in panels[0]

            await client.call_tool("delete_visualization", {"visualization_id": viz_id})
            deleted_id, viz_id = viz_id, None
            data, warnings = gateway.get_dashboard_data(dash_id)
            assert warnings == []
            assert data["panels"][1]["config"] == {"ref_id": deleted_id}  # dangling, kept
            panels = (await client.call_tool("get_dashboard", {"dashboard_id": dash_id})).data[
                "panels"
            ]
            assert panels[1]["title"] == "" and panels[1]["visualization_id"] == deleted_id
            await client.call_tool("add_panel", {"dashboard_id": dash_id, "panel": _PANEL})
            await client.call_tool("delete_panel", {"dashboard_id": dash_id, "panel_index": 1})
            data, warnings = gateway.get_dashboard_data(dash_id)
            assert warnings == [] and len(data["panels"]) == 3
            assert all("ref_id" not in (p.get("config") or {}) for p in data["panels"])

            with pytest.raises(ToolError, match=r"visualization '.+' not found"):
                await client.call_tool(  # a ref Kibana would store is refused first
                    "add_library_panel",
                    {"dashboard_id": dash_id, "visualization_id": deleted_id},
                )
    finally:
        _delete_viz_if_exists(gateway, viz_id)
        _delete_if_exists(gateway, dash_id)


async def test_library_tools_scoped_live(gateway):
    # The five tools in a space: results echo it, the dashboard URL carries
    # /s/<id>, the library item is invisible from the default space, and a miss
    # carries the scoped suffix once.
    marker = uuid.uuid4().hex[:8]
    title = f"ct scoped library {marker}"
    dash_id = derive_dashboard_id(title)
    with _temp_space(gateway, "mcp-for-kibana-ct-lib") as space_id:
        with _live_gateway(space_id) as gw:
            gw.create_data_view(FLIGHTS, f"ct-flights-{marker}", "timestamp")
        async with Client(_tool_server()) as client:
            viz_id = (await client.call_tool("create_visualization", {
                "spec": dict(_PANEL, title=f"ct scoped viz {marker}"), "space": space_id,
            })).data["id"]
            found = (await client.call_tool(
                "search_visualizations", {"query": marker, "space": space_id}
            )).data
            assert [v["id"] for v in found] == [viz_id] and "space" not in found[0]
            assert (await client.call_tool("search_visualizations", {"query": marker})).data == []
            got = (await client.call_tool(
                "get_visualization", {"visualization_id": viz_id, "space": space_id}
            )).data
            assert got["space"] == space_id and got["chart_type"] == "bar"
            with pytest.raises(ToolError, match=r"call search_visualizations to find it$"):
                await client.call_tool("get_visualization", {"visualization_id": viz_id})
            await client.call_tool(
                "create_dashboard", {"title": title, "panels": [_PANEL], "space": space_id}
            )
            placed = (await client.call_tool("add_library_panel", {
                "dashboard_id": dash_id, "visualization_id": viz_id, "space": space_id,
            })).data
            assert placed["space"] == space_id
            assert f"/s/{space_id}/app/dashboards#/view/{dash_id}" in placed["url"]
            updated = (await client.call_tool("update_visualization", {
                "visualization_id": viz_id, "spec": dict(_PANEL, title=f"ct v2 {marker}"),
                "space": space_id,
            })).data
            assert updated == {"id": viz_id, "title": f"ct v2 {marker}", "space": space_id}
            with pytest.raises(ToolError) as miss:
                await client.call_tool(
                    "delete_visualization",
                    {"visualization_id": f"ct-no-such-{marker}", "space": space_id},
                )
            assert str(miss.value).endswith(f" (in space '{space_id}')")
            assert str(miss.value).count("(in space") == 1
            deleted = (await client.call_tool(
                "delete_visualization", {"visualization_id": viz_id, "space": space_id}
            )).data
            assert deleted == {"id": viz_id, "deleted": True, "space": space_id}


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


def _alerting_tool_server():
    server = FastMCP("contract-alerting")
    AlertingToolbox().register(
        server,
        ToolboxDeps(
            gateway_factory=lambda space=None: KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space
            ),
            public_kibana_url="http://kb:5601",
        ),
    )
    return server


async def test_alerting_maintenance_tools_scoped_live(gateway):
    # Through the MCP surface in a space: edits land in that space only, dict
    # results echo it, a miss carries the suffix once, and no secrets field
    # ever appears.
    with _temp_space(gateway, "mcp-for-kibana-ct-alrt-upd") as space_id:
        async with Client(_alerting_tool_server()) as client:
            conn = (await client.call_tool("create_connector", {
                "name": "contract scoped log", "connector_type_id": ".server-log",
                "space": space_id,
            })).data
            rule = (await client.call_tool("create_alert_rule", {
                "name": "contract scoped rule", "rule_type_id": ".es-query",
                "consumer": "stackAlerts", "params": _ESQ_PARAMS, "space": space_id,
            })).data
            upd_rule = (await client.call_tool("update_alert_rule", {
                "rule_id": rule["id"], "name": "contract scoped renamed",
                "params": {"threshold": [3]}, "space": space_id,
            })).data
            got = (await client.call_tool(
                "get_connector", {"connector_id": conn["id"], "space": space_id}
            )).data
            upd_conn = (await client.call_tool("update_connector", {
                "connector_id": conn["id"], "name": "contract scoped renamed log",
                "space": space_id,
            })).data
            with pytest.raises(ToolError) as miss:
                await client.call_tool(
                    "get_connector", {"connector_id": "no-such-connector-xyz", "space": space_id}
                )
        assert upd_rule["name"] == "contract scoped renamed" and upd_rule["space"] == space_id
        assert upd_rule["enabled"] is False
        assert got["space"] == space_id and got["connector_type_id"] == ".server-log"
        assert upd_conn["name"] == "contract scoped renamed log" and upd_conn["space"] == space_id
        assert "secrets" not in got and "secrets" not in upd_conn
        assert str(miss.value).endswith(f" (in space '{space_id}')")
        assert str(miss.value).count("(in space") == 1
        with pytest.raises(KibanaNotFound):  # invisible from the root space
            gateway.get_connector(conn["id"])
        with pytest.raises(KibanaNotFound):
            gateway.update_alert_rule(rule["id"], "x", None, None, None, None)


async def test_get_alert_rule_reads_params_before_and_after_a_threshold_fix_live(gateway):
    # The read-modify-write pair through the MCP surface: get_alert_rule shows
    # the stored params (Kibana's defaults included) and actions, update merges
    # a threshold into them, and get_alert_rule shows the change. The result is
    # built from named keys: no secret and no bookkeeping key (probed on 9.5.4,
    # GET returns api_key_owner, never the rule's API key).
    conn = gateway.create_connector(f"contract-log-{uuid.uuid4().hex[:8]}", ".server-log", None, None)
    action = {
        "id": conn.id, "group": "query matched", "params": {"message": "ct {{rule.name}}"},
        "frequency": {"summary": False, "notify_when": "onActionGroupChange", "throttle": None},
    }
    rid = None
    try:
        async with Client(_alerting_tool_server()) as client:
            rid = (await client.call_tool("create_alert_rule", {
                "name": f"contract-esq-get-{uuid.uuid4().hex[:8]}", "rule_type_id": ".es-query",
                "consumer": "stackAlerts", "params": {**_ESQ_PARAMS, "threshold": [10000]},
                "tags": ["contract"],
            })).data["id"]
            await client.call_tool("update_alert_rule", {"rule_id": rid, "actions": [action]})
            await client.call_tool("enable_alert_rule", {"rule_id": rid})
            before = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
            await client.call_tool(
                "update_alert_rule", {"rule_id": rid, "params": {"threshold": [100]}}
            )
            after_call = await client.call_tool("get_alert_rule", {"rule_id": rid})
            after = after_call.data
            with _raw_kibana() as raw:
                stored = raw.alerting.rule.get(id=rid).body
            # D40 amendment (probed on 9.5.4): the read uuid, passed back, keeps
            # the action's identity; an action sent without one gets a new uuid.
            await client.call_tool(
                "update_alert_rule", {"rule_id": rid, "actions": after["actions"]}
            )
            with _raw_kibana() as raw:
                kept = [a["uuid"] for a in raw.alerting.rule.get(id=rid).body["actions"]]
            await client.call_tool("update_alert_rule", {"rule_id": rid, "actions": [action]})
            with _raw_kibana() as raw:
                minted = [a["uuid"] for a in raw.alerting.rule.get(id=rid).body["actions"]]
        assert before["params"]["threshold"] == [10000]
        assert before["params"]["aggType"] == "count"  # Kibana's default, read back
        assert [a["uuid"] for a in after["actions"]] == [a["uuid"] for a in stored["actions"]]
        assert [{k: v for k, v in a.items() if k != "uuid"} for a in before["actions"]] == [action]
        assert before["actions"] == after["actions"]  # a params-only edit keeps the uuid
        assert kept == [a["uuid"] for a in after["actions"]]
        assert minted != kept  # the premise for returning uuid
        assert after["params"] == {**before["params"], "threshold": [100]}
        assert after["params"] == stored["params"]
        assert after["enabled"] is True and after["tags"] == ["contract"]
        assert set(after) == {
            "id", "name", "rule_type_id", "consumer", "enabled", "schedule_interval",
            "status", "tags", "params", "actions", "muted", "muted_alert_ids",
        }
        assert stored.get("api_key_owner")  # an enabled rule has a key; it never comes back
        assert os.environ["KIBANA_TEST_API_KEY"] not in str(after_call.content)
    finally:
        if rid is not None:
            gateway.delete_alert_rule(rid)
        gateway.delete_connector(conn.id)


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


async def test_update_data_view_tool_scoped_live(gateway, tmp_path):
    # Through the MCP surface in a space: the edit lands there only, the result
    # echoes the space, and a miss carries the scoped suffix once.
    with _temp_space(gateway, "mcp-for-kibana-ct-dv-upd") as space_id:
        async with Client(_dm_tool_server(tmp_path)) as client:
            created = (await client.call_tool("create_data_view", {
                "index_pattern": FLIGHTS, "name": "ct scoped dv", "space": space_id,
            })).data
            updated = (await client.call_tool("update_data_view", {
                "view_id": created["id"], "name": "ct scoped dv renamed",
                "time_field": "timestamp", "space": space_id,
            })).data
            with pytest.raises(ToolError) as miss:
                await client.call_tool(
                    "update_data_view",
                    {"view_id": "no-such-dv-xyz", "name": "x", "space": space_id},
                )
            with pytest.raises(ToolError, match=r"call list_data_views to find it$"):
                await client.call_tool(  # the root space cannot see it
                    "update_data_view", {"view_id": created["id"], "name": "x"}
                )
        assert updated == {
            "id": created["id"], "name": "ct scoped dv renamed", "index_pattern": FLIGHTS,
            "time_field": "timestamp", "field_count": updated["field_count"], "space": space_id,
        }
        assert updated["field_count"] > 0
        assert "call list_data_views to find it" in str(miss.value)
        assert str(miss.value).endswith(f" (in space '{space_id}')")
        assert str(miss.value).count("(in space") == 1


# --- alerting rule lifecycle: mute, snooze, API key, untrack (B1, probed on 9.5.4) ---

# An ungrouped .es-query rule over every flight in ten years: its first run
# (right after enable) raises the one alert such a rule has, "query matched".
_ESQ_FIRING = {**_ESQ_PARAMS, "timeWindowSize": 3650, "timeWindowUnit": "d"}


async def _wait_for_alert(client, rule_id, alert_id, space=None, timeout=120.0):
    """Mute `alert_id` as soon as the rule has raised it: Kibana answers 404
    for an alert the rule has not raised (yet), so the first success is the
    alert existing. Polls, never sleeps a fixed time."""
    args = {"rule_id": rule_id, "alert_id": alert_id, **({"space": space} if space else {})}
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            return (await client.call_tool("mute_alert", args)).data
        except ToolError as e:
            if "not found on alert rule" not in str(e) or (
                asyncio.get_running_loop().time() > deadline
            ):
                raise
            await asyncio.sleep(2)


async def test_mute_tools_through_mcp_live(gateway):
    # The per-alert mute and the rule-wide mute land on the rule Kibana stores
    # and get_alert_rule reads both back. Kibana's rule-wide mute and unmute
    # both clear the per-alert mutes, and on a muted rule it answers a per-alert
    # mute with 204 but records nothing — so mute_alert refuses that case. An
    # alert id the rule never raised is refused with guidance
    # (validate_alerts_existence stays at Kibana's default).
    rid = None
    try:
        async with Client(_alerting_tool_server()) as client:
            rid = (await client.call_tool("create_alert_rule", {
                "name": f"contract-mute-{uuid.uuid4().hex[:8]}", "rule_type_id": ".es-query",
                "consumer": "stackAlerts", "params": _ESQ_FIRING, "enabled": True,
            })).data["id"]
            with pytest.raises(ToolError) as typo:
                await client.call_tool("mute_alert", {"rule_id": rid, "alert_id": "no-such-alert"})
            muted_one = await _wait_for_alert(client, rid, "query matched")
            await client.call_tool(  # repeat: no error, still one entry
                "mute_alert", {"rule_id": rid, "alert_id": "query matched"})
            got_one = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
            unmuted_one = (await client.call_tool(
                "unmute_alert", {"rule_id": rid, "alert_id": "query matched"})).data
            await client.call_tool(  # an alert that is not muted: 204, nothing changes
                "unmute_alert", {"rule_id": rid, "alert_id": "never-muted"})
            await client.call_tool("mute_alert", {"rule_id": rid, "alert_id": "query matched"})
            muted_all = (await client.call_tool("mute_alert_rule", {"rule_id": rid})).data
            await client.call_tool("mute_alert_rule", {"rule_id": rid})  # repeat: no error
            got_all = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
            with _raw_kibana() as raw:
                stored = raw.alerting.rule.get(id=rid).body
            with pytest.raises(ToolError) as on_muted:
                await client.call_tool("mute_alert", {"rule_id": rid, "alert_id": "query matched"})
            unmuted_all = (await client.call_tool("unmute_alert_rule", {"rule_id": rid})).data
            await client.call_tool("mute_alert", {"rule_id": rid, "alert_id": "query matched"})
            # on a rule NOT muted as a whole: still clears, and names what it cleared
            unmuted_none = (await client.call_tool("unmute_alert_rule", {"rule_id": rid})).data
            got_none = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
            for alert_id in unmuted_none["cleared_alert_ids"]:  # restore from the result
                await client.call_tool("mute_alert", {"rule_id": rid, "alert_id": alert_id})
            got_restored = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
            with pytest.raises(ToolError) as missing:
                await client.call_tool("mute_alert_rule", {"rule_id": "no-such-rule"})
        assert "alert 'no-such-alert' not found on alert rule" in str(typo.value)
        assert "'query matched'" in str(typo.value)  # the guidance names the real id
        assert muted_one == {"rule_id": rid, "alert_id": "query matched", "muted": True}
        assert got_one["muted"] is False and got_one["muted_alert_ids"] == ["query matched"]
        assert unmuted_one == {"rule_id": rid, "alert_id": "query matched", "muted": False}
        # the rule-wide mute names the per-alert mute it dropped
        assert muted_all == {"id": rid, "muted": True, "cleared_alert_ids": ["query matched"]}
        assert got_all["muted"] is True and got_all["enabled"] is True  # still running
        # Kibana dropped the per-alert mute when it muted the whole rule
        assert got_all["muted_alert_ids"] == [] and stored["muted_alert_ids"] == []
        assert stored["mute_all"] is True
        assert "is muted as a whole" in str(on_muted.value)
        assert "call unmute_alert_rule first" in str(on_muted.value)
        assert unmuted_all == {"id": rid, "muted": False, "cleared_alert_ids": []}
        # ...and the rule-wide unmute clears a per-alert mute set after it, naming it
        assert unmuted_none == {"id": rid, "muted": False, "cleared_alert_ids": ["query matched"]}
        assert got_none["muted"] is False and got_none["muted_alert_ids"] == []
        assert got_restored["muted_alert_ids"] == ["query matched"]  # restorable
        assert "alert rule 'no-such-rule' not found" in str(missing.value)
        assert "call list_alert_rules to find it" in str(missing.value)
    finally:
        if rid is not None:
            gateway.delete_alert_rule(rid)


async def test_snooze_tools_through_mcp_live(gateway):
    # snooze_alert_rule turns any ISO-8601 instant into the one format Kibana
    # takes, returns the schedule_id unsnooze_alert_rule needs, and the limit of
    # five snoozes (expired ones count) is refused naming the fix.
    rid = None
    try:
        async with Client(_alerting_tool_server()) as client:
            rid = (await client.call_tool("create_alert_rule", {
                "name": f"contract-snooze-{uuid.uuid4().hex[:8]}", "rule_type_id": ".es-query",
                "consumer": "stackAlerts", "params": _ESQ_PARAMS,
            })).data["id"]
            offset = (await client.call_tool("snooze_alert_rule", {
                "rule_id": rid, "duration": "2h", "start": "2030-01-01T02:00:00+02:00",
                "recurring": {"every": "1w", "onWeekDay": ["SA"], "end": "2030-03-01T00:00:00Z"},
            })).data
            now = (await client.call_tool(
                "snooze_alert_rule", {"rule_id": rid, "duration": "30m"})).data
            past = (await client.call_tool(  # an expired window is stored, and counts
                "snooze_alert_rule", {"rule_id": rid, "duration": "1h", "start": "2020-01-01"})).data
            for _ in range(2):
                await client.call_tool("snooze_alert_rule", {"rule_id": rid, "duration": "1d"})
            with pytest.raises(ToolError) as limit:
                await client.call_tool("snooze_alert_rule", {"rule_id": rid, "duration": "1h"})
            gone = (await client.call_tool(
                "unsnooze_alert_rule", {"rule_id": rid, "schedule_id": past["schedule_id"]})).data
            again = (await client.call_tool(  # the freed slot takes a new snooze
                "snooze_alert_rule", {"rule_id": rid, "duration": "1h"})).data
            with pytest.raises(ToolError) as stale:
                await client.call_tool(
                    "unsnooze_alert_rule", {"rule_id": rid, "schedule_id": past["schedule_id"]})
            with pytest.raises(ToolError) as bad_every:
                await client.call_tool("snooze_alert_rule", {
                    "rule_id": rid, "duration": "1h", "recurring": {"every": "1x"}})
            with pytest.raises(ToolError) as bad_unit:
                await client.call_tool("snooze_alert_rule", {"rule_id": rid, "duration": "1w"})
        assert offset["rule_id"] == rid and offset["schedule_id"]
        assert offset["start"] == "2030-01-01T00:00:00.000Z" and offset["timezone"] == "UTC"
        assert offset["recurring"] == {
            "every": "1w", "onWeekDay": ["SA"], "end": "2030-03-01T00:00:00.000Z"}
        assert now["duration"] == "30m" and now["start"].endswith("Z") and now["recurring"] is None
        assert past["start"] == "2020-01-01T00:00:00.000Z"
        assert "already has 5 snooze schedules" in str(limit.value)
        assert "unsnooze_alert_rule" in str(limit.value)
        assert gone == {"rule_id": rid, "schedule_id": past["schedule_id"], "unsnoozed": True}
        assert again["schedule_id"] not in {offset["schedule_id"], past["schedule_id"]}
        assert f"snooze schedule '{past['schedule_id']}' not found on alert rule" in str(stale.value)
        assert "Kibana rejected the payload" in str(bad_every.value)  # Kibana's own check
        assert "'1x'" not in str(bad_unit.value) and "is not a snooze duration" in str(bad_unit.value)
    finally:
        if rid is not None:
            gateway.delete_alert_rule(rid)


async def test_update_alert_rule_api_key_and_untrack_live(gateway):
    # The key refresh keeps the rule as it was (definition, enabled) and returns
    # no key material; disable with untrack=True is accepted and disables, and
    # the default disable request is unchanged (no body).
    rid = None
    try:
        async with Client(_alerting_tool_server()) as client:
            rid = (await client.call_tool("create_alert_rule", {
                "name": f"contract-key-{uuid.uuid4().hex[:8]}", "rule_type_id": ".es-query",
                "consumer": "stackAlerts", "params": _ESQ_PARAMS, "enabled": True,
                "tags": ["contract"],
            })).data["id"]
            with _raw_kibana() as raw:
                before = raw.alerting.rule.get(id=rid).body
            refreshed = await client.call_tool("update_alert_rule_api_key", {"rule_id": rid})
            with _raw_kibana() as raw:
                after = raw.alerting.rule.get(id=rid).body
            disabled = (await client.call_tool(
                "disable_alert_rule", {"rule_id": rid, "untrack": True})).data
            got = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
            with pytest.raises(ToolError) as missing:
                await client.call_tool("update_alert_rule_api_key", {"rule_id": "no-such-rule"})
        assert refreshed.data == {"id": rid, "api_key_updated": True}
        assert os.environ["KIBANA_TEST_API_KEY"] not in str(refreshed.content)
        assert after["enabled"] is True and after["api_key_owner"]
        assert after["updated_at"] > before["updated_at"]  # Kibana recorded the refresh
        for key in ("name", "params", "tags", "schedule", "actions"):
            assert after[key] == before[key], key
        assert disabled == {"id": rid, "enabled": False, "untracked": True}
        assert got["enabled"] is False
        assert "alert rule 'no-such-rule' not found" in str(missing.value)
    finally:
        if rid is not None:
            gateway.delete_alert_rule(rid)


async def test_lifecycle_tools_scoped_live(gateway):
    # In a space: every lifecycle tool lands there and echoes it, the rule is
    # invisible from the root space, and a miss carries the suffix once.
    with _temp_space(gateway, "mcp-for-kibana-ct-alrt-life") as space_id:
        async with Client(_alerting_tool_server()) as client:
            rid = (await client.call_tool("create_alert_rule", {
                "name": "contract scoped lifecycle", "rule_type_id": ".es-query",
                "consumer": "stackAlerts", "params": _ESQ_PARAMS, "space": space_id,
            })).data["id"]
            scoped = {"rule_id": rid, "space": space_id}
            muted = (await client.call_tool("mute_alert_rule", scoped)).data
            unmuted = (await client.call_tool("unmute_alert_rule", scoped)).data
            snoozed = (await client.call_tool(
                "snooze_alert_rule", {**scoped, "duration": "1h"})).data
            unsnoozed = (await client.call_tool(
                "unsnooze_alert_rule", {**scoped, "schedule_id": snoozed["schedule_id"]})).data
            unmuted_alert = (await client.call_tool(
                "unmute_alert", {**scoped, "alert_id": "query matched"})).data
            keyed = (await client.call_tool("update_alert_rule_api_key", scoped)).data
            with pytest.raises(ToolError) as miss:
                await client.call_tool(
                    "unsnooze_alert_rule", {**scoped, "schedule_id": snoozed["schedule_id"]})
            with pytest.raises(ToolError) as root:  # invisible from the root space
                await client.call_tool("mute_alert_rule", {"rule_id": rid})
        for result in (muted, unmuted, snoozed, unsnoozed, unmuted_alert, keyed):
            assert result["space"] == space_id
        assert str(miss.value).endswith(f" (in space '{space_id}')")
        assert str(miss.value).count("(in space") == 1
        assert f"alert rule '{rid}' not found" in str(root.value)



# --- observability: the Synthetics write tier (D46) --------------------------
# Every object is a throwaway this test creates (prefix contract-syn-) and
# removes in `finally`, so the reads above keep seeing an empty Synthetics
# surface. A private location needs a Fleet agent policy no other location uses:
# each test makes its own and deletes it last (that removes the Synthetics
# package policies Kibana adds to it, a test run's included). No agent is
# enrolled in it, so a test run is accepted but does not execute here; D46
# records the probe that saw one execute.


def _obs_tool_server():
    server = FastMCP("contract-observability")
    ObservabilityToolbox().register(
        server,
        ToolboxDeps(
            gateway_factory=lambda space=None: KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space
            ),
            public_kibana_url="http://kb:5601",
        ),
    )
    return server


def _synthetics_cleanup(gateway, *policy_ids):
    with _raw_kibana() as raw:
        for m in raw.synthetics.get_monitors(per_page=100).body["monitors"]:
            if m["name"].startswith("contract-syn-"):
                raw.synthetics.delete_monitor(id=m["config_id"])
        for p in raw.synthetics.get_params().body:
            if p["key"].startswith("contract_syn_"):
                raw.synthetics.delete_param(id=p["id"])
        for loc in raw.synthetics.get_private_locations().body:
            if loc["label"].startswith("contract-syn-"):
                raw.synthetics.delete_private_location(id=loc["id"])
    for policy_id in policy_ids:
        if policy_id is not None:
            gateway.delete_agent_policy(policy_id, force=True)


async def test_synthetic_params_lifecycle_live(gateway):
    # The value goes in and never comes out (Kibana's update echoes the OLD
    # value); a key in use is refused (Kibana itself stores the duplicate); a
    # miss on update or delete names list_synthetic_params.
    key = f"contract_syn_{uuid.uuid4().hex[:8]}"
    secret, rotated = f"s3cret-{uuid.uuid4().hex}", f"rotated-{uuid.uuid4().hex}"
    try:
        async with Client(_obs_tool_server()) as client:
            created = await client.call_tool("create_synthetic_param", {
                "key": key, "value": secret, "description": "contract", "tags": ["contract"],
            })
            with pytest.raises(ToolError) as dup:
                await client.call_tool("create_synthetic_param", {"key": key, "value": "x-1"})
            pid = created.data["id"]
            updated = await client.call_tool("update_synthetic_param", {
                "param_id": pid, "value": rotated, "description": "rotated",
            })
            listed = (await client.call_tool("list_synthetic_params", {})).data
            with pytest.raises(ToolError) as miss:
                await client.call_tool(
                    "update_synthetic_param", {"param_id": "no-such-param", "tags": []})
            deleted = (await client.call_tool("delete_synthetic_param", {"param_id": pid})).data
            with pytest.raises(ToolError) as gone:
                await client.call_tool("delete_synthetic_param", {"param_id": pid})
        assert created.data == {"id": pid, "key": key, "description": "contract",
                                "tags": ["contract"]}
        assert updated.data == {"id": pid, "key": key, "description": "rotated",
                                "tags": ["contract"]}
        for shown in (created.content, updated.content, listed):
            assert secret not in str(shown) and rotated not in str(shown)
        assert f"a parameter with key '{key}' already exists (id '{pid}')" in str(dup.value)
        assert "synthetic param 'no-such-param' not found" in str(miss.value)
        assert "call list_synthetic_params" in str(miss.value)
        assert deleted == {"id": pid, "deleted": True}
        assert f"synthetic param '{pid}' not found" in str(gone.value)
        assert [p for p in gateway.list_synthetic_params() if p.key == key] == []
    finally:
        _synthetics_cleanup(gateway)


def _test_now_policies(gateway, agent_policy_id):
    return [p for p in gateway.list_package_policies()
            if p.agent_policy_id == agent_policy_id
            and p.name == "LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN"]


async def test_synthetic_locations_and_monitors_lifecycle_live(gateway):
    # A private location on a throwaway agent policy, a monitor placed on it by
    # label, an in-place partial edit (tags replaced, not merged), a run request
    # and the package policy it leaves on the agent policy, the refusal to
    # delete a location still in use, the move to a second location that lets
    # the first be deleted, and each delete with its miss.
    tag = uuid.uuid4().hex[:8]
    label, name = f"contract-syn-loc-{tag}", f"contract-syn-mon-{tag}"
    label_b = f"contract-syn-locb-{tag}"
    policy_id = policy_b = None
    try:
        policy_id = gateway.create_agent_policy(
            name=f"contract-syn-policy-{tag}", namespace="default", monitoring_enabled=[]).id
        policy_b = gateway.create_agent_policy(
            name=f"contract-syn-policy-{tag}-b", namespace="default", monitoring_enabled=[]).id
        async with Client(_obs_tool_server()) as client:
            loc = (await client.call_tool("create_synthetic_private_location", {
                "label": label, "agent_policy_id": policy_id, "tags": ["contract"],
            })).data
            with pytest.raises(ToolError) as taken_policy:
                await client.call_tool("create_synthetic_private_location", {
                    "label": f"{label}-2", "agent_policy_id": policy_id})
            with pytest.raises(ToolError) as no_policy:
                await client.call_tool("create_synthetic_private_location", {
                    "label": f"{label}-3", "agent_policy_id": "no-such-agent-policy"})
            renamed = (await client.call_tool("update_synthetic_private_location", {
                "location_id": loc["id"], "label": f"{label}-renamed",
            })).data
            with pytest.raises(ToolError) as no_location:
                await client.call_tool("update_synthetic_private_location", {
                    "location_id": "no-such-location", "label": f"{label}-x"})
            loc_b = (await client.call_tool("create_synthetic_private_location", {
                "label": label_b, "agent_policy_id": policy_b,
            })).data
            created = (await client.call_tool("create_synthetic_monitor", {
                "type": "http", "name": name, "url": "http://kibana:5601/api/status",
                "private_locations": [f"{label}-renamed"], "schedule": "030s",
                "enabled": False, "tags": ["contract"],
            })).data
            with pytest.raises(ToolError) as bad_schedule:
                await client.call_tool("create_synthetic_monitor", {
                    "type": "http", "name": f"{name}-2", "url": "http://x",
                    "private_locations": [loc["id"]], "schedule": "45s"})
            with pytest.raises(ToolError) as dup_name:
                await client.call_tool("create_synthetic_monitor", {
                    "type": "http", "name": name, "url": "http://x",
                    "private_locations": [loc["id"]]})
            edited = (await client.call_tool("update_synthetic_monitor", {
                "monitor_id": created["id"], "schedule": "05m", "tags": ["edited"],
            })).data
            got = (await client.call_tool(
                "get_synthetic_monitor", {"monitor_id": created["id"]})).data
            with pytest.raises(ToolError) as wrong_target:
                await client.call_tool("update_synthetic_monitor", {
                    "monitor_id": created["id"], "host": "kibana:5601"})
            with pytest.raises(ToolError) as no_monitor_read:
                await client.call_tool("update_synthetic_monitor", {
                    "monitor_id": "no-such-monitor", "url": "http://x"})
            before_run = _test_now_policies(gateway, policy_id)
            run = (await client.call_tool(
                "test_synthetic_monitor", {"monitor_id": created["id"]})).data
            after_run = _test_now_policies(gateway, policy_id)
            with pytest.raises(ToolError) as no_monitor:
                await client.call_tool("test_synthetic_monitor", {"monitor_id": "no-such-monitor"})
            with pytest.raises(ToolError) as in_use:
                await client.call_tool(
                    "delete_synthetic_private_location", {"location_id": loc["id"]})
            # The path the in-use refusal names: move the monitor, then delete.
            moved = (await client.call_tool("update_synthetic_monitor", {
                "monitor_id": created["id"], "private_locations": [loc_b["id"]],
            })).data
            loc_deleted = (await client.call_tool(
                "delete_synthetic_private_location", {"location_id": loc["id"]})).data
            after_location = _test_now_policies(gateway, policy_id)
            with pytest.raises(ToolError) as loc_gone:
                await client.call_tool(
                    "delete_synthetic_private_location", {"location_id": loc["id"]})
            deleted = (await client.call_tool(
                "delete_synthetic_monitor", {"monitor_id": created["id"]})).data
            with pytest.raises(ToolError) as monitor_gone:
                await client.call_tool("delete_synthetic_monitor", {"monitor_id": created["id"]})
            loc_b_deleted = (await client.call_tool(
                "delete_synthetic_private_location", {"location_id": loc_b["id"]})).data
        assert loc == {"id": loc["id"], "label": label, "agent_policy_id": policy_id,
                       "is_invalid": False, "tags": ["contract"]}
        assert f"agent policy '{policy_id}' already backs private location" in str(
            taken_policy.value)
        assert "Agent policy with id no-such-agent-policy not found" in str(no_policy.value)
        assert "list_agent_policies shows them" in str(no_policy.value)
        assert renamed == {**loc, "label": f"{label}-renamed"}
        assert "private location 'no-such-location' not found" in str(no_location.value)
        assert "call list_synthetic_private_locations" in str(no_location.value)
        assert created["name"] == name and created["type"] == "http"
        assert created["enabled"] is False and created["schedule"] == "30s"
        assert created["locations"] == [f"{label}-renamed"]
        assert created["target"] == "http://kibana:5601/api/status"
        assert "1, 2, 3, 5, 10, 15, 20, 30, 60, 120 or 240" in str(bad_schedule.value)
        assert "must be unique" in str(dup_name.value)
        # Kibana's PUT is partial: the edit kept everything it did not name, and
        # replaced the tag list ("contract" is gone); "05m" went out as "5m".
        assert edited == got == {**created, "schedule": "5m", "tags": ["edited"]}
        assert "is an http monitor: it takes `url`, not `host`" in str(wrong_target.value)
        assert run["monitor_id"] == created["id"] and uuid.UUID(run["test_run_id"])
        # Each run adds a test-now package policy to the location's agent policy;
        # it outlives the location (Kibana's clean-up task removes it later).
        assert len(after_run) == len(before_run) + 1
        assert {p.id for p in after_location} >= {p.id for p in after_run}
        assert "synthetic monitor 'no-such-monitor' not found" in str(no_monitor.value)
        assert "call list_synthetic_monitors" in str(no_monitor_read.value)
        assert "is used by 1 monitor(s)" in str(in_use.value)
        assert "move those monitors to another private location" in str(in_use.value)
        assert moved == {**edited, "locations": [label_b]}
        assert deleted == {"id": created["id"], "deleted": True}
        assert f"synthetic monitor '{created['id']}' not found" in str(monitor_gone.value)
        assert loc_deleted == {"id": loc["id"], "deleted": True}
        assert f"private location '{loc['id']}' not found" in str(loc_gone.value)
        assert loc_b_deleted == {"id": loc_b["id"], "deleted": True}
        assert gateway.list_synthetic_private_locations() == []
        assert gateway.list_synthetic_monitors() == []
    finally:
        _synthetics_cleanup(gateway, policy_id, policy_b)


# --- observability: the APM agent-configuration writes (D47) -----------------
# Throwaway configs for services named contract-apm-<hex>, removed in `finally`
# so the read above keeps seeing no configuration at all.


def _apm_cleanup(*targets):
    with _raw_kibana() as raw:
        for name, env in targets:
            with contextlib.suppress(kibana.NotFoundError):
                raw.apm.delete_agent_configuration(service_name=name, service_environment=env)


async def test_apm_agent_config_lifecycle_live(gateway):
    # Create (a number sent as a string), a replacement that returns the old map
    # and replaces rather than merges, a value Kibana rejects (its message omits
    # the key; the tool names the settings), the delete with what it removed,
    # and the miss that names list_apm_agent_configs.
    name, env = f"contract-apm-{uuid.uuid4().hex[:8]}", "contract"
    try:
        async with Client(_obs_tool_server()) as client:
            created = (await client.call_tool("upsert_apm_agent_config", {
                "service_name": name, "environment": env,
                "settings": {"transaction_sample_rate": 0.2},
            })).data
            replaced = (await client.call_tool("upsert_apm_agent_config", {
                "service_name": name, "environment": env,
                "settings": {"capture_body": "errors", "recording": False},
            })).data
            with pytest.raises(ToolError) as bad:
                await client.call_tool("upsert_apm_agent_config", {
                    "service_name": name, "environment": env,
                    "settings": {"transaction_sample_rate": "7"},
                })
            with _raw_kibana() as raw:
                stored = raw.apm.get_agent_configuration(name=name, environment=env).body
            deleted = (await client.call_tool("delete_apm_agent_config", {
                "service_name": name, "environment": env})).data
            with pytest.raises(ToolError) as gone:
                await client.call_tool("delete_apm_agent_config", {
                    "service_name": name, "environment": env})
        assert created["created"] is True and created["previous_settings"] is None
        assert created["service_name"] == name and created["service_environment"] == env
        assert created["settings"] == {"transaction_sample_rate": "0.2"}
        assert created["etag"] and created["agent_name"] is None
        assert replaced["created"] is False
        assert replaced["previous_settings"] == {"transaction_sample_rate": "0.2"}
        assert replaced["settings"] == {"capture_body": "errors", "recording": "false"}
        assert "Must be a number between 0.0000 and 1" in str(bad.value)
        assert "settings sent: {'transaction_sample_rate': '7'}" in str(bad.value)
        assert stored["settings"] == {"capture_body": "errors", "recording": "false"}
        assert deleted == {"service_name": name, "environment": env, "deleted": True,
                           "settings": {"capture_body": "errors", "recording": "false"},
                           "agent_name": None}
        assert str(gone.value) == (
            f"no APM agent configuration for service '{name}', environment '{env}' — "
            "service_name and environment must match a configuration exactly (an omitted one "
            "means all); call list_apm_agent_configs to see them")
        with pytest.raises(KibanaNotFound):
            gateway.get_apm_agent_config(name, env)
    finally:
        _apm_cleanup((name, env))


async def test_apm_agent_config_agent_name_round_trip_live(gateway):
    # A config written with agent_name (as Kibana's UI does) keeps it through a
    # tool replacement: Kibana's PUT drops it unless it is sent again (probed);
    # a replacement refuses a different one, which its result could not return.
    # The delete returns it, and passing it back to the upsert recreates it.
    name = f"contract-apm-{uuid.uuid4().hex[:8]}"
    try:
        with _raw_kibana() as raw:
            raw.apm.create_or_update_agent_configuration(
                service_name=name, settings={"capture_body": "all"}, agent_name="java")
        async with Client(_obs_tool_server()) as client:
            replaced = (await client.call_tool("upsert_apm_agent_config", {
                "service_name": name, "settings": {"capture_body": "off"}})).data
            with pytest.raises(ToolError) as renamed:
                await client.call_tool("upsert_apm_agent_config", {
                    "service_name": name, "agent_name": "go",
                    "settings": {"capture_body": "errors"}})
            with _raw_kibana() as raw:
                stored = raw.apm.get_agent_configuration(name=name).body
            deleted = (await client.call_tool("delete_apm_agent_config", {
                "service_name": name})).data
            recreated = (await client.call_tool("upsert_apm_agent_config", {
                "service_name": name, "settings": deleted["settings"],
                "agent_name": deleted["agent_name"]})).data
        with _raw_kibana() as raw:
            restored = raw.apm.get_agent_configuration(name=name).body
        assert replaced["agent_name"] == "java" and replaced["service_environment"] is None
        assert replaced["previous_settings"] == {"capture_body": "all"}
        # A different agent_name on a replacement is refused before any write.
        assert "has agent_name 'java', and a replacement keeps it" in str(renamed.value)
        assert stored["agent_name"] == "java" and stored["settings"] == {"capture_body": "off"}
        assert deleted["agent_name"] == "java"
        assert recreated["created"] is True and recreated["agent_name"] == "java"
        assert restored["agent_name"] == "java" and restored["settings"] == {"capture_body": "off"}
    finally:
        _apm_cleanup((name, None))


async def test_apm_agent_config_literal_all_option_live(gateway):
    # Kibana stores "ALL_OPTION_VALUE" as a literal environment and its view and
    # delete match it exactly (probed): the tools replace and delete that one and
    # leave the service's all-environments configuration alone; creating one is
    # refused before any write.
    name, literal = f"contract-apm-{uuid.uuid4().hex[:8]}", "ALL_OPTION_VALUE"
    fresh = f"contract-apm-{uuid.uuid4().hex[:8]}"
    try:
        with _raw_kibana() as raw:
            raw.apm.create_or_update_agent_configuration(
                service_name=name, service_environment=literal,
                settings={"recording": "true"})
            raw.apm.create_or_update_agent_configuration(
                service_name=name, settings={"recording": "false"})
        async with Client(_obs_tool_server()) as client:
            replaced = (await client.call_tool("upsert_apm_agent_config", {
                "service_name": name, "environment": literal,
                "settings": {"log_level": "debug"}})).data
            deleted = (await client.call_tool("delete_apm_agent_config", {
                "service_name": name, "environment": literal})).data
            with pytest.raises(ToolError) as refused:
                await client.call_tool("upsert_apm_agent_config", {
                    "service_name": fresh, "environment": literal,
                    "settings": {"recording": "true"}})
        left = [(c.service_name, c.service_environment, c.settings)
                for c in gateway.list_apm_agent_configs() if c.service_name in (name, fresh)]
        assert replaced["created"] is False and replaced["previous_settings"] == {
            "recording": "true"}
        assert deleted["deleted"] is True and deleted["settings"] == {"log_level": "debug"}
        assert str(refused.value).endswith(
            f"omit environment to create or replace the configuration for service "
            f"'{fresh}', all environments")
        assert left == [(name, None, {"recording": "false"})]
    finally:
        _apm_cleanup((name, literal), (name, None), (fresh, literal))


async def test_apm_agent_config_both_sentinels_live(gateway):
    # With both names the sentinel and nothing stored under that pair, the
    # refusal says to omit both: omitting only service_name would replace a
    # configuration stored under {all services, literal "ALL_OPTION_VALUE"}.
    literal = "ALL_OPTION_VALUE"
    try:
        with _raw_kibana() as raw:
            raw.apm.create_or_update_agent_configuration(
                service_environment=literal, settings={"recording": "true"})
        async with Client(_obs_tool_server()) as client:
            with pytest.raises(ToolError) as refused:
                await client.call_tool("upsert_apm_agent_config", {
                    "service_name": literal, "environment": literal,
                    "settings": {"log_level": "debug"}})
        with _raw_kibana() as raw:
            kept = raw.apm.get_agent_configuration(environment=literal).body
        assert str(refused.value).endswith(
            "omit service_name and environment to create or replace the configuration for "
            "all services, all environments")
        assert kept["settings"] == {"recording": "true"}
        assert not any(c.service_name == literal for c in gateway.list_apm_agent_configs())
    finally:
        _apm_cleanup((None, literal), (literal, literal))


# --- data-management: runtime fields, the default data view, saved-objects reads (D48) ---


def _flights_view_id(gateway):
    # The seeded sample's view (other tests add views over the same pattern).
    (view,) = [v for v in gateway.list_data_views()
               if v.index_pattern == FLIGHTS and v.name == "Kibana Sample Data Flights"]
    return view.id


async def test_runtime_field_lifecycle_live(gateway, tmp_path):
    # Through the MCP surface on a throwaway view over the flights fields: a
    # create (the POST), a replacement that returns what it replaced and keeps
    # the custom label Kibana's PUT drops, the refusals that name the fix, a
    # script Kibana stores without compiling it, and the delete with its miss.
    view = gateway.create_data_view(_dv_pattern(), f"ct-rt-{uuid.uuid4().hex[:6]}", "timestamp")
    try:
        async with Client(_dm_tool_server(tmp_path)) as client:
            created = (await client.call_tool("upsert_runtime_field", {
                "view_id": view.id, "name": "hour", "type": "long",
                "script": "emit(doc['timestamp'].value.getHour())",
            })).data
            with _raw_kibana() as raw:  # a label set the way Kibana's UI does
                raw.data_views.create_or_update_runtime_field(
                    view_id=view.id, name="hour",
                    runtime_field={**{"type": "long", "script": {"source": "emit(1)"}},
                                   "customLabel": "Hour", "customDescription": "UTC hour"},
                )
            replaced = (await client.call_tool("upsert_runtime_field", {
                "view_id": view.id, "name": "hour", "type": "keyword", "script": "emit('x')",
            })).data
            broken = (await client.call_tool("upsert_runtime_field", {
                "view_id": view.id, "name": "broken", "type": "long",
                "script": "this is not painless(((",
            })).data
            plain = (await client.call_tool("upsert_runtime_field", {
                "view_id": view.id, "name": "Dest_rt", "type": "keyword",
            })).data
            with pytest.raises(ToolError) as mapped:
                await client.call_tool("upsert_runtime_field", {
                    "view_id": view.id, "name": "Carrier", "type": "keyword",
                })
            with _raw_kibana() as raw:
                raw.data_views.create_or_update_runtime_field(
                    view_id=view.id, name="comp",
                    runtime_field={"type": "composite", "script": {"source": "emit(['a': 'x'])"},
                                   "fields": {"a": {"type": "keyword"}}},
                )
                stored = raw.data_views.get(view_id=view.id).body["data_view"]
            with pytest.raises(ToolError, match=r"is a composite field, which this tool does "
                                                r"not replace"):
                await client.call_tool("upsert_runtime_field", {
                    "view_id": view.id, "name": "comp", "type": "keyword",
                })
            with pytest.raises(ToolError, match=r"delete 'comp' to remove it with its subfields"):
                await client.call_tool("delete_runtime_field", {"view_id": view.id, "name": "comp.a"})
            with pytest.raises(ToolError, match=r"only runtime fields can be deleted"):
                await client.call_tool("delete_runtime_field", {"view_id": view.id, "name": "Carrier"})
            comp = (await client.call_tool(
                "delete_runtime_field", {"view_id": view.id, "name": "comp"})).data
            for name in ("broken", "Dest_rt"):
                await client.call_tool("delete_runtime_field", {"view_id": view.id, "name": name})
            deleted = (await client.call_tool(
                "delete_runtime_field", {"view_id": view.id, "name": "hour"})).data
            with pytest.raises(ToolError) as gone:
                await client.call_tool("delete_runtime_field", {"view_id": view.id, "name": "hour"})
            with pytest.raises(ToolError, match=r"call list_data_views to find it$"):
                await client.call_tool("upsert_runtime_field", {
                    "view_id": "no-such-dv-xyz", "name": "h", "type": "long",
                })
            with _raw_kibana() as raw:
                after = raw.data_views.get(view_id=view.id).body["data_view"]
        assert created == {
            "view_id": view.id, "created": True, "previous": None,
            "field": {"name": "hour", "type": "long",
                      "script": "emit(doc['timestamp'].value.getHour())"},
        }
        assert replaced["created"] is False
        assert replaced["previous"] == {"name": "hour", "type": "long", "script": "emit(1)"}
        assert replaced["field"] == {"name": "hour", "type": "keyword", "script": "emit('x')"}
        # Kibana compiles nothing: the broken script is stored as sent.
        assert broken["field"]["script"] == "this is not painless((("
        assert plain["field"] == {"name": "Dest_rt", "type": "keyword", "script": None}
        assert "'Carrier' is already a field of data view" in str(mapped.value)
        assert stored["fieldAttrs"]["hour"] == {"customLabel": "Hour", "customDescription": "UTC hour"}
        assert stored["runtimeFieldMap"]["hour"]["type"] == "keyword"
        assert comp["field"] == {"name": "comp", "type": "composite",
                                 "script": "emit(['a': 'x'])", "fields": {"a": "keyword"}}
        assert deleted == {"view_id": view.id, "deleted": True, "field": replaced["field"]}
        assert str(gone.value) == (
            f"data view '{view.id}' has no runtime field 'hour' — it has no runtime fields"
        )
        assert after["runtimeFieldMap"] == {}
    finally:
        gateway.delete_data_view(view.id)


async def test_default_data_view_set_get_and_clear_scoped_live(gateway, tmp_path):
    # In a throwaway space, so the shared default space's default is untouched:
    # unset → Kibana makes the space's first data view its default (probed on
    # 9.5.4) → a set replaces it (force) → a refused unknown id leaves it → a
    # deleted default reads as not existing → clear, with previous_id each time.
    with _temp_space(gateway, "mcp-for-kibana-ct-default-dv") as space_id:
        async with Client(_dm_tool_server(tmp_path)) as client:
            unset = (await client.call_tool("get_default_data_view", {"space": space_id})).data
            first = (await client.call_tool("create_data_view", {
                "index_pattern": FLIGHTS, "name": "ct default a", "space": space_id})).data
            auto = (await client.call_tool("get_default_data_view", {"space": space_id})).data
            second = (await client.call_tool("create_data_view", {
                "index_pattern": FLIGHTS, "name": "ct default b", "space": space_id})).data
            set_a = (await client.call_tool("set_default_data_view", {
                "view_id": first["id"], "space": space_id})).data
            set_b = (await client.call_tool("set_default_data_view", {
                "view_id": second["id"], "space": space_id})).data
            with pytest.raises(ToolError) as bogus:
                await client.call_tool("set_default_data_view", {
                    "view_id": "no-such-dv-xyz", "space": space_id})
            kept = (await client.call_tool("get_default_data_view", {"space": space_id})).data
            await client.call_tool("delete_data_view", {"view_id": second["id"], "space": space_id})
            dangling = (await client.call_tool("get_default_data_view", {"space": space_id})).data
            # Over a dangling default the old id cannot be passed back (it names
            # no view), so previous_exists says to undo with null instead.
            over_dangling = (await client.call_tool("set_default_data_view", {
                "view_id": first["id"], "space": space_id})).data
            with pytest.raises(ToolError) as no_restore:
                await client.call_tool("set_default_data_view", {
                    "view_id": second["id"], "space": space_id})
            cleared = (await client.call_tool("set_default_data_view", {
                "view_id": None, "space": space_id})).data
            with _raw_kibana(space_id) as raw:
                stored = raw.data_views.get_default().body
    assert unset == {"id": None, "name": None, "index_pattern": None, "exists": False,
                     "space": space_id}
    assert auto == {"id": first["id"], "name": "ct default a", "index_pattern": FLIGHTS,
                    "exists": True, "space": space_id}
    # a repeat changes nothing
    assert set_a == {**auto, "previous_id": first["id"], "previous_exists": True}
    assert set_b["id"] == second["id"] and set_b["previous_id"] == first["id"]
    assert set_b["previous_exists"] is True
    assert "call list_data_views to find it" in str(bogus.value)
    assert str(bogus.value).endswith(f" (in space '{space_id}')")
    assert kept["id"] == second["id"] and kept["exists"] is True
    assert dangling == {"id": second["id"], "name": None, "index_pattern": None,
                        "exists": False, "space": space_id}
    assert over_dangling["previous_id"] == second["id"]
    assert over_dangling["previous_exists"] is False
    assert "call list_data_views to find it" in str(no_restore.value)
    assert cleared["id"] is None and cleared["previous_id"] == first["id"]
    assert cleared["previous_exists"] is True
    assert stored == {"data_view_id": ""}


async def test_find_and_get_saved_objects_live(gateway, tmp_path):
    # The seeded flights sample: its dashboard references the flights data view,
    # carries a panelsJSON over the cap, and is invisible from another space; a
    # reference to no object and a word only in its description find nothing; a
    # hidden type answers an empty page; get reads analytics content only, so
    # Fleet's output (plain config_yaml / service_token attributes on 9.5.4) and
    # proxy types are refused before Kibana is called, naming only ways forward
    # that work for each type.
    flights_id = _flights_view_id(gateway)
    with _raw_kibana() as raw:
        outputs = raw.saved_objects.find(type="ingest-outputs", fields=["name"]).body
    async with Client(_dm_tool_server(tmp_path)) as client:
        by_ref = (await client.call_tool("find_saved_objects", {
            "types": ["dashboard"], "has_reference": {"type": "index-pattern", "id": flights_id},
        })).data
        no_ref = (await client.call_tool("find_saved_objects", {
            "types": ["dashboard"],
            "has_reference": {"type": "index-pattern", "id": f"no-such-{uuid.uuid4().hex}"},
        })).data
        by_word = (await client.call_tool("find_saved_objects", {
            "types": ["dashboard", "index-pattern"], "search": "flight*", "per_page": 100,
        })).data
        # In the flights dashboard's description only, not its title.
        by_description = (await client.call_tool("find_saved_objects", {
            "types": ["dashboard"], "search": "JetBeats",
        })).data
        hidden = (await client.call_tool("find_saved_objects", {"types": ["action"]})).data
        (dash,) = [o for o in by_ref["saved_objects"]
                   if o["title"] == "[Flights] Global Flight Dashboard"]
        got = (await client.call_tool("get_saved_object", {
            "type": "dashboard", "id": dash["id"]})).data
        view = (await client.call_tool("get_saved_object", {
            "type": "index-pattern", "id": flights_id})).data
        refused = {}
        for type_, id_ in [("ingest-outputs", o["id"]) for o in outputs["saved_objects"]] + [
            ("fleet-proxy", "x"), ("synthetics-param", "x"), ("action", "x"), ("alert", "x"),
            ("config", "x"),  # not analytics, but Kibana exports it: the fallback text
        ]:
            with pytest.raises(ToolError) as exc:
                await client.call_tool("get_saved_object", {"type": type_, "id": id_})
            refused[type_] = str(exc.value)
        with pytest.raises(ToolError, match=r"past the first 10,000 results"):
            await client.call_tool("find_saved_objects", {
                "types": ["dashboard"], "page": 101, "per_page": 100})
    with _temp_space(gateway, "mcp-for-kibana-ct-so") as space_id:
        async with Client(_dm_tool_server(tmp_path)) as client:
            elsewhere = (await client.call_tool("find_saved_objects", {
                "types": ["dashboard"], "space": space_id})).data
            with pytest.raises(ToolError) as scoped_miss:
                await client.call_tool("get_saved_object", {
                    "type": "dashboard", "id": dash["id"], "space": space_id})
    assert by_ref["total"] >= 1 and dash["namespaces"] == ["default"]
    assert no_ref["total"] == 0 and no_ref["saved_objects"] == []
    assert set(dash) == {"type", "id", "title", "name", "updated_at", "namespaces", "managed"}
    found = {(o["type"], o["id"]) for o in by_word["saved_objects"]}
    assert ("dashboard", dash["id"]) in found and ("index-pattern", flights_id) in found
    assert by_description["total"] == 0
    assert hidden["total"] == 0 and "check the type names" in hidden["note"]
    assert got["attributes"]["title"] == "[Flights] Global Flight Dashboard"
    assert "JetBeats" in got["attributes"]["description"]
    assert got["omitted_attributes"]["panelsJSON"] > 4000
    assert "panelsJSON" not in got["attributes"]
    assert {"type": "index-pattern", "id": flights_id} in got["references"]
    assert len(got["references"]) == len({(r["type"], r["id"]) for r in got["references"]})
    assert view["attributes"]["title"] == FLIGHTS
    assert outputs["total"] >= 1  # the stack's default Fleet output exists, and is refused
    assert set(refused) == {
        "ingest-outputs", "fleet-proxy", "synthetics-param", "action", "alert", "config"}
    exported = []
    for type_, message in refused.items():
        assert f"does not read '{type_}' objects" in message
        assert "config_yaml" not in message and "service_token" not in message
        # The refusal names only ways that work for the type: export only where
        # Kibana exports it (it 400s Fleet's and Synthetics' types), find only
        # where the type is not hidden (find answers action/alert an empty page).
        if "export_saved_objects" in message:
            gateway.export_saved_objects([type_], None, False)
            exported.append(type_)
    assert exported == ["config"]  # the fallback's export path ran live
    for type_ in ("ingest-outputs", "fleet-proxy", "synthetics-param"):
        assert "export_saved_objects" not in refused[type_]
        with pytest.raises(KibanaRejected):  # 400 "Trying to export non-exportable type(s)"
            gateway.export_saved_objects([type_], None, False)
    assert "find_saved_objects" not in refused["action"] + refused["alert"]
    assert "list_outputs" in refused["ingest-outputs"]
    assert "no tool reads" in refused["fleet-proxy"]
    assert elsewhere["total"] == 0 and "space" in elsewhere
    assert str(scoped_miss.value).endswith(f" (in space '{space_id}')")
