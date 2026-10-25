# security-detections — behavior contract

Status: Draft v1.2 (2026-10-25) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#security-detections-toolbox · Enforcement: named per section below

## Purpose & persona

Lets an LLM read and administer the Kibana Security **detection engine**:
detection rules, detection alerts (signals), rule tags and prepackaged-rule
install status, exception lists/items (including editing, duplicating,
exporting, importing and restoring a list, D50), value lists/items, and
investigation timelines. GA on a **Basic** license — only creating an ML-type rule needs
Platinum, and this toolbox never creates one. It is the core of the
`soc-analyst` persona, which is **withdrawn**: its sibling `security-ai` toolbox
(assistant + attack-discovery) needs an Enterprise-gated `.gen-ai` connector and is
out of scope — `security-detections` itself is built,
contract-tested, and usable standalone.

## Surface

30 tools: 12 read, 12 write, 6 destructive. Full per-tool table:
docs/tools.md#security-detections-toolbox. Tier visibility is cumulative
(docs/tools.md#tiers-and-annotations): `write` (default) shows reads + writes,
`destructive` adds the 5 deletes and `overwrite_exception_list`, `read` shows
only the 12 reads.
`create_detection_rule` builds only a KQL/Lucene `query`-type rule; every
other tool (read/update/replace/enable/disable/delete) is rule-type-agnostic.

## Behavioral guarantees

**Space targeting.** All 30 tools accept the shared optional `space`
parameter with the shared fail-closed-existence and dict-echo/list-never-
echoes rules (docs/tools.md#space-targeting). Two toolbox-specific edges:
`namespace_type="agnostic"` exception lists/items are shared across every
space — `space` routes the write/delete but never isolates them; value lists
sit at the other extreme, with per-space backing indices auto-created on the
first value-list write a space has ever seen. `search_detection_alerts` in a
space where the detection engine never ran returns an empty list, not an
error.

**Guard before gateway, only where the tool declares its own guard.**
`delete_detection_rule`, `delete_exception_list`, `delete_exception_item`,
`update_detection_rule`, `replace_detection_rule`, `update_exception_list`,
`duplicate_exception_list` and `export_exception_list` validate their
identifier/field arguments, and `import_exception_list` and
`overwrite_exception_list` read and parse their handle, and raise before
`deps.gateway_factory(space)` is ever called — a failing guard never opens a connection or validates `space`.
`get_detection_rule`, `get_exception_list`, `enable_detection_rule`, and
`disable_detection_rule` carry no tool-body guard; the identical check runs
inside the gateway, after the connection is already open. A rewrite must keep
each tool on the side of that line it is on today, or a bad-space vs. bad-
identifier error can swap which one wins.

**One read-modify-write flow underlies replace, enable, and disable.**
`replace_detection_rule`, `enable_detection_rule`, and `disable_detection_rule`
all fetch the rule's current full body, echo every writable field back
unchanged, and layer only the caller's change on top. Fields the caller never
mentions — interval, tags, actions, the alert time-window — must round-trip
untouched. `update_detection_rule` is a separate true partial PATCH: it can
reach only name/description/tags/severity/risk_score/query/interval, never
`enabled`/`actions`/`index`/`language` — reaching those needs
`replace_detection_rule`.

**Immutable-rule guard applies to content replacement, not enable/disable.**
`replace_detection_rule` refuses an Elastic-prebuilt (`immutable=true`) rule
outright; `enable_detection_rule`/`disable_detection_rule` ride the same flow
but carry no such refusal — a prebuilt rule's schedule can be toggled even
though its content can never be replaced here.

**Exception-item entries.** Each `ExceptionEntry` (`field`, `value`,
`operator` default `"included"`) becomes a full match-type entry with an
added `"type": "match"` discriminator; entries inside one item are ANDed.
`create_exception_item` requires at least one entry.

**Exception-list edit, copy, export and import (D50).** Probed on 9.5.4
(Basic); every behavior below is pinned by a live contract test.

- *Namespaces.* A list made with `namespace_type="agnostic"` answers 404 to
  a `"single"` read, so `get_exception_list`, `find_exception_items` and every
  tool below take `namespace_type` (default `"single"`), and the not-found
  text names the other namespace. `find_exception_lists` lists only
  `"single"` lists (roadmap).
- *`update_exception_list`* reads the list, then PUTs the stored name,
  description and type (the PUT 400s without them) with the caller's fields
  on top and the read's `_version`: a change made between the two is a 409
  that writes nothing, and the error says to call again. Omitted tags are
  kept, `tags=[]` clears them. The type is never changed and os_types are
  never sent (the PUT ignores them: `["windows"]` left `["linux"]`). The
  result carries `previous` (name, description, tags) — passing it back
  undoes the edit.
- *`duplicate_exception_list`* is Kibana's duplicate route: a new list named
  `"<name> [Duplicate]"` with a generated `list_id` and new item ids, in the
  same space; `include_expired_exceptions=False` leaves expired items out.
  Kibana answers 405 "action not allowed" for an endpoint list, so the tool
  reads the list first and refuses any `endpoint*` type, naming export +
  import as the way to copy it (the adapter maps a 405 the same way).
- *`export_exception_list`* (read tier, like `export_saved_objects`) reads
  the list to get both ids (Kibana's export needs both and answers an
  unknown pair with a 400), keeps the NDJSON on the server under an `el-`
  handle and returns only a summary (ids, name, namespace, item count, byte
  size). `el-` and `so-` handles share the export directory with a
  retention of 20 each; each import reads only its own kind, and a handle of
  the other kind is refused naming the tools that take it.
- *`import_exception_list`* (write) never uses Kibana's `as_new_list`: that
  mode keeps the source `item_id`s (two lists then hold items with the same
  `item_id`) and does not report the new `list_id`. The tool rewrites the
  export instead — the caller's `list_id` or a fresh uuid on the list and
  every item, a fresh `item_id` per item, `id`/`_version`/`tie_breaker_id`
  dropped — and sends a plain import. A plain import onto an existing
  `list_id` skips the list (409) but still adds the items to that list, so
  the tool refuses an existing `list_id` before sending anything. It may
  import into another space than the export's. Kibana's import does not
  carry `os_types`.
- *`overwrite_exception_list`* (destructive) imports the export with
  `overwrite=true`: the stored list with that `list_id` keeps its `id` and
  takes the export's name, description and tags, and its items are replaced
  by the export's (new item `id`s, same `item_id`s) — items added since the
  export are deleted. The tool lists their `item_id`s first and returns them
  as `removed_item_ids`. A list that no longer exists is recreated with a
  new `id` (`previous_id: null`), so rules that referenced the old `id` are
  not reattached.
- *Errors.* Per-object import failures come in a 200; they are returned as
  `"list_id[/item_id]: <status> <kind>"`, never Kibana's message (a 400's
  echoes the values it refused). A rejected import names a fresh export.

**Value-list item identity.** `create_value_list_item`/`find_value_list_items`
take a `list_id` argument that routes to the parent value list's own `id`
field — `ValueList`, unlike `ExceptionList`, has no separate `list_id`
attribute. A created item's `type` is always inherited from its parent list.

**`delete_value_list`'s `force` gate.** `force=False` refuses (409) a value
list an exception item still references; `force=True` deletes it anyway and
leaves that reference dangling — never cascaded into the referencing item.

**No `confirm` gate anywhere in this toolbox.** Every destructive tool's only
protection is the `KIBANA_MCP_TIER=destructive` visibility cap (contrast e.g.
`streams`' `activate_fork`).

**Error guidance is product contract.** The "provide exactly one of …" /
"provide at least one field to …" strings, the immutable-rule refusal
message, and the shared space guidance/not-found suffix are regression-pinned
wording, not incidental phrasing.

## Invariants

- Exactly 30 tools: 12 read, 12 write, 6 destructive (docs/tools.md#security-detections-toolbox).
- `create_detection_rule` only produces a `query`-type rule; a new rule's
  `enabled` always starts `false` — there is no way to create one pre-enabled.
- Every tool takes optional `space: str` matching `^[a-z0-9_-]+$`, no max
  length, never required; omitting it is byte-identical to no `space` param.
- Only dict-returning tools echo `"space"`; every list-returning tool
  (`list_detection_rule_tags` included) never does.
- `update_detection_rule` never touches `enabled`/`actions`; only
  `enable_detection_rule`/`disable_detection_rule` toggle `enabled`.
- `replace_detection_rule`/`enable_detection_rule`/`disable_detection_rule`
  preserve every field the caller omits; only `replace_detection_rule`
  refuses an immutable rule.
- `delete_value_list(force=False)` refuses a referenced list;
  `force=True` deletes it without touching the referencing exception item.
- `namespace_type="agnostic"` exception objects are visible/deletable from
  every space; `space` chooses routing only, never isolation, for them.
- `import_exception_list` never writes into an existing list: it refuses an
  existing `list_id` and every imported item gets a new `item_id`.
- `update_exception_list` writes nothing when the list changed after its
  read (409 on the read's `_version`), and never changes the type or
  os_types.
- `export_exception_list` returns a handle and a summary, never the list's
  content; `el-` handles are read only by the exception-list imports.
- All 30 tools set `openWorldHint=false` and tier-correct
  `readOnlyHint`/`destructiveHint`/`idempotentHint`.

## Deliberate exclusions & caveats

- **`security-ai`** (assistant + attack-discovery) is out of scope: it needs
  an Enterprise-gated `.gen-ai` connector.
- **Only query-type rule creation is buildable.** Threshold, EQL, indicator-
  match, new-terms, ES|QL, and ML rule types can be read/updated/replaced/
  enabled/disabled/deleted once they exist, but never created here.
- **Enable/disable's RMW routing is a recorded correction, not the first
  design.** The more direct bulk enable/disable action and partial `enabled`
  patch were found privilege-gated (403/500) under API-key auth; the shipped
  code instead routes both through the one full-replace RMW flow above,
  which does work under an API key.
- **Not every read shape was confirmed against a live seeded object.**
  `ExceptionItem`, `ValueList`, and `DetectionAlert` are mapped defensively —
  never confirmed against a seeded object (a firing alert cannot be seeded at
  all). `Timeline`'s shape, by contrast, was confirmed against a seeded
  timeline — narrower than the toolbox module docstring's grouping of
  "alerts, timelines" as equally unverified.
- **`get_exception_list`'s docstring overstates its own guard.** It reads
  "provide exactly one" of `id`/`list_id`, but the shipped gateway only
  requires at least one — both together is accepted. `get_detection_rule` is
  the true exactly-one case: both together is rejected. A rewrite must match
  each tool's actual accept/reject behavior, not the looser docstring.
- **Exception-list limits (D50).** `os_types` cannot be edited (the PUT
  ignores them) and are not carried by an import; `find_exception_lists`
  lists only `"single"` lists; the import's existence check and the import
  are two calls (a list created between them under the same `list_id`
  would receive the items); the overwrite's `removed_item_ids` is read
  before the import, not reported by Kibana.
- **`search_detection_alerts`'s `size` carries no schema-level bound** (no
  `ge`/`le`, unlike every other numeric parameter here) — any `int` passes
  through as a page-size cap.

## Enforcement

- **Unit** — `tests/unit/toolboxes/test_security_detections.py`: exact
  30-tool set and tier annotations; exception-entry match-type mapping;
  the exception-list edit/copy/export/import tools (fields, guards, the
  endpoint refusal, the existing-list_id refusal, removed items, the
  content-free import error);
  every identifier/field guard; value-list-item type inheritance; space
  threading/echo pair; guard-before-gateway ordering.
- **Unit, adapter-level** —
  `tests/unit/adapters/test_security_detections_gateway.py`: real captured-
  body field mapping; pagination termination on a non-full final page; the
  replace/enable/disable RMW's `from`→`from_` translation and echoed-
  identifier stripping; the immutable-guard asymmetry itself (enable/disable
  tests deliberately fetch an `immutable=true` rule to prove they don't
  refuse it).
- **Unit, cross-cutting** — `tests/unit/toolboxes/test_space_threading.py`,
  the 30 `security-detections` rows of the shared 91-tool table: threading,
  schema shape, dict-vs-list echo, not-found-as-guidance, and auth-passthrough
  shared with the other four space-aware toolboxes.
- **Contract, live Kibana** — `tests/contract/test_gateway_contract.py`:
  empty-state reads; create/delete roundtrips for rules and exception
  lists/items; update/replace field-preservation (including the `from`/
  interval/tags window); enable/disable toggling; value-list `force`
  semantics (409 without, deletes with); value-list-item CRUD; per-space
  backing-index auto-creation on first write; uninitialized-space alerts
  search returning empty; scoped-space rule isolation with suffixed
  not-found; the D50 premises (as_new_list keeps item ids, a plain import
  onto an existing list adds its items, the PUT ignores os_types and 409s a
  stale `_version`) and the edit/duplicate/export/import/overwrite tools
  end to end, including a cross-space import, an endpoint list and an
  agnostic list.
- **Unit, core** — `tests/unit/test_exception_lists_core.py` (export
  parsing, the new-copy rewrite, error text) and the handle kinds in
  `tests/unit/test_saved_objects_core.py`.
- No e2e-replay transcript and no ephemeral destructive-tier harness cover
  this toolbox; every destructive tool's only live proof is the contract
  tests' own create/delete roundtrips and teardown calls.
