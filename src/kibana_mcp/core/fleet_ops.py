"""Fleet advanced operations (D51): the checks and guidance the fleet agent-tag,
diagnostics and package install/uninstall tools apply, kept pure so the adapter
and the unit-test fake say the same thing (D17: guidance text is product
contract).

Observed on Kibana 9.5.4 (Basic), and what each shaped:
- `bulk_update_agent_tags` skips an agent id that names no agent without an
  error (2 of 3 ids updated, the action counted 2, none failed), so every id is
  read first and an unknown one refuses the call;
- an install without a version upgrades a package installed at an older one,
  and the rollback route answers 403 "Rollback integration requires an
  enterprise license", so an install never changes an installed version;
- an uninstall deletes the package's package policies (one on an agent policy
  with no agents went without `force`; one with an agent needed `force`), so a
  package that has any is refused unless forced, naming them;
- an uninstall of `elastic_agent` went through without `force` while an agent
  policy had monitoring on (it removed the monitoring data streams' index
  templates and ingest pipelines; Kibana has no check), so it is refused while
  any agent policy has `monitoring_enabled`, regardless of `force`;
- with Fleet space awareness on, an uninstall deletes every space's package
  policies, while the spaces listing hides each space the API key cannot read
  (a key restricted to 'default' saw only it, and its uninstall deleted another
  space's package policy), so the uninstall is refused, regardless of `force`.

stdlib only (no fastmcp/mcp/kibana) so this stays in `core`.
"""

from collections.abc import Iterable, Sequence

from kibana_mcp.core.models import FleetAgentTagChange


def _quoted(values: Iterable[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def missing_agents_message(missing: Sequence[str]) -> str:
    noun, it = ("agent id", "it") if len(missing) == 1 else ("agent ids", "them")
    return (
        f"no agent has {noun} {_quoted(missing)} — Kibana would skip {it} without an "
        "error; list_agents shows the enrolled agents' ids. Nothing was changed."
    )


def unknown_agent_message(agent_id: str) -> str:
    return f"agent '{agent_id}' not found — list_agents shows the enrolled agents' ids"


def tags_not_applied(
    changes: Iterable[FleetAgentTagChange], tags_to_add: Sequence[str], tags_to_remove: Sequence[str]
) -> tuple[str, ...]:
    """The agents whose read-back tags lack a tag to add or keep one to remove."""
    add, remove = set(tags_to_add), set(tags_to_remove)
    return tuple(
        c.agent_id for c in changes
        if not add <= set(c.tags) or remove & set(c.tags)
    )


def unknown_package_message(name: str) -> str:
    return (
        f"integration package '{name}' not found in the registry — list_packages "
        "shows the package names (e.g. 'nginx', 'tcp')"
    )


def unknown_version_message(name: str, version: str, latest: str) -> str:
    return (
        f"integration package '{name}' has no version '{version}' in the registry — "
        f"omit version to install the latest ({latest})"
    )


def outdated_version_message(name: str, version: str, latest: str) -> str:
    return (
        f"Kibana installs only the latest version of '{name}' ({latest}), not "
        f"'{version}' (it answers 400 out-of-date) — omit version to install {latest}"
    )


def installed_version_refusal(name: str, installed: str, requested: str) -> str:
    return (
        f"integration package '{name}' is installed at version {installed}, not "
        f"{requested}; install_package only installs a package that is not installed — "
        "an upgrade cannot be undone on a Basic license (Kibana's rollback needs "
        "Enterprise) and Kibana refuses an older version. Change the version in "
        "Kibana's Integrations UI, or uninstall_package it (which deletes its package "
        "policies) and install_package again."
    )


def not_installed_message(name: str) -> str:
    return (
        f"integration package '{name}' is not installed — list_installed_packages "
        "shows the installed packages"
    )


FLEET_SERVER_PACKAGE = "fleet_server"
AGENT_MONITORING_PACKAGE = "elastic_agent"
_FLEET_SERVER_POLICY = "a Fleet Server policy (that package policy runs its Fleet Servers)"
_UNATTACHED_FLEET_SERVER = "a fleet_server package policy on no agent policy"


def guard_reason(name: str, *, is_managed: bool, is_default_fleet_server: bool) -> str | None:
    """Why a package policy of package `name` on an agent policy with these flags
    may never be deleted by an uninstall, or None: Kibana-owned lifecycles, as
    `delete_agent_policy` guards them, and any `fleet_server` package policy —
    deleting one strands the Fleet Servers on its agent policy, default or not."""
    if is_managed:
        return "managed"
    if is_default_fleet_server:
        return "the default Fleet Server policy"
    if name == FLEET_SERVER_PACKAGE:
        return _FLEET_SERVER_POLICY
    return None


def unattached_guard_reason(name: str) -> str | None:
    """`guard_reason` for a package policy on no agent policy: `fleet_server`'s
    is guarded too, so every `fleet_server` package policy refuses the uninstall."""
    return _UNATTACHED_FLEET_SERVER if name == FLEET_SERVER_PACKAGE else None


def guarded_policy_refusal(name: str, guarded: Sequence[tuple[str, str, str]]) -> str:
    """`guarded`: (package policy id, agent policy id or "", why), one per guarded pair."""
    def where(pp_id: str, ap_id: str, why: str) -> str:
        if not ap_id:
            return f"its package policy '{pp_id}' is {why}"
        return f"its package policy '{pp_id}' is on agent policy '{ap_id}', which is {why}"

    listed = "; ".join(where(*g) for g in guarded)
    fix = ""
    if any(why in (_FLEET_SERVER_POLICY, _UNATTACHED_FLEET_SERVER) for _, _, why in guarded):
        fix = (" A fleet_server package policy on no agent policy, or on one that is neither "
               "managed nor the default Fleet Server policy, can be deleted on its own with "
               "delete_package_policy once no Fleet Server is enrolled on that agent policy.")
    return (
        f"refusing to uninstall integration package '{name}': {listed}, "
        "and Kibana deletes a package's package policies with it. This is refused "
        f"regardless of force.{fix}"
    )


def monitoring_refusal(name: str, agent_policy_ids: Sequence[str]) -> str:
    listed = _quoted(agent_policy_ids)
    noun = "agent policy" if len(agent_policy_ids) == 1 else "agent policies"
    return (
        f"refusing to uninstall integration package '{name}': agent monitoring is on in "
        f"{noun} {listed}, and the package holds the index templates and ingest pipelines "
        "of the agents' monitoring data streams (logs-elastic_agent*, metrics-elastic_agent*); "
        "Kibana uninstalls it without a check. This is refused regardless of force — turn "
        "monitoring off on each first with update_agent_policy monitoring_enabled=[] (a "
        "managed agent policy cannot be changed)."
    )


def space_awareness_refusal(name: str) -> str:
    """With Fleet space awareness on, Kibana's uninstall deletes the package's
    package policies in every space, while the spaces listing leaves out, with no
    error, each space the API key cannot read (probed on 9.5.4 with a key
    restricted to 'default': the listing showed only 'default', and Kibana's
    uninstall, no `force`, deleted a package policy in the other space). No
    Kibana API this server calls proves the key reads every space, so nothing it
    lists can be shown complete: refused whatever `force` says."""
    return (
        f"refusing to uninstall integration package '{name}': Fleet space awareness is on, "
        "so Kibana's uninstall deletes the package's package policies in every space, and "
        "this server cannot prove its API key reads every space (Kibana's spaces listing "
        "leaves out, without an error, each space the key cannot read), so it cannot show "
        "what would be deleted. This is refused regardless of force; nothing was changed. "
        f"Uninstall it in Kibana's Integrations UI ('{name}' → Settings) as a user whose role "
        "grants Fleet and Integrations in all spaces (spaces [\"*\"]), after reviewing its "
        "integration policies in each space."
    )


def package_policies_refusal(name: str, policies: Sequence[tuple[str, str, str, int]]) -> str:
    """`policies`: (package policy id, its name, agent policy id or "", that
    agent policy's agent count), one per package policy and agent policy."""
    def where(ap_id: str, agents: int) -> str:
        if not ap_id:
            return "on no agent policy"
        return f"on agent policy '{ap_id}' ({agents} agent{'' if agents == 1 else 's'})"

    listed = "; ".join(f"'{pp_id}' ('{pp_name}') {where(ap_id, agents)}"
                       for pp_id, pp_name, ap_id, agents in policies)
    count = len({pp_id for pp_id, *_ in policies})
    noun = "package policy" if count == 1 else "package policies"
    return (
        f"refusing to uninstall integration package '{name}': it has {count} {noun} — "
        f"{listed} — and Kibana deletes them with the package, so the agents on those "
        "agent policies stop collecting its data. Delete them first with "
        "delete_package_policy, or pass force=True to delete them with the package."
    )
