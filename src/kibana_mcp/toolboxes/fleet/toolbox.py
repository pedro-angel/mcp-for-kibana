"""The fleet toolbox: read, configure, and operate Fleet — the Elastic Agent
fleet, agent + integration policies, enrolled integrations (EPM), and outputs.

Fleet is GA on a Basic license. `read` tools list/get the fleet surface; the
`write` tier creates/updates agent policies, package (integration) policies,
and outputs; the `destructive` tier deletes those objects and commands live
agents — reassign/upgrade/unenroll, single and bulk. Bulk tools act ONLY on
the explicit agent_ids given (never a fleet-wide sweep) and require
confirm=True, like disable_streams. Updates are read-modify-write (omitted
fields are preserved); managed policies, the default Fleet Server policy, and
default outputs are guarded against deletion/hostile reassignment. Three read
families stay SECRET-REDACTED: enrollment keys drop the `api_key` value,
outputs never accept or return ssl/secret fields, and uninstall-token *values*
are never fetched (only their metadata). Enrollment-key minting/revocation —
the only secret-minting surface — is deliberately out of scope (deferred to
#82 for a vetted credential-handling design); so are EPM package install/
delete and Cloud-only surfaces (agentless policies, cloud connectors,
proxies).
"""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from kibana_mcp.toolboxes.base import ToolboxDeps, gateway_errors

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


class FleetToolbox:
    name = "fleet"

    def register(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        self._register_read(mcp, deps)
        self._register_write(mcp, deps)
        self._register_destructive(mcp, deps)

    def _register_read(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        # --- Fleet status / settings ---

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_fleet_settings() -> dict[str, Any]:
            """Get global Fleet settings: whether prerelease integrations and the
            integration knowledge base are enabled, and the space-awareness
            migration status."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_fleet_settings())

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def check_fleet_permissions() -> dict[str, Any]:
            """Check whether the current API key has the privileges to operate
            Fleet ({"success": true} when it does). Use this first if other fleet
            tools return authorization errors."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.check_fleet_permissions())

        # --- Agents ---

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_agents() -> list[dict[str, Any]]:
            """List enrolled Elastic Agents (including inactive ones): each
            agent's id, status ('online'/'offline'/'error'/'degraded'/...),
            assigned policy_id, hostname, version, and last check-in. Empty when
            no agents are enrolled."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(a) for a in gw.list_agents()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_agent(agent_id: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one Elastic Agent by its id: status, assigned policy_id,
            hostname, version, enrolled-at and last check-in. Errors if no agent
            with that id exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_agent(agent_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_agent_status_summary() -> dict[str, Any]:
            """Get fleet-wide agent status counts: how many agents are online,
            error, offline, inactive, updating, unenrolled, and the total."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_agent_status())

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_agent_versions() -> list[str]:
            """List the Elastic Agent versions available for upgrade on this
            deployment (newest first)."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return gw.list_agent_versions()

        # --- Agent + package (integration) policies ---

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_agent_policies() -> list[dict[str, Any]]:
            """List agent policies: each policy's id, name, namespace, assigned
            agent_count, status, whether it is managed, and which monitoring
            (logs/metrics) is enabled."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(p) for p in gw.list_agent_policies()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_agent_policy(
            agent_policy_id: Annotated[str, Field(min_length=1)],
        ) -> dict[str, Any]:
            """Get one agent policy by id: name, namespace, description, assigned
            agent_count, status, managed flag, and enabled monitoring. Errors if
            no policy with that id exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_agent_policy(agent_policy_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_package_policies() -> list[dict[str, Any]]:
            """List package (integration) policies — the integrations attached to
            agent policies: each one's id, name, the integration package
            (name/title/version), the parent agent_policy_id, and enabled flag."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(p) for p in gw.list_package_policies()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_package_policy(
            package_policy_id: Annotated[str, Field(min_length=1)],
        ) -> dict[str, Any]:
            """Get one package (integration) policy by id: name, namespace, its
            integration package (name/title/version), parent agent_policy_id, and
            enabled flag. Errors if no package policy with that id exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_package_policy(package_policy_id))

        # --- Enrollment (metadata only; secrets are redacted) ---

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_enrollment_keys() -> list[dict[str, Any]]:
            """List enrollment API keys — METADATA ONLY (id, name, policy_id,
            active, created-at). The secret key value is never returned."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(k) for k in gw.list_enrollment_keys()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_enrollment_key(key_id: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one enrollment API key by id — METADATA ONLY (name, policy_id,
            active, created-at); the secret key value is never returned. Errors if
            no key with that id exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_enrollment_key(key_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_uninstall_tokens() -> list[dict[str, Any]]:
            """List agent uninstall tokens — METADATA ONLY (id, policy_id,
            policy_name, created-at). The decrypted token value is never
            returned."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(t) for t in gw.list_uninstall_tokens()]

        # --- Integrations (EPM) ---

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_packages() -> list[dict[str, Any]]:
            """List integration packages available in the registry: each one's
            name, title, version, install status, and description. (This is the
            full catalog and can be large.)"""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(p) for p in gw.list_packages()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_installed_packages() -> list[dict[str, Any]]:
            """List the integration packages actually installed on this
            deployment: name, title, version, and status."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(p) for p in gw.list_installed_packages()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_package(name: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one integration package by name (e.g. 'nginx', 'system'): its
            latest title, version, install status, type and description. Errors if
            no package with that name exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_package(name))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_package_categories() -> list[dict[str, Any]]:
            """List integration categories (id, title, and how many packages are
            in each) for browsing the integration catalog."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(c) for c in gw.list_package_categories()]

        # --- Outputs + Fleet Server hosts (secrets redacted) ---

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_outputs() -> list[dict[str, Any]]:
            """List Fleet outputs (where agent data is shipped): each output's id,
            name, type (elasticsearch/logstash/kafka), hosts, and default flags.
            Secret/ssl fields are never returned."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(o) for o in gw.list_outputs()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_output_health(output_id: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get the latest health of one output by id: state
            (HEALTHY/DEGRADED/UNKNOWN), a message, and the timestamp."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_output_health(output_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_fleet_server_hosts() -> list[dict[str, Any]]:
            """List configured Fleet Server hosts (the URLs agents connect to):
            each host's id, name, urls, and default flag. Empty when none are
            registered."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(h) for h in gw.list_fleet_server_hosts()]

    def _register_write(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        # --- Agent policies ---

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_agent_policy(
            name: Annotated[str, Field(min_length=1)],
            namespace: Annotated[str, Field(min_length=1)],
            description: Annotated[str, Field(min_length=1)] | None = None,
            monitoring_enabled: list[Annotated[str, Field(min_length=1)]] | None = None,
            inactivity_timeout: Annotated[int, Field(ge=0)] | None = None,
        ) -> dict[str, Any]:
            """Create an agent policy. `namespace` scopes the policy's data;
            `monitoring_enabled` turns on agent monitoring (e.g.
            ['logs','metrics']); `inactivity_timeout` (ms) unenrolls an agent
            after it has been inactive that long. Returns the created policy."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.create_agent_policy(
                    name=name, namespace=namespace, description=description,
                    monitoring_enabled=monitoring_enabled, inactivity_timeout=inactivity_timeout))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_agent_policy(
            agent_policy_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)] | None = None,
            namespace: Annotated[str, Field(min_length=1)] | None = None,
            description: str | None = None,
            monitoring_enabled: list[Annotated[str, Field(min_length=1)]] | None = None,
        ) -> dict[str, Any]:
            """Update an agent policy's fields (read-modify-write — omitted
            fields are preserved). Pass description="" to clear the
            description; monitoring_enabled=[] turns monitoring off. Refuses a
            managed policy. Returns the updated policy."""
            changes = {k: v for k, v in {
                "name": name, "namespace": namespace, "description": description,
                "monitoring_enabled": monitoring_enabled,
            }.items() if v is not None}
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.update_agent_policy(agent_policy_id=agent_policy_id, changes=changes))

        # --- Package (integration) policies ---

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_package_policy(
            name: Annotated[str, Field(min_length=1)],
            package: dict[str, Any],
            agent_policy_id: Annotated[str, Field(min_length=1)],
            inputs: dict[str, Any] | list[Any] | None = None,
        ) -> dict[str, Any]:
            """Attach an integration to an agent policy. `package` identifies
            the integration (e.g. {'name': 'system', 'version': '1.62.0'} — see
            get_package); `inputs` optionally overrides the integration's
            default input config (Kibana fills sensible defaults when
            omitted). Returns the created package policy."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.create_package_policy(
                    name=name, package=package, agent_policy_id=agent_policy_id, inputs=inputs))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_package_policy(
            package_policy_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)] | None = None,
            namespace: Annotated[str, Field(min_length=1)] | None = None,
            description: str | None = None,
            enabled: bool | None = None,
            agent_policy_id: Annotated[str, Field(min_length=1)] | None = None,
            package: dict[str, Any] | None = None,
            inputs: dict[str, Any] | list[Any] | None = None,
        ) -> dict[str, Any]:
            """Update a package (integration) policy's fields (read-modify-
            write — omitted fields are preserved). `agent_policy_id` re-parents
            the policy to a different agent policy; `package` changes the
            integration/version (e.g. {'name': 'system', 'version': '1.63.0'}
            — see get_package); `inputs` overrides the integration's input
            config. Returns the updated package policy."""
            changes = {k: v for k, v in {
                "name": name, "namespace": namespace, "description": description,
                "enabled": enabled, "agent_policy_id": agent_policy_id,
                "package": package, "inputs": inputs,
            }.items() if v is not None}
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.update_package_policy(
                    package_policy_id=package_policy_id, changes=changes))

        # --- Outputs ---

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def create_output(
            name: Annotated[str, Field(min_length=1)],
            type: Literal["elasticsearch", "logstash", "kafka", "remote_elasticsearch"],
            hosts: list[Annotated[str, Field(min_length=1)]],
            is_default: bool | None = None,
            is_default_monitoring: bool | None = None,
        ) -> dict[str, Any]:
            """Create a Fleet output (where agent data is shipped). Non-secret
            fields only: no ssl/secrets/config_yaml (use the Kibana UI for
            TLS/authenticated outputs). `is_default`/`is_default_monitoring`
            promote this output, auto-un-defaulting the prior one. Returns the
            created output (secrets are never accepted here, and never
            returned either)."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.create_output(
                    name=name, type=type, hosts=hosts,
                    is_default=is_default, is_default_monitoring=is_default_monitoring))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def update_output(
            output_id: Annotated[str, Field(min_length=1)],
            name: Annotated[str, Field(min_length=1)] | None = None,
            type: Literal["elasticsearch", "logstash", "kafka", "remote_elasticsearch"] | None = None,
            hosts: list[Annotated[str, Field(min_length=1)]] | None = None,
            is_default: bool | None = None,
            is_default_monitoring: bool | None = None,
            confirm: bool = False,
        ) -> dict[str, Any]:
            """Update a Fleet output (read-modify-write — omitted fields are
            preserved). Setting is_default/is_default_monitoring=True PROMOTES
            this output (Fleet auto-un-defaults the prior one, which then
            becomes deletable). Editing a CURRENTLY-default output requires
            confirm=True (protects live agent traffic). Returns the updated
            output."""
            changes = {k: v for k, v in {
                "name": name, "type": type, "hosts": hosts,
                "is_default": is_default, "is_default_monitoring": is_default_monitoring,
            }.items() if v is not None}
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.update_output(output_id=output_id, changes=changes, confirm=confirm))

    def _register_destructive(self, mcp: FastMCP, deps: ToolboxDeps) -> None:
        # --- Agent policies / package policies / outputs ---

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_agent_policy(
            agent_policy_id: Annotated[str, Field(min_length=1)], force: bool = False
        ) -> dict[str, Any]:
            """Delete an agent policy. DESTRUCTIVE: removes the policy and its
            package policies. Refuses a managed policy and the default Fleet
            Server policy regardless of force. force=True bypasses Kibana's
            assigned-agent check."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_agent_policy(agent_policy_id=agent_policy_id, force=force)
            return {"deleted": True, "agent_policy_id": agent_policy_id}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_package_policy(
            package_policy_id: Annotated[str, Field(min_length=1)], force: bool = False
        ) -> dict[str, Any]:
            """Delete a package (integration) policy. DESTRUCTIVE: detaches
            the integration from its agent policy; agents stop collecting
            that data. force=True bypasses Kibana's in-use checks."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_package_policy(package_policy_id=package_policy_id, force=force)
            return {"deleted": True, "package_policy_id": package_policy_id}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def delete_output(output_id: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Delete a Fleet output. DESTRUCTIVE: agents shipping through it
            need a working output. Refuses the default output(s) — promote a
            replacement first (update_output is_default=True), then delete
            the old one. No force escape."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.delete_output(output_id=output_id)
            return {"deleted": True, "output_id": output_id}

        # --- Agent lifecycle: single ---

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def reassign_agent(
            agent_id: Annotated[str, Field(min_length=1)],
            policy_id: Annotated[str, Field(min_length=1)],
        ) -> dict[str, Any]:
            """Reassign one enrolled agent to a different agent policy.
            DESTRUCTIVE: changes what the live agent collects/ships. Refuses a
            managed or default Fleet Server target policy."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.reassign_agent(agent_id=agent_id, policy_id=policy_id)
            return {"ok": True, "agent_id": agent_id}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def upgrade_agent(
            agent_id: Annotated[str, Field(min_length=1)],
            version: Annotated[str, Field(min_length=1)],
            source_uri: Annotated[str, Field(min_length=1)] | None = None,
        ) -> dict[str, Any]:
            """Upgrade one enrolled agent to `version` (must be one of
            list_agent_versions). DESTRUCTIVE: commands a live agent binary
            swap; async — the agent applies it on its own schedule.
            `source_uri` overrides the default download source."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.upgrade_agent(agent_id=agent_id, version=version, source_uri=source_uri)
            return {"ok": True, "agent_id": agent_id}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def unenroll_agent(
            agent_id: Annotated[str, Field(min_length=1)],
            force: bool = False,
            revoke: bool = False,
        ) -> dict[str, Any]:
            """Unenroll one Elastic Agent. DESTRUCTIVE: the agent stops
            shipping data (only uninstalls if it later checks in with an
            uninstall command). force=True unenrolls an already-unenrolled/
            offline agent; revoke=True also invalidates its API key
            immediately."""
            with gateway_errors(), deps.gateway_factory() as gw:
                gw.unenroll_agent(agent_id=agent_id, force=force, revoke=revoke)
            return {"ok": True, "agent_id": agent_id}

        # --- Agent lifecycle: bulk (explicit agent_ids + confirm=True only) ---

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def bulk_reassign(
            agent_ids: list[Annotated[str, Field(min_length=1)]],
            policy_id: Annotated[str, Field(min_length=1)],
            confirm: bool = False,
        ) -> dict[str, Any]:
            """Reassign agents to a different agent policy. DESTRUCTIVE: acts
            on the exact agent_ids given (never a fleet-wide sweep), requires
            confirm=True. Refuses a managed or default Fleet Server target
            policy. Async — returns an action_id, not the immediate per-agent
            result."""
            if not agent_ids:
                raise ToolError("agent_ids must be non-empty")
            if not confirm:
                raise ToolError("bulk actions require confirm=True")
            with gateway_errors(), deps.gateway_factory() as gw:
                return {"action_id": gw.bulk_reassign(agent_ids=agent_ids, policy_id=policy_id)}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def bulk_upgrade(
            agent_ids: list[Annotated[str, Field(min_length=1)]],
            version: Annotated[str, Field(min_length=1)],
            source_uri: Annotated[str, Field(min_length=1)] | None = None,
            confirm: bool = False,
        ) -> dict[str, Any]:
            """Upgrade agents to `version`. DESTRUCTIVE: acts on the exact
            agent_ids given (never a fleet-wide sweep), requires confirm=True.
            Async — queues the upgrade per-agent and returns an action_id."""
            if not agent_ids:
                raise ToolError("agent_ids must be non-empty")
            if not confirm:
                raise ToolError("bulk actions require confirm=True")
            with gateway_errors(), deps.gateway_factory() as gw:
                return {"action_id": gw.bulk_upgrade(
                    agent_ids=agent_ids, version=version, source_uri=source_uri)}

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def bulk_unenroll(
            agent_ids: list[Annotated[str, Field(min_length=1)]],
            force: bool = False,
            revoke: bool = False,
            confirm: bool = False,
        ) -> dict[str, Any]:
            """Unenroll agents. DESTRUCTIVE: acts on the exact agent_ids given
            (never a fleet-wide sweep), requires confirm=True. Async —
            returns an action_id."""
            if not agent_ids:
                raise ToolError("agent_ids must be non-empty")
            if not confirm:
                raise ToolError("bulk actions require confirm=True")
            with gateway_errors(), deps.gateway_factory() as gw:
                return {"action_id": gw.bulk_unenroll(
                    agent_ids=agent_ids, force=force, revoke=revoke)}
