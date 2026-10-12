# alerting — behavior contract

Status: Draft v1.2 (2026-10-12) — regeneration corpus, reconciled to shipped code
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
CRUD, in-place edits of both, and the ability to test-fire a connector can
enable just `alerting`.

## Surface

15 tools: 6 `read`, 6 `write`, 3 `destructive` — one Kibana object per
tool, no batch or bulk form (the two `update_*` tools read the object before
writing it). `read`: `list_alert_rules`, `get_alert_rule`,
`get_alerting_health`, `list_connectors`, `list_rule_types`, `get_connector`.
`write`: `create_alert_rule`, `update_alert_rule`, `enable_alert_rule`,
`disable_alert_rule`, `create_connector`, `update_connector`.
`destructive`: `delete_alert_rule`, `delete_connector`, `execute_connector`.
The tier cap hides the destructive three at the default `write` tier and all
nine non-read tools at `read` tier — a rewrite must keep `execute_connector`
in the destructive group even though it deletes nothing (see Behavioral
guarantees). Full per-tool arguments, return shapes, and examples:
docs/tools.md#alerting-toolbox.

## Behavioral guarantees

- **Space targeting follows the shared 73-tool contract**
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
  key, because the caller often cannot see the rest (`get_alert_rule` returns
  a summary without params); `tags` and `actions` replace the whole list, so
  `[]` clears. The rule's `throttle`, `notify_when`, `alert_delay`, `flapping`
  and `artifacts` are re-sent when set. GET echoes each action's
  `connector_type_id`, which the PUT schema rejects, so it is stripped; the
  action's `uuid` is kept and Kibana preserves it.
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

- MUST register exactly 15 tools, split 6 read / 6 write / 3 destructive, as
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
- MUST make `update_alert_rule` and `update_connector` read-modify-write:
  an argument not passed leaves that field as it was.
- MUST NOT change a rule's `enabled` state from `update_alert_rule`.
- MUST NOT return a connector's secrets from any tool.
- MUST refuse `update_connector` without `secrets` on any connector type
  other than `.index` and `.server-log`, and on any preconfigured connector,
  before writing anything.

## Deliberate exclusions & caveats

- **`maintenance_windows` is out of scope.** Platinum-gated in Kibana, and
  subscription-gated surfaces are not built here; the reference/dev stack runs
  Basic, so there is no license to build or contract-test it against either.
- **Exactly one connector surface.** Connectors are managed through Kibana's
  current connector API only — never a deprecated legacy alias naming the
  same objects — so there is one path to create/list/get/update/delete/execute
  a connector, not two views of the same store.
- **Rule-lifecycle depth deferred** (recorded 2026-08-19, ordered as this
  toolbox's next work): mute/unmute (per-alert and rule-wide),
  snooze/unsnooze, and `update_api_key`. The `rule_types` read shipped
  2026-10-12 as `list_rule_types`, and in-place rule `update` shipped
  2026-10-12 as `update_alert_rule` (with `get_connector`/`update_connector`
  for connectors).
- **`update_alert_rule` leaves out the rule-level `notify_when` and
  `throttle`.** Kibana 9.5 refuses them beside any action carrying a
  per-action `frequency` ("Cannot specify per-action frequency params when
  notify_when or throttle are defined at the rule level", probed), and
  kibana-py marks rule-level `throttle` deprecated in favour of `frequency`;
  `create_alert_rule` does not take them either. A rule that already has
  them keeps them. `alert_delay`, `flapping` and `artifacts` are likewise
  preserved, not editable.
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

- `tests/unit/toolboxes/test_alerting.py` — the exact 15-tool set;
  `create_alert_rule` defaulting to disabled; a full rule lifecycle
  (create → enable → get → list → delete); a connector lifecycle including
  `execute_connector`'s `status: "ok"` path; `get_alerting_health`'s
  shape; per-tool tier annotations including `execute_connector`'s
  distinct destructive/non-idempotent/open-world triple; space threading
  and echo for a representative call; the no-`space` call keeping the
  pre-space result shape byte-identical.
- `tests/unit/toolboxes/test_space_threading.py` — the shared 73-tool space
  contract (factory threading, schema shape, echo-only-when-set, scoped
  not-found suffix) parametrized over all 15 alerting tools alongside the
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
- `tests/e2e/test_lmstudio_alerting_space.py` — live-model gate
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
  stripped from echoed actions, no `enabled`; connector config merged and
  name kept; secrets forwarded but absent from results; the preconfigured
  and missing-secrets refusals sending nothing.
- `tests/unit/toolboxes/test_alerting.py` and
  `tests/unit/test_server_assembly.py` — `get_connector` read-tier and the two
  `update_*` tools write-tier; `update_alert_rule` has no `enabled`
  argument; the at-least-one-field guards run before the factory; no secret
  in any result.
- `tests/contract/test_gateway_contract.py` — live:
  `test_update_alert_rule_preserves_what_it_was_not_given_live` (actions keep
  their uuid, tags and params survive a params-only edit, an enabled rule
  stays enabled, `[]` clears), `test_update_alert_rule_rejections_name_the_fix_live`,
  `test_connector_get_and_update_live` (a `.index` rename keeps its required
  config; config merges; secrets on `.index` are rejected) and
  `test_alerting_maintenance_tools_scoped_live` (the three tools through MCP
  in a space: echo, scoped suffix, invisible from the root space).
