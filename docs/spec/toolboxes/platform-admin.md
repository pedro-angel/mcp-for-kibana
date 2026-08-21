# platform-admin — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#platform-admin-toolbox · Enforcement: named per section below

## Purpose & persona

Administers the core Kibana objects every other toolbox's `space` parameter routes into — Spaces
and security Roles — plus a read of Upgrade Assistant readiness, all on a Basic license. Backs the
planned `platform-admin` profile (paired with `data-management`, destructive tier, an admin-scoped
API key over HTTP): a deployment administrator provisioning spaces and RBAC roles for other
personas, and checking upgrade readiness before a version bump. The one toolbox that manages the
multi-tenancy/access-control substrate itself, rather than doing space-scoped work within it.

## Surface

Ten tools across three tiers, tagged `{"platform-admin", <tier>}`, with the same
`_READ`/`_WRITE`/`_DESTRUCTIVE` annotation shapes as the shared
[Tiers and annotations](../../tools.md#tiers-and-annotations) reference:

- **read** (5, every tier): `list_spaces`, `get_space`, `list_roles`, `get_role`, `get_upgrade_status`.
- **write** (3, default `write` tier+): `create_space`, `update_space`, `create_or_update_role`.
- **destructive** (2, `KIBANA_MCP_TIER=destructive` only): `delete_space`, `delete_role`.

A tier includes everything below it; the cap hides tools above it from the tool list, not merely
at call time. Full per-tool arguments and return shapes:
[docs/tools.md#platform-admin-toolbox](../../tools.md#platform-admin-toolbox).

## Behavioral guarantees

- **Not space-targeted.** None of these ten tools accept the `space` parameter that 63 tools
  elsewhere carry — spaces and roles are the instance-global objects that parameter routes
  *into*, so a space echo in results here would be circular. This is the deliberate negative case of
  the space-targeting contract, not an oversight.
- **Cross-toolbox guidance dependency.** When a space-targeting tool elsewhere rejects a
  nonexistent space id, its guidance names `list_spaces` and `create_space` by exact tool name as
  the fix — those two names are load-bearing for that *other* contract's product text too.
- **Guard-before-gateway ordering.** `create_or_update_role` rejects an empty grant set (no
  `cluster_privileges`/`index_privileges`/`kibana_base`) and a `kibana_spaces` argument without
  `kibana_base`, before any call reaches the gateway port (`ports/gateway.py`) — zero network I/O
  on a rejected call. `delete_space`/`delete_role` refuse their reserved-object case (default/any
  reserved space; any reserved role) before the mutating request, and that refusal is
  unconditional — `force=True` and `create_only=False` never bypass it.
- **Read-modify-write behind one port call.** `update_space` lets the caller pass only changed
  fields; the gateway port's `update_space` re-sends every omitted field unchanged (color,
  initials, disabled_features, solution all survive a name-only update) inside that single call —
  never a separate read followed by a separate write from the toolbox's side.
- **`create_or_update_role` is a full replace once it proceeds.** `create_only` defaults `True`,
  so a bare call against an existing role errors instead of dropping its grants; `create_only=False`
  deliberately overwrites, and still drops every grant the call omits — no partial-grant merge.
- **`delete_space`'s `force` flag is the only per-call escape hatch here.** No reliable per-space
  object count exists to gate on instead (see exclusions), so the whole-space wipe always demands
  `force=True`. `delete_role` carries no equivalent flag — the destructive tier gate alone confirms it.
- **Roles read as configuration, never credential material.** `list_roles`/`get_role` return
  privilege grants (ES cluster + index, `run_as`, per-space Kibana base/feature) summarized to
  "who can do what where," per-feature lists collapsed to sorted feature names — no secret value.
- **`get_upgrade_status` is a live, non-deterministic snapshot.** The ES deprecation-log count
  varies with cluster usage and each raw entry's message embeds a live timestamp, dropped; only
  the stable `title`/`level`/`type` triple is surfaced per deprecated API.
- **Bare arrays, no pagination.** `list_spaces`/`list_roles` return the complete set in one call.

## Invariants

- MUST NOT accept a `space` parameter on any of the 10 tools.
- MUST keep `list_spaces` and `create_space` as the exact names other toolboxes' guidance text
  for a nonexistent space references.
- MUST reject a `create_or_update_role` call that grants nothing, before any gateway call.
- MUST reject a `kibana_spaces` argument unaccompanied by `kibana_base`, before any gateway call.
- MUST refuse to delete or full-replace the default space, any reserved space, or any reserved
  role, regardless of `force` or `create_only`.
- MUST require `force=True` on `delete_space`; MUST refuse without it even for a non-reserved space.
- MUST default `create_only=True` on `create_or_update_role` so a bare call on an existing role fails closed.
- MUST preserve every field the caller omits on `update_space` rather than resetting it.
- MUST keep a space's `id` immutable after `create_space` — `update_space` cannot change it.
- MUST summarize role privileges (feature names collapsed, sorted), never the raw per-feature tree.
- MUST NOT return a secret value from any read in this toolbox.
- MUST NOT pin an exact deprecation count or a live timestamp as an expected value from `get_upgrade_status`.

## Deliberate exclusions & caveats

- **Logstash pipeline management** is filed and split out as its own follow-up, blocked on
  Platinum licensing — confirmed `403` live on Basic, not merely undocumented.
- **Session invalidation, space object copy/move (`spaces.copy_saved_objects` /
  `update_objects_spaces`), feature-level role grants, and avatar-image editing** are out of
  scope. The space-copy/move pair is unfiled backlog (a natural extension of space targeting);
  the feature-level grants and avatar editing are recorded non-goals with no filed follow-up.
- **Roles surface configuration only, by design.** No write here can grant a role more than the
  calling API key's own privileges allow — that boundary is Kibana's, not this server's.
- **`delete_space` has no reliable per-space object count to check before wiping** — the count
  API needs an explicit type list and is deprecated on the target Kibana version — hence the
  unconditional `force=True` instead of a fail-open estimate.
- **`get_upgrade_status` readiness and deprecation counts reflect live cluster state**, not a
  fixed value; a rewrite must not treat any exact count as a contract.

## Enforcement

- **Unit** (fakes) — `tests/unit/toolboxes/test_platform_admin.py`: tier membership + annotation
  hints for all 10 tools; `create_space` id-pattern rejection; `update_space` field-preservation
  on a partial call; `create_or_update_role`'s empty-grant and `kibana_spaces`-without-`kibana_base`
  guards, its `IndexPrivilege`→dict round-trip, `create_only`-defaults-True rejection of an
  existing (`kibana_system`) role, and that `create_only=False` still refuses a reserved role;
  `delete_space`'s default-space refusal and force requirement; `delete_role`'s reserved-role
  refusal; shape assertions (a missing space/role surfacing as a tool error, blank-id/name rejection) per read tool — the HTTP-level 404 mapping is pinned by the contract tier below, not here.
- **Unit negative control** — `tests/unit/toolboxes/test_space_threading.py` registers
  platform-admin alongside the five space-aware toolboxes and asserts none of its 10 tools gained
  a `space` property in their input schema — the deliberate "outside" half of that contract.
- **Contract** (live Kibana 9.4.3) — `tests/contract/test_gateway_contract.py`: `list_spaces`
  contains the reserved default space; `get_space`/`get_role` 404 on a missing id; `list_roles`
  contains reserved `kibana_system`; `get_upgrade_status` shape (never exact counts); a
  create→update→delete round-trip on a uuid-suffixed space asserting `update_space` preserves
  `disabled_features` set at create time; a `create_or_update_role` ES+Kibana privilege round-trip
  plus its `create_only=True`-on-existing rejection; `delete_space`/`delete_role` guard rejections
  on `default`/`kibana_system`, incl. `create_only=False` still refusing `kibana_system`; an
  MCP-tool-level (not just gateway-level) `IndexPrivilege` mapping round-trip via a live client.
- **e2e-replay** — `create_space` is the enabling call three transcripts depend on
  (`space-dashboard.json`, `space-dashboard-gemma-4-12b-qat.json`,
  `alerting-space-recovery-gemma-4-12b-qat.json`); each supplies `platform-admin` in
  `KIBANA_MCP_TOOLBOXES` so `create_space` is registered before the transcript's first step runs,
  and its recorded response shape is replayed and asserted like every other pinned call.
- **Docs drift guard** — `tests/unit/test_docs_tool_reference.py` asserts every one of these 10
  registered tool names appears in `docs/tools.md`.
- **DoD gates** (`dod.config`) — `unit_green`, `contract_green`, `e2e_replay_green` all cover this
  toolbox; no dedicated ephemeral-stack criterion — destructive paths (`delete_space`,
  `delete_role`) are proven against uuid-suffixed, self-cleaned objects on the shared dev stack.
