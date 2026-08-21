# alerting — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#alerting-toolbox · Enforcement: named per section below

## Purpose & persona

Alert rules (scheduled condition checks, e.g. an ES query threshold) and the
action connectors those rules — or a caller directly — fire through (Slack,
email, webhook, index, server-log, ...). It is Wave 2's cross-persona
toolbox, shipped alongside `cases` so an incident workflow (detect → notify →
track) has both halves; it is the alerting half of the planned
`observability-sre` profile (paired with `observability` + `cases`, blocked
today only on the license-gated SLO reads elsewhere in that profile, not on
this toolbox). It also stands alone: any deployment wanting rule/connector
CRUD and the ability to test-fire a connector can enable just `alerting`.

## Surface

11 tools: 4 `read`, 4 `write`, 3 `destructive` — one Kibana operation per
tool, no batch or bulk form. `read`: `list_alert_rules`, `get_alert_rule`,
`get_alerting_health`, `list_connectors`. `write`: `create_alert_rule`,
`enable_alert_rule`, `disable_alert_rule`, `create_connector`.
`destructive`: `delete_alert_rule`, `delete_connector`, `execute_connector`.
The tier cap hides the destructive three at the default `write` tier and all
seven non-read tools at `read` tier — a rewrite must keep `execute_connector`
in the destructive group even though it deletes nothing (see Behavioral
guarantees). Full per-tool arguments, return shapes, and examples:
docs/tools.md#alerting-toolbox.

## Behavioral guarantees

- **Space targeting follows the shared 63-tool contract**
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
  (non-empty `rule_id`/`name`/`connector_id`, the `space` pattern) reject
  before the gateway is ever constructed, so a schema failure performs no
  space-existence check.

## Invariants

- MUST register exactly 11 tools, split 4 read / 4 write / 3 destructive, as
  listed under Surface — one call per Kibana operation.
- MUST default `create_alert_rule`'s `enabled` to `False`.
- MUST NOT echo `space` on `list_alert_rules` or `list_connectors` results,
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

## Deliberate exclusions & caveats

- **`maintenance_windows` is deferred.** Platinum-gated in Kibana; the
  reference/dev stack runs Basic, so there is no license to build or
  contract-test it against.
- **Exactly one connector surface.** Connectors are managed through Kibana's
  current connector API only — never a deprecated legacy alias naming the
  same objects — so there is one path to create/list/delete/execute a
  connector, not two views of the same store.
- **Rule-lifecycle depth deferred** (recorded 2026-08-19, ordered as this
  toolbox's next work): in-place rule `update` (today the only way to
  change an existing rule's config is delete + recreate), mute/unmute
  (per-alert and rule-wide), snooze/unsnooze, `update_api_key`, and a
  `rule_types` read — so there is no tool to discover which
  `rule_type_id`/`consumer` combinations are valid before calling
  `create_alert_rule`; a caller must already know one.
- **Preconfigured connectors cannot be deleted through `delete_connector`.**
  Kibana itself rejects the attempt; the `is_preconfigured` field returned
  by `list_connectors`/`create_connector` is the only signal a caller has
  to avoid trying — no client-side guard short-circuits it.
- **`is_missing_secrets` is informational, not enforced.** A connector can
  be created successfully even when required secrets are absent; the field
  tells the caller it will be non-functional, it does not block creation.
- **Cases is the paired half, out of this file's scope.** The
  detect-notify-track workflow this toolbox's persona runs (create/enable a
  rule, wire a connector, later open a case) spans `alerting` + `cases`;
  the case side is documented in the `cases` toolbox's own contract.

## Enforcement

- `tests/unit/toolboxes/test_alerting.py` — the exact 11-tool set;
  `create_alert_rule` defaulting to disabled; a full rule lifecycle
  (create → enable → get → list → delete); a connector lifecycle including
  `execute_connector`'s `status: "ok"` path; `get_alerting_health`'s
  shape; per-tool tier annotations including `execute_connector`'s
  distinct destructive/non-idempotent/open-world triple; space threading
  and echo for a representative call; the no-`space` call keeping the
  pre-space result shape byte-identical.
- `tests/unit/toolboxes/test_space_threading.py` — the shared 63-tool space
  contract (factory threading, schema shape, echo-only-when-set, scoped
  not-found suffix) parametrized over all 11 alerting tools alongside the
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
