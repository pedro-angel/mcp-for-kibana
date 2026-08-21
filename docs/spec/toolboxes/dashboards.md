# dashboards — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#dashboards-toolbox · Enforcement: named per section below

## Purpose & persona

The flagship job of the server: turn a plain-English chart or dashboard
request into real Kibana Lens visualizations and dashboards. Sole toolbox
behind `dashboards-analyst` (data/BI analyst, dashboard builder — the
project's MVP default, `write` tier); its `read` tier alone backs
`read-only-explorer`, the small-/local-model reference persona that browses
without changing anything. Both pair it with `data-management` for
data-view discovery.

## Surface

11 tools: 2 read, 7 write (2 upsert/idempotent), 2 destructive. Per-tool
arguments/returns, the annotation table, and the tier-visibility mechanism
(a tool above `KIBANA_MCP_TIER` is hidden at registration, not just
rejected at call time) live at docs/tools.md#dashboards-toolbox and
docs/tools.md#tiers-and-annotations. `list_data_views`/`describe_data_view`
appear alongside this toolbox in that table (the default-profile pairing)
but are owned and registered once by data-management, not here.

## Behavioral guarantees

- **Space targeting** (docs/tools.md#space-targeting) is uniform: all 11
  tools accept optional `space`; `search_dashboards` (the one
  list-returning tool) never echoes it back; every dict-returning tool
  echoes `"space"` only when the caller set one.
- **Guard-before-gateway ordering.** Every tool obtains its gateway via
  `deps.gateway_factory(space)` on the gateway PORT (`ports/gateway.py`)
  inside the same `gateway_errors()` context as the tool body — a
  factory-raised space-validation failure surfaces as the same
  guidance-shaped `ToolError`, not a different path.
- **One scoped gateway per read-modify-write.** `add_panel`, the three
  `add_esql_*_panel` tools, `update_panel`, and `delete_panel` each fetch,
  mutate in memory, and write back through the *same*
  `gateway_factory(space)` call, so a read and its paired write can never
  resolve to two spaces.
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
- **Positional panel addressing.** Panels have no id; `update_panel` and
  `delete_panel` address the zero-based index `get_dashboard` reports.
  `update_panel` refuses a non-`vis` target; `delete_panel` refuses a
  *section* entry (groups several panels), which would else cascade.
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

- All 11 tools accept optional `space` (`^[a-z0-9_-]+$`) threaded through
  exactly one `gateway_factory(space)` call per invocation.
- `search_dashboards` never gains a `space` key; every other tool's dict
  result gains one only when `space` was passed.
- `create_dashboard`'s id is a pure function of the normalized title —
  never caller-supplied, never random.
- A blank/whitespace-only `title` is rejected before any gateway call.
- `create_dashboard` must not treat a space-not-found probe error as
  "dashboard does not exist."
- `create_dashboard` (replace path), `add_panel`, the `add_esql_*_panel`
  tools, `update_panel`, and `delete_panel` must refuse to write when the
  fetched dashboard cannot round-trip losslessly, before any mutation.
  (`delete_panel`'s refusal is currently unpinned by a dedicated unit
  test — a rewrite must preserve it regardless.)
- `update_panel`/`delete_panel` must validate `panel_index` range before
  any write; `update_panel` refuses non-`vis`; `delete_panel` refuses a
  section entry.
- VizSpec-based tools (`create_dashboard`, `create_visualization`,
  `add_panel`, `update_panel`) must fully pre-flight-validate against the
  real, just-fetched data view — one batched error — before any write.
- `add_esql_*_panel` tools must never validate the query or its columns
  server-side; a wrong query/column still yields a created panel.
- ES|QL panels can only be appended to an existing dashboard, never created
  as a standalone library visualization.
- Every dashboard-scoped `url` is
  `{public_kibana_url}{/s/<space> if space else ""}/app/dashboards#/view/{dashboard_id}`.
- `delete_dashboard`/`delete_panel` require no parameter beyond tier gating.

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

## Enforcement

- **Unit — test_dashboards_read.py**: read-tool registration/annotations,
  not-found guidance, empty-search shape.
- **Unit — test_dashboards_write.py**: pre-flight validation +
  suggestions; ESQL appends and arg guards; title-based idempotency
  (`created`/`replaced`, dedup, settings preservation, unroundtrippable
  refusal, space-not-found-probe non-swallow); `update_panel`/
  `delete_panel` index and type/section guards; the annotation matrix; the
  space matrix (factory scoping, echo, `/s/<space>` URL prefix across all
  5 URL-emitting tools × `{explicit, "default", omitted}`).
- **Unit — test_space_threading.py**: the 63-tool cross-toolbox space
  contract (all 11 dashboards tools included) — threading, schema, echo,
  and error-path (`gateway_errors()` wraps factory errors) parity.
- **Contract — test_gateway_contract.py**: live lifecycle round-trip with
  zero warnings; live idempotent-create through the actual tool; long-title
  id derivation; missing-dashboard-not-found; all three ES|QL panel shapes
  round-tripping losslessly; the space-scoped chain (invisible from the
  default space, 409 on a same-id twin create, only the owning space's
  delete removes it).
- **e2e-replay — tests/e2e_replay/**: `flights-dashboard.json` replays a
  recorded model turn recovering from a bad `time_range`, an unknown data
  view, and two bad field names before it builds the dashboard, pinning
  the pre-flight guidance verbatim. `space-dashboard.json` replays an
  authored space-scoped `create_dashboard` sequence;
  `space-dashboard-gemma-4-12b-qat.json` replays a REAL model-chosen turn
  over the same surface — together pinning space-echo and URL-prefix
  behavior through a real client/server.
