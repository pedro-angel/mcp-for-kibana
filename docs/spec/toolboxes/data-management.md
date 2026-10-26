# data-management — behavior contract

Status: Draft v1.6 (2026-10-25) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#data-management-toolbox · Enforcement: named per section below

## Purpose & persona

Owns two independent object types — data views (the datasets a visualization
or dashboard reads, with their runtime fields and the space's default data
view) and short URLs (shareable Kibana app links) — plus a saved-objects
export/import/overwrite trio that moves whole sets of saved objects between
spaces or takes a portable snapshot, without ever routing their bytes
through the model, and a find/get pair: the find lists saved objects of
any type Kibana serves by title and name, the get reads one object of an
analytics content type in full. It is bundled with `dashboards` in both live
profiles: `read-only-explorer` (`read` tier — browse data views and saved
objects, resolve links, take an export) and `dashboards-analyst` (`write`
tier — also create and edit data views and their runtime fields, set the
default data view and create short URLs), because charting needs to know what datasets
exist before it can chart them. The export/import/overwrite trio additionally
serves a portability job — cloning content into a new space, or restoring a
space from a prior export — that the planned `platform-admin` profile pairs
this toolbox with.

## Surface

17 tools across three tiers (`src/kibana_mcp/toolboxes/data_management/toolbox.py`),
tagged `{"data-management", <tier>}`:

- read (7): `list_data_views`, `describe_data_view`, `resolve_short_url`, `export_saved_objects`,
  `find_saved_objects`, `get_saved_object`, `get_default_data_view`
- write (6): `import_saved_objects`, `create_data_view`, `update_data_view`, `create_short_url`,
  `upsert_runtime_field`, `set_default_data_view` (both `idempotentHint=true`)
- destructive (4): `delete_data_view`, `delete_short_url`, `overwrite_saved_objects`,
  `delete_runtime_field`

A tier includes everything below it (docs/tools.md#tiers-and-annotations):
the default `write` tier exposes 13 tools, `destructive` all 17, `read` only
the 7. `export_saved_objects` sits in `read` despite writing a file on the
server, because the tier reflects Kibana-side mutation, not local disk
activity — it stays a sensitive read regardless (see Deliberate exclusions).
Per-tool arguments and return shapes: docs/tools.md#data-management-toolbox
and docs/tools.md#saved-objects-exportimport-data-management.

## Behavioral guarantees

- **Space targeting.** All 17 tools accept optional `space` per
  docs/tools.md#space-targeting. `list_data_views` never carries the `space`
  echo (the one list-returning tool here); every other tool echoes it only
  when set. Data views and short URLs are cleanly space-isolated — neither
  is one of the global-visibility exceptions docs/tools.md#space-targeting
  catalogues for other toolboxes. The default data view is a per-space
  setting, and the saved-objects reads see only the objects of the space
  the gateway is scoped to.
- **Guard-before-gateway ordering.** `export_saved_objects`'s exactly-one-of
  `types`/`objects` check, `create_short_url`'s locator/url/`/s/`-prefix
  checks, `update_data_view`'s at-least-one-field and no-blank-field checks,
  the import/overwrite handle-format-and-existence check,
  `find_saved_objects`' type, search, reference and result-window checks,
  `get_saved_object`'s blank-argument check, and `upsert_runtime_field`'s
  and `delete_runtime_field`'s blank-name/script checks all run
  before the tool obtains a gateway via `deps.gateway_factory(space)` on the
  gateway PORT (`ports/gateway.py`) — a guard failure performs no space
  validation and reaches Kibana never.
- **Handle-based saved-objects flows.** `export_saved_objects` returns a
  handle plus a summary — the exported NDJSON itself never appears in a tool
  result. The handle is a bearer token confined by construction to the
  server's export directory; its not-found error never carries the
  `(in space '<id>')` suffix used elsewhere (handles are not per-space
  objects). `import_saved_objects` always clones (Kibana regenerates every
  destination id; nothing existing is touched). `overwrite_saved_objects`
  restores in place — destination id equals source id — only when the
  target space is the handle's export space; a different target space mints
  one new destination id on first restore there and replaces (never
  duplicates) it on every later restore.
- **Error-guidance strings are product contract**, pinned verbatim by the
  unit and e2e-replay tiers: the locator/url/`/s/`-prefix rejections, the
  exactly-one-selector message, the `<verb> failed for handle '<handle>'`
  message (verb is `import`/`restore`, never the Kibana rejection's own
  detail text — that text can carry object bytes), the data-view
  not-found guidance `describe_data_view` shares with the gateway port, and
  `create_data_view`'s and `update_data_view`'s time-field rejections
  (which name the pattern's date fields), `create_data_view`'s
  duplicate-name guidance (which names `update_data_view`), and
  `update_data_view`'s wrong-id guidance (which names `list_data_views`,
  D17).
- **Data-view creation checks the time field and leaves a refused call
  without effect (D43).** Probed on 9.5.4: Kibana's data-view create
  (`POST /api/data_views/data_view`) stores any string as the time field
  (`@timestamp` on the flights sample, which has only `timestamp`), and no
  public route lists a pattern's fields without creating a view (the
  fields-for-wildcard route is internal). The create returns the view's
  `fields`, so `create_data_view` checks the time field against them — it
  must be a `date` field (a `date_nanos` mapping is typed `date` there) —
  and on a mismatch deletes the view it just created and fails with
  `... (no data view was created)`. If that delete fails, the error keeps
  the rejection and the delete's own error, names the view's id, and names
  the two calls that fix or remove it (`update_data_view`,
  `delete_data_view`). A pattern that matches no index has no fields, so its
  time field is not checked, as for `update_data_view`. Kibana refuses a
  second view under a name already used (the name defaults to the pattern):
  `Duplicate data view: <name>`; the error then adds the id of the view
  that holds the name when one view does, and names `update_data_view` and
  passing a different name. A second view over the same pattern under a
  different name is accepted.
- **In-place data-view edits read first and leave a refused call without
  effect (D36).** `update_data_view` reads before it writes and may write
  twice. Probed on 9.5.4: Kibana's data-view update
  (`POST /api/data_views/data_view/{id}`) merges the keys it is sent, so
  omitted fields survive without a re-send; it validates no value
  semantically — a time field that does not exist or is a keyword is
  stored, `""` is stored as a time field, an empty title is stored and then
  matches every index, duplicate names and titles are accepted — and it
  rejects only type errors (`null` time field, unknown keys); a view's
  fields are recomputed from the pattern on every read and write (with or
  without `refresh_fields`), and are empty for a pattern that matches no
  index. So the tool reads the view (not-found surfaces here), checks that
  the time field to keep is a `date` field of the view, and sends only the
  changed keys. When `index_pattern` changes, the new pattern's fields are
  known only after the write, so it writes the pattern alone, checks the
  time field (passed, or kept) against the returned fields, and on a
  mismatch writes the old pattern back and fails; only then are `name` and
  `time_field` written. Kibana has no transaction across the two writes, so
  when the second one fails — the restore, or the `name`/`time_field`
  write — the error keeps the original cause and states what the view now
  holds (the new pattern) and the `update_data_view` call that finishes or
  undoes it. An empty field list skips the check (a pattern for
  data not yet ingested is legitimate, as `create_data_view` allows).
- **Runtime fields read first and refuse what they could not put back
  (D48).** Probed on 9.5.4: Kibana's runtime-field PUT creates or replaces
  and compiles nothing — a script that does not compile is stored, and
  Elasticsearch then fails every search whose runtime mappings carry it,
  even one that does not use the field — so `upsert_runtime_field`'s
  description states that and the way back (another upsert, or
  `delete_runtime_field`). The upsert reads the view: a name the view has
  as a mapped or scripted field, a stored composite field and a
  composite's subfield are refused before any write; a create uses the
  POST (which 400s "Field [name = x] already exists." for a name taken in
  the meantime, answered with "call upsert_runtime_field again"); a
  replacement uses the PUT, re-sends the stored custom label and
  description (the PUT drops them otherwise, probed) and returns the
  replaced definition as `previous`. `type` is the seven single-value types
  (`composite` is not offered). `delete_runtime_field` reads the view too,
  because Kibana's 404 does not tell a missing name from a mapped field or a
  subfield; a mapped field and a composite's subfield are refused naming
  what to delete instead, any other miss lists the view's runtime fields,
  and it returns the deleted definition (a composite's `fields` included).
- **The default data view is checked and always replaced (D48).** Kibana
  stores any id as a space's default and, without `force`, answers 200 and
  keeps a default that is set (probed), so `set_default_data_view` reads
  the view first (D17 guidance on a miss), always sends `force: true`, and
  returns `previous_id` and `previous_exists` (whether that id names a
  view: a dangling one cannot be passed back, and `view_id=null` undoes it,
  since Kibana treats an id that names no view as no default);
  `view_id=null` clears the default. Kibana makes
  the first data view created in a space with no default its default, and
  keeps the id of a deleted default, so `get_default_data_view` returns
  `exists: false` for an id that names no view.
- **Saved-objects reads stay small and say what an empty page means
  (D48).** `find_saved_objects` asks Kibana for each hit's `title` and
  `name` only, matches `search` against those two only (`search_fields`;
  without it Kibana matches every attribute), returns one page (`per_page`
  ≤ 100, `page * per_page` ≤
  10,000, Kibana's result window) and adds a `note` when nothing matched —
  Kibana answers an empty page for unknown and hidden types alike — or the
  page is past the end; `types` refuses `*`. `get_saved_object` reads only
  a fixed allowlist of analytics content types
  (`core/saved_objects.py` `READABLE_TYPES`: dashboards, Lens and legacy
  visualizations, saved searches, data views, maps, tags, saved queries,
  Canvas, Graph, links and annotation groups) and refuses any other type
  before calling Kibana, naming only ways forward that work for that type
  (`_UNREADABLE_WAYS`: no export for Fleet's and Synthetics' types, which
  Kibana does not export; no find for hidden ones; the toolbox serving each
  named tool, with how to enable it, since the profiles that ship
  `get_saved_object` publish none of them; for an unknown type, that no
  tool reads it, plus find and export each with its condition): Kibana strips only attributes registered as
  encrypted, and Fleet's `fleet-proxy` and `ingest-outputs` keep
  certificate keys, proxy headers, `config_yaml` and service tokens in
  plain attributes (the fleet contract's field-allowlist rule). It leaves out
  any attribute longer than 4,000 characters (`omitted_attributes` gives
  each one's size), returns the distinct references, and its miss names
  `find_saved_objects`. Both use Kibana's deprecated saved-objects routes
  (no public replacement finds across types or by reference).
- **No derived identity, no confirm gate beyond tier.** Every other
  write/destructive tool opens one scoped gateway and performs exactly one
  Kibana operation, except `create_data_view`'s cleanup delete after a
  refused time field and its id lookup after a duplicate name, and the
  reads before a write in `upsert_runtime_field`, `delete_runtime_field`
  and `set_default_data_view`. Data-view and short-url ids are minted by Kibana, never derived
  (unlike the dashboards toolbox's title-derived id). `delete_data_view`,
  `delete_short_url`, `delete_runtime_field` and `overwrite_saved_objects` require nothing beyond
  the `destructive` tier cap — no `force`/`confirm` parameter, unlike
  `platform-admin`'s `delete_space` or `security-detections`'
  `delete_value_list`.

## Invariants

- Exposes exactly 17 tools tagged `data-management`, tiered as in Surface.
- Every tool accepts optional `space` (`^[a-z0-9_-]+$`, no maximum length),
  byte-identical to today's behavior when omitted.
- Call order is always: a tool's own argument guard (if any) → space
  existence validation → the Kibana operation; a guard failure short-circuits
  before any space check, with guidance naming `list_spaces`/`create_space`.
- Every dict result carries `"space"` only when the caller set it;
  `list_data_views`' list result never carries it, set or not.
- `export_saved_objects` returns a handle plus a summary; the exported NDJSON
  itself must never appear in the tool result, and a handle's file resolution
  must stay confined to the server's export directory.
- `import_saved_objects` must regenerate every destination id and must never
  modify an existing object, regardless of the handle's source space.
- `overwrite_saved_objects` must set destination id equal to source id when
  the target space is the handle's export space; targeting a different space
  must mint one new destination id on the first restore there and must
  replace — never duplicate — that object on every later restore.
- A Kibana rejection during import or overwrite must name the handle and the
  verb (`import`/`restore`) but must never include the rejection's own detail
  text.
- `create_short_url` must accept only `locator_id="LEGACY_SHORT_URL_LOCATOR"`,
  a single-leading-slash same-origin `params.url` (rejecting `//` and `/\`),
  and, only when `space` is set, must also reject a `/s/`-prefixed
  `params.url` — without `space`, a `/s/`-prefixed path must still pass and
  land in the default space.
- `describe_data_view`'s argument must resolve against a data view's id,
  name, or index pattern interchangeably.
- Data views and short URLs must be visible only through a gateway scoped to
  the space that holds them.
- `update_data_view` must change only the fields passed, must refuse a call
  that passes none, and must never leave a data view with a time field that
  is not a `date` field of its pattern whenever that pattern has fields; a
  call it refuses must leave the data view as it found it, and when a
  later write fails after the pattern was written, its error must say the
  pattern changed and name the call that undoes it.
- `create_data_view` must never leave a data view whose time field is not a
  `date` field of its pattern whenever that pattern has fields: a call it
  refuses must leave no data view behind, and when the cleanup delete fails,
  its error must name the view's id and the calls that fix or remove it.
- `update_data_view`'s result must carry `field_count`, never the field map
  (a broad pattern has thousands of fields; `describe_data_view` returns
  them).
- `upsert_runtime_field` must never replace a runtime field without
  returning the definition it replaced, must never write over a mapped
  field or a stored composite field, and must keep a replaced field's
  custom label and description.
- `delete_runtime_field` must return the definition it deleted and must
  delete nothing when the name is not a runtime field of the view.
- `set_default_data_view` must never store an id that names no data view in
  the space, and must return the default it replaced.
- `find_saved_objects` must never return more than each hit's summary, and
  an empty result must say that unknown and hidden types also answer empty.
- `get_saved_object` must never return an attribute longer than 4,000
  characters; it lists such attributes by size.
- `get_saved_object` must never fetch an object whose type is outside
  `READABLE_TYPES`; `find_saved_objects` must never match `search` against
  an attribute other than `title` and `name`.
- `set_default_data_view` must say whether the default it replaced names a
  data view (`previous_exists`).

## Deliberate exclusions & caveats

- **The short-URL locator allow-list is deliberate, not partial coverage.**
  Kibana does not validate short-URL locator params — an ill-formed params
  blob can break Kibana's own client — so the tool exposes only the one
  locator (`LEGACY_SHORT_URL_LOCATOR`) whose shape is verified. Adding
  another locator requires the same verification, not just widening the list.
- **`export_saved_objects` is a sensitive read regardless of its tier.**
  `types=["*"]` exports an entire space; the `read` classification reflects
  that Kibana itself is not mutated, not that the content is low-sensitivity.
  What can actually be read is bounded by the API key's own privileges, never
  by this tool.
- **The export directory is not multi-tenant.** It is one server-side
  location shared by every caller of the process; retention is capped
  (oldest files pruned first) rather than per-caller, so concurrent HTTP
  callers can expire each other's handles. Per-caller isolation would need a
  new confinement scheme, not a wider version of the current one.
- **No server-side validation of export/import content beyond handle
  mechanics.** A handle that resolves but names a Kibana-incompatible export
  produces the generic "invalid or incompatible" rejection, not field-level
  diagnostics.
- **`update_data_view` cannot remove a time field** — Kibana rejects `null`
  and would store `""`, whose meaning the reference stack does not pin; a
  view that should have no time field is recreated. A name Kibana defaulted
  to the old pattern does not follow a pattern change (observed: it keeps
  the old pattern); the caller passes `name` too. Panels the dashboards
  toolbox built embed the pattern and time field inline
  (`data_view_spec`, observed on 9.5.4), so an edit does not reach them;
  `update_panel` rebuilds one against the edited view.
- **Deferred, recorded on the roadmap, not stubbed:** composite runtime
  fields (create and replace; delete is supported), checking a runtime
  field's script before it is stored (no public Kibana route compiles one),
  and raw saved-objects writes (create, update, delete, bulk — Kibana marks
  them deprecated and the type-specific tools cover them). The find/get
  reads use deprecated routes too, deliberately: no public replacement
  finds across types or by reference (D48).
- **Runtime-field writes are not atomic with their read.** A change another
  client stores between `upsert_runtime_field`'s read and its PUT is
  replaced, and `previous` is the definition the call read (D45/D47
  precedent: the window is within one call).
- **Short URLs are create/resolve/delete only** — no update tool is wrapped,
  matching Kibana's own short-URL surface.

## Enforcement

- **Unit — `tests/unit/toolboxes/test_data_management.py`.** The exact
  17-tool surface; data-view create/delete and optional `time_field`;
  `create_data_view`'s time-field refusal (nothing created) and its
  description stating the check;
  `update_data_view`'s partial update, guards before the factory, schema
  rejection of empty strings, time-field and wrong-id guidance, space
  threading and echo;
  short-url create/resolve/delete and the locator/same-origin/`/s/` guards
  (and the no-`space` exemption); tier annotation hints; space threading and
  echo on `create_data_view`; export→import (clone, regenerated id) and
  export→overwrite (in-place, matching ids) round trips; content-free
  rejection messages and bogus-handle rejection on both import and overwrite.
- **Unit — `tests/unit/adapters/test_data_management_gateway.py` and
  `test_id_guidance.py`.** `update_data_view` posts only the given keys,
  checks the time field before the write, writes a new pattern alone and
  restores it on a mismatch, reports a failed restore or a failed
  name/time-field write with the resulting pattern, the undo call and the
  original cause, accepts a field-less pattern, caps the listed
  date fields, keeps the `Kibana rejected the payload:` prefix, and turns an
  empty or unknown `view_id` into guidance (scoped suffix once).
  `create_data_view` refuses a missing or non-date time field and deletes
  the view it created, says to omit the time field on a pattern without
  date fields, keeps a `date` (including `date_nanos`) time field, accepts
  any time field on a pattern that matches no index, reports a failed
  cleanup with the view's id, the fix and the original cause, and turns
  Kibana's duplicate-name 400 into guidance naming the existing view's id
  and `update_data_view` (other 400s unchanged).
- **Unit — the D48 tools.** `tests/unit/toolboxes/test_data_management.py`:
  tiers and hints, the upsert's create/replace with `previous`, its type
  enum without `composite`, blank-argument guards before the factory, the
  description's compile warning; the delete's definition and misses; the
  default's get/set/clear round trip, a dangling id and `previous_exists`
  over it; the delete's mapped-field and subfield refusals; find's filters,
  notes, guards, schema bounds and title-or-name search; get's attribute
  cap, references, miss, the type allowlist before the factory and the
  description listing every allowed type. `tests/unit/adapters/test_data_management_gateway.py`: POST for a
  create and PUT for a replacement, the label and description re-sent, the
  refusals before any write, the create race, the delete's read-first
  guidance and scoped suffix, `force: true` and the unknown-id refusal,
  find's `fields`/`search_fields`/filters and summary mapping, get's
  distinct references, miss and the type refusal before Kibana, and
  `previous_exists`. `tests/unit/test_saved_objects_core.py`: the attribute cap.
- **Unit — `tests/unit/toolboxes/test_space_threading.py`.** All 17 tools
  inside the shared 92-tool cross-toolbox matrix: threading (the factory
  sees exactly the caller's space), schema (space present, optional,
  correctly patterned), echo (dict rows only, only when set), and the two
  shared error-path cases (space-not-found guidance, auth-error passthrough).
- **Contract — `tests/contract/test_gateway_contract.py`.** `test_data_views_shape`,
  `test_create_and_delete_data_view_live`, `test_short_url_lifecycle_live`,
  `test_saved_objects_export_import_roundtrip_live`,
  `test_read_export_confines_to_export_dir`,
  `test_create_data_view_time_field_guard_live` (a wrong time field is
  refused and no view is left; a duplicate name names the existing view),
  `test_update_data_view_partial_and_time_field_guard_live`,
  `test_data_view_edit_leaves_built_panels_alone_live`,
  `test_export_overwrite_restores_in_place_tool_live` (drives the MCP tool
  surface, not just the gateway); the space-scoped block —
  `test_scoped_gateway_full_chain_live`,
  `test_visualization_and_short_url_scoped_live`,
  `test_cross_space_import_clones`, `test_cross_space_overwrite_replaces_twin`,
  `test_update_data_view_tool_scoped_live` (MCP surface, echo, suffix);
  the D48 block — `test_runtime_field_lifecycle_live` (create, replacement
  with `previous` and the kept label, a stored broken script, the
  refusals, composite delete, the miss), and, through the MCP surface,
  `test_default_data_view_set_get_and_clear_scoped_live` (in a throwaway
  space: Kibana's automatic first default, replace, refused id, dangling,
  clear, with `previous_exists` over the dangling id) and
  `test_find_and_get_saved_objects_live` (the flights sample by reference,
  none for a reference to no object, by word but not by a word only in its
  description, a hidden type, the cap, distinct references, the stack's
  Fleet output and other unlisted types refused, the result window, another
  space).
- **e2e-replay — `tests/e2e_replay/transcripts/`.** `space-dashboard.json`
  (and its gemma variant) pin `create_data_view`'s space echo through a real
  MCP client and server; `flights-dashboard.json` pins the data-view
  not-found guidance recovery as a real recorded model turn.
- **DoD.** `unit_green`, `contract_green`, and `e2e_replay_green`
  (`dod.config`) cover this toolbox as part of the whole-server gate; no
  criterion is scoped to `data-management` alone.
