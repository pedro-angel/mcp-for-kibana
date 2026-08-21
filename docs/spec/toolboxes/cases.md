# cases — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#cases-toolbox · Enforcement: named per section below

## Purpose & persona

Lets an LLM open, read, update, comment on, and delete Kibana incident cases
using the built-in "none" connector — full case CRUD, no external ITSM setup
required, GA on Basic. Cross-persona: `observability-sre` pairs it with
`alerting` for post-incident tracking, `soc-analyst` pairs it with
`security-detections` for security incidents. Both stay "planned" profiles
(blocked on their own sibling toolboxes, not on `cases`), so no shipped
`.mcp.json` enables this toolbox today even though it is built and
contract-tested.

## Surface

6 tools: 2 read, 3 write, 1 destructive. Full per-tool table:
docs/tools.md#cases-toolbox. Every tool returns the same **summary** shape —
`id`, `title`, `status`, `severity`, `owner`, `tags`, `total_comments` —
never description text or a comment body; that narrower surface is
intentional (see Deliberate exclusions).

## Behavioral guarantees

**Space targeting.** All 6 tools accept an optional `space` (pattern
`^[a-z0-9_-]+$`, no max length; shared grammar and fail-closed rules in
docs/tools.md#space-targeting). Without `space`, every result is
byte-identical to a build with no space parameter at all. With `space` set,
the 5 dict-returning tools gain `"space": "<value>"` in the result;
`list_cases` — the one list-returning tool — never echoes it, so an empty
list from the wrong space still reads as "nothing exists," not as a routing
signal. A case created in a non-default space is invisible to `list_cases`
run against the default space (proven live). A nonexistent space is
rejected before any gateway work happens, with guidance naming
`list_spaces`/`create_space`; a not-found case or comment error raised with
`space` set gets `" (in space '<id>')"` appended, never double-suffixed with
the space-not-found guidance.

**Guard before gateway.** `update_case` requires at least one of
status/severity/tags/title; that check raises before the gateway factory is
called — a call that fails validation never opens a connection, and never
resolves or validates the requested `space` either.

**`update_case` is read-modify-write.** Callers never supply an
optimistic-concurrency version; "the current version is handled for you"
(its docstring) is a guarantee the gateway PORT (`ports/gateway.py`)
fulfills, not something the tool signature exposes. Fields the call does not
name are carried forward unchanged, and `total_comments` specifically must
still be correct on the response after an update that follows a comment — a
comment count that reverts on the next update is a contract violation
(pinned against a live stack).

**Case identity and lifecycle.** `create_case` always opens at status
`"open"`; status is settable only via `update_case`. `description` is
required at creation and is the case's only place to carry one: no read
tool ever returns it, and no write tool can change it afterward.

**`delete_case` has no confirm gate.** It is destructive-tier
(`destructiveHint=true`, `idempotentHint=true`) but takes only `case_id`
(and `space`) — the tier cap (`KIBANA_MCP_TIER=destructive`) is its sole
protection, unlike destructive-shaped tools elsewhere in the server that
additionally require `confirm=True`. A successful delete returns
`{"id": case_id, "deleted": True}` (plus the space echo if set), never the
case's prior state.

**Error guidance is product contract.** The space-not-found guidance string
and the not-found suffix format are regression-pinned. An auth error never
gains a space *suffix*; when `space` is set, an auth failure during the
fail-closed space validation carries added context naming the validation
step (the key must be valid and able to read spaces) — a rewrap for
guidance, not a masking of the auth cause.

## Invariants

- Exactly 6 tools: `list_cases`, `get_case` (read); `create_case`,
  `update_case`, `add_case_comment` (write); `delete_case` (destructive).
- Every tool takes an optional `space: str` matching `^[a-z0-9_-]+$`, never
  required, no max length; omitting it leaves output byte-identical to a
  build that never added the parameter.
- Only the 5 dict-returning tools echo `"space"` when set; `list_cases`
  never does.
- A space that does not exist is rejected before any other work the call
  would do, distinctly from a not-found case.
- `update_case` rejects an all-fields-omitted call before constructing the
  gateway, and is read-modify-write: fields not named in the call, and
  `total_comments` specifically, survive the update unchanged or correctly
  incremented.
- `create_case` always yields `status="open"`; only `update_case` changes
  status. The returned Case shape is always the summary — `id`, `title`,
  `status`, `severity`, `owner`, `tags`, `total_comments` — never
  description or comment text/authorship.
- `delete_case` has no `confirm` parameter; the tier cap is its only gate.

## Deliberate exclusions & caveats

- **No external ITSM push.** The toolbox only ever uses Kibana's built-in
  "none" connector; wiring a real connector (Jira, ServiceNow, etc.) needs a
  Gold+-licensed connector type and is out of scope.
- **Comment bodies, case configuration, assignees, and alert/visualization
  attachments are deferred** — deprioritized 2026-08-19 (6 of ~22 available
  case methods wrapped). Core CRUD is Basic-buildable and shipped; the rest
  is either license-gated (external push needs Gold+, assignees need
  Platinum) or thin workflow plumbing not worth building against a license
  this project doesn't run. Revisit if a license lands.
- **No live persona profile yet.** Both personas that want `cases`
  (`observability-sre`, `soc-analyst`) stay "planned," blocked on their own
  sibling toolboxes (Platinum SLOs; an LLM connector) — not on anything in
  this toolbox.

## Enforcement

- **Unit** — `tests/unit/toolboxes/test_cases.py`: exact 6-tool set; full
  create → update → comment → get → list → delete lifecycle on the fake
  gateway; `update_case`'s all-fields-omitted rejection (case left
  untouched); invalid `severity` rejected at the schema boundary (`Literal`);
  tier annotation values; space echo/no-echo on `create_case`;
  guard-before-gateway ordering for `update_case`.
- **Unit, cross-cutting** — `tests/unit/toolboxes/test_space_threading.py`,
  the 6 `cases` rows: threading to the gateway factory with/without a value;
  schema shape as part of the shared 63-tool assertion; dict-vs-list echo;
  not-found-surfaces-as-guidance and auth-error-passthrough on the shared
  error path.
- **Contract, live Kibana** —
  `tests/contract/test_gateway_contract.py::test_case_lifecycle_live`
  (create → comment → update status+severity → list → get → delete,
  asserting `total_comments` survives the update) and
  `::test_case_scoped_lifecycle_live` (a case created in a temporary
  non-default space is invisible to `list_cases` in the default space).
- No e2e-replay transcript and no ephemeral destructive-tier harness cover
  this toolbox; `delete_case`'s only live destructive proof is the contract
  test's teardown call.
