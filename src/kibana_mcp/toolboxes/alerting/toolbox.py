"""The alerting toolbox: alert rules + connectors.

Wraps kibana-py's `alerting.rule` + `connectors` namespaces (NOT the deprecated
`actions` alias). `maintenance_windows` is deferred (Platinum-gated; the stack is
basic license). Two safety choices: rules are created **disabled by default**
(inert until an explicit enable), and `execute_connector` is **destructive-tier**
because it fires the connector's real external action irreversibly.
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastmcp import FastMCP
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
# execute_connector fires a real external action: NOT idempotent (repeats repeat
# the side effect) and open-world (reaches systems outside Kibana).
_EXECUTE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)


class AlertingToolbox:
    name = "alerting"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_alert_rules(
            search: str | None = None,
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List alerting rules (optionally filtered by a name search). Returns
            concise summaries (id, name, rule_type_id, consumer, enabled, schedule,
            status, tags) — not the full rule params.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(r) for r in gw.list_alert_rules(search)]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_alert_rule(
            rule_id: Annotated[str, Field(min_length=1)],
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Get one alerting rule's summary by id.

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
        def list_connectors(
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> list[dict[str, Any]]:
            """List configured action connectors (id, name, connector_type_id).

            Preconfigured connectors (defined in kibana.yml) are instance-global and appear in every space with the same id.
            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                return [asdict(c) for c in gw.list_connectors()]

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
            space: Annotated[str, Field(pattern=SPACE_ID_PATTERN)] | None = None,
        ) -> dict[str, Any]:
            """Disable (stop running) an alerting rule.

            `space` targets a Kibana space by id (default: the default space)."""
            with gateway_errors(), deps.gateway_factory(space) as gw:
                gw.disable_alert_rule(rule_id)
            return with_space({"id": rule_id, "enabled": False}, space)

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
