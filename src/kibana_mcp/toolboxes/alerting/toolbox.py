"""The alerting toolbox: alert rules + connectors.

Wraps kibana-py's `alerting.rule` + `connectors` namespaces (NOT the deprecated
`actions` alias). `maintenance_windows` is out of scope (Platinum-gated;
subscription-gated surfaces are not built here). Two safety choices: rules are created **disabled by default**
(inert until an explicit enable), and `execute_connector` is **destructive-tier**
because it fires the connector's real external action irreversibly.
"""

import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.toolboxes.base import SPACE_ID_PATTERN, ToolboxDeps, gateway_errors, with_space

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)
# A repeat of a mute, an unmute, an unsnooze or an API-key refresh leaves the
# rule as the first call did (Kibana answers 204 again, probed on 9.5.4).
_WRITE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
# execute_connector fires a real external action: NOT idempotent (repeats repeat
# the side effect) and open-world (reaches systems outside Kibana).
_EXECUTE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)

# Kibana's snooze durations: a whole number and one of s/m/h/d (probed on 9.5.4:
# '1w', '1M', '1y', '1.5h', '2H' and '-1' are 400s). 0 is refused here too.
_SNOOZE_DURATION_RE = re.compile(r"[1-9][0-9]*[smhd]")


def _snooze_duration(value: str) -> str:
    if not _SNOOZE_DURATION_RE.fullmatch(value):
        raise ToolError(
            f"duration '{value}' is not a snooze duration — pass a whole number and one unit, "
            "s, m, h or d (e.g. '30m', '2h', '1d'; '1.5h' is '90m'); Kibana takes no week, "
            "month or year unit here"
        )
    return value


def _instant(name: str, value: str, hint: str = "") -> datetime:
    """An ISO-8601 date or date-time as an aware UTC datetime (no offset: UTC).
    Kibana itself takes only YYYY-MM-DDTHH:mm:ss.sssZ; the adapter formats it."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(
            f"{name} '{value}' is not an ISO-8601 date — pass e.g. '2026-10-25T09:00:00Z' "
            f"(UTC) or '2026-10-25T11:00:00+02:00'{hint}"
        ) from None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


class AlertingToolbox:
    name = "alerting"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_lifecycle(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_alert_rules(
            search: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List alerting rules (optionally filtered by a name search). Returns
            concise summaries (id, name, rule_type_id, consumer, enabled, schedule,
            status, tags) — not the rule's params or actions (get_alert_rule
            returns those).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(r) for r in gw.list_alert_rules(search)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get one alerting rule by id: the summary fields list_alert_rules
            returns plus the rule's full `params` (as Kibana stores them,
            defaults included) and its `actions`, each {"id": <connector id>,
            "uuid", "group", "params", "frequency" (when set)} — the shapes
            update_alert_rule takes (pass `uuid` back to keep the action's
            identity). Read a rule here before update_alert_rule
            merges into its params, and again afterwards to confirm the change.
            `muted` is the rule-wide mute (mute_alert_rule / unmute_alert_rule) and
            `muted_alert_ids` the alerts muted one by one (mute_alert / unmute_alert).
            Snoozes are not shown: Kibana's public API does not expose them (keep
            the schedule_id snooze_alert_rule returns).
            Rules hold no secrets (connectors do; see get_connector).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_alert_rule(rule_id)), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_alerting_health(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Kibana alerting framework health: overall status plus whether a
            permanent encryption key is configured and the connection is secure
            (both required for rules to run reliably).

            The health report is instance-wide: `space` validates and routes the request but the data does not vary by space.
            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_alerting_health()), space)

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_rule_types(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List the alerting rule types this Kibana offers, to choose
            create_alert_rule's arguments: `id` is the `rule_type_id`,
            `consumers` are the values its `consumer` may take for you, and a
            type with `enabled_in_license` false cannot be created on this
            licence. Kibana's API does not describe each type's `params`; this
            list does not either.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(t) for t in gw.list_rule_types()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_connectors(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List configured action connectors (id, name, connector_type_id).

            Preconfigured connectors (defined in kibana.yml) are instance-global and appear in every space with the same id.
            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(c) for c in gw.list_connectors()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_connector(
            connector_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get one action connector by id: name, connector_type_id, its
            non-secret `config`, and flags (is_preconfigured, is_missing_secrets,
            is_deprecated, is_system_action, is_connector_type_deprecated).
            Secrets are never returned — Kibana does not expose them.

            Preconfigured connectors (defined in kibana.yml) are instance-global and resolve in every space.
            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.get_connector(connector_id)), space)

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_alert_rule(
            name: Annotated[str, Field(min_length=1)],
            rule_type_id: Annotated[str, Field(min_length=1)],
            consumer: str,
            params: dict[str, Any],
            schedule_interval: str = "1m",
            tags: list[str] | None = None,
            enabled: bool = False,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create an alerting rule. `rule_type_id` is e.g. '.es-query' or
            '.index-threshold'; `consumer` is usually 'stackAlerts'; `params` is
            the rule-type-specific config; `schedule_interval` e.g. '1m'.
            Created DISABLED by default — call enable_alert_rule to start it.

            Example `.es-query` params (note `esQuery` is a JSON *string*):
            {"searchType": "esQuery", "esQuery": "{\\"query\\":{\\"match_all\\":{}}}",
             "index": ["my-index"], "timeField": "@timestamp", "threshold": [0],
             "thresholdComparator": ">", "timeWindowSize": 5, "timeWindowUnit": "m",
             "size": 100}.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.create_alert_rule(
                    name, rule_type_id, consumer, schedule_interval, params, tags, enabled
                )), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)] | None = None,
            tags: list[str] | None = None,
            schedule_interval: Annotated[str, Field(min_length=1)] | None = None,
            params: dict[str, Any] | None = None,
            actions: list[dict[str, Any]] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Edit an existing alerting rule in place, by id. Only the fields
            you pass change; everything else is kept (read-modify-write).
            `params` is MERGED key-by-key into the rule's current params, so pass
            only the keys you change (e.g. {"threshold": [10]}); get_alert_rule
            shows the current params and actions before the edit, and confirms
            them after it (this tool returns the summary only). `tags` and
            `actions` replace the whole list (pass [] to clear). An action is
            {"id": <connector id>, "group": <action group, e.g. "query matched">,
             "params": {...type-specific, e.g. {"message": "..."}},
             "frequency": {"summary": false, "notify_when": "onActionGroupChange",
             "throttle": null}}.
            Actions that carry `frequency` replace any rule-level notify_when /
            throttle (Kibana does not allow both), so the rule's timing then
            comes from each action's frequency.
            `rule_type_id` and `consumer` cannot change. This does NOT enable or
            disable the rule — use enable_alert_rule / disable_alert_rule.

            `space` targets a Kibana space by id (default: the default space)."""
            if all(v is None for v in (name, tags, schedule_interval, params, actions)):
                raise ToolError(
                    "provide at least one field to update: name, tags, schedule_interval, "
                    "params or actions (to start or stop the rule use enable_alert_rule / "
                    "disable_alert_rule)"
                )
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(asdict(gw.update_alert_rule(
                    rule_id, name, tags, schedule_interval, params, actions
                )), space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def enable_alert_rule(
            rule_id: str,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Enable (start running) an alerting rule.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.enable_alert_rule(rule_id)
            return with_space({"id": rule_id, "enabled": True}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def disable_alert_rule(
            rule_id: str,
            untrack: bool = False,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Disable (stop running) an alerting rule. By default its active
            alerts stay active, as Kibana leaves them; `untrack=True` also marks
            them untracked, so they stop being reported as active (the alert
            documents are kept). Re-enabling the rule raises alerts afresh either way.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.disable_alert_rule(rule_id, untrack=untrack)
            result: dict[str, Any] = {"id": rule_id, "enabled": False}
            if untrack:
                result["untracked"] = True
            return with_space(result, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_connector(
            name: Annotated[str, Field(min_length=1)],
            connector_type_id: Annotated[str, Field(min_length=1)],
            config: dict[str, Any] | None = None,
            secrets: dict[str, Any] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Create an action connector. `connector_type_id` is e.g.
            '.server-log' (no config), '.index', '.slack', '.email', '.webhook'.
            `config`/`secrets` are type-specific.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(
                    asdict(gw.create_connector(name, connector_type_id, config, secrets)), space
                )

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_connector(
            connector_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)] | None = None,
            config: dict[str, Any] | None = None,
            secrets: dict[str, Any] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Edit an action connector in place, by id: rename it and/or change
            its `config`. `config` is MERGED key-by-key into the current config
            (pass only the keys you change; null clears an optional key). The
            connector type cannot change. `secrets` are write-only: Kibana never
            returns them and resets them on any update that omits them, so for
            a type that can hold secrets (every type except .index and
            .server-log) you must pass the full `secrets`, or the call is
            refused. Secrets are never echoed back. Preconfigured (kibana.yml)
            connectors cannot be edited. Returns the connector as get_connector does.

            `space` targets a Kibana space by id (default: the default space)."""
            if name is None and config is None and secrets is None:
                raise ToolError("provide at least one field to update: name, config or secrets")
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(
                    asdict(gw.update_connector(connector_id, name, config, secrets)), space
                )

    def _register_lifecycle(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        # Notifications and credentials of an existing rule: mute (no end),
        # snooze (a time window), and the rule's API key. Each is reversible
        # (unmute, unsnooze, a second key refresh) and deletes nothing: write tier.
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def mute_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Mute a whole alerting rule, by id: the rule keeps running and
            tracking its alerts, but none of its actions fire (no notifications)
            until unmute_alert_rule. A mute has no end time; for a time window use
            snooze_alert_rule. It does not enable or disable the rule. Kibana
            drops the rule's per-alert mutes (mute_alert) when it mutes the whole
            rule. get_alert_rule shows it as `muted`.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.mute_alert_rule(rule_id)
            return with_space({"id": rule_id, "muted": True}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def unmute_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Undo mute_alert_rule: the rule's actions fire again. Kibana also
            clears every per-alert mute (mute_alert) on this call, so afterwards
            no alert of the rule is muted; snoozes stay (unsnooze_alert_rule).

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.unmute_alert_rule(rule_id)
            return with_space({"id": rule_id, "muted": False}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def mute_alert(
            rule_id: Annotated[str, Field(min_length=1)],
            alert_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Mute one alert of a rule: that alert's actions stop firing while
            the rule's other alerts still notify; undo with unmute_alert.
            `alert_id` is the id of an alert the rule has already raised — for a
            rule grouped by a field, the group's value (e.g. a host name); for an
            ungrouped .es-query rule, 'query matched'. Kibana refuses an id the
            rule has not raised, and this tool refuses a rule muted as a whole
            (Kibana would record nothing). get_alert_rule lists muted alerts in
            `muted_alert_ids`. To silence the whole rule use mute_alert_rule.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.mute_alert(rule_id, alert_id)
            return with_space({"rule_id": rule_id, "alert_id": alert_id, "muted": True}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def unmute_alert(
            rule_id: Annotated[str, Field(min_length=1)],
            alert_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Undo mute_alert for one alert of a rule: its actions fire again.
            `alert_id` is one of the ids get_alert_rule lists in
            `muted_alert_ids`. Unmuting an alert that is not muted succeeds and
            changes nothing.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.unmute_alert(rule_id, alert_id)
            return with_space({"rule_id": rule_id, "alert_id": alert_id, "muted": False}, space)

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def snooze_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            duration: str,
            start: str | None = None,
            recurring: dict[str, Any] | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Snooze an alerting rule's notifications for a time window: the rule
            keeps running, but its actions do not fire from `start` for
            `duration`. `duration` is a whole number and s, m, h or d ('30m',
            '2h', '1d'). `start` is an ISO-8601 date-time (no offset means UTC;
            default: now). `recurring` repeats the window, e.g.
            {"every": "1w", "onWeekDay": ["SA", "SU"], "end": "2026-12-31T00:00:00Z"}
            — `every` is a number and h, d, w, M or y and must be longer than
            `duration`; `end` (ISO-8601) or `occurrences` (a count) bounds it;
            `onWeekDay` (MO..SU), `onMonthDay` and `onMonth` (1-12) narrow it.
            Returns the snooze with its `schedule_id`: keep it, because
            unsnooze_alert_rule takes it and Kibana's public API never lists a
            rule's snoozes (get_alert_rule cannot show them). A rule holds at most
            5 snoozes, expired ones included. For no end time use mute_alert_rule.

            `space` targets a Kibana space by id (default: the default space)."""
            window = _snooze_duration(duration)
            begin = (_instant("start", start, "; omit start to begin now")
                     if start is not None else datetime.now(UTC))
            repeat = None
            if recurring is not None:
                repeat = dict(recurring)
                if isinstance(repeat.get("end"), str):
                    repeat["end"] = _instant("recurring.end", repeat["end"])
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(
                    asdict(gw.snooze_alert_rule(rule_id, window, begin, repeat)), space
                )

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def unsnooze_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            schedule_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Cancel one snooze of an alerting rule, so its actions fire again in
            that window. `schedule_id` is the `schedule_id` snooze_alert_rule
            returned — Kibana's public API lists no snoozes, so there is no other
            source. A cancelled snooze no longer counts toward the rule's 5.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.unsnooze_alert_rule(rule_id, schedule_id)
            return with_space(
                {"rule_id": rule_id, "schedule_id": schedule_id, "unsnoozed": True}, space
            )

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def update_alert_rule_api_key(
            rule_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Regenerate an alerting rule's API key from this server's
            credentials, by id: from then on the rule runs its queries and actions
            with this server's API key's privileges. Use it when a rule fails
            because the key it ran with was revoked or lost privileges. It does not
            change the rule's definition or enabled state, and no key material is
            returned.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.update_alert_rule_api_key(rule_id)
            return with_space({"id": rule_id, "api_key_updated": True}, space)

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete an alerting rule.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_alert_rule(rule_id)
            return with_space({"id": rule_id, "deleted": True}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_connector(
            connector_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Permanently delete an action connector. This cannot be undone.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.delete_connector(connector_id)
            return with_space({"id": connector_id, "deleted": True}, space)

        @mcp.tool(tags={self.name, "destructive"}, annotations=_EXECUTE)
        def execute_connector(
            connector_id: str,
            params: dict[str, Any],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Run a connector NOW with the given type-specific params (e.g.
            .server-log: {'message': ...}; .index: {'documents': [...]}).
            WARNING: this fires the connector's real external action (send email,
            post to Slack, create a ticket) — it is not reversible.

            Preconfigured connectors are instance-global: executing one succeeds in any space.
            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return with_space(gw.execute_connector(connector_id, params), space)
