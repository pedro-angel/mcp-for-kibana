# platform-admin — behavior contract

Status: Draft v1.6 (2026-10-25) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#platform-admin-toolbox · Enforcement: named per section below

## Purpose & persona

Administers the core Kibana objects every other toolbox's `space` parameter routes into — Spaces
and security Roles — moves saved objects between spaces (copy, share; D49), plus a read of Upgrade
Assistant readiness, all on a Basic license. Backs the
planned `platform-admin` profile (paired with `data-management`, destructive tier, an admin-scoped
API key over HTTP): a deployment administrator provisioning spaces and RBAC roles for other
personas, and checking upgrade readiness before a version bump. The one toolbox that manages the
multi-tenancy/access-control substrate itself, rather than doing space-scoped work within it.

## Surface

Fourteen tools across three tiers, tagged `{"platform-admin", <tier>}`, with the same
`_READ`/`_WRITE`/`_DESTRUCTIVE` annotation shapes as the shared
[Tiers and annotations](../../tools.md#tiers-and-annotations) reference
(`share_saved_objects_to_spaces` is the one write with `idempotentHint=true`):

- **read** (5, every tier): `list_spaces`, `get_space`, `list_roles`, `get_role`, `get_upgrade_status`.
- **write** (5, default `write` tier+): `create_space`, `update_space`, `create_or_update_role`,
  `copy_saved_objects_to_spaces`, `share_saved_objects_to_spaces`.
- **destructive** (4, `KIBANA_MCP_TIER=destructive` only): `delete_space`, `delete_role`,
  `overwrite_saved_objects_in_spaces`, `unshare_saved_objects_from_spaces`.

A tier includes everything below it; the cap hides tools above it from the tool list, not merely
at call time. Full per-tool arguments and return shapes:
[docs/tools.md#platform-admin-toolbox](../../tools.md#platform-admin-toolbox).

## Behavioral guarantees

- **Not space-targeted.** None of these fourteen tools accept the `space` parameter that 86 tools
  elsewhere carry — spaces and roles are the instance-global objects that parameter routes
  *into*, so a space echo in results here would be circular. This is the deliberate negative case of
  the space-targeting contract, not an oversight. The four saved-objects tools name their spaces
  as arguments of the operation instead — `source_space` (where the objects are read; default the
  default space, or the space a `/s/<id>` `KIBANA_URL` pins, where `source_space` is refused; it is
  refused too when only the public URL is pinned, and the source is then the default space — so
  no guidance offers it on either deployment, and the guidance names the URL that fixes the
  source instead),
  `target_spaces` (copy, overwrite) and `spaces` (share, unshare) — validated by the same id
  grammar (`*` allowed in `spaces`), and echo `source_space` only when it was passed (D7's rule).
- **Copy makes new objects; overwrite replaces earlier copies; share adds; unshare removes (D49).**
  `copy_saved_objects_to_spaces` always sends `createNewCopies` (new ids, nothing existing
  touched); `overwrite_saved_objects_in_spaces` sends `overwrite` without it, replacing the
  object an earlier copy made in each target (Kibana matches it by origin) — the edit made there
  is lost, hence destructive. `include_references` defaults `true` in both (Kibana's default is
  false, under which a dashboard copied alone fails `missing_references`). The share only adds
  spaces and a repeat adds none; the unshare only removes them, and Kibana deletes an object left
  in no space.
- **Every space written into exists first (D5).** The copy, overwrite and share check each target
  space id (except `*`) against the space list before writing: Kibana writes into an id that names
  no space without an error, and the object later appears in a space created with that id
  (probed). The unshare writes nothing new and does not check.
- **Read before the share and unshare write.** Both read each object's spaces from the source space
  (`_get_shareable_references`) and refuse, before any change: an object the source space does
  not hold (`isMissing`), a type Kibana keeps in one space (`spaces: []` — dashboards,
  visualizations, saved searches, maps, tags, config on 9.5.4), a removal that removes nothing,
  and, unless `force=True`, a removal that leaves an object in no space. The read and the write are
  not atomic.
- **Per-object results, never a silent partial.** The copy tools return one result per target
  space with `copied` (source `id`, `destination_id`, `overwritten`), `failed` (Kibana's error type
  and a `fix`; nothing written for it) and `unchanged` (requested, neither copied nor failed:
  already in that space by sharing); a target space Kibana refused as a whole (one bare error, no
  object) fails every requested object not otherwise reported there with `space_error`, while the
  other targets keep their results (on a 403 the fix asks for a new API key (sent as `Authorization: ApiKey <key>` or set in `KIBANA_API_KEY`, the latter ignored in HTTP mode by default): a key keeps
  its owner's privileges from when it was created, so a role change does not reach it); the sharing tools return each object's `spaces` after the
  call with `added`/`removed`/`deleted`, and Kibana's per-object `error` when one object failed
  while the others were updated.
- **The source space is refused as a copy target.** Copying into it would duplicate the objects
  in place and overwriting into it changes nothing (probed), so both tools refuse it with the fix.
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

- MUST NOT accept a `space` parameter on any of the 14 tools; the saved-objects tools take
  `source_space`/`target_spaces`/`spaces` instead.
- MUST check every space a copy, overwrite or share writes into exists (except `*`), before writing.
- MUST NOT make `copy_saved_objects_to_spaces` overwrite anything: it always creates new copies.
- MUST refuse, before any write, an unshare that would leave an object in no space unless
  `force=True`, an unshare that removes nothing, and a share or unshare of a missing object or of
  a type kept in one space.
- MUST refuse the source space as a copy or overwrite target.
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

- **Logstash pipeline management is out of scope.** Platinum-gated — confirmed `403` live
  on Basic, not merely undocumented — and subscription-gated surfaces are not built here.
- **Session invalidation, feature-level role grants, and avatar-image editing** are out of
  scope: recorded non-goals with no filed follow-up.
- **Saved objects between spaces leave out** (D49, roadmap): resolving copy errors one by one
  (`_resolve_copy_saved_objects_errors` — choosing a destination for an `ambiguous_conflict`,
  ignoring missing references), `compatibilityMode`, disabling legacy URL aliases, and following
  related objects on a share (the share moves exactly the objects named).
- **Roles surface configuration only, by design.** No write here can grant a role more than the
  calling API key's own privileges allow — that boundary is Kibana's, not this server's.
- **`delete_space` has no reliable per-space object count to check before wiping** — the count
  API needs an explicit type list and is deprecated on the target Kibana version — hence the
  unconditional `force=True` instead of a fail-open estimate.
- **`get_upgrade_status` readiness and deprecation counts reflect live cluster state**, not a
  fixed value; a rewrite must not treat any exact count as a contract.

## Enforcement

- **Unit** (fakes) — `tests/unit/toolboxes/test_platform_admin.py`: tier membership + annotation
  hints for all 14 tools; `create_space` id-pattern rejection; `update_space` field-preservation
  on a partial call; `create_or_update_role`'s empty-grant and `kibana_spaces`-without-`kibana_base`
  guards, its `IndexPrivilege`→dict round-trip, `create_only`-defaults-True rejection of an
  existing (`kibana_system`) role, and that `create_only=False` still refuses a reserved role;
  `delete_space`'s default-space refusal and force requirement; `delete_role`'s reserved-role
  refusal; shape assertions (a missing space/role surfacing as a tool error, blank-id/name rejection) per read tool — the HTTP-level 404 mapping is pinned by the contract tier below, not here.
  The four saved-objects tools: tiers and the share's idempotent hint, `source_space` in place of
  `space`, the copy/overwrite mode sent, every refusal (missing space, source as target, missing
  object, repeats, empty lists, `*` as a copy target), the share's repeat, the unshare's
  last-space refusal and forced delete.
- **Unit (adapter and core)** — `tests/unit/adapters/test_space_objects_gateway.py`: live-captured
  bodies for the copy map (success, `overwrite`, `missing_references`, `ambiguous_conflict`,
  `unsupported_type`, the empty `unchanged` case, the 404 of a missing object), a whole-space
  failure beside a copied space (a 403 for a target the key may only read), the
  shareable references read and the update; the existence check before any write; the
  source-space refusal with a pinned base path, and no `source_space` in the guidance there; the routing of the spaces routes under `/s/<source>` (kibana-py
  serves `spaces` unscoped on a space-scoped client). `tests/unit/test_space_sharing_core.py`:
  the refusals and fix texts, and that each refusing deployment names the URL that fixes the
  source (never `KIBANA_URL` when only the public URL is pinned).
- **Unit negative control** — `tests/unit/toolboxes/test_space_threading.py` registers
  platform-admin alongside the five space-aware toolboxes and asserts none of its 14 tools gained
  a `space` property in their input schema — the deliberate "outside" half of that contract.
- **Contract** (live Kibana 9.4.7 and 9.5.4) — `tests/contract/test_gateway_contract.py`: `list_spaces`
  contains the reserved default space; `get_space`/`get_role` 404 on a missing id; `list_roles`
  contains reserved `kibana_system`; `get_upgrade_status` shape (never exact counts); a
  create→update→delete round-trip on a uuid-suffixed space asserting `update_space` preserves
  `disabled_features` set at create time; a `create_or_update_role` ES+Kibana privilege round-trip
  plus its `create_only=True`-on-existing rejection; `delete_space`/`delete_role` guard rejections
  on `default`/`kibana_system`, incl. `create_only=False` still refusing `kibana_system`; an
  MCP-tool-level (not just gateway-level) `IndexPrivilege` mapping round-trip via a live client;
  the flights dashboard copied with its references into a throwaway space and refused or failed
  without them, a space that does not exist (nothing written there), the source space, a missing
  object, an uncopyable Fleet output, an overwrite that restores an edited copy; a copy with a
  throwaway key (role `all` in one throwaway target, `read` in the other; the key made by its
  user, since a key a key makes has no privileges) that copies into the first and fails the
  second with `space_error`, nothing written there, and still fails it after the role gains `all`
  there (a key keeps its creation-time privileges); a throwaway data
  view shared from a throwaway space to the default and another space (the same id there), the
  share's repeat, an overwrite onto a shared object (`unchanged`), the one-space-type, missing-space
  and missing-object refusals, a removal that removes nothing, the last-space refusal and the
  forced delete (the object gone).
- **e2e-replay** — `create_space` is the enabling call three transcripts depend on
  (`space-dashboard.json`, `space-dashboard-gemma-4-12b-qat.json`,
  `alerting-space-recovery-gemma-4-12b-qat.json`); each supplies `platform-admin` in
  `KIBANA_MCP_TOOLBOXES` so `create_space` is registered before the transcript's first step runs,
  and its recorded response shape is replayed and asserted like every other pinned call.
- **Docs drift guard** — `tests/unit/test_docs_tool_reference.py` asserts every one of these 14
  registered tool names appears in `docs/tools.md`.
- **DoD gates** (`dod.config`) — `unit_green`, `contract_green`, `e2e_replay_green` all cover this
  toolbox; no dedicated ephemeral-stack criterion — destructive paths (`delete_space`,
  `delete_role`, `overwrite_saved_objects_in_spaces`, `unshare_saved_objects_from_spaces`) are
  proven against uuid-suffixed, self-cleaned objects and spaces on the shared dev stack.
