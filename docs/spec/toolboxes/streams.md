# streams — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#streams-toolbox · Enforcement: named per section below

## Purpose & persona

Streams gives a model access to Kibana's Wired Streams framework — the schema-managed, self-routing evolution of classic log data streams — to inspect stream topology and ingest lifecycle (read tier) and, where enabled, provision the framework, stage and activate child routing forks, edit ingest processing, and manage retention/deletion (write/destructive tiers). Persona: a logs/platform engineer. `profiles/README.md` documents a `logs-engineer` (+ Streams) variant as a future profile, not yet a shipped `.mcp.json` — today the toolbox is reached by naming `streams` directly in `KIBANA_MCP_TOOLBOXES`. The toolbox carries the Kibana Tech-Preview caveat — API shapes may change between minor versions — stated in 10 of the 12 tool docstrings (the two structural reads, `get_stream` and `get_stream_ingest`, omit it).

## Surface

Twelve tools across three tiers: **read** (3) — `list_streams`, `get_stream`, `get_stream_ingest`. **write** (5) — `enable_streams`, `resync_streams`, `fork_stream`, `set_stream_processing`, `deactivate_fork`. **destructive** (4) — `set_stream_retention`, `activate_fork`, `delete_stream`, `disable_streams`. Full per-tool parameters and return shapes: docs/tools.md#streams-toolbox. `KIBANA_MCP_TIER` hides tiers above its cap from the tool list entirely — a capped tool is never listed, not merely refused at call time (docs/tools.md#tiers-and-annotations).

## Behavioral guarantees

- **Not space-aware — cluster-global.** None of the 12 tools accept a `space` parameter, and the gateway PORT's stream methods (`ports/gateway.py`) take no space argument: Kibana Streams wraps a cluster-global Elasticsearch resource, not a space-scoped one (docs/tools.md, Space targeting). No streams result carries the `"space"` echo key other toolboxes' dict-returning tools add when a caller passes `space=`.
- **Guard-before-gateway ordering.** Client-side guards can reject before any Kibana call: `fork_stream`'s child-prefix check, `set_stream_processing`'s `steps=[] + confirm` gate, `activate_fork`'s confirm gate, and `disable_streams`'s confirm gate all raise `ToolError` ahead of `deps.gateway_factory()`. A rejected call makes zero requests to Kibana.
- **Read-modify-write over one gateway connection.** `set_stream_processing`, `set_stream_retention`, `activate_fork`, and `deactivate_fork` each read a stream's current ingest config, edit only their own facet (or, for the fork tools, only the matching routing entry's status), and re-send the whole config — every other facet/entry is preserved untouched. The read and the write execute inside the single gateway connection the tool opens for that call; there is no reconnect between them.
- **Confirm gates are per-tool, not per-tier.** `activate_fork` and `disable_streams` require `confirm=True` because their effect (diverting live documents / deleting all wired data cluster-wide) is invisible until the caller reads the guidance. `set_stream_retention` and `delete_stream` — both also destructive-tier — take no `confirm`: retention is a single explicit value the caller already typed, and `delete_stream` is instead guarded by `force` (refuses a root or a parent-with-children unless `force=True`), the same override shape `set_stream_processing`'s clear-all guard uses at the write tier.
- **Error-guidance strings are product contract.** Every guard's message names the exact fix — `"pass confirm=True"`, `"child_name must start with '<parent>.'"`, the force-required refusal text — so a model can self-correct on its next turn. The guards themselves are unit-pinned (each raises on its trigger); the exact wording is not yet asserted by any test — a rewrite must preserve fix-naming messages, and pinning the wording is an open test gap.
- **Fork lifecycle: staged by default, routing status flips both ways, documents never move.** `fork_stream` creates a child **staged** (`disabled`, no live routing); `activate_fork` (destructive, confirm-gated) flips its routing entry to `enabled` going forward only — already-ingested documents never move; `deactivate_fork` (write tier, no confirm) flips it back to `disabled`, leaving documents already routed into the child untouched. Re-activation after deactivation is allowed (the live contract tier flips the same fork both ways). Neither tool moves or copies data; only the routing entry's status changes.
- **`delete_stream`'s children check has a documented TOCTOU window.** It reads the stream list, then deletes; a child forked in the gap is missed. The destructive tier gate plus deliberate operator intent is the stated backstop, not an atomic check.
- **Name normalization.** `fork_stream` strips both `parent_name` and `child_name` before the prefix check and the gateway call, matching `delete_stream`'s own name normalization, so incidental whitespace neither trips the prefix guard nor reaches Kibana.

## Invariants

- MUST NOT accept or thread a `space` parameter on any streams tool.
- MUST NOT open a gateway connection before a client-side guard (confirm, prefix, or clear-all) has been checked and passed.
- MUST implement `set_stream_processing`, `set_stream_retention`, `activate_fork`, and `deactivate_fork` as read-modify-write over the stream's full ingest config — never a partial-field PATCH that could drop an untouched facet.
- MUST require `confirm=True` before `activate_fork` diverts live documents or `disable_streams` deletes wired streams cluster-wide.
- MUST refuse `delete_stream` on a root stream or a parent with children unless `force=True`.
- MUST require `child_name` to start with `parent_name + "."` on `fork_stream`, rejected before any gateway call.
- MUST create a forked child in a staged/disabled state — never live-routing on creation.
- MUST leave documents already routed into a forked child untouched on `deactivate_fork` — deactivation flips only future routing.
- MUST make `enable_streams` idempotent: a repeat call on an already-enabled framework returns a no-op result, never an error (live-pinned). `resync_streams` must likewise never error on repeat, but its no-op return shape is not yet pinned by any test — verify live before leaning on it.
- MUST reject `set_stream_retention` on a stream whose lifecycle is `ilm` rather than silently converting it.
- MUST surface a missing stream from `get_stream`/`get_stream_ingest` as a caller-visible not-found error.
- MUST keep `list_streams` a bare, unpaginated list — no cursor, no page parameter, no wrapper envelope.

## Deliberate exclusions & caveats

- **Query / significant-events / attachments surface** needs an Enterprise license — confirmed live as a 403 on Basic — and stays out of the toolbox; significant-events specifically may also require an AI connector, unverified and flagged for confirmation before it is ever built.
- **ILM-mode retention writes** are deferred: no ILM policy exists on the reference stack to develop or verify against, so `set_stream_retention` refuses a stream already on an ILM lifecycle rather than guessing at its handling.
- **A raw full-config ingest-update path** was evaluated and deliberately not built: a whole-config overwrite would bypass the read-modify-write facet safety the four shipped RMW tools provide, so it stays deferred as a footgun rather than shipping.
- **Per-space stream targeting** is deferred; the toolbox stays cluster-global today, consistent with its "no space axis" classification.
- **wired vs classic is a real product asymmetry, not a gap.** Only `wired` streams carry a managed field schema and child-routing; `classic` streams report zero processing/routing/field counts.
- **Recovery from `disable_streams` is partial by design.** `enable_streams` recreates empty root streams only — forked children and their data are never restored. This is a documented data-loss edge, not an oversight.
- **No persona profile ships streams yet.** `profiles/README.md` lists `logs-engineer` (+ Streams) as a documented future variant; there is no shipped `.mcp.json` for it today.

## Enforcement

- **Unit** — `tests/unit/toolboxes/test_streams.py`: all 12 tools present with correct tier annotation hints; guard-before-gateway ordering for `fork_stream`'s prefix check and `activate_fork`/`disable_streams`'s confirm gates; `delete_stream`'s root/force guard exercised through the tool (not hardcoded); `gateway_errors()` wrapping of a raised domain error into `ToolError`; `get_stream`/`get_stream_ingest` not-found and blank-name rejection; `list_streams`'s minimal per-item shape.
- **Contract** (live Kibana) — `tests/contract/test_gateway_contract.py`: read shapes (a wired root has fields; a classic stream has zero; a missing stream 404s) and the write/destructive tier — a fork → set-retention → delete roundtrip asserting the parent's routing-count delta, `enable_streams` idempotency, root-delete and parent-with-children refusals, `activate_fork`/`deactivate_fork`'s live routing-status flip inspected on the parent's raw ingest body, and `set_stream_processing`'s processing-step delta.
- **Ephemeral** (DoD criterion `streams_ephemeral`, required) — `tests/ephemeral/test_disable_streams_ephemeral.py` on an isolated single-node stack: `disable_streams` deletes the wired framework and a forked child cluster-wide; `enable_streams` recreates the root but not the forked child. Isolated because it cannot run against the shared dev stack, which it would wipe.
- **e2e-replay**: no recorded transcript currently exercises a streams tool — the recorded-model-turn tier does not yet pin streams tool-selection behavior.
- **DoD gate** (`dod.config`): `streams_ephemeral` is required as a criterion distinct from the shared `contract_green`/`unit_green` criteria, because the disable path is destructive at cluster scope and cannot be certified against shared infrastructure.
