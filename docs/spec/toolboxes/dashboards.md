# dashboards — behavior contract

Status: Draft v1.2 (2026-10-12) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#dashboards-toolbox · Enforcement: named per section below

## Purpose & persona

The flagship job of the server: turn a plain-English chart or dashboard
request into real Kibana Lens visualizations and dashboards, and keep the
library visualizations it makes reusable: found, read, edited, deleted and
placed on dashboards by reference (D37). Sole toolbox
behind `dashboards-analyst` (data/BI analyst, dashboard builder — the
project's MVP default, `write` tier); its `read` tier alone backs
`read-only-explorer`, the small-/local-model reference persona that browses
without changing anything. Both pair it with `data-management` for
data-view discovery.

## Surface

16 tools: 4 read (`search_dashboards`, `get_dashboard`,
`search_visualizations`, `get_visualization`), 9 write (`create_dashboard`,
`create_visualization`, `update_visualization`, `add_library_panel`,
`add_panel`, the three `add_esql_*_panel`, `update_panel`; 3 of them
upsert/idempotent: `create_dashboard`, `update_visualization`,
`update_panel`), 3 destructive (`delete_dashboard`, `delete_visualization`,
`delete_panel`). Per-tool
arguments/returns, the annotation table, and the tier-visibility mechanism
(a tool above `KIBANA_MCP_TIER` is hidden at registration, not just
rejected at call time) live at docs/tools.md#dashboards-toolbox and
docs/tools.md#tiers-and-annotations. `list_data_views`/`describe_data_view`
appear alongside this toolbox in that table (the default-profile pairing)
but are owned and registered once by data-management, not here.

## Behavioral guarantees

- **Space targeting** (docs/tools.md#space-targeting) is uniform: all 16
  tools accept optional `space`; `search_dashboards` and
  `search_visualizations` (the list-returning tools) never echo it back;
  every dict-returning tool echoes `"space"` only when the caller set one.
- **Guard-before-gateway ordering.** Every tool obtains its gateway via
  `deps.gateway_factory(space)` on the gateway PORT (`ports/gateway.py`)
  inside the same `gateway_errors()` context as the tool body — a
  factory-raised space-validation failure surfaces as the same
  guidance-shaped `ToolError`, not a different path.
- **One scoped gateway per read-modify-write.** `add_panel`,
  `add_library_panel`, the three `add_esql_*_panel` tools, `update_panel`,
  `delete_panel`, and `update_visualization` each fetch, check or mutate in
  memory, and write back through the *same* `gateway_factory(space)` call,
  so a read and its paired write can never resolve to two spaces — and
  `add_library_panel` can never link a visualization from another space.
- **Derived identity + idempotent create.** `create_dashboard` never
  accepts a caller-chosen id — it derives one deterministically from the
  normalized title, so an equivalent title always acts on the same
  dashboard. Its existence probe governs `created` vs `replaced`; on
  replace, settings the tool doesn't author (`options`, `filters`, `query`,
  `refresh_interval`, `tags`, `pinned_panels`) carry over. The probe must
  distinguish "space does not exist" from "dashboard does not exist" —
  conflating them would create/replace inside a rejected space.
- **The unsupported-panels refusal.** Every read-modify-write tool and
  `create_dashboard`'s replace path inspect the fetched dashboard for
  round-trip warnings first; anything that can't be written back
  losslessly makes the tool refuse outright, untouched, naming the cause
  and pointing at the Kibana UI.
- **Positional panel addressing.** `update_panel` and `delete_panel`
  address the zero-based index `get_dashboard` reports (Kibana 9.5 also
  returns a per-panel `id`, which these tools carry through untouched but do
  not address by). `update_panel` refuses a non-`vis` target and a
  by-reference panel; `delete_panel` refuses a *section* entry (groups
  several panels), which would else cascade.
- **Library visualizations are placed by reference (D37).** Probed on
  9.5.4: the public Dashboards API stores a panel
  `{"type": "vis", "config": {"ref_id": "<visualization id>"}}` (an optional
  `title` beside `ref_id` overrides the panel title), saves it as a `lens`
  saved-object reference, and reads it back unchanged with no warnings — so
  it does not trip the unsupported-panels refusal, and `add_panel`, the
  ES|QL appends and `delete_panel` carry it through their read-modify-write
  untouched (contract-tested). `add_library_panel` appends one at the
  bottom, half width, after checking that the visualization exists in the
  same space: Kibana itself stores a `ref_id` that names nothing.
  `get_dashboard` reports such a panel with `visualization_id` and the
  library item's title (a per-panel `title` override wins); inline panels
  never gain the key, so their result shape is unchanged. `update_panel` on
  a by-reference panel refuses before any write: "panel {i} shows library
  visualization '{id}' — change it with update_visualization, which updates
  every dashboard that shows it" (writing an inline config over it would
  silently unlink it). When the referenced item no longer exists that
  advice dead-ends, so the refusal instead reads "panel {i} shows library
  visualization '{id}', which no longer exists — remove the broken panel with
  delete_panel (destructive tier), then place a chart with add_panel or
  add_library_panel". `create_dashboard`'s replace drops it with the other
  panels; the library item is untouched.
- **Library edits never create, and deletes can dangle.** Kibana's
  visualization PUT is an upsert, so `update_visualization` reads the id
  first (a miss is guidance naming `search_visualizations`), then runs the
  same pre-flight VizSpec validation as `create_visualization`, then
  replaces the stored config in place; every by-reference panel shows the
  change. Kibana deletes a visualization that dashboards reference (204,
  probed): their panels keep the dangling `ref_id`, still round-trip, and
  `get_dashboard` reports them with an empty title. `delete_visualization`'s
  description says so and names `delete_panel` as the cleanup.
- **`search_visualizations` matches title words.** Kibana's `query`
  matches whole title words, case-insensitively (`probe`, `PROBE`, `lib`
  and `probe lib` all found `probe-lib-viz`); results carry `id`, `title`,
  `description` and the VizSpec `chart_type`. kibana-py's `fields` /
  `search_fields` parameters 500 on live Kibana and are not used.
- **`get_visualization` speaks VizSpec.** It reads the stored Lens config
  back (`describe_lens_config` in `core/visualizations/translate.py`, the
  one module that knows Lens shapes): `chart_type` in VizSpec terms
  (`data_table` → `table`, an `xy` chart by its first layer: a breakdown
  bar reads back as `bar_stacked` → `bar`), `data_view` (the index pattern
  the config embeds), `time_field`, `metrics` (`agg`/`field`/`label`),
  `group_by` (`field`/`kind`/`limit`), and the KQL `query` its filters
  became. A shape it does not know (a UI-made chart, a `legacy_metric`)
  yields Kibana's own type name and fewer entries, never an error.
- **Error-guidance strings are product contract**, not incidental text:
  the pre-flight spec-validation message (docs/tools.md#error-behavior) is
  pinned verbatim by the e2e-replay tier — a real model recovered using
  that exact wording. The unsupported-panels refusal message is pinned by
  the unit suite (tests/unit/toolboxes/test_dashboards_write.py), not yet
  by a recorded turn.
- **No confirm gate beyond tier.** `delete_dashboard`/`delete_panel` sit
  behind `KIBANA_MCP_TIER=destructive` and take no `confirm` parameter —
  tier visibility is the only gate this toolbox adds, atop the API key's
  own RBAC.

## Invariants

- All 16 tools accept optional `space` (`^[a-z0-9_-]+$`) threaded through
  exactly one `gateway_factory(space)` call per invocation.
- `search_dashboards` and `search_visualizations` never gain a `space` key;
  every other tool's dict result gains one only when `space` was passed.
- `create_dashboard`'s id is a pure function of the normalized title —
  never caller-supplied, never random.
- A blank/whitespace-only `title` is rejected before any gateway call.
- `create_dashboard` must not treat a space-not-found probe error as
  "dashboard does not exist."
- `create_dashboard` (replace path), `add_panel`, `add_library_panel`, the
  `add_esql_*_panel` tools, `update_panel`, and `delete_panel` must refuse
  to write when the fetched dashboard cannot round-trip losslessly, before
  any mutation.
  (`delete_panel`'s refusal is currently unpinned by a dedicated unit
  test — a rewrite must preserve it regardless.)
- `update_panel`/`delete_panel` must validate `panel_index` range before
  any write; `update_panel` refuses non-`vis` and by-reference panels,
  naming `delete_panel` instead of `update_visualization` when the
  referenced visualization no longer exists;
  `delete_panel` refuses a section entry.
- VizSpec-based tools (`create_dashboard`, `create_visualization`,
  `update_visualization`, `add_panel`, `update_panel`) must fully
  pre-flight-validate against the real, just-fetched data view — one
  batched error — before any write.
- A dashboard any tool here touched must stay editable by every other tool
  here: by-reference panels round-trip through every read-modify-write.
- `add_library_panel` must never write a `ref_id` it has not just read back
  as an existing visualization in the same space; `update_visualization`
  must never create a visualization.
- `get_dashboard` adds `visualization_id` only to by-reference panels.
- An omitted `time_field` is valid in every VizSpec, `date_histogram`
  included: it is auto-filled from the data view before the pre-flight,
  so no parse-time rule may reject it. Only a `date_histogram` on a data
  view with no default time field, and no `time_field` given, is
  rejected — by the pre-flight, naming the field to set (D32).
- `add_esql_*_panel` tools must never validate the query or its columns
  server-side; a wrong query/column still yields a created panel.
- ES|QL panels can only be appended to an existing dashboard, never created
  as a standalone library visualization.
- Every dashboard-scoped `url` is
  `{public_kibana_url}{/s/<space> if space else ""}/app/dashboards#/view/{dashboard_id}`.
- `delete_dashboard`/`delete_panel` require no parameter beyond tier gating.
- Every tool taking a `dashboard_id` answers an empty one with "dashboard_id is
  empty — pass the id that create_dashboard or search_dashboards returns"
  (before this, kibana-py's unmapped ValueError reached the caller as a bare
  "Error calling tool"), and an id that matches no dashboard with "dashboard
  '<id>' not found — dashboard_id is the id … not the dashboard's title; call
  search_dashboards to find it" (D17; the space suffix still applies).
- Every tool taking a `visualization_id` answers the same way: "visualization_id
  is empty — pass the id that create_visualization or search_visualizations
  returns", and "visualization '<id>' not found — visualization_id is the id
  that create_visualization or search_visualizations returns, not the
  visualization's title; call search_visualizations to find it".

## Deliberate exclusions & caveats

- **Point/pin-map visualizations are out of scope.** Kibana 9.4 has no
  public API for them (Maps-app-only). The one geo visualization reachable
  through the public API — a `region_map` choropleth — **is** a supported
  `chart_type`, live-tested against real Kibana
  (tests/contract/test_gateway_contract.py, the every-chart-type case).
- **ES|QL is a deliberately separate tool family, not folded into
  `VizSpec`** — a small field-based schema is a reliability requirement
  for small/local models, not an oversight — and **is never
  server-side-validated** at this seam: the gateway has no
  query-execution path to check against, only a Kibana-side
  panel-structure check.
- **`list_data_views`/`describe_data_view` stay out of this toolbox** —
  owned by data-management so the data-view namespace has one owner; this
  toolbox's docstrings still name `describe_data_view` since the default
  profile pairs the two.
- **Sections cannot be edited by these tools** — `delete_panel`'s refusal
  is permanent: structural dashboard edits stay Kibana-UI-only.
- **`create_dashboard`'s idempotency is title-based, not content-based** —
  two calls with different panels but the same normalized title collapse
  to one dashboard (a replace), by design.
- **Panels from `add_panel`/`create_dashboard` are inline copies**, not
  library items; `update_visualization` does not reach them, and there is no
  tool that turns an inline panel into a library item.
- **`delete_visualization` does not look for referencing dashboards.**
  Kibana answers which dashboards reference an item only through the
  saved-objects `has_reference` search (deprecated raw saved-objects
  surface, kept out); scanning every dashboard per delete is not done.
  Recorded on the roadmap.
- **Mosaic accepts only one `group_by` on Kibana 9.5.4** ("Only a single
  non-collapsed dimension is allowed for group_by", observed while probing
  D37), although VizSpec allows two; a two-group mosaic is rejected by
  Kibana with the `Kibana rejected the payload:` prefix. Recorded on the
  roadmap, not changed here.

## Enforcement

- **Unit — test_dashboards_read.py**: read-tool registration/annotations,
  not-found guidance, empty-search shape.
- **Unit — test_dashboards_first_call.py**: the efficiency study's
  recorded first `create_dashboard` calls (date_histogram panels without
  `time_field`) create the dashboard with the auto-filled time field; the
  no-time-field data view gets the fix-naming pre-flight error and no
  write (D32).
- **Unit — test_dashboards_write.py**: pre-flight validation +
  suggestions; ESQL appends and arg guards; title-based idempotency
  (`created`/`replaced`, dedup, settings preservation, unroundtrippable
  refusal, space-not-found-probe non-swallow); `update_panel`/
  `delete_panel` index and type/section guards; the annotation matrix; the
  space matrix (factory scoping, echo, `/s/<space>` URL prefix across all
  6 URL-emitting tools × `{explicit, "default", omitted}`).
- **Unit — test_dashboards_library.py**: the five library tools' annotations,
  search/filter, the VizSpec-shaped read, empty-id guidance, in-place
  replace with pre-flight before any write, wrong id checked first and
  nothing created, the linked panel and its `get_dashboard` row (inline rows
  unchanged), the unknown-item and unroundtrippable refusals, a dashboard
  holding a library panel staying editable by `add_panel`, the ES|QL
  appends, `update_panel` (inline) and `delete_panel`, the pinned
  `update_panel` refusal texts (live item, and a dangling ref naming
  `delete_panel`), `create_dashboard` replace, an edit reaching
  the linked panel, and the dangling panel after a delete.
- **Unit — tests/unit/adapters/test_visualization_gateway.py,
  tests/unit/visualizations/test_describe.py**: paging and query passing,
  the read-before-PUT that keeps an update from creating, by-reference panel
  titles (library title, override, dangling); every chart type built reads
  back as its spec; unknown shapes never raise.
- **Unit — test_space_threading.py**: the 86-tool cross-toolbox space
  contract (all 16 dashboards tools included) — threading, schema, echo,
  and error-path (`gateway_errors()` wraps factory errors) parity.
- **Contract — test_gateway_contract.py**: live lifecycle round-trip with
  zero warnings; live idempotent-create through the actual tool; long-title
  id derivation; a date_histogram panel without `time_field` auto-filled
  and round-tripped with zero warnings; missing-dashboard-not-found; all three ES|QL panel shapes
  round-tripping losslessly; the space-scoped chain (invisible from the
  default space, 409 on a same-id twin create, only the owning space's
  delete removes it); the library loop
  (`test_library_visualization_read_update_delete_live`: search, read in
  VizSpec terms, replace in place, a wrong-id update creating nothing;
  `test_library_panel_keeps_the_dashboard_editable_live`: the by-reference
  panel round-trips with zero warnings through `add_panel`, `update_panel`
  refuses it, an edit reaches it, a deleted item leaves a dangling panel the
  tools still edit around and remove, and a dangling id is refused by
  `add_library_panel`; `test_library_tools_scoped_live`: the five tools in a
  space with echo, `/s/` URL and the suffix once).
- **e2e-replay — tests/e2e_replay/**: `flights-dashboard.json` replays a
  recorded model turn recovering from a bad `time_range`, an unknown data
  view, and two bad field names before it builds the dashboard, pinning
  the pre-flight guidance verbatim. `space-dashboard.json` replays an
  authored space-scoped `create_dashboard` sequence;
  `space-dashboard-gemma-4-12b-qat.json` replays a REAL model-chosen turn
  over the same surface — together pinning space-echo and URL-prefix
  behavior through a real client/server.
- `tests/unit/adapters/test_id_guidance.py` — the empty-id and unknown-id
  guidance for dashboards, visualizations and alert rules, and that a
  missing space still reports the space.
