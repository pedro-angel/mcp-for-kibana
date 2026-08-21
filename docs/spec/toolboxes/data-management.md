# data-management — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#data-management-toolbox · Enforcement: named per section below

## Purpose & persona

Owns two independent object types — data views (the datasets a visualization
or dashboard reads) and short URLs (shareable Kibana app links) — plus a
saved-objects export/import/overwrite trio that moves whole sets of saved
objects between spaces or takes a portable snapshot, without ever routing
their bytes through the model. It is bundled with `dashboards` in both live
profiles: `read-only-explorer` (`read` tier — browse data views, resolve
links, take an export) and `dashboards-analyst` (`write` tier — also create
data views and short URLs), because charting needs to know what datasets
exist before it can chart them. The export/import/overwrite trio additionally
serves a portability job — cloning content into a new space, or restoring a
space from a prior export — that the planned `platform-admin` profile pairs
this toolbox with.

## Surface

10 tools across three tiers (`src/kibana_mcp/toolboxes/data_management/toolbox.py`),
tagged `{"data-management", <tier>}`:

- read (4): `list_data_views`, `describe_data_view`, `resolve_short_url`, `export_saved_objects`
- write (3): `import_saved_objects`, `create_data_view`, `create_short_url`
- destructive (3): `delete_data_view`, `delete_short_url`, `overwrite_saved_objects`

A tier includes everything below it (docs/tools.md#tiers-and-annotations):
the default `write` tier exposes 7 tools, `destructive` all 10, `read` only
the 4. `export_saved_objects` sits in `read` despite writing a file on the
server, because the tier reflects Kibana-side mutation, not local disk
activity — it stays a sensitive read regardless (see Deliberate exclusions).
Per-tool arguments and return shapes: docs/tools.md#data-management-toolbox
and docs/tools.md#saved-objects-exportimport-data-management.

## Behavioral guarantees

- **Space targeting.** All 10 tools accept optional `space` per
  docs/tools.md#space-targeting. `list_data_views` never carries the `space`
  echo (the one list-returning tool here); every other tool echoes it only
  when set. Data views and short URLs are cleanly space-isolated — neither
  is one of the global-visibility exceptions docs/tools.md#space-targeting
  catalogues for other toolboxes.
- **Guard-before-gateway ordering.** `export_saved_objects`'s exactly-one-of
  `types`/`objects` check, `create_short_url`'s locator/url/`/s/`-prefix
  checks, and the import/overwrite handle-format-and-existence check all run
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
  detail text — that text can carry object bytes), and the data-view
  not-found guidance `describe_data_view` shares with the gateway port.
- **No read-modify-write, no derived identity, no confirm gate beyond
  tier.** Every write/destructive tool opens one scoped gateway and performs
  exactly one Kibana operation — unlike `dashboards`' panel tools or
  `platform-admin`'s `update_space`, nothing here fetches-then-merges-then-
  writes. Data-view and short-url ids are minted by Kibana, never derived
  (unlike the dashboards toolbox's title-derived id). `delete_data_view`,
  `delete_short_url`, and `overwrite_saved_objects` require nothing beyond
  the `destructive` tier cap — no `force`/`confirm` parameter, unlike
  `platform-admin`'s `delete_space` or `security-detections`'
  `delete_value_list`.

## Invariants

- Exposes exactly 10 tools tagged `data-management`, tiered as in Surface.
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
- **Deferred, recorded on the roadmap, not stubbed:** data-view runtime-field
  management and default-data-view get/set; granular `saved_objects` find/get
  reads (raw CRUD stays out — Kibana itself marks it deprecated).
- **Short URLs are create/resolve/delete only** — no update tool is wrapped,
  matching Kibana's own short-URL surface.

## Enforcement

- **Unit — `tests/unit/toolboxes/test_data_management.py`.** The exact
  10-tool surface; data-view create/delete and optional `time_field`;
  short-url create/resolve/delete and the locator/same-origin/`/s/` guards
  (and the no-`space` exemption); tier annotation hints; space threading and
  echo on `create_data_view`; export→import (clone, regenerated id) and
  export→overwrite (in-place, matching ids) round trips; content-free
  rejection messages and bogus-handle rejection on both import and overwrite.
- **Unit — `tests/unit/toolboxes/test_space_threading.py`.** All 10 tools
  inside the shared 63-tool cross-toolbox matrix: threading (the factory
  sees exactly the caller's space), schema (space present, optional,
  correctly patterned), echo (dict rows only, only when set), and the two
  shared error-path cases (space-not-found guidance, auth-error passthrough).
- **Contract — `tests/contract/test_gateway_contract.py`.** `test_data_views_shape`,
  `test_create_and_delete_data_view_live`, `test_short_url_lifecycle_live`,
  `test_saved_objects_export_import_roundtrip_live`,
  `test_read_export_confines_to_export_dir`,
  `test_export_overwrite_restores_in_place_tool_live` (drives the MCP tool
  surface, not just the gateway); the space-scoped block —
  `test_scoped_gateway_full_chain_live`,
  `test_visualization_and_short_url_scoped_live`,
  `test_cross_space_import_clones`, `test_cross_space_overwrite_replaces_twin`.
- **e2e-replay — `tests/e2e_replay/transcripts/`.** `space-dashboard.json`
  (and its gemma variant) pin `create_data_view`'s space echo through a real
  MCP client and server; `flights-dashboard.json` pins the data-view
  not-found guidance recovery as a real recorded model turn.
- **DoD.** `unit_green`, `contract_green`, and `e2e_replay_green`
  (`dod.config`) cover this toolbox as part of the whole-server gate; no
  criterion is scoped to `data-management` alone.
