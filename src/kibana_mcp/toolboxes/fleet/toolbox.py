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
#82 for a vetted credential-handling design); so are Cloud-only surfaces
(agentless policies, cloud connectors, proxies). D51 adds agent tags (read and
edit on explicit ids), diagnostics requests and their bundles' status, and EPM
package install (a package that is not installed) / uninstall (destructive:
Kibana deletes the package's package policies with it).
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
# A write whose repeat leaves the same state (D51: a tag edit, a fresh install).
_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Kibana's limit-free id list, bounded so the per-agent reads before and after
# the call stay small (D51).
_MAX_TAGGED_AGENTS = 100


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
            assigned policy_id, hostname, version, last check-in and tags.
            Empty when no agents are enrolled."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(a) for a in gw.list_agents()]

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_agent(agent_id: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
            """Get one Elastic Agent by its id: status, assigned policy_id,
            hostname, version, enrolled-at, last check-in and tags. Errors if no
            agent with that id exists."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_agent(agent_id))

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def get_agent_status_summary() -> dict[str, Any]:
            """Get fleet-wide agent status counts: how many agents are online,
            error, offline, inactive, updating, unenrolled, and the total."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.get_agent_status())

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_agent_tags() -> list[str]:
            """List the distinct tags set on enrolled agents (inactive ones
            included), e.g. ['production', 'linux']. Empty when no agent has a
            tag. get_agent and list_agents show each agent's own tags;
            update_agent_tags adds or removes them."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return gw.list_agent_tags()

        @mcp.tool(tags={self.name, "read"}, annotations=_READ)
        def list_agent_diagnostics(
            agent_id: Annotated[str, Field(min_length=1)],
        ) -> list[dict[str, Any]]:
            """List the diagnostics bundles requested from one agent: each
            bundle's id, file name, status
            ('IN_PROGRESS' until the agent uploads it, then 'READY'; 'FAILED',
            'EXPIRED'), created-at and the action_id request_agent_diagnostics
            returned. Metadata only — download a bundle from the agent's
            Diagnostics tab in Kibana (Fleet > Agents). Errors if no agent with
            that id exists (list_agents shows the ids)."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return [asdict(d) for d in gw.list_agent_diagnostics(agent_id)]

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

        # --- Integrations (EPM) install, agent tags and diagnostics (D51) ---

        @mcp.tool(tags={self.name, "write"}, annotations=_IDEMPOTENT_WRITE)
        def install_package(
            name: Annotated[str, Field(min_length=1)],
            version: Annotated[str, Field(min_length=1)] | None = None,
        ) -> dict[str, Any]:
            """Install an integration package that is not installed yet (e.g.
            'nginx'; names from list_packages), at its latest version or at
            `version` (Kibana installs only the latest one). Creates the
            package's Elasticsearch and Kibana assets (one with Kibana assets
            also adds the managed 'logs-*' and 'metrics-*' data views to every
            space if missing); attach it to an agent policy afterwards with
            create_package_policy. A package already
            installed at that version (or at any version when `version` is
            omitted) is left as it is: already_installed=true, nothing sent.
            Never changes an installed version — an upgrade cannot be undone on
            this license; that is refused, naming what to do instead.
            uninstall_package undoes an install. Returns {name, version,
            status, already_installed}."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.install_package(name=name, version=version))

        @mcp.tool(tags={self.name, "write"}, annotations=_IDEMPOTENT_WRITE)
        def update_agent_tags(
            agent_ids: Annotated[
                list[Annotated[str, Field(min_length=1)]],
                Field(min_length=1, max_length=_MAX_TAGGED_AGENTS),
            ],
            tags_to_add: list[Annotated[str, Field(min_length=1)]] | None = None,
            tags_to_remove: list[Annotated[str, Field(min_length=1)]] | None = None,
        ) -> dict[str, Any]:
            """Add and/or remove tags on the exact agents given (agent ids from
            list_agents, at most 100; never a fleet-wide query). Other tags
            are kept; removing a tag an agent lacks is a no-op. Tags are
            Fleet-side labels for finding agents: the agents themselves, their
            policies and data are unchanged (an offline agent is tagged at
            once). Refuses an unknown agent id before changing
            anything. Returns the action_id and, per agent, previous_tags and
            tags (read back after the call: passing the difference back undoes
            it); not_applied_agent_ids lists any agent Kibana did not update."""
            add = list(dict.fromkeys(tags_to_add or []))
            remove = list(dict.fromkeys(tags_to_remove or []))
            if not add and not remove:
                raise ToolError(
                    "nothing to change: pass tags_to_add and/or tags_to_remove "
                    "(a non-empty list of tags)")
            both = [t for t in add if t in remove]
            if both:
                raise ToolError(
                    f"tag(s) {both} are in both tags_to_add and tags_to_remove — pass each "
                    "tag in one list only (Kibana would keep it)")
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.update_agent_tags(
                    agent_ids=agent_ids, tags_to_add=add, tags_to_remove=remove))

        @mcp.tool(tags={self.name, "write"}, annotations=_WRITE)
        def request_agent_diagnostics(
            agent_id: Annotated[str, Field(min_length=1)],
            include_cpu_metrics: bool = False,
        ) -> dict[str, Any]:
            """Ask one Elastic Agent (id from list_agents) to collect a
            diagnostics bundle (its logs, configuration and state) and upload
            it to Fleet. It does not change what the agent collects or ships.
            include_cpu_metrics=True adds a CPU profile. An online agent
            uploads within seconds; an offline one when it next checks in, if
            within the 3-hour action expiry. Returns {action_id, agent_id,
            agent_status}; follow it with list_agent_diagnostics (status
            READY = uploaded; download it from the agent's Diagnostics tab in
            Kibana). Each call makes a new bundle."""
            with gateway_errors(), deps.gateway_factory() as gw:
                return asdict(gw.request_agent_diagnostics(
                    agent_id=agent_id, include_cpu_metrics=include_cpu_metrics))

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

        @mcp.tool(tags={self.name, "destructive"}, annotations=_DESTRUCTIVE)
        def uninstall_package(
            name: Annotated[str, Field(min_length=1)], force: bool = False
        ) -> dict[str, Any]:
            """Uninstall an installed integration package (names from
            list_installed_packages). DESTRUCTIVE: removes the package's
            Elasticsearch and Kibana assets (index templates, ingest pipelines,
            its dashboards) and DELETES its package policies, so agents stop
            collecting its data; ingested data and the shared 'logs-*'/
            'metrics-*' data views stay. A package with package
            policies is refused unless force=True (the refusal names them;
            delete_package_policy removes one at a time; force is sent to
            Kibana only then); one with a package policy on a managed or the
            default Fleet Server agent policy, and fleet_server with any
            package policy, are refused regardless of force, as is
            elastic_agent while an agent policy has monitoring on (it holds
            the monitoring data streams' templates and pipelines). With Fleet
            space awareness on (get_fleet_settings
            space_awareness_migration_status set) every uninstall is refused,
            force or not: Kibana deletes the package policies of every space
            and this server cannot prove its key reads them all. Returns
            {uninstalled, name, version,
            deleted_package_policies} (as read before the call);
            install_package reinstalls the package, not its policies."""
            with gateway_errors(), deps.gateway_factory() as gw:
                result = gw.uninstall_package(name=name, force=force)
            return {"uninstalled": True, **asdict(result)}

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
