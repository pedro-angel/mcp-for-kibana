"""KibanaGateway implementation on kibana-py. The ONLY module that may
import kibana (machine-enforced)."""

import contextlib
import functools
import inspect
import json
import re
import urllib.parse
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

import kibana
from kibana import SpacesClient, SpaceScopedKibana
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
    KibanaMcpError,
    KibanaNotFound,
    KibanaRejected,
    KibanaSpaceNotFound,
    KibanaUnavailable,
)
from kibana_mcp.core.models import (
    AlertingHealth,
    RuleType,
    AlertRule,
    AlertRuleDetail,
    ApiDeprecation,
    ApmAgentConfig,
    ApmAnnotation,
    ApmEnvironment,
    ApmSourcemap,
    Case,
    Connector,
    ConnectorDetail,
    CopiedObject,
    CopyFailure,
    DashboardDetail,
    DashboardSummary,
    DataViewDetail,
    DataViewSummary,
    DefaultDataView,
    DefaultDataViewChange,
    DetectionAlert,
    DetectionRule,
    EmailDefaults,
    ExceptionItem,
    ExceptionList,
    ExceptionListChange,
    ExceptionListImportResult,
    FleetAgent,
    FleetAgentDiagnostics,
    FleetAgentPolicy,
    FleetAgentStatus,
    FleetAgentTagChange,
    FleetAgentTagsResult,
    FleetDiagnosticsRequest,
    FleetEnrollmentKey,
    FleetOutput,
    FleetOutputHealth,
    FleetPackage,
    FleetPackageCategory,
    FleetPackageInstall,
    FleetPackagePolicy,
    FleetPackageUninstall,
    FleetPermissions,
    FleetServerHost,
    FleetSettings,
    FleetUninstallToken,
    ImportedObject,
    KibanaStats,
    KibanaStatus,
    ObjectSpaces,
    PanelSummary,
    PrepackagedRulesStatus,
    Role,
    RuleSnooze,
    RoleIndexPrivilege,
    RoleKibanaPrivilege,
    RuntimeField,
    RuntimeFieldUpsert,
    SavedObjectDetail,
    SavedObjectImportResult,
    SavedObjectPage,
    SavedObjectSummary,
    ServiceHealth,
    SharingChange,
    ShortUrl,
    Space,
    SpaceCopyResult,
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
    VisualizationDetail,
    VisualizationSummary,
)
from kibana_mcp.core.exception_lists import import_error_text
from kibana_mcp.core.fleet_ops import (
    AGENT_MONITORING_PACKAGE,
    guard_reason,
    guarded_policy_refusal,
    installed_version_refusal,
    missing_agents_message,
    monitoring_refusal,
    not_installed_message,
    outdated_version_message,
    package_policies_refusal,
    space_awareness_refusal,
    tags_not_applied,
    unattached_guard_reason,
    unknown_agent_message,
    unknown_package_message,
    unknown_version_message,
)
from kibana_mcp.core.saved_objects import READABLE_TYPES, unreadable_type_message
from kibana_mcp.core.space_sharing import (
    ALL_SPACES,
    SPACE_ERROR,
    copy_fix,
    missing_spaces_message,
    objects_not_found_message,
    self_target_message,
    sharing_refusal,
    source_fixed_reason,
    space_failure_fix,
    unreadable_space_fix,
    unreadable_spaces_message,
    unshare_refusal,
)
from kibana_mcp.core.visualizations.translate import describe_lens_config, lens_chart_type
from kibana_mcp.ports.gateway import KibanaGateway


_PINNED_PATH_RE = re.compile(r"(?:^|/)s/[a-z0-9_-]+$")


def is_space_pinned(url: str) -> bool:
    """True when the URL path (trailing slashes stripped) ends in
    '/s/<space-id-grammar>'. Purely syntactic: a reverse-proxy base path
    ending in s/<id-shaped-segment> false-positives, and a proxy that
    rewrites to /s/<id> is invisible — both documented limitations."""
    return pinned_space(url) is not None


def pinned_space(url: str) -> str | None:
    """The space id a '/s/<id>' base path pins, else None (same limits as
    is_space_pinned)."""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    match = _PINNED_PATH_RE.search(path)
    return match.group(0).rsplit("/", 1)[1] if match else None


_SPACE_GUIDANCE = (
    "space '{space}' not found — check what exists with list_spaces, "
    "create it with create_space (both tools exist when the platform-admin "
    "toolbox is enabled), or omit `space` to use the default space"
)


# What to do about a 401 / 403 from Kibana (D17: the error names the fix). A
# 403 names no single privilege: Kibana's text says which action it refused.
_SEND_KEY = "send it as 'Authorization: ApiKey <key>' or set KIBANA_API_KEY"
_INVALID_KEY_FIX = f"the API key is invalid, expired or invalidated: use a valid one — {_SEND_KEY}"
_PRIVILEGE_FIX = (
    "the API key lacks the privilege for this action in this space; a key keeps the "
    "privileges its owner had when it was created, so use an API key created by a user "
    f"whose role grants it there — {_SEND_KEY}"
)


def _auth_error(e: AuthenticationException | AuthorizationException, fix: str) -> str:
    return f"Kibana rejected the API key or permissions: {e.message} — {fix}"


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
    if isinstance(e, AuthenticationException):
        return KibanaAuthError(_auth_error(e, _INVALID_KEY_FIX))
    if isinstance(e, AuthorizationException):
        return KibanaAuthError(_auth_error(e, _PRIVILEGE_FIX))
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


# Where a valid id comes from, per object kind: (parameter, source tools, the
# human label a caller tends to pass instead). D17: a wrong id names the fix.
_ID_SOURCES = {
    "dashboard": ("dashboard_id", "create_dashboard or search_dashboards", "title"),
    "alert rule": ("rule_id", "create_alert_rule or list_alert_rules", "name"),
    "connector": ("connector_id", "create_connector or list_connectors", "name"),
    "data view": ("view_id", "create_data_view or list_data_views", "name or index pattern"),
    "visualization": (
        "visualization_id", "create_visualization or search_visualizations", "title"
    ),
    "synthetic monitor": (
        "monitor_id", "create_synthetic_monitor or list_synthetic_monitors", "name"
    ),
    "synthetic param": ("param_id", "create_synthetic_param or list_synthetic_params", "key"),
    "private location": (
        "location_id",
        "create_synthetic_private_location or list_synthetic_private_locations",
        "label",
    ),
}

# How many date fields a time-field rejection lists before eliding the rest.
_DATE_FIELDS_SHOWN = 20

# Rule fields a PUT /api/alerting/rule/{id} drops or resets when omitted, beyond
# the ones update_alert_rule exposes: re-sent from the current rule whenever it
# carries them (probed on 9.5.4: an omitted notify_when resets to null).
_RULE_PRESERVED_KEYS = ("notify_when", "throttle", "alert_delay", "flapping", "artifacts")
# The rule-level timing pair that per-action `frequency` replaces: never re-sent
# alongside caller actions that carry a frequency (Kibana 400s the mix).
_RULE_LEVEL_FREQUENCY_KEYS = frozenset({"notify_when", "throttle"})

# Connector types whose secrets schema is empty: Kibana rejects ANY secrets key on
# them ("Unrecognized key", probed on 9.5.4), so an update that omits `secrets`
# cannot lose one. They are also the only types the Basic licence enables. Every
# other type may hold secrets that a PUT without `secrets` resets to {}.
_SECRETLESS_CONNECTOR_TYPES = frozenset({".index", ".server-log"})


@contextlib.contextmanager
def _known_id(kind: str, value: str) -> Iterator[None]:
    """Reject an empty id before kibana-py does (its bare ValueError is
    unmapped), and turn a 404 on the id into guidance naming where ids come
    from. A missing *space* still surfaces as the space (fail-closed)."""
    param, sources, _label = _ID_SOURCES[kind]
    if not value.strip():
        raise KibanaRejected(f"{param} is empty — pass the id that {sources} returns")
    try:
        yield
    except SpaceNotFoundError:
        raise
    except NotFoundError as e:
        raise _unknown_id(kind, value) from e


def _unknown_id(kind: str, value: str) -> KibanaNotFound:
    """D17's not-found text for `kind`: also raised where Kibana reports a miss
    without a 404 (a Synthetics delete answers 200 with `deleted: false`)."""
    param, sources, label = _ID_SOURCES[kind]
    lookup = sources.split(" or ")[1]
    return KibanaNotFound(
        f"{kind} '{value}' not found — {param} is the id that {sources} returns, "
        f"not the {kind.split()[-1]}'s {label}; call {lookup} to find it"
    )


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


def _to_data_view_detail(raw: dict[str, Any]) -> DataViewDetail:
    return DataViewDetail(
        id=raw["id"],
        name=raw.get("name") or raw["title"],
        index_pattern=raw["title"],
        time_field=raw.get("timeFieldName"),
        fields=_normalize_fields(raw.get("fields", {})),
    )


def _time_field_problem(time_field: str, fields: dict[str, str], pattern: str) -> str | None:
    """Why `time_field` cannot be the time field of a view over `pattern`, or
    None when it can. Kibana stores any string here (probed on 9.5.4: a missing
    field and a keyword field were both accepted), so this is the only check.
    A pattern that matches no index has no fields yet: nothing to check against."""
    if not fields or fields.get(time_field) == "date":
        return None
    if time_field in fields:
        what = f"is a {fields[time_field]} field, not a date field, of '{pattern}'"
    else:
        what = f"is not a field of '{pattern}'"
    dates = sorted(n for n, t in fields.items() if t == "date")
    if not dates:
        return (
            f"time field '{time_field}' {what} — '{pattern}' has no date fields, so a data "
            "view over it cannot have a time field; use create_data_view without time_field"
        )
    shown = ", ".join(dates[:_DATE_FIELDS_SHOWN]) + (", …" if len(dates) > _DATE_FIELDS_SHOWN else "")
    return f"time field '{time_field}' {what} — pass time_field as one of its date fields: {shown}"


def _domain_error(e: BaseException, space: str | None) -> KibanaMcpError | None:
    """`e` as the domain error the tools would show, or None when unmapped."""
    translated = e if isinstance(e, KibanaMcpError) else _translated(e, space)
    return translated if isinstance(translated, KibanaMcpError) else None


def _cause_text(e: BaseException, space: str | None) -> str:
    """The text a tool would show for `e` (safe for the LLM: no raw repr)."""
    d = _domain_error(e, space)
    if d is None:
        return "an unexpected error"
    detail = d.detail if isinstance(d, KibanaRejected) else ""
    return f"{d.message}: {detail}" if detail else d.message


def _with_state(e: BaseException, space: str | None, state: str) -> BaseException:
    """The domain error for a write that failed after an earlier write of the
    same call landed: the original cause, then what the object now holds and
    the call that finishes or undoes it. Unmapped errors pass through."""
    d = _domain_error(e, space)
    if d is None:
        return e
    if isinstance(d, KibanaRejected):
        if d.detail:
            return KibanaRejected(d.message, detail=f"{d.detail} — {state}")
        return KibanaRejected(f"{d.message} — {state}")
    return type(d)(f"{d.message} — {state}")


def _to_runtime_field(name: str, raw: dict[str, Any]) -> RuntimeField:
    sub = raw.get("fields")
    return RuntimeField(
        name=name,
        type=str(raw.get("type", "")),
        script=(raw.get("script") or {}).get("source"),
        fields=(
            {k: str((v or {}).get("type", "")) for k, v in sub.items()}
            if isinstance(sub, dict) else None
        ),
    )


def _composite_parent(name: str, stored: dict[str, Any]) -> str | None:
    """The composite runtime field `name` is a subfield of ('comp' for
    'comp.a'), or None. Kibana lists a composite's subfields among the view's
    fields under '<parent>.<sub>' (probed on 9.5.4)."""
    for parent, raw in stored.items():
        subfields = (raw or {}).get("fields") or {}
        if (raw or {}).get("type") == "composite" and name.startswith(f"{parent}."):
            if name[len(parent) + 1:] in subfields:
                return parent
    return None


# How many runtime-field names a not-found error lists before eliding the rest.
_RUNTIME_NAMES_SHOWN = 20


def _runtime_names(stored: dict[str, Any]) -> str:
    names = sorted(stored)
    if not names:
        return "it has no runtime fields"
    shown = ", ".join(names[:_RUNTIME_NAMES_SHOWN])
    return f"its runtime fields: {shown}" + (", …" if len(names) > _RUNTIME_NAMES_SHOWN else "")


def _to_saved_object_summary(body: dict[str, Any]) -> SavedObjectSummary:
    attributes = body.get("attributes") or {}
    title, name = attributes.get("title"), attributes.get("name")
    return SavedObjectSummary(
        type=body["type"],
        id=body["id"],
        title=title if isinstance(title, str) else None,
        name=name if isinstance(name, str) else None,
        updated_at=body.get("updated_at"),
        namespaces=tuple(body.get("namespaces") or ()),
        managed=bool(body.get("managed", False)),
    )


def _to_saved_object_detail(body: dict[str, Any]) -> SavedObjectDetail:
    # A dashboard holds one reference per panel layer (the flights sample: 27
    # references to 4 objects), so the distinct objects are what is returned.
    seen: dict[tuple[str, str], None] = {}
    for ref in body.get("references") or ():
        seen.setdefault((str(ref.get("type", "")), str(ref.get("id", ""))), None)
    return SavedObjectDetail(
        type=body["type"],
        id=body["id"],
        namespaces=tuple(body.get("namespaces") or ()),
        created_at=body.get("created_at"),
        updated_at=body.get("updated_at"),
        managed=bool(body.get("managed", False)),
        attributes=dict(body.get("attributes") or {}),
        references=tuple({"type": t, "id": i} for t, i in seen),
    )


def _saved_object_miss(type: str, id: str) -> KibanaNotFound:
    return KibanaNotFound(
        f"saved object '{type}/{id}' not found — check the type and id with "
        f"find_saved_objects(types=['{type}'])"
    )


def _to_short_url(body: dict[str, Any]) -> ShortUrl:
    return ShortUrl(
        id=body["id"],
        slug=body.get("slug", ""),
        locator_id=(body.get("locator") or {}).get("id", ""),
        url=body.get("url"),
    )


def _to_rule_type(raw: dict[str, Any]) -> RuleType:
    consumers = raw.get("authorized_consumers") or {}
    return RuleType(
        id=raw["id"],
        name=raw.get("name", ""),
        category=raw.get("category") or "",
        producer=raw.get("producer") or "",
        consumers=tuple(sorted(c for c, p in consumers.items() if (p or {}).get("all"))),
        enabled_in_license=bool(raw.get("enabled_in_license")),
        minimum_license_required=raw.get("minimum_license_required") or "",
        internally_managed=bool(raw.get("is_internally_managed")),
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


def _to_rule_action(raw: dict[str, Any]) -> dict[str, object]:
    # The keys update_alert_rule's `actions` take, so a read action can be
    # edited and passed back. GET's connector_type_id is left out (the PUT
    # schema rejects it), as is any other key. `uuid` is kept: the PUT accepts
    # it and Kibana then keeps the action's identity, where an action sent
    # without one gets a new uuid (probed on 9.5.4, D40). A system action has
    # no group, and an action without per-action timing no frequency: those
    # keys (and uuid) appear only when Kibana sent them, so a round trip adds
    # nothing.
    action: dict[str, object] = {"id": raw.get("id", "")}
    if raw.get("uuid") is not None:
        action["uuid"] = raw["uuid"]
    if "group" in raw:
        action["group"] = raw["group"]
    action["params"] = dict(raw.get("params") or {})
    if raw.get("frequency") is not None:
        action["frequency"] = dict(raw["frequency"])
    return action


def _to_alert_rule_detail(body: dict[str, Any]) -> AlertRuleDetail:
    # Built from named keys only. The GET-rule body carries no secret (probed
    # on 9.5.4: the rule's API key is never returned, only api_key_owner);
    # none of its bookkeeping keys reach the caller either.
    s = _to_alert_rule(body)
    return AlertRuleDetail(
        id=s.id, name=s.name, rule_type_id=s.rule_type_id, consumer=s.consumer,
        enabled=s.enabled, schedule_interval=s.schedule_interval, status=s.status,
        tags=s.tags,
        params=dict(body.get("params") or {}),
        actions=tuple(_to_rule_action(a) for a in body.get("actions") or []),
        # mute_all / muted_alert_ids are on every GET (probed on 9.5.4); the
        # rule's snoozes are not, on any public route, so none is returned.
        muted=bool(body.get("mute_all", False)),
        muted_alert_ids=tuple(str(a) for a in body.get("muted_alert_ids") or []),
    )


def _kibana_instant(value: datetime) -> str:
    """An aware datetime in the only form the snooze API accepts:
    YYYY-MM-DDTHH:mm:ss.sssZ (probed on 9.5.4: '...:00Z' and '+02:00' are 400s)."""
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _to_rule_snooze(rule_id: str, body: dict[str, Any]) -> RuleSnooze:
    schedule = body.get("schedule") or {}
    custom = schedule.get("custom") or {}
    recurring = custom.get("recurring")
    return RuleSnooze(
        rule_id=rule_id,
        schedule_id=str(schedule.get("id", "")),
        duration=str(custom.get("duration", "")),
        start=str(custom.get("start", "")),
        timezone=str(custom.get("timezone", "")),
        recurring=dict(recurring) if isinstance(recurring, dict) else None,
    )


# Kibana's 404 bodies for a part of a rule that is missing while the rule
# itself exists (probed on 9.5.4). Any other 404 on these routes — "Saved
# object [alert/<id>] not found" — is the rule, so it gets the rule guidance.
_MISSING_ALERT_MARK = "Alert instance with id"
_MISSING_SNOOZE_MARKS = ("has no snooze schedule",)
_SNOOZE_LIMIT_MARK = "cannot have more than 5 snooze schedules"
_ALERT_ID_FIX = (
    "pass the id of an alert the rule has raised (get_alert_rule lists the muted ones "
    "in muted_alert_ids)"
)


def _alert_not_found(rule_id: str, alert_id: str) -> KibanaNotFound:
    return KibanaNotFound(
        f"alert '{alert_id}' not found on alert rule '{rule_id}' — alert_id is the id of an "
        "alert the rule has raised (for a rule grouped by a field, the group's value, e.g. "
        "a host name; for an ungrouped .es-query rule, 'query matched'), not the rule's "
        "name; an alert can be muted only once the rule has raised it — to silence the "
        "whole rule use mute_alert_rule"
    )


def _snooze_not_found(rule_id: str, schedule_id: str) -> KibanaNotFound:
    return KibanaNotFound(
        f"snooze schedule '{schedule_id}' not found on alert rule '{rule_id}' — schedule_id "
        "is the schedule_id that snooze_alert_rule returned (Kibana's public API does not "
        "list a rule's snoozes); it may already have been removed"
    )


def _blank(param: str, value: str, fix: str) -> None:
    """Refuse an empty id before kibana-py does (its bare ValueError is unmapped)."""
    if not value.strip():
        raise KibanaRejected(f"{param} is empty — {fix}")


def _to_connector(body: dict[str, Any]) -> Connector:
    return Connector(
        id=body["id"],
        name=body.get("name", ""),
        connector_type_id=body.get("connector_type_id", ""),
        is_missing_secrets=bool(body.get("is_missing_secrets", False)),
        is_preconfigured=bool(body.get("is_preconfigured", False)),
    )


def _to_connector_detail(body: dict[str, Any]) -> ConnectorDetail:
    # Built from named keys only: a secrets key in a response could never
    # reach the caller through this mapping.
    return ConnectorDetail(
        id=body["id"],
        name=body.get("name", ""),
        connector_type_id=body.get("connector_type_id", ""),
        config=dict(body.get("config") or {}),
        is_preconfigured=bool(body.get("is_preconfigured", False)),
        is_system_action=bool(body.get("is_system_action", False)),
        is_deprecated=bool(body.get("is_deprecated", False)),
        is_missing_secrets=bool(body.get("is_missing_secrets", False)),
        is_connector_type_deprecated=bool(body.get("is_connector_type_deprecated", False)),
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


def _given(**kwargs: Any) -> dict[str, Any]:
    """The keyword arguments that were given: None means "leave it out"."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _monitor_schedule(schedule: str) -> dict[str, str]:
    # "10m" / "30s" (the read shape, validated by the tool) -> Kibana's
    # {"number": "10", "unit": "m"}; Kibana stores the number as a string and
    # compares it as text, so "05" is a 400 (9.5.4): send the canonical digits.
    return {"number": str(int(schedule[:-1])), "unit": schedule[-1]}


def _deleted(body: Any, kind: str, value: str) -> None:
    """A Synthetics monitor/param DELETE answers 200 with a per-id result list,
    also for an id that matches nothing: `deleted: false` (a monitor adds
    "Monitor id <id> not found!", a param no text; probed on 9.5.4)."""
    results = [r for r in (body if isinstance(body, list) else []) if isinstance(r, dict)]
    if any(r.get("deleted") is True for r in results):
        return
    error = next((str(r["error"]) for r in results if r.get("error")), "")
    if error and "not found" not in error.lower():
        raise KibanaRejected(f"Kibana did not delete the {kind}", detail=error)
    raise _unknown_id(kind, value)


@contextlib.contextmanager
def _secret_withheld(secret: str | None) -> Iterator[None]:
    """A Synthetics param's value is a secret: a payload rejection never carries
    it back to the model, whatever Kibana's text quotes."""
    try:
        yield
    except (BadRequestError, ConflictError) as e:
        d = _translated(e, None)
        if not isinstance(d, KibanaRejected):
            raise
        if secret:
            d = KibanaRejected(
                d.message.replace(secret, "[redacted]"),
                detail=d.detail.replace(secret, "[redacted]"),
            )
        raise d from e


_AGENT_POLICY_HINT = (
    "agent_policy_id is the id of a Fleet agent policy that no other private location uses: "
    "list_agent_policies shows them, create_agent_policy creates one (fleet toolbox)"
)
_LOCATION_IN_USE_HINT = (
    "move those monitors to another private location with update_synthetic_monitor "
    "(private_locations) or delete them with delete_synthetic_monitor first; "
    "list_synthetic_monitors shows each monitor's locations"
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
        agent_name=body.get("agent_name") or None,
    )


def _apm_scope(service_name: str | None, environment: str | None) -> str:
    service = f"service '{service_name}'" if service_name is not None else "all services"
    env = f"environment '{environment}'" if environment is not None else "all environments"
    return f"{service}, {env}"


@contextlib.contextmanager
def _known_apm_config(service_name: str | None, environment: str | None) -> Iterator[None]:
    """Kibana's 404 on an APM agent config (view, delete) says only "Not Found"
    (probed on 9.5.4): name the exact-match rule and where configs are listed."""
    try:
        yield
    except NotFoundError as e:
        raise KibanaNotFound(
            f"no APM agent configuration for {_apm_scope(service_name, environment)} — "
            "service_name and environment must match a configuration exactly (an omitted one "
            "means all); call list_apm_agent_configs to see them"
        ) from e


_APM_SETTINGS_HINT = (
    "each value is a string in the form the setting takes (a number such as '0.5', 'true' or "
    "'false', a duration such as '10s', or one of the setting's words)"
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
        description=body.get("description") or "",
    )


def _unknown_exception_list(
    id: str | None, list_id: str | None, namespace_type: str, *, list_id_only: bool = False
) -> KibanaNotFound:
    """D17's not-found text for an exception list: where the ids come from,
    and the namespace a list is found in (a list made with
    namespace_type='agnostic' answers 404 to a 'single' read, probed). A tool
    that takes only `list_id` says which field that is: the list's uuid `id`
    is the likeliest wrong value."""
    which = f"list_id '{list_id}'" if list_id else f"id '{id}'"
    other = "agnostic" if namespace_type == "single" else "single"
    where = (
        "pass the list's `list_id` field from find_exception_lists or "
        "create_exception_list, not its `id` (a uuid) or its name"
        if list_id_only else
        "`id` and `list_id` are the values find_exception_lists or create_exception_list "
        "return, not the list's name"
    )
    return KibanaNotFound(
        f"exception list {which} not found (namespace_type '{namespace_type}') — {where}; "
        f"a list created with namespace_type='{other}' is found "
        f"only with namespace_type='{other}'"
    )


def _to_exception_import(body: dict[str, Any]) -> ExceptionListImportResult:
    return ExceptionListImportResult(
        success=bool(body.get("success", False)),
        list_count=int(body.get("success_count_exception_lists", 0) or 0),
        item_count=int(body.get("success_count_exception_list_items", 0) or 0),
        errors=tuple(
            import_error_text(e) for e in (body.get("errors") or []) if isinstance(e, dict)
        ),
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


def _ref_pair(raw: dict[str, Any]) -> dict[str, str]:
    return {"type": str(raw.get("type", "")), "id": str(raw.get("id", ""))}


def _to_copy_failure(raw: dict[str, Any], overwrite: bool) -> CopyFailure:
    error = raw.get("error") or {}
    kind = str(error.get("type") or "unknown")
    return CopyFailure(
        type=str(raw.get("type", "")), id=str(raw.get("id", "")),
        title=(raw.get("meta") or {}).get("title"), error=kind,
        fix=copy_fix(kind, overwrite),
        missing_references=tuple(_ref_pair(r) for r in error.get("references") or ()),
        destinations=tuple(
            {"id": str(d.get("id", "")), "title": str(d.get("title", ""))}
            for d in error.get("destinations") or ()
        ),
    )


def _to_space_copy_result(
    space: str, raw: dict[str, Any], objects: list[dict[str, str]], overwrite: bool
) -> SpaceCopyResult:
    """One target space's entry of the `_copy_saved_objects` map. A requested
    object in neither list is already in the space (overwriting an object
    shared to it answers successCount 0 and nothing else, probed)."""
    copied = tuple(
        CopiedObject(
            type=str(r.get("type", "")), id=str(r.get("id", "")),
            title=(r.get("meta") or {}).get("title"),
            # Kibana omits destinationId when the copy keeps the source id.
            destination_id=str(r.get("destinationId") or r.get("id", "")),
            overwritten=bool(r.get("overwrite")),
        )
        for r in raw.get("successResults") or ()
    )
    entries = [e for e in raw.get("errors") or () if isinstance(e, dict)]
    failed = tuple(_to_copy_failure(e, overwrite) for e in entries if _is_object_error(e))
    seen = {(c.type, c.id) for c in copied} | {(f.type, f.id) for f in failed}
    rest = [o for o in objects if (o["type"], o["id"]) not in seen]
    whole = [e for e in entries if not _is_object_error(e)]
    if whole:
        # Kibana refused the import into this space as a whole (its copy route
        # answers that space with a Boom payload {statusCode, error, message},
        # no object): nothing else was written here, so the rest failed too —
        # never "unchanged". The other target spaces keep their own results.
        e = whole[0]
        fix = space_failure_fix(
            space, e.get("statusCode"), str(e.get("error") or ""), str(e.get("message") or ""))
        failed += tuple(
            CopyFailure(type=o["type"], id=o["id"], title=None, error=SPACE_ERROR, fix=fix,
                        missing_references=(), destinations=())
            for o in rest
        )
        rest = []
    return SpaceCopyResult(
        space=space, success=bool(raw.get("success")) and not whole, copied=copied,
        failed=failed, unchanged=tuple(_ref_pair(o) for o in rest),
    )


def _is_object_error(entry: dict[str, Any]) -> bool:
    """A per-object import failure ({type, id, error: {type, …}}), as opposed to
    the bare error Kibana answers when a whole target space failed."""
    return isinstance(entry.get("error"), dict) and "type" in entry and "id" in entry


def _to_object_spaces(raw: dict[str, Any]) -> ObjectSpaces:
    return ObjectSpaces(
        type=str(raw.get("type", "")), id=str(raw.get("id", "")),
        spaces=tuple(raw.get("spaces") or ()), missing=bool(raw.get("isMissing")),
        spaces_with_matching_origins=tuple(raw.get("spacesWithMatchingOrigins") or ()),
    )


def _to_sharing_changes(
    before: list[ObjectSpaces], body: dict[str, Any]
) -> list[SharingChange]:
    """Each object's spaces after `_update_objects_spaces`, against what the
    read before it saw. A per-object error (200 with `error`, spaces []) left
    that object as it was; the others were still updated."""
    after = {(o.get("type"), o.get("id")): o for o in body.get("objects") or ()}
    changes = []
    for state in before:
        raw = after.get((state.type, state.id)) or {}
        error = raw.get("error")
        spaces = state.spaces if error else tuple(raw.get("spaces") or ())
        changes.append(SharingChange(
            type=state.type, id=state.id, spaces=spaces,
            added=tuple(s for s in spaces if s not in state.spaces),
            removed=tuple(s for s in state.spaces if s not in spaces),
            deleted=not error and not spaces,
            spaces_with_matching_origins=state.spaces_with_matching_origins,
            error=str(error.get("message") or error) if error else None,
        ))
    return changes


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
        tags=tuple(body.get("tags") or ()),
    )


def _to_fleet_agent_diagnostics(body: dict[str, Any]) -> FleetAgentDiagnostics:
    # filePath (a Kibana download route) is left out: the bundle is fetched in
    # Kibana's UI, never through the model.
    return FleetAgentDiagnostics(
        id=body.get("id", ""),
        name=body.get("name", ""),
        status=body.get("status", ""),
        created_at=body.get("createTime", ""),
        action_id=body.get("actionId", ""),
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


class _SourceSpaceSpaces(SpacesClient):
    """kibana-py's SpacesClient with every path under `/s/<space>`. Kibana's
    copy and share routes act on the space of the request ("copies from the
    current space"), but kibana-py serves `spaces` unscoped on a space-scoped
    client and never prefixes these paths: a share from a throwaway space read
    `isMissing` for a data view created in it (probed on 9.5.4, D49)."""

    def __init__(self, client: kibana.Kibana, space: str) -> None:
        super().__init__(client, default_space_id=space, validate_spaces=False)

    def perform_request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None, body: dict[str, Any] | None = None,
    ) -> Any:
        return super().perform_request(
            method, self._build_space_path(path), params=params, headers=headers, body=body)


class KibanaPyGateway:
    def __init__(
        self, client: kibana.Kibana | SpaceScopedKibana, space: str | None = None,
        pinned_space: str | None = None, public_url_pinned: bool = False,
    ) -> None:
        self._client = client
        self._space = space
        # The space a '/s/<id>' KIBANA_URL pins (connect sets it): the source
        # space of the copy and share calls when no `space` scopes the gateway.
        self._pinned_space = pinned_space
        # Why this deployment refuses source_space (either URL pinned, D8),
        # None when it accepts it: the copy and share guidance names the URL
        # that fixes the source instead of offering source_space.
        self._source_fixed = source_fixed_reason(pinned_space is not None, public_url_pinned)

    @classmethod
    def connect(
        cls, url: str, api_key: str, space: str | None = None, *,
        public_url_pinned: bool = False,
    ) -> "KibanaPyGateway":
        """`public_url_pinned`: the factory refuses `space` because the public
        URL is space-pinned (only it can see that URL)."""
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
                if isinstance(e, (AuthenticationException, AuthorizationException)):
                    raise KibanaAuthError(_auth_error(e, (
                        f"raised while validating space '{space}'; the API key must be "
                        "valid and able to read spaces: " + (
                            unreadable_space_fix([space])
                            if isinstance(e, AuthorizationException) else _INVALID_KEY_FIX)
                    ))) from e
                if translated is e:
                    raise  # pass-through unchanged, no self-referential cause
                raise translated from e
        return cls(
            client, space, pinned_space=pinned_space(url), public_url_pinned=public_url_pinned)

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
        return _to_data_view_detail(raw)

    @_translate_errors
    def create_data_view(
        self, index_pattern: str, name: str | None, time_field: str | None
    ) -> DataViewSummary:
        # Kibana stores any string as the time field (probed on 9.5.4) and has
        # no public route that lists a pattern's fields without creating a view
        # (D43), so the view is created and its time field checked against the
        # fields the create returns; on a mismatch the view is deleted again,
        # so a refused call leaves nothing behind.
        spec: dict[str, Any] = {"title": index_pattern}
        if name is not None:
            spec["name"] = name
        if time_field is not None:
            spec["timeFieldName"] = time_field
        try:
            raw = self._client.data_views.create(data_view=spec).body["data_view"]
        except BadRequestError as e:
            duplicate = self._duplicate_data_view(e, name or index_pattern)
            if duplicate is None:
                raise
            raise duplicate from e
        view_id = raw["id"]
        if time_field is not None:
            problem = _time_field_problem(
                time_field, _normalize_fields(raw.get("fields", {})), index_pattern
            )
            if problem:
                try:
                    self._client.data_views.delete(view_id=view_id)
                except Exception as e:
                    # The cleanup failed: the view exists with the bad time field.
                    raise KibanaRejected(
                        f"{problem}. Deleting the data view this call created then failed "
                        f"({_cause_text(e, self._space)}), so data view '{view_id}' exists "
                        f"with time field '{time_field}' — fix it with update_data_view "
                        f"(view_id='{view_id}', time_field=<a date field>), or remove it with "
                        f"delete_data_view (view_id='{view_id}')"
                    ) from e
                raise KibanaRejected(f"{problem} (no data view was created)")
        return DataViewSummary(
            id=view_id, name=raw.get("name") or raw["title"], index_pattern=raw["title"]
        )

    def _duplicate_data_view(self, e: BadRequestError, name: str) -> KibanaRejected | None:
        """Kibana's 400 for a data view whose NAME is taken (the name defaults
        to the pattern; probed on 9.5.4: "Duplicate data view: <name>"), as an
        error naming the view that holds it and update_data_view. None for any
        other 400."""
        message = str(e.message)
        if not message.startswith("Duplicate data view"):
            return None
        holder: str | None = None
        with contextlib.suppress(Exception):  # the id is a courtesy, not required
            ids = [v.id for v in self.list_data_views() if v.name == name]
            holder = ids[0] if len(ids) == 1 else None
        held = f" (id '{holder}')" if holder else ""
        edit = f"update_data_view(view_id='{holder}')" if holder else "update_data_view"
        return KibanaRejected(
            "Kibana rejected the payload",
            detail=(
                f"{message} — a data view named '{name}'{held} already exists: use it, "
                f"change it in place with {edit}, or pass a different name"
            ),
        )

    def _post_data_view(
        self, view_id: str, changes: dict[str, Any], refresh_fields: bool = False
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"view_id": view_id, "data_view": changes}
        if refresh_fields:
            kwargs["refresh_fields"] = True
        with _known_id("data view", view_id):
            return dict(self._client.data_views.update(**kwargs).body["data_view"])

    @_translate_errors
    def update_data_view(
        self, view_id: str, name: str | None, index_pattern: str | None, time_field: str | None
    ) -> DataViewDetail:
        # POST /api/data_views/data_view/{id} merges: only the keys sent change
        # (probed on 9.5.4), so omitted fields need no re-send. The read is for
        # what Kibana does not check: it stores a time field that is missing or
        # not a date. A pattern change is written first and checked against the
        # NEW pattern's fields (Kibana recomputes them on write); on a mismatch
        # the old pattern is written back, so a refused call changes nothing.
        with _known_id("data view", view_id):
            cur = dict(self._client.data_views.get(view_id=view_id).body["data_view"])
        old_pattern = cur["title"]
        new_pattern = index_pattern if index_pattern not in (None, old_pattern) else None
        rest = {k: v for k, v in (("name", name), ("timeFieldName", time_field)) if v is not None}
        # The time field to check: the one passed, else the kept one when the
        # pattern under it changes. A view with no time field keeps none.
        check = time_field if time_field is not None else (
            (cur.get("timeFieldName") or None) if new_pattern is not None else None
        )
        if new_pattern is None:
            if check is not None:
                problem = _time_field_problem(
                    check, _normalize_fields(cur.get("fields", {})), old_pattern
                )
                if problem:
                    raise KibanaRejected(problem)
            return _to_data_view_detail(self._post_data_view(view_id, rest) if rest else cur)
        if check is None:
            return _to_data_view_detail(
                self._post_data_view(view_id, {**rest, "title": new_pattern}, refresh_fields=True)
            )
        raw = self._post_data_view(view_id, {"title": new_pattern}, refresh_fields=True)
        problem = _time_field_problem(check, _normalize_fields(raw.get("fields", {})), new_pattern)
        undo = f"to undo, call update_data_view with index_pattern='{old_pattern}'"
        if problem:
            try:
                self._post_data_view(view_id, {"title": old_pattern}, refresh_fields=True)
            except Exception as e:
                # The restore failed: say so, and what the view now holds.
                raise KibanaRejected(
                    f"{problem}. Restoring the old index pattern then failed "
                    f"({_cause_text(e, self._space)}), so the index pattern is now "
                    f"'{new_pattern}' — {undo}"
                ) from e
            raise KibanaRejected(f"{problem} (nothing was changed)")
        if not rest:
            return _to_data_view_detail(raw)
        try:
            return _to_data_view_detail(self._post_data_view(view_id, rest))
        except Exception as e:
            # The pattern change already landed; the second write did not.
            retry = ", ".join(
                f"{arg}='{rest[key]}'"
                for arg, key in (("name", "name"), ("time_field", "timeFieldName"))
                if key in rest
            )
            raise _with_state(
                e, self._space,
                f"the index pattern was changed to '{new_pattern}' but {retry} was not "
                f"saved; call update_data_view again with {retry} to finish, or {undo}",
            ) from e

    @_translate_errors
    def delete_data_view(self, view_id: str) -> None:
        self._client.data_views.delete(view_id=view_id)

    def _read_view(self, view_id: str) -> dict[str, Any]:
        with _known_id("data view", view_id):
            return dict(self._client.data_views.get(view_id=view_id).body["data_view"])

    @_translate_errors
    def upsert_runtime_field(self, view_id: str, field: RuntimeField) -> RuntimeFieldUpsert:
        # Kibana's PUT .../runtime_field creates or replaces, and stores any
        # script: it compiles nothing (probed on 9.5.4), so the read here is for
        # what it does not report — the definition a replacement loses, the
        # custom label and description its PUT drops unless re-sent, a mapped
        # name (its PUT 400s "Only runtime fields can be updated"), and a stored
        # composite field, which this single-value surface cannot put back.
        cur = self._read_view(view_id)
        stored = dict(cur.get("runtimeFieldMap") or {})
        name = field.name
        prev_raw = stored.get(name)
        if prev_raw is None:
            parent = _composite_parent(name, stored)
            if parent is not None:
                raise KibanaRejected(
                    f"'{name}' is a subfield of the composite runtime field '{parent}' of data "
                    f"view '{view_id}', which this tool does not manage — pick another name"
                )
            if name in (cur.get("fields") or {}):
                raise KibanaRejected(
                    f"'{name}' is already a field of data view '{view_id}' "
                    f"('{cur.get('title', '')}') and not a runtime field (a mapped or scripted "
                    "field) — pick a name the view does not have; describe_data_view lists "
                    "its fields"
                )
        elif prev_raw.get("type") == "composite":
            raise KibanaRejected(
                f"runtime field '{name}' of data view '{view_id}' is a composite field, which "
                "this tool does not replace — remove it with delete_runtime_field first, or "
                "pick another name"
            )
        spec: dict[str, Any] = {"type": field.type}
        if field.script is not None:
            spec["script"] = {"source": field.script}
        if prev_raw is None:
            # A create uses the POST, which refuses a name taken in the meantime
            # ("Field [name = x] already exists.", probed) instead of replacing
            # a definition this call never read.
            try:
                with _known_id("data view", view_id):
                    body = self._client.data_views.create_runtime_field(
                        view_id=view_id, name=name, runtime_field=spec
                    ).body
            except BadRequestError as e:
                if "already exists" not in str(e.message):
                    raise
                raise KibanaRejected(
                    "Kibana rejected the payload",
                    detail=(
                        f"{e.message} — a field '{name}' was added to data view '{view_id}' "
                        "after this call read it; call upsert_runtime_field again (a runtime "
                        "field is then replaced and returned as `previous`)"
                    ),
                ) from e
        else:
            attrs = (cur.get("fieldAttrs") or {}).get(name) or {}
            for key in ("customLabel", "customDescription"):
                if attrs.get(key):
                    spec[key] = attrs[key]
            with _known_id("data view", view_id):
                body = self._client.data_views.create_or_update_runtime_field(
                    view_id=view_id, name=name, runtime_field=spec
                ).body
        written = ((body.get("data_view") or {}).get("runtimeFieldMap") or {}).get(name)
        return RuntimeFieldUpsert(
            view_id=view_id,
            field=_to_runtime_field(name, written) if written else field,
            previous=_to_runtime_field(name, prev_raw) if prev_raw is not None else None,
        )

    @_translate_errors
    def delete_runtime_field(self, view_id: str, name: str) -> RuntimeField:
        # Read first: the DELETE answers 200 with no body, and its 404 says only
        # "Field [index_pattern = <id>, field = <name>] not found." for a
        # missing name, a mapped field and a composite's subfield alike (probed).
        cur = self._read_view(view_id)
        stored = dict(cur.get("runtimeFieldMap") or {})
        raw = stored.get(name)
        if raw is None:
            parent = _composite_parent(name, stored)
            if parent is not None:
                raise KibanaRejected(
                    f"'{name}' is a subfield of the composite runtime field '{parent}' of data "
                    f"view '{view_id}' — delete '{parent}' to remove it with its subfields"
                )
            if name in (cur.get("fields") or {}):
                raise KibanaRejected(
                    f"'{name}' is a field of data view '{view_id}' but not a runtime field — "
                    f"only runtime fields can be deleted ({_runtime_names(stored)})"
                )
            raise KibanaNotFound(
                f"data view '{view_id}' has no runtime field '{name}' — {_runtime_names(stored)}"
            )
        try:
            self._client.data_views.delete_runtime_field(view_id=view_id, name=name)
        except SpaceNotFoundError:
            raise
        except NotFoundError as e:
            raise KibanaNotFound(
                f"runtime field '{name}' of data view '{view_id}' (or the view) was deleted "
                "after this call read it — this call deleted nothing; call list_data_views "
                "to see what exists"
            ) from e
        return _to_runtime_field(name, raw)

    def _default_view(self, view_id: str | None) -> DefaultDataView:
        if not view_id:
            return DefaultDataView(id=None, name=None, index_pattern=None, exists=False)
        try:
            raw = self._client.data_views.get(view_id=view_id).body["data_view"]
        except SpaceNotFoundError:
            raise
        except NotFoundError:
            # Kibana keeps the id of a default view that was deleted (probed).
            return DefaultDataView(id=view_id, name=None, index_pattern=None, exists=False)
        return DefaultDataView(
            id=view_id, name=raw.get("name") or raw["title"], index_pattern=raw["title"],
            exists=True,
        )

    @_translate_errors
    def get_default_data_view(self) -> DefaultDataView:
        # GET /api/data_views/default answers {"data_view_id": ""} when none is set.
        return self._default_view(self._client.data_views.get_default().body.get("data_view_id"))

    @_translate_errors
    def set_default_data_view(self, view_id: str | None) -> DefaultDataViewChange:
        previous = self._client.data_views.get_default().body.get("data_view_id") or None
        if view_id is None:
            current = DefaultDataView(id=None, name=None, index_pattern=None, exists=False)
        else:
            # Kibana stores any id as the default (probed: "bogus-id" answered 200),
            # so the view is read first; a miss is D17's guidance.
            raw = self._read_view(view_id)
            current = DefaultDataView(
                id=view_id, name=raw.get("name") or raw["title"], index_pattern=raw["title"],
                exists=True,
            )
        # force=True: without it Kibana answers 200 {"acknowledged": true} and
        # keeps a default that is already set (probed on 9.5.4).
        # Whether the old default names a view: Kibana keeps a deleted view's id,
        # which this tool will not store again, and treats it as no default (it
        # removes it and opens the first data view, data_views.js
        # getDefaultDataViewId on 9.5.4) — so view_id=None is that undo.
        previous_exists = previous is not None and (
            previous == view_id or self._default_view(previous).exists
        )
        self._client.data_views.set_default(data_view_id=view_id, force=True)
        return DefaultDataViewChange(
            current=current, previous_id=previous, previous_exists=previous_exists
        )

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
    def get_alert_rule(self, rule_id: str) -> AlertRuleDetail:
        with _known_id("alert rule", rule_id):
            return _to_alert_rule_detail(self._client.alerting.rule.get(id=rule_id).body)

    @_translate_errors
    def list_rule_types(self) -> list[RuleType]:
        return [_to_rule_type(r) for r in self._client.alerting.rule_types().body]

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
    def update_alert_rule(
        self, rule_id: str, name: str | None, tags: list[str] | None,
        schedule_interval: str | None, params: dict[str, Any] | None,
        actions: list[dict[str, Any]] | None,
    ) -> AlertRule:
        # Read-modify-write. PUT /api/alerting/rule/{id} is a full replace
        # (probed on 9.5.4): omitted `actions` and `tags` are wiped to [], and
        # omitted `params` fail validation. So fetch the rule, re-send every
        # writable field, and overlay only what the caller passed. `enabled`,
        # `rule_type_id` and `consumer` are not writable here (Kibana 400s
        # `enabled` in the body); enable/disable_alert_rule own `enabled`.
        with _known_id("alert rule", rule_id):
            cur = self._client.alerting.rule.get(id=rule_id).body
        current_params = dict(cur.get("params") or {})
        body: dict[str, Any] = {
            "name": name if name is not None else cur.get("name", ""),
            "schedule": ({"interval": schedule_interval} if schedule_interval is not None
                         else cur.get("schedule") or {}),
            # params merge key-by-key: the caller passes only what changes.
            "params": {**current_params, **params} if params is not None else current_params,
            "tags": list(tags) if tags is not None else list(cur.get("tags") or []),
            # GET echoes each action's connector_type_id, which the PUT schema
            # rejects ("Additional properties are not allowed"); uuid is accepted
            # and keeps the action's identity.
            "actions": (list(actions) if actions is not None else [
                {k: v for k, v in a.items() if k != "connector_type_id"}
                for a in cur.get("actions") or []
            ]),
        }
        # Kibana refuses per-action `frequency` while the rule carries a
        # rule-level notify_when/throttle (probed on 9.5.4), and a PUT that
        # omits them clears them. So actions that bring their own frequency
        # move the rule to per-action timing instead of failing.
        per_action = actions is not None and any("frequency" in a for a in actions)
        for key in _RULE_PRESERVED_KEYS:
            if per_action and key in _RULE_LEVEL_FREQUENCY_KEYS:
                continue
            if cur.get(key) is not None:
                body[key] = cur[key]
        with _known_id("alert rule", rule_id):
            return _to_alert_rule(self._client.alerting.rule.update(id=rule_id, **body).body)

    @_translate_errors
    def enable_alert_rule(self, rule_id: str) -> None:
        with _known_id("alert rule", rule_id):
            self._client.alerting.rule.enable(id=rule_id)

    @_translate_errors
    def disable_alert_rule(self, rule_id: str, untrack: bool = False) -> None:
        # untrack is sent only when asked: without it the request is the one
        # disable_alert_rule always sent, and the rule's active alerts stay
        # active; with it Kibana marks them untracked (probed on 9.5.4).
        kwargs: dict[str, Any] = {"id": rule_id}
        if untrack:
            kwargs["untrack"] = True
        with _known_id("alert rule", rule_id):
            self._client.alerting.rule.disable(**kwargs)

    # Kibana's rule-wide mute and unmute both reset muted_alert_ids to [] (probed
    # on 9.5.4), and no public route lists a rule's alert ids: the per-alert
    # mutes are read first and returned, so the caller can restore them.
    def _muted_alert_ids(self, rule_id: str) -> tuple[str, ...]:
        body = self._client.alerting.rule.get(id=rule_id).body
        return tuple(str(a) for a in body.get("muted_alert_ids") or [])

    @_translate_errors
    def mute_alert_rule(self, rule_id: str) -> tuple[str, ...]:
        with _known_id("alert rule", rule_id):
            cleared = self._muted_alert_ids(rule_id)
            self._client.alerting.rule.mute_all(id=rule_id)
        return cleared

    @_translate_errors
    def unmute_alert_rule(self, rule_id: str) -> tuple[str, ...]:
        with _known_id("alert rule", rule_id):
            cleared = self._muted_alert_ids(rule_id)
            self._client.alerting.rule.unmute_all(id=rule_id)
        return cleared

    @_translate_errors
    def mute_alert(self, rule_id: str, alert_id: str) -> None:
        _blank("alert_id", alert_id, _ALERT_ID_FIX)
        with _known_id("alert rule", rule_id):
            # On a rule muted as a whole Kibana answers 204 but records no
            # per-alert mute, and unmuting the rule clears every per-alert mute
            # (probed on 9.5.4), so this mute would vanish unseen: refuse it.
            if self._client.alerting.rule.get(id=rule_id).body.get("mute_all"):
                raise KibanaRejected(
                    f"alert rule '{rule_id}' is muted as a whole (mute_alert_rule), and Kibana "
                    "records no per-alert mute on a muted rule — its alerts are already "
                    "silent; to keep only this alert muted, call unmute_alert_rule first "
                    "(it clears every per-alert mute), then mute_alert"
                )
            # validate_alerts_existence is left to Kibana's default (true): an alert
            # id the rule has never raised is a 404, not a silent mute of a typo.
            try:
                self._client.alerting.rule.mute_alert(rule_id=rule_id, alert_id=alert_id)
            except SpaceNotFoundError:
                raise
            except NotFoundError as e:
                if _MISSING_ALERT_MARK in str(e.message):
                    raise _alert_not_found(rule_id, alert_id) from e
                raise

    @_translate_errors
    def unmute_alert(self, rule_id: str, alert_id: str) -> None:
        # Kibana answers 204 for an alert that is not muted, or never existed
        # (probed): unmuting is idempotent, so there is no alert-level 404 here.
        _blank("alert_id", alert_id, _ALERT_ID_FIX)
        with _known_id("alert rule", rule_id):
            self._client.alerting.rule.unmute_alert(rule_id=rule_id, alert_id=alert_id)

    @_translate_errors
    def snooze_alert_rule(
        self, rule_id: str, duration: str, start: datetime, recurring: dict[str, Any] | None
    ) -> RuleSnooze:
        custom: dict[str, Any] = {"duration": duration, "start": _kibana_instant(start)}
        if recurring is not None:
            custom["recurring"] = {
                k: _kibana_instant(v) if isinstance(v, datetime) else v
                for k, v in recurring.items()
            }
        with _known_id("alert rule", rule_id):
            try:
                body = self._client.alerting.rule.snooze(
                    id=rule_id, schedule={"custom": custom}
                ).body
            except BadRequestError as e:
                if _SNOOZE_LIMIT_MARK in str(e.message):
                    raise KibanaRejected(
                        f"alert rule '{rule_id}' already has 5 snooze schedules, Kibana's limit "
                        "(expired ones count) — remove one with unsnooze_alert_rule and a "
                        "schedule_id that snooze_alert_rule returned (Kibana's public API does "
                        "not list a rule's snoozes); if those ids are lost, cancel the snoozes "
                        "on the rule in Kibana's Rules page"
                    ) from e
                raise
        return _to_rule_snooze(rule_id, body or {})

    @_translate_errors
    def unsnooze_alert_rule(self, rule_id: str, schedule_id: str) -> None:
        _blank("schedule_id", schedule_id, "pass the schedule_id that snooze_alert_rule returned")
        with _known_id("alert rule", rule_id):
            try:
                self._client.alerting.rule.unsnooze(rule_id=rule_id, schedule_id=schedule_id)
            except SpaceNotFoundError:
                raise
            except (NotFoundError, BadRequestError) as e:
                # 404 "Rule has no snooze schedule with id <id>." when the rule has
                # others, 400 "Rule has no snooze schedules." when it has none.
                if any(m in str(e.message) for m in _MISSING_SNOOZE_MARKS):
                    raise _snooze_not_found(rule_id, schedule_id) from e
                raise

    @_translate_errors
    def update_alert_rule_api_key(self, rule_id: str) -> None:
        # Kibana regenerates the rule's API key from the caller's credentials:
        # the rule then runs with this server's API key's privileges. 204, no body.
        with _known_id("alert rule", rule_id):
            self._client.alerting.rule.update_api_key(id=rule_id)

    @_translate_errors
    def delete_alert_rule(self, rule_id: str) -> None:
        with _known_id("alert rule", rule_id):
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
    def get_connector(self, connector_id: str) -> ConnectorDetail:
        with _known_id("connector", connector_id):
            return _to_connector_detail(self._client.connectors.get(id=connector_id).body)

    @_translate_errors
    def update_connector(
        self, connector_id: str, name: str | None, config: dict[str, Any] | None,
        secrets: dict[str, Any] | None,
    ) -> ConnectorDetail:
        # PUT /api/actions/connector/{id} is a full replace: `name` is required,
        # an omitted `config` is reset to {} (a .index connector then fails
        # validation, probed on 9.5.4) and an omitted `secrets` is reset to {}.
        # Config is read-modify-write; secrets cannot be — Kibana never returns
        # them — so a type that may hold secrets needs them passed explicitly.
        with _known_id("connector", connector_id):
            cur = self._client.connectors.get(id=connector_id).body
        if cur.get("is_preconfigured"):
            raise KibanaRejected(
                f"connector '{connector_id}' is preconfigured (defined in kibana.yml) and "
                "cannot be edited through the API — change it in kibana.yml, or create a "
                "new one with create_connector and point your rules' actions at it"
            )
        type_id = cur.get("connector_type_id", "")
        if secrets is None and type_id not in _SECRETLESS_CONNECTOR_TYPES:
            raise KibanaRejected(
                f"connector '{connector_id}' is a {type_id} connector, whose secrets Kibana "
                "never returns and resets to empty on any update that omits them — pass "
                "`secrets` with its full secret values (secrets={} only if it has none)"
            )
        current_config = dict(cur.get("config") or {})
        kwargs: dict[str, Any] = {
            "id": connector_id,
            "name": name if name is not None else cur.get("name", ""),
            # config merges key-by-key: the caller passes only what changes.
            "config": {**current_config, **config} if config is not None else current_config,
        }
        if secrets is not None:
            kwargs["secrets"] = secrets
        with _known_id("connector", connector_id):
            return _to_connector_detail(self._client.connectors.update(**kwargs).body)

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

    def _library_title(self, visualization_id: str) -> str:
        # A by-reference panel stores only {"ref_id": ...} (plus an optional
        # title override); its title lives on the library item. Kibana keeps the
        # ref after the item is deleted (probed on 9.5.4), so a miss reads "".
        try:
            body = self._client.visualizations.get(id=visualization_id).body
        except NotFoundError:
            return ""
        return str((body.get("data") or {}).get("title", ""))

    def _panel_summary(self, index: int, panel: dict[str, Any]) -> PanelSummary:
        config = panel.get("config") or {}
        ref = config.get("ref_id")
        if not ref:
            return PanelSummary(
                index=index, type=panel.get("type", "unknown"), title=config.get("title", "")
            )
        return PanelSummary(
            index=index,
            type=panel.get("type", "unknown"),
            title=config.get("title") or self._library_title(ref),
            visualization_id=ref,
        )

    @_translate_errors
    def get_dashboard(self, dashboard_id: str) -> DashboardDetail:
        with _known_id("dashboard", dashboard_id):
            body = self._client.dashboards.get(id=dashboard_id).body
        data = body["data"]
        panels = tuple(
            self._panel_summary(i, p) for i, p in enumerate(data.get("panels", []))
        )
        return DashboardDetail(
            id=body["id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            panels=panels,
        )

    @_translate_errors
    def get_dashboard_data(self, dashboard_id: str) -> tuple[dict[str, Any], list[str]]:
        with _known_id("dashboard", dashboard_id):
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
        with _known_id("dashboard", dashboard_id):
            self._client.dashboards.update(id=dashboard_id, **kwargs)

    @_translate_errors
    def upsert_dashboard(self, dashboard_id: str, data: dict[str, Any]) -> str:
        # PUT /api/dashboards/{id} is an upsert (create-or-replace) on a
        # client-chosen id. Same _UPDATABLE_KEYS filter as update_dashboard.
        kwargs = {k: v for k, v in data.items() if k in _UPDATABLE_KEYS}
        self._client.dashboards.update(id=dashboard_id, **kwargs)
        return dashboard_id

    @_translate_errors
    def get_tag_names(self, tag_ids: list[str]) -> dict[str, str]:
        # No public tags API in kibana-py: tags are `tag` saved objects, read
        # with the saved-objects bulk get on this gateway's (space-scoped)
        # client. A miss is a per-object 404 inside a 200 (probed on 9.5.4:
        # "Saved object [tag/nope] not found"; a default-space tag read from
        # another space is a 404 too, tags live in one space).
        if not tag_ids:
            return {}
        body = self._client.saved_objects.bulk_get(
            objects=[{"type": "tag", "id": tag_id} for tag_id in tag_ids]
        ).body
        return {
            str(o["id"]): str((o.get("attributes") or {}).get("name", ""))
            for o in body.get("saved_objects", [])
            if not o.get("error") and o.get("type") == "tag"
        }

    @_translate_errors
    def create_visualization(self, config: dict[str, Any]) -> str:
        visualization_id: str = self._client.visualizations.create(data=config).body["id"]
        return visualization_id

    @_translate_errors
    def search_visualizations(self, query: str | None) -> list[VisualizationSummary]:
        # Same walk as search_dashboards. `query` matches title words
        # case-insensitively (probed on 9.5.4). kibana-py's `fields` and
        # `search_fields` parameters 500 on live Kibana, so whole items come back.
        base: dict[str, Any] = {"per_page": 100}
        if query:
            base["query"] = query
        found: list[VisualizationSummary] = []
        page = 1
        while True:
            body = self._client.visualizations.get_all(**base, page=page).body
            batch = body.get("data", [])
            found.extend(
                VisualizationSummary(
                    id=item["id"],
                    title=item["data"].get("title", ""),
                    description=item["data"].get("description", ""),
                    chart_type=lens_chart_type(item["data"]),
                )
                for item in batch
            )
            total = (body.get("meta") or {}).get("total")
            if not batch or (isinstance(total, int) and len(found) >= total):
                break
            page += 1
        return found

    @_translate_errors
    def get_visualization(self, visualization_id: str) -> VisualizationDetail:
        with _known_id("visualization", visualization_id):
            body = self._client.visualizations.get(id=visualization_id).body
        data = body["data"]
        described = describe_lens_config(data)
        return VisualizationDetail(
            id=body["id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            chart_type=described["chart_type"],
            data_view=described["data_view"],
            time_field=described["time_field"],
            metrics=tuple(described["metrics"]),
            group_by=tuple(described["group_by"]),
            query=described["query"],
        )

    @_translate_errors
    def update_visualization(self, visualization_id: str, config: dict[str, Any]) -> None:
        # PUT /api/visualizations/{id} is an upsert: an unknown id would CREATE
        # a visualization under it (kibana-py documents the 201). Reading first
        # turns a wrong id into guidance instead of a stray library item.
        with _known_id("visualization", visualization_id):
            self._client.visualizations.get(id=visualization_id)
            self._client.visualizations.update(id=visualization_id, data=config)

    @_translate_errors
    def delete_dashboard(self, dashboard_id: str) -> None:
        with _known_id("dashboard", dashboard_id):
            self._client.dashboards.delete(id=dashboard_id)

    @_translate_errors
    def delete_visualization(self, visualization_id: str) -> None:
        # Kibana deletes a visualization that dashboards still reference (204,
        # probed on 9.5.4); their panels keep a dangling ref_id.
        with _known_id("visualization", visualization_id):
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
        # D17 guidance, shared with the write tier (update_synthetic_monitor
        # reads the monitor first to learn its type).
        with _known_id("synthetic monitor", monitor_id):
            body = self._client.synthetics.get_monitor(id=monitor_id).body
        return _to_synthetic_monitor(body)

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

    # --- observability: the Synthetics write tier (D46) ---

    @_translate_errors
    def create_synthetic_monitor(
        self, *, type: str, name: str, private_locations: list[str], url: str | None,
        host: str | None, inline_script: str | None, schedule: str | None, enabled: bool,
        tags: list[str] | None,
    ) -> SyntheticMonitor:
        # Only private locations: a self-managed stack has no Elastic-managed
        # ones ("No Elastic managed location available to use", 9.5.4).
        body = self._client.synthetics.create_monitor(**_given(
            type=type, name=name, private_locations=private_locations, url=url, host=host,
            inline_script=inline_script,
            schedule=_monitor_schedule(schedule) if schedule is not None else None,
            enabled=enabled, tags=tags,
        )).body
        return _to_synthetic_monitor(body)

    @_translate_errors
    def update_synthetic_monitor(
        self, monitor_id: str, *, name: str | None = None, url: str | None = None,
        host: str | None = None, inline_script: str | None = None, schedule: str | None = None,
        enabled: bool | None = None, tags: list[str] | None = None,
        private_locations: list[str] | None = None,
    ) -> SyntheticMonitor:
        # The PUT is partial: omitted fields keep their values (probed on 9.5.4),
        # so only the given ones are sent and nothing is read back first.
        with _known_id("synthetic monitor", monitor_id):
            body = self._client.synthetics.update_monitor(**_given(
                id=monitor_id, name=name, url=url, host=host, inline_script=inline_script,
                schedule=_monitor_schedule(schedule) if schedule is not None else None,
                enabled=enabled, tags=tags, private_locations=private_locations,
            )).body
        return _to_synthetic_monitor(body)

    @_translate_errors
    def test_synthetic_monitor(self, monitor_id: str) -> str:
        with _known_id("synthetic monitor", monitor_id):
            body = self._client.synthetics.test_monitor(monitor_id=monitor_id).body
        return str(body.get("testRunId", ""))

    @_translate_errors
    def delete_synthetic_monitor(self, monitor_id: str) -> None:
        with _known_id("synthetic monitor", monitor_id):
            body = self._client.synthetics.delete_monitor(id=monitor_id).body
        _deleted(body, "synthetic monitor", monitor_id)

    @_translate_errors
    def create_synthetic_param(
        self, key: str, value: str, description: str | None, tags: list[str] | None
    ) -> SyntheticParam:
        with _secret_withheld(value):
            body = self._client.synthetics.create_param(**_given(
                key=key, value=value, description=description, tags=tags
            )).body
        return _to_synthetic_param(body)

    @_translate_errors
    def update_synthetic_param(
        self, param_id: str, *, key: str | None = None, value: str | None = None,
        description: str | None = None, tags: list[str] | None = None,
    ) -> SyntheticParam:
        # Kibana's response echoes the PREVIOUS value (probed on 9.5.4); the
        # SyntheticParam shape has no value field, so neither value surfaces.
        with _known_id("synthetic param", param_id), _secret_withheld(value):
            body = self._client.synthetics.update_param(**_given(
                id=param_id, key=key, value=value, description=description, tags=tags
            )).body
        return _to_synthetic_param(body)

    @_translate_errors
    def delete_synthetic_param(self, param_id: str) -> None:
        with _known_id("synthetic param", param_id):
            body = self._client.synthetics.delete_param(id=param_id).body
        _deleted(body, "synthetic param", param_id)

    @_translate_errors
    def create_synthetic_private_location(
        self, label: str, agent_policy_id: str, tags: list[str] | None
    ) -> SyntheticPrivateLocation:
        try:
            body = self._client.synthetics.create_private_location(**_given(
                label=label, agent_policy_id=agent_policy_id, tags=tags
            )).body
        except BadRequestError as e:
            # A missing policy, or one another location uses (probed on 9.5.4).
            raise KibanaRejected(
                "Kibana rejected the payload", detail=f"{e.message} — {_AGENT_POLICY_HINT}"
            ) from e
        return _to_private_location(body)

    @_translate_errors
    def update_synthetic_private_location(
        self, location_id: str, label: str
    ) -> SyntheticPrivateLocation:
        # The label is the only editable attribute (kibana-py, Kibana's schema).
        with _known_id("private location", location_id):
            body = self._client.synthetics.update_private_location(
                id=location_id, label=label
            ).body
        return _to_private_location(body)

    @_translate_errors
    def delete_synthetic_private_location(self, location_id: str) -> None:
        # The agent policy behind it stays (it belongs to Fleet).
        with _known_id("private location", location_id):
            try:
                self._client.synthetics.delete_private_location(id=location_id)
            except BadRequestError as e:
                # 9.5.4 answers a missing id with a 400 "... does not exist." and
                # a location monitors still use with a 400 "... used by N monitor(s).".
                if "does not exist" in str(e.message):
                    raise _unknown_id("private location", location_id) from e
                if "used by" in str(e.message):
                    raise KibanaRejected(
                        "Kibana refused to delete the private location",
                        detail=f"{e.message} — {_LOCATION_IN_USE_HINT}",
                    ) from e
                raise

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
        # /view matches exactly (an omitted name or environment means "all", not
        # "any") and 404s when nothing matches; the miss names the list tool.
        with _known_apm_config(service_name, environment):
            body = self._client.apm.get_agent_configuration(
                **_given(name=service_name, environment=environment)
            ).body
        return _to_apm_agent_config(body)

    @_translate_errors
    def upsert_apm_agent_config(
        self,
        service_name: str | None,
        environment: str | None,
        settings: dict[str, str],
        *,
        agent_name: str | None,
        overwrite: bool,
    ) -> ApmAgentConfig:
        # The PUT answers {} and replaces the whole settings map; without
        # ?overwrite=true an existing config is a 400 (probed on 9.5.4), so the
        # caller overwrites only a config it has read.
        try:
            self._client.apm.create_or_update_agent_configuration(**_given(
                service_name=service_name, service_environment=environment,
                settings=settings, agent_name=agent_name, overwrite=True if overwrite else None,
            ))
        except BadRequestError as e:
            if "already exists" in str(e.message):
                raise KibanaRejected(
                    "Kibana refused to replace the APM agent configuration",
                    detail=(
                        f"a configuration for {_apm_scope(service_name, environment)} was "
                        "created after this call read none — call upsert_apm_agent_config "
                        "again: it then replaces that one and returns its settings as "
                        "previous_settings"
                    ),
                ) from e
            # Kibana checks the values of the settings it knows, and its message
            # can omit which one failed ("Must be a number between 0.0000 and 1").
            raise KibanaRejected(
                "Kibana rejected the payload",
                detail=f"{e.message} — settings sent: {settings}; {_APM_SETTINGS_HINT}",
            ) from e
        return self.get_apm_agent_config(service_name, environment)

    @_translate_errors
    def delete_apm_agent_config(self, service_name: str | None, environment: str | None) -> None:
        with _known_apm_config(service_name, environment):
            self._client.apm.delete_agent_configuration(
                **_given(service_name=service_name, service_environment=environment)
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

    def _read_exception_list(
        self, id: str | None, list_id: str | None, namespace_type: str
    ) -> dict[str, Any]:
        # exception_lists.get tolerates both; it only raises on neither.
        id = id or None
        list_id = list_id or None
        if id is None and list_id is None:
            raise KibanaRejected("provide an id or list_id to identify the exception list")
        try:
            body: dict[str, Any] = self._client.exception_lists.get(
                id=id, list_id=list_id, namespace_type=namespace_type
            ).body
        except SpaceNotFoundError:
            raise
        except NotFoundError as e:
            raise _unknown_exception_list(id, list_id, namespace_type) from e
        return body

    @_translate_errors
    def get_exception_list(
        self, id: str | None, list_id: str | None, namespace_type: str = "single"
    ) -> ExceptionList:
        return _to_exception_list(self._read_exception_list(id, list_id, namespace_type))

    @_translate_errors
    def find_exception_items(
        self, list_id: str, namespace_type: str = "single"
    ) -> list[ExceptionItem]:
        # list_id required; a bogus one (or one in the other namespace) 404s ->
        # the same not-found text get_exception_list gives.
        items: list[ExceptionItem] = []
        page = 1
        while True:
            try:
                body = self._client.exception_lists.find_items(
                    list_id=list_id, namespace_type=namespace_type, page=page, per_page=100
                ).body
            except SpaceNotFoundError:
                raise
            except NotFoundError as e:
                raise _unknown_exception_list(
                    None, list_id, namespace_type, list_id_only=True) from e
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

    # --- exception-list edit, copy, export and import (D50) ---

    @_translate_errors
    def update_exception_list(
        self, id: str | None, list_id: str | None, namespace_type: str,
        name: str | None, description: str | None, tags: list[str] | None,
    ) -> ExceptionListChange:
        # PUT /api/exception_lists requires name, description and type (a PUT
        # without them 400s) and keeps omitted tags; tags=[] clears them. It
        # ignores os_types (a PUT of ["windows"] left ["linux"], probed), so
        # they are never sent. The read's `_version` makes a change made in
        # between a 409 version_conflict instead of a silent overwrite.
        raw = self._read_exception_list(id, list_id, namespace_type)
        try:
            body = self._client.exception_lists.update(
                id=raw.get("id"),
                name=name if name is not None else raw.get("name", ""),
                description=description if description is not None else raw.get("description", ""),
                type=raw.get("type", "detection"),
                namespace_type=namespace_type,
                tags=list(tags) if tags is not None else None,
                _version=raw.get("_version"),
            ).body
        except ConflictError as e:
            raise KibanaRejected(
                f"exception list '{raw.get('list_id', list_id or id)}' changed after "
                "update_exception_list read it (Kibana answered 409, version conflict); "
                "nothing was written — call update_exception_list again"
            ) from e
        return ExceptionListChange(list=_to_exception_list(body), previous=_to_exception_list(raw))

    @_translate_errors
    def duplicate_exception_list(
        self, list_id: str, namespace_type: str, include_expired_exceptions: bool
    ) -> ExceptionList:
        try:
            body = self._client.exception_lists.duplicate(
                list_id=list_id, namespace_type=namespace_type,
                include_expired_exceptions=include_expired_exceptions,
            ).body
        except SpaceNotFoundError:
            raise
        except NotFoundError as e:
            raise _unknown_exception_list(
                None, list_id, namespace_type, list_id_only=True) from e
        except ApiError as e:
            # 405 "unable to duplicate exception list with list_id: ... - action
            # not allowed" for an endpoint list (probed); unmapped it would read
            # as an outage.
            if getattr(e, "status_code", None) != 405:
                raise
            raise KibanaRejected(
                f"Kibana refuses to duplicate exception list '{list_id}' (405 action not "
                "allowed: it refuses endpoint lists) — export_exception_list then "
                "import_exception_list makes a copy"
            ) from e
        return _to_exception_list(body)

    @_translate_errors
    def export_exception_list(
        self, id: str, list_id: str, namespace_type: str, include_expired_exceptions: bool
    ) -> list[dict[str, Any]]:
        body = self._client.exception_lists.export(
            id=id, list_id=list_id, namespace_type=namespace_type,
            include_expired_exceptions=include_expired_exceptions,
        ).body
        if isinstance(body, (bytes, str)):  # kibana-py parses application/ndjson; be defensive
            text = body.decode("utf-8") if isinstance(body, bytes) else body
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        return list(body)

    @_translate_errors
    def import_exception_list(
        self, content: bytes, overwrite: bool
    ) -> ExceptionListImportResult:
        # Never `as_new_list`: it keeps the source item_ids (D50, probed); a
        # new copy is rewritten in core.exception_lists and imported plainly.
        body = self._client.exception_lists.import_lists(
            file=content, overwrite=True if overwrite else None,
        ).body
        return _to_exception_import(body)

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
        except ApiError as e:
            # A key without the lists privilege gets a bare 500 here (9.5.4,
            # probed with a `read` key); create_index answers it with the 403
            # naming the privilege, so try it rather than mask the error.
            if e.meta.status != 500:
                raise
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

    @_translate_errors
    def find_saved_objects(
        self,
        types: list[str],
        search: str | None,
        has_reference: dict[str, str] | None,
        page: int,
        per_page: int,
    ) -> SavedObjectPage:
        # GET /api/saved_objects/_find (deprecated, still served by 9.5.4 with a
        # `warning: 299 ... This endpoint is deprecated` header). `fields` keeps
        # each hit's attributes to its title and name: a dashboard's are ~40 KB.
        kwargs: dict[str, Any] = {
            "type": list(types), "fields": ["title", "name"], "page": page, "per_page": per_page,
        }
        if search is not None:
            # Without search_fields Kibana matches the words against every
            # attribute (probed: "JetBeats", only in the flights dashboard's
            # description, found it), so it would also answer for words in
            # attributes this tool never returns; title and name are the ones
            # a hit carries.
            kwargs["search"] = search
            kwargs["search_fields"] = ["title", "name"]
        if has_reference is not None:
            kwargs["has_reference"] = dict(has_reference)
        body = self._client.saved_objects.find(**kwargs).body
        return SavedObjectPage(
            total=int(body.get("total", 0)),
            page=int(body.get("page", page)),
            per_page=int(body.get("per_page", per_page)),
            saved_objects=tuple(_to_saved_object_summary(o) for o in body.get("saved_objects", [])),
        )

    @_translate_errors
    def get_saved_object(self, type: str, id: str) -> SavedObjectDetail:
        if not type.strip() or not id.strip():
            raise KibanaRejected(
                "type and id must not be blank — pass the ones find_saved_objects returns"
            )
        if type not in READABLE_TYPES:
            raise KibanaRejected(unreadable_type_message(type))
        try:
            body = self._client.saved_objects.get(type=type, id=id).body
        except SpaceNotFoundError:
            raise
        except NotFoundError as e:
            raise _saved_object_miss(type, id) from e
        return _to_saved_object_detail(body)

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

    # --- platform-admin: saved objects between spaces (D49) ---

    def _require_spaces(self, spaces: list[str]) -> None:
        """D5 for every space written into: Kibana's copy and share routes
        write into a space id that names no space without an error (probed)."""
        wanted = [s for s in dict.fromkeys(spaces) if s != ALL_SPACES]
        if not wanted:
            return
        # get_all lists only the spaces this key can view: a miss is either no
        # such space (get answers 404) or one the key has no privilege in
        # (403, also for an id no space has when the key cannot read it).
        known = {str(s.get("id")) for s in self._client.spaces.get_all().body}
        hidden, absent = [], []
        for s in (s for s in wanted if s not in known):
            try:
                self._client.spaces.get(id=s)
            except NotFoundError:
                absent.append(s)
            except AuthorizationException:
                hidden.append(s)
        if hidden:
            raise KibanaAuthError(unreadable_spaces_message(hidden, absent))
        if absent:
            raise KibanaSpaceNotFound(missing_spaces_message(absent))

    def _source_spaces(self) -> SpacesClient:
        """The spaces routes on the source space. Unscoped, the base path is
        the source: the default space, or the space KIBANA_URL pins."""
        if isinstance(self._client, SpaceScopedKibana) and self._space is not None:
            # SpaceScopedKibana keeps its root client in `_client` (no public
            # accessor); the contract tier pins the routing this relies on.
            return _SourceSpaceSpaces(self._client._client, self._space)
        return self._client.spaces

    def _object_spaces(self, objects: list[dict[str, str]]) -> list[ObjectSpaces]:
        # `_get_shareable_references` may also list related objects: keep the
        # requested ones, in request order.
        body = self._source_spaces().get_shareable_references(objects=objects).body
        found = {(o.get("type"), o.get("id")): o for o in body.get("objects") or ()}
        return [
            _to_object_spaces(found.get((o["type"], o["id"])) or {**o, "isMissing": True})
            for o in objects
        ]

    @_translate_errors
    def copy_saved_objects_to_spaces(
        self, objects: list[dict[str, str]], spaces: list[str], include_references: bool,
        overwrite: bool,
    ) -> list[SpaceCopyResult]:
        source = self._space or self._pinned_space or "default"
        if source in spaces:
            raise KibanaRejected(self_target_message(source, self._source_fixed))
        self._require_spaces(spaces)
        # create_new_copies (Kibana's default) mints new ids and never touches an
        # existing object; overwrite replaces the object an earlier copy made
        # (matched by origin). Kibana 400s the two together.
        mode: dict[str, Any] = (
            {"create_new_copies": False, "overwrite": True} if overwrite
            else {"create_new_copies": True}
        )
        try:
            body = self._source_spaces().copy_saved_objects(
                spaces=spaces, objects=objects, include_references=include_references, **mode,
            ).body
        except NotFoundError as e:
            # 404 "Saved objects not found" + attributes.objects: the misses.
            attributes = (e.body or {}).get("attributes") if isinstance(e.body, dict) else None
            misses = [
                f"{o.get('type')}/{o.get('id')}"
                for o in (attributes or {}).get("objects") or ()
            ] or [f"{o['type']}/{o['id']}" for o in objects]
            raise KibanaNotFound(objects_not_found_message(misses, self._source_fixed)) from e
        return [
            _to_space_copy_result(space, body.get(space) or {}, objects, overwrite)
            for space in spaces
        ]

    @_translate_errors
    def share_saved_objects(
        self, objects: list[dict[str, str]], spaces: list[str]
    ) -> list[SharingChange]:
        self._require_spaces(spaces)
        before = self._object_spaces(objects)
        refusal = sharing_refusal(before, self._source_fixed)
        if refusal:
            raise KibanaRejected(refusal)
        body = self._source_spaces().update_objects_spaces(
            objects=objects, spaces_to_add=spaces, spaces_to_remove=[]).body
        return _to_sharing_changes(before, body)

    @_translate_errors
    def unshare_saved_objects(
        self, objects: list[dict[str, str]], spaces: list[str], force: bool
    ) -> list[SharingChange]:
        # No existence check: removing a space adds nothing, and an object can
        # sit in a space id that names no space (written there before D5).
        before = self._object_spaces(objects)
        refusal = (sharing_refusal(before, self._source_fixed)
                   or unshare_refusal(before, spaces, force))
        if refusal:
            raise KibanaRejected(refusal)
        body = self._source_spaces().update_objects_spaces(
            objects=objects, spaces_to_add=[], spaces_to_remove=spaces).body
        return _to_sharing_changes(before, body)

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

    # --- fleet advanced ops: agent tags, diagnostics, package install/uninstall (D51) ---

    def _read_agent(self, agent_id: str) -> dict[str, Any]:
        try:
            item: dict[str, Any] = self._client.fleet_agents.get(agent_id=agent_id).body["item"]
        except NotFoundError as e:
            raise KibanaNotFound(unknown_agent_message(agent_id)) from e
        return item

    @_translate_errors
    def list_agent_tags(self) -> list[str]:
        # show_inactive like list_agents: the tags of every agent it lists.
        return list(self._client.fleet_agents.get_tags(show_inactive=True).body.get("items", []))

    @_translate_errors
    def update_agent_tags(
        self, *, agent_ids: list[str], tags_to_add: list[str], tags_to_remove: list[str]
    ) -> FleetAgentTagsResult:
        ids = list(dict.fromkeys(agent_ids))
        before: dict[str, tuple[str, ...]] = {}
        missing: list[str] = []
        for agent_id in ids:
            try:
                raw = self._client.fleet_agents.get(agent_id=agent_id).body["item"]
            except NotFoundError:
                missing.append(agent_id)
                continue
            before[agent_id] = tuple(raw.get("tags") or ())
        if missing:
            # Kibana answers 200 and skips an unknown id (probed): refuse first.
            raise KibanaNotFound(missing_agents_message(missing))
        kwargs: dict[str, Any] = {"agents": ids}
        if tags_to_add:
            kwargs["tags_to_add"] = tags_to_add
        if tags_to_remove:
            kwargs["tags_to_remove"] = tags_to_remove
        action_id: str = self._client.fleet_agents.bulk_update_tags(**kwargs).body["actionId"]
        changes = tuple(
            FleetAgentTagChange(
                agent_id=agent_id, previous_tags=before[agent_id],
                tags=tuple(self._client.fleet_agents.get(
                    agent_id=agent_id).body["item"].get("tags") or ()),
            )
            for agent_id in ids
        )
        return FleetAgentTagsResult(
            action_id=action_id, agents=changes,
            not_applied_agent_ids=tags_not_applied(changes, tags_to_add, tags_to_remove),
        )

    @_translate_errors
    def request_agent_diagnostics(
        self, *, agent_id: str, include_cpu_metrics: bool = False
    ) -> FleetDiagnosticsRequest:
        agent = self._read_agent(agent_id)
        kwargs: dict[str, Any] = {"agent_id": agent_id}
        if include_cpu_metrics:
            kwargs["additional_metrics"] = ["CPU"]  # the only value Kibana accepts (probed)
        action_id: str = self._client.fleet_agents.request_diagnostics(**kwargs).body["actionId"]
        return FleetDiagnosticsRequest(
            action_id=action_id, agent_id=agent_id, agent_status=agent.get("status", ""))

    @_translate_errors
    def list_agent_diagnostics(self, agent_id: str) -> list[FleetAgentDiagnostics]:
        try:
            body = self._client.fleet_agents.get_uploads(agent_id=agent_id).body
        except NotFoundError as e:
            raise KibanaNotFound(unknown_agent_message(agent_id)) from e
        return [_to_fleet_agent_diagnostics(u) for u in body.get("items", [])]

    def _read_package(self, name: str) -> dict[str, Any]:
        try:
            item: dict[str, Any] = (
                self._client.fleet_epm.get_package(pkg_name=name).body.get("item") or {})
        except NotFoundError as e:
            raise KibanaNotFound(unknown_package_message(name)) from e
        if not item:
            raise KibanaNotFound(unknown_package_message(name))
        return item

    @_translate_errors
    def install_package(self, *, name: str, version: str | None = None) -> FleetPackageInstall:
        item = self._read_package(name)
        latest = str(item.get("latestVersion") or item.get("version") or "")
        if item.get("status") == "installed":
            # item.version is the installed version once installed (probed).
            installed = str(item.get("version", ""))
            if version is None or version == installed:
                return FleetPackageInstall(
                    name=name, version=installed, status="installed", already_installed=True)
            raise KibanaRejected(installed_version_refusal(name, installed, version))
        kwargs: dict[str, Any] = {"pkg_name": name}
        if version is not None:
            kwargs["pkg_version"] = version
        try:
            self._client.fleet_epm.install_package(**kwargs)
        except NotFoundError as e:
            # Only a version can be unknown here: the read found the name.
            raise KibanaNotFound(unknown_version_message(name, version or "", latest)) from e
        except BadRequestError as e:
            if "out-of-date" not in str(e.message):
                raise
            raise KibanaRejected(outdated_version_message(name, version or "", latest)) from e
        after = self._read_package(name)
        return FleetPackageInstall(
            name=name, version=str(after.get("version", "")),
            status=str(after.get("status", "")), already_installed=False)

    @_translate_errors
    def uninstall_package(self, *, name: str, force: bool = False) -> FleetPackageUninstall:
        item = self._read_package(name)
        if item.get("status") != "installed":
            raise KibanaNotFound(not_installed_message(name))
        # With space awareness on, Kibana's uninstall deletes every space's package
        # policies, while spaces.get_all leaves out each space the key cannot read
        # (probed with a key restricted to 'default'): nothing listed is provably
        # complete, so refuse before reading any policy, whatever force says.
        settings = self._client.fleet.get_settings().body.get("item") or {}
        if settings.get("use_space_awareness_migration_status"):
            raise KibanaRejected(space_awareness_refusal(name))
        policies = self._client.fleet_policies

        def every(list_page: Callable[..., Any]) -> list[dict[str, Any]]:
            return _fleet_all_items(lambda p: list_page(page=p, per_page=100).body)

        if name == AGENT_MONITORING_PACKAGE:
            # Kibana uninstalls it under a monitoring agent policy unchecked (probed).
            monitoring = [ap["id"] for ap in every(policies.get_agent_policies)
                          if ap.get("monitoring_enabled")]
            if monitoring:
                raise KibanaRejected(monitoring_refusal(name, monitoring))
        package_policies = [pp for pp in every(policies.get_package_policies)
                            if (pp.get("package") or {}).get("name") == name]
        listed: list[tuple[str, str, str, int]] = []
        guarded: list[tuple[str, str, str]] = []
        agent_policies: dict[str, dict[str, Any]] = {}
        for pp in package_policies:
            ap_ids = [i for i in (pp.get("policy_ids") or [pp.get("policy_id")]) if i]
            if not ap_ids:
                unattached = unattached_guard_reason(name)
                if unattached is not None:
                    guarded.append((pp["id"], "", unattached))
                listed.append((pp["id"], pp.get("name", ""), "", 0))
            for ap_id in ap_ids:
                if ap_id not in agent_policies:
                    agent_policies[ap_id] = policies.get_agent_policy(
                        agent_policy_id=ap_id).body["item"]
                ap = agent_policies[ap_id]
                why = guard_reason(name, is_managed=bool(ap.get("is_managed")),
                                   is_default_fleet_server=bool(ap.get("is_default_fleet_server")))
                if why is not None:
                    guarded.append((pp["id"], ap_id, why))
                listed.append((pp["id"], pp.get("name", ""), ap_id, int(ap.get("agents") or 0)))
        if guarded:
            raise KibanaRejected(guarded_policy_refusal(name, guarded))
        if package_policies and not force:
            raise KibanaRejected(package_policies_refusal(name, listed))
        # force lets Kibana delete package policies agents use (400 without it);
        # it is sent only then, so it can never reach Kibana for anything else.
        self._client.fleet_epm.uninstall_package(
            pkg_name=name, force=(True if force and package_policies else None))
        return FleetPackageUninstall(
            name=name, version=str(item.get("version", "")),
            deleted_package_policies=tuple(
                _to_fleet_package_policy(pp) for pp in package_policies),
        )
