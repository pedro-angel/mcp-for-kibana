# Tool reference

Toolboxes are selected via
[`KIBANA_MCP_TOOLBOXES`](configuration.md#toolbox-selection), or single tools
via [`KIBANA_MCP_TOOLS`](configuration.md#tool-selection): **dashboards**,
**[data-management](#data-management-toolbox)**,
**[alerting](#alerting-toolbox)**, **[cases](#cases-toolbox)**,
**[platform-health](#platform-health-toolbox)**,
**[observability](#observability-toolbox)**,
**[security-detections](#security-detections-toolbox)**,
**[platform-admin](#platform-admin-toolbox)**,
**[streams](#streams-toolbox)**, and **[fleet](#fleet-toolbox)** — 162 tools in
all. This reference details every toolbox in full. A drift-guard test
(`tests/unit/test_docs_tool_reference.py`) asserts that every registered tool
name appears on this page, so it cannot silently fall behind the code.
[`KIBANA_MCP_TIER`](configuration.md#tier-semantics) caps which tiers are
visible to the model — a tool above the cap is not merely denied at call
time, it never appears in the tool list (registered, then hidden via
FastMCP's visibility API).

## dashboards toolbox

| Tool | Tier | Description |
|---|---|---|
| [`list_data_views`](#list_data_views) | read | List Kibana data views (the datasets you can visualize). |
| [`describe_data_view`](#describe_data_view) | read | Get a data view's fields and types. |
| [`search_dashboards`](#search_dashboards) | read | Search dashboards by title/description. |
| [`get_dashboard`](#get_dashboard) | read | Get a dashboard summary: title, description, and its panels. |
| [`create_dashboard`](#create_dashboard) | write | Create a dashboard from one or more visualization specs; re-creating the same title overwrites it in place instead of duplicating. |
| [`search_visualizations`](#search_visualizations) | read | Search library visualizations by title. |
| [`get_visualization`](#get_visualization) | read | Get a library visualization in VizSpec terms: chart type, data view, metrics, group_by, filter query. |
| [`create_visualization`](#create_visualization) | write | Create a reusable visualization in the library (not on a dashboard). |
| [`update_visualization`](#update_visualization) | write | Replace a library visualization with a new spec, same id; dashboards that show it by reference change too. |
| [`add_library_panel`](#add_library_panel) | write | Show a library visualization on an existing dashboard, linked (by reference), not copied. |
| [`add_panel`](#add_panel) | write | Add a visualization panel to an existing dashboard. |
| [`add_esql_metric_panel`](#esql-panels-dedicated-add_esql__panel-tools) | write | Add an ES\|QL metric panel to an existing dashboard: one output column as a single number. |
| [`add_esql_table_panel`](#esql-panels-dedicated-add_esql__panel-tools) | write | Add an ES\|QL table panel to an existing dashboard: grouping columns as rows, metric columns as values. |
| [`add_esql_xy_panel`](#esql-panels-dedicated-add_esql__panel-tools) | write | Add an ES\|QL bar/line/area panel to an existing dashboard. |
| [`update_panel`](#update_panel) | write | Replace the visualization at a given panel index. |
| [`delete_dashboard`](#delete_dashboard) | destructive | Permanently delete a dashboard. |
| [`delete_visualization`](#delete_visualization) | destructive | Permanently delete a library visualization; dashboards that show it keep a broken panel. |
| [`delete_panel`](#delete_panel) | destructive | Permanently remove one panel from a dashboard. |

A tier includes everything below it: `write` (the default) registers
read + write tools; `destructive` adds the delete tools; `read` registers
only the read-only tools. (This table shows the default `dashboards` +
`data-management` reads; the other toolboxes are documented in their own
sections below.)

## Tiers and annotations

Every tool carries an [MCP tool annotation](https://modelcontextprotocol.io)
hint set in code (`_READ` / `_WRITE` / `_UPSERT` / `_DESTRUCTIVE` in
`toolbox.py`), plus tags `{"dashboards", <tier>}` used for visibility
gating:

| Tier | Tools | `readOnlyHint` | `destructiveHint` | `idempotentHint` |
|---|---|---|---|---|
| read | `list_data_views`, `describe_data_view`, `search_dashboards`, `get_dashboard`, `search_visualizations`, `get_visualization` | `true` | — | — |
| write | `create_visualization`, `add_library_panel`, `add_panel`, `add_esql_metric_panel`, `add_esql_table_panel`, `add_esql_xy_panel` | `false` | `false` | `false` |
| write (upsert) | `create_dashboard`, `update_panel`, `update_visualization` | `false` | `false` | `true` — `update_panel`/`update_visualization`: replacing with the same spec twice is a no-op; `create_dashboard`: re-creating the same title overwrites, never duplicates. |
| destructive | `delete_dashboard`, `delete_visualization`, `delete_panel` | `false` | `true` | `true` |

All tools also set `openWorldHint=False` (Kibana is a closed, known system,
not an open web search). The two tools elsewhere that reach past Kibana —
alerting's `execute_connector` and observability's `test_synthetic_monitor` —
set `openWorldHint=True`.

At server assembly (`server.py`), `KIBANA_MCP_TIER` (default `write`)
computes the set of *disallowed* tier tags and calls FastMCP's
`mcp.disable(tags={tier_tag})` for each — so with the default tier, the
`destructive` tools are registered internally but hidden from the tool list
the model ever sees, not just rejected if called. Set
`KIBANA_MCP_TIER=destructive` to allow deletion, or `KIBANA_MCP_TIER=read`
for a look-but-don't-touch deployment.

## The VizSpec input

`create_dashboard` (via its `panels` list), `create_visualization`,
`add_panel`, and `update_panel` all take one or more **VizSpec** objects —
the flat, LLM-friendly chart description defined in
`src/kibana_mcp/core/visualizations/spec.py`. It is deliberately small:
every field must be guessable by a 14B local model from a one-sentence
request plus a `describe_data_view` field listing.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `title` | string (min length 1) | — | Human-readable chart title. |
| `chart_type` | `line` \| `area` \| `bar` \| `pie` \| `metric` \| `table` | — | See below. |
| `data_view` | string | — | Data view name, index pattern, or id. |
| `time_field` | string \| null | `null` | Time field of the data view. See [auto-fill](#the-auto-filled-time_field) below. |
| `metrics` | list of `MetricSpec` (1+) | — | What to measure. |
| `group_by` | list of `GroupBySpec` | `[]` | How to split the data (x axis / slices / rows). |
| `filters` | list of `FilterSpec` | `[]` | Optional equality filters, ANDed together. |

### Chart types and their group_by rules

Each `chart_type` enforces its own shape via a Pydantic model validator
(`VizSpec._chart_rules`) — an invalid combination is rejected before the
tool call ever executes, as a schema-validation error on the arguments
themselves (not a `describe_data_view`-driven pre-flight check):

| `chart_type` | Meaning | `group_by` count | Notes |
|---|---|---|---|
| `line` / `area` / `bar` | Compare a metric across groups or time | 1–2 | 1 = x axis; 2nd = series split/breakdown. `bar` with 2 becomes a stacked bar. |
| `pie` | Proportions | 1–2 | Exactly **1** metric required; every `group_by` entry must be `kind: "terms"`. |
| `metric` | A single number | 0 | `group_by` is rejected outright — a metric card has no axes. |
| `table` | Rows of data | 0–3 | Up to 3 `group_by` entries become table rows. |

> **Known limitation:** point/pin-map visualizations are not
> supported. Kibana 9.4 has no public API for point/pin maps — that
> functionality lives only in the Maps app, which isn't exposed by the
> Dashboards/Visualizations APIs this server targets. A Lens `region_map`
> choropleth (shading regions by a metric, no lat/lon points) is the one
> geo visualization reachable through the public API and is supported as a
> `chart_type`.

### MetricSpec — `metrics`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `agg` | `count` \| `sum` \| `average` \| `min` \| `max` \| `median` \| `unique_count` | — | Aggregation to compute. |
| `field` | string \| null | `null` | Field to aggregate. Required for every `agg` except `count`. |
| `label` | string \| null | `null` | Optional display label. |

`sum`, `average`, `min`, `max`, and `median` additionally require the field
to be a **numeric** data-view field — checked during pre-flight validation
(see [error behavior](#error-behavior) below), not by the model schema.

### GroupBySpec — `group_by`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `field` | string | — | Field to group by. |
| `kind` | `terms` \| `date_histogram` | `terms` | `terms` = top values; `date_histogram` = bucket over time. |
| `limit` | integer, 1–100 | `10` | Top-N buckets. Only meaningful for `terms`. |

A `date_histogram` entry needs a time field on the `VizSpec`; omit
`time_field` and the server fills in the data view's own — see
[the auto-filled `time_field`](#the-auto-filled-time_field).

### FilterSpec — `filters`

| Field | Type | Meaning |
|---|---|---|
| `field` | string | Field to filter on. |
| `eq` | string \| int \| float \| bool | Keep documents where `field` equals this value. |

Filters are combined with the Kibana Query Language: multiple entries are
ANDed together (`field1: "value1" AND field2: 42`), with string values
quoted and escaped.

### ES|QL panels — dedicated `add_esql_*_panel` tools

VizSpec is field-based (a `data_view` plus aggregations). To visualize an **ES|QL
query** instead, use the dedicated tools that add a query-driven panel to an
existing dashboard. Each references the query's **output columns** (the query does
the aggregation):

| Tool | Args (besides `dashboard_id`, `title`, `esql`) | Panel |
|---|---|---|
| `add_esql_metric_panel` | `column` | single-number metric |
| `add_esql_table_panel` | `columns` (grouping/row columns), `metric_columns` (numeric values) | table |
| `add_esql_xy_panel` | `x_column`, `y_columns`, `chart_type` (`bar`/`line`/`area`), `breakdown_column` | bar/line/area chart |

Example: `add_esql_xy_panel(dashboard_id, "Flights by carrier", "FROM kibana_sample_data_flights | STATS count = COUNT(*) BY Carrier", x_column="Carrier", y_columns=["count"])`.

Notes:

- ES|QL panels can only be **added to a dashboard**, never saved as a library
  visualization — Kibana's library API rejects an ES|QL data source.
- **No server-side query validation** (ES|QL runs on Elasticsearch, which this
  server does not connect to): a wrong query or column name yields an empty panel,
  so write the query and the column names carefully. Kibana *does* validate the
  panel **structure** (a malformed config is rejected), so the shape is checked
  even though column existence is not.
- ES|QL is a **separate set of tools on purpose** — deliberately kept out of
  VizSpec so the field-based `create_dashboard`/`add_panel` schema (and small
  local models' reliability on it) is unchanged.

### The auto-filled `time_field`

Omit `time_field`: the toolbox code (`_prepare_config` in `toolbox.py`)
looks up the named `data_view` and, when `time_field` is `null` and the
data view has a default time field, copies it onto the spec before the
pre-flight runs. This holds for every chart type, `date_histogram`
included — `group_by` names the x-axis date field, `time_field` scopes the
Lens data source, and the two need not be the same field.

Set `time_field` only when the data view has **no** default time field and
a `group_by` entry is a `date_histogram`; the pre-flight then rejects the
spec with a message naming the fix (see [error behavior](#error-behavior)).
An explicit `time_field` must match the data view's own when it has one;
call `describe_data_view` first to learn it (it's returned as the
`time_field` key).

## Returns

### `list_data_views`

Returns a list of data view summaries:

```json
[{"id": "...", "name": "...", "index_pattern": "..."}]
```

### `describe_data_view`

Returns one data view's detail, including every field name and its type
(`"number"`, `"string"`, `"date"`, …):

```json
{
  "id": "...", "name": "...", "index_pattern": "...",
  "time_field": "timestamp",
  "fields": {"Carrier": "string", "AvgTicketPrice": "number", "timestamp": "date"}
}
```

### `search_dashboards`

Returns a list of dashboard summaries:

```json
[{"id": "...", "title": "...", "description": "..."}]
```

### `get_dashboard`

Returns a dashboard summary with its panel list, each panel's index used by
`update_panel`/`delete_panel`:

```json
{
  "id": "...", "title": "...", "description": "...",
  "panels": [
    {"index": 0, "type": "vis", "title": "..."},
    {"index": 1, "type": "vis", "title": "...", "visualization_id": "..."}
  ]
}
```

Only a panel placed with [`add_library_panel`](#add_library_panel) carries
`visualization_id` (the library item it shows by reference); its `title` is
the library item's title, or `""` once that item has been deleted. Inline
panels never carry the key.

### `search_visualizations`

Returns a list of library-visualization summaries (no `space` echo, like
every list-returning tool); `query` matches whole title words,
case-insensitively, and an empty query lists all:

```json
[{"id": "...", "title": "...", "description": "", "chart_type": "bar"}]
```

### `get_visualization`

Returns one library visualization read back in [VizSpec](#the-vizspec-input)
terms, so it can be edited and passed to `update_visualization`:

```json
{
  "id": "...", "title": "...", "description": "", "chart_type": "bar",
  "data_view": "kibana_sample_data_flights", "time_field": "timestamp",
  "metrics": [{"agg": "average", "field": "AvgTicketPrice"}],
  "group_by": [{"field": "Carrier", "kind": "terms", "limit": 10}],
  "query": "Cancelled: false"
}
```

`data_view` is the index pattern the chart embeds; `query` is the KQL its
`filters` became (pass them back as `filters`). A chart made in the Kibana UI
in a shape VizSpec does not have reports Kibana's own type name (e.g.
`legacy_metric`) and only the metrics and groupings it can read.

All write/destructive tools below return a small dict with the `id` and,
where a dashboard is involved, a human-clickable `url` built as
`{public_kibana_url}/app/dashboards#/view/{dashboard_id}`
(`public_kibana_url` is `KIBANA_PUBLIC_URL` if set, else `KIBANA_URL` —
see [Configuration](configuration.md)):

### `create_dashboard`

```json
{"id": "...", "url": "...", "title": "...", "panel_count": 3, "status": "created"}
```

`status` — `created` or `replaced` (re-creating a same-titled dashboard
replaces its panels in place; filters, query, tags and display options set
outside this tool are preserved).

### `create_visualization`

```json
{"id": "...", "title": "..."}
```

No dashboard is involved, so there is no `url`.

### `update_visualization`

```json
{"id": "...", "title": "..."}
```

The library item keeps its id; its whole config is replaced by the new spec,
after the same [pre-flight validation](#pre-flight-spec-validation) as
`create_visualization`. A wrong id is refused before validation and never
creates an item (Kibana's PUT alone would). Every dashboard panel placed with
`add_library_panel` shows the change; panels added with `add_panel` or
`create_dashboard` are separate copies and do not.

### `add_library_panel`

```json
{"id": "...", "url": "...", "panel_index": 2}
```

The dashboard's `id`/`url` and the new panel's index. The visualization must
exist in the same space (Kibana itself would store a reference to nothing);
the panel is appended at the bottom, half width.

### `add_panel`

```json
{"id": "...", "url": "..."}
```

The dashboard's own `id`/`url` — not a panel id (panels aren't addressed
by id, only by index; see `get_dashboard`).

### `update_panel`

```json
{"id": "...", "panel_index": 0}
```

### `delete_dashboard`

```json
{"id": "...", "deleted": true}
```

### `delete_visualization`

```json
{"id": "...", "deleted": true}
```

Kibana deletes a library visualization even while dashboards show it. Their
panels stay behind, broken (`get_dashboard` reports them with an empty
`title`); remove each with `delete_panel`.

### `delete_panel`

```json
{"id": "...", "deleted_panel": 0}
```

## Error behavior

### Pre-flight spec validation

Before any of `create_dashboard`, `create_visualization`,
`update_visualization`, `add_panel`, or `update_panel` touch Kibana, `_prepare_config` calls
`validate_spec(spec, data_view)`
(`src/kibana_mcp/core/visualizations/validate.py`) against the real,
just-fetched data view. If validation finds any problems, the tool raises
before making a single write call, with all problems batched into one
message:

```
invalid visualization spec:
- metric 'average': field 'carrier' does not exist in the data view — did you mean 'Carrier'?
Use describe_data_view to check field names and types.
```

The specific checks, each producing its own bullet:

- **Unknown field** (metric, `group_by`, or filter): `"{where}: field
  '{field}' does not exist in the data view"`, plus either a fuzzy-matched
  suggestion (`— did you mean '{closest}'?`, via `difflib`) or a preview of
  up to 8 real field names if nothing is close.
- **Wrong type for a numeric aggregation**: `sum`/`average`/`min`/`max`/
  `median` against a non-`number` field →
  `"metric '{agg}' needs a number field but '{field}' is {type}"`.
- **`date_histogram` on a non-date field** →
  `"group_by date_histogram needs a date field but '{field}' is {type}"`.
- **Explicit `time_field` mismatch**: if the caller sets `time_field` and
  it doesn't match the data view's actual time field →
  `"time_field '{spec.time_field}' does not match the data view's time
  field '{data_view.time_field}'"`.
- **`date_histogram` with no time field at all** (the caller omitted
  `time_field` and the data view has no default one) →
  `"group_by date_histogram on '{field}' needs a time field, and data view
  '{name}' has no default time field — set time_field on this panel to a
  date field, e.g. time_field: '{field}'"`.

### The unsupported-panels refusal

`add_panel`, `add_library_panel`, the `add_esql_*_panel` tools,
`update_panel`, and `delete_panel` all read-modify-write an *existing*
dashboard via `_checked_dashboard_data`
(`toolbox.py`), which calls `get_dashboard_data` and inspects the
`warnings` it returns. If Kibana's own response carries warnings, or the
dashboard has top-level fields the update API doesn't accept (anything
outside `title`, `description`, `panels`, `options`, `filters`, `query`,
`time_range`, `refresh_interval`, `tags`, `pinned_panels` —
`_UPDATABLE_KEYS` in `adapters/kibana/gateway.py`), the tool refuses
outright rather than perform a read-modify-write that would silently drop
those panels/fields on save:

```
dashboard '<id>' contains unsupported panels or fields that the Kibana API
cannot round-trip (they would be silently deleted): [...]. Modify this
dashboard in the Kibana UI instead.
```

This is a deliberate fail-closed guard — see
[Architecture](architecture.md#the-dropped-panel-guard) for why.

### Other errors

- `update_panel` / `delete_panel` with an out-of-range index:
  `"panel_index {i} out of range — dashboard has {n} panels"`.
- `update_panel` targeting a non-visualization panel (e.g. a text/markdown
  panel at that index): `"panel {i} is type '{type}', not a visualization —
  only vis panels can be updated"`.
- `update_panel` targeting a panel placed with `add_library_panel`: `"panel
  {i} shows library visualization '{id}' — change it with
  update_visualization, which updates every dashboard that shows it"`
  (replacing it would silently unlink it from the library item). When that
  visualization no longer exists, update_visualization cannot help, so the
  refusal reads `"panel {i} shows library visualization '{id}', which no
  longer exists — remove the broken panel with delete_panel (destructive
  tier), then place a chart with add_panel or add_library_panel"`. A
  by-reference panel does not trip the unsupported-panels refusal: the other
  tools carry it through unchanged.
- A `visualization_id` that is empty: `"visualization_id is empty — pass the
  id that create_visualization or search_visualizations returns"`; one that
  matches nothing: `"visualization '{id}' not found — visualization_id is the
  id that create_visualization or search_visualizations returns, not the
  visualization's title; call search_visualizations to find it"`.
- Any error surfaced by Kibana itself (auth, not-found, schema rejection,
  connectivity) is translated from the gateway's domain exceptions
  (`KibanaAuthError`, `KibanaNotFound`, `KibanaRejected`,
  `KibanaUnavailable` in `core/errors.py`) into a plain-text `ToolError` by
  `gateway_errors()` in `toolboxes/base.py` — messages are written to be
  safe and useful to show the calling LLM (no secrets, no stack traces).

## Space targeting

Every one of the 80 tools in the **dashboards**, **data-management**,
**alerting**, **cases**, and **security-detections** toolboxes — including
the 15 destructive-tier ones — accepts an optional `space` parameter that
targets a Kibana space by id:

```
space: string matching ^[a-z0-9_-]+$ | omit for the default space
```

There is no maximum length on the id — Kibana itself enforces none, so
neither does this parameter. Passing `space="default"` targets the default
space, the same as omitting the parameter, **with one exception**: on a
space-pinned deployment `space="default"` is refused like any other explicit
value, while omitting `space` still works (see
[Configuration](configuration.md#space-targeting)).

**Additive and default-off.** Without `space`, every tool's behavior and
result shape are byte-identical to before this parameter existed — no
`"space"` key, no message change. The effects below apply only once a call
sets `space`:

- **Fail-closed validation.** The space must exist — Kibana silently accepts
  writes under a nonexistent space id into an invisible orphan namespace
  otherwise. A nonexistent `space` is rejected up front, before any other
  work the call would have done, with guidance naming `list_spaces` and
  `create_space` as next steps (both exist only when the `platform-admin`
  toolbox is enabled — see
  [Configuration](configuration.md#space-targeting)).
- **Result echo.** Every **dict-returning** tool's result gains
  `"space": "<the value passed>"` so the model can see where it acted. The
  **list-returning** tools — `search_dashboards`, `search_visualizations`,
  `list_data_views`,
  `list_alert_rules`, `list_connectors`, `list_rule_types`, `list_cases`,
  `find_detection_rules`, `list_detection_rule_tags` (a bare string array),
  `search_detection_alerts`, `find_exception_lists`, `find_exception_items`,
  `find_value_lists`, `find_value_list_items`, and `find_timelines` — keep
  their bare-list shape and never carry the echo; an empty list from the
  wrong space still reads like "nothing exists".
- **Scoped not-found suffix.** A not-found error raised while `space` is set
  has `" (in space '<id>')"` appended to its message, e.g. `dashboard 'x' not
  found (in space 'sales')` — so a miss is never ambiguous about which space
  was searched. (A space itself not existing is a different, guidance-shaped
  error — see fail-closed validation above — and is never double-suffixed.)
- **`create_short_url`'s `/s/` rejection.** When `space` is set, a
  `params.url` starting with `/s/` is rejected — the `space` parameter
  chooses the space, not a prefix in the path. **Without `space`**, the same
  `/s/`-prefixed path still passes (byte-identical to today) and creates the
  slug in the **default space**, regardless of what its `/s/<id>` prefix
  says — a model that pastes a space-prefixed link into `create_short_url`
  and forgets `space` gets a slug that lives somewhere other than the path
  implies.
- **Cross-space saved-objects export/import.** A handle from
  `export_saved_objects(space="a")` carries no space of its own:
  `import_saved_objects(handle, space="b")` **clones** its content into `b`
  with new ids, regardless of which space it was exported from, and never
  touches existing objects. `overwrite_saved_objects(handle, space="b")`
  restores in place only within the handle's own space. Restoring into a
  **different** space mints a **new** `destination_id` on the first restore
  (saved-object ids are globally unique across spaces — an identically-titled
  dashboard cannot even exist in two spaces; the second create fails with a
  409 conflict) and replaces that copy on repeats. See
  [saved-objects export/import](#saved-objects-exportimport-data-management)
  below for the same-space case.
- **Some objects stay global even under a space.** The routing and the
  fail-closed validation always apply, but a few surfaces read or touch
  instance-global state, so the echo must not be read as isolation there:
  `get_alerting_health`'s report is instance-wide (identical in every
  space); **preconfigured connectors** (defined in `kibana.yml`) appear in
  `list_connectors` in every space with the same id, and
  `execute_connector` on one succeeds in any space; exception lists and
  items created with `namespace_type="agnostic"` are shared across **all**
  spaces — `space` chooses the routing space but does not isolate them.
  Value lists sit at the other end: their backing indices are **per-space**
  (`.lists-<space>`/`.items-<space>`), auto-created on the first value-list
  write in a space. And `search_detection_alerts` in a space where the
  detection engine has never run may find no per-space alerts index at all.

Only the remaining toolboxes have no space axis and take no `space`
parameter: `platform-admin` and `platform-health` manage instance-global
objects, `streams` wraps cluster-global ES data streams, `fleet`'s space
awareness is an opt-in per-deployment migration, and `observability` mixes
space-scoped synthetics/uptime objects with cluster-global APM
configuration (deferred as a follow-up; the Synthetics write tools and the
APM agent-config writes follow the toolbox and take no `space` either, D10).

## data-management toolbox

Data views (the datasets you visualize) and short URLs. The saved-objects
export/import/overwrite tools are also part of this toolbox — see
[saved-objects export/import](#saved-objects-exportimport-data-management) below.

| Tool | Tier | Description |
|---|---|---|
| `list_data_views` | read | List Kibana data views (the datasets you can visualize). |
| `describe_data_view` | read | Get a data view's fields and types. |
| `resolve_short_url` | read | Resolve a Kibana short-URL slug to its locator and target app path. |
| `create_data_view` | write | Create a Kibana data view over an index pattern (e.g. `logs-*`). The time field must be a date field of the pattern; a refused call creates nothing. |
| `update_data_view` | write | Edit a data view in place by id: `name`, `index_pattern` and/or `time_field`. Only the fields passed change; the time field must be a date field of the view. |
| `create_short_url` | write | Create a Kibana short URL for a locator + app path. |
| `delete_data_view` | destructive | Permanently delete a data view by id. This cannot be recovered. |
| `delete_short_url` | destructive | Permanently delete a short URL by id. |

Notes on `create_data_view`:

- **The time field is checked, because Kibana does not check it.** Kibana
  stores any string as a new view's time field — `@timestamp` over
  `kibana_sample_data_flights`, whose date field is `timestamp`, is accepted —
  and `create_dashboard` then refuses the view. Kibana's public API cannot list
  a pattern's fields without creating a view, so the tool creates it, checks
  the time field against the view's fields, and on a mismatch deletes the view
  again: `time field '@timestamp' is not a field of 'kibana_sample_data_flights'
  — pass time_field as one of its date fields: timestamp (no data view was
  created)`. A `date_nanos` field counts as a date field. On a pattern with no
  date fields the error says to omit `time_field`.
- **A failed cleanup says what is left.** If deleting the just-created view
  fails, the error keeps the rejection and the delete's error, and ends `so
  data view '<id>' exists with time field '<field>' — fix it with
  update_data_view (view_id='<id>', time_field=<a date field>), or remove it
  with delete_data_view (view_id='<id>')`.
- **A pattern that matches no index yet** has no fields, so its time field is
  accepted unchecked (as for `update_data_view`).
- **A used name is refused at create.** Kibana refuses a second view under a
  name already used — the name defaults to the index pattern — with
  `Duplicate data view: <name>`; the error adds `a data view named '<name>' (id '<id>') already
  exists: use it, change it in place with update_data_view(view_id='<id>'), or
  pass a different name` (the id when exactly one view holds the name). A
  second view over the same pattern under another `name` is accepted.

Notes on `update_data_view`:

- **Only what you pass changes.** Kibana's data-view update merges the keys it
  is sent, so `name`, `index_pattern` and `time_field` are each optional and
  an omitted one keeps its value. At least one is required, and a blank one is
  refused before anything reaches Kibana. The argument names match
  `create_data_view` and `describe_data_view`'s result.
- **The time field is checked, because Kibana does not check it.** Kibana
  stores any string as a data view's time field — one that does not exist, or
  a keyword — and every chart the dashboards toolbox builds on the view then
  inherits it. So the tool refuses a `time_field` that is not a `date` field of the view, and
  lists the view's date fields: `time field 'Carrier' is a string field, not
  a date field, of 'kibana_sample_data_flights' — pass time_field as one of
  its date fields: timestamp`.
- **A new index pattern is checked against its own fields.** When
  `index_pattern` changes, the time field (the one passed, or the one the view
  already has) must be a date field of the **new** pattern. The fields of a
  pattern are known only once Kibana stores it, so the tool writes the pattern
  first, checks, and on a mismatch writes the old pattern back and fails with
  `... (nothing was changed)`. A pattern that matches no index yet has no
  fields, so it is accepted unchecked and the result's `field_count` is `0`.
- **A failure after the pattern was written says what the view now holds.**
  If writing the old pattern back fails, the error keeps the time-field
  rejection, adds the restore's own error, and ends `so the index pattern is
  now '<new>' — to undo, call update_data_view with index_pattern='<old>'`.
  If the pattern landed but the following write of `name`/`time_field`
  fails, the error keeps Kibana's cause and adds `the index pattern was
  changed to '<new>' but name='…' was not saved; call update_data_view again
  with name='…' to finish, or to undo, call update_data_view with
  index_pattern='<old>'`.
- **What it does not do.** It cannot remove a time field (Kibana rejects
  `null`). A name that Kibana defaulted to the old pattern (a view created
  without `name`) keeps the old pattern as its name; pass `name` to change it.
  Kibana does not keep names or patterns unique, so a duplicate name makes
  `describe_data_view` by that name ambiguous (it then asks for the id).
- **Built dashboards are unaffected.** Panels this server builds carry the
  pattern and time field inline, not the data view's id, so an edit does not
  reach them; rebuild a panel with `update_panel` to use the new values.
  New panels resolve the data view by its current name, pattern, or id.
- **A wrong id names its source.** `data view '<id>' not found — view_id is
  the id that create_data_view or list_data_views returns, not the view's
  name or index pattern; call list_data_views to find it`, plus the
  `(in space '<id>')` suffix when `space` is set. Kibana validation failures
  keep the `Kibana rejected the payload:` prefix.

Returns:

```jsonc
// update_data_view  (+ "space" when the caller passed one)
{"id": "7902514d-...", "name": "Flights", "index_pattern": "kibana_sample_data_flights",
 "time_field": "timestamp", "field_count": 32}
```

## alerting toolbox

Alerting rules and action connectors.

| Tool | Tier | Description |
|---|---|---|
| `list_alert_rules` | read | List alerting rules (optionally filtered by a name search). |
| `get_alert_rule` | read | Get one rule by id: the summary fields plus its full `params`, its `actions` (`id` = connector id, `uuid`, `group`, `params`, `frequency` when set), `muted` and `muted_alert_ids`. Read it before and after `update_alert_rule`. |
| `get_alerting_health` | read | Kibana alerting framework health status. |
| `list_connectors` | read | List configured action connectors (id, name, connector_type_id). |
| `list_rule_types` | read | List rule types: `id` (the `rule_type_id`), the `consumers` the caller may create it under, licence status. No `params` schema (Kibana's API has none). |
| `get_connector` | read | Get one connector by id: name, type, its non-secret `config` and flags (`is_preconfigured`, `is_missing_secrets`, ...). Never returns secrets. |
| `create_alert_rule` | write | Create an alerting rule (e.g. `rule_type_id` `.es-query`). |
| `update_alert_rule` | write | Edit a rule in place by id (read-modify-write): `name`, `tags`, `schedule_interval`, `params` (merged key-by-key), `actions`. Never toggles `enabled`. |
| `enable_alert_rule` | write | Enable (start running) an alerting rule. |
| `disable_alert_rule` | write | Disable (stop running) an alerting rule; `untrack=True` (default off) also marks its active alerts untracked. |
| `create_connector` | write | Create an action connector (e.g. `.index`, `.webhook`). |
| `update_connector` | write | Edit a connector in place by id: `name`, `config` (merged key-by-key), and `secrets` only when passed. Refuses preconfigured connectors. |
| `mute_alert_rule` | write | Mute a whole rule: it keeps running, no action fires, no end time. Clears per-alert mutes and returns them in `cleared_alert_ids`. |
| `unmute_alert_rule` | write | Undo `mute_alert_rule`; also clears every per-alert mute, even on a rule not muted as a whole, and returns them in `cleared_alert_ids` (`mute_alert` on each restores them). |
| `mute_alert` | write | Mute one alert (`alert_id`) of a rule; refused on a rule muted as a whole. |
| `unmute_alert` | write | Undo `mute_alert` for one alert (an id from `muted_alert_ids`). |
| `snooze_alert_rule` | write | Silence a rule's actions for a window: `duration` (`30m`, `2h`, `1d`), `start` (ISO-8601, default now), optional `recurring`. Returns the `schedule_id`. |
| `unsnooze_alert_rule` | write | Cancel one snooze by the `schedule_id` `snooze_alert_rule` returned. |
| `update_alert_rule_api_key` | write | Regenerate a rule's API key from this server's credentials (the rule then runs with its privileges). |
| `delete_alert_rule` | destructive | Permanently delete an alerting rule. |
| `delete_connector` | destructive | Permanently delete an action connector. This cannot be undone. |
| `execute_connector` | destructive | Run a connector now with the given type-specific params. |

A tier includes everything below it: `write` (the default) shows the 6 reads +
13 writes; `destructive` adds the 2 deletes and `execute_connector`; `read`
shows only the 6 reads.

Notes:

- **Edits keep what you did not pass.** Kibana's rule and connector updates are
  full replaces: a rule update that omits `actions` or `tags` empties them and
  one that omits `params` is rejected; a connector update that omits `config`
  resets it. `update_alert_rule` and `update_connector` therefore read the
  object, re-send every field, and overlay only the arguments given. `params`
  and `config` merge key by key, so pass only the keys that change
  (`params={"threshold": [10]}`); `tags` and `actions` replace the whole list
  (`[]` clears it). `get_alert_rule` shows a rule's current `params` and
  `actions` before the edit and confirms them after it (`update_alert_rule`
  returns the summary only); its actions are in the shape `actions` takes,
  so one can be read, changed and passed back. Pass each read action's `uuid`
  back with it: Kibana then keeps the action's identity, while an action sent
  without one gets a new uuid.
- **`update_alert_rule` does not start or stop a rule.** `enabled` is not an
  argument (Kibana rejects it on update); an enabled rule stays enabled. Use
  `enable_alert_rule` / `disable_alert_rule`. `rule_type_id` and `consumer`
  cannot change. A rule's action is
  `{"id": <connector id>, "group": "query matched", "params": {...}, "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}}`;
  the rule-level `notify_when`/`throttle` are not arguments (Kibana refuses
  them beside a per-action `frequency`). A rule that already has them keeps
  them, unless the `actions` passed carry a `frequency`: then they are left
  out of the update, Kibana clears them, and the rule's timing comes from each
  action's `frequency`.
- **Connector secrets are write-only.** Kibana never returns them, so
  `get_connector` and `update_connector` cannot either, and a connector
  update that omits `secrets` resets them. For every type except `.index`
  and `.server-log` (whose secrets schema is empty) `update_connector`
  refuses a call without `secrets` and says to pass the full secret values;
  `secrets={}` clears them deliberately. Passed secrets are sent to Kibana
  and never echoed back.
- **Preconfigured connectors cannot be edited.** A connector defined in
  `kibana.yml` (`is_preconfigured: true`) is refused before any write, with a
  pointer to `kibana.yml` or `create_connector`.
- **Mute, snooze, or untrack — three ways to quiet a rule that keeps its
  definition.** All three leave the rule's params and actions as they are.
  `mute_alert_rule` stops every action until `unmute_alert_rule`, with no end
  time; the rule keeps running and tracking alerts. Kibana's rule-wide mute and
  unmute both clear the per-alert mutes (the unmute even on a rule that is not
  muted), so both return the cleared ids in `cleared_alert_ids` — no tool lists
  a rule's alert ids, and `mute_alert` on each puts them back. On a rule muted
  as a whole Kibana accepts a per-alert mute but records nothing, so
  `mute_alert` refuses that case and says to unmute the rule first. `mute_alert` takes the id of an alert
  the rule has raised — the group's value for a rule grouped by a field (e.g.
  a host name), `query matched` for an ungrouped `.es-query` rule; Kibana
  refuses an id the rule has not raised, and unmuting an alert that is not
  muted succeeds. `snooze_alert_rule` stops actions for a window: `duration`
  is a whole number and `s`, `m`, `h` or `d` (Kibana has no week unit here),
  `start` any ISO-8601 instant (no offset means UTC; sent to Kibana as
  `YYYY-MM-DDTHH:mm:ss.sssZ`, the only form it takes), and `recurring`
  (`every` such as `1d`/`2w`/`1M`, bounded by `end` or `occurrences`,
  narrowed by `onWeekDay`/`onMonthDay`/`onMonth`) repeats it.
  **Keep the `schedule_id`**: Kibana's public API never lists a rule's
  snoozes, so `unsnooze_alert_rule` has no other source, and a rule holds at
  most 5 snoozes, expired ones included (a sixth is refused, naming
  `unsnooze_alert_rule`; lost ids are cancelled in Kibana's Rules page).
  `disable_alert_rule(untrack=True)` stops the rule and marks its active
  alerts untracked; without it they stay active.
- **`update_alert_rule_api_key` changes who the rule runs as.** Kibana
  regenerates the rule's API key from the caller's credentials — this
  server's API key — so the rule's queries and actions then run with that
  key's privileges. Use it when a rule fails because its key was revoked; no
  key material is returned.
- **A wrong id names its source.** An id that matches nothing fails with
  `connector '<id>' not found — connector_id is the id that create_connector
  or list_connectors returns, not the connector's name; call list_connectors
  to find it` (and the rule equivalent naming `list_alert_rules`), plus the
  `(in space '<id>')` suffix when `space` is set. Kibana validation failures
  keep the `Kibana rejected the payload:` prefix.

Returns:

```jsonc
// get_connector / update_connector  (+ "space" when the caller passed one)
{"id": "032a5771-...", "name": "Alert index", "connector_type_id": ".index",
 "config": {"index": "alerts-out", "refresh": false, "executionTimeField": null},
 "is_preconfigured": false, "is_system_action": false, "is_deprecated": false,
 "is_missing_secrets": false, "is_connector_type_deprecated": false}

// snooze_alert_rule  (+ "space" when the caller passed one)
{"rule_id": "8b1c...", "schedule_id": "f828e368-...", "duration": "2h",
 "start": "2026-10-25T07:00:00.000Z", "timezone": "UTC",
 "recurring": {"every": "1w", "onWeekDay": ["SA"], "end": "2026-12-31T00:00:00.000Z"}}

// mute_alert_rule / unmute_alert_rule · mute_alert / unmute_alert ·
// unsnooze_alert_rule · update_alert_rule_api_key · disable_alert_rule
{"id": "8b1c...", "muted": false, "cleared_alert_ids": ["host-a", "host-b"]}
{"rule_id": "8b1c...", "alert_id": "query matched", "muted": true}
{"rule_id": "8b1c...", "schedule_id": "f828e368-...", "unsnoozed": true}
{"id": "8b1c...", "api_key_updated": true}
{"id": "8b1c...", "enabled": false, "untracked": true}   // "untracked" only with untrack=true

// update_alert_rule  (the summary each list_alert_rules row carries)
{"id": "8b1c...", "name": "Flights over threshold", "rule_type_id": ".es-query",
 "consumer": "stackAlerts", "enabled": true, "schedule_interval": "5m",
 "status": "ok", "tags": ["flights"]}

// get_alert_rule  (the summary + params as Kibana stores them + actions + mute state)
{"id": "8b1c...", "name": "Flights over threshold", "rule_type_id": ".es-query",
 "consumer": "stackAlerts", "enabled": true, "schedule_interval": "5m",
 "status": "ok", "tags": ["flights"],
 "params": {"searchType": "esQuery", "esQuery": "{\"query\":{\"match_all\":{}}}",
            "index": ["kibana_sample_data_flights"], "timeField": "timestamp",
            "threshold": [100], "thresholdComparator": ">", "timeWindowSize": 5,
            "timeWindowUnit": "m", "size": 100, "aggType": "count", "groupBy": "all",
            "excludeHitsFromPreviousRun": true},
 "actions": [{"id": "032a5771-...", "uuid": "5f0e...", "group": "query matched",
              "params": {"message": "{{rule.name}} matched"},
              "frequency": {"summary": false, "notify_when": "onActionGroupChange",
                            "throttle": null}}],
 "muted": false, "muted_alert_ids": ["JetBeats"]}
```

## cases toolbox

Kibana Cases: open, comment on, update, and close cases.

| Tool | Tier | Description |
|---|---|---|
| `list_cases` | read | List cases (optionally filtered by a title/description search). |
| `get_case` | read | Get one case's summary by id. |
| `create_case` | write | Open a new Kibana case (uses the built-in 'none' connector). |
| `add_case_comment` | write | Add a text comment to a case. Returns the updated case. |
| `update_case` | write | Update a case's status, severity, tags, and/or title. |
| `delete_case` | destructive | Permanently delete a case. This cannot be undone. |

## platform-health toolbox

Three read-only tools (`src/kibana_mcp/toolboxes/platform_health/toolbox.py`),
all `read` tier (`readOnlyHint=true`), tagged `{"platform-health", "read"}`.
Enable with `KIBANA_MCP_TOOLBOXES=platform-health` (or alongside others, e.g.
`dashboards,platform-health`). Each **summarizes** a large raw Kibana response
into a concise health signal — deliberately not the full metric tree — so a
small local model gets a usable answer.

| Tool | Tier | Description |
|---|---|---|
| `get_kibana_status` | read | Kibana's overall health: overall status level, version, and any core services/plugins that are not `available`. |
| `get_kibana_stats` | read | Runtime resource stats: heap used/total/limit bytes, event-loop delay (ms), concurrent connections. |
| `get_task_manager_health` | read | Task Manager health: `status` (OK / warn / error) and the latest health/update timestamps. |

Returns:

```jsonc
// get_kibana_status
{"overall_level": "available", "overall_summary": "All services and plugins are available",
 "version": "9.5.4", "unhealthy": []}
// ...unhealthy lists only services whose level != "available":
// "unhealthy": [{"name": "reporting", "level": "unavailable", "summary": "..."}]

// get_kibana_stats
{"heap_used_bytes": 645926408, "heap_total_bytes": 708210688,
 "heap_size_limit_bytes": 4496293888, "event_loop_delay_ms": 13.7, "concurrent_connections": 4}

// get_task_manager_health
{"status": "OK", "timestamp": "2026-07-12T08:09:54.102Z", "last_update": "2026-07-12T08:09:52.755Z"}
```

## observability toolbox

Twenty-two tools (`src/kibana_mcp/toolboxes/observability/toolbox.py`): 10
`read`, 8 `write`, 4 `destructive`, tagged `{"observability", <tier>}`. Enable
with `KIBANA_MCP_TOOLBOXES=observability` (or alongside others). Access to the
Kibana observability surface an operator can reach on a **Basic** license:
Synthetics monitoring (read, and the write tier: monitors, global parameters and
private locations — create, edit, run once, delete), Uptime settings, and APM
**configuration** (read, and agent configurations created, replaced and
deleted). No tool takes `space` (see [Space targeting](#space-targeting)).
Hints: reads `readOnlyHint=true`; the three Synthetics updates and
`upsert_apm_agent_config` `idempotentHint=true`; the deletes
`destructiveHint=true`;
`test_synthetic_monitor` `openWorldHint=true` (the run sends the monitor's
requests to its target), every other tool `openWorldHint=false`.

!!! note "v1 scope — what this is *not*"
    This toolbox intentionally does **not** expose APM **service/transaction/
    trace/service-map telemetry** — those are internal-only Kibana APIs
    (`/internal/apm/*`), unreachable by an external client on 9.x and not wrapped
    by kibana-py. It also does not expose **SLOs**, which require a Platinum
    license (they return `403` on a Basic stack), and it does not **create APM
    annotations**, which need a Gold license (`403` "Annotations require at least
    a gold license or a trial license." on Basic; `search_apm_annotations` reads
    them). All are out of scope: the APM telemetry has no supported external
    path, and subscription-gated surfaces are not built here.

| Tool | Tier | Description |
|---|---|---|
| `list_synthetic_monitors` | read | List Synthetics monitors (HTTP/TCP/ICMP/browser checks): id, name, type, enabled, tags, locations, schedule, target. |
| `get_synthetic_monitor` | read | Get one Synthetics monitor by its config id. |
| `list_synthetic_params` | read | List Synthetics global parameters (id, key, description, tags). Values are never returned. |
| `list_synthetic_private_locations` | read | List Synthetics private locations (Fleet-agent-backed run locations). |
| `get_uptime_settings` | read | Uptime app settings: heartbeat index pattern, TLS cert thresholds, default connectors/email. |
| `list_apm_agent_configs` | read | List APM agent (central) configurations: service, settings, applied-by-agent, etag. |
| `get_apm_agent_config` | read | Get one APM agent configuration by service name + environment (omit both for the all-services config). |
| `list_apm_environments` | read | List APM environments for agent config. `ALL_OPTION_VALUE` is a sentinel meaning "all environments". |
| `list_apm_sourcemaps` | read | List uploaded RUM source-map artifacts (identifier, created). |
| `search_apm_annotations` | read | Search APM annotations (e.g. deployment markers) for a service in an ISO-8601 `[start, end]` window. |
| `create_synthetic_monitor` | write | Create an http/tcp/icmp/browser monitor on private locations (ids or labels); returns it as `get_synthetic_monitor` does. |
| `update_synthetic_monitor` | write | Edit a monitor in place: only the fields passed change (`enabled=False` pauses it); the type cannot change. |
| `test_synthetic_monitor` | write | Run a monitor once now, a paused one too (requests reach its target); adds a short-lived package policy to the location's agent policy; returns its `test_run_id` (no tool reads the result). |
| `create_synthetic_param` | write | Create a global parameter monitors reference as `${key}`; the value is never returned. |
| `update_synthetic_param` | write | Rename, rotate or re-describe a parameter in place; no value is ever returned, so a replaced value cannot be recovered by any tool. |
| `create_synthetic_private_location` | write | Create a private location on a Fleet agent policy no other location uses. |
| `update_synthetic_private_location` | write | Rename a private location (the label is all Kibana lets change). |
| `delete_synthetic_monitor` | destructive | Permanently delete a monitor. |
| `delete_synthetic_param` | destructive | Permanently delete a parameter; its value is lost and monitors lose `${key}`. |
| `delete_synthetic_private_location` | destructive | Permanently delete a private location no monitor uses; its agent policy stays. |
| `upsert_apm_agent_config` | write | Create or replace the APM agent configuration of a service + environment (omit either for all): `settings` replaces the stored map; returns `created` and the replaced `previous_settings`. |
| `delete_apm_agent_config` | destructive | Permanently delete one APM agent configuration (exact service + environment match); returns the deleted settings. |

Synthetics write tier, as probed on Kibana 9.5.4 (decision
[D46](spec/decisions.md)):

- **Monitors.** Each type takes its own target: `url` (http), `host` (tcp, with
  its port; icmp), `inline_script` (browser, a Playwright journey). The tools
  refuse a missing target and another type's field — Kibana stores a `url` on a
  tcp monitor without complaint. `private_locations` entries are ids or labels
  from `list_synthetic_private_locations`, checked before the write; this server
  offers no Elastic-managed locations (a self-managed stack has none).
  `schedule` takes the read shape (`10m`, `30s`): the number must be one of 1,
  2, 3, 5, 10, 15, 20, 30, 60, 120, 240, with unit `m` or `s` (Kibana checks the
  number against its minute list whatever the unit). Leading zeros are dropped
  before the call (`05m` is sent as `5m`; Kibana refuses `"05"`). `tags` and
  `private_locations` on an update replace the whole list. Names are unique (Kibana's
  400 passes through). `update_synthetic_monitor` sends only the fields given —
  Kibana's PUT keeps the rest — and reads the monitor first only to check a
  target field against its type. Created monitors start enabled
  (`enabled=False` to create one paused).
- **Runs.** `test_synthetic_monitor` returns `{"monitor_id", "test_run_id"}`. The
  check executes on the Elastic Agents enrolled in the location's agent policy;
  with none running, Kibana still accepts the request and nothing runs. A
  paused monitor runs too, so the run can send requests its schedule would
  not; every call is a new run. Each run adds a
  `LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN` package policy
  (`BROWSER_SYNTHETICS_TEST_NOW_RUN` for browser) to that agent policy, and it
  stays there after the location is deleted until Kibana's clean-up task
  removes it (lightweight ones older than 2 minutes, browser ones older than
  15; the task runs right after a run and then every 20 minutes while any
  remain, so about 20 minutes). The
  result is written to the `synthetics-*` data streams under that
  `test_run_id` (shown in Kibana's Synthetics app); no tool reads it.
- **Parameters.** The value is a secret: no result carries it (Kibana's update
  response echoes the *previous* value; the tools drop it), and a rejection's
  text is scrubbed of it. A key already in use is refused — Kibana stores a
  second parameter with the same key, which would make `${key}` ambiguous.
  `update_synthetic_param` and both create tools refuse an empty change or value.
  A value replaced by `update_synthetic_param` cannot be read back by any tool,
  so setting it back needs the caller to hold the old one.
- **Private locations.** One per Fleet agent policy, labels unique (both
  checked before the write); a missing policy is Kibana's 400 plus where policy
  ids come from (`list_agent_policies`, `create_agent_policy` in the
  [fleet toolbox](#fleet-toolbox)). Deleting one that monitors use is refused by
  Kibana; the error names `update_synthetic_monitor` and
  `delete_synthetic_monitor`.
- **APM agent configurations** (decision [D47](spec/decisions.md)). A
  configuration is named by `service_name` and `environment`, matched exactly:
  an omitted one means *all services* or *all environments*, never *any*, so
  `{service_name: "cart"}` and `{service_name: "cart", environment: "prod"}` are
  two configurations. `list_apm_environments`' `ALL_OPTION_VALUE` is refused as
  a name (Kibana would store it literally); so is an empty string (Kibana
  stores `""` and then cannot find it again). `upsert_apm_agent_config` reads
  the configuration first and replaces it only if it read one, so a
  configuration created in between is refused, not overwritten unreported.
  `settings` replaces the whole map (to change one setting, pass the map from
  `get_apm_agent_config` with that key changed); values go out as strings
  (`0.5` → `"0.5"`, `false` → `"false"`). Kibana checks the values of the
  settings it knows (a rejection lists the settings sent, since Kibana's message
  can omit the key) but stores setting names it does not know, which agents
  ignore. A stored `agent_name` (Kibana's UI writes one) is sent again on a
  replacement, since Kibana's PUT drops it otherwise. A miss reads "no APM agent
  configuration for service 'cart', all environments — service_name and
  environment must match a configuration exactly (an omitted one means all);
  call list_apm_agent_configs to see them", on `get_apm_agent_config` too.
- **Misses.** A wrong id names where ids come from (D17), e.g. "synthetic
  monitor 'x' not found — monitor_id is the id that create_synthetic_monitor or
  list_synthetic_monitors returns, not the monitor's name; call
  list_synthetic_monitors to find it". Kibana answers a monitor or parameter
  delete of an unknown id with `200 [{"deleted": false}]` and a private-location
  one with a 400 "does not exist" (its update with a 404); all read as
  not-found here.

Returns:

```jsonc
// list_synthetic_monitors -> list
[{"id": "0f19...c23", "name": "home", "type": "http", "enabled": true,
  "tags": ["prod"], "locations": ["us-east"], "schedule": "10m",
  "target": "https://example.com"}]

// get_uptime_settings
{"heartbeat_indices": "heartbeat-*", "cert_expiration_threshold": 30,
 "cert_age_threshold": 730, "default_connectors": [],
 "default_email": {"to": [], "cc": [], "bcc": []}}

// list_apm_agent_configs -> list  (get_apm_agent_config: one of these)
[{"service_name": "checkout", "service_environment": "production",
  "settings": {"transaction_sample_rate": "0.5"}, "applied_by_agent": true, "etag": "e1",
  "agent_name": "java"}]

// list_apm_environments -> list  (ALL_OPTION_VALUE = "all environments")
[{"name": "ALL_OPTION_VALUE", "already_configured": false}]

// search_apm_annotations -> list
[{"id": "a1", "timestamp": "2026-07-12T00:00:00.000Z", "text": "v2 deploy", "type": "deployment"}]

// create_synthetic_monitor / update_synthetic_monitor  (the get_synthetic_monitor shape)
{"id": "2820...fc76", "name": "api health", "type": "http", "enabled": true,
 "tags": ["api"], "locations": ["dc1"], "schedule": "30s",
 "target": "https://example.com/health"}

// test_synthetic_monitor
{"monitor_id": "2820...fc76", "test_run_id": "2962b617-4d24-4d2b-8923-1a497e6d4898"}

// create_synthetic_param / update_synthetic_param  (never a value)
{"id": "7c2e...88f7", "key": "api_token", "description": "RUM token", "tags": ["shared"]}

// create_synthetic_private_location / update_synthetic_private_location
{"id": "bbce...8def", "label": "dc1", "agent_policy_id": "9d19...d417",
 "is_invalid": false, "tags": []}

// delete_synthetic_monitor / delete_synthetic_param / delete_synthetic_private_location
{"id": "2820...fc76", "deleted": true}

// upsert_apm_agent_config  (the get_apm_agent_config shape + created, previous_settings)
{"service_name": "checkout", "service_environment": "production",
 "settings": {"capture_body": "errors"}, "applied_by_agent": false,
 "etag": "8176...5555", "agent_name": null,
 "created": false, "previous_settings": {"transaction_sample_rate": "0.5"}}

// delete_apm_agent_config
{"service_name": "checkout", "environment": "production", "deleted": true,
 "settings": {"capture_body": "errors"}}
```

## security-detections toolbox

25 tools across three tiers (`src/kibana_mcp/toolboxes/security_detections/toolbox.py`),
tagged `{"security-detections", <tier>}`. Enable with
`KIBANA_MCP_TOOLBOXES=security-detections`. Read/write access to the Kibana
Security **detection engine** and its supporting objects — all GA and available
on a **Basic** license (the SIEM detection engine ships in the free tier; only
ML-type *rules* need Platinum, and only to create them).

!!! note "scope — full rule lifecycle (incl. enable/disable) + value-list items"
    Reads never mutate. The **write** tier creates detection rules (query-type) +
    exception lists/items, partial- and full-replace-updates a rule's fields
    (including `enabled`, via `enable_detection_rule` / `disable_detection_rule`),
    and creates value lists + their items; the **destructive** tier deletes
    rules/exceptions/value lists and value-list items. Enable/disable looked
    privilege-gated on the deployment key at first pass (403/500 on
    `bulk_action_rules`/`patch_rule`), but `update_rule` (full-replace) works —
    `replace_detection_rule` and enable/disable all ride that read-modify-write
    path. The `security-ai` assistant + attack-discovery surface is
    out of scope (needs an Enterprise-gated `.gen-ai` connector). Some read object shapes (alerts,
    timelines) could not be seeded, so their fields are mapped defensively.

| Tool | Tier | Description |
|---|---|---|
| `find_detection_rules` | read | List detection-engine rules (id, rule_id, name, enabled, type, severity, risk_score, tags, immutable, version). |
| `get_detection_rule` | read | Get one rule by its stable `rule_id` or its `id` (uuid). |
| `get_prepackaged_rules_status` | read | Elastic prebuilt-rule install status (installed / not-installed / custom counts, for rules and timelines). |
| `list_detection_rule_tags` | read | The distinct tags used across detection rules. |
| `search_detection_alerts` | read | The most recent detection alerts (id, rule_name, severity, status, timestamp); `size` caps the count. |
| `find_exception_lists` | read | List exception-list containers. |
| `get_exception_list` | read | Get one exception-list container by `id` or `list_id`. |
| `find_exception_items` | read | List the exception items within an exception list (by `list_id`). |
| `find_value_lists` | read | List value lists (shared value sets referenced by rule exceptions). |
| `find_value_list_items` | read | List the items of a value list, by the list's `id` (passed as `list_id`). |
| `find_timelines` | read | List investigation timelines. |
| `create_detection_rule` | write | Create a custom KQL/Lucene `query` rule (`enabled` defaults false — a new rule starts inactive). Returns the created rule. |
| `create_exception_list` | write | Create an exception-list container. |
| `create_exception_item` | write | Add an exception item (field==/!=value `entries`) to a list. |
| `update_detection_rule` | write | Partial-update a rule's non-`enabled` fields (name/description/tags/severity/risk_score/query/interval) by `rule_id` / `id`. Does not toggle `enabled`. |
| `replace_detection_rule` | write | Full-replace (PUT) a rule by `rule_id` / `id` — read-modify-write, so it can reach fields `update_detection_rule` can't (e.g. `index`, `language`). Refuses an Elastic-prebuilt (immutable) rule. |
| `enable_detection_rule` | write | Enable a rule's schedule by `rule_id` / `id`. |
| `disable_detection_rule` | write | Disable a rule's schedule by `rule_id` / `id`. |
| `create_value_list` | write | Create a value list of a given ES `type` (keyword/ip/…); first use initializes the `.lists` backing streams. |
| `create_value_list_item` | write | Add an item (a value) to a value list, by the list's `id` (passed as `list_id`). |
| `delete_detection_rule` | destructive | Delete a rule by exactly one of `rule_id` / `id`. |
| `delete_exception_list` | destructive | Delete an exception-list container (and its items) by `id` / `list_id`. |
| `delete_exception_item` | destructive | Delete an exception item by `id` / `item_id`. |
| `delete_value_list` | destructive | Delete a value list (+ its items) by `id`. Refuses a referenced list unless `force=True`. |
| `delete_value_list_item` | destructive | Delete a single value-list item by `item_id` (the parent list and its other items are untouched). |

A tier includes everything below it: `write` (the default) shows the 11 reads +
9 writes; `destructive` adds the 5 deletes; `read` shows only the 11 reads.

Returns:

```jsonc
// find_detection_rules -> list
[{"id": "8ae4...80a6", "rule_id": "custom-rule-1", "name": "Suspicious login",
  "enabled": true, "type": "query", "severity": "high", "risk_score": 73,
  "tags": ["auth"], "immutable": false, "version": 1}]

// get_prepackaged_rules_status
{"rules_installed": 0, "rules_not_installed": 0, "rules_custom_installed": 1,
 "rules_not_updated": 0, "timelines_installed": 0, "timelines_not_installed": 10,
 "timelines_not_updated": 0}

// find_exception_lists -> list
[{"id": "33c0...ae32", "list_id": "endpoint_list", "name": "Endpoint Security",
  "type": "endpoint", "namespace_type": "agnostic", "tags": [], "os_types": []}]

// search_detection_alerts -> list  (kibana.alert.* fields, defensive)
[{"id": "a1", "rule_name": "Suspicious login", "severity": "high",
  "status": "open", "timestamp": "2026-07-13T00:00:00.000Z"}]
```

## saved-objects export/import (data-management)

Two tools in the `data-management` toolbox move saved objects (dashboards, data
views, …) around **without routing their contents through the model**. Export
writes NDJSON to a file on the server and hands back an opaque **handle**; import
consumes the handle.

| Tool | Tier | Description |
|---|---|---|
| `export_saved_objects` | read | Export saved objects and return a `handle` + a summary (counts, missing references). Select EITHER `types` (e.g. `["dashboard"]`, or `["*"]` for the whole space) OR `objects` (`[{"type":…, "id":…}]`). The NDJSON is **never** returned — only the handle + summary. |
| `import_saved_objects` | write | Import a previously-exported set by its `handle`, as **new copies** (regenerated ids — a clone into the current space, not an in-place restore). Never touches existing objects. Returns each source id → new destination id. |
| `overwrite_saved_objects` | destructive | Restore a previously-exported set by its `handle` **in place** (same ids), **overwriting** any existing objects with those ids. Cannot be undone. Returns each restored object (its `destination_id` == `source_id`). |

Notes:

- **Handle, not bytes.** The export content stays in a confined server-side
  directory (`KIBANA_MCP_EXPORT_DIR`, default a fresh 0700 temp dir); the handle
  is a `so-<hex>` token that can only ever resolve to a file inside that dir.
  This is what keeps a whole-space export out of the model context.
- **Clone vs overwrite.** `import_saved_objects` (write) regenerates ids, so
  export→import into the *same* space **duplicates** objects and never touches
  existing ones. `overwrite_saved_objects` (destructive) restores **in place** —
  same ids, replacing whatever is there. Choose by whether you want a copy or a
  true restore. All three tools take the same `space` parameter as every other
  dashboards/data-management tool, so the same choice applies **across**
  spaces too — see
  [Space targeting](#space-targeting) above.
- **Export is a sensitive read.** It can read anything the API key's RBAC allows
  (`types=["*"]` exports the space); the tier is `read` because it mutates
  nothing, but treat the API key's privileges as the real boundary.
- **No server-side query validation** of the objects; a wrong handle errors, and
  an import that Kibana rejects returns a content-free failure (never object bytes).
- **Not multi-tenant.** Export handles live in one server-side directory shared
  by all callers — a handle is a bearer token for that export, and the 20-file
  retention cap is global, so concurrent HTTP callers can expire each other's
  handles.

## platform-admin toolbox

Ten tools across three tiers (`src/kibana_mcp/toolboxes/platform_admin/toolbox.py`),
tagged `{"platform-admin", <tier>}`. Enable with `KIBANA_MCP_TOOLBOXES=platform-admin`.
Manage core Kibana administration objects — **Spaces**, security **Roles**, and
read **Upgrade Assistant** readiness — all GA and available on a **Basic** license.
Five `read` tools are visible at every tier; the three `write` tools appear at the
default `write` tier; the two `destructive` deletes require `KIBANA_MCP_TIER=destructive`.

!!! warning "Destructive tier wipes data"
    `delete_space` permanently removes **every saved object in the space**
    (dashboards, rules, data views, …) and always requires `force=true`; it refuses
    the `default` and any reserved space. `delete_role` revokes access for everyone
    assigned the role and refuses reserved system roles. `create_or_update_role`
    is a **full replace** — `create_only` defaults `true` so a bare call on an
    existing role is rejected rather than silently dropping its other grants; pass
    `create_only=false` to deliberately overwrite.

!!! note "Out of scope"
    **Logstash pipeline** management needs a **Platinum** license (403 on Basic,
    confirmed live), so it is out of scope like the rest of this list;
    **session invalidation**, space object copy/move, feature-level role grants,
    and avatar-image editing are out of scope too. Reading roles surfaces
    RBAC *configuration* (privilege grants), never secrets, and every write is
    bounded by the API key's own privileges.

| Tool | Tier | Description |
|---|---|---|
| `list_spaces` | read | List all Kibana spaces (id, name, description, solution, disabled features, whether reserved). |
| `get_space` | read | Get one space by its `space_id` (e.g. `default`). |
| `list_roles` | read | List all roles (incl. reserved) with a privilege summary: ES cluster + index privileges, run_as, and per-space Kibana base/feature grants. |
| `get_role` | read | Get one role by `role_name` (e.g. `kibana_system`). |
| `get_upgrade_status` | read | Upgrade Assistant readiness: ready flag, details, recent ES deprecation-log count, and deprecated Kibana APIs still in use (title + level + type). |
| `create_space` | write | Create a space (`id` is immutable lowercase alnum/`_`/`-`; optional description, color, initials, disabled features, solution). |
| `update_space` | write | Update a space's fields (read-modify-write — omitted fields are preserved; `description=""` clears it). |
| `create_or_update_role` | write | Create or **full-replace** a role: ES cluster + index privileges, a Kibana base grant across spaces. `create_only` defaults `true` (rejects an existing role); pass `false` to overwrite. |
| `delete_space` | destructive | Delete a space — **wipes every saved object in it**; requires `force=true`; refuses `default` + reserved spaces. |
| `delete_role` | destructive | Delete a role by name; refuses reserved system roles. |

Notes:

- **Bare arrays, no pagination.** Spaces and roles come back as full lists in one
  call; there is no page envelope to walk.
- **Roles are summarized, not dumped.** The deep privilege JSON
  (`elasticsearch.{cluster,indices,run_as}`, `kibana[].{base,feature,spaces}`) is
  reduced to the operator-relevant "who can do what where"; per-feature privilege
  lists collapse to sorted feature names.
- **Upgrade status is non-deterministic.** Deprecation counts shift with cluster
  usage and each entry's raw message embeds a live timestamp, so those are
  dropped — only stable `title`/`level`/`type` are surfaced.
- **`update_space` is read-modify-write.** The gateway reads the current space and
  re-sends every unspecified field, so a partial update never resets the space's
  other attributes (disabled features, avatar image, solution).
- **`delete_space` is always force-gated.** A reliable per-space object count isn't
  available (the count API requires an explicit type list and is deprecated on 9.4),
  so rather than a fail-open count the whole-space wipe always demands `force=true`.

## streams toolbox

Twelve tools across three tiers (`src/kibana_mcp/toolboxes/streams/toolbox.py`),
tagged `{"streams", <tier>}`. Enable with `KIBANA_MCP_TOOLBOXES=streams`. Access to
**Kibana Streams** — the logs/observability data streams, their ingest
configuration, and (write/destructive tiers) their management.

!!! warning "Tech-Preview API"
    Kibana Streams is an Elastic **Tech-Preview** feature; its API shapes may
    change between minor versions. The query / significant-events / attachments
    surface needs an **Enterprise** license (403 on Basic, confirmed live) and is
    not exposed.

!!! danger "Destructive tier — irreversible data loss"
    The `destructive` tier (only visible at `KIBANA_MCP_TIER=destructive`) can
    permanently delete data or divert it: `disable_streams` deletes **all** wired
    streams and their data cluster-wide; `delete_stream` destroys a stream's
    backing data and, for a parent, its whole subtree; `set_stream_retention`
    shortening a window ages out (deletes) older documents; `activate_fork`
    diverts live documents from a parent into a forked child going forward.
    `delete_stream` refuses roots and parents-with-children unless `force=True`;
    `disable_streams` and `activate_fork` require `confirm=True`. Recovery from
    `disable_streams` (via `enable_streams`) recreates **empty** roots only —
    forked children and their data are not restored.

| Tool | Tier | Description |
|---|---|---|
| `list_streams` | read | List all streams: name, type (`wired` = managed schema/routing, `classic` = an existing data stream), description. |
| `get_stream` | read | One stream's summary by `name`: type, description, updated-at, lifecycle mode + retention, and counts of processing steps, routing rules, and managed fields. |
| `get_stream_ingest` | read | One stream's ingest config by `name`: lifecycle + retention, step/routing counts, and the managed field schema (name→type; populated for `wired`, empty for `classic`). |
| `enable_streams` | write | Enable the wired Streams framework (creates root streams). Idempotent (`noop` when already enabled). |
| `resync_streams` | write | Rebuild the Elasticsearch assets (templates, ingest pipelines) backing all streams from their stored definitions. |
| `fork_stream` | write | Fork a wired stream: create a child (prefixed by the parent) that routes docs where `condition_field` == `condition_value`. Staged `disabled` — no live routing until `activate_fork`. |
| `set_stream_processing` | write | Replace a stream's ingest processing steps (whole-list replace, not a merge/append). Applies to both wired and classic streams. Clearing all steps (`steps=[]`) requires `confirm=True`. |
| `deactivate_fork` | write | Stop routing new documents from a parent into a forked child. Documents already routed into the child are untouched — only the routing rule's status flips. |
| `set_stream_retention` | destructive | Set a stream's DSL data retention (e.g. `30d`). Shortening deletes older docs; converting a non-DSL lifecycle decouples the child from its parent. |
| `activate_fork` | destructive | Activate a forked child: start routing matching documents from the parent into it LIVE. Requires `confirm=True`. |
| `delete_stream` | destructive | Delete a stream (definition + backing data; a parent cascades its subtree). Refuses roots / parents-with-children unless `force=True`. |
| `disable_streams` | destructive | Disable the framework — deletes all wired streams + data cluster-wide (classic data preserved). Requires `confirm=True`. |

Notes:

- **Bare list, no pagination.** `list_streams` returns all streams in one call.
- **wired vs classic.** Only `wired` streams carry a managed field schema and
  child-routing; `classic` streams (e.g. the APM data streams) report zero for
  both. The lifecycle mode (`dsl`/`ilm`/`inherit`/`disabled`) and any
  `data_retention` are surfaced from the stream's ingest lifecycle.
- **Counts, not step detail.** Processing steps and routing rules are surfaced as
  counts; the managed field schema (the populated, verifiable part) is surfaced
  in full by `get_stream_ingest`.
- **`set_stream_processing`, `set_stream_retention`, `activate_fork`, and
  `deactivate_fork` are all read-modify-write.** Each reads the stream's current
  ingest config, edits only its own facet (or, for the fork tools, only the
  matching routing entry's `status`), and re-sends the whole config — every
  other facet/entry is preserved untouched. `activate_fork`/`deactivate_fork`
  error if `child` is not a routing entry of `parent`.

## fleet toolbox

Thirty-five tools across three tiers (`src/kibana_mcp/toolboxes/fleet/toolbox.py`),
tagged `{"fleet", <tier>}`. Enable with `KIBANA_MCP_TOOLBOXES=fleet`. Read,
configure, and operate **Fleet** — the Elastic Agent fleet, agent + integration
(package) policies, enrolled agents, integrations (EPM), and outputs — all GA on
a **Basic** license. Twenty `read` tools are visible at every tier; the six
`write` tools appear at the default `write` tier; the nine `destructive` tools
(policy/output deletes, plus single and bulk agent-lifecycle commands) require
`KIBANA_MCP_TIER=destructive`.

!!! warning "Agent-lifecycle actions are destructive-tier, not write-tier"
    Every tool that commands a running enrolled agent — `reassign_agent`,
    `upgrade_agent`, `unenroll_agent`, and their bulk equivalents — is
    `destructive` tier with `destructiveHint=True`, even though none of them
    delete a Kibana object: changing what a live agent collects/ships, swapping
    its binary, or unenrolling it is a destructive act on infrastructure. Bulk
    tools act ONLY on the explicit `agent_ids` list given (never a fleet-wide
    sweep or kuery) and require `confirm=True`, like `disable_streams`; they are
    async and return an `action_id`, not the immediate per-agent result.

!!! note "Secret-redacted reads; enrollment-key mint/revoke deferred"
    Three read families are SECRET-REDACTED: `list_enrollment_keys` /
    `get_enrollment_key` drop the `api_key` value; outputs never accept or
    return ssl/secret fields (`create_output`/`update_output` take non-secret
    fields only — no TLS/authenticated outputs); `list_uninstall_tokens` never
    fetches token *values* (metadata only). Enrollment-key minting/revocation —
    the only secret-minting surface in this toolbox — is deliberately out of
    scope, deferred for a vetted credential-handling design. EPM package
    install/delete and Cloud-only surfaces (agentless policies, cloud
    connectors, proxies) are also out of scope.

| Tool | Tier | Description |
|---|---|---|
| `get_fleet_settings` | read | Global Fleet settings: whether prerelease integrations and the integration knowledge base are enabled, and the space-awareness migration status. |
| `check_fleet_permissions` | read | Check whether the current API key has the privileges to operate Fleet. Use first if other fleet tools return authorization errors. |
| `list_agents` | read | List enrolled Elastic Agents (including inactive ones): id, status, assigned policy_id, hostname, version, last check-in. Empty when no agents are enrolled. |
| `get_agent` | read | Get one Elastic Agent by id: status, assigned policy_id, hostname, version, enrolled-at, last check-in. |
| `get_agent_status_summary` | read | Fleet-wide agent status counts: online, error, offline, inactive, updating, unenrolled, and total. |
| `list_agent_versions` | read | List the Elastic Agent versions available for upgrade on this deployment (newest first). |
| `list_agent_policies` | read | List agent policies: id, name, namespace, assigned agent_count, status, managed flag, enabled monitoring (logs/metrics). |
| `get_agent_policy` | read | Get one agent policy by id: name, namespace, description, agent_count, status, managed flag, enabled monitoring. |
| `list_package_policies` | read | List package (integration) policies attached to agent policies: id, name, integration package (name/title/version), parent agent_policy_id, enabled flag. |
| `get_package_policy` | read | Get one package (integration) policy by id: name, namespace, integration package, parent agent_policy_id, enabled flag. |
| `list_enrollment_keys` | read | List enrollment API keys — METADATA ONLY (id, name, policy_id, active, created-at). The secret key value is never returned. |
| `get_enrollment_key` | read | Get one enrollment API key by id — METADATA ONLY; the secret key value is never returned. |
| `list_uninstall_tokens` | read | List agent uninstall tokens — METADATA ONLY (id, policy_id, policy_name, created-at). The decrypted token value is never returned. |
| `list_packages` | read | List integration packages available in the registry: name, title, version, install status, description. (Full catalog; can be large.) |
| `list_installed_packages` | read | List the integration packages actually installed on this deployment: name, title, version, status. |
| `get_package` | read | Get one integration package by name (e.g. `nginx`, `system`): latest title, version, install status, type, description. |
| `list_package_categories` | read | List integration categories (id, title, package count per category) for browsing the integration catalog. |
| `list_outputs` | read | List Fleet outputs (where agent data is shipped): id, name, type (elasticsearch/logstash/kafka), hosts, default flags. Secret/ssl fields never returned. |
| `get_output_health` | read | Get the latest health of one output by id: state (HEALTHY/DEGRADED/UNKNOWN), message, timestamp. |
| `list_fleet_server_hosts` | read | List configured Fleet Server hosts (the URLs agents connect to): id, name, urls, default flag. |
| `create_agent_policy` | write | Create an agent policy. `namespace` scopes its data; `monitoring_enabled` turns on agent monitoring (e.g. `["logs","metrics"]`); `inactivity_timeout` (ms) unenrolls an agent after that long idle. |
| `update_agent_policy` | write | Update an agent policy's fields (read-modify-write — omitted fields preserved). `description=""` clears it; `monitoring_enabled=[]` turns monitoring off. Refuses a managed policy. |
| `create_package_policy` | write | Attach an integration to an agent policy. `package` identifies it (e.g. `{"name": "system", "version": "1.62.0"}` — see `get_package`); `inputs` optionally overrides its default input config. |
| `update_package_policy` | write | Update a package (integration) policy's fields (read-modify-write). `agent_policy_id` re-parents it; `package` changes the integration/version; `inputs` overrides the input config. |
| `create_output` | write | Create a Fleet output (where agent data is shipped). Non-secret fields only (no ssl/secrets/config_yaml). `is_default`/`is_default_monitoring` promote this output, auto-un-defaulting the prior one. |
| `update_output` | write | Update a Fleet output (read-modify-write). Setting `is_default`/`is_default_monitoring=True` PROMOTES it (the prior default becomes deletable). Editing a currently-default output requires `confirm=True`. |
| `delete_agent_policy` | destructive | Delete an agent policy — removes it and its package policies. Refuses a managed policy and the default Fleet Server policy regardless of `force`. `force=True` bypasses the assigned-agent check. |
| `delete_package_policy` | destructive | Delete a package (integration) policy — detaches the integration; agents stop collecting that data. `force=True` bypasses Kibana's in-use checks. |
| `delete_output` | destructive | Delete a Fleet output. Refuses the default output(s) — promote a replacement first (`update_output` `is_default=True`), then delete the old one. No force escape. |
| `reassign_agent` | destructive | Reassign one enrolled agent to a different agent policy — changes what the live agent collects/ships. Refuses a managed or default Fleet Server target policy. |
| `upgrade_agent` | destructive | Upgrade one enrolled agent to `version` (must be one of `list_agent_versions`). Commands a live binary swap; async — the agent applies it on its own schedule. |
| `unenroll_agent` | destructive | Unenroll one Elastic Agent — it stops shipping data. `force=True` unenrolls an already-unenrolled/offline agent; `revoke=True` also invalidates its API key immediately. |
| `bulk_reassign` | destructive | Reassign the given `agent_ids` to a different agent policy. Explicit ids only (never a fleet-wide sweep), requires `confirm=True`. Async — returns an `action_id`. |
| `bulk_upgrade` | destructive | Upgrade the given `agent_ids` to `version`. Explicit ids only, requires `confirm=True`. Async — queues the upgrade per-agent and returns an `action_id`. |
| `bulk_unenroll` | destructive | Unenroll the given `agent_ids`. Explicit ids only, requires `confirm=True`. Async — returns an `action_id`. |

Notes:

- **Bare arrays, no pagination surfaced.** All fleet list tools walk Kibana's own
  pagination internally and return the full list in one call.
- **Updates are read-modify-write via a writable-field allowlist.** `update_agent_policy`
  / `update_package_policy` / `update_output` fetch the current raw object, then
  re-send only the fields Kibana's update API actually accepts (plus the caller's
  changes) — read-only fields (`revision`, `updated_at`, `status`, …) are dropped
  automatically rather than round-tripped, and omitted fields are preserved rather
  than reset to their default.
- **Reserved/default guards.** `delete_agent_policy` and `reassign_agent`/
  `bulk_reassign` refuse a managed or default-Fleet-Server policy; `delete_output`
  and editing a currently-default output are similarly guarded — promote a
  replacement output before deleting the old default.
- **Bulk = explicit ids + confirm, never a sweep.** `bulk_reassign`/`bulk_upgrade`/
  `bulk_unenroll` only accept an explicit, non-empty `agent_ids` list (the
  fleet-wide kuery form Kibana's API also supports is not exposed) and reject the
  call outright without `confirm=True`.
- **Agent-lifecycle results are async.** `reassign_agent`/`upgrade_agent`/
  `unenroll_agent` return `{"ok": true, "agent_id": ...}` once Kibana accepts the
  command, not once the agent has applied it; the bulk equivalents return
  `{"action_id": ...}` for the same reason.
- **No enrollment-key mint/revoke, no EPM install/delete.** See the note above —
  both are deliberately out of scope for this tier.
