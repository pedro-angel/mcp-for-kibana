# fleet — behavior contract

Status: Draft v1.5 (2026-10-25) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#fleet-toolbox · Enforcement: named per section below

## Purpose & persona

Read, configure, and operate Fleet — the Elastic Agent fleet, agent and
integration (package) policies, enrolled agents (their tags and diagnostics
bundles), integrations (EPM, installed and uninstalled), and outputs — all GA
on a Basic license. Serves the `fleet-admin` profile's sole
persona: a platform/fleet admin managing agent and integration state at
scale, running this toolbox alone at the `destructive` tier because the job
routinely reassigns, upgrades, and unenrolls live agents. No other shipped
profile enables this toolbox.

## Surface

Forty-one tools: 22 `read`, 9 `write`, 10 `destructive`. Full per-tool
listing: docs/tools.md#fleet-toolbox. At the default `write` tier the ten
`destructive` tools are registered internally but hidden from the
advertised tool list. Every tool that changes what a running enrolled agent
does (`reassign_agent`, `upgrade_agent`, `unenroll_agent`, their bulk
equivalents) sits at `destructive` tier, not `write`, even though none
deletes a Kibana object — changing what a live agent collects/ships,
swapping its binary, or unenrolling it is destructive to infrastructure, not
to a saved object. `request_agent_diagnostics` sends a live agent an action
too, but sits at `write`: the agent collects and uploads a bundle and nothing
it collects or ships changes (D51). `uninstall_package` is `destructive`
because Kibana deletes the package's package policies with it.
Annotations follow docs/tools.md#tiers-and-annotations
(`read`→`readOnlyHint=True`; `write`→`destructiveHint=False,
idempotentHint=False`, except `install_package` and `update_agent_tags`,
whose repeat leaves the same state (`idempotentHint=True`);
`destructive`→`destructiveHint=True, idempotentHint=True`; all
three→`openWorldHint=False`).

## Behavioral guarantees

**No space targeting.** Unlike dashboards, data-management, alerting,
cases, and security-detections, no fleet tool accepts a `space` parameter —
no echo, no fail-closed validation, no scoped not-found suffix
(docs/tools.md#space-targeting). `get_fleet_settings`'s
`space_awareness_migration_status` field is Fleet's own internal
per-deployment migration state, not the same axis. Once that migration has
run (the dev stack reads `success`), Fleet's agent and package policy
listings return only the calling space's (probed on 9.5.4), so the other
fleet tools see the default space's (or the pinned space's) policies only;
`uninstall_package`, whose Kibana call reaches every space, is refused then
(below).

**Secret redaction is a fixed field allowlist, not after-the-fact
stripping.** Enrollment-key/uninstall-token reads and every output
read/write return exactly the field sets in Invariants below, regardless of
what Kibana's own response body carries; uninstall-token values are never
fetched at all, not fetched-then-dropped. The raw saved-objects route is not
a way around it: data-management's `get_saved_object` reads only analytics
content types and refuses Fleet's (`ingest-outputs`, `fleet-proxy`, …),
whose plain attributes carry `config_yaml`, service tokens, certificate
keys and proxy headers (D48).

**Updates are one read-modify-write gateway operation per tool.**
`update_agent_policy`, `update_package_policy`, and `update_output` each
correspond to a single port operation (`ports/gateway.py`) that reads
current state, merges in only the caller's non-null fields against a
writable-field allowlist, and re-sends the merged body — computed fields
(revision, timestamps, status, compiled input/stream, …) are dropped rather
than round-tripped, and the read-then-write is never split across two
gateway calls. `description=""` clears a description;
`monitoring_enabled=[]` turns monitoring off — both differ from omitting
the field.

**Reserved/default guards read fresh state at call time, before the
mutation, and leave the target unmodified when they reject.**
`reassign_agent`/`bulk_reassign` guard the **target** policy;
`upgrade_agent`/`unenroll_agent` have no target policy and carry no
equivalent guard. Promoting an output auto-un-defaults the prior default,
which then becomes deletable.

**Bulk agent-lifecycle tools require explicit ids and confirm, never a
sweep** — the fleet-wide kuery-filter form Kibana's own bulk API also
supports is never exposed. The empty-list and missing-confirm checks run
before the gateway is even acquired, one layer earlier than the
reserved/default guards above.

**Agent tags are edited on explicit ids, read before and after (D51).**
`update_agent_tags` takes 1–100 agent ids (never a kuery) and at least one tag
to add or remove, none in both lists; the empty, overlapping and oversized
cases are refused before the gateway is acquired. It reads every agent first
and refuses the call when an id names no agent — Kibana answers 200 and skips
it (probed) — then reads each agent back after the call (Kibana applies an
id-list update before it answers, probed): the result's `previous_tags` and
`tags` are the change, and an agent the update did not reach is in
`not_applied_agent_ids`. Tags are Fleet-side labels (an offline agent was
tagged at once), so no agent policy or data changes.

**Diagnostics are requested, then watched; the bundle never reaches the
model (D51).** `request_agent_diagnostics` reads the agent (status, and a
not-found naming `list_agents`) and returns Kibana's `action_id` once it
accepts the request; `list_agent_diagnostics` reports each bundle's status and
`action_id`, never its download path or content (the bundle holds the agent's
logs and configuration; it is downloaded in Kibana's UI). `include_cpu_metrics`
sends `additional_metrics=["CPU"]`, the only value Kibana accepts (400
otherwise, probed); that bundle took ~66 s on the demo agent, the plain one
~6 s, and the agent takes one request a minute (a second one within it ended
`FAILED`, "rate limit exceeded", probed).

**Package install never changes an installed version; uninstall names what
it deletes (D51).** `install_package` reads the package first: an unknown name
names `list_packages`; a package installed at the requested version, or at any
version when none is given, is returned with `already_installed=true` and
nothing sent (Kibana's install without a version upgrades it, probed); one
installed at another version is refused, because the rollback route needs an
Enterprise license (403 on Basic, probed) and Kibana refuses older versions.
`uninstall_package` reads the package and every package policy of it: a
package that is not installed names `list_installed_packages`; a package
policy on a managed or the default Fleet Server agent policy refuses the call
regardless of `force` (as `delete_agent_policy` guards those policies), and so
does any `fleet_server` package policy, on whatever agent policy or none (it
runs the Fleet Servers there; the refusal names every guarded one, and for a
plain or unattached one `delete_package_policy` once no Fleet Server is
enrolled on it); any
other package policy refuses it unless `force=True`, naming each one with its
agent policy and agent count, because Kibana deletes them with the package
(probed: without `force` when no agent uses them, with it otherwise), and
Kibana's `force` is sent only then. `elastic_agent` is refused, whatever
`force` says, while any agent policy has `monitoring_enabled`: it holds the
index templates and ingest pipelines of the agents' monitoring data streams,
and Kibana uninstalled it under a monitoring agent policy without `force` or a
check (probed); the refusal names those agent policies and
`update_agent_policy monitoring_enabled=[]`. **With Fleet space awareness on
(`use_space_awareness_migration_status` set to any value), every uninstall
is refused, whatever `force` says, before any policy is read** (fail
closed): each space lists only its own agent and package policies, Kibana's
uninstall deletes every space's package policies (probed: read by id after
it), and Kibana's spaces listing leaves out, without an error, each space
the API key cannot read — a key limited to the default space listed only it,
and Kibana's uninstall with that key, no `force`, deleted a package policy
in a space it was never shown (probed). No Kibana route the server calls
proves a key reads every space (the package `stats` and
`withPackagePoliciesCount` counts are space-scoped as well, probed), so no
listing is provably complete; the refusal names Kibana's Integrations UI, as
a user whose role grants Fleet and Integrations in all spaces. The checks
above therefore run with awareness off, where one listing holds every
space's policies. The
managed `logs-*`/`metrics-*` data views an install may add to every space are
kept (probed with `nginx`). The result lists the package policies read before the call; one created between
that read and the uninstall is deleted unreported (the route has no
conditional form).

**Agent-lifecycle results are async; return shape differs by arity.**
Single-agent lifecycle tools return once Kibana *accepts* the command, not
once the agent applies it (`upgrade_agent` queues an async binary swap on
the agent's own schedule); bulk equivalents return only an `action_id`.
Destructive delete tools return a confirmation dict, never the deleted
object's body.

## Invariants

- Registers exactly 22 read + 9 write + 10 destructive tools (41 total),
  tagged `{"fleet", <tier>}`; `KIBANA_MCP_TIER` hides `write`/`destructive`
  tools from the advertised list, not merely rejects calls to them.
- No tool in this toolbox accepts or echoes a `space` parameter.
- `list_enrollment_keys`/`get_enrollment_key` return exactly
  `{id, name, policy_id, active, created_at}` — never `api_key`/`api_key_id`.
- `list_uninstall_tokens` returns exactly
  `{id, policy_id, policy_name, created_at}` — never a token value.
- Every output shape returns exactly
  `{id, name, type, hosts, is_default, is_default_monitoring}`; no tool
  accepts or returns `ssl`/`secrets`/`config_yaml`.
- `update_agent_policy`/`update_package_policy`/`update_output` are
  read-modify-write through one gateway operation each: an omitted field is
  preserved, never reset.
- `update_agent_policy` refuses a managed policy (it takes no `force`
  parameter); `delete_agent_policy` refuses a managed policy and the
  default Fleet Server policy regardless of `force`.
- `reassign_agent`/`bulk_reassign` refuse a managed or default-Fleet-Server
  target policy; `upgrade_agent`/`unenroll_agent` carry no such guard.
- `delete_output` refuses a default output with no `force` escape;
  `update_output` on a default output requires `confirm=True`.
- `bulk_reassign`/`bulk_upgrade`/`bulk_unenroll` reject an empty
  `agent_ids` list or a missing `confirm=True` before calling the gateway;
  none accepts a kuery filter in place of explicit ids.
- Single agent-lifecycle tools return `{"ok": true, "agent_id": ...}`; bulk
  tools return `{"action_id": ...}` only — neither blocks on the agent
  actually applying the command.
- The list tools whose Kibana endpoints paginate — `list_agents`,
  `list_agent_policies`, `list_package_policies`, `list_enrollment_keys`,
  `list_uninstall_tokens` (page walk) and `list_installed_packages`
  (cursor walk) — return the complete collection in one call; the
  remaining list tools wrap single-response endpoints.
  `list_agent_policies`/`get_agent_policy` report the real assigned
  `agent_count`, never a placeholder zero.
- No tool exists to mint or revoke an enrollment API key.
- `update_agent_tags` sends nothing when an agent id names no agent, and
  returns every agent's tags as read before and after the call;
  `not_applied_agent_ids` is exactly the agents whose read-back tags lack a
  tag to add or keep a tag to remove.
- `install_package` never sends an install for a package that is installed;
  a different requested version is refused.
- `uninstall_package` never sends an uninstall when the package has a package
  policy on a managed or the default Fleet Server agent policy, when it is
  `fleet_server` with any package policy (on any agent policy or none), when
  it is `elastic_agent` while an agent policy has `monitoring_enabled`, nor,
  without `force=True`, when it has any package policy, nor, whatever
  `force` says, while Fleet space awareness is on (no listing it can read
  proves complete); it sends Kibana's `force` only for a package with package
  policies.
- `list_agent_diagnostics` returns exactly
  `{id, name, status, created_at, action_id}` per bundle — no download path.

## Deliberate exclusions & caveats

- **Enrollment-key minting/revocation is entirely out of scope** — the only
  secret-minting surface this toolbox would otherwise touch. Recorded
  reason: unsafe under this server's API-key auth model (a minted key
  passed inline leaks into model context; writing it to disk instead breaks
  the HTTP transport mode). Deferred pending a vetted credential-handling
  design, not merely unbuilt.
- **Package upgrades and rollbacks are out of scope** (D51): an upgrade cannot
  be undone on Basic (the rollback route answers 403, Enterprise only), so
  `install_package` installs only a package that is not installed. Uploading a
  package archive, the Kubernetes-manifest endpoint and downloading a
  diagnostics bundle are not wrapped (roadmap).
- **Cloud-only surfaces are out of scope for a different reason**:
  agentless policies, cloud connectors, and proxies exist only on Elastic
  Cloud, outside this project's Basic/self-managed baseline.
- **Registry-backed reads are unbounded**: `list_packages` proxies the full
  Elastic Package Registry catalog with no filtering exposed.

## Enforcement

- `tests/unit/toolboxes/test_fleet.py` — tool count/tier membership,
  annotation directions, bulk empty/missing-confirm rejection,
  `update_output`'s `confirm` forwarded and excluded from `changes`,
  `update_package_policy` forwards real fields, enrollment-key/
  uninstall-token allowlists at the tool boundary; the D51 tools' annotations,
  tag-list checks before the gateway, round trips and refusals.
- `tests/unit/adapters/test_fleet_gateway.py` — RMW body construction and
  read-only-field stripping; every reserved/default guard raises before
  the mutating call is sent; secret stripping on output create; bulk tools
  return the action id; D51: the tag pre-read, read-back and not-applied
  report, the diagnostics status read and CPU option, the install's
  installed-version and unknown name/version/out-of-date paths, the
  uninstall's package-policy, guarded-policy (a plain Fleet Server policy
  included, every guarded one named) and agent-monitoring refusals (nothing
  sent), `force` withheld from a package without package policies, and the
  space-awareness refusal whatever the key's spaces listing shows (one space,
  a pinned one, several) or `force` says, with no policy read.
  `tests/unit/test_fleet_ops_core.py` pins the shared guidance text.
- `tests/contract/test_fleet_contract.py` — a dedicated fleet suite,
  distinct from `tests/contract/test_gateway_contract.py` (different
  toolbox set, no fleet cases) — live shape assertions against a real
  Kibana with an always-on Fleet Server and demo agent: secret redaction
  verified live, CRUD round-trips, default-policy/default-output refusal
  tests assert the guarded object survives. D51: a tag added to and removed
  from the demo agent (and Kibana's 200 for an unknown id), a diagnostics
  bundle uploaded to READY then deleted, `tcp` installed, refused at another
  version (and the rollback route's 403), refused for uninstall while a
  package policy exists, uninstalled with `force` (its package policy gone),
  Kibana's own deletion of a package policy on a plain uninstall,
  `fleet_server`'s uninstall refused without force naming both the default
  Fleet Server policy and a plain one carrying a `fleet_server` package
  policy, `elastic_agent`'s refused with `force` under a monitoring agent
  policy (and sent once none monitors), and a CPU-metrics diagnostics bundle
  uploaded to READY before the plain one. With Fleet space awareness on (the
  dev stack), the uninstalls are refused for awareness instead (`tcp` with
  and without `force`, `fleet_server` without; the `elastic_agent` case is
  skipped), and a
  throwaway API key limited to the default space (minted with the stack's
  `elastic` password from `elastic-start-local/.env`, invalidated after) is
  shown a spaces listing without a throwaway space holding a `tcp` package
  policy, both gateways refuse, and Kibana's own uninstall with that key
  deletes that package policy (read by id).
- `tests/ephemeral/test_fleet_ephemeral.py`, gated by the required
  `fleet_ephemeral` Definition-of-Done criterion (`dod.config`) — the only
  tier exercising `reassign_agent`/`upgrade_agent`/`unenroll_agent` and
  their bulk equivalents against real enrolled agents, on an isolated
  throwaway stack; also proves output-promotion auto-un-defaulting.
- Not covered by the e2e-replay recorded-transcript tier
  (`tests/e2e_replay/`): its four transcripts cover dashboards and
  alerting only. `tests/unit/toolboxes/test_space_threading.py` excludes
  fleet, consistent with "no space parameter" above.
- `tests/unit/test_server_assembly.py` — the tier-visibility invariant for
  this toolbox specifically: the advertised tool set at read/write/
  destructive tiers, via the server's real disable mechanism.
