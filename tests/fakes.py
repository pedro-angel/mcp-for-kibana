"""In-memory KibanaGateway for unit tests."""

import itertools
from dataclasses import replace

from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected
from kibana_mcp.core.models import (
    AlertingHealth, AlertRule, AlertRuleDetail, RuleType, ApiDeprecation, ApmAgentConfig, ApmAnnotation, ApmEnvironment,
    ApmSourcemap, Case, Connector, ConnectorDetail, DashboardDetail, DashboardSummary, DataViewDetail,
    DataViewSummary, DetectionAlert, DetectionRule, EmailDefaults, ExceptionItem, ExceptionList,
    FleetAgent, FleetAgentPolicy, FleetAgentStatus, FleetEnrollmentKey, FleetOutput, FleetOutputHealth,
    FleetPackage, FleetPackageCategory, FleetPackagePolicy, FleetPermissions, FleetServerHost,
    FleetSettings, FleetUninstallToken,
    ImportedObject, KibanaStats, KibanaStatus, PanelSummary, PrepackagedRulesStatus, Role,
    RoleIndexPrivilege, RoleKibanaPrivilege, RuleSnooze, SavedObjectImportResult, ShortUrl, Space, Stream,
    StreamIngest, StreamSummary, StreamWriteResult, SyntheticMonitor, SyntheticParam, SyntheticPrivateLocation,
    TaskManagerHealth, Timeline, UpgradeReadiness, UptimeSettings, ValueList, ValueListItem,
    VisualizationDetail, VisualizationSummary,
)
from kibana_mcp.core.visualizations.translate import describe_lens_config, lens_chart_type

# Mirrors kibana_mcp.adapters.kibana.gateway._UPDATABLE_KEYS. Copied rather than
# imported so this fake doesn't pull in the adapters.kibana layer (would violate
# the "kibana-py only inside adapters.kibana" import-linter contract).
_UPDATABLE_KEYS = frozenset({
    "title", "description", "panels", "options", "filters", "query",
    "time_range", "refresh_interval", "tags", "pinned_panels",
})

FLIGHTS_DV = DataViewDetail(
    id="dv1",
    name="flights",
    index_pattern="kibana_sample_data_flights",
    time_field="timestamp",
    fields={
        "Carrier": "string",
        "AvgTicketPrice": "number",
        "Cancelled": "boolean",
        "timestamp": "date",
        "DestCountry": "string",
    },
)


class FakeGateway:
    def __init__(self):
        self.data_views = {FLIGHTS_DV.id: FLIGHTS_DV}
        self.data_view_updates: list[dict] = []  # update_data_view calls, as passed
        self.dashboards: dict[str, dict] = {}
        self.visualizations: dict[str, dict] = {}
        self.short_urls: dict[str, ShortUrl] = {}
        self.alert_rules: dict[str, AlertRule] = {}
        self.connectors: dict[str, Connector] = {}
        # Per-connector config and the secrets last written (write-only in
        # Kibana: no fake read path returns them).
        self.connector_configs: dict[str, dict] = {}
        self.connector_secrets: dict[str, dict] = {}
        self.rule_updates: list[dict] = []  # update_alert_rule calls, as passed
        # A rule's params and actions, beside its summary in alert_rules (a
        # summary stored directly has none: get_alert_rule then reads {} / []).
        self.rule_params: dict[str, dict] = {}
        self.rule_actions: dict[str, list] = {}
        # Rule lifecycle state: rule-wide mute, per-alert mutes, snoozes by
        # schedule id (the arguments as passed), API-key refreshes, untrack flags.
        self.rule_muted: dict[str, bool] = {}
        self.rule_muted_alerts: dict[str, list[str]] = {}
        self.rule_snoozes: dict[str, dict[str, dict]] = {}
        self.api_key_updates: list[str] = []
        self.disable_untracks: list[bool] = []
        self.cases: dict[str, Case] = {}
        self.alerting_health = AlertingHealth(
            status="ok", has_permanent_encryption_key=True, is_sufficiently_secure=True
        )
        self.warnings: list[str] = []  # set by tests to simulate dropped panels
        self.raise_on_get: Exception | None = None  # set by tests to inject an error
        # platform-health defaults (a healthy stack); tests override as needed.
        self.kibana_status = KibanaStatus(
            overall_level="available", overall_summary="All good", version="9.4.3", unhealthy=()
        )
        self.kibana_stats = KibanaStats(
            heap_used_bytes=100, heap_total_bytes=200, heap_size_limit_bytes=400,
            event_loop_delay_ms=1.0, concurrent_connections=2,
        )
        self.task_manager_health = TaskManagerHealth(status="OK", timestamp="t0", last_update="t1")
        # observability defaults (a lightly-populated stack); tests override.
        self.synthetic_monitors = [
            SyntheticMonitor(
                id="mon-1", name="home", type="http", enabled=True, tags=("prod",),
                locations=("us-east",), schedule="10m", target="https://example.com",
            )
        ]
        self.synthetic_params = [
            SyntheticParam(id="p-1", key="api_token", description="RUM token", tags=("shared",))
        ]
        self.synthetic_private_locations = [
            SyntheticPrivateLocation(
                id="loc-1", label="dc1", agent_policy_id="ap-1", is_invalid=False, tags=()
            )
        ]
        # Synthetics write tier (D46): every write is recorded; ids per kind.
        self.monitor_writes: list[tuple] = []
        self.param_writes: list[tuple] = []
        self.location_writes: list[tuple] = []
        self._synthetic_ids = {k: itertools.count(2) for k in ("mon", "p", "loc")}
        self._test_runs = itertools.count(1)
        self.uptime_settings = UptimeSettings(
            heartbeat_indices="heartbeat-*", cert_expiration_threshold=30, cert_age_threshold=730,
            default_connectors=(), default_email=EmailDefaults(to=(), cc=(), bcc=()),
        )
        self.apm_agent_configs = [
            ApmAgentConfig(
                service_name="checkout", service_environment="production",
                settings={"transaction_sample_rate": "0.5"}, applied_by_agent=True, etag="e1",
            )
        ]
        self.apm_environments = [ApmEnvironment(name="ALL_OPTION_VALUE", already_configured=False)]
        self.apm_sourcemaps = [ApmSourcemap(identifier="checkout-1.0.0", created="2026-07-12T00:00:00Z")]
        self.apm_annotations: list[ApmAnnotation] = []
        # security-detections defaults (a lightly-populated stack); tests override.
        self.detection_rules = [
            DetectionRule(
                id="r-1", rule_id="rule-1", name="Suspicious login", enabled=True, type="query",
                severity="high", risk_score=73, tags=("auth",), immutable=False, version=1,
            )
        ]
        self.prepackaged_status = PrepackagedRulesStatus(
            rules_installed=0, rules_not_installed=0, rules_custom_installed=1, rules_not_updated=0,
            timelines_installed=0, timelines_not_installed=10, timelines_not_updated=0,
        )
        self.detection_rule_tags = ["auth", "network"]
        self.detection_alerts: list[DetectionAlert] = []
        self.exception_lists = [
            ExceptionList(
                id="el-1", list_id="exc-1", name="Allowlist", type="detection",
                namespace_type="single", tags=(), os_types=(),
            )
        ]
        self.exception_items = {
            "exc-1": [ExceptionItem(id="ei-1", item_id="item-1", name="allow host", list_id="exc-1")]
        }
        self.value_lists = [ValueList(id="vl-1", name="bad-ips", type="ip", description="known bad")]
        # item id -> item, flat (delete_value_list_item takes only item_id, no list_id).
        self.value_list_items: dict[str, ValueListItem] = {}
        self.missing_rules: set[str] = set()  # test-controlled: force update_detection_rule not-found
        self.patched: dict | None = None  # last update_detection_rule field forward
        self.timelines = [Timeline(saved_object_id="t-1", title="Investigation", description="")]
        # saved_objects export/import: a canned export body (objects + details).
        self.export_body: list[dict] = [
            {"type": "index-pattern", "id": "dv1", "attributes": {"title": "flights"}, "references": []},
            {"exportedCount": 1, "missingRefCount": 0, "missingReferences": [],
             "excludedObjects": [], "excludedObjectsCount": 0},
        ]
        self.import_result = SavedObjectImportResult(
            success=True, imported_count=1,
            objects=(ImportedObject(type="index-pattern", source_id="dv1", destination_id="dv1-copy"),),
            warnings=(), errors=(),
        )
        # overwrite restores in place: destination_id == source_id.
        self.overwrite_import_result = SavedObjectImportResult(
            success=True, imported_count=1,
            objects=(ImportedObject(type="index-pattern", source_id="dv1", destination_id="dv1"),),
            warnings=(), errors=(),
        )
        self.last_import_overwrite: bool | None = None
        # platform-admin defaults: spaces + roles + upgrade readiness.
        self.spaces = [
            Space(
                id="default", name="Default", description="This is your default space!",
                solution="es", disabled_features=("apm", "uptime"), reserved=True,
            )
        ]
        self.roles = [
            Role(
                name="kibana_system", description="System role", reserved=True,
                cluster_privileges=("monitor", "manage_index_templates"),
                index_privileges=(RoleIndexPrivilege(names=(".kibana*",), privileges=("all",)),),
                run_as=(),
                kibana_privileges=(
                    RoleKibanaPrivilege(
                        base=("all",), features=("dashboard", "discover"), spaces=("*",)
                    ),
                ),
            )
        ]
        self.upgrade_readiness = UpgradeReadiness(
            ready_for_upgrade=True, details="All deprecation warnings have been resolved.",
            es_deprecation_count=0,
            api_deprecations=(
                ApiDeprecation(title="DELETE route deprecated", level="warning", type="api"),
            ),
        )
        # streams defaults: one wired + one classic stream.
        self.streams = [
            Stream(
                name="logs.ecs", type="wired", description="Root stream for logs.ecs",
                updated_at="2026-07-11T13:23:57.798Z", lifecycle="dsl", data_retention=None,
                processing_step_count=0, routing_count=0, field_count=2,
            ),
            Stream(
                name="traces-apm-default", type="classic", description="", updated_at="",
                lifecycle="inherit", data_retention=None, processing_step_count=0,
                routing_count=0, field_count=0,
            ),
        ]
        self.stream_ingests = {
            "logs.ecs": StreamIngest(
                lifecycle="dsl", data_retention=None, processing_step_count=0, routing_count=0,
                fields={"@timestamp": "date", "host.name": "keyword"},
            ),
            "traces-apm-default": StreamIngest(
                lifecycle="inherit", data_retention=None, processing_step_count=0,
                routing_count=0, fields={},
            ),
        }
        # fleet defaults: a fleet-server + a demo agent, policies, an integration, an output.
        self.fleet_agents_data = [
            FleetAgent(id="agent-fs", status="online", policy_id="fleet-server-policy", active=True,
                       hostname="fleet-server", version="9.4.3", enrolled_at="2026-07-18T00:00:00Z",
                       last_checkin="2026-07-18T00:01:00Z", last_checkin_status="online"),
            FleetAgent(id="agent-demo", status="online", policy_id="fleet-agent-policy", active=True,
                       hostname="demo-agent", version="9.4.3", enrolled_at="2026-07-18T00:00:00Z",
                       last_checkin="2026-07-18T00:01:00Z", last_checkin_status="online"),
        ]
        self.fleet_agent_status = FleetAgentStatus(
            online=2, error=0, offline=0, inactive=0, updating=0, unenrolled=0, total=2)
        self.fleet_agent_versions = ["9.4.3", "9.4.2"]
        self.fleet_agent_policies = [
            FleetAgentPolicy(id="fleet-server-policy", name="Fleet Server", namespace="default",
                             description=None, agent_count=1, status="active", is_managed=False,
                             updated_at="2026-07-18T00:00:00Z", monitoring_enabled=()),
            FleetAgentPolicy(id="fleet-agent-policy", name="Demo Agent", namespace="default",
                             description=None, agent_count=1, status="active", is_managed=False,
                             updated_at="2026-07-18T00:00:00Z", monitoring_enabled=()),
        ]
        self.fleet_package_policies = [
            FleetPackagePolicy(id="pp-1", name="fleet_server-1", namespace="", enabled=True,
                               agent_policy_id="fleet-server-policy", package_name="fleet_server",
                               package_title="Fleet Server", package_version="1.6.1", description=None),
        ]
        self.fleet_enrollment_keys = [
            FleetEnrollmentKey(id="ek-1", name="Default", policy_id="fleet-agent-policy", active=True,
                               created_at="2026-07-18T00:00:00Z"),
        ]
        self.fleet_uninstall_tokens = [
            FleetUninstallToken(id="ut-1", policy_id="fleet-agent-policy", policy_name="Demo Agent",
                                created_at="2026-07-18T00:00:00Z"),
        ]
        self.fleet_packages = [
            FleetPackage(name="system", title="System", version="1.0.0", status="installed",
                         description="System integration", type="integration"),
            FleetPackage(name="nginx", title="Nginx", version="1.0.0", status="not_installed",
                         description="Nginx integration", type="integration"),
        ]
        self.fleet_installed_packages = [
            FleetPackage(name="system", title="System", version="1.0.0", status="installed",
                         description="System integration", type=None),
        ]
        self.fleet_categories = [FleetPackageCategory(id="security", title="Security", count=42)]
        self.fleet_outputs = [
            FleetOutput(id="fleet-default-output", name="default", type="elasticsearch",
                        hosts=("http://localhost:9200",), is_default=True, is_default_monitoring=True),
        ]
        self.fleet_output_health = FleetOutputHealth(
            state="HEALTHY", message="", timestamp="2026-07-18T00:00:00Z")
        self.fleet_server_hosts = [
            FleetServerHost(id="fsh-1", name="default", host_urls=("http://localhost:8220",),
                            is_default=True),
        ]
        self.fleet_settings = FleetSettings(
            id="fleet-default-settings", prerelease_integrations_enabled=False,
            integration_knowledge_enabled=True, space_awareness_migration_status="success")
        self.fleet_permissions = FleetPermissions(success=True)
        self._ids = itertools.count(1)
        self.deleted: list[tuple[str, str | None]] = []  # (kind, identifier) for write-tier asserts
        self.last_exception_entries: list[dict] | None = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def list_data_views(self):
        return [
            DataViewSummary(id=v.id, name=v.name, index_pattern=v.index_pattern)
            for v in self.data_views.values()
        ]

    def get_data_view(self, name_or_id):
        for v in self.data_views.values():
            if name_or_id in (v.id, v.name, v.index_pattern):
                return v
        raise KibanaNotFound(f"data view '{name_or_id}' not found")

    def create_data_view(self, index_pattern, name, time_field):
        # Mirrors the adapter's contract: the pattern's fields (here: those of a
        # view already over it, else none) must hold the time field as a date.
        fields = next(
            (v.fields for v in self.data_views.values() if v.index_pattern == index_pattern), {}
        )
        if time_field is not None and fields and fields.get(time_field) != "date":
            raise KibanaRejected(
                f"time field '{time_field}' is not a date field of '{index_pattern}' "
                "(no data view was created)"
            )
        new_id = f"dv-{next(self._ids)}"
        self.data_views[new_id] = DataViewDetail(
            id=new_id, name=name or index_pattern, index_pattern=index_pattern,
            time_field=time_field, fields=dict(fields),
        )
        return DataViewSummary(id=new_id, name=name or index_pattern, index_pattern=index_pattern)

    def update_data_view(self, view_id, name, index_pattern, time_field):
        # Mirrors the adapter's contract, not Kibana: a partial merge, and a time
        # field that must be a date field whenever the view's fields are known.
        if view_id not in self.data_views:
            raise KibanaNotFound(
                f"data view '{view_id}' not found — view_id is the id that create_data_view "
                "or list_data_views returns, not the view's name or index pattern; "
                "call list_data_views to find it"
            )
        cur = self.data_views[view_id]
        self.data_view_updates.append({
            "view_id": view_id, "name": name, "index_pattern": index_pattern,
            "time_field": time_field,
        })
        if time_field is not None and cur.fields and cur.fields.get(time_field) != "date":
            raise KibanaRejected(f"time field '{time_field}' is not a date field of '{cur.index_pattern}'")
        self.data_views[view_id] = replace(
            cur,
            name=name if name is not None else cur.name,
            index_pattern=index_pattern if index_pattern is not None else cur.index_pattern,
            time_field=time_field if time_field is not None else cur.time_field,
        )
        return self.data_views[view_id]

    def delete_data_view(self, view_id):
        if view_id not in self.data_views:
            raise KibanaNotFound(f"data view '{view_id}' not found")
        del self.data_views[view_id]

    def create_short_url(self, locator_id, params):
        new_id = f"su-{next(self._ids)}"
        su = ShortUrl(id=new_id, slug=f"slug-{new_id}", locator_id=locator_id, url=params.get("url"))
        self.short_urls[new_id] = su
        return su

    def resolve_short_url(self, slug):
        for su in self.short_urls.values():
            if su.slug == slug:
                return su
        raise KibanaNotFound(f"short url slug '{slug}' not found")

    def delete_short_url(self, short_url_id):
        if short_url_id not in self.short_urls:
            raise KibanaNotFound(f"short url '{short_url_id}' not found")
        del self.short_urls[short_url_id]

    def list_alert_rules(self, search):
        rules = list(self.alert_rules.values())
        if search:
            rules = [r for r in rules if search.lower() in r.name.lower()]
        return rules

    def _rule(self, rule_id):
        if rule_id not in self.alert_rules:
            raise KibanaNotFound(f"rule '{rule_id}' not found")
        return self.alert_rules[rule_id]

    def get_alert_rule(self, rule_id):
        r = self._rule(rule_id)
        return AlertRuleDetail(
            id=r.id, name=r.name, rule_type_id=r.rule_type_id, consumer=r.consumer,
            enabled=r.enabled, schedule_interval=r.schedule_interval, status=r.status,
            tags=r.tags, params=dict(self.rule_params.get(rule_id, {})),
            actions=tuple(dict(a) for a in self.rule_actions.get(rule_id, [])),
            muted=self.rule_muted.get(rule_id, False),
            muted_alert_ids=tuple(self.rule_muted_alerts.get(rule_id, [])),
        )

    def get_alerting_health(self):
        return self.alerting_health

    def create_alert_rule(self, name, rule_type_id, consumer, schedule_interval, params, tags, enabled):
        new_id = f"rule-{next(self._ids)}"
        rule = AlertRule(
            id=new_id, name=name, rule_type_id=rule_type_id, consumer=consumer,
            enabled=enabled, schedule_interval=schedule_interval, status="pending",
            tags=tuple(tags or []),
        )
        self.alert_rules[new_id] = rule
        self.rule_params[new_id] = dict(params)
        return rule

    def update_alert_rule(self, rule_id, name, tags, schedule_interval, params, actions):
        cur = self._rule(rule_id)
        if params is not None:  # merged key by key, as the adapter does
            self.rule_params[rule_id] = {**self.rule_params.get(rule_id, {}), **params}
        if actions is not None:
            self.rule_actions[rule_id] = [dict(a) for a in actions]
        self.rule_updates.append({
            "rule_id": rule_id, "name": name, "tags": tags,
            "schedule_interval": schedule_interval, "params": params, "actions": actions,
        })
        self.alert_rules[rule_id] = replace(
            cur,
            name=name if name is not None else cur.name,
            tags=tuple(tags) if tags is not None else cur.tags,
            schedule_interval=(schedule_interval if schedule_interval is not None
                               else cur.schedule_interval),
        )
        return self.alert_rules[rule_id]

    def enable_alert_rule(self, rule_id):
        self.alert_rules[rule_id] = replace(self._rule(rule_id), enabled=True)

    def disable_alert_rule(self, rule_id, untrack=False):
        self.alert_rules[rule_id] = replace(self._rule(rule_id), enabled=False)
        self.disable_untracks.append(untrack)

    # Kibana's rule-wide mute and unmute both clear the per-alert mutes (probed);
    # the gateway returns the ids it cleared.
    def mute_alert_rule(self, rule_id):
        self._rule(rule_id)
        self.rule_muted[rule_id] = True
        return tuple(self.rule_muted_alerts.pop(rule_id, []))

    def unmute_alert_rule(self, rule_id):
        self._rule(rule_id)
        self.rule_muted[rule_id] = False
        return tuple(self.rule_muted_alerts.pop(rule_id, []))

    def mute_alert(self, rule_id, alert_id):
        self._rule(rule_id)
        if self.rule_muted.get(rule_id):
            raise KibanaRejected(f"alert rule '{rule_id}' is muted as a whole (mute_alert_rule)")
        muted = self.rule_muted_alerts.setdefault(rule_id, [])
        if alert_id not in muted:
            muted.append(alert_id)

    def unmute_alert(self, rule_id, alert_id):
        self._rule(rule_id)
        muted = self.rule_muted_alerts.get(rule_id, [])
        if alert_id in muted:
            muted.remove(alert_id)

    def snooze_alert_rule(self, rule_id, duration, start, recurring):
        self._rule(rule_id)
        schedule_id = f"snooze-{next(self._ids)}"
        self.rule_snoozes.setdefault(rule_id, {})[schedule_id] = {
            "duration": duration, "start": start, "recurring": recurring,
        }
        return RuleSnooze(
            rule_id=rule_id, schedule_id=schedule_id, duration=duration,
            start=start.isoformat(), timezone="UTC",
            recurring=None if recurring is None else {
                k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in recurring.items()
            },
        )

    def unsnooze_alert_rule(self, rule_id, schedule_id):
        self._rule(rule_id)
        snoozes = self.rule_snoozes.get(rule_id, {})
        if schedule_id not in snoozes:
            raise KibanaNotFound(f"snooze schedule '{schedule_id}' not found")
        del snoozes[schedule_id]

    def update_alert_rule_api_key(self, rule_id):
        self._rule(rule_id)
        self.api_key_updates.append(rule_id)

    def delete_alert_rule(self, rule_id):
        self._rule(rule_id)
        del self.alert_rules[rule_id]
        self.rule_params.pop(rule_id, None)
        self.rule_actions.pop(rule_id, None)

    def list_rule_types(self):
        return [
            RuleType(
                id=".es-query", name="Elasticsearch query", category="management",
                producer="stackAlerts", consumers=("alerts", "discover", "stackAlerts"),
                enabled_in_license=True, minimum_license_required="basic",
                internally_managed=False,
            )
        ]

    def list_connectors(self):
        return list(self.connectors.values())

    def create_connector(self, name, connector_type_id, config, secrets):
        new_id = f"conn-{next(self._ids)}"
        conn = Connector(
            id=new_id, name=name, connector_type_id=connector_type_id,
            is_missing_secrets=False, is_preconfigured=False,
        )
        self.connectors[new_id] = conn
        self.connector_configs[new_id] = dict(config or {})
        if secrets is not None:
            self.connector_secrets[new_id] = dict(secrets)
        return conn

    def get_connector(self, connector_id):
        if connector_id not in self.connectors:
            raise KibanaNotFound(f"connector '{connector_id}' not found")
        c = self.connectors[connector_id]
        return ConnectorDetail(
            id=c.id, name=c.name, connector_type_id=c.connector_type_id,
            config=dict(self.connector_configs.get(connector_id, {})),
            is_preconfigured=c.is_preconfigured, is_system_action=False, is_deprecated=False,
            is_missing_secrets=c.is_missing_secrets, is_connector_type_deprecated=False,
        )

    def update_connector(self, connector_id, name, config, secrets):
        cur = self.get_connector(connector_id)
        if name is not None:
            self.connectors[connector_id] = replace(self.connectors[connector_id], name=name)
        if config is not None:
            self.connector_configs[connector_id] = {**cur.config, **config}
        if secrets is not None:
            self.connector_secrets[connector_id] = dict(secrets)
        return self.get_connector(connector_id)

    def delete_connector(self, connector_id):
        if connector_id not in self.connectors:
            raise KibanaNotFound(f"connector '{connector_id}' not found")
        del self.connectors[connector_id]

    def execute_connector(self, connector_id, params):
        if connector_id not in self.connectors:
            raise KibanaNotFound(f"connector '{connector_id}' not found")
        return {"connector_id": connector_id, "status": "ok"}

    def list_cases(self, search):
        cases = list(self.cases.values())
        if search:
            # Real cases.find searches title AND description; the Case DTO drops
            # description, so the fake can only approximate by matching title.
            cases = [c for c in cases if search.lower() in c.title.lower()]
        return cases

    def get_case(self, case_id):
        if case_id not in self.cases:
            raise KibanaNotFound(f"case '{case_id}' not found")
        return self.cases[case_id]

    def create_case(self, title, description, tags, severity):
        new_id = f"case-{next(self._ids)}"
        case = Case(
            id=new_id, title=title, status="open", severity=severity or "low",
            owner="cases", tags=tuple(tags or []), total_comments=0,
        )
        self.cases[new_id] = case
        return case

    def update_case(self, case_id, status, severity, tags, title):
        c = self.get_case(case_id)
        self.cases[case_id] = replace(
            c,
            status=status if status is not None else c.status,
            severity=severity if severity is not None else c.severity,
            tags=tuple(tags) if tags is not None else c.tags,
            title=title if title is not None else c.title,
        )
        return self.cases[case_id]

    def add_case_comment(self, case_id, comment):
        c = self.get_case(case_id)
        self.cases[case_id] = replace(c, total_comments=c.total_comments + 1)
        return self.cases[case_id]

    def delete_case(self, case_id):
        self.get_case(case_id)
        del self.cases[case_id]

    def search_dashboards(self, query):
        return [
            DashboardSummary(id=k, title=d["title"], description=d.get("description", ""))
            for k, d in self.dashboards.items()
            if not query or query.lower() in d["title"].lower()
        ]

    def get_dashboard(self, dashboard_id):
        d = self._get(dashboard_id)
        panels = tuple(self._panel_summary(i, p) for i, p in enumerate(d.get("panels", [])))
        return DashboardDetail(
            id=dashboard_id, title=d["title"], description=d.get("description", ""), panels=panels
        )

    def get_dashboard_data(self, dashboard_id):
        if self.raise_on_get is not None:
            raise self.raise_on_get
        data = self._get(dashboard_id)
        warnings = list(self.warnings)
        unexpected = set(data) - _UPDATABLE_KEYS
        if unexpected:
            warnings.append(
                f"dashboard contains fields the update API cannot round-trip: {sorted(unexpected)}"
            )
        return data, warnings

    def create_dashboard(self, data):
        new_id = f"dash-{next(self._ids)}"
        self.dashboards[new_id] = data
        return new_id

    def update_dashboard(self, dashboard_id, data):
        self._get(dashboard_id)
        self.dashboards[dashboard_id] = data

    def upsert_dashboard(self, dashboard_id, data):
        self.dashboards[dashboard_id] = data  # create-if-absent (no _get)
        return dashboard_id

    def _panel_summary(self, index, panel):
        # Mirrors the adapter: a by-reference panel reports its library item's
        # id and title ("" once that item is deleted).
        config = panel.get("config") or {}
        ref = config.get("ref_id")
        if not ref:
            return PanelSummary(index=index, type=panel["type"], title=config.get("title", ""))
        title = config.get("title") or self.visualizations.get(ref, {}).get("title", "")
        return PanelSummary(index=index, type=panel["type"], title=title, visualization_id=ref)

    def create_visualization(self, config):
        new_id = f"viz-{next(self._ids)}"
        self.visualizations[new_id] = config
        return new_id

    def _viz(self, visualization_id):
        if not visualization_id.strip():
            raise KibanaRejected(
                "visualization_id is empty — pass the id that create_visualization or "
                "search_visualizations returns"
            )
        if visualization_id not in self.visualizations:
            raise KibanaNotFound(f"visualization '{visualization_id}' not found")
        return self.visualizations[visualization_id]

    def search_visualizations(self, query):
        return [
            VisualizationSummary(
                id=k, title=c.get("title", ""), description=c.get("description", ""),
                chart_type=lens_chart_type(c),
            )
            for k, c in self.visualizations.items()
            if not query or query.lower() in c.get("title", "").lower()
        ]

    def get_visualization(self, visualization_id):
        config = self._viz(visualization_id)
        d = describe_lens_config(config)
        return VisualizationDetail(
            id=visualization_id, title=config.get("title", ""),
            description=config.get("description", ""), chart_type=d["chart_type"],
            data_view=d["data_view"], time_field=d["time_field"], metrics=tuple(d["metrics"]),
            group_by=tuple(d["group_by"]), query=d["query"],
        )

    def update_visualization(self, visualization_id, config):
        self._viz(visualization_id)  # never creates: the port refuses an unknown id
        self.visualizations[visualization_id] = config

    def delete_dashboard(self, dashboard_id):
        self._get(dashboard_id)
        del self.dashboards[dashboard_id]

    def delete_visualization(self, visualization_id):
        self._viz(visualization_id)
        del self.visualizations[visualization_id]

    def get_kibana_status(self):
        return self.kibana_status

    def get_kibana_stats(self):
        return self.kibana_stats

    def get_task_manager_health(self):
        return self.task_manager_health

    def list_synthetic_monitors(self):
        return list(self.synthetic_monitors)

    def get_synthetic_monitor(self, monitor_id):
        for m in self.synthetic_monitors:
            if m.id == monitor_id:
                return m
        raise KibanaNotFound(f"monitor '{monitor_id}' not found")

    def list_synthetic_params(self):
        return list(self.synthetic_params)

    def list_synthetic_private_locations(self):
        return list(self.synthetic_private_locations)

    # --- Synthetics write tier (D46) ---

    def _location_label(self, ref):
        for loc in self.synthetic_private_locations:
            if ref in (loc.id, loc.label):
                return loc.label
        raise KibanaRejected("Kibana rejected the payload", detail=f"Private Location(s) '{ref}' not found")

    def create_synthetic_monitor(self, *, type, name, private_locations, url, host,
                                 inline_script, schedule, enabled, tags):
        self.monitor_writes.append(("create", {
            "type": type, "name": name, "private_locations": private_locations, "url": url,
            "host": host, "inline_script": inline_script, "schedule": schedule,
            "enabled": enabled, "tags": tags,
        }))
        monitor = SyntheticMonitor(
            id=f"mon-{next(self._synthetic_ids['mon'])}", name=name, type=type, enabled=enabled,
            tags=tuple(tags or ()),
            locations=tuple(self._location_label(r) for r in private_locations),
            schedule=schedule or ("10m" if type == "browser" else "3m"),
            target=url or host,
        )
        self.synthetic_monitors.append(monitor)
        return monitor

    def update_synthetic_monitor(self, monitor_id, *, name=None, url=None, host=None,
                                 inline_script=None, schedule=None, enabled=None, tags=None,
                                 private_locations=None):
        current = self.get_synthetic_monitor(monitor_id)
        given = {k: v for k, v in {
            "name": name, "url": url, "host": host, "inline_script": inline_script,
            "schedule": schedule, "enabled": enabled, "tags": tags,
            "private_locations": private_locations,
        }.items() if v is not None}
        self.monitor_writes.append(("update", monitor_id, given))
        changes = {k: v for k, v in given.items() if k in ("name", "schedule", "enabled")}
        if tags is not None:
            changes["tags"] = tuple(tags)
        if private_locations is not None:
            changes["locations"] = tuple(self._location_label(r) for r in private_locations)
        if url or host:
            changes["target"] = url or host
        updated = replace(current, **changes)
        self.synthetic_monitors = [updated if m.id == monitor_id else m
                                   for m in self.synthetic_monitors]
        return updated

    def test_synthetic_monitor(self, monitor_id):
        self.get_synthetic_monitor(monitor_id)
        return f"run-{next(self._test_runs)}"

    def delete_synthetic_monitor(self, monitor_id):
        self.get_synthetic_monitor(monitor_id)
        self.monitor_writes.append(("delete", monitor_id))
        self.synthetic_monitors = [m for m in self.synthetic_monitors if m.id != monitor_id]

    def _param(self, param_id):
        for p in self.synthetic_params:
            if p.id == param_id:
                return p
        raise KibanaNotFound(f"synthetic param '{param_id}' not found")

    def create_synthetic_param(self, key, value, description, tags):
        self.param_writes.append(("create", key))  # the value is never recorded
        param = SyntheticParam(id=f"p-{next(self._synthetic_ids['p'])}", key=key,
                               description=description or "", tags=tuple(tags or ()))
        self.synthetic_params.append(param)
        return param

    def update_synthetic_param(self, param_id, *, key=None, value=None, description=None,
                               tags=None):
        current = self._param(param_id)
        given = {k: v for k, v in {"key": key, "value": value, "description": description,
                                   "tags": tags}.items() if v is not None}
        self.param_writes.append(("update", param_id, given))
        changes = {k: v for k, v in given.items() if k in ("key", "description")}
        if tags is not None:
            changes["tags"] = tuple(tags)
        updated = replace(current, **changes)
        self.synthetic_params = [updated if p.id == param_id else p for p in self.synthetic_params]
        return updated

    def delete_synthetic_param(self, param_id):
        self._param(param_id)
        self.param_writes.append(("delete", param_id))
        self.synthetic_params = [p for p in self.synthetic_params if p.id != param_id]

    def _location(self, location_id):
        for loc in self.synthetic_private_locations:
            if loc.id == location_id:
                return loc
        raise KibanaNotFound(f"private location '{location_id}' not found")

    def create_synthetic_private_location(self, label, agent_policy_id, tags):
        self.location_writes.append(("create", label, agent_policy_id))
        loc = SyntheticPrivateLocation(
            id=f"loc-{next(self._synthetic_ids['loc'])}", label=label,
            agent_policy_id=agent_policy_id, is_invalid=False, tags=tuple(tags or ()),
        )
        self.synthetic_private_locations.append(loc)
        return loc

    def update_synthetic_private_location(self, location_id, label):
        updated = replace(self._location(location_id), label=label)
        self.location_writes.append(("update", location_id, label))
        self.synthetic_private_locations = [
            updated if loc.id == location_id else loc for loc in self.synthetic_private_locations]
        return updated

    def delete_synthetic_private_location(self, location_id):
        loc = self._location(location_id)
        if any(loc.label in m.locations for m in self.synthetic_monitors):
            raise KibanaRejected("Kibana refused to delete the private location",
                                 detail=f"Private location with id {location_id} cannot be "
                                        "deleted because it is used by 1 monitor(s).")
        self.location_writes.append(("delete", location_id))
        self.synthetic_private_locations = [
            x for x in self.synthetic_private_locations if x.id != location_id]

    def get_uptime_settings(self):
        return self.uptime_settings

    def list_apm_agent_configs(self):
        return list(self.apm_agent_configs)

    def get_apm_agent_config(self, service_name, environment):
        for c in self.apm_agent_configs:
            if c.service_name == service_name and c.service_environment == environment:
                return c
        raise KibanaNotFound(
            f"no APM agent config for service={service_name!r} environment={environment!r}"
        )

    def list_apm_environments(self, service_name):
        return list(self.apm_environments)

    def list_apm_sourcemaps(self):
        return list(self.apm_sourcemaps)

    def search_apm_annotations(self, service_name, start, end, environment):
        return list(self.apm_annotations)

    def find_detection_rules(self):
        return list(self.detection_rules)

    def get_detection_rule(self, rule_id, id):
        for r in self.detection_rules:
            if (rule_id and r.rule_id == rule_id) or (id and r.id == id):
                return r
        raise KibanaNotFound("detection rule not found")

    def get_prepackaged_rules_status(self):
        return self.prepackaged_status

    def list_detection_rule_tags(self):
        return list(self.detection_rule_tags)

    def search_detection_alerts(self, size):
        return list(self.detection_alerts[:size])

    def find_exception_lists(self):
        return list(self.exception_lists)

    def get_exception_list(self, id, list_id):
        for x in self.exception_lists:
            if (id and x.id == id) or (list_id and x.list_id == list_id):
                return x
        raise KibanaNotFound("exception list not found")

    def find_exception_items(self, list_id):
        if list_id not in self.exception_items:
            raise KibanaNotFound(f"exception list '{list_id}' not found")
        return list(self.exception_items[list_id])

    def find_value_lists(self):
        return list(self.value_lists)

    def find_timelines(self):
        return list(self.timelines)

    def export_saved_objects(self, types, objects, include_references_deep):
        return list(self.export_body)

    def import_saved_objects(self, content, overwrite):
        self.last_import_overwrite = overwrite
        return self.overwrite_import_result if overwrite else self.import_result

    def list_spaces(self):
        return list(self.spaces)

    def get_space(self, space_id):
        for s in self.spaces:
            if s.id == space_id:
                return s
        raise KibanaNotFound(f"space '{space_id}' not found")

    def list_roles(self):
        return list(self.roles)

    def get_role(self, role_name):
        for r in self.roles:
            if r.name == role_name:
                return r
        raise KibanaNotFound(f"role '{role_name}' not found")

    def create_space(self, id, name, description, color, initials, disabled_features, solution):
        if any(s.id == id for s in self.spaces):
            raise KibanaRejected(f"space '{id}' already exists")
        sp = Space(id=id, name=name, description=description, solution=solution,
                   disabled_features=tuple(disabled_features or ()), reserved=False)
        self.spaces.append(sp)
        return sp

    def update_space(self, space_id, name, description, color, initials, disabled_features, solution):
        for i, s in enumerate(self.spaces):
            if s.id == space_id:
                self.spaces[i] = replace(
                    s, name=name if name is not None else s.name,
                    description=description if description is not None else s.description,
                    solution=solution if solution is not None else s.solution,
                    disabled_features=(tuple(disabled_features) if disabled_features is not None
                                       else s.disabled_features))
                return self.spaces[i]
        raise KibanaNotFound(f"space '{space_id}' not found")

    def create_or_update_role(self, name, cluster_privileges, index_privileges, kibana_base,
                              kibana_spaces, description, create_only):
        existing = next((r for r in self.roles if r.name == name), None)
        if existing and existing.reserved:
            raise KibanaRejected(f"role '{name}' is reserved and cannot be modified")
        if create_only and existing:
            raise KibanaRejected(f"role '{name}' already exists")
        role = Role(
            name=name, description=description, reserved=False,
            cluster_privileges=tuple(cluster_privileges or ()),
            index_privileges=tuple(
                RoleIndexPrivilege(names=tuple(ip["names"]), privileges=tuple(ip["privileges"]))
                for ip in (index_privileges or ())),
            run_as=(),
            kibana_privileges=((RoleKibanaPrivilege(
                base=tuple(kibana_base), features=(), spaces=tuple(kibana_spaces or ("*",))),)
                if kibana_base else ()))
        self.roles = [r for r in self.roles if r.name != name] + [role]
        return role

    def delete_space(self, space_id, force):
        s = next((s for s in self.spaces if s.id == space_id), None)
        if s is None:
            raise KibanaNotFound(f"space '{space_id}' not found")
        if space_id == "default" or s.reserved:
            raise KibanaRejected(f"space '{space_id}' is reserved")
        if not force:
            raise KibanaRejected(f"deleting space '{space_id}' wipes its objects; pass force=True")
        self.spaces = [x for x in self.spaces if x.id != space_id]
        self.deleted.append(("space", space_id))

    def delete_role(self, name):
        r = next((r for r in self.roles if r.name == name), None)
        if r is None:
            raise KibanaNotFound(f"role '{name}' not found")
        if r.reserved:
            raise KibanaRejected(f"role '{name}' is reserved")
        self.roles = [x for x in self.roles if x.name != name]
        self.deleted.append(("role", name))

    def get_upgrade_status(self):
        return self.upgrade_readiness

    def create_detection_rule(
        self, name, description, query, index, severity, risk_score,
        rule_id, tags, interval, language, enabled,
    ):
        return DetectionRule(
            id="new-rule-uuid", rule_id=rule_id or "gen-rule-id", name=name, enabled=enabled,
            type="query", severity=severity, risk_score=risk_score, tags=tuple(tags),
            immutable=False, version=1,
        )

    def delete_detection_rule(self, rule_id, id):
        self.deleted.append(("rule", rule_id or id))

    def create_exception_list(self, name, description, type, list_id, namespace_type, tags):
        return ExceptionList(
            id="new-el-uuid", list_id=list_id or "gen-list-id", name=name, type=type,
            namespace_type=namespace_type, tags=tuple(tags), os_types=(),
        )

    def delete_exception_list(self, id, list_id, namespace_type):
        self.deleted.append(("exclist", id or list_id))

    def create_exception_item(self, list_id, name, description, entries, item_id, namespace_type, tags):
        self.last_exception_entries = entries
        return ExceptionItem(
            id="new-ei-uuid", item_id=item_id or "gen-item-id", name=name, list_id=list_id,
        )

    def delete_exception_item(self, id, item_id, namespace_type):
        self.deleted.append(("excitem", id or item_id))

    def update_detection_rule(self, rule_id, id, name, description, tags, severity, risk_score, query, interval):
        ident = rule_id or id
        if ident in self.missing_rules:
            raise KibanaNotFound(f"rule '{ident}' not found")
        self.patched = {k: v for k, v in dict(
            name=name, description=description, tags=tags, severity=severity,
            risk_score=risk_score, query=query, interval=interval).items() if v is not None}
        return DetectionRule(
            id="new-rule-uuid", rule_id=rule_id or "r", name=name or "n", enabled=False,
            type="query", severity=severity or "low",
            risk_score=risk_score if risk_score is not None else 21,
            tags=tuple(tags or []), immutable=False, version=2,
        )

    def create_value_list(self, name, description, type, id):
        vid = id or f"gen-vl-{next(self._ids)}"
        if any(v.id == vid for v in self.value_lists):
            raise KibanaRejected(f"value list '{vid}' already exists")  # models the 409 conflict
        vl = ValueList(id=vid, name=name, type=type, description=description)
        self.value_lists.append(vl)  # single store — find_value_lists reads self.value_lists
        return vl

    def delete_value_list(self, id, force):
        before = len(self.value_lists)
        self.value_lists = [v for v in self.value_lists if v.id != id]
        if len(self.value_lists) == before:
            raise KibanaNotFound(f"value list '{id}' not found")
        self.deleted.append(("value-list", id))

    def find_value_list_items(self, *, list_id):
        return [i for i in self.value_list_items.values() if i.list_id == list_id]

    def create_value_list_item(self, *, list_id, value):
        new_id = f"vli-{next(self._ids)}"
        parent = next((v for v in self.value_lists if v.id == list_id), None)
        item = ValueListItem(
            id=new_id, list_id=list_id, value=value,
            type=parent.type if parent else "keyword",
            timestamp="2026-07-18T00:00:00Z",
        )
        self.value_list_items[new_id] = item
        return item

    def delete_value_list_item(self, *, item_id):
        if item_id not in self.value_list_items:
            raise KibanaNotFound(f"value list item '{item_id}' not found")
        del self.value_list_items[item_id]
        self.deleted.append(("value-list-item", item_id))

    def replace_detection_rule(self, *, rule_id, id, changes):
        r = self.get_detection_rule(rule_id, id)
        idx = self.detection_rules.index(r)
        allowed = {"name", "severity", "risk_score", "tags", "type"}
        kw = {k: v for k, v in changes.items() if k in allowed}
        if "tags" in kw:
            kw["tags"] = tuple(kw["tags"])
        updated = replace(r, **kw)
        self.detection_rules[idx] = updated
        return updated

    def enable_detection_rule(self, *, rule_id, id):
        r = self.get_detection_rule(rule_id, id)
        self.detection_rules[self.detection_rules.index(r)] = updated = replace(r, enabled=True)
        return updated

    def disable_detection_rule(self, *, rule_id, id):
        r = self.get_detection_rule(rule_id, id)
        self.detection_rules[self.detection_rules.index(r)] = updated = replace(r, enabled=False)
        return updated

    def list_streams(self):
        return [StreamSummary(name=s.name, type=s.type, description=s.description) for s in self.streams]

    def get_stream(self, name):
        for s in self.streams:
            if s.name == name:
                return s
        raise KibanaNotFound(f"stream '{name}' not found")

    def get_stream_ingest(self, name):
        if name in self.stream_ingests:
            return self.stream_ingests[name]
        raise KibanaNotFound(f"stream '{name}' not found")

    # Set `gw.stream_error = KibanaRejected(...)` to make the next streams write
    # raise, so a toolbox test can verify the gateway_errors() -> ToolError wrapping.
    def _maybe_fail(self):
        err = getattr(self, "stream_error", None)
        if err is not None:
            raise err

    def enable_streams(self):
        self._maybe_fail()
        return StreamWriteResult(acknowledged=True, result="noop")

    def disable_streams(self):
        self._maybe_fail()
        self.deleted.append(("streams-framework", None))
        return StreamWriteResult(acknowledged=True, result="deleted")

    def resync_streams(self):
        self._maybe_fail()
        return StreamWriteResult(acknowledged=True, result="updated")

    def fork_stream(self, parent, child, field, value):
        self._maybe_fail()
        return StreamWriteResult(acknowledged=True, result="created")

    def set_stream_retention(self, name, retention):
        self._maybe_fail()
        base = self.stream_ingests.get(name) or StreamIngest(
            lifecycle="dsl", data_retention=None, processing_step_count=0, routing_count=0, fields={})
        if base.lifecycle == "ilm":  # mirror the adapter's DSL-mode allowlist
            raise KibanaRejected(f"stream '{name}' uses an 'ilm' lifecycle")
        updated = replace(base, lifecycle="dsl", data_retention=retention)
        self.stream_ingests[name] = updated  # persist, so a re-read reflects the write
        return updated

    def set_stream_processing(self, *, name, steps):
        self._maybe_fail()
        base = self.stream_ingests.get(name)
        if base is None:
            raise KibanaNotFound(f"stream '{name}' not found")
        updated = replace(base, processing_step_count=len(steps))
        self.stream_ingests[name] = updated  # persist, so a re-read reflects the write
        return updated

    # Naming-convention proxy for "child is a fork of parent" (mirrors delete_stream's
    # wired-children check below) — the fake has no raw wired.routing to inspect.
    def _fork_status(self, *, parent, child, status):
        self._maybe_fail()
        is_fork = child.startswith(parent + ".") and any(s.name == child for s in self.streams)
        if not is_fork:
            raise KibanaRejected(f"'{child}' is not a fork of '{parent}'")
        base = self.stream_ingests.get(parent)
        if base is None:
            raise KibanaNotFound(f"stream '{parent}' not found")
        updated = replace(base, routing_count=base.routing_count)  # status untracked at this layer
        self.stream_ingests[parent] = updated
        return updated

    def activate_fork(self, *, parent, child):
        return self._fork_status(parent=parent, child=child, status="enabled")

    def deactivate_fork(self, *, parent, child):
        return self._fork_status(parent=parent, child=child, status="disabled")

    def delete_stream(self, name, force):
        self._maybe_fail()
        target = name.strip()
        if not force:  # mirror the adapter's wired-only root + children guard
            wired = {s.name for s in self.streams if s.type == "wired"}
            parent = target.rpartition(".")[0]
            is_root = target in {"logs.ecs", "logs.otel"} or (target in wired and parent not in wired)
            has_children = any(s.name.startswith(target + ".") for s in self.streams)
            if is_root or has_children:
                raise KibanaRejected(f"refusing to delete '{target}': it is a root or has children")
        self.deleted.append(("stream", target))
        return StreamWriteResult(acknowledged=True, result="deleted")

    def get_fleet_settings(self):
        return self.fleet_settings

    def check_fleet_permissions(self):
        return self.fleet_permissions

    def list_agents(self):
        return list(self.fleet_agents_data)

    def get_agent(self, agent_id):
        for a in self.fleet_agents_data:
            if a.id == agent_id:
                return a
        raise KibanaNotFound(f"agent '{agent_id}' not found")

    def get_agent_status(self):
        return self.fleet_agent_status

    def list_agent_versions(self):
        return list(self.fleet_agent_versions)

    def list_agent_policies(self):
        return list(self.fleet_agent_policies)

    def get_agent_policy(self, agent_policy_id):
        for p in self.fleet_agent_policies:
            if p.id == agent_policy_id:
                return p
        raise KibanaNotFound(f"agent policy '{agent_policy_id}' not found")

    def list_package_policies(self):
        return list(self.fleet_package_policies)

    def get_package_policy(self, package_policy_id):
        for p in self.fleet_package_policies:
            if p.id == package_policy_id:
                return p
        raise KibanaNotFound(f"package policy '{package_policy_id}' not found")

    def list_enrollment_keys(self):
        return list(self.fleet_enrollment_keys)

    def get_enrollment_key(self, key_id):
        for k in self.fleet_enrollment_keys:
            if k.id == key_id:
                return k
        raise KibanaNotFound(f"enrollment key '{key_id}' not found")

    def list_uninstall_tokens(self):
        return list(self.fleet_uninstall_tokens)

    def list_packages(self):
        return list(self.fleet_packages)

    def list_installed_packages(self):
        return list(self.fleet_installed_packages)

    def get_package(self, name):
        for p in self.fleet_packages:
            if p.name == name:
                return p
        raise KibanaNotFound(f"integration package '{name}' not found")

    def list_package_categories(self):
        return list(self.fleet_categories)

    def list_outputs(self):
        return list(self.fleet_outputs)

    def get_output_health(self, output_id):
        if any(o.id == output_id for o in self.fleet_outputs):
            return self.fleet_output_health
        raise KibanaNotFound(f"output '{output_id}' not found")

    def list_fleet_server_hosts(self):
        return list(self.fleet_server_hosts)

    def create_agent_policy(self, *, name, namespace, description=None,
                            monitoring_enabled=None, inactivity_timeout=None):
        new_id = f"fap-{next(self._ids)}"
        policy = FleetAgentPolicy(
            id=new_id, name=name, namespace=namespace, description=description,
            agent_count=0, status="active", is_managed=False, updated_at="2026-07-18T00:00:00Z",
            monitoring_enabled=tuple(monitoring_enabled or ()),
        )
        self.fleet_agent_policies.append(policy)
        return policy

    def update_agent_policy(self, *, agent_policy_id, changes):
        allowed = {"name", "namespace", "description", "monitoring_enabled"}
        for i, p in enumerate(self.fleet_agent_policies):
            if p.id == agent_policy_id:
                kw = {k: v for k, v in changes.items() if k in allowed}
                if "monitoring_enabled" in kw:
                    kw["monitoring_enabled"] = tuple(kw["monitoring_enabled"])
                self.fleet_agent_policies[i] = replace(p, **kw)
                return self.fleet_agent_policies[i]
        raise KibanaNotFound(f"agent policy '{agent_policy_id}' not found")

    def create_package_policy(self, *, name, package, agent_policy_id, inputs=None):
        new_id = f"fpp-{next(self._ids)}"
        pp = FleetPackagePolicy(
            id=new_id, name=name, namespace="default", enabled=True,
            agent_policy_id=agent_policy_id, package_name=package.get("name", ""),
            package_title=package.get("title", package.get("name", "")),
            package_version=package.get("version", ""), description=None,
        )
        self.fleet_package_policies.append(pp)
        return pp

    def update_package_policy(self, *, package_policy_id, changes):
        # Only the change fields the real tool sends AND the read DTO carries can be
        # applied via `replace`. `package`/`inputs` are handled by the real adapter
        # (live-verified) but the read DTO deliberately omits them, so the fake can't
        # reflect them — a known fake-fidelity gap, not a production gap.
        allowed = {"name", "namespace", "enabled", "agent_policy_id", "description"}
        for i, p in enumerate(self.fleet_package_policies):
            if p.id == package_policy_id:
                kw = {k: v for k, v in changes.items() if k in allowed}
                self.fleet_package_policies[i] = replace(p, **kw)
                return self.fleet_package_policies[i]
        raise KibanaNotFound(f"package policy '{package_policy_id}' not found")

    def create_output(self, *, name, type, hosts, is_default=None, is_default_monitoring=None):
        new_id = f"fout-{next(self._ids)}"
        out = FleetOutput(
            id=new_id, name=name, type=type, hosts=tuple(hosts),
            is_default=bool(is_default), is_default_monitoring=bool(is_default_monitoring),
        )
        self.fleet_outputs.append(out)
        return out

    def update_output(self, *, output_id, changes, confirm=False):
        allowed = {"name", "type", "hosts", "is_default", "is_default_monitoring"}
        for i, o in enumerate(self.fleet_outputs):
            if o.id == output_id:
                kw = {k: v for k, v in changes.items() if k in allowed}
                if "hosts" in kw:
                    kw["hosts"] = tuple(kw["hosts"])
                self.fleet_outputs[i] = replace(o, **kw)
                return self.fleet_outputs[i]
        raise KibanaNotFound(f"output '{output_id}' not found")

    def delete_agent_policy(self, *, agent_policy_id, force=False):
        self.get_agent_policy(agent_policy_id)
        self.fleet_agent_policies = [
            p for p in self.fleet_agent_policies if p.id != agent_policy_id
        ]

    def delete_package_policy(self, *, package_policy_id, force=False):
        self.get_package_policy(package_policy_id)
        self.fleet_package_policies = [
            p for p in self.fleet_package_policies if p.id != package_policy_id
        ]

    def delete_output(self, *, output_id):
        if not any(o.id == output_id for o in self.fleet_outputs):
            raise KibanaNotFound(f"output '{output_id}' not found")
        self.fleet_outputs = [o for o in self.fleet_outputs if o.id != output_id]

    def reassign_agent(self, *, agent_id, policy_id):
        self.get_agent(agent_id)

    def upgrade_agent(self, *, agent_id, version, source_uri=None):
        self.get_agent(agent_id)

    def unenroll_agent(self, *, agent_id, force=False, revoke=False):
        self.get_agent(agent_id)

    def bulk_reassign(self, *, agent_ids, policy_id):
        return "fake-action-id"

    def bulk_upgrade(self, *, agent_ids, version, source_uri=None):
        return "fake-action-id"

    def bulk_unenroll(self, *, agent_ids, force=False, revoke=False):
        return "fake-action-id"

    def _get(self, dashboard_id):
        if dashboard_id not in self.dashboards:
            raise KibanaNotFound(f"dashboard '{dashboard_id}' not found")
        return self.dashboards[dashboard_id]
