# fleet — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#fleet-toolbox · Enforcement: named per section below

## Purpose & persona

Read, configure, and operate Fleet — the Elastic Agent fleet, agent and
integration (package) policies, enrolled agents, integrations (EPM), and
outputs — all GA on a Basic license. Serves the `fleet-admin` profile's sole
persona: a platform/fleet admin managing agent and integration state at
scale, running this toolbox alone at the `destructive` tier because the job
routinely reassigns, upgrades, and unenrolls live agents. No other shipped
profile enables this toolbox.

## Surface

Thirty-five tools: 20 `read`, 6 `write`, 9 `destructive`. Full per-tool
listing: docs/tools.md#fleet-toolbox. At the default `write` tier the nine
`destructive` tools are registered internally but hidden from the
advertised tool list. Every tool that commands a running enrolled agent
(`reassign_agent`, `upgrade_agent`, `unenroll_agent`, their bulk
equivalents) sits at `destructive` tier, not `write`, even though none
deletes a Kibana object — changing what a live agent collects/ships,
swapping its binary, or unenrolling it is destructive to infrastructure, not
to a saved object. Annotations follow docs/tools.md#tiers-and-annotations
(`read`→`readOnlyHint=True`; `write`→`destructiveHint=False,
idempotentHint=False`; `destructive`→`destructiveHint=True,
idempotentHint=True`; all three→`openWorldHint=False`).

## Behavioral guarantees

**No space targeting.** Unlike dashboards, data-management, alerting,
cases, and security-detections, no fleet tool accepts a `space` parameter —
no echo, no fail-closed validation, no scoped not-found suffix
(docs/tools.md#space-targeting). `get_fleet_settings`'s
`space_awareness_migration_status` field is Fleet's own internal
per-deployment migration state, informational only, not the same axis.

**Secret redaction is a fixed field allowlist, not after-the-fact
stripping.** Enrollment-key/uninstall-token reads and every output
read/write return exactly the field sets in Invariants below, regardless of
what Kibana's own response body carries; uninstall-token values are never
fetched at all, not fetched-then-dropped.

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

**Agent-lifecycle results are async; return shape differs by arity.**
Single-agent lifecycle tools return once Kibana *accepts* the command, not
once the agent applies it (`upgrade_agent` queues an async binary swap on
the agent's own schedule); bulk equivalents return only an `action_id`.
Destructive delete tools return a confirmation dict, never the deleted
object's body.

## Invariants

- Registers exactly 20 read + 6 write + 9 destructive tools (35 total),
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

## Deliberate exclusions & caveats

- **Enrollment-key minting/revocation is entirely out of scope** — the only
  secret-minting surface this toolbox would otherwise touch. Recorded
  reason: unsafe under this server's API-key auth model (a minted key
  passed inline leaks into model context; writing it to disk instead breaks
  the HTTP transport mode). Deferred pending a vetted credential-handling
  design, not merely unbuilt.
- **EPM package install/uninstall are out of scope**, tracked in the
  unordered Basic-license backlog with package diagnostics/tags/the
  Kubernetes-manifest endpoint — reachable on the target license, just not
  yet built.
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
  uninstall-token allowlists at the tool boundary.
- `tests/unit/adapters/test_fleet_gateway.py` — RMW body construction and
  read-only-field stripping; every reserved/default guard raises before
  the mutating call is sent; secret stripping on output create; bulk tools
  return the action id.
- `tests/contract/test_fleet_contract.py` — a dedicated fleet suite,
  distinct from `tests/contract/test_gateway_contract.py` (different
  toolbox set, no fleet cases) — live shape assertions against a real
  Kibana with an always-on Fleet Server and demo agent: secret redaction
  verified live, CRUD round-trips, default-policy/default-output refusal
  tests assert the guarded object survives.
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
