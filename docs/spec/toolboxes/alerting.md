# alerting — behavior contract

Status: Draft v1.7 (2026-10-25) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#alerting-toolbox · Enforcement: named per section below

## Purpose & persona

Alert rules (scheduled condition checks, e.g. an ES query threshold) and the
action connectors those rules — or a caller directly — fire through (Slack,
email, webhook, index, server-log, ...). It is Wave 2's cross-persona
toolbox, shipped alongside `cases` so an incident workflow (detect → notify →
track) has both halves; it is the alerting half of the withdrawn
`observability-sre` profile (paired with `observability` + `cases`; that persona is
withdrawn because its SLO reads are Platinum-gated and out of scope, not because of
anything in this toolbox). It also stands alone: any deployment wanting rule/connector
CRUD, in-place edits of both, a rule's notification lifecycle (mute,
snooze, API key) and the ability to test-fire a connector can enable just
`alerting`.

## Surface

22 tools: 6 `read`, 13 `write`, 3 `destructive` — one Kibana object per
tool, no batch or bulk form (the two `update_*` tools, `mute_alert_rule`,
`unmute_alert_rule` and `mute_alert` read the object before writing it). `read`: `list_alert_rules`, `get_alert_rule`,
`get_alerting_health`, `list_connectors`, `list_rule_types`, `get_connector`.
`write`: `create_alert_rule`, `update_alert_rule`, `enable_alert_rule`,
`disable_alert_rule`, `create_connector`, `update_connector`, and the rule
lifecycle — `mute_alert_rule`, `unmute_alert_rule`, `mute_alert`,
`unmute_alert`, `snooze_alert_rule`, `unsnooze_alert_rule`,
`update_alert_rule_api_key`.
`destructive`: `delete_alert_rule`, `delete_connector`, `execute_connector`.
The tier cap hides the destructive three at the default `write` tier and all
sixteen non-read tools at `read` tier — a rewrite must keep `execute_connector`
in the destructive group even though it deletes nothing (see Behavioral
guarantees). Full per-tool arguments, return shapes, and examples:
docs/tools.md#alerting-toolbox.

## Behavioral guarantees

- **Space targeting follows the shared 92-tool contract**
  (docs/tools.md#space-targeting): optional `space` on every tool; omitted
  means the default space, byte-identical to before the parameter existed;
  a nonexistent `space` fails closed before any other work; every
  dict-returning tool's result gains a `"space"` key only when the caller
  passed one; the two list-returning tools — `list_alert_rules`,
  `list_connectors` — never carry that echo, so an empty list from the
  wrong space still reads as "nothing here"; a scoped not-found error
  carries the `(in space '<id>')` suffix exactly once.
- **Two surfaces stay instance-global regardless of `space`.**
  `get_alerting_health`'s report is identical in every space — `space`
  routes the call but never changes the payload. Preconfigured connectors
  appear in `list_connectors` with the same id in every space, and
  `execute_connector` against one succeeds from any space — neither is a
  per-space object to isolate.
- **In-place edits keep what the caller did not pass.** Both Kibana update
  endpoints are full replaces (probed on 9.5.4): a rule PUT that omits
  `actions` or `tags` empties them, one that omits `params` fails validation,
  and one that omits `notify_when` resets it to null; a connector PUT that
  omits `config` resets it to `{}` (a `.index` connector then fails
  validation) and requires `name`. `update_alert_rule` and `update_connector`
  therefore fetch the object, re-send every writable field, and overlay only
  the arguments given. `params` (rule) and `config` (connector) merge key by
  key, so the caller passes only what changes (`get_alert_rule` and
  `get_connector` show the rest); `tags` and `actions` replace the whole list, so
  `[]` clears. The rule's `throttle`, `notify_when`, `alert_delay`, `flapping`
  and `artifacts` are re-sent when set — except `notify_when` and `throttle`
  when the caller's `actions` carry a `frequency` (see the caveat below). GET echoes each action's
  `connector_type_id`, which the PUT schema rejects, so it is stripped; the
  action's `uuid` is kept and Kibana preserves it.
- **`get_alert_rule` is the read half of `update_alert_rule`.** It returns
  the summary fields plus the rule's `params` as Kibana stores them (defaults
  it filled in included) and its `actions`, so a caller reads the value
  before merging into it and reads it again to confirm the edit;
  `update_alert_rule`'s own result stays the summary, and the two
  descriptions point to each other. Each action carries the keys
  `update_alert_rule`'s `actions` take — `id` (the connector id), `group`,
  `params`, `frequency` and `uuid` — with `group`, `frequency` and `uuid`
  present only when Kibana sent them (a system action has no group), and
  without GET's `connector_type_id` (the update rejects it), so a read action
  passes back unchanged. `uuid` is the action's identity: passed back, Kibana
  keeps it; an action sent without one gets a new uuid (probed on 9.5.4, D40
  amendment), so a read → update round trip keeps every action's identity. The result is built from named keys: a rule holds no
  secret (secrets live on connectors), and the GET body (probed on 9.5.4)
  carries `api_key_owner` and `api_key_created_by_user` but never the rule's
  API key; neither they nor any other bookkeeping key are returned.
  `list_alert_rules` stays a summary per row (its description says so and
  names `get_alert_rule`).
- **`update_alert_rule` never changes `enabled`.** It has no `enabled`
  argument (Kibana's update schema rejects the key), and an enabled rule is
  still enabled afterwards (probed); `enable_alert_rule` /
  `disable_alert_rule` own that state. `rule_type_id` and `consumer` are
  immutable after creation.
- **Connector secrets are write-only, and an edit never loses them
  silently.** Kibana never returns a connector's secrets, and its update
  resets omitted `secrets` to `{}`. The secrets schema of `.index` and
  `.server-log` is empty — Kibana rejects any key with "Unrecognized key"
  (probed on 9.5.4) — so updating those without `secrets` loses nothing; for
  every other type `update_connector` refuses a call without `secrets`,
  before any write, naming the fix (pass the full secret values; `{}` clears
  them deliberately). The wipe itself is not observed on the reference stack:
  every type that holds secrets needs Gold or above (`.webhook` 403s on
  Basic), so the refusal rests on kibana-py 0.6.0's documented PUT contract
  and is fail-safe by construction. Passed secrets go to Kibana and never
  come back: `get_connector`/`update_connector` results are built from named
  non-secret keys only.
- **Preconfigured connectors are refused before any edit.** A connector with
  `is_preconfigured` set (defined in `kibana.yml`) cannot be changed through
  the API; `update_connector` reads the flag and refuses with a pointer to
  `kibana.yml` or `create_connector`. Not live-verified: the reference stack
  defines no preconfigured connector. System connectors
  (`system-connector-.cases`, ...) 404 on GET, so they surface as not-found
  (Kibana's own PUT answers "System action ... can not be updated", probed).
- **The rule lifecycle tools quiet a rule without touching its
  definition, and every one is reversible** (D45, probed on 9.5.4). Each
  writes with one POST or DELETE answering 204 (the snooze answers 200 with
  its schedule), and `mute_alert_rule`, `unmute_alert_rule` and `mute_alert`
  read the rule with one GET first (below); none changes params, actions,
  tags or `enabled`.
  `mute_alert_rule`/`unmute_alert_rule` set the rule-wide mute — the rule
  runs and tracks alerts, no action fires, no end time — and Kibana clears
  the per-alert mutes on both calls, the unmute even on a rule that is not
  muted as a whole. No public route lists a rule's alert ids, so both tools
  read `muted_alert_ids` first and return them as `cleared_alert_ids`;
  `mute_alert` on each restores them, which is what keeps the pair
  reversible — for a caller that keeps the ids from the call that cleared
  them. A repeat (a retry after a lost response included) and the inverse
  both return `[]`, the list being empty by then, and the read and the
  write are not atomic: a `mute_alert` that lands between them is cleared
  and not reported. `mute_alert`/`unmute_alert` mute one
  alert by the id the rule raised it under (the group's value for a grouped
  rule; `query matched` for an ungrouped `.es-query` rule, observed); Kibana's
  `validate_alerts_existence` default stays on, so an id the rule has not
  raised is a 404, answered with guidance; unmuting an alert that is not
  muted is a 204. On a rule muted as a whole Kibana answers a per-alert mute
  with 204 and records nothing, so `mute_alert` reads the rule first and
  refuses, naming `unmute_alert_rule` (it clears every per-alert mute) then
  `mute_alert`. `get_alert_rule` returns `muted` (Kibana's `mute_all`) and
  `muted_alert_ids`. Repeats are safe: the mutes, unmutes, the unsnooze and
  the key refresh carry `idempotentHint=true`; the snooze does not (each call
  adds a schedule).
- **A snooze is identified only by the `schedule_id` it returns.**
  `snooze_alert_rule` takes `duration` (a whole number and `s`/`m`/`h`/`d`,
  checked before the gateway — Kibana 400s `1w`, `1M`, `1y`, `1.5h`, `2H`,
  `-1`), `start` (any ISO-8601 instant, default now, no offset read as UTC)
  and an optional `recurring` passed to Kibana (`every` takes `h`/`d`/`w`/`M`/`y`
  and must exceed `duration`; `end` is converted like `start`). Kibana accepts
  only `YYYY-MM-DDTHH:mm:ss.sssZ` for `start` and `end` (a `Z` without
  milliseconds or an offset is a 400), so the adapter sends that form in UTC.
  The result is the stored schedule: `rule_id`, `schedule_id`, `duration`,
  `start`, `timezone`, `recurring`. Kibana's public API never returns a
  rule's snoozes (not on GET, not in `_find`, not with `fields`; only an
  internal route the UI uses does), so the description says to keep the id;
  a rule holds at most 5 snoozes and expired ones count — the sixth is
  refused with guidance naming `unsnooze_alert_rule` and Kibana's Rules page.
  An unknown `schedule_id` (Kibana: 404 when the rule has other snoozes, 400
  "Rule has no snooze schedules." when it has none) is a not-found naming
  `snooze_alert_rule`.
- **`update_alert_rule_api_key` changes the identity a rule runs as.**
  Kibana regenerates the key from the caller's credentials — this server's
  API key — and bumps `updated_at`; definition and `enabled` are unchanged
  (probed). No key material comes back.
- **`disable_alert_rule`'s `untrack` is additive and default off.** Omitted
  or `false`, the request is the one sent before the parameter existed (no
  body) and the rule's active alerts stay `active`; `true` sends
  `{"untrack": true}` and Kibana marks them `untracked` in the alerts index
  (observed: 3 active → 3 untracked). The result gains `"untracked": true`
  only when it was asked for.
- **Rules are created disabled by default.** `create_alert_rule`'s `enabled`
  parameter defaults to `False`; a rule exists inert until an explicit
  `enable_alert_rule` call. This is a deliberate safety default, not an
  incidental pass-through of Kibana's own default.
- **`list_alert_rules` returns the complete result set.** The read paginates
  through every page of the underlying listing to exhaustion before
  returning — a rewrite must not stop at the first page and silently
  truncate.
- **`get_alerting_health`'s status collapses multiple sub-checks by
  severity.** Any sub-check reporting `"error"` wins over `"warn"` wins over
  `"ok"`; a missing or absent sub-status (or an empty health payload) yields
  `"unknown"` — never silently reported as `"ok"`. A health tool must never
  present an absent signal as a healthy one.
- **`execute_connector` treats a 200-with-failure body as a real failure.**
  A connector-execute call can return HTTP 200 with an internal `status` of
  `"error"`; the tool result carries that `status` plus, when it is not
  `"ok"`, a `message` field pulled from the connector's own service-level
  detail — a failed external action is never reported as a bare success or
  an opaque error.
- **A rule's `status` field is independent of its `enabled` flag.** `status`
  reflects the rule's last-observed execution status (pending / ok / error),
  not a function of `enabled` — enabling a rule does not itself flip
  `status` to `"ok"`.
- **`execute_connector` is annotated distinctly from the toolbox's other two
  destructive tools:** `destructiveHint=true`, `idempotentHint=false`
  (repeated calls repeat the real external side effect), `openWorldHint=true`
  (reaches systems outside Kibana). `delete_alert_rule`/`delete_connector`
  set `destructiveHint=true`, `idempotentHint=true` (the target stays
  deleted on repeat — the annotation describes real-world effect, not that
  a repeat call succeeds without raising), `openWorldHint=false`.
- **No confirm-parameter gate on the destructive tier.** Unlike `fleet` and
  `streams`' bulk-action `confirm=True` pattern, `delete_alert_rule`,
  `delete_connector`, and `execute_connector` take no confirm parameter —
  the destructive tier cap (`KIBANA_MCP_TIER`) is the only gate. A rewrite
  must not add one; that would change the tested call signature for no
  behavior this toolbox promises.
- **Guard-before-gateway ordering** applies toolbox-wide: schema guards
  (non-empty `rule_id`/`name`/`connector_id`, the `space` pattern) and the
  update tools' "provide at least one field to update" check reject before
  the gateway is ever constructed, so such a failure performs no
  space-existence check.

## Invariants

- MUST register exactly 22 tools, split 6 read / 13 write / 3 destructive, as
  listed under Surface.
- MUST default `create_alert_rule`'s `enabled` to `False`.
- MUST NOT echo `space` on `list_alert_rules`, `list_connectors` or
  `list_rule_types` results,
  even when `space` is set.
- MUST echo `space` on every other (dict-returning) tool's result, and only
  when the caller passed one.
- MUST classify `execute_connector` as destructive, non-idempotent,
  open-world — distinct from `delete_alert_rule`/`delete_connector`
  (destructive, idempotent, closed-world).
- MUST treat `get_alerting_health` as instance-global: identical payload
  regardless of `space` (the parameter validates and routes, never changes
  the data). Preconfigured (`kibana.yml`-defined) connectors are likewise
  instance-global — a documented semantic caveat carried in the tool
  docstrings, not currently pinned by any live test (no preconfigured
  connector exists on the test stack).
- MUST paginate `list_alert_rules` to exhaustion rather than returning a
  single page.
- MUST surface a non-`"ok"` `execute_connector` status (and its
  service-level detail when present) rather than collapsing it into a bare
  error or a false `"ok"`.
- MUST NOT require a `confirm` parameter on any tool in this toolbox.
- MUST run schema/argument guards before the gateway is constructed.
- MUST answer an empty `rule_id` on `get`/`update`/`enable`/`disable`/`delete_alert_rule`
  with "rule_id is empty — pass the id that create_alert_rule or
  list_alert_rules returns", and a `rule_id` that matches no rule with
  "alert rule '<id>' not found — rule_id is the id … not the rule's name; call
  list_alert_rules to find it" (D17; the space suffix still applies). Probed
  on Kibana 9.5.5: models pass a rule's name or an empty string as its id.
  `get_connector`/`update_connector` answer the same way for `connector_id`,
  naming `create_connector or list_connectors` and `list_connectors`.
- MUST return `params` and `actions` from `get_alert_rule`, each action in
  `update_alert_rule`'s `actions` shape (no `connector_type_id`; `uuid` kept
  when Kibana sent it, so a round trip keeps the action's identity), built from
  named keys only; MUST keep `list_alert_rules` rows and `update_alert_rule`'s
  result the summary.
- MUST make `update_alert_rule` and `update_connector` read-modify-write:
  an argument not passed leaves that field as it was.
- MUST NOT change a rule's `enabled` state from `update_alert_rule`.
- MUST leave the rule-level `notify_when`/`throttle` out of an
  `update_alert_rule` whose `actions` carry a `frequency`, and re-send them
  otherwise.
- MUST NOT return a connector's secrets from any tool.
- MUST refuse `update_connector` without `secrets` on any connector type
  other than `.index` and `.server-log`, and on any preconfigured connector,
  before writing anything.
- MUST NOT change a rule's definition or `enabled` state from any lifecycle
  tool, and MUST NOT send `untrack` from `disable_alert_rule` unless the caller
  passed `untrack=true`.
- MUST send a snooze's `start` and `recurring.end` as UTC
  `YYYY-MM-DDTHH:mm:ss.sssZ`, refuse a `duration` outside `[1-9][0-9]*[smhd]`
  before the gateway, and return the `schedule_id`.
- MUST refuse `mute_alert` on a rule muted as a whole, before the mute.
- MUST return, from `mute_alert_rule` and `unmute_alert_rule`, the per-alert
  mutes the call cleared (`cleared_alert_ids`, read before the call).
- MUST answer an unknown alert id, an unknown snooze `schedule_id` and the
  five-snooze limit with guidance naming where the id comes from or the tool
  that frees a slot, and any other 404 on these routes with the rule's
  not-found guidance.

## Deliberate exclusions & caveats

- **`maintenance_windows` is out of scope.** Platinum-gated in Kibana, and
  subscription-gated surfaces are not built here; the reference/dev stack runs
  Basic, so there is no license to build or contract-test it against either.
- **Exactly one connector surface.** Connectors are managed through Kibana's
  current connector API only — never a deprecated legacy alias naming the
  same objects — so there is one path to create/list/get/update/delete/execute
  a connector, not two views of the same store.
- **Rule lifecycle shipped 2026-10-24** (D45): mute/unmute (per-alert and
  rule-wide), snooze/unsnooze, `update_api_key`, and `untrack` on disable —
  the depth recorded as deferred on 2026-08-19. The `rule_types` read shipped
  2026-10-12 as `list_rule_types`, and in-place rule `update` shipped
  2026-10-12 as `update_alert_rule` (with `get_connector`/`update_connector`
  for connectors). Not built: `alerting.backfill` (scheduling past runs).
- **No read of a rule's snoozes or alert ids.** Kibana's public API has
  neither, so a lost `schedule_id` can be cancelled only in Kibana's Rules
  page, and `mute_alert`'s id comes from the user or the rule's grouping
  (the alerts-as-data index holds them; no tool reads it). The snooze's
  `timezone` is not an argument: Kibana stores `UTC`, so a recurring snooze
  repeats at the same UTC time across daylight-saving changes.
- **`update_alert_rule` leaves out the rule-level `notify_when` and
  `throttle`.** Kibana 9.5 refuses them beside any action carrying a
  per-action `frequency` ("Cannot specify per-action frequency params when
  notify_when or throttle are defined at the rule level", probed), and
  kibana-py marks rule-level `throttle` deprecated in favour of `frequency`;
  `create_alert_rule` does not take them either. A rule that already has
  them keeps them, unless the `actions` passed carry a `frequency`: then the
  pair is left out of the PUT, which clears it (probed on 9.5.4: a PUT
  without them succeeds and stores `null`), so the rule moves to per-action
  timing instead of the edit failing with no tool able to clear them.
  `alert_delay`, `flapping` and `artifacts` are preserved, not editable.
- **A merge cannot remove a key.** `params`/`config` merge key by key, so a
  key can be overwritten (or set to `null` where the type allows it) but not
  dropped; Kibana fills defaults into a rule's stored params anyway
  (`aggType`, `groupBy`, `excludeHitsFromPreviousRun` on `.es-query`).
- **`list_rule_types` lists, it does not describe `params`.** Each row is
  the type's `id` (the `rule_type_id`), name, category, producer, the
  `consumers` the caller may create it under (Kibana's `authorized_consumers`
  with `all`), `enabled_in_license`, `minimum_license_required` and Kibana's
  `is_internally_managed` flag as `internally_managed`. Kibana's rule-types
  API publishes no params schema; its `action_variables.params` lists only
  the params usable in action messages (for `.es-query` it omits
  `timeWindowSize` and `timeField`, probed on 9.5.5), so it is not returned
  as if it were one.
- **Preconfigured connectors cannot be deleted through `delete_connector`.**
  Kibana itself rejects the attempt; the `is_preconfigured` field returned
  by `list_connectors`/`create_connector`/`get_connector` is the signal a
  caller has to avoid trying — no client-side guard short-circuits the
  delete (unlike `update_connector`, which already reads the connector).
- **`is_missing_secrets` is informational, not enforced.** A connector can
  be created successfully even when required secrets are absent; the field
  tells the caller it will be non-functional, it does not block creation.
- **Cases is the paired half, out of this file's scope.** The
  detect-notify-track workflow this toolbox's persona runs (create/enable a
  rule, wire a connector, later open a case) spans `alerting` + `cases`;
  the case side is documented in the `cases` toolbox's own contract.

## Enforcement

- `tests/unit/toolboxes/test_alerting.py` — the exact 22-tool set;
  `create_alert_rule` defaulting to disabled; a full rule lifecycle
  (create → enable → get → list → delete); a connector lifecycle including
  `execute_connector`'s `status: "ok"` path; `get_alerting_health`'s
  shape; per-tool tier annotations including `execute_connector`'s
  distinct destructive/non-idempotent/open-world triple; space threading
  and echo for a representative call; the no-`space` call keeping the
  pre-space result shape byte-identical.
- `tests/unit/toolboxes/test_space_threading.py` — the shared 92-tool space
  contract (factory threading, schema shape, echo-only-when-set, scoped
  not-found suffix) parametrized over all 22 alerting tools alongside the
  other space-aware toolboxes.
- `tests/contract/test_gateway_contract.py` — live-Kibana pins:
  `get_alerting_health`'s status enum and encryption-key flag; a full
  alert-rule lifecycle including enable/disable transitions and the
  deleted rule disappearing from `list_alert_rules`; a connector
  create → execute → list → delete cycle against a real `.server-log`
  connector; space-scoped variants — a rule invisible outside its own
  space, the scoped not-found suffix on a miss, and a connector created
  and executed in a space staying invisible from the root space.
- `tests/unit/adapters/test_alerting_gateway.py` — the adapter behaviors
  the invariants lean on: `get_alerting_health` collapsing mixed
  sub-statuses to the worst (and to `unknown` when absent),
  `list_alert_rules` paginating to exhaustion across pages, and
  `execute_connector` surfacing a non-`ok` `service_message`.
- `tests/e2e/test_lmstudio_alerting_space.py` — live-model tier (on demand,
  `make test-e2e-local`; outside every gate)
  (Study L-alerting): a local model must create a space and an enabled
  `.es-query` rule inside it using only this toolbox plus `platform-admin`,
  scored against a frozen pass/fail ladder and swept afterward for
  default-space contamination.
- `docs/tools.md#alerting-toolbox` and `#space-targeting` — the per-tool
  reference and shared space semantics this file does not restate; a
  drift-guard test (`tests/unit/test_docs_tool_reference.py`) keeps every
  registered tool name on that page.
- `tests/unit/adapters/test_alerting_gateway.py` and
  `tests/unit/toolboxes/test_alerting.py` — `list_rule_types`' row mapping
  (writable consumers only, licence flags) and its read-only registration.
- `tests/contract/test_gateway_contract.py::test_list_rule_types_live` — live:
  `.es-query` lists `stackAlerts` as a consumer on Basic, and types the
  licence does not allow are flagged, not hidden.
- `tests/unit/adapters/test_id_guidance.py` — the empty-id and unknown-id
  guidance for dashboards, alert rules (including `update_alert_rule`) and
  connectors, and that a missing space still reports the space.
- `tests/unit/adapters/test_alerting_gateway.py` — the update bodies:
  every writable rule field re-sent, params merged, `connector_type_id`
  stripped from echoed actions, no `enabled`, rule-level
  `notify_when`/`throttle` left out only beside caller actions that carry a
  `frequency`; connector config merged and
  name kept; secrets forwarded but absent from results; the preconfigured
  and missing-secrets refusals sending nothing.
- `tests/unit/toolboxes/test_alerting.py` and
  `tests/unit/test_server_assembly.py` — `get_connector` read-tier and the two
  `update_*` tools write-tier; `update_alert_rule` has no `enabled`
  argument; the at-least-one-field guards run before the factory; no secret
  in any result.
- `tests/unit/adapters/test_alerting_gateway.py` — `get_alert_rule`'s
  params and actions (no `connector_type_id`; `uuid`, `group` and
  `frequency` only when sent), named keys only against a hostile body, a read
  action passing back into `update_alert_rule` unchanged, and
  `list_alert_rules` staying a summary; `tests/unit/toolboxes/test_alerting.py`
  — the read before and after an update through the tools, and the
  descriptions naming each other.
- `tests/contract/test_gateway_contract.py::test_get_alert_rule_reads_params_before_and_after_a_threshold_fix_live`
  — live, through the MCP tools: create a rule, read its params (Kibana's
  defaults included) and actions, merge a new threshold, read it again and
  match Kibana's stored params; the result keeps to the named keys and an
  enabled rule's API key never appears; the read actions passed back keep
  their uuids, and an action sent without one gets a new uuid.
- `tests/contract/test_gateway_contract.py` — live:
  `test_update_alert_rule_preserves_what_it_was_not_given_live` (actions keep
  their uuid, tags and params survive a params-only edit, an enabled rule
  stays enabled, `[]` clears),
  `test_update_alert_rule_per_action_frequency_replaces_rule_level_timing_live`
  (a rule with a rule-level `notify_when` keeps it on a rename and takes
  actions with a `frequency`), `test_update_alert_rule_rejections_name_the_fix_live`,
  `test_connector_get_and_update_live` (a `.index` rename keeps its required
  config; config merges; secrets on `.index` are rejected) and
  `test_alerting_maintenance_tools_scoped_live` (the three tools through MCP
  in a space: echo, scoped suffix, invisible from the root space).
- `tests/unit/adapters/test_alerting_gateway.py` — the lifecycle requests
  (rule id only for mute/unmute/key refresh; no `validate_alerts_existence`;
  no `untrack` unless asked), the strict UTC `start`/`end` form, the snooze
  result, the five-snooze, unknown-alert and unknown-schedule guidance, the
  refusal of `mute_alert` on a muted rule sending nothing, the rule-wide mute
  and unmute reading `muted_alert_ids` before the call and returning them, and
  the mute state
  on `get_alert_rule`; `tests/unit/adapters/test_id_guidance.py` — the
  empty and unknown `rule_id` guidance on every lifecycle call.
- `tests/unit/toolboxes/test_alerting.py` — the lifecycle tools' write tier
  and idempotency hints, the duration/start/`recurring.end` guards before the
  factory, ISO-8601 inputs reaching the gateway as UTC, `untrack` default off
  with the echo only when on, and the descriptions naming their partners.
- `tests/contract/test_gateway_contract.py` — live, through the MCP tools:
  `test_mute_tools_through_mcp_live` (a firing rule's `query matched` alert
  muted and read back, the rule-wide mute and unmute clearing per-alert mutes
  and naming them in `cleared_alert_ids`, the cleared mute restored from that
  result, the refusal on a muted rule, an unknown alert id answered with guidance),
  `test_snooze_tools_through_mcp_live` (an offset `start` stored as UTC
  milliseconds, a recurring snooze, the five-snooze limit with an expired one
  counting, a freed slot reused, a stale `schedule_id`),
  `test_update_alert_rule_api_key_and_untrack_live` and
  `test_lifecycle_tools_scoped_live` (echo, scoped suffix once, invisible
  from the root space).
