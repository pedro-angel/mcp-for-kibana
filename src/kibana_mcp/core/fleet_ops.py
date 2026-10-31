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
  with no agents went without `force`; one with an agent needed `force`), so
  the uninstall never sends one while any is left: a package still used by a
  package policy is refused, naming each one, its agent policy and space, and
  the agent policies it would leave with no integration (D64, owner);
- an uninstall of `elastic_agent` went through without `force` while an agent
  policy had monitoring on (it removed the monitoring data streams' index
  templates and ingest pipelines; Kibana has no check), so it is refused while
  any agent policy has `monitoring_enabled`;
- with Fleet space awareness on, each space lists only its own package
  policies and the spaces listing leaves out each space the API key cannot
  access. Up to 9.5.4 / 9.4.7 Kibana's uninstall deleted the package policies
  of those hidden spaces too; 9.5.5 and 9.4.8 answer 403 "... it is used in
  spaces you are not authorized to access" instead (probed on both; Fleet's
  uninstall_authz.ts, also on the 9.6 and main branches). So the uninstall
  reads every space the key lists, leaves the hidden ones to Kibana's 403, and
  is refused on a Kibana that lacks the check.

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
        "Kibana's Integrations UI, or uninstall_package it (once no package policy uses "
        "it) and install_package again."
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
        f"refusing to uninstall integration package '{name}': {listed}, and this "
        f"server never removes an integration from such an agent policy. Nothing was "
        f"changed.{fix}"
    )


def monitoring_refusal(name: str, agent_policy_ids: Sequence[str]) -> str:
    listed = _quoted(agent_policy_ids)
    noun = "agent policy" if len(agent_policy_ids) == 1 else "agent policies"
    return (
        f"refusing to uninstall integration package '{name}': agent monitoring is on in "
        f"{noun} {listed}, and the package holds the index templates and ingest pipelines "
        "of the agents' monitoring data streams (logs-elastic_agent*, metrics-elastic_agent*); "
        "Kibana uninstalls it without a check. Nothing was changed; turn monitoring off on "
        "each first with update_agent_policy monitoring_enabled=[] (a managed agent policy "
        "cannot be changed)."
    )


# The first patch of each line whose uninstall refuses (403) when the package is
# used in a space the API key cannot access (Fleet's uninstall_authz.ts: present
# at v9.4.8, v9.5.5 and on the 9.6 and main branches; absent at v9.4.7, v9.5.4).
_HIDDEN_SPACE_CHECK = {(9, 4): 8, (9, 5): 5}
_HIDDEN_SPACE_CHECK_FROM = (9, 6)


def checks_hidden_spaces(version: str) -> bool:
    """Whether Kibana `version` ("9.5.5", "9.6.0-SNAPSHOT") refuses an uninstall
    of a package used in a space the API key cannot access. Unparseable: False."""
    parts = version.split("-", 1)[0].split(".")
    try:
        major, minor, patch = (int(p) for p in parts[:3])
    except ValueError:
        return False
    if (major, minor) >= _HIDDEN_SPACE_CHECK_FROM:
        return True
    first = _HIDDEN_SPACE_CHECK.get((major, minor))
    return first is not None and patch >= first


def _integrations_ui(name: str) -> str:
    return (f"uninstall it in Kibana's Integrations UI ('{name}' → Settings) as a user whose "
            "role grants Fleet and Integrations in all spaces (spaces [\"*\"])")


def space_awareness_refusal(name: str, version: str) -> str:
    """Fleet space awareness on and a Kibana without the hidden-space check (or
    whose version could not be read): its uninstall deletes the package's
    package policies in spaces the key cannot see, so nothing listed is complete."""
    seen = f"Kibana {version}" if version else "this Kibana (its version could not be read)"
    return (
        f"refusing to uninstall integration package '{name}': Fleet space awareness is on, and "
        f"{seen} uninstalls a package's package policies in every space, including spaces this "
        "API key cannot access, which no listing shows; Kibana 9.5.5 and 9.4.8 refuse that "
        "instead. Nothing was changed. Upgrade Kibana to 9.5.5 or later (9.4.8 or later on "
        f"9.4), or {_integrations_ui(name)}."
    )


def pinned_space_refusal(name: str, pinned: str, others: Sequence[str]) -> str:
    """Space awareness on and KIBANA_URL pinned to one space while the key can
    access others: the server reads Fleet in the pinned space only, and Kibana's
    uninstall would remove the package's package policies in the others unseen."""
    noun = "space" if len(others) == 1 else "spaces"
    return (
        f"refusing to uninstall integration package '{name}': Fleet space awareness is on, "
        f"KIBANA_URL is pinned to space '{pinned}', and the API key can also access {noun} "
        f"{_quoted(others)}, whose package policies Kibana's uninstall would remove but this "
        "server cannot read through a pinned URL. Nothing was changed. Point KIBANA_URL at "
        f"the deployment's root URL (no '/s/<space>'), or {_integrations_ui(name)}."
    )


def hidden_spaces_refusal(name: str) -> str:
    """Kibana's 403 on the uninstall (9.5.5 / 9.4.8 and later): the package is
    still used in a space the API key cannot access."""
    return (
        f"refusing to uninstall integration package '{name}': Kibana answered 403 — it is "
        "still used in spaces this API key cannot access, so this server cannot list those "
        "package policies. Nothing was changed. Remove them in Kibana's Fleet UI in each of "
        "those spaces (or with an API key that can access them), then call uninstall_package "
        f"again; or {_integrations_ui(name)}."
    )


def package_policies_refusal(
    name: str,
    policies: Sequence[tuple[str, str, str, str, int]],
    emptied: Sequence[tuple[str, str]],
    current_space: str,
) -> str:
    """`policies`: (package policy id, its name, space or "" when spaces do not
    apply, agent policy id or "", that agent policy's agent count), one per
    package policy and agent policy. `emptied`: (space or "", agent policy id)
    of each agent policy left with no integration once these are removed.
    `current_space`: the space the fleet tools act in."""
    def where(space: str, ap_id: str, agents: int) -> str:
        at = f" in space '{space}'" if space else ""
        if not ap_id:
            return f"on no agent policy{at}"
        return f"on agent policy '{ap_id}'{at} ({agents} agent{'' if agents == 1 else 's'})"

    listed = "; ".join(f"'{pp_id}' ('{pp_name}') {where(space, ap_id, agents)}"
                       for pp_id, pp_name, space, ap_id, agents in policies)
    count = len({pp_id for pp_id, *_ in policies})
    noun = "package policy" if count == 1 else "package policies"
    elsewhere = sorted({space for _, _, space, _, _ in policies if space and space != current_space})
    how = (f"delete_package_policy removes each one in space '{current_space}'"
           if elsewhere else "delete_package_policy removes each one")
    if elsewhere:
        how += (f"; this server's fleet tools act in space '{current_space}' only, so remove "
                f"those in {_quoted(elsewhere)} in Kibana's Fleet UI in that space")
    text = (
        f"refusing to uninstall integration package '{name}': it is still used by {count} "
        f"{noun} — {listed} — and this server never removes an integration from agent "
        "policies as part of an uninstall. Nothing was changed. Remove the integration from "
        f"them first ({how}; the agents on those agent policies stop collecting its data), "
        "then call uninstall_package again."
    )
    if emptied:
        aps = ", ".join(f"'{ap}'" + (f" (space '{sp}')" if sp else "") for sp, ap in emptied)
        verb = "would be" if len(emptied) == 1 else "would each be"
        text += (f" Agent polic{'y' if len(emptied) == 1 else 'ies'} {aps} {verb} left with no "
                 "integration: delete_agent_policy removes it if it is no longer wanted.")
    return text
