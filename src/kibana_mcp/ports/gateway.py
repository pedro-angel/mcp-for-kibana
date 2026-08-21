"""The driven-side port: everything the domain needs from Kibana."""

from types import TracebackType
from typing import Any, Protocol

from kibana_mcp.core.models import (
    AlertingHealth,
    AlertRule,
    ApmAgentConfig,
    ApmAnnotation,
    ApmEnvironment,
    ApmSourcemap,
    Case,
    Connector,
    DashboardDetail,
    DashboardSummary,
    DataViewDetail,
    DataViewSummary,
    DetectionAlert,
    DetectionRule,
    ExceptionItem,
    ExceptionList,
    FleetAgent,
    FleetAgentPolicy,
    FleetAgentStatus,
    FleetEnrollmentKey,
    FleetOutput,
    FleetOutputHealth,
    FleetPackage,
    FleetPackageCategory,
    FleetPackagePolicy,
    FleetPermissions,
    FleetServerHost,
    FleetSettings,
    FleetUninstallToken,
    KibanaStats,
    KibanaStatus,
    PrepackagedRulesStatus,
    Role,
    SavedObjectImportResult,
    ShortUrl,
    Space,
    Stream,
    StreamIngest,
    StreamSummary,
    StreamWriteResult,
    SyntheticMonitor,
    SyntheticParam,
    SyntheticPrivateLocation,
    TaskManagerHealth,
    Timeline,
    UpgradeReadiness,
    UptimeSettings,
    ValueList,
    ValueListItem,
)


class KibanaGateway(Protocol):
    def __enter__(self) -> "KibanaGateway": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def list_data_views(self) -> list[DataViewSummary]: ...

    def get_data_view(self, name_or_id: str) -> DataViewDetail: ...

    def search_dashboards(self, query: str | None) -> list[DashboardSummary]: ...

    def get_dashboard(self, dashboard_id: str) -> DashboardDetail: ...

    def get_dashboard_data(self, dashboard_id: str) -> tuple[dict[str, Any], list[str]]: ...

    def create_dashboard(self, data: dict[str, Any]) -> str: ...

    def update_dashboard(self, dashboard_id: str, data: dict[str, Any]) -> None: ...

    def upsert_dashboard(self, dashboard_id: str, data: dict[str, Any]) -> str: ...

    def create_visualization(self, config: dict[str, Any]) -> str: ...

    def delete_dashboard(self, dashboard_id: str) -> None: ...

    def delete_visualization(self, visualization_id: str) -> None: ...

    def create_data_view(
        self, index_pattern: str, name: str | None, time_field: str | None
    ) -> DataViewSummary: ...

    def delete_data_view(self, view_id: str) -> None: ...

    def create_short_url(self, locator_id: str, params: dict[str, Any]) -> ShortUrl: ...

    def resolve_short_url(self, slug: str) -> ShortUrl: ...

    def delete_short_url(self, short_url_id: str) -> None: ...

    def list_alert_rules(self, search: str | None) -> list[AlertRule]: ...

    def get_alert_rule(self, rule_id: str) -> AlertRule: ...

    def get_alerting_health(self) -> AlertingHealth: ...

    def create_alert_rule(
        self,
        name: str,
        rule_type_id: str,
        consumer: str,
        schedule_interval: str,
        params: dict[str, Any],
        tags: list[str] | None,
        enabled: bool,
    ) -> AlertRule: ...

    def enable_alert_rule(self, rule_id: str) -> None: ...

    def disable_alert_rule(self, rule_id: str) -> None: ...

    def delete_alert_rule(self, rule_id: str) -> None: ...

    def list_connectors(self) -> list[Connector]: ...

    def create_connector(
        self, name: str, connector_type_id: str, config: dict[str, Any] | None, secrets: dict[str, Any] | None
    ) -> Connector: ...

    def delete_connector(self, connector_id: str) -> None: ...

    def execute_connector(self, connector_id: str, params: dict[str, Any]) -> dict[str, Any]: ...

    def list_cases(self, search: str | None) -> list[Case]: ...

    def get_case(self, case_id: str) -> Case: ...

    def create_case(
        self, title: str, description: str, tags: list[str] | None, severity: str | None
    ) -> Case: ...

    def update_case(
        self,
        case_id: str,
        status: str | None,
        severity: str | None,
        tags: list[str] | None,
        title: str | None,
    ) -> Case: ...

    def add_case_comment(self, case_id: str, comment: str) -> Case: ...

    def delete_case(self, case_id: str) -> None: ...

    def get_kibana_status(self) -> KibanaStatus: ...

    def get_kibana_stats(self) -> KibanaStats: ...

    def get_task_manager_health(self) -> TaskManagerHealth: ...

    def list_synthetic_monitors(self) -> list[SyntheticMonitor]: ...

    def get_synthetic_monitor(self, monitor_id: str) -> SyntheticMonitor: ...

    def list_synthetic_params(self) -> list[SyntheticParam]: ...

    def list_synthetic_private_locations(self) -> list[SyntheticPrivateLocation]: ...

    def get_uptime_settings(self) -> UptimeSettings: ...

    def list_apm_agent_configs(self) -> list[ApmAgentConfig]: ...

    def get_apm_agent_config(
        self, service_name: str | None, environment: str | None
    ) -> ApmAgentConfig: ...

    def list_apm_environments(self, service_name: str | None) -> list[ApmEnvironment]: ...

    def list_apm_sourcemaps(self) -> list[ApmSourcemap]: ...

    def search_apm_annotations(
        self, service_name: str, start: str, end: str, environment: str
    ) -> list[ApmAnnotation]: ...

    def find_detection_rules(self) -> list[DetectionRule]: ...

    def get_detection_rule(
        self, rule_id: str | None, id: str | None
    ) -> DetectionRule: ...

    def get_prepackaged_rules_status(self) -> PrepackagedRulesStatus: ...

    def list_detection_rule_tags(self) -> list[str]: ...

    def search_detection_alerts(self, size: int) -> list[DetectionAlert]: ...

    def find_exception_lists(self) -> list[ExceptionList]: ...

    def get_exception_list(self, id: str | None, list_id: str | None) -> ExceptionList: ...

    def find_exception_items(self, list_id: str) -> list[ExceptionItem]: ...

    def find_value_lists(self) -> list[ValueList]: ...

    def find_timelines(self) -> list[Timeline]: ...

    def create_detection_rule(
        self,
        name: str,
        description: str,
        query: str,
        index: list[str],
        severity: str,
        risk_score: int,
        rule_id: str | None,
        tags: list[str],
        interval: str,
        language: str,
        enabled: bool,
    ) -> DetectionRule: ...

    def delete_detection_rule(self, rule_id: str | None, id: str | None) -> None: ...

    def create_exception_list(
        self,
        name: str,
        description: str,
        type: str,
        list_id: str | None,
        namespace_type: str,
        tags: list[str],
    ) -> ExceptionList: ...

    def delete_exception_list(
        self, id: str | None, list_id: str | None, namespace_type: str
    ) -> None: ...

    def create_exception_item(
        self,
        list_id: str,
        name: str,
        description: str,
        entries: list[dict[str, Any]],
        item_id: str | None,
        namespace_type: str,
        tags: list[str],
    ) -> ExceptionItem: ...

    def delete_exception_item(
        self, id: str | None, item_id: str | None, namespace_type: str
    ) -> None: ...

    def update_detection_rule(
        self, rule_id: str | None, id: str | None, name: str | None, description: str | None,
        tags: list[str] | None, severity: str | None, risk_score: int | None,
        query: str | None, interval: str | None,
    ) -> DetectionRule: ...

    def create_value_list(self, name: str, description: str, type: str, id: str | None) -> ValueList: ...

    def delete_value_list(self, id: str, force: bool) -> None: ...

    def find_value_list_items(self, *, list_id: str) -> list[ValueListItem]: ...

    def create_value_list_item(self, *, list_id: str, value: str) -> ValueListItem: ...

    def delete_value_list_item(self, *, item_id: str) -> None: ...

    def replace_detection_rule(
        self, *, rule_id: str | None, id: str | None, changes: dict[str, Any]
    ) -> DetectionRule: ...

    def enable_detection_rule(
        self, *, rule_id: str | None, id: str | None
    ) -> DetectionRule: ...

    def disable_detection_rule(
        self, *, rule_id: str | None, id: str | None
    ) -> DetectionRule: ...

    def export_saved_objects(
        self,
        types: list[str] | None,
        objects: list[dict[str, Any]] | None,
        include_references_deep: bool,
    ) -> list[dict[str, Any]]: ...

    def import_saved_objects(
        self, content: bytes, overwrite: bool
    ) -> SavedObjectImportResult: ...

    def list_spaces(self) -> list[Space]: ...

    def get_space(self, space_id: str) -> Space: ...

    def list_roles(self) -> list[Role]: ...

    def get_role(self, role_name: str) -> Role: ...

    def get_upgrade_status(self) -> UpgradeReadiness: ...

    def create_space(
        self, id: str, name: str, description: str | None, color: str | None,
        initials: str | None, disabled_features: list[str] | None, solution: str | None,
    ) -> Space: ...

    def update_space(
        self, space_id: str, name: str | None, description: str | None, color: str | None,
        initials: str | None, disabled_features: list[str] | None, solution: str | None,
    ) -> Space: ...

    def create_or_update_role(
        self, name: str, cluster_privileges: list[str] | None, index_privileges: list[dict[str, Any]],
        kibana_base: list[str] | None, kibana_spaces: list[str] | None,
        description: str | None, create_only: bool,
    ) -> Role: ...

    def delete_space(self, space_id: str, force: bool) -> None: ...

    def delete_role(self, name: str) -> None: ...

    def list_streams(self) -> list[StreamSummary]: ...

    def get_stream(self, name: str) -> Stream: ...

    def get_stream_ingest(self, name: str) -> StreamIngest: ...

    def enable_streams(self) -> StreamWriteResult: ...

    def disable_streams(self) -> StreamWriteResult: ...

    def resync_streams(self) -> StreamWriteResult: ...

    def fork_stream(self, parent: str, child: str, field: str, value: str) -> StreamWriteResult: ...

    def set_stream_retention(self, name: str, retention: str) -> StreamIngest: ...

    def set_stream_processing(self, *, name: str, steps: list[dict[str, Any]]) -> StreamIngest: ...

    def activate_fork(self, *, parent: str, child: str) -> StreamIngest: ...

    def deactivate_fork(self, *, parent: str, child: str) -> StreamIngest: ...

    def delete_stream(self, name: str, force: bool) -> StreamWriteResult: ...

    def get_fleet_settings(self) -> FleetSettings: ...

    def check_fleet_permissions(self) -> FleetPermissions: ...

    def list_agents(self) -> list[FleetAgent]: ...

    def get_agent(self, agent_id: str) -> FleetAgent: ...

    def get_agent_status(self) -> FleetAgentStatus: ...

    def list_agent_versions(self) -> list[str]: ...

    def list_agent_policies(self) -> list[FleetAgentPolicy]: ...

    def get_agent_policy(self, agent_policy_id: str) -> FleetAgentPolicy: ...

    def list_package_policies(self) -> list[FleetPackagePolicy]: ...

    def get_package_policy(self, package_policy_id: str) -> FleetPackagePolicy: ...

    def list_enrollment_keys(self) -> list[FleetEnrollmentKey]: ...

    def get_enrollment_key(self, key_id: str) -> FleetEnrollmentKey: ...

    def list_uninstall_tokens(self) -> list[FleetUninstallToken]: ...

    def list_packages(self) -> list[FleetPackage]: ...

    def list_installed_packages(self) -> list[FleetPackage]: ...

    def get_package(self, name: str) -> FleetPackage: ...

    def list_package_categories(self) -> list[FleetPackageCategory]: ...

    def list_outputs(self) -> list[FleetOutput]: ...

    def get_output_health(self, output_id: str) -> FleetOutputHealth: ...

    def list_fleet_server_hosts(self) -> list[FleetServerHost]: ...

    def create_agent_policy(
        self,
        *,
        name: str,
        namespace: str,
        description: str | None = None,
        monitoring_enabled: list[str] | None = None,
        inactivity_timeout: int | None = None,
    ) -> FleetAgentPolicy: ...

    def update_agent_policy(
        self, *, agent_policy_id: str, changes: dict[str, Any]
    ) -> FleetAgentPolicy: ...

    def create_package_policy(
        self, *, name: str, package: dict[str, Any], agent_policy_id: str, inputs: dict[str, Any] | list[Any] | None = None
    ) -> FleetPackagePolicy: ...

    def update_package_policy(
        self, *, package_policy_id: str, changes: dict[str, Any]
    ) -> FleetPackagePolicy: ...

    def create_output(
        self,
        *,
        name: str,
        type: str,
        hosts: list[str],
        is_default: bool | None = None,
        is_default_monitoring: bool | None = None,
    ) -> FleetOutput: ...

    def update_output(
        self, *, output_id: str, changes: dict[str, Any], confirm: bool = False
    ) -> FleetOutput: ...

    def delete_agent_policy(self, *, agent_policy_id: str, force: bool = False) -> None: ...

    def delete_package_policy(self, *, package_policy_id: str, force: bool = False) -> None: ...

    def delete_output(self, *, output_id: str) -> None: ...

    def reassign_agent(self, *, agent_id: str, policy_id: str) -> None: ...

    def upgrade_agent(
        self, *, agent_id: str, version: str, source_uri: str | None = None
    ) -> None: ...

    def unenroll_agent(
        self, *, agent_id: str, force: bool = False, revoke: bool = False
    ) -> None: ...

    def bulk_reassign(self, *, agent_ids: list[str], policy_id: str) -> str: ...

    def bulk_upgrade(
        self, *, agent_ids: list[str], version: str, source_uri: str | None = None
    ) -> str: ...

    def bulk_unenroll(
        self, *, agent_ids: list[str], force: bool = False, revoke: bool = False
    ) -> str: ...
