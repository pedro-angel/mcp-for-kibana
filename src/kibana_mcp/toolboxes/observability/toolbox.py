"""The observability toolbox (Wave 3): access to the Kibana observability surface
that is available on a Basic license and wrapped by kibana-py — Synthetics
monitoring, Uptime settings, and APM *configuration*.

The reads are `read` tier. The Synthetics write tier (D46) edits monitors,
global parameters and private locations (`write`), runs a monitor once
(`write`), and deletes each of the three (`destructive`). APM agent
configurations are created or replaced (`write`) and deleted (`destructive`)
(D47); APM annotations are only read, since creating one needs a Gold license
(403 on Basic, probed). This toolbox does NOT
expose APM service/transaction/trace/service-map telemetry (those are
internal-only Kibana APIs, unreachable by an external client) or SLOs (which
need a Platinum license). Both are out of scope: subscription-gated surfaces are
not built here. No tool takes `space` (D10: the toolbox mixes space-scoped
Synthetics with cluster-global APM configuration).
"""

import re
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.core.errors import KibanaNotFound
from kibana_mcp.core.models import SyntheticMonitor
from kibana_mcp.ports.gateway import KibanaGateway
from kibana_mcp.toolboxes.base import ToolboxDeps, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
# A repeated update leaves the object as the first call did.
_WRITE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
# The run reaches outside Kibana: the agent sends the monitor's requests to its
# target (a paused monitor too), and every call starts a new run.
_RUN = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

_Id = Annotated[str, Field(min_length=1)]
_Text = Annotated[str, Field(min_length=1)]

# The field that says what a monitor checks, per type. Kibana stores another
# type's field without complaint (a url on a tcp monitor, probed on 9.5.4), so
# the tools refuse it.
_TARGET_FIELD = {"http": "url", "tcp": "host", "icmp": "host", "browser": "inline_script"}
_TARGET_EXAMPLE = {
    "http": "url (e.g. 'https://example.com/health')",
    "tcp": "host with its port (e.g. 'db.internal:5432')",
    "icmp": "host (e.g. 'gateway.internal')",
    "browser": "inline_script, a Playwright journey (e.g. \"step('home', async () => "
               "{ await page.goto('https://example.com'); });\")",
}

# Kibana checks a schedule's number against its minute list whatever the unit
# (9.5.4: '15s' and '240s' pass, '45s' and '90s' fail as "45 minutes"), and its
# 400 drops the list from the message the tool would show, so the check is here.
# It compares the digits as text ('05' is refused as "05 minutes"), so the
# number is sent in its canonical form: '05m' goes out as '5m'.
_SCHEDULE_RE = re.compile(r"([0-9]+)([sm])")
_SCHEDULE_NUMBERS = (1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 240)


def _schedule(value: str | None) -> str | None:
    if value is None:
        return None
    match = _SCHEDULE_RE.fullmatch(value)
    if match is None or int(match[1]) not in _SCHEDULE_NUMBERS:
        raise ToolError(
            f"schedule '{value}' is not a Synthetics schedule — pass one of the numbers Kibana "
            "allows, 1, 2, 3, 5, 10, 15, 20, 30, 60, 120 or 240, with unit m (minutes) or s "
            "(seconds), e.g. '10m' or '30s'"
        )
    return f"{int(match[1])}{match[2]}"


def _check_locations(gw: KibanaGateway, wanted: list[str]) -> None:
    """Every entry names an existing private location by id or label (Kibana
    takes either). Refused before the write, naming where locations come from."""
    known = gw.list_synthetic_private_locations()
    if not known:
        raise ToolError(
            "no private location exists — create one with create_synthetic_private_location "
            "(it needs a Fleet agent policy) and pass its id; this server offers no "
            "Elastic-managed locations"
        )
    names = {loc.id for loc in known} | {loc.label for loc in known}
    unknown = [w for w in wanted if w not in names]
    if unknown:
        available = ", ".join(f"{loc.id} ({loc.label})" for loc in known)
        raise ToolError(
            f"private location(s) {unknown} not found — pass an id or label from "
            f"list_synthetic_private_locations: {available}"
        )


def _a(monitor_type: str) -> str:
    return f"an {monitor_type}" if monitor_type in ("http", "icmp") else f"a {monitor_type}"


def _check_targets(
    monitor_type: str, given: dict[str, str | None], monitor_id: str | None = None
) -> None:
    """The type's own target field, and no other type's (create: required)."""
    field = _TARGET_FIELD[monitor_type]
    for other, value in given.items():
        if other == field or value is None:
            continue
        if monitor_id is None:
            raise ToolError(
                f"`{other}` does not apply to {_a(monitor_type)} monitor — {_a(monitor_type)} "
                f"monitor takes `{field}`"
            )
        fits = " or ".join(_a(t) for t, f in _TARGET_FIELD.items() if f == other)
        raise ToolError(
            f"monitor '{monitor_id}' is {_a(monitor_type)} monitor: it takes `{field}`, not "
            f"`{other}` — the type cannot change; create {fits} monitor with "
            "create_synthetic_monitor"
        )
    if monitor_id is None and given.get(field) is None:
        raise ToolError(
            f"{_a(monitor_type)} monitor needs `{field}` — pass {_TARGET_EXAMPLE[monitor_type]}"
        )


def _monitor(m: SyntheticMonitor) -> dict[str, Any]:
    return asdict(m)


# list_apm_environments' marker for "all environments" (Kibana's UI uses it for
# services too). Kibana stores it as a literal name (probed on 9.5.4), which no
# agent's lookup reads as "all", so the upsert refuses to create a configuration
# under it. A configuration another client stored under the literal name is
# matched exactly by view and delete (probed), so the tools still replace and
# delete that one; "omit it" would point them at the real all-* configuration.
_ALL_OPTION = "ALL_OPTION_VALUE"
# An APM config's service name or environment: Kibana stores "" and then cannot
# match it again (its view and delete read "" as omitted; probed), so never "".
_ApmName = Annotated[str, Field(min_length=1)] | None
_SettingValue = str | bool | int | float


def _refuse_new_all_option(service_name: str | None, environment: str | None) -> None:
    # Only called when no configuration is stored under the given names.
    for arg, value, scope in (("service_name", service_name, "services"),
                              ("environment", environment, "environments")):
        if value == _ALL_OPTION:
            raise ToolError(
                f"{arg} '{_ALL_OPTION}' is list_apm_environments' marker for all {scope}, "
                f"and no configuration is stored under it as a literal name — omit {arg} "
                f"to create or replace the all-{scope} configuration"
            )


def _setting(value: _SettingValue) -> str:
    # Kibana takes only strings ("0.5 does not match expected type string",
    # probed), so numbers and booleans are sent in the form it parses.
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class ObservabilityToolbox:
    name = "observability"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_synthetics_write(mcp, deps)
        self._register_synthetics_destructive(mcp, deps)
        self._register_apm_config_writes(mcp, deps)
        self._register_apm_config_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_synthetic_monitors() -> list[dict[str, Any]]:
            """List all Synthetics monitors (HTTP/TCP/ICMP/browser availability
            checks): id, name, type, enabled, tags, locations, schedule, and
            target (the url or host being checked). Paginated to completion."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(m) for m in gw.list_synthetic_monitors()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_synthetic_monitor(monitor_id: str) -> dict[str, Any]:
            """Get one Synthetics monitor by its config id (the `id` field
            returned for each monitor by list_synthetic_monitors)."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_synthetic_monitor(monitor_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_synthetic_params() -> list[dict[str, Any]]:
            """List Synthetics global parameters (id, key, description, tags).
            Parameter VALUES are never returned."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(p) for p in gw.list_synthetic_params()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_synthetic_private_locations() -> list[dict[str, Any]]:
            """List Synthetics private locations (id, label, agent_policy_id,
            is_invalid, tags) — the Fleet-agent-backed places monitors run
            from."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(loc) for loc in gw.list_synthetic_private_locations()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_uptime_settings() -> dict[str, Any]:
            """The Uptime app settings: the heartbeat index pattern, TLS
            certificate age / expiration alert thresholds, and default alert
            connectors and email recipients."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_uptime_settings())

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_apm_agent_configs() -> list[dict[str, Any]]:
            """List APM agent configurations (central config pushed to APM
            agents): service name/environment, the settings map, whether an
            agent has applied it, and the etag. This is APM *configuration*, not
            service metrics or traces."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(c) for c in gw.list_apm_agent_configs()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_apm_agent_config(
            service_name: str | None = None,
            environment: str | None = None,
        ) -> dict[str, Any]:
            """Get one APM agent configuration by service name + environment.
            Omit both to fetch the all-services/all-environments configuration.
            Errors if no matching configuration exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_apm_agent_config(service_name, environment))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_apm_environments(service_name: str | None = None) -> list[dict[str, Any]]:
            """List the APM service environments known to agent-configuration.
            Omit service_name to list across all services. The sentinel
            environment name "ALL_OPTION_VALUE" means "all environments" and is
            always present — it is not a literal environment."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(e) for e in gw.list_apm_environments(service_name)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_apm_sourcemaps() -> list[dict[str, Any]]:
            """List uploaded RUM source map artifacts (identifier, created).
            Paginated to completion."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(s) for s in gw.list_apm_sourcemaps()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def search_apm_annotations(
            service_name: Annotated[str, Field(min_length=1)],
            start: Annotated[str, Field(min_length=1)],
            end: Annotated[str, Field(min_length=1)],
            environment: str = "ENVIRONMENT_ALL",
        ) -> list[dict[str, Any]]:
            """Search APM annotations (e.g. deployment markers) for a service in
            an ISO-8601 [start, end] time window. `environment` defaults to all
            environments. Returns each annotation's id, timestamp, text, type."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [
                    asdict(a)
                    for a in gw.search_apm_annotations(service_name, start, end, environment)
                ]

    def _register_synthetics_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_synthetic_monitor(
            type: Literal["http", "tcp", "icmp", "browser"],
            name: _Text,
            private_locations: Annotated[list[_Text], Field(min_length=1)],
            url: _Text | None = None,
            host: _Text | None = None,
            inline_script: _Text | None = None,
            schedule: str | None = None,
            enabled: bool = True,
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Create a Synthetics monitor that checks a target on a schedule from
            private locations. Pass the target field of its `type`: `url` for
            http, `host` for tcp (with its port, e.g. 'db.internal:5432') and
            icmp, `inline_script` (a Playwright journey) for browser.
            `private_locations` takes ids or labels from
            list_synthetic_private_locations (create one with
            create_synthetic_private_location). `schedule` is e.g. '10m' or '30s'
            (Kibana allows 1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 240; default 3m,
            10m for browser). `name` must be unique. Starts enabled unless
            enabled=False. Checks run on the Elastic Agents enrolled in each
            location's agent policy; browser monitors need the
            elastic-agent-complete image. Returns the monitor as
            get_synthetic_monitor does."""
            _check_targets(type, {"url": url, "host": host, "inline_script": inline_script})
            checked = _schedule(schedule)
            with gateway_errors(), deps.gateway_factory() as gw:
                _check_locations(gw, private_locations)
                return _monitor(gw.create_synthetic_monitor(
                    type=type, name=name, private_locations=private_locations, url=url,
                    host=host, inline_script=inline_script, schedule=checked,
                    enabled=enabled, tags=tags,
                ))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def update_synthetic_monitor(
            monitor_id: _Id,
            name: _Text | None = None,
            url: _Text | None = None,
            host: _Text | None = None,
            inline_script: _Text | None = None,
            schedule: str | None = None,
            enabled: bool | None = None,
            tags: list[str] | None = None,
            private_locations: Annotated[list[_Text], Field(min_length=1)] | None = None,
        ) -> dict[str, Any]:
            """Edit a Synthetics monitor in place, by id (from
            list_synthetic_monitors). Only the fields you pass change. Pass
            enabled=False to pause it, True to resume. The target field must
            match the monitor's type (url: http; host: tcp, icmp; inline_script:
            browser), and the type cannot change. `tags` and `private_locations`
            replace the whole list. Returns the monitor as get_synthetic_monitor
            does."""
            targets = {"url": url, "host": host, "inline_script": inline_script}
            if all(v is None for v in (name, schedule, enabled, tags, private_locations,
                                       *targets.values())):
                raise ToolError(
                    "provide at least one field to update: name, url, host, inline_script, "
                    "schedule, enabled, tags or private_locations"
                )
            checked = _schedule(schedule)
            with gateway_errors(), deps.gateway_factory() as gw:
                if any(v is not None for v in targets.values()):
                    current = gw.get_synthetic_monitor(monitor_id)
                    _check_targets(current.type, targets, monitor_id=monitor_id)
                if private_locations is not None:
                    _check_locations(gw, private_locations)
                return _monitor(gw.update_synthetic_monitor(
                    monitor_id, name=name, url=url, host=host, inline_script=inline_script,
                    schedule=checked, enabled=enabled, tags=tags,
                    private_locations=private_locations,
                ))

        @mcp.tool(tags={self.name, "write"}, annotations=_RUN)
        def test_synthetic_monitor(monitor_id: _Id) -> dict[str, Any]:
            """Run a Synthetics monitor once now, outside its schedule, by id (from
            list_synthetic_monitors). A paused (enabled=False) monitor runs too:
            the agent sends the monitor's requests to its target now, even when
            it would not on its schedule. Each call is a new run and returns its
            test_run_id. Each run also adds a LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN
            package policy (BROWSER_SYNTHETICS_TEST_NOW_RUN for a browser
            monitor) to the location's Fleet agent policy; Kibana's own clean-up
            task removes it about 20 minutes later, even if the location is
            deleted first. The run executes only where an Elastic Agent enrolled
            in that agent policy is running; Kibana accepts the request either
            way. No tool reads the result: Kibana writes it to the synthetics-*
            data streams under that test_run_id and shows it in the Synthetics
            app."""
            with gateway_errors(), deps.gateway_factory() as gw:
                run_id = gw.test_synthetic_monitor(monitor_id)
            return {"monitor_id": monitor_id, "test_run_id": run_id}

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_synthetic_param(
            key: _Text,
            value: _Text,
            description: str | None = None,
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Create a Synthetics global parameter: a value monitors reference as
            ${key} (e.g. a token a browser journey types in). The key must not be
            in use (list_synthetic_params). The value is stored encrypted and is
            never returned by any tool. Returns id, key, description, tags."""
            with gateway_errors(), deps.gateway_factory() as gw:
                taken = next((p for p in gw.list_synthetic_params() if p.key == key), None)
                if taken is not None:
                    raise ToolError(
                        f"a parameter with key '{key}' already exists (id '{taken.id}') — change "
                        "it with update_synthetic_param, or pick another key: monitors reference "
                        "a parameter as ${key}, so two with one key would be ambiguous"
                    )
                return asdict(gw.create_synthetic_param(key, value, description, tags))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def update_synthetic_param(
            param_id: _Id,
            key: _Text | None = None,
            value: _Text | None = None,
            description: str | None = None,
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Edit a Synthetics global parameter in place, by id (from
            list_synthetic_params): rename its key, rotate its value, or change
            its description or tags (`tags` replaces the list). Only the fields
            you pass change. Neither the new nor the old value is ever returned:
            once `value` is replaced, no tool can recover the previous one, so
            keep it yourself if you may need to set it back. Renaming the key
            breaks monitors that reference the old ${key}. Returns id, key,
            description, tags."""
            if all(v is None for v in (key, value, description, tags)):
                raise ToolError(
                    "provide at least one field to update: key, value, description or tags"
                )
            with gateway_errors(), deps.gateway_factory() as gw:
                if key is not None:
                    taken = next((p for p in gw.list_synthetic_params()
                                  if p.key == key and p.id != param_id), None)
                    if taken is not None:
                        raise ToolError(
                            f"a parameter with key '{key}' already exists (id '{taken.id}') — "
                            "pick another key, or delete that one with delete_synthetic_param"
                        )
                return asdict(gw.update_synthetic_param(
                    param_id, key=key, value=value, description=description, tags=tags
                ))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_synthetic_private_location(
            label: _Text,
            agent_policy_id: _Id,
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Create a Synthetics private location: a place monitors run from,
            backed by a Fleet agent policy (`agent_policy_id`, from
            list_agent_policies or create_agent_policy in the fleet toolbox) that
            no other private location uses. Monitors placed there run on the
            Elastic Agents enrolled in that policy; Kibana adds a Synthetics
            integration to the policy for them. The label must be unique.
            Returns the location as list_synthetic_private_locations does."""
            with gateway_errors(), deps.gateway_factory() as gw:
                for loc in gw.list_synthetic_private_locations():
                    if loc.label == label:
                        raise ToolError(
                            f"a private location labelled '{label}' already exists (id "
                            f"'{loc.id}') — pick another label"
                        )
                    if loc.agent_policy_id == agent_policy_id:
                        raise ToolError(
                            f"agent policy '{agent_policy_id}' already backs private location "
                            f"'{loc.label}' (id '{loc.id}') — each private location needs its "
                            "own agent policy: create one with create_agent_policy (fleet "
                            "toolbox) and pass its id"
                        )
                return asdict(gw.create_synthetic_private_location(label, agent_policy_id, tags))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def update_synthetic_private_location(location_id: _Id, label: _Text) -> dict[str, Any]:
            """Rename a Synthetics private location, by id (from
            list_synthetic_private_locations). The label is the only thing
            Kibana lets change; it must stay unique. Returns the location as
            list_synthetic_private_locations does."""
            with gateway_errors(), deps.gateway_factory() as gw:
                for loc in gw.list_synthetic_private_locations():
                    if loc.label == label and loc.id != location_id:
                        raise ToolError(
                            f"a private location labelled '{label}' already exists (id "
                            f"'{loc.id}') — pick another label"
                        )
                return asdict(gw.update_synthetic_private_location(location_id, label))

    def _register_synthetics_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_synthetic_monitor(monitor_id: _Id) -> dict[str, Any]:
            """Permanently delete a Synthetics monitor, by id (from
            list_synthetic_monitors): its configuration stops running from every
            location. This cannot be undone; to pause it instead, call
            update_synthetic_monitor with enabled=False."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_synthetic_monitor(monitor_id)
            return {"id": monitor_id, "deleted": True}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_synthetic_param(param_id: _Id) -> dict[str, Any]:
            """Permanently delete a Synthetics global parameter, by id (from
            list_synthetic_params). Its value is lost (no tool can read it back),
            and monitors that reference its ${key} lose it. This cannot be
            undone."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_synthetic_param(param_id)
            return {"id": param_id, "deleted": True}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_synthetic_private_location(location_id: _Id) -> dict[str, Any]:
            """Permanently delete a Synthetics private location, by id (from
            list_synthetic_private_locations). Kibana refuses while monitors
            still run there: move them (update_synthetic_monitor with
            private_locations) or delete them first. The Fleet agent policy
            behind it stays, and so does any LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN
            package policy a recent test_synthetic_monitor run added to it until
            Kibana's clean-up task removes it. This cannot be undone."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_synthetic_private_location(location_id)
            return {"id": location_id, "deleted": True}

    def _register_apm_config_writes(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE_IDEMPOTENT)
        def upsert_apm_agent_config(
            settings: dict[str, _SettingValue],
            service_name: _ApmName = None,
            environment: _ApmName = None,
            agent_name: _ApmName = None,
        ) -> dict[str, Any]:
            """Create or replace an APM agent configuration: the settings APM
            agents fetch from Kibana (central configuration), e.g.
            {"transaction_sample_rate": "0.2", "log_level": "debug"}. Omit
            service_name to target all services and environment to target all
            environments; the pair names one configuration exactly
            (list_apm_agent_configs shows them). `settings` REPLACES the stored
            map: to change one setting, read the map with get_apm_agent_config
            and pass it whole with that key changed. Values go out as strings
            (numbers and booleans are converted). Kibana checks the values of
            the settings it knows but stores names it does not know, which
            agents ignore, so use the setting names from the APM agent's
            central-configuration docs. agent_name (e.g. "java", which Kibana's
            UI uses to choose the settings it shows) is optional: omitted, a
            replacement keeps the stored one and a new configuration has none.
            Returns the configuration as get_apm_agent_config does, plus
            `created` (true when none existed) and `previous_settings` (the map
            this call read and replaced, or null): pass previous_settings back
            to undo a replacement."""
            if not settings:
                raise ToolError(
                    "settings is empty — pass at least one setting, e.g. "
                    "{'transaction_sample_rate': '0.5'}; to remove a configuration use "
                    "delete_apm_agent_config"
                )
            values = {name: _setting(value) for name, value in settings.items()}
            with gateway_errors(), deps.gateway_factory() as gw:
                try:
                    previous = gw.get_apm_agent_config(service_name, environment)
                except KibanaNotFound:
                    previous = None
                if previous is None:
                    _refuse_new_all_option(service_name, environment)
                # Kibana's PUT takes no etag (probed), so the read and the
                # overwrite are not atomic; see D47.
                stored = gw.upsert_apm_agent_config(
                    service_name, environment, values,
                    # Kibana's PUT drops a stored agent_name that is not sent again.
                    agent_name=agent_name or (previous.agent_name if previous else None),
                    overwrite=previous is not None,
                )
            return {
                **asdict(stored),
                "created": previous is None,
                "previous_settings": previous.settings if previous else None,
            }

    def _register_apm_config_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_apm_agent_config(
            service_name: _ApmName = None,
            environment: _ApmName = None,
        ) -> dict[str, Any]:
            """Permanently delete one APM agent configuration, matched exactly by
            service_name and environment: omit service_name for the
            all-services configuration and environment for the
            all-environments one (list_apm_agent_configs shows them; omitting
            both deletes the configuration for all services and all
            environments). Agents then get whatever other configuration matches
            them, or their own defaults. Returns the deleted settings and
            agent_name; only passing them to upsert_apm_agent_config recreates
            it."""
            with gateway_errors(), deps.gateway_factory() as gw:
                current = gw.get_apm_agent_config(service_name, environment)
                gw.delete_apm_agent_config(service_name, environment)
            return {
                "service_name": service_name,
                "environment": environment,
                "deleted": True,
                "settings": current.settings,
                "agent_name": current.agent_name,
            }
