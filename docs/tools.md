# Tool reference

Toolboxes are selected via
[`KIBANA_MCP_TOOLBOXES`](configuration.md): **dashboards**,
**[data-management](#data-management-toolbox)**,
**[alerting](#alerting-toolbox)**, **[cases](#cases-toolbox)**,
**[platform-health](#platform-health-toolbox)**,
**[observability](#observability-toolbox)**,
**[security-detections](#security-detections-toolbox)**,
**[platform-admin](#platform-admin-toolbox)**,
**[streams](#streams-toolbox)**, and **[fleet](#fleet-toolbox)** — 133 tools in
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
| [`create_visualization`](#create_visualization) | write | Create a reusable visualization in the library (not on a dashboard). |
| [`add_panel`](#add_panel) | write | Add a visualization panel to an existing dashboard. |
| [`update_panel`](#update_panel) | write | Replace the visualization at a given panel index. |
| [`delete_dashboard`](#delete_dashboard) | destructive | Permanently delete a dashboard. |
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
| read | `list_data_views`, `describe_data_view`, `search_dashboards`, `get_dashboard` | `true` | — | — |
| write | `create_visualization`, `add_panel` | `false` | `false` | `false` |
| write (upsert) | `create_dashboard`, `update_panel` | `false` | `false` | `true` — `update_panel`: replacing the same index twice is a no-op; `create_dashboard`: re-creating the same title overwrites, never duplicates. |
| destructive | `delete_dashboard`, `delete_panel` | `false` | `true` | `true` |

All tools also set `openWorldHint=False` (Kibana is a closed, known system,
not an open web search).

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

A `date_histogram` entry additionally requires `time_field` to be set on
the `VizSpec` — see the next section, since this interacts with when
auto-fill happens.

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

The server fills in `time_field` from the data view's own default time
field when the caller omits it — but the mechanics matter:

1. FastMCP parses the tool call arguments into a `VizSpec`, running the
   `_chart_rules` validator immediately. If any `group_by` entry has
   `kind: "date_histogram"` and `time_field` is still `null` at this
   point, **the call fails right here** with `"date_histogram group_by
   requires time_field"` — auto-fill has not happened yet.
2. Only if the spec passed step 1 does the toolbox code
   (`_prepare_config` in `toolbox.py`) look up the named `data_view` and,
   if `time_field` is still `null` and the data view has a default time
   field, copy it onto the spec.

In practice: for chart types that don't use `date_histogram`, you can omit
`time_field` and the server fills it in (used to scope the Lens data
source). For a `date_histogram` group_by, supply `time_field` explicitly —
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
  "panels": [{"index": 0, "type": "vis", "title": "..."}]
}
```

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

### `delete_panel`

```json
{"id": "...", "deleted_panel": 0}
```

## Error behavior

### Pre-flight spec validation

Before any of `create_dashboard`, `create_visualization`, `add_panel`, or
`update_panel` touch Kibana, `_prepare_config` calls
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

### The unsupported-panels refusal

`add_panel`, `update_panel`, and `delete_panel` all read-modify-write an
*existing* dashboard via `_checked_dashboard_data`
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
- Any error surfaced by Kibana itself (auth, not-found, schema rejection,
  connectivity) is translated from the gateway's domain exceptions
  (`KibanaAuthError`, `KibanaNotFound`, `KibanaRejected`,
  `KibanaUnavailable` in `core/errors.py`) into a plain-text `ToolError` by
  `gateway_errors()` in `toolboxes/base.py` — messages are written to be
  safe and useful to show the calling LLM (no secrets, no stack traces).

## Space targeting

Every one of the 63 tools in the **dashboards**, **data-management**,
**alerting**, **cases**, and **security-detections** toolboxes — including
the 14 destructive-tier ones — accepts an optional `space` parameter that
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
  **list-returning** tools — `search_dashboards`, `list_data_views`,
  `list_alert_rules`, `list_connectors`, `list_cases`,
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
configuration (deferred as a follow-up).

## data-management toolbox

Data views (the datasets you visualize) and short URLs. The saved-objects
export/import/overwrite tools are also part of this toolbox — see
[saved-objects export/import](#saved-objects-exportimport-data-management) below.

| Tool | Tier | Description |
|---|---|---|
| `list_data_views` | read | List Kibana data views (the datasets you can visualize). |
| `describe_data_view` | read | Get a data view's fields and types. |
| `resolve_short_url` | read | Resolve a Kibana short-URL slug to its locator and target app path. |
| `create_data_view` | write | Create a Kibana data view over an index pattern (e.g. `logs-*`). |
| `create_short_url` | write | Create a Kibana short URL for a locator + app path. |
| `delete_data_view` | destructive | Permanently delete a data view by id. This cannot be recovered. |
| `delete_short_url` | destructive | Permanently delete a short URL by id. |

## alerting toolbox

Alerting rules and action connectors.

| Tool | Tier | Description |
|---|---|---|
| `list_alert_rules` | read | List alerting rules (optionally filtered by a name search). |
| `get_alert_rule` | read | Get one alerting rule's summary by id. |
| `get_alerting_health` | read | Kibana alerting framework health status. |
| `list_connectors` | read | List configured action connectors (id, name, connector_type_id). |
| `create_alert_rule` | write | Create an alerting rule (e.g. `rule_type_id` `.es-query`). |
| `enable_alert_rule` | write | Enable (start running) an alerting rule. |
| `disable_alert_rule` | write | Disable (stop running) an alerting rule. |
| `create_connector` | write | Create an action connector (e.g. `.index`, `.webhook`). |
| `delete_alert_rule` | destructive | Permanently delete an alerting rule. |
| `delete_connector` | destructive | Permanently delete an action connector. This cannot be undone. |
| `execute_connector` | destructive | Run a connector now with the given type-specific params. |

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
 "version": "9.4.3", "unhealthy": []}
// ...unhealthy lists only services whose level != "available":
// "unhealthy": [{"name": "reporting", "level": "unavailable", "summary": "..."}]

// get_kibana_stats
{"heap_used_bytes": 645926408, "heap_total_bytes": 708210688,
 "heap_size_limit_bytes": 4496293888, "event_loop_delay_ms": 13.7, "concurrent_connections": 4}

// get_task_manager_health
{"status": "OK", "timestamp": "2026-07-12T08:09:54.102Z", "last_update": "2026-07-12T08:09:52.755Z"}
```

## observability toolbox

Ten read-only tools (`src/kibana_mcp/toolboxes/observability/toolbox.py`), all
`read` tier (`readOnlyHint=true`), tagged `{"observability", "read"}`. Enable
with `KIBANA_MCP_TOOLBOXES=observability` (or alongside others). Read access to
the Kibana observability surface an operator can reach on a **Basic** license:
Synthetics monitoring, Uptime settings, and APM **configuration**.

!!! note "v1 scope — what this is *not*"
    This toolbox intentionally does **not** expose APM **service/transaction/
    trace/service-map telemetry** — those are internal-only Kibana APIs
    (`/internal/apm/*`), unreachable by an external client on 9.x and not wrapped
    by kibana-py. It also does not expose **SLOs**, which require a Platinum
    license (they return `403` on a Basic stack). Both are deferred to future
    additive tiers of this same toolbox.

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

// list_apm_agent_configs -> list
[{"service_name": "checkout", "service_environment": "production",
  "settings": {"transaction_sample_rate": "0.5"}, "applied_by_agent": true, "etag": "e1"}]

// list_apm_environments -> list  (ALL_OPTION_VALUE = "all environments")
[{"name": "ALL_OPTION_VALUE", "already_configured": false}]

// search_apm_annotations -> list
[{"id": "a1", "timestamp": "2026-07-12T00:00:00.000Z", "text": "v2 deploy", "type": "deployment"}]
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
    path. The `security-ai` assistant + attack-discovery surface is a
    separate toolbox (needs an LLM connector). Some read object shapes (alerts,
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

!!! note "Still deferred"
    **Logstash pipeline** management needs a **Platinum** license (403 on Basic,
    confirmed live); **session invalidation**, space object copy/move, feature-level
    role grants, and avatar-image editing are out of scope. Reading roles surfaces
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
