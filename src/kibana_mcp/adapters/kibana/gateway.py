"""KibanaGateway implementation on kibana-py. The ONLY module that may
import kibana (machine-enforced)."""

import contextlib
import functools
import inspect
import re
import urllib.parse
from collections.abc import Callable
from types import TracebackType
from typing import Any

import kibana
from kibana import SpaceScopedKibana
from kibana.exceptions import (
    ApiError,
    AuthenticationException,
    AuthorizationException,
    BadRequestError,
    ConflictError,
    InvalidSpaceIdError,
    KibanaException,
    NotFoundError,
    SpaceNotFoundError,
    TransportError,
)

from kibana_mcp.core.errors import (
    KibanaAuthError,
    KibanaNotFound,
    KibanaRejected,
    KibanaSpaceNotFound,
    KibanaUnavailable,
)
from kibana_mcp.core.models import (
    AlertingHealth,
    AlertRule,
    ApiDeprecation,
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
    EmailDefaults,
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
    ImportedObject,
    KibanaStats,
    KibanaStatus,
    PanelSummary,
    PrepackagedRulesStatus,
    Role,
    RoleIndexPrivilege,
    RoleKibanaPrivilege,
    SavedObjectImportResult,
    ServiceHealth,
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
from kibana_mcp.ports.gateway import KibanaGateway


_PINNED_PATH_RE = re.compile(r"(?:^|/)s/[a-z0-9_-]+$")


def is_space_pinned(url: str) -> bool:
    """True when the URL path (trailing slashes stripped) ends in
    '/s/<space-id-grammar>'. Purely syntactic: a reverse-proxy base path
    ending in s/<id-shaped-segment> false-positives, and a proxy that
    rewrites to /s/<id> is invisible — both documented limitations."""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    return _PINNED_PATH_RE.search(path) is not None


_SPACE_GUIDANCE = (
    "space '{space}' not found — check what exists with list_spaces, "
    "create it with create_space (both tools exist when the platform-admin "
    "toolbox is enabled), or omit `space` to use the default space"
)


def _translated(e: BaseException, space: str | None) -> BaseException:
    """One shared map for the decorator and connect(): ordered isinstance
    dispatch (the kibana-py hierarchy nests); every arm keeps its current
    message verbatim; unmapped exceptions return unchanged."""
    if isinstance(e, SpaceNotFoundError):
        return KibanaSpaceNotFound(_SPACE_GUIDANCE.format(space=space))
    if isinstance(e, InvalidSpaceIdError):
        return KibanaRejected(
            f"invalid space id '{space}': lowercase a-z, 0-9, '_', '-' only"
        )
    if isinstance(e, (AuthenticationException, AuthorizationException)):
        return KibanaAuthError(f"Kibana rejected the API key or permissions: {e.message}")
    if isinstance(e, NotFoundError):
        return KibanaNotFound(str(e.message))
    if isinstance(e, ConflictError):
        # 409: e.g. an optimistic-concurrency version conflict on update — a
        # payload/state rejection, NOT an outage (ConflictError is not a
        # BadRequestError, so it must be mapped explicitly).
        return KibanaRejected("Kibana rejected the request (conflict)", detail=str(e.message))
    if isinstance(e, BadRequestError):
        return KibanaRejected("Kibana rejected the payload", detail=str(e.message))
    if isinstance(e, TransportError):
        return KibanaUnavailable(
            "cannot reach Kibana — check that the server's KIBANA_URL is reachable"
        )
    if isinstance(e, (ApiError, KibanaException)):
        return KibanaUnavailable("Kibana returned an unexpected error")
    return e


def _scoped(gw: "KibanaPyGateway", e: KibanaNotFound) -> KibanaNotFound:
    """Append " (in space '<id>')" once, never to space-origin errors,
    never on the default path, always as a NEW exception."""
    space = gw._space
    if space is None or isinstance(e, KibanaSpaceNotFound):
        return e
    suffix = f" (in space '{space}')"
    if e.message.endswith(suffix):
        return e
    return KibanaNotFound(e.message + suffix)


def _translate_errors[F: Callable[..., Any]](fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(self: "KibanaPyGateway", *args: Any, **kwargs: Any) -> Any:
        # A sibling `except` cannot see an exception raised inside another
        # handler, so the scoped-suffix pass is an OUTER try around the
        # translation try — one suffix site for translated 404s and
        # adapter-raised misses alike.
        try:
            try:
                return fn(self, *args, **kwargs)
            except (
                AuthenticationException,
                AuthorizationException,
                NotFoundError,
                ConflictError,
                BadRequestError,
                TransportError,
                ApiError,
                KibanaException,
            ) as e:
                translated = _translated(e, self._space)
                if translated is e:
                    raise
                raise translated from e
        except KibanaNotFound as e:
            scoped = _scoped(self, e)
            if scoped is not e:
                raise scoped from e
            raise

    return wrapper  # type: ignore[return-value]


def _normalize_fields(raw: dict[str, Any] | list[dict[str, Any]]) -> dict[str, str]:
    if isinstance(raw, dict):
        return {name: spec.get("type", "unknown") for name, spec in raw.items()}
    return {f["name"]: f.get("type", "unknown") for f in raw}


def _to_short_url(body: dict[str, Any]) -> ShortUrl:
    return ShortUrl(
        id=body["id"],
        slug=body.get("slug", ""),
        locator_id=(body.get("locator") or {}).get("id", ""),
        url=body.get("url"),
    )


def _to_alert_rule(body: dict[str, Any]) -> AlertRule:
    return AlertRule(
        id=body["id"],
        name=body.get("name", ""),
        rule_type_id=body.get("rule_type_id", ""),
        consumer=body.get("consumer", ""),
        enabled=bool(body.get("enabled", False)),
        schedule_interval=(body.get("schedule") or {}).get("interval", ""),
        status=(body.get("execution_status") or {}).get("status", ""),
        tags=tuple(body.get("tags") or []),
    )


def _to_connector(body: dict[str, Any]) -> Connector:
    return Connector(
        id=body["id"],
        name=body.get("name", ""),
        connector_type_id=body.get("connector_type_id", ""),
        is_missing_secrets=bool(body.get("is_missing_secrets", False)),
        is_preconfigured=bool(body.get("is_preconfigured", False)),
    )


def _to_case(body: dict[str, Any]) -> Case:
    return Case(
        id=body["id"],
        title=body.get("title", ""),
        status=body.get("status", ""),
        severity=body.get("severity", ""),
        owner=body.get("owner", ""),
        tags=tuple(body.get("tags") or []),
        total_comments=int(body.get("totalComment", 0) or 0),
    )


def _to_synthetic_monitor(body: dict[str, Any]) -> SyntheticMonitor:
    # Shape observed live from a seeded monitor: id==config_id; schedule is
    # {number,unit}; locations are objects (surface the human label); http has
    # `url`, tcp/icmp have `host`, browser has neither (target None).
    sched = body.get("schedule")
    if isinstance(sched, dict) and sched.get("number") and sched.get("unit"):
        schedule = f"{sched['number']}{sched['unit']}"  # e.g. "10m"
    else:
        schedule = ""  # partial/missing schedule -> empty, never a half-formed "10"
    locations = tuple(
        loc.get("label") or loc.get("id", "")
        for loc in (body.get("locations") or [])
        if isinstance(loc, dict)
    )
    return SyntheticMonitor(
        id=body.get("config_id") or body.get("id", ""),
        name=body.get("name", ""),
        type=body.get("type", ""),
        enabled=bool(body.get("enabled", False)),
        tags=tuple(body.get("tags") or []),
        locations=locations,
        schedule=schedule,
        target=body.get("url") or body.get("host"),
    )


def _to_synthetic_param(body: dict[str, Any]) -> SyntheticParam:
    return SyntheticParam(
        id=body.get("id", ""),
        key=body.get("key", ""),
        description=body.get("description", ""),
        tags=tuple(body.get("tags") or []),
    )


def _to_private_location(body: dict[str, Any]) -> SyntheticPrivateLocation:
    return SyntheticPrivateLocation(
        id=body.get("id", ""),
        label=body.get("label", ""),
        agent_policy_id=body.get("agentPolicyId", ""),
        is_invalid=bool(body.get("isInvalid", False)),
        tags=tuple(body.get("tags") or []),
    )


def _to_uptime_settings(body: dict[str, Any]) -> UptimeSettings:
    email = body.get("defaultEmail")
    email = email if isinstance(email, dict) else {}
    return UptimeSettings(
        heartbeat_indices=body.get("heartbeatIndices", ""),
        cert_expiration_threshold=body.get("certExpirationThreshold", 0),
        cert_age_threshold=body.get("certAgeThreshold", 0),
        default_connectors=tuple(body.get("defaultConnectors") or []),
        default_email=EmailDefaults(
            to=tuple(email.get("to") or []),
            cc=tuple(email.get("cc") or []),
            bcc=tuple(email.get("bcc") or []),
        ),
    )


def _to_apm_agent_config(body: dict[str, Any]) -> ApmAgentConfig:
    service = body.get("service")
    service = service if isinstance(service, dict) else {}
    return ApmAgentConfig(
        service_name=service.get("name"),
        service_environment=service.get("environment"),
        settings=body.get("settings") or {},
        applied_by_agent=bool(body.get("applied_by_agent", False)),
        etag=body.get("etag", ""),
    )


def _to_apm_environment(body: dict[str, Any]) -> ApmEnvironment:
    return ApmEnvironment(
        name=body.get("name", ""),
        already_configured=bool(body.get("alreadyConfigured", False)),
    )


def _to_apm_sourcemap(body: dict[str, Any]) -> ApmSourcemap:
    return ApmSourcemap(
        identifier=body.get("identifier", ""),
        created=str(body.get("created", "")),
    )


def _to_apm_annotation(body: dict[str, Any]) -> ApmAnnotation:
    return ApmAnnotation(
        id=body.get("id", ""),
        timestamp=body.get("@timestamp", ""),
        text=body.get("text", ""),
        type=body.get("type", ""),
    )


def _to_detection_rule(body: dict[str, Any]) -> DetectionRule:
    # Shape confirmed live from a seeded rule (create -> GET -> delete).
    return DetectionRule(
        id=body.get("id", ""),
        rule_id=body.get("rule_id", ""),
        name=body.get("name", ""),
        enabled=bool(body.get("enabled", False)),
        type=body.get("type", ""),
        severity=body.get("severity", ""),
        risk_score=int(body.get("risk_score", 0) or 0),
        tags=tuple(body.get("tags") or []),
        immutable=bool(body.get("immutable", False)),
        version=int(body.get("version", 0) or 0),
    )


def _to_prepackaged_status(body: dict[str, Any]) -> PrepackagedRulesStatus:
    return PrepackagedRulesStatus(
        rules_installed=int(body.get("rules_installed", 0) or 0),
        rules_not_installed=int(body.get("rules_not_installed", 0) or 0),
        rules_custom_installed=int(body.get("rules_custom_installed", 0) or 0),
        rules_not_updated=int(body.get("rules_not_updated", 0) or 0),
        timelines_installed=int(body.get("timelines_installed", 0) or 0),
        timelines_not_installed=int(body.get("timelines_not_installed", 0) or 0),
        timelines_not_updated=int(body.get("timelines_not_updated", 0) or 0),
    )


def _to_exception_list(body: dict[str, Any]) -> ExceptionList:
    # Shape confirmed live from a seeded exception list.
    return ExceptionList(
        id=body.get("id", ""),
        list_id=body.get("list_id", ""),
        name=body.get("name", ""),
        type=body.get("type", ""),
        namespace_type=body.get("namespace_type", ""),
        tags=tuple(body.get("tags") or []),
        os_types=tuple(body.get("os_types") or []),
    )


def _to_exception_item(body: dict[str, Any]) -> ExceptionItem:
    return ExceptionItem(
        id=body.get("id", ""),
        item_id=body.get("item_id", ""),
        name=body.get("name", ""),
        list_id=body.get("list_id", ""),
    )


def _to_value_list(body: dict[str, Any]) -> ValueList:
    return ValueList(
        id=body.get("id", ""),
        name=body.get("name", ""),
        type=body.get("type", ""),
        description=body.get("description") or "",
    )


def _to_value_list_item(body: dict[str, Any]) -> ValueListItem:
    return ValueListItem(
        id=body.get("id", ""),
        list_id=body.get("list_id", ""),
        value=body.get("value", ""),
        type=body.get("type", ""),
        timestamp=body.get("@timestamp") or "",
    )


def _to_timeline(body: dict[str, Any]) -> Timeline:
    return Timeline(
        saved_object_id=body.get("savedObjectId", ""),
        title=body.get("title") or "",
        description=body.get("description") or "",
    )


def _alert_field(fields: dict[str, Any], key: str) -> str:
    # ES `fields` returns each value as an array, flat-dotted regardless of
    # whether _source is nested — shape-agnostic. Take the first, defensively.
    vals = fields.get(key)
    return str(vals[0]) if isinstance(vals, list) and vals else ""


def _to_detection_alert(hit: dict[str, Any]) -> DetectionAlert:
    # An ES search hit read via the `fields` projection (see search_alerts). The
    # populated shape is unverified (a firing alert can't be seeded here), so the
    # kibana.alert.* fields are read defensively.
    fields = hit.get("fields") or {}
    return DetectionAlert(
        id=hit.get("_id", ""),
        rule_name=_alert_field(fields, "kibana.alert.rule.name"),
        severity=_alert_field(fields, "kibana.alert.severity"),
        status=_alert_field(fields, "kibana.alert.workflow_status"),
        timestamp=_alert_field(fields, "@timestamp"),
    )


def _to_import_result(body: dict[str, Any]) -> SavedObjectImportResult:
    results = body.get("successResults") or []
    return SavedObjectImportResult(
        success=bool(body.get("success", False)),
        imported_count=int(body.get("successCount", 0) or 0),
        objects=tuple(
            ImportedObject(
                type=r.get("type", ""),
                source_id=r.get("id", ""),
                # create_new_copies mints a new id in destinationId (fallback to id).
                destination_id=r.get("destinationId") or r.get("id", ""),
            )
            for r in results
        ),
        # Warning `type` only (e.g. "action_required") — like errors, kept
        # content-free: a raw `message` can echo object-derived text.
        warnings=tuple(
            w.get("type", "warning") if isinstance(w, dict) else "warning"
            for w in (body.get("warnings") or [])
        ),
        # On success=false Kibana returns errors:[{type,id,error:{type,...}}]. Map
        # to "type/id: reason" — identity + failure kind only, never object bytes.
        errors=tuple(
            f"{e.get('type', '?')}/{e.get('id', '?')}: {(e.get('error') or {}).get('type', 'error')}"
            for e in (body.get("errors") or [])
            if isinstance(e, dict)
        ),
    )


def _to_space(body: dict[str, Any]) -> Space:
    return Space(
        id=body.get("id", ""),
        name=body.get("name", ""),
        description=body.get("description"),
        solution=body.get("solution"),
        disabled_features=tuple(body.get("disabledFeatures") or []),
        reserved=bool(body.get("_reserved", False)),
    )


def _to_role(body: dict[str, Any]) -> Role:
    es = body.get("elasticsearch")
    es = es if isinstance(es, dict) else {}
    meta = body.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    index_privileges = tuple(
        RoleIndexPrivilege(
            names=tuple(ip.get("names") or []),
            privileges=tuple(ip.get("privileges") or []),
        )
        for ip in (es.get("indices") or [])
        if isinstance(ip, dict)
    )
    kibana_privileges = tuple(
        RoleKibanaPrivilege(
            base=tuple(kp.get("base") or []),
            # feature is {featureName: [privs]}; summarize to sorted feature names.
            features=tuple(sorted((kp.get("feature") or {}))),
            spaces=tuple(kp.get("spaces") or []),
        )
        for kp in (body.get("kibana") or [])
        if isinstance(kp, dict)
    )
    return Role(
        name=body.get("name", ""),
        description=body.get("description"),
        reserved=bool(meta.get("_reserved", False)),
        cluster_privileges=tuple(es.get("cluster") or []),
        index_privileges=index_privileges,
        run_as=tuple(es.get("run_as") or []),
        kibana_privileges=kibana_privileges,
    )


def _to_upgrade_readiness(body: dict[str, Any]) -> UpgradeReadiness:
    es_logs = body.get("recentEsDeprecationLogs")
    es_logs = es_logs if isinstance(es_logs, dict) else {}
    # Keep title/level/type only; message[]/correctiveActions embed live
    # call-counts + timestamps (non-deterministic) — see the env-research lesson.
    api_deprecations = tuple(
        ApiDeprecation(
            title=d.get("title", ""),
            level=d.get("level", ""),
            type=d.get("deprecationType"),
        )
        for d in (body.get("kibanaApiDeprecations") or [])
        if isinstance(d, dict)
    )
    return UpgradeReadiness(
        ready_for_upgrade=bool(body.get("readyForUpgrade", False)),
        details=body.get("details"),
        es_deprecation_count=int(es_logs.get("count") or 0),
        api_deprecations=api_deprecations,
    )


def _stream_lifecycle(ingest: dict[str, Any]) -> tuple[str, str | None]:
    # ingest.lifecycle is a single-key discriminated object:
    # {dsl|ilm|inherit|disabled: {...}}. The key is the mode; data_retention (when
    # set) nests under it. Absent/empty -> ("", None).
    lifecycle = ingest.get("lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    mode = next(iter(lifecycle), "")
    inner = lifecycle.get(mode)
    inner = inner if isinstance(inner, dict) else {}
    return mode, inner.get("data_retention")


def _stream_counts(ingest: dict[str, Any]) -> tuple[int, int, dict[str, str]]:
    # processing.steps -> count; wired.routing -> count; wired.fields -> name->type
    # map (wired streams only; classic streams have no wired block -> empty).
    processing = ingest.get("processing")
    processing = processing if isinstance(processing, dict) else {}
    steps = processing.get("steps")
    step_count = len(steps) if isinstance(steps, list) else 0
    wired = ingest.get("wired")
    wired = wired if isinstance(wired, dict) else {}
    routing = wired.get("routing")
    routing_count = len(routing) if isinstance(routing, list) else 0
    raw_fields = wired.get("fields")
    raw_fields = raw_fields if isinstance(raw_fields, dict) else {}
    # "unknown" for a typeless field spec, matching _normalize_fields (data views).
    fields = {
        name: (spec.get("type", "unknown") if isinstance(spec, dict) else "unknown")
        for name, spec in raw_fields.items()
    }
    return step_count, routing_count, fields


def _to_stream_summary(body: dict[str, Any]) -> StreamSummary:
    return StreamSummary(
        name=body.get("name", ""),
        type=body.get("type", ""),
        description=body.get("description", ""),
    )


def _to_stream(body: dict[str, Any]) -> Stream:
    ingest = body.get("ingest")
    ingest = ingest if isinstance(ingest, dict) else {}
    lifecycle, data_retention = _stream_lifecycle(ingest)
    step_count, routing_count, fields = _stream_counts(ingest)
    return Stream(
        name=body.get("name", ""),
        type=body.get("type", ""),
        description=body.get("description", ""),
        updated_at=body.get("updated_at", ""),
        lifecycle=lifecycle,
        data_retention=data_retention,
        processing_step_count=step_count,
        routing_count=routing_count,
        field_count=len(fields),
    )


def _to_stream_ingest(ingest: dict[str, Any]) -> StreamIngest:
    lifecycle, data_retention = _stream_lifecycle(ingest)
    step_count, routing_count, fields = _stream_counts(ingest)
    return StreamIngest(
        lifecycle=lifecycle,
        data_retention=data_retention,
        processing_step_count=step_count,
        routing_count=routing_count,
        fields=fields,
    )


# Lifecycle modes set_stream_retention may convert to DSL (allowlist: an 'ilm'
# or a future managed mode is refused, never silently clobbered).
_RETENTION_MODES_OK = {"dsl", "inherit", "disabled", ""}
# The 9.4 wired root streams — a LOAD-BEARING floor for the delete guard (the
# dynamic wired-parent check catches future roots; this catches these two even
# if the stream list drifts).
_KNOWN_ROOTS = frozenset({"logs.ecs", "logs.otel"})


def _to_stream_write_result(body: dict[str, Any]) -> StreamWriteResult:
    # Surface-not-swallow: raise only when NEITHER key is present. delete/disable
    # may return {result:...} without acknowledged, so an acknowledged-only check
    # would error a *successful* destructive op.
    if "acknowledged" not in body and "result" not in body:
        raise KibanaUnavailable("unexpected Streams API response: no acknowledged/result")
    if body.get("acknowledged") is False:
        raise KibanaUnavailable(
            f"Streams API did not acknowledge the request (result={body.get('result', '')!r})"
        )
    return StreamWriteResult(
        acknowledged=bool(body.get("acknowledged", "result" in body)),
        result=str(body.get("result", "")),
    )


# Fields the Dashboards update API actually accepts. Shared between
# get_dashboard_data (to warn about fields that would be silently dropped on
# a read-modify-write round trip) and update_dashboard (to enforce it).
_UPDATABLE_KEYS = frozenset({
    "title", "description", "panels", "options", "filters", "query",
    "time_range", "refresh_interval", "tags", "pinned_panels",
})


# --- fleet mappers. Three redact secrets before crossing the port: enrollment
# keys drop api_key/api_key_id, outputs map only non-secret fields, and uninstall
# tokens never carry their decrypted value (that read isn't exposed). ---


def _to_fleet_agent(body: dict[str, Any]) -> FleetAgent:
    lm = body.get("local_metadata") or {}
    host = lm.get("host") or {}
    agent = (lm.get("elastic") or {}).get("agent") or {}
    return FleetAgent(
        id=body.get("id", ""),
        status=body.get("status", ""),
        policy_id=body.get("policy_id"),
        active=bool(body.get("active", False)),
        hostname=host.get("hostname", ""),
        version=agent.get("version", ""),
        enrolled_at=body.get("enrolled_at", ""),
        last_checkin=body.get("last_checkin"),
        last_checkin_status=body.get("last_checkin_status"),
    )


def _to_fleet_agent_status(results: dict[str, Any]) -> FleetAgentStatus:
    return FleetAgentStatus(
        online=int(results.get("online", 0)),
        error=int(results.get("error", 0)),
        offline=int(results.get("offline", 0)),
        inactive=int(results.get("inactive", 0)),
        updating=int(results.get("updating", 0)),
        unenrolled=int(results.get("unenrolled", 0)),
        total=int(results.get("all", 0)),
    )


def _to_fleet_agent_policy(body: dict[str, Any]) -> FleetAgentPolicy:
    return FleetAgentPolicy(
        id=body.get("id", ""),
        name=body.get("name", ""),
        namespace=body.get("namespace", ""),
        description=body.get("description"),
        agent_count=int(body.get("agents") or 0),
        status=body.get("status", ""),
        is_managed=bool(body.get("is_managed", False)),
        updated_at=body.get("updated_at", ""),
        monitoring_enabled=tuple(body.get("monitoring_enabled") or ()),
    )


def _to_fleet_package_policy(body: dict[str, Any]) -> FleetPackagePolicy:
    pkg = body.get("package") or {}
    return FleetPackagePolicy(
        id=body.get("id", ""),
        name=body.get("name", ""),
        namespace=body.get("namespace", ""),
        enabled=bool(body.get("enabled", False)),
        agent_policy_id=body.get("policy_id"),
        package_name=pkg.get("name", ""),
        package_title=pkg.get("title", ""),
        package_version=pkg.get("version", ""),
        description=body.get("description"),
    )


def _to_fleet_enrollment_key(body: dict[str, Any]) -> FleetEnrollmentKey:
    # api_key + api_key_id deliberately dropped — secret, enrolls agents.
    return FleetEnrollmentKey(
        id=body.get("id", ""),
        name=body.get("name"),
        policy_id=body.get("policy_id"),
        active=bool(body.get("active", False)),
        created_at=body.get("created_at", ""),
    )


def _to_fleet_uninstall_token(body: dict[str, Any]) -> FleetUninstallToken:
    return FleetUninstallToken(
        id=body.get("id", ""),
        policy_id=body.get("policy_id"),
        policy_name=body.get("policy_name"),
        created_at=body.get("created_at", ""),
    )


def _to_fleet_package(body: dict[str, Any]) -> FleetPackage:
    return FleetPackage(
        name=body.get("name", ""),
        title=body.get("title", ""),
        version=body.get("version", ""),
        status=body.get("status", ""),
        description=body.get("description", ""),
        type=body.get("type"),
    )


def _to_fleet_category(body: dict[str, Any]) -> FleetPackageCategory:
    return FleetPackageCategory(
        id=body.get("id", ""),
        title=body.get("title", ""),
        count=int(body.get("count") or 0),
    )


def _to_fleet_output(body: dict[str, Any]) -> FleetOutput:
    # Only non-secret fields — ssl/secrets/api-key fields are never mapped.
    return FleetOutput(
        id=body.get("id", ""),
        name=body.get("name", ""),
        type=body.get("type", ""),
        hosts=tuple(body.get("hosts") or ()),
        is_default=bool(body.get("is_default", False)),
        is_default_monitoring=bool(body.get("is_default_monitoring", False)),
    )


def _to_fleet_output_health(body: dict[str, Any]) -> FleetOutputHealth:
    return FleetOutputHealth(
        state=body.get("state", ""),
        message=body.get("message", ""),
        timestamp=body.get("timestamp", ""),
    )


def _to_fleet_server_host(body: dict[str, Any]) -> FleetServerHost:
    return FleetServerHost(
        id=body.get("id", ""),
        name=body.get("name", ""),
        host_urls=tuple(body.get("host_urls") or ()),
        is_default=bool(body.get("is_default", False)),
    )


def _fleet_all_items(fetch: Callable[[int], dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk every page of a Fleet list endpoint ({items, total, page, perPage})
    so a fleet with >100 agents/policies/keys is never silently truncated —
    mirroring the pagination the other list_* gateway methods do."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        body = fetch(page)
        batch = body.get("items", [])
        items.extend(batch)
        total = body.get("total")
        if not batch or not isinstance(total, int) or len(items) >= total:
            break
        page += 1
    return items


def _rmw_body(
    update_method: Callable[..., Any], raw: dict[str, Any], changes: dict[str, Any]
) -> dict[str, Any]:
    """Read-modify-write body for a Kibana update call. `update_method`'s own
    signature is the writable-fields allowlist (read-only response fields like
    id/revision/updated_at are never real update kwargs, so they drop out
    naturally); `changes` then overrides on top of the retained raw values."""
    allowed = set(inspect.signature(update_method).parameters) - {"self"}
    body = {k: v for k, v in raw.items() if k in allowed}
    body.update({k: v for k, v in changes.items() if k in allowed})
    return body


class KibanaPyGateway:
    def __init__(
        self, client: kibana.Kibana | SpaceScopedKibana, space: str | None = None
    ) -> None:
        self._client = client
        self._space = space

    @classmethod
    def connect(cls, url: str, api_key: str, space: str | None = None) -> "KibanaPyGateway":
        if space is not None and is_space_pinned(url):
            # connect owns this URL, so it enforces its own precondition; the
            # factory adds only the public-URL half it alone can see.
            raise KibanaRejected(
                "KIBANA_URL is already space-pinned ('/s/<id>' base path); "
                "the `space` parameter cannot be used with this deployment"
            )
        # `root` keeps the concrete type: only kibana.Kibana declares
        # .space(), so calling it on the union-typed name fails mypy strict.
        root = kibana.Kibana(url, api_key=api_key)
        client: kibana.Kibana | SpaceScopedKibana = root
        if space is not None:
            try:
                client = root.space(space, validate=True)
            except Exception as e:
                # Best-effort: kibana-py's close() re-raises transport
                # failures, which would supersede the crafted guidance error.
                with contextlib.suppress(Exception):
                    root.close()
                translated = _translated(e, space)
                if isinstance(translated, KibanaAuthError):
                    raise KibanaAuthError(
                        f"{translated.message} (raised while validating space "
                        f"'{space}'; the API key must be valid and able to read "
                        "spaces)"
                    ) from e
                if translated is e:
                    raise  # pass-through unchanged, no self-referential cause
                raise translated from e
        return cls(client, space)

    # Returns the port type, not the concrete one: `with KibanaPyGateway...` is
    # the only way callers obtain a gateway, so narrowing here would leak the
    # adapter type into every `with` block and let a caller reach past the port.
    def __enter__(self) -> KibanaGateway:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._client.close()

    @_translate_errors
    def list_data_views(self) -> list[DataViewSummary]:
        body = self._client.data_views.get_all().body
        return [
            DataViewSummary(
                id=v["id"], name=v.get("name") or v["title"], index_pattern=v["title"]
            )
            for v in body.get("data_view", [])
        ]

    @_translate_errors
    def get_data_view(self, name_or_id: str) -> DataViewDetail:
        views = self.list_data_views()
        for tier in (
            [v for v in views if v.id == name_or_id],
            [v for v in views if v.name == name_or_id],
            [v for v in views if v.index_pattern == name_or_id],
        ):
            if tier:
                if len(tier) > 1:
                    raise KibanaRejected(
                        f"'{name_or_id}' matches {len(tier)} data views "
                        f"({', '.join(v.id for v in tier)}); pass the id"
                    )
                match = tier[0]
                break
        else:
            raise KibanaNotFound(
                f"data view '{name_or_id}' not found — call list_data_views to see what exists"
            )
        raw = self._client.data_views.get(view_id=match.id).body["data_view"]
        return DataViewDetail(
            id=raw["id"],
            name=raw.get("name") or raw["title"],
            index_pattern=raw["title"],
            time_field=raw.get("timeFieldName"),
            fields=_normalize_fields(raw.get("fields", {})),
        )

    @_translate_errors
    def create_data_view(
        self, index_pattern: str, name: str | None, time_field: str | None
    ) -> DataViewSummary:
        spec: dict[str, Any] = {"title": index_pattern}
        if name is not None:
            spec["name"] = name
        if time_field is not None:
            spec["timeFieldName"] = time_field
        raw = self._client.data_views.create(data_view=spec).body["data_view"]
        return DataViewSummary(
            id=raw["id"], name=raw.get("name") or raw["title"], index_pattern=raw["title"]
        )

    @_translate_errors
    def delete_data_view(self, view_id: str) -> None:
        self._client.data_views.delete(view_id=view_id)

    @_translate_errors
    def create_short_url(self, locator_id: str, params: dict[str, Any]) -> ShortUrl:
        return _to_short_url(
            self._client.short_urls.create(locator_id=locator_id, params=params).body
        )

    @_translate_errors
    def resolve_short_url(self, slug: str) -> ShortUrl:
        return _to_short_url(self._client.short_urls.resolve(slug=slug).body)

    @_translate_errors
    def delete_short_url(self, short_url_id: str) -> None:
        self._client.short_urls.delete(id=short_url_id)

    @_translate_errors
    def list_alert_rules(self, search: str | None) -> list[AlertRule]:
        # rule.find paginates (default per_page=10); walk every page to
        # exhaustion so the returned list is COMPLETE, never silently truncated.
        base: dict[str, Any] = {"per_page": 100}
        if search:
            base["search"] = search
        rules: list[AlertRule] = []
        page = 1
        while True:
            body = self._client.alerting.rule.find(**base, page=page).body
            batch = body.get("data", [])
            rules.extend(_to_alert_rule(r) for r in batch)
            total = body.get("total")
            if not batch or (isinstance(total, int) and len(rules) >= total):
                break
            page += 1
        return rules

    @_translate_errors
    def get_alert_rule(self, rule_id: str) -> AlertRule:
        return _to_alert_rule(self._client.alerting.rule.get(id=rule_id).body)

    @_translate_errors
    def get_alerting_health(self) -> AlertingHealth:
        body = self._client.alerting.health().body
        fw = body.get("alerting_framework_health") or {}
        sub = [(fw.get(k) or {}).get("status") for k in
               ("decryption_health", "execution_health", "read_health")]
        # error > warn > ok; a missing/None sub-status is "unknown", not silently
        # "ok" (a health tool must not hide absent signals).
        if "error" in sub:
            status = "error"
        elif "warn" in sub:
            status = "warn"
        elif None in sub or not fw:
            status = "unknown"
        else:
            status = "ok"
        return AlertingHealth(
            status=status,
            has_permanent_encryption_key=bool(body.get("has_permanent_encryption_key")),
            is_sufficiently_secure=bool(body.get("is_sufficiently_secure")),
        )

    @_translate_errors
    def create_alert_rule(
        self, name: str, rule_type_id: str, consumer: str, schedule_interval: str,
        params: dict[str, Any], tags: list[str] | None, enabled: bool,
    ) -> AlertRule:
        body = self._client.alerting.rule.create(
            name=name, consumer=consumer, rule_type_id=rule_type_id,
            schedule={"interval": schedule_interval}, params=params,
            tags=tags or [], enabled=enabled,
        ).body
        return _to_alert_rule(body)

    @_translate_errors
    def enable_alert_rule(self, rule_id: str) -> None:
        self._client.alerting.rule.enable(id=rule_id)

    @_translate_errors
    def disable_alert_rule(self, rule_id: str) -> None:
        self._client.alerting.rule.disable(id=rule_id)

    @_translate_errors
    def delete_alert_rule(self, rule_id: str) -> None:
        self._client.alerting.rule.delete(id=rule_id)

    @_translate_errors
    def list_connectors(self) -> list[Connector]:
        return [_to_connector(c) for c in self._client.connectors.get_all().body]

    @_translate_errors
    def create_connector(
        self, name: str, connector_type_id: str, config: dict[str, Any] | None,
        secrets: dict[str, Any] | None,
    ) -> Connector:
        kwargs: dict[str, Any] = {"name": name, "connector_type_id": connector_type_id}
        if config is not None:
            kwargs["config"] = config
        if secrets is not None:
            kwargs["secrets"] = secrets
        return _to_connector(self._client.connectors.create(**kwargs).body)

    @_translate_errors
    def delete_connector(self, connector_id: str) -> None:
        self._client.connectors.delete(id=connector_id)

    @_translate_errors
    def execute_connector(self, connector_id: str, params: dict[str, Any]) -> dict[str, Any]:
        body = self._client.connectors.execute(id=connector_id, params=params).body
        # execute can return HTTP 200 with status="error"; surface the reason so a
        # failed real-world action isn't reported as a bare "error".
        result = {"connector_id": connector_id, "status": body.get("status", "")}
        if result["status"] != "ok":
            detail = body.get("service_message") or body.get("message")
            if detail:
                result["message"] = detail
        return result

    @_translate_errors
    def list_cases(self, search: str | None) -> list[Case]:
        base: dict[str, Any] = {"per_page": 100}
        if search:
            base["search"] = search
        cases: list[Case] = []
        page = 1
        while True:
            body = self._client.cases.find(**base, page=page).body
            batch = body.get("cases", [])
            cases.extend(_to_case(cs) for cs in batch)
            total = body.get("total")
            if not batch or (isinstance(total, int) and len(cases) >= total):
                break
            page += 1
        return cases

    @_translate_errors
    def get_case(self, case_id: str) -> Case:
        return _to_case(self._client.cases.get(case_id=case_id).body)

    @_translate_errors
    def create_case(
        self, title: str, description: str, tags: list[str] | None, severity: str | None
    ) -> Case:
        kwargs: dict[str, Any] = {"title": title, "description": description, "tags": tags or []}
        if severity is not None:
            kwargs["severity"] = severity
        return _to_case(self._client.cases.create(**kwargs).body)

    @_translate_errors
    def update_case(
        self, case_id: str, status: str | None, severity: str | None,
        tags: list[str] | None, title: str | None,
    ) -> Case:
        # Read-modify-write: fetch the current optimistic-concurrency version so
        # the caller never has to track it. A concurrent edit -> Kibana version
        # conflict -> KibanaRejected -> ToolError (never a silent clobber).
        version = self._client.cases.get(case_id=case_id).body["version"]
        fields = {"status": status, "severity": severity, "tags": tags, "title": title}
        kwargs: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
        updated = self._client.cases.update(id=case_id, version=version, **kwargs).body
        # The PATCH response is a list of updated cases (probe C1c). Guard an empty
        # or unexpected shape with a clean domain error rather than an IndexError.
        if isinstance(updated, list):
            if not updated:
                raise KibanaUnavailable("case update returned no updated case")
            body = updated[0]
        else:
            body = updated
        return _to_case(body)

    @_translate_errors
    def add_case_comment(self, case_id: str, comment: str) -> Case:
        body = self._client.cases.add_comment(case_id=case_id, comment=comment, type="user").body
        return _to_case(body)

    @_translate_errors
    def delete_case(self, case_id: str) -> None:
        self._client.cases.delete(ids=[case_id])

    @_translate_errors
    def search_dashboards(self, query: str | None) -> list[DashboardSummary]:
        base: dict[str, Any] = {"per_page": 100}
        if query:
            base["query"] = query
        found: list[DashboardSummary] = []
        page = 1
        while True:
            body = self._client.dashboards.get_all(**base, page=page).body
            batch = body.get("dashboards", [])
            found.extend(
                DashboardSummary(
                    id=item["id"],
                    title=item["data"].get("title", ""),
                    description=item["data"].get("description", ""),
                )
                for item in batch
            )
            total = body.get("total")
            if not batch or (isinstance(total, int) and len(found) >= total):
                break
            page += 1
        return found

    @_translate_errors
    def get_dashboard(self, dashboard_id: str) -> DashboardDetail:
        body = self._client.dashboards.get(id=dashboard_id).body
        data = body["data"]
        panels = tuple(
            PanelSummary(
                index=i,
                type=p.get("type", "unknown"),
                title=(p.get("config") or {}).get("title", ""),
            )
            for i, p in enumerate(data.get("panels", []))
        )
        return DashboardDetail(
            id=body["id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            panels=panels,
        )

    @_translate_errors
    def get_dashboard_data(self, dashboard_id: str) -> tuple[dict[str, Any], list[str]]:
        body = self._client.dashboards.get(id=dashboard_id).body
        data = body["data"]
        warnings = body.get("warnings") or (body.get("meta") or {}).get("warnings") or []
        warnings = [str(w) for w in warnings]
        unexpected = set(data) - _UPDATABLE_KEYS
        if unexpected:
            warnings.append(
                f"dashboard contains fields the update API cannot round-trip: {sorted(unexpected)}"
            )
        return data, warnings

    @_translate_errors
    def create_dashboard(self, data: dict[str, Any]) -> str:
        # The JSON body is free-form Any; the local pins the id to the declared str.
        dashboard_id: str = self._client.dashboards.create(**data).body["id"]
        return dashboard_id

    @_translate_errors
    def update_dashboard(self, dashboard_id: str, data: dict[str, Any]) -> None:
        kwargs = {k: v for k, v in data.items() if k in _UPDATABLE_KEYS}
        self._client.dashboards.update(id=dashboard_id, **kwargs)

    @_translate_errors
    def upsert_dashboard(self, dashboard_id: str, data: dict[str, Any]) -> str:
        # PUT /api/dashboards/{id} is an upsert (create-or-replace) on a
        # client-chosen id. Same _UPDATABLE_KEYS filter as update_dashboard.
        kwargs = {k: v for k, v in data.items() if k in _UPDATABLE_KEYS}
        self._client.dashboards.update(id=dashboard_id, **kwargs)
        return dashboard_id

    @_translate_errors
    def create_visualization(self, config: dict[str, Any]) -> str:
        visualization_id: str = self._client.visualizations.create(data=config).body["id"]
        return visualization_id

    @_translate_errors
    def delete_dashboard(self, dashboard_id: str) -> None:
        self._client.dashboards.delete(id=dashboard_id)

    @_translate_errors
    def delete_visualization(self, visualization_id: str) -> None:
        self._client.visualizations.delete(id=visualization_id)

    @_translate_errors
    def get_kibana_status(self) -> KibanaStatus:
        # Summarize: the raw body carries a large `metrics` blob and a full
        # per-service tree; we keep the overall verdict, version, and only the
        # core/plugin services that are NOT "available" (surface problems).
        body = self._client.status.get_status().body
        status = body.get("status") or {}
        overall = status.get("overall") or {}
        unhealthy = tuple(
            ServiceHealth(name=name, level=svc.get("level", "unknown"), summary=svc.get("summary", ""))
            for group in ("core", "plugins")
            for name, svc in (status.get(group) or {}).items()
            if isinstance(svc, dict) and svc.get("level") != "available"
        )
        return KibanaStatus(
            overall_level=overall.get("level", "unknown"),
            overall_summary=overall.get("summary", ""),
            version=(body.get("version") or {}).get("number", "unknown"),
            unhealthy=unhealthy,
        )

    @_translate_errors
    def get_kibana_stats(self) -> KibanaStats:
        body = self._client.status.get_stats().body
        process = body.get("process") or {}
        heap = (process.get("memory") or {}).get("heap") or {}
        return KibanaStats(
            heap_used_bytes=heap.get("used_bytes", 0),
            heap_total_bytes=heap.get("total_bytes", 0),
            heap_size_limit_bytes=heap.get("size_limit", 0),
            event_loop_delay_ms=process.get("event_loop_delay", 0.0),
            concurrent_connections=body.get("concurrent_connections", 0),
        )

    @_translate_errors
    def get_task_manager_health(self) -> TaskManagerHealth:
        body = self._client.task_manager.health().body
        return TaskManagerHealth(
            status=body.get("status", "unknown"),
            timestamp=body.get("timestamp", ""),
            last_update=body.get("last_update", ""),
        )

    # --- observability: synthetics + uptime + apm-config (read-first) ---

    @_translate_errors
    def list_synthetic_monitors(self) -> list[SyntheticMonitor]:
        # get_monitors paginates; walk to exhaustion via `total` (the grand total;
        # `absoluteTotal` is the unfiltered count) so the list is never truncated.
        monitors: list[SyntheticMonitor] = []
        page = 1
        while True:
            body = self._client.synthetics.get_monitors(page=page, per_page=100).body
            batch = body.get("monitors", [])
            monitors.extend(_to_synthetic_monitor(m) for m in batch)
            total = body.get("total")
            if not batch or (isinstance(total, int) and len(monitors) >= total):
                break
            page += 1
        return monitors

    @_translate_errors
    def get_synthetic_monitor(self, monitor_id: str) -> SyntheticMonitor:
        return _to_synthetic_monitor(self._client.synthetics.get_monitor(id=monitor_id).body)

    @_translate_errors
    def list_synthetic_params(self) -> list[SyntheticParam]:
        # get_params returns a bare JSON array (no envelope).
        return [_to_synthetic_param(p) for p in self._client.synthetics.get_params().body]

    @_translate_errors
    def list_synthetic_private_locations(self) -> list[SyntheticPrivateLocation]:
        # get_private_locations returns a bare JSON array (no envelope).
        return [
            _to_private_location(loc)
            for loc in self._client.synthetics.get_private_locations().body
        ]

    @_translate_errors
    def get_uptime_settings(self) -> UptimeSettings:
        return _to_uptime_settings(self._client.uptime.get_settings().body)

    @_translate_errors
    def list_apm_agent_configs(self) -> list[ApmAgentConfig]:
        body = self._client.apm.get_agent_configurations().body
        return [_to_apm_agent_config(c) for c in body.get("configurations", [])]

    @_translate_errors
    def get_apm_agent_config(
        self, service_name: str | None, environment: str | None
    ) -> ApmAgentConfig:
        # /view 404s when no matching config exists -> NotFoundError -> KibanaNotFound.
        return _to_apm_agent_config(
            self._client.apm.get_agent_configuration(
                name=service_name, environment=environment
            ).body
        )

    @_translate_errors
    def list_apm_environments(self, service_name: str | None) -> list[ApmEnvironment]:
        body = self._client.apm.get_environments(service_name=service_name).body
        return [_to_apm_environment(e) for e in body.get("environments", [])]

    @_translate_errors
    def list_apm_sourcemaps(self) -> list[ApmSourcemap]:
        # Same pagination shape as monitors, but `total`-is-grand-total is
        # UNVERIFIED here: a RUM sourcemap can't be seeded on this stack, so only
        # the empty {"artifacts":[],"total":0} was observed. The `not batch`
        # guard still terminates a misbehaving server that omits/repeats pages.
        sourcemaps: list[ApmSourcemap] = []
        page = 1
        while True:
            body = self._client.apm.get_sourcemaps(page=page, per_page=100).body
            batch = body.get("artifacts", [])
            sourcemaps.extend(_to_apm_sourcemap(a) for a in batch)
            total = body.get("total")
            if not batch or (isinstance(total, int) and len(sourcemaps) >= total):
                break
            page += 1
        return sourcemaps

    @_translate_errors
    def search_apm_annotations(
        self, service_name: str, start: str, end: str, environment: str
    ) -> list[ApmAnnotation]:
        # The live server 400s without environment/start/end; the tool always
        # supplies them (environment defaults to ENVIRONMENT_ALL at the tool layer).
        body = self._client.apm.search_annotations(
            service_name=service_name, environment=environment, start=start, end=end
        ).body
        return [_to_apm_annotation(a) for a in body.get("annotations", [])]

    # --- security-detections: detection-engine reads (read-first) ---

    @_translate_errors
    def find_detection_rules(self) -> list[DetectionRule]:
        # Envelope {page, perPage, total, data}; walk via `total`.
        rules: list[DetectionRule] = []
        page = 1
        while True:
            body = self._client.detection_engine.find_rules(page=page, per_page=100).body
            batch = body.get("data", [])
            rules.extend(_to_detection_rule(r) for r in batch)
            total = body.get("total")
            if not batch or len(batch) < 100 or (isinstance(total, int) and len(rules) >= total):
                break
            page += 1
        return rules

    @_translate_errors
    def get_detection_rule(self, rule_id: str | None, id: str | None) -> DetectionRule:
        # get_rule enforces EXACTLY one via `(id is None) == (rule_id is None)`,
        # raising a bare ValueError (uncaught by _translate_errors) for neither
        # AND both. Coerce empty strings to None so "" counts as absent, then
        # match kibana-py's is-None semantics precisely.
        rule_id = rule_id or None
        id = id or None
        if (rule_id is None) == (id is None):
            raise KibanaRejected("provide exactly one of rule_id or id to identify the detection rule")
        return _to_detection_rule(
            self._client.detection_engine.get_rule(id=id, rule_id=rule_id).body
        )

    @_translate_errors
    def get_prepackaged_rules_status(self) -> PrepackagedRulesStatus:
        return _to_prepackaged_status(
            self._client.detection_engine.get_prepackaged_rules_status().body
        )

    @_translate_errors
    def list_detection_rule_tags(self) -> list[str]:
        # get_tags returns a bare JSON array of strings.
        return [str(t) for t in self._client.detection_engine.get_tags().body]

    @_translate_errors
    def search_detection_alerts(self, size: int) -> list[DetectionAlert]:
        # The MOST RECENT alerts: match-all sorted by @timestamp desc (match_all
        # alone yields arbitrary _doc order). `fields` gives shape-agnostic
        # flat-dotted values. Clamp size to a sane range (the tool default is 20).
        size = max(1, min(int(size), 500))
        body = self._client.detection_engine.search_alerts(
            query={"match_all": {}},
            size=size,
            sort=[{"@timestamp": {"order": "desc"}}],
            fields=[
                "kibana.alert.rule.name",
                "kibana.alert.severity",
                "kibana.alert.workflow_status",
                "@timestamp",
            ],
        ).body
        hits = (body.get("hits") or {}).get("hits") or []
        return [_to_detection_alert(h) for h in hits]

    @_translate_errors
    def find_exception_lists(self) -> list[ExceptionList]:
        # Envelope {data, page, per_page, total}; walk via `total`.
        lists: list[ExceptionList] = []
        page = 1
        while True:
            body = self._client.exception_lists.find(page=page, per_page=100).body
            batch = body.get("data", [])
            lists.extend(_to_exception_list(x) for x in batch)
            total = body.get("total")
            if not batch or len(batch) < 100 or (isinstance(total, int) and len(lists) >= total):
                break
            page += 1
        return lists

    @_translate_errors
    def get_exception_list(self, id: str | None, list_id: str | None) -> ExceptionList:
        # exception_lists.get tolerates both; it only raises on neither.
        id = id or None
        list_id = list_id or None
        if id is None and list_id is None:
            raise KibanaRejected("provide an id or list_id to identify the exception list")
        return _to_exception_list(
            self._client.exception_lists.get(id=id, list_id=list_id).body
        )

    @_translate_errors
    def find_exception_items(self, list_id: str) -> list[ExceptionItem]:
        # list_id required; a bogus one 404s -> KibanaNotFound.
        items: list[ExceptionItem] = []
        page = 1
        while True:
            body = self._client.exception_lists.find_items(
                list_id=list_id, page=page, per_page=100
            ).body
            batch = body.get("data", [])
            items.extend(_to_exception_item(x) for x in batch)
            total = body.get("total")
            if not batch or len(batch) < 100 or (isinstance(total, int) and len(items) >= total):
                break
            page += 1
        return items

    @_translate_errors
    def find_value_lists(self) -> list[ValueList]:
        # Value lists use CURSOR pagination {data, total, cursor}. Pass the
        # returned cursor for the next page; stop on empty batch, on len>=total,
        # or if the cursor stops advancing (defence against a non-terminating loop).
        lists: list[ValueList] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            body = self._client.lists.find(per_page=100, cursor=cursor).body
            batch = body.get("data", [])
            lists.extend(_to_value_list(x) for x in batch)
            total = body.get("total")
            if not batch or (isinstance(total, int) and len(lists) >= total):
                break
            next_cursor = body.get("cursor")
            if not next_cursor or next_cursor in seen:
                break
            seen.add(next_cursor)
            cursor = next_cursor
        return lists

    @_translate_errors
    def find_timelines(self) -> list[Timeline]:
        # Envelope {timeline, totalCount}; walk via `totalCount`.
        timelines: list[Timeline] = []
        page_index = 1
        while True:
            body = self._client.timeline.get_all(page_index=page_index, page_size=100).body
            batch = body.get("timeline", [])
            timelines.extend(_to_timeline(t) for t in batch)
            total = body.get("totalCount")
            if not batch or len(batch) < 100 or (isinstance(total, int) and len(timelines) >= total):
                break
            page_index += 1
        return timelines

    # --- security-detections writes (v2): rule + exception create/delete ---

    @_translate_errors
    def create_detection_rule(
        self, name: str, description: str, query: str, index: list[str], severity: str,
        risk_score: int, rule_id: str | None, tags: list[str], interval: str,
        language: str, enabled: bool,
    ) -> DetectionRule:
        body = self._client.detection_engine.create_rule(
            type="query", name=name, description=description, severity=severity,
            risk_score=risk_score, query=query, index=list(index), language=language,
            enabled=enabled, interval=interval, tags=list(tags),
            rule_id=(rule_id or None),
        ).body
        return _to_detection_rule(body)

    @_translate_errors
    def delete_detection_rule(self, rule_id: str | None, id: str | None) -> None:
        rule_id = rule_id or None
        id = id or None
        if (rule_id is None) == (id is None):
            raise KibanaRejected("provide exactly one of rule_id or id to identify the detection rule")
        self._client.detection_engine.delete_rule(id=id, rule_id=rule_id)

    @_translate_errors
    def create_exception_list(
        self, name: str, description: str, type: str, list_id: str | None,
        namespace_type: str, tags: list[str],
    ) -> ExceptionList:
        body = self._client.exception_lists.create(
            name=name, description=description, type=type,
            list_id=(list_id or None), namespace_type=namespace_type, tags=list(tags),
        ).body
        return _to_exception_list(body)

    @_translate_errors
    def delete_exception_list(
        self, id: str | None, list_id: str | None, namespace_type: str
    ) -> None:
        id = id or None
        list_id = list_id or None
        if (id is None) == (list_id is None):
            raise KibanaRejected("provide exactly one of id or list_id to identify the exception list")
        self._client.exception_lists.delete(id=id, list_id=list_id, namespace_type=namespace_type)

    @_translate_errors
    def create_exception_item(
        self, list_id: str, name: str, description: str, entries: list[dict[str, Any]],
        item_id: str | None, namespace_type: str, tags: list[str],
    ) -> ExceptionItem:
        body = self._client.exception_lists.create_item(
            list_id=list_id, name=name, description=description,
            entries=[dict(e) for e in entries], type="simple",
            item_id=(item_id or None), namespace_type=namespace_type, tags=list(tags),
        ).body
        return _to_exception_item(body)

    @_translate_errors
    def delete_exception_item(
        self, id: str | None, item_id: str | None, namespace_type: str
    ) -> None:
        id = id or None
        item_id = item_id or None
        if (id is None) == (item_id is None):
            raise KibanaRejected("provide exactly one of id or item_id to identify the exception item")
        self._client.exception_lists.delete_item(id=id, item_id=item_id, namespace_type=namespace_type)

    # --- security-detections writes (v3, #60): update rule + value lists ---

    @_translate_errors
    def update_detection_rule(
        self, rule_id: str | None, id: str | None, name: str | None, description: str | None,
        tags: list[str] | None, severity: str | None, risk_score: int | None,
        query: str | None, interval: str | None,
    ) -> DetectionRule:
        rid, uid = (rule_id or None), (id or None)
        if (rid is None) == (uid is None):
            raise KibanaRejected("provide exactly one of rule_id or id to identify the detection rule")
        # is-not-None sentinels: tags=[] clears; None drops. Never forward enabled/actions/fields=
        # (privilege-gated / footgun). PATCH is a true partial update (env-research P4/P5).
        fields = {"name": name, "description": description, "tags": tags, "severity": severity,
                  "risk_score": risk_score, "query": query, "interval": interval}
        patch: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
        if not patch:
            raise KibanaRejected("provide at least one field to update")
        ident: dict[str, Any] = {"rule_id": rid} if rid is not None else {"id": uid}
        body = self._client.detection_engine.patch_rule(**ident, **patch).body
        return _to_detection_rule(body)

    # --- security-detections write follow-ups (#73 task 3): full-replace RMW ---

    def _rmw_rule(
        self, rule_id: str | None, id: str | None, changes: dict[str, Any],
        *, guard_immutable: bool,
    ) -> DetectionRule:
        # update_rule is a FULL REPLACE — a PUT that omits a field wipes it
        # (design-flaw live-confirmed a bare update_rule wipes `actions` to
        # `[]`). Fetch the current rule, echo every writable field back, then
        # layer the caller's changes on top.
        rid, uid = (rule_id or None), (id or None)
        if (rid is None) == (uid is None):
            raise KibanaRejected("provide exactly one of rule_id or id to identify the detection rule")
        raw = self._client.detection_engine.get_rule(id=uid, rule_id=rid).body
        if guard_immutable and raw.get("immutable") is True:
            # Elastic-prebuilt rule content: never full-replaced client-side,
            # mirroring fleet update_agent_policy's is_managed guard.
            raise KibanaRejected("detection rule is immutable (Elastic-prebuilt) and cannot be replaced")
        raw = dict(raw)
        if "from" in raw:
            # get_rule's body key is "from" but update_rule's kwarg is the
            # reserved-word workaround "from_" (SD-P6, live-verified). Translate
            # BEFORE the _rmw_body intersection, or the query time-window is
            # silently dropped and reset to default — the exact footgun this
            # RMW exists to prevent.
            raw["from_"] = raw.pop("from")
        body = _rmw_body(self._client.detection_engine.update_rule, raw, changes)
        # get_rule's body echoes BOTH id and rule_id; update_rule accepts
        # both as real kwargs too, so an unstripped echo sends both
        # identifiers together and Kibana 400s on the redundant one (the
        # same class of bug update_package_policy's body.pop("id", None)
        # fix addressed, live-verified there). Strip both, then set only
        # the caller's chosen identifier.
        body.pop("id", None)
        body.pop("rule_id", None)
        if uid:
            body["id"] = uid
        else:
            body["rule_id"] = rid
        updated = self._client.detection_engine.update_rule(**body).body
        return _to_detection_rule(updated)

    @_translate_errors
    def replace_detection_rule(
        self, *, rule_id: str | None, id: str | None, changes: dict[str, Any]
    ) -> DetectionRule:
        # enabled is never exposed by this tool (use enable/disable instead);
        # drop it defensively even if a caller's changes dict includes it —
        # the RMW echoes the current unchanged enabled either way (safe).
        changes = {k: v for k, v in changes.items() if k != "enabled"}
        return self._rmw_rule(rule_id, id, changes, guard_immutable=True)

    @_translate_errors
    def enable_detection_rule(self, *, rule_id: str | None, id: str | None) -> DetectionRule:
        return self._rmw_rule(rule_id, id, {"enabled": True}, guard_immutable=False)

    @_translate_errors
    def disable_detection_rule(self, *, rule_id: str | None, id: str | None) -> DetectionRule:
        return self._rmw_rule(rule_id, id, {"enabled": False}, guard_immutable=False)

    def _ensure_value_list_index(self) -> None:
        # UNDECORATED: catches the raw kibana NotFoundError before _translate_errors would
        # convert it. The .lists index is NOT auto-created (env-research P2); both backing
        # streams must exist (P7) or lists.create 400s. create_index is idempotent on 9.4.3.
        try:
            st = self._client.lists.get_index_status().body
        except NotFoundError:
            st = {}
        if not (st.get("list_index") and st.get("list_item_index")):
            self._client.lists.create_index()

    @_translate_errors
    def create_value_list(
        self, name: str, description: str, type: str, id: str | None
    ) -> ValueList:
        self._ensure_value_list_index()
        body = self._client.lists.create(
            name=name, description=description, type=type, id=(id or None)).body
        return _to_value_list(body)

    @_translate_errors
    def delete_value_list(self, id: str, force: bool) -> None:
        # force -> ignore_references: default (False) 409s on a referenced list (env-research
        # P6 — Kibana blocks, doesn't orphan); force deletes it anyway (orphans the reference).
        self._client.lists.delete(id=id, ignore_references=force)

    @_translate_errors
    def create_value_list_item(self, *, list_id: str, value: str) -> ValueListItem:
        self._ensure_value_list_index()
        body = self._client.lists.create_item(list_id=list_id, value=value).body
        return _to_value_list_item(body)

    @_translate_errors
    def find_value_list_items(self, *, list_id: str) -> list[ValueListItem]:
        # find_items defaults to ~20/page; page-walk with per_page=100 until a
        # short/empty page so a >20-item list is never silently truncated.
        items: list[ValueListItem] = []
        page = 1
        while True:
            body = self._client.lists.find_items(
                list_id=list_id, page=page, per_page=100
            ).body
            batch = body.get("data", [])
            items.extend(_to_value_list_item(x) for x in batch)
            total = body.get("total")
            if not batch or len(batch) < 100 or (isinstance(total, int) and len(items) >= total):
                break
            page += 1
        return items

    @_translate_errors
    def delete_value_list_item(self, *, item_id: str) -> None:
        self._client.lists.delete_item(id=item_id)

    # --- saved_objects export/import (#37): opaque body, handle-based ---

    @_translate_errors
    def export_saved_objects(
        self, types: list[str] | None, objects: list[dict[str, Any]] | None,
        include_references_deep: bool,
    ) -> list[dict[str, Any]]:
        # export() takes `objects` OR `type`, never both; guard so a bad call is a
        # clean domain rejection, not kibana-py's bare ValueError.
        if bool(types) == bool(objects):
            raise KibanaRejected("provide exactly one of types or objects to export")
        kwargs: dict[str, Any] = {"include_references_deep": include_references_deep}
        # Dispatch on truthiness to MATCH the guard: an empty `objects=[]` alongside
        # `types` must select by-type, not send an empty (0-object) export.
        if objects:
            kwargs["objects"] = objects
        else:
            kwargs["type"] = types
        return list(self._client.saved_objects.export(**kwargs).body)

    @_translate_errors
    def import_saved_objects(self, content: bytes, overwrite: bool) -> SavedObjectImportResult:
        # Mutually-exclusive modes: overwrite=True restores IN PLACE (same ids,
        # replacing existing objects — destructive); overwrite=False mints new ids
        # (a clone, never touching existing objects).
        mode: dict[str, Any] = {"overwrite": True} if overwrite else {"create_new_copies": True}
        body = self._client.saved_objects.import_objects(file=content, **mode).body
        return _to_import_result(body)

    # --- platform-admin: spaces + roles + upgrade reads (read-first) ---

    @_translate_errors
    def list_spaces(self) -> list[Space]:
        # get_all returns a bare JSON array (no envelope).
        return [_to_space(s) for s in self._client.spaces.get_all().body]

    @_translate_errors
    def get_space(self, space_id: str) -> Space:
        # A missing space id 404s -> NotFoundError -> KibanaNotFound.
        return _to_space(self._client.spaces.get(id=space_id).body)

    @_translate_errors
    def list_roles(self) -> list[Role]:
        # get_all_roles returns a bare JSON array (no envelope).
        return [_to_role(r) for r in self._client.security.get_all_roles().body]

    @_translate_errors
    def get_role(self, role_name: str) -> Role:
        # A missing role name 404s -> NotFoundError -> KibanaNotFound.
        return _to_role(self._client.security.get_role(name=role_name).body)

    @_translate_errors
    def get_upgrade_status(self) -> UpgradeReadiness:
        return _to_upgrade_readiness(self._client.upgrade_assistant.status().body)

    # --- platform-admin writes (#57): spaces + roles CRUD ---

    @_translate_errors
    def create_space(
        self, id: str, name: str, description: str | None, color: str | None,
        initials: str | None, disabled_features: list[str] | None, solution: str | None,
    ) -> Space:
        body = self._client.spaces.create(
            id=id, name=name, description=description, color=color, initials=initials,
            disabled_features=disabled_features, solution=solution).body
        return _to_space(body)

    @_translate_errors
    def update_space(
        self, space_id: str, name: str | None, description: str | None, color: str | None,
        initials: str | None, disabled_features: list[str] | None, solution: str | None,
    ) -> Space:
        # Full read-modify-write. The kibana-py docstring warns omitted schema-default fields
        # reset (live 9.4.3 preserves them, env-research P7, but RMW is robust). Read the
        # camelCase GET keys (disabledFeatures/imageUrl); never **cur (it carries _reserved).
        cur = self._client.spaces.get(id=space_id).body
        self._client.spaces.update(
            id=space_id,
            name=name if name is not None else cur.get("name"),
            description=description if description is not None else cur.get("description"),
            color=color if color is not None else cur.get("color"),
            initials=initials if initials is not None else cur.get("initials"),
            image_url=cur.get("imageUrl"),  # preserve avatar image (not a tool input)
            disabled_features=(disabled_features if disabled_features is not None
                               else cur.get("disabledFeatures")),
            solution=solution if solution is not None else cur.get("solution"),
        )
        return _to_space(self._client.spaces.get(id=space_id).body)

    @_translate_errors
    def create_or_update_role(
        self, name: str, cluster_privileges: list[str] | None,
        index_privileges: list[dict[str, Any]], kibana_base: list[str] | None,
        kibana_spaces: list[str] | None, description: str | None, create_only: bool,
    ) -> Role:
        # Refuse modifying a reserved system role (mirrors delete_role). A full replace
        # (create_only=False) would drop the role's grants; Kibana 400s such a PUT, but
        # guard client-side so the safety is explicit + tested. A fresh create (get_role
        # 404) proceeds past the guard.
        try:
            existing = self._client.security.get_role(name=name).body
        except NotFoundError:
            existing = None
        if existing and (existing.get("metadata") or {}).get("_reserved"):
            raise KibanaRejected(f"role '{name}' is reserved and cannot be modified")
        es = {"cluster": list(cluster_privileges or []),
              "indices": [dict(ip) for ip in (index_privileges or [])]}
        kibana = ([{"base": list(kibana_base), "spaces": list(kibana_spaces or ["*"])}]
                  if kibana_base else None)
        self._client.security.create_or_update_role(
            name=name, elasticsearch=es, kibana=kibana,
            description=(description or None), create_only=(create_only or None))
        return _to_role(self._client.security.get_role(name=name).body)  # PUT is 204 -> re-read

    @_translate_errors
    def delete_space(self, space_id: str, force: bool) -> None:
        # DESTRUCTIVE: wipes EVERY saved object in the space. The default + any _reserved
        # space are never deletable (Kibana 400s the default, P5). A non-reserved space
        # requires force=True: reliably counting a space's objects is not possible
        # (saved_objects.find requires a type + is deprecated on 9.4), so the whole-space
        # wipe demands an explicit force rather than a fail-open count.
        if space_id == "default":  # fast-path: the default space is always reserved
            raise KibanaRejected("the default space cannot be deleted")
        sp = self._client.spaces.get(id=space_id).body  # 404 -> KibanaNotFound
        if sp.get("_reserved"):
            raise KibanaRejected(f"space '{space_id}' is reserved and cannot be deleted")
        if not force:
            raise KibanaRejected(
                f"deleting space '{space_id}' permanently removes ALL its saved objects; pass force=True")
        self._client.spaces.delete(id=space_id)

    @_translate_errors
    def delete_role(self, name: str) -> None:
        r = self._client.security.get_role(name=name).body  # 404 -> KibanaNotFound
        if (r.get("metadata") or {}).get("_reserved"):
            raise KibanaRejected(f"role '{name}' is reserved and cannot be deleted")
        self._client.security.delete_role(name=name)

    # --- streams: stream reads (read-first; Tech-Preview API) ---

    @_translate_errors
    def list_streams(self) -> list[StreamSummary]:
        # get_all -> {"streams": [<stream>...]}; no page envelope observed.
        body = self._client.streams.get_all().body
        return [_to_stream_summary(s) for s in body.get("streams", [])]

    @_translate_errors
    def get_stream(self, name: str) -> Stream:
        # get -> {"stream": <stream>}; a missing name 404s -> KibanaNotFound. A 200
        # without the wrapper key means the Tech-Preview envelope drifted: surface
        # it, don't silently return a phantom-empty stream an LLM reads as real.
        body = self._client.streams.get(name=name).body
        stream = body.get("stream")
        if not isinstance(stream, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'stream' object")
        return _to_stream(stream)

    @_translate_errors
    def get_stream_ingest(self, name: str) -> StreamIngest:
        # get_ingest -> {"ingest": <ingest>}; a missing name 404s -> KibanaNotFound.
        body = self._client.streams.get_ingest(name=name).body
        ingest = body.get("ingest")
        if not isinstance(ingest, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'ingest' object")
        return _to_stream_ingest(ingest)

    # --- streams: write/destructive tier (Tech-Preview; env-research P1-P14) ---

    @_translate_errors
    def enable_streams(self) -> StreamWriteResult:
        return _to_stream_write_result(self._client.streams.enable().body)

    @_translate_errors
    def disable_streams(self) -> StreamWriteResult:
        # DESTRUCTIVE: deletes all wired stream definitions + their data cluster-wide.
        return _to_stream_write_result(self._client.streams.disable().body)

    @_translate_errors
    def resync_streams(self) -> StreamWriteResult:
        return _to_stream_write_result(self._client.streams.resync().body)

    @_translate_errors
    def fork_stream(self, parent: str, child: str, field: str, value: str) -> StreamWriteResult:
        body = self._client.streams.fork(
            name=parent,
            stream_name=child,
            where={"field": field, "eq": value},
            status="disabled",
        ).body
        return _to_stream_write_result(body)

    @_translate_errors
    def set_stream_retention(self, name: str, retention: str) -> StreamIngest:
        # update_ingest is a FULL REPLACE (env-research P2/P10) -> read-modify-write.
        ingest = self._client.streams.get_ingest(name=name).body.get("ingest")
        if not isinstance(ingest, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'ingest' object")
        mode = _stream_lifecycle(ingest)[0]
        if mode not in _RETENTION_MODES_OK:  # allowlist: never clobber an ilm/future mode
            raise KibanaRejected(
                f"stream '{name}' uses a '{mode}' lifecycle; "
                "set_stream_retention manages DSL retention only"
            )
        processing = ingest.get("processing")
        if isinstance(processing, dict):
            processing.pop("updated_at", None)  # read-only; Kibana 9.4.3 rejects it on write
        ingest["lifecycle"] = {"dsl": {"data_retention": retention}}
        resp = self._client.streams.update_ingest(name=name, ingest=ingest).body
        # A 200 carrying acknowledged:false means the write silently didn't apply;
        # surface it rather than returning the pre-write retention as a false success.
        if isinstance(resp, dict) and resp.get("acknowledged") is False:
            raise KibanaUnavailable(f"stream '{name}' retention update was not acknowledged")
        after = self._client.streams.get_ingest(name=name).body.get("ingest")
        if not isinstance(after, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'ingest' object")
        return _to_stream_ingest(after)

    @_translate_errors
    def set_stream_processing(self, *, name: str, steps: list[dict[str, Any]]) -> StreamIngest:
        # update_ingest is a FULL REPLACE -> read-modify-write (mirrors set_stream_retention).
        # processing is a generic ingest facet (present on wired AND classic streams — only
        # wired.* is wired-specific), so there is NO wired-vs-classic special-case here: a
        # stream without a processing facet surfaces Kibana's own validation error on write.
        ingest = self._client.streams.get_ingest(name=name).body.get("ingest")
        if not isinstance(ingest, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'ingest' object")
        processing = ingest.get("processing")
        if isinstance(processing, dict):
            processing.pop("updated_at", None)  # read-only; Kibana 9.4.3 rejects it on write
            processing["steps"] = steps
        else:
            ingest["processing"] = {"steps": steps}
        resp = self._client.streams.update_ingest(name=name, ingest=ingest).body
        if isinstance(resp, dict) and resp.get("acknowledged") is False:
            raise KibanaUnavailable(f"stream '{name}' processing update was not acknowledged")
        after = self._client.streams.get_ingest(name=name).body.get("ingest")
        if not isinstance(after, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'ingest' object")
        return _to_stream_ingest(after)

    def _set_fork_routing_status(self, *, parent: str, child: str, status: str) -> StreamIngest:
        # RMW the PARENT's ingest: find the wired.routing entry whose destination == child
        # (SW-P2 confirmed the key is 'destination'); flip only that entry's status, leave
        # every OTHER routing entry untouched, then echo the whole ingest back.
        ingest = self._client.streams.get_ingest(name=parent).body.get("ingest")
        if not isinstance(ingest, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'ingest' object")
        wired = ingest.get("wired")
        routing = wired.get("routing") if isinstance(wired, dict) else None
        entry = None
        if isinstance(routing, list):
            for r in routing:
                if isinstance(r, dict) and r.get("destination") == child:
                    entry = r
                    break
        if entry is None:
            raise KibanaRejected(f"'{child}' is not a fork of '{parent}'")
        entry["status"] = status
        processing = ingest.get("processing")
        if isinstance(processing, dict):
            processing.pop("updated_at", None)  # read-only; Kibana 9.4.3 rejects it on write
        resp = self._client.streams.update_ingest(name=parent, ingest=ingest).body
        if isinstance(resp, dict) and resp.get("acknowledged") is False:
            raise KibanaUnavailable(f"stream '{parent}' fork routing update was not acknowledged")
        after = self._client.streams.get_ingest(name=parent).body.get("ingest")
        if not isinstance(after, dict):
            raise KibanaUnavailable("unexpected Streams API response: missing 'ingest' object")
        return _to_stream_ingest(after)

    @_translate_errors
    def activate_fork(self, *, parent: str, child: str) -> StreamIngest:
        return self._set_fork_routing_status(parent=parent, child=child, status="enabled")

    @_translate_errors
    def deactivate_fork(self, *, parent: str, child: str) -> StreamIngest:
        return self._set_fork_routing_status(parent=parent, child=child, status="disabled")

    @_translate_errors
    def delete_stream(self, name: str, force: bool) -> StreamWriteResult:
        target = name.strip()
        if not force:
            # Guard over WIRED streams only (a classic/query sibling named 'logs'
            # must not reclassify a root) + a hard known-root floor. TOCTOU note: a
            # child forked between this list and the delete is missed -- destructive
            # tier + deliberate intent is the backstop, force the authoritative path.
            streams = self.list_streams()
            wired = {s.name for s in streams if s.type == "wired"}
            parent = target.rpartition(".")[0]
            is_root = target in _KNOWN_ROOTS or (target in wired and parent not in wired)
            # Count ANY child (not just wired): a query-type child under the target
            # would otherwise be cascade-deleted invisibly. Root detection stays
            # wired-only (a classic/query sibling named 'logs' must not reclassify a root).
            has_children = any(s.name.startswith(target + ".") for s in streams)
            if is_root or has_children:
                what = "a root stream" if is_root else "a parent with child streams"
                raise KibanaRejected(
                    f"refusing to delete '{target}': it is {what}; deleting it destroys its "
                    "backing data (and, for a parent, the whole subtree). Pass force=True to proceed."
                )
        return _to_stream_write_result(self._client.streams.delete(name=target).body)

    # --- fleet (read-first v1) ---

    @_translate_errors
    def get_fleet_settings(self) -> FleetSettings:
        item = self._client.fleet.get_settings().body.get("item") or {}
        return FleetSettings(
            id=item.get("id", ""),
            prerelease_integrations_enabled=bool(item.get("prerelease_integrations_enabled", False)),
            integration_knowledge_enabled=bool(item.get("integration_knowledge_enabled", False)),
            space_awareness_migration_status=item.get("use_space_awareness_migration_status", ""),
        )

    @_translate_errors
    def check_fleet_permissions(self) -> FleetPermissions:
        return FleetPermissions(
            success=bool(self._client.fleet.check_permissions().body.get("success", False))
        )

    @_translate_errors
    def list_agents(self) -> list[FleetAgent]:
        # show_inactive so an unenrolled/offline agent still lists (a read tool
        # should show the full fleet); paginate so >100 agents aren't truncated.
        return [_to_fleet_agent(a) for a in _fleet_all_items(
            lambda p: self._client.fleet_agents.get_all(
                page=p, per_page=100, show_inactive=True).body)]

    @_translate_errors
    def get_agent(self, agent_id: str) -> FleetAgent:
        return _to_fleet_agent(self._client.fleet_agents.get(agent_id=agent_id).body["item"])

    @_translate_errors
    def get_agent_status(self) -> FleetAgentStatus:
        return _to_fleet_agent_status(self._client.fleet_agents.get_status().body.get("results") or {})

    @_translate_errors
    def list_agent_versions(self) -> list[str]:
        return list(self._client.fleet_agents.get_available_versions().body.get("items", []))

    @_translate_errors
    def list_agent_policies(self) -> list[FleetAgentPolicy]:
        # with_agent_count so agent_count is the real assigned count — the list
        # endpoint returns 0 for every policy without it (unlike the single get).
        return [_to_fleet_agent_policy(p) for p in _fleet_all_items(
            lambda p: self._client.fleet_policies.get_agent_policies(
                page=p, per_page=100, with_agent_count=True).body)]

    @_translate_errors
    def get_agent_policy(self, agent_policy_id: str) -> FleetAgentPolicy:
        return _to_fleet_agent_policy(
            self._client.fleet_policies.get_agent_policy(agent_policy_id=agent_policy_id).body["item"]
        )

    @_translate_errors
    def list_package_policies(self) -> list[FleetPackagePolicy]:
        return [_to_fleet_package_policy(p) for p in _fleet_all_items(
            lambda p: self._client.fleet_policies.get_package_policies(page=p, per_page=100).body)]

    @_translate_errors
    def get_package_policy(self, package_policy_id: str) -> FleetPackagePolicy:
        return _to_fleet_package_policy(
            self._client.fleet_policies.get_package_policy(package_policy_id=package_policy_id).body["item"]
        )

    @_translate_errors
    def list_enrollment_keys(self) -> list[FleetEnrollmentKey]:
        return [_to_fleet_enrollment_key(k) for k in _fleet_all_items(
            lambda p: self._client.fleet_enrollment.get_keys(page=p, per_page=100).body)]

    @_translate_errors
    def get_enrollment_key(self, key_id: str) -> FleetEnrollmentKey:
        return _to_fleet_enrollment_key(
            self._client.fleet_enrollment.get_key(key_id=key_id).body["item"]
        )

    @_translate_errors
    def list_uninstall_tokens(self) -> list[FleetUninstallToken]:
        return [_to_fleet_uninstall_token(t) for t in _fleet_all_items(
            lambda p: self._client.fleet_enrollment.get_uninstall_tokens(page=p, per_page=100).body)]

    @_translate_errors
    def list_packages(self) -> list[FleetPackage]:
        body = self._client.fleet_epm.get_packages().body
        return [_to_fleet_package(p) for p in body.get("items", [])]

    @_translate_errors
    def list_installed_packages(self) -> list[FleetPackage]:
        packages: list[FleetPackage] = []
        cursor: list[Any] | None = None
        while True:
            kwargs: dict[str, Any] = {"per_page": 100}
            if cursor is not None:
                kwargs["search_after"] = cursor
            body = self._client.fleet_epm.get_installed_packages(**kwargs).body
            batch = body.get("items", [])
            packages.extend(_to_fleet_package(p) for p in batch)
            total = body.get("total")
            next_cursor = body.get("searchAfter")
            if not batch or not next_cursor or (isinstance(total, int) and len(packages) >= total):
                break
            cursor = next_cursor
        return packages

    @_translate_errors
    def get_package(self, name: str) -> FleetPackage:
        item = self._client.fleet_epm.get_package(pkg_name=name).body.get("item") or {}
        if not item:
            raise KibanaNotFound(f"integration package '{name}' not found")
        return _to_fleet_package(item)

    @_translate_errors
    def list_package_categories(self) -> list[FleetPackageCategory]:
        body = self._client.fleet_epm.get_categories().body
        return [_to_fleet_category(c) for c in body.get("items", [])]

    @_translate_errors
    def list_outputs(self) -> list[FleetOutput]:
        body = self._client.fleet_outputs.get_outputs().body
        return [_to_fleet_output(o) for o in body.get("items", [])]

    @_translate_errors
    def get_output_health(self, output_id: str) -> FleetOutputHealth:
        return _to_fleet_output_health(
            self._client.fleet_outputs.get_output_health(output_id=output_id).body
        )

    @_translate_errors
    def list_fleet_server_hosts(self) -> list[FleetServerHost]:
        body = self._client.fleet_outputs.get_fleet_server_hosts().body
        return [_to_fleet_server_host(h) for h in body.get("items", [])]

    # --- fleet writes: agent-policy CRUD (#81) ---

    @_translate_errors
    # Keyword-only with the three optionals defaulted, matching ports/gateway.py
    # EXACTLY. The port is the contract and this package ships py.typed, so a
    # consumer coding against KibanaGateway may legitimately write
    # `create_agent_policy(name=..., namespace=...)` — against the old
    # all-required signature that was a TypeError at runtime. No in-repo caller
    # changes: every one already passes these by keyword.
    def create_agent_policy(
        self, *, name: str, namespace: str, description: str | None = None,
        monitoring_enabled: list[str] | None = None, inactivity_timeout: int | None = None,
    ) -> FleetAgentPolicy:
        body = self._client.fleet_policies.create_agent_policy(
            name=name, namespace=namespace, description=description,
            monitoring_enabled=monitoring_enabled, inactivity_timeout=inactivity_timeout,
        ).body
        return _to_fleet_agent_policy(body["item"])

    @_translate_errors
    def update_agent_policy(
        self, agent_policy_id: str, changes: dict[str, Any]
    ) -> FleetAgentPolicy:
        # DESTRUCTIVE-adjacent: a managed policy (e.g. Elastic-defend-managed) is
        # never modifiable through this path — Kibana itself owns its lifecycle.
        raw = self._client.fleet_policies.get_agent_policy(
            agent_policy_id=agent_policy_id
        ).body["item"]  # 404 -> KibanaNotFound
        if raw.get("is_managed"):
            raise KibanaRejected(f"agent policy '{agent_policy_id}' is managed and cannot be updated")
        body = _rmw_body(self._client.fleet_policies.update_agent_policy, raw, changes)
        body["agent_policy_id"] = agent_policy_id  # re-set the path kwarg explicitly
        resp = self._client.fleet_policies.update_agent_policy(**body).body
        return _to_fleet_agent_policy(resp["item"])

    @_translate_errors
    def delete_agent_policy(self, agent_policy_id: str, force: bool = False) -> None:
        # DESTRUCTIVE: removes the policy and its package policies. Refuse a
        # managed policy (Kibana-owned lifecycle) and the default Fleet Server
        # policy (deleting it strands every Fleet Server on it).
        raw = self._client.fleet_policies.get_agent_policy(
            agent_policy_id=agent_policy_id
        ).body["item"]  # 404 -> KibanaNotFound
        if raw.get("is_managed"):
            raise KibanaRejected(f"agent policy '{agent_policy_id}' is managed and cannot be deleted")
        if raw.get("is_default_fleet_server"):
            raise KibanaRejected(
                f"agent policy '{agent_policy_id}' is the default Fleet Server policy and cannot be deleted")
        self._client.fleet_policies.delete_agent_policy(agent_policy_id=agent_policy_id, force=(force or None))

    # --- fleet writes: package-policy CRUD (#81) ---

    @_translate_errors
    def create_package_policy(
        self, *, name: str, package: dict[str, Any], agent_policy_id: str,
        inputs: dict[str, Any] | list[Any] | None = None,
    ) -> FleetPackagePolicy:
        kwargs: dict[str, Any] = {"name": name, "package": package, "policy_id": agent_policy_id}
        if inputs is not None:
            kwargs["inputs"] = inputs
        body = self._client.fleet_policies.create_package_policy(**kwargs).body
        return _to_fleet_package_policy(body["item"])

    @_translate_errors
    def update_package_policy(
        self, *, package_policy_id: str, changes: dict[str, Any]
    ) -> FleetPackagePolicy:
        raw = self._client.fleet_policies.get_package_policy(
            package_policy_id=package_policy_id
        ).body["item"]  # 404 -> KibanaNotFound
        # Friendly name -> real kwarg, same mapping as create_package_policy's
        # "policy_id": agent_policy_id (mirrored here for update consistency).
        # Builds a NEW dict rather than mutating the caller's `changes`.
        if "agent_policy_id" in changes:
            changes = {"policy_id" if k == "agent_policy_id" else k: v for k, v in changes.items()}
        body = _rmw_body(self._client.fleet_policies.update_package_policy, raw, changes)
        # The update endpoint 400s on the server-computed compiled_input /
        # compiled_stream sub-fields it just handed back in the raw body —
        # strip them before re-sending, same as streams popping updated_at.
        stripped_inputs: list[dict[str, Any]] = []
        for inp in body.get("inputs") or []:
            inp = {k: v for k, v in inp.items() if k != "compiled_input"}
            if "streams" in inp:
                inp["streams"] = [
                    {k: v for k, v in stream.items() if k != "compiled_stream"}
                    for stream in inp["streams"]
                ]
            stripped_inputs.append(inp)
        if "inputs" in body:
            body["inputs"] = stripped_inputs
        # Kibana's update-package-policy schema 400s on a body-level "id" (it's
        # redundant with the path's package_policy_id) even though the kibana-py
        # client accepts it as an optional kwarg and _rmw_body's generic allowlist
        # retains it from the raw GET — verified live (unlike update_agent_policy,
        # where the equivalent raw "id" is harmless and intentionally kept).
        body.pop("id", None)
        body["package_policy_id"] = package_policy_id  # re-set the path kwarg explicitly
        resp = self._client.fleet_policies.update_package_policy(**body).body
        return _to_fleet_package_policy(resp["item"])

    @_translate_errors
    def delete_package_policy(self, *, package_policy_id: str, force: bool = False) -> None:
        self._client.fleet_policies.delete_package_policy(
            package_policy_id=package_policy_id, force=(force or None))

    # --- fleet writes: output CRUD (#81) ---

    @_translate_errors
    def create_output(
        self, *, name: str, type: str, hosts: list[str], is_default: bool | None = None,
        is_default_monitoring: bool | None = None,
    ) -> FleetOutput:
        # Non-secret kwargs only — ssl/secrets/config_yaml/etc. are never
        # accepted here (mirrors _to_fleet_output's redaction on the way back).
        kwargs: dict[str, Any] = {"name": name, "type": type, "hosts": hosts}
        if is_default is not None:
            kwargs["is_default"] = is_default
        if is_default_monitoring is not None:
            kwargs["is_default_monitoring"] = is_default_monitoring
        body = self._client.fleet_outputs.create_output(**kwargs).body
        return _to_fleet_output(body["item"])

    @_translate_errors
    def update_output(
        self, *, output_id: str, changes: dict[str, Any], confirm: bool = False
    ) -> FleetOutput:
        # Protect the live default(s): editing the default agent-data or
        # default-monitoring output can silently redirect a whole fleet's
        # traffic, so it requires an explicit confirm=True.
        raw = self._client.fleet_outputs.get_output(
            output_id=output_id
        ).body["item"]  # 404 -> KibanaNotFound
        if (raw.get("is_default") or raw.get("is_default_monitoring")) and not confirm:
            raise KibanaRejected(
                f"output '{output_id}' is a default output; pass confirm=True to update it")
        body = _rmw_body(self._client.fleet_outputs.update_output, raw, changes)
        body["output_id"] = output_id  # re-set the path kwarg explicitly
        resp = self._client.fleet_outputs.update_output(**body).body
        return _to_fleet_output(resp["item"])

    @_translate_errors
    def delete_output(self, *, output_id: str) -> None:
        # No force escape: unlike agent/package policies, a default output has
        # no safe override — deleting it would strand agents mid-flight.
        raw = self._client.fleet_outputs.get_output(
            output_id=output_id
        ).body["item"]  # 404 -> KibanaNotFound
        if raw.get("is_default") or raw.get("is_default_monitoring"):
            raise KibanaRejected(f"output '{output_id}' is a default output and cannot be deleted")
        self._client.fleet_outputs.delete_output(output_id=output_id)

    # --- fleet writes: agent lifecycle, single + bulk (#81) ---

    def _guard_reassign_target(self, policy_id: str) -> None:
        # REASSIGN ONLY: a managed or default-Fleet-Server policy has a
        # Kibana-owned lifecycle — moving agents into it fights that owner
        # (managed) or silently redirects Fleet Server's own agents
        # (default_fleet_server). upgrade/unenroll have no target policy, so
        # they carry no equivalent guard. Shared by reassign_agent + bulk_reassign.
        target = self._client.fleet_policies.get_agent_policy(
            agent_policy_id=policy_id
        ).body["item"]  # 404 -> KibanaNotFound
        if target.get("is_managed"):
            raise KibanaRejected(
                f"agent policy '{policy_id}' is managed; agents cannot be reassigned into it")
        if target.get("is_default_fleet_server"):
            raise KibanaRejected(
                f"agent policy '{policy_id}' is the default Fleet Server policy; "
                "agents cannot be reassigned into it")

    @_translate_errors
    def reassign_agent(self, *, agent_id: str, policy_id: str) -> None:
        self._guard_reassign_target(policy_id)
        self._client.fleet_agents.reassign(agent_id=agent_id, policy_id=policy_id)

    @_translate_errors
    def upgrade_agent(self, *, agent_id: str, version: str, source_uri: str | None = None) -> None:
        kwargs: dict[str, Any] = {"agent_id": agent_id, "version": version}
        if source_uri is not None:
            kwargs["source_uri"] = source_uri
        self._client.fleet_agents.upgrade(**kwargs)

    @_translate_errors
    def unenroll_agent(self, *, agent_id: str, force: bool = False, revoke: bool = False) -> None:
        self._client.fleet_agents.unenroll(agent_id=agent_id, force=force, revoke=revoke)

    @_translate_errors
    def bulk_reassign(self, *, agent_ids: list[str], policy_id: str) -> str:
        self._guard_reassign_target(policy_id)
        action_id: str = self._client.fleet_agents.bulk_reassign(
            agents=agent_ids, policy_id=policy_id).body["actionId"]
        return action_id

    @_translate_errors
    def bulk_upgrade(self, *, agent_ids: list[str], version: str, source_uri: str | None = None) -> str:
        kwargs: dict[str, Any] = {"agents": agent_ids, "version": version}
        if source_uri is not None:
            kwargs["source_uri"] = source_uri
        action_id: str = self._client.fleet_agents.bulk_upgrade(**kwargs).body["actionId"]
        return action_id

    @_translate_errors
    def bulk_unenroll(self, *, agent_ids: list[str], force: bool = False, revoke: bool = False) -> str:
        action_id: str = self._client.fleet_agents.bulk_unenroll(
            agents=agent_ids, force=force, revoke=revoke).body["actionId"]
        return action_id
