"""DTOs crossing the gateway port. Deliberately thin and Kibana-agnostic."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DataViewSummary:
    id: str
    name: str
    index_pattern: str


@dataclass(frozen=True, slots=True)
class DataViewDetail:
    id: str
    name: str
    index_pattern: str
    time_field: str | None
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class ShortUrl:
    id: str
    slug: str
    locator_id: str
    url: str | None


@dataclass(frozen=True, slots=True)
class PanelSummary:
    index: int
    type: str
    title: str


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    id: str
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class DashboardDetail:
    id: str
    title: str
    description: str
    panels: tuple[PanelSummary, ...]


@dataclass(frozen=True, slots=True)
class ServiceHealth:
    name: str
    level: str
    summary: str


@dataclass(frozen=True, slots=True)
class KibanaStatus:
    overall_level: str
    overall_summary: str
    version: str
    unhealthy: tuple[ServiceHealth, ...]


@dataclass(frozen=True, slots=True)
class KibanaStats:
    heap_used_bytes: int
    heap_total_bytes: int
    heap_size_limit_bytes: int
    event_loop_delay_ms: float
    concurrent_connections: int


@dataclass(frozen=True, slots=True)
class TaskManagerHealth:
    status: str
    timestamp: str
    last_update: str


@dataclass(frozen=True, slots=True)
class AlertRule:
    id: str
    name: str
    rule_type_id: str
    consumer: str
    enabled: bool
    schedule_interval: str
    status: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Connector:
    id: str
    name: str
    connector_type_id: str
    is_missing_secrets: bool     # created 200 but non-functional if required secrets absent
    is_preconfigured: bool       # preconfigured connectors can't be deleted via the API


@dataclass(frozen=True, slots=True)
class AlertingHealth:
    status: str
    has_permanent_encryption_key: bool
    is_sufficiently_secure: bool


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    title: str
    status: str
    severity: str
    owner: str
    tags: tuple[str, ...]
    total_comments: int


# --- observability toolbox (Wave 3): synthetics + uptime + apm-config reads ---


@dataclass(frozen=True, slots=True)
class SyntheticMonitor:
    id: str  # from config_id (the id get_synthetic_monitor accepts)
    name: str
    type: str
    enabled: bool
    tags: tuple[str, ...]
    locations: tuple[str, ...]  # each API location object -> its label (fallback id)
    schedule: str  # "10m" from {"number": "10", "unit": "m"}
    target: str | None  # url (http) / host (tcp/icmp) / None (browser)


@dataclass(frozen=True, slots=True)
class SyntheticParam:
    # Deliberately omits `value` so a synthetics param value is never surfaced,
    # regardless of the API key's privileges (kibana-py returns it for a
    # sufficiently-privileged key).
    id: str
    key: str
    description: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyntheticPrivateLocation:
    id: str
    label: str
    agent_policy_id: str
    is_invalid: bool
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmailDefaults:
    # Nested inside UptimeSettings only; never a gateway return type on its own.
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UptimeSettings:
    heartbeat_indices: str
    # kibana-py types these float; the live defaults are ints (30/730). Pass raw.
    cert_expiration_threshold: int | float
    cert_age_threshold: int | float
    default_connectors: tuple[str, ...]
    default_email: EmailDefaults


@dataclass(frozen=True, slots=True)
class ApmAgentConfig:
    # Holds a settings dict — unhashable if ever hashed, same accepted nit as
    # DataViewDetail; nothing hashes it.
    service_name: str | None
    service_environment: str | None
    settings: dict[str, str]
    applied_by_agent: bool
    etag: str


@dataclass(frozen=True, slots=True)
class ApmEnvironment:
    name: str  # may be the sentinel "ALL_OPTION_VALUE" = all environments
    already_configured: bool


@dataclass(frozen=True, slots=True)
class ApmSourcemap:
    # Populated shape unverified (a RUM sourcemap cannot be seeded here); thin.
    identifier: str
    created: str


@dataclass(frozen=True, slots=True)
class ApmAnnotation:
    id: str
    timestamp: str
    text: str
    type: str


# --- security-detections toolbox (Wave 3): detection-engine reads ---


@dataclass(frozen=True, slots=True)
class DetectionRule:
    id: str  # uuid
    rule_id: str  # stable human id (get_detection_rule accepts either)
    name: str
    enabled: bool
    type: str
    severity: str
    risk_score: int
    tags: tuple[str, ...]
    immutable: bool
    version: int


@dataclass(frozen=True, slots=True)
class PrepackagedRulesStatus:
    rules_installed: int
    rules_not_installed: int
    rules_custom_installed: int
    rules_not_updated: int
    timelines_installed: int
    timelines_not_installed: int
    timelines_not_updated: int


@dataclass(frozen=True, slots=True)
class ExceptionList:
    id: str
    list_id: str
    name: str
    type: str
    namespace_type: str
    tags: tuple[str, ...]
    os_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExceptionItem:
    # Populated shape unverified (not seeded); thin + defensive.
    id: str
    item_id: str
    name: str
    list_id: str


@dataclass(frozen=True, slots=True)
class ValueList:
    # Populated shape unverified (lists data streams not initialized); defensive.
    id: str
    name: str
    type: str
    description: str


@dataclass(frozen=True, slots=True)
class ValueListItem:
    # Shape confirmed live (SD-P1): lists.create_item returns id/list_id/value/
    # type/@timestamp (+ created_by/tie_breaker_id, dropped as thin/defensive).
    id: str
    list_id: str
    value: str
    type: str
    timestamp: str  # mapped from the body's "@timestamp" key


@dataclass(frozen=True, slots=True)
class Timeline:
    # Shape confirmed live (seeded timeline captured); `description` may be null.
    saved_object_id: str
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class DetectionAlert:
    # Populated shape unverified (cannot seed a firing alert); mapped from an ES
    # hit's kibana.alert.* fields, all defensive.
    id: str
    rule_name: str
    severity: str
    status: str
    timestamp: str


# --- saved_objects export/import (#37): handle-based, NDJSON stays on disk ---


@dataclass(frozen=True, slots=True)
class TypeCount:
    type: str
    count: int


@dataclass(frozen=True, slots=True)
class ExportSummary:
    handle: str  # opaque token; pass to import_saved_objects (NOT the content)
    exported_count: int
    types: tuple[TypeCount, ...]
    missing_ref_count: int
    missing_references: tuple[str, ...]  # "type/id" of unresolved references
    excluded_count: int
    byte_size: int


@dataclass(frozen=True, slots=True)
class ImportedObject:
    type: str
    source_id: str
    destination_id: str  # regenerated id (create_new_copies -> a clone)


@dataclass(frozen=True, slots=True)
class SavedObjectImportResult:
    success: bool
    imported_count: int
    objects: tuple[ImportedObject, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]  # "type/id: reason" per failed object (success=false path)


# --- platform-admin toolbox (Wave 4, read-first v1): spaces + roles + upgrade reads ---


@dataclass(frozen=True, slots=True)
class Space:
    id: str
    name: str
    description: str | None
    solution: str | None  # 'es' | 'classic' | 'oblt' | 'security' | None
    disabled_features: tuple[str, ...]  # feature ids turned off in the space
    reserved: bool  # from _reserved: a system space (e.g. default)


@dataclass(frozen=True, slots=True)
class RoleIndexPrivilege:
    # Nested inside Role only; never a gateway return type on its own. Drops
    # field_security/query — v1 summarizes "which indices, which privileges".
    names: tuple[str, ...]
    privileges: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoleKibanaPrivilege:
    # Nested inside Role only. `features` is the sorted set of feature NAMES
    # granted (the API's per-feature privilege lists are summarized to names).
    base: tuple[str, ...]
    features: tuple[str, ...]
    spaces: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    description: str | None
    reserved: bool  # from metadata._reserved: a system/reserved role
    cluster_privileges: tuple[str, ...]
    index_privileges: tuple[RoleIndexPrivilege, ...]
    run_as: tuple[str, ...]
    kibana_privileges: tuple[RoleKibanaPrivilege, ...]


@dataclass(frozen=True, slots=True)
class ApiDeprecation:
    # Nested inside UpgradeReadiness only. message[]/correctiveActions are dropped
    # on purpose: they embed live call-counts and "last call was on <timestamp>"
    # strings, so keeping them would make the DTO non-deterministic.
    title: str
    level: str  # 'warning' | 'critical'
    type: str | None  # deprecationType: 'api' | 'feature' | ...


@dataclass(frozen=True, slots=True)
class UpgradeReadiness:
    ready_for_upgrade: bool
    details: str | None
    es_deprecation_count: int  # recentEsDeprecationLogs.count (a count, not the logs)
    api_deprecations: tuple[ApiDeprecation, ...]


# --- streams toolbox (Wave 4, read-first v1; Tech-Preview API): stream reads ---


@dataclass(frozen=True, slots=True)
class StreamSummary:
    name: str
    type: str  # raw type string: 'wired' | 'classic' | ... (not enum-constrained)
    description: str


@dataclass(frozen=True, slots=True)
class Stream:
    name: str
    type: str
    description: str
    updated_at: str
    lifecycle: str  # discriminator key: 'dsl' | 'ilm' | 'inherit' | 'disabled' | ''
    data_retention: str | None  # e.g. '30d' when set under the lifecycle mode, else None
    processing_step_count: int
    routing_count: int  # child-routing rules (wired streams; 0 for classic)
    field_count: int  # managed fields (wired streams; 0 for classic)


@dataclass(frozen=True, slots=True)
class StreamIngest:
    # Holds a dict (unhashable — same accepted nit as DataViewDetail; nothing
    # hashes it). processing/routing are summarized by COUNT: their non-empty
    # element shapes are unverifiable on the Basic stack (always empty there).
    lifecycle: str
    data_retention: str | None
    processing_step_count: int
    routing_count: int
    fields: dict[str, str]  # managed field name -> type (wired streams; {} for classic)


@dataclass(frozen=True, slots=True)
class StreamWriteResult:
    # Streams write ack {acknowledged, result}: 'created'|'updated'|'deleted'|
    # 'noop'|'' (see _to_stream_write_result — a drifted envelope with neither
    # key raises rather than flattening a real success to a false no-op).
    acknowledged: bool
    result: str


# --- fleet toolbox (read-first v1): agents, policies, integrations, outputs ---
# Fleet is GA on Basic. Read-only DTOs; three families are SECRET-REDACTED in the
# gateway mappers (enrollment keys drop api_key; outputs map only non-secret
# fields; uninstall-token *values* are never fetched) — see the mappers.


@dataclass(frozen=True, slots=True)
class FleetAgent:
    id: str
    status: str  # 'online' | 'offline' | 'error' | 'degraded' | 'updating' | 'enrolling' | ...
    policy_id: str | None
    active: bool
    hostname: str  # local_metadata.host.hostname
    version: str  # local_metadata.elastic.agent.version
    enrolled_at: str
    last_checkin: str | None
    last_checkin_status: str | None


@dataclass(frozen=True, slots=True)
class FleetAgentStatus:
    # The /api/fleet/agents/status `results` counts.
    online: int
    error: int
    offline: int
    inactive: int
    updating: int
    unenrolled: int
    total: int  # 'all'


@dataclass(frozen=True, slots=True)
class FleetAgentPolicy:
    id: str
    name: str
    namespace: str
    description: str | None
    agent_count: int  # 'agents': agents assigned to this policy
    status: str
    is_managed: bool
    updated_at: str
    monitoring_enabled: tuple[str, ...]  # e.g. ('logs', 'metrics'); () when off


@dataclass(frozen=True, slots=True)
class FleetPackagePolicy:
    id: str
    name: str
    namespace: str
    enabled: bool
    agent_policy_id: str | None  # the parent agent policy ('policy_id')
    package_name: str
    package_title: str
    package_version: str
    description: str | None


@dataclass(frozen=True, slots=True)
class FleetEnrollmentKey:
    # SECRET-REDACTED: the api_key value and api_key_id are dropped in the mapper
    # (they enroll agents) — metadata only, never crossing the port.
    id: str
    name: str | None
    policy_id: str | None
    active: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class FleetUninstallToken:
    # Metadata only. The decrypted token value is never fetched (get_uninstall_token
    # is deliberately NOT exposed as a tool).
    id: str
    policy_id: str | None
    policy_name: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class FleetPackage:
    name: str
    title: str
    version: str
    status: str  # 'installed' | 'not_installed' | 'install_failed' | ...
    description: str
    type: str | None  # 'integration' | 'input' | ...; absent on installed-list entries


@dataclass(frozen=True, slots=True)
class FleetPackageCategory:
    id: str
    title: str
    count: int


@dataclass(frozen=True, slots=True)
class FleetOutput:
    # SECRET-SAFE: only non-secret fields are mapped. ssl / secrets / api-key
    # fields on logstash/kafka outputs are never spread across the port.
    id: str
    name: str
    type: str  # 'elasticsearch' | 'logstash' | 'kafka' | 'remote_elasticsearch'
    hosts: tuple[str, ...]
    is_default: bool
    is_default_monitoring: bool


@dataclass(frozen=True, slots=True)
class FleetOutputHealth:
    state: str  # 'HEALTHY' | 'DEGRADED' | 'UNKNOWN'
    message: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class FleetServerHost:
    id: str
    name: str
    host_urls: tuple[str, ...]
    is_default: bool


@dataclass(frozen=True, slots=True)
class FleetSettings:
    id: str
    prerelease_integrations_enabled: bool
    integration_knowledge_enabled: bool
    space_awareness_migration_status: str  # 'success' | 'pending' | ...


@dataclass(frozen=True, slots=True)
class FleetPermissions:
    # /api/fleet/check_permissions: whether the current key can operate Fleet.
    success: bool
