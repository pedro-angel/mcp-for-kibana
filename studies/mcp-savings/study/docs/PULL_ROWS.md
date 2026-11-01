# Pulling the runs' rows from Honeycomb

Every reported number is computed from rows pulled from Honeycomb, the
study's single source of data. The rows are pulled with the Honeycomb MCP
connector (`run_query` in raw-row mode), page by page, and each page's
full-precision JSON is downloaded and merged into one rows file.
`mcp_savings_analyze.py` and `mcp_savings_explore.py` read that file and
nothing else.

> Published copy. The paths below are those of the study's working
> repository: on this branch `scripts/experiment/` is `study/harness/` and
> `docs/results/study/` is `study/data/`.

Where the data is: team `pedro-angel`, environment `test` (EU), dataset
`mcp-savings`. Each run sends one root span `efficiency.run` (service.name
`mcp-savings`); a rescore sends one span `efficiency.rescore` per run,
keyed by `study.run_id`.

## 1. Census: how many rows to expect, and when

One aggregate query, to find the time window and the number of root spans:

```json
{"environment_slug": "test", "dataset_slug": "mcp-savings",
 "query_spec": {
   "calculations": [{"op": "COUNT"}],
   "filters": [{"column": "name", "op": "in", "value": ["efficiency.run", "efficiency.rescore"]}],
   "breakdowns": ["name", "study.config_sha", "study.phase", "study.harness_sha"],
   "from": "-7d", "to": "now"},
 "include_markers": false}
```

The time series shows when the rows arrived; take an absolute window that
holds them all with an hour's margin each side (the 2026-10-17 smoke test:
`2026-10-17T06:00:00Z` to `2026-10-17T14:00:00Z`). The window does not
separate attempts: rows of an earlier attempt on another harness commit
may fall inside it and are pulled too. The analysis separates them by
`study.harness_sha` (step 7). Note the COUNT for the config and phase,
summed over the harness shas: the merged rows file must hold exactly that
many `efficiency.run` rows, and the frozen commit's own COUNT is what the
analysis counts from. Do not use `COUNT_DISTINCT(study.run_id)` as a
check: it is approximate (it said 599 for the 600 distinct run ids of
config `09dfcb7e6974`). Duplicates are found by the analysis, from the rows.

## 2. The columns

```
uv run python scripts/experiment/mcp_savings_analyze.py columns              # root spans
uv run python scripts/experiment/mcp_savings_analyze.py columns --span rescore
```

prints the `raw_row_columns` as a JSON list. Honeycomb refuses a query that
names a column the dataset has never seen (`unknown column`): before the
harness change lands, `study.harness_sha`, `study.aborted` and
`study.invalid_reason` may not exist yet. Check with `get_dataset_columns`
and drop the missing ones; the analysis treats an absent column as absent
on every row. The span name, config sha and phase are not columns: they are
the query's filters, and step 5 writes them into every row.

## 3. The root spans, page by page

```json
{"environment_slug": "test", "dataset_slug": "mcp-savings",
 "query_spec": {
   "filters": [
     {"column": "name", "op": "=", "value": "efficiency.run"},
     {"column": "study.config_sha", "op": "=", "value": "<config sha>"},
     {"column": "study.phase", "op": "=", "value": "<study|pilot>"},
     {"column": "trace.parent_id", "op": "does-not-exist"}],
   "from": "<absolute start, RFC3339>", "to": "<absolute end, RFC3339>"},
 "raw_row_columns": ["<the list from step 2>"],
 "include_markers": false}
```

- Leave `results_limit` out: the connector returns as many rows as fit its
  cap of 1000 values per page, timestamp included, that is
  `floor(1000 / (columns + 1))` rows. With the 33 root columns: 29 rows a
  page, 21 pages for 600 runs. The smoke test, with the 30 columns the
  dataset had: 32 rows a page, 19 pages (18 × 32 + 24 = 600).
- Pagination: while the response's Metadata has `next_cursor`, send the
  same request again with `"cursor": "<next_cursor>"`, the same filters and
  columns, and `from`/`to` set to the absolute values the Metadata
  reports. The last page has no `next_cursor`.
- Rows come in storage order, not time order; that does not matter.

## 4. Download every page's JSON

Each response's Metadata has `query_result_json:
"https://mcp.eu1.honeycomb.io/query_results/<token>"`. Download it right
away, one file per page (the token expires 15 minutes after the query):

```
curl -sS -o pull/page-01.json "<query_result_json>"
```

Never transcribe the Markdown table the connector prints: it rounds floats
(`study.total_cost_usd` shows `0.17` where the JSON has `0.1741214`), and
the price check needs the exact values. The JSON has, per row, `data` (the
columns; a column the span lacks is absent or null), `time` and `row_id`,
and `template.filters` (the query's filters).

## 5. The rescore spans

Only once any run has been rescored (before that the columns do not exist
and the query is refused, which means there is nothing to pull):

```json
{"environment_slug": "test", "dataset_slug": "mcp-savings",
 "query_spec": {
   "filters": [{"column": "name", "op": "=", "value": "efficiency.rescore"}],
   "from": "<the start of step 3>", "to": "now"},
 "raw_row_columns": ["<the list from `columns --span rescore`>"],
 "include_markers": false}
```

Paginate and download the same way (`rescore-01.json`, ...). The analysis
matches rescores to runs by `study.run_id`; for each run the row with the
newest `study.rescored_at` replaces its checks and success.

## 6. Merge the pages into the rows file

```
uv run python scripts/experiment/mcp_savings_analyze.py rows --out rows.jsonl pull/*.json
```

It prints the number of rows per span name: the `efficiency.run` count must
equal the census of step 1. Keep the rows file and the pages outside git
(the scratch directory, or `scripts/experiment/runs/`, which is ignored).

## The rows file

JSON lines, one object per span, sorted by time:

- every pulled column under its Honeycomb name (`study.total_cost_usd`,
  `agent.tool.failures`, `trace.trace_id`, ...); a column the span lacks is
  left out, never written as null;
- `name`, `study.config_sha`, `study.phase`: the query's equality filters,
  written in;
- `time`: the span's timestamp (RFC3339), and `row_id`: Honeycomb's row id.
  A row seen on two pages (same `row_id`) is kept once.

## 7. Analyse

```
uv run python scripts/experiment/mcp_savings_analyze.py analyze --phase study --rows rows.jsonl --harness-sha <frozen commit>
uv run python scripts/experiment/mcp_savings_explore.py --phase study --rows rows.jsonl --harness-sha <frozen commit>
```

`--config-sha` defaults to the sha of `mcp_savings.json` as it stands;
`--out` to `scripts/experiment/runs/mcp-savings/<phase>/`.
`--harness-sha` keeps only the root spans whose `study.harness_sha` is that
commit (at least 7 characters; one sha a prefix of the other is the same
commit, since git can abbreviate a commit to different lengths in different
clones). Without it, every root span of the config and phase must carry the
same `study.harness_sha`, or none may (older data, such as the smoke test
below); otherwise the analysis stops (`FAIL:`, exit 2) and lists each sha,
`(none)` included, with its count. Rescore rows apply by `study.run_id`
whatever their own `study.harness_sha`, which names the rescoring code, not
the run's. The analysis
exits 1, and its page says NOT COMPLETE, when a planned run is missing or
appears more than once among the valid root spans, a run id is on more than
one root span, or a cost does not reproduce from its tokens; it then writes
no statistics unless `--allow-incomplete` (a machinery check, never
results). `--audit-runs <phase folder>` compares the rows with the local
run records, and a mismatch stops the report too.

## Smoke test, 2026-10-17

Config `09dfcb7e6974`, phase study (600 runs, server 0.3.0, before the
harness change): 19 `run_query` pages, 19 downloads, 600 rows. The analysis
completed: 600 of 600 planned runs counted, no missing or duplicate runs,
and the price table reproduced the CLI's cost of all 600 runs from their
token totals. `harness_sha` was absent on every row and was reported as
such. A machinery check only: those runs are not the study's results.

## The CLI spans, for "where the time goes" (exploratory)

`mcp_savings_explore.py --spans` reads the CLI's own spans from the runs'
traces: `claude_code.tool.execution` (one per tool call, its duration is
the tool's run time), `claude_code.tool` (its parent, carries
`tool_name`; joined by `tool_use_id`) and `claude_code.llm_request` (one
per model request). Every one carries `study.run_id`, which attaches it to
its run; use that, not the trace id: in the study one run (b099, Sonnet
without the server) lost its trace context, and its 13 `claude_code.tool`
and 14 `claude_code.llm_request` spans each start a trace of their own (40
spans with their children). `parent.<col>` is not accepted as a raw
column, hence the join.

Census first (COUNT by `name` and `study.phase`, `usage_mode: true`), then
one raw-row query whose single calculated column joins the six fields with
`|`, so a page holds 500 spans instead of 142:

```json
{"environment_slug": "test", "dataset_slug": "mcp-savings",
 "query_spec": {
   "filters": [
     {"column": "name", "op": "in", "value": ["claude_code.tool.execution", "claude_code.tool", "claude_code.llm_request"]},
     {"column": "study.phase", "op": "=", "value": "study"}],
   "calculated_fields": [{"name": "r", "expression":
     "CONCAT($name, \"|\", $study.run_id, \"|\", COALESCE($tool_use_id, \"\"), \"|\", $duration_ms, \"|\", COALESCE($tool_name, \"\"), \"|\", $trace.trace_id)"}],
   "from": "2026-10-17T23:40:00Z", "to": "2026-10-18T01:48:00Z"},
 "raw_row_columns": ["r"],
 "include_markers": false}
```

Page with the cursor and download each page's `query_result_json` as in
steps 3 and 4 (`spans/pull/page-NN.json`), then merge:

```
uv run python scripts/experiment/mcp_savings_explore.py spans --out spans.jsonl pull/page-*.json
uv run python scripts/experiment/mcp_savings_explore.py --phase study --rows rows.jsonl --harness-sha 9d4ec85 --spans spans.jsonl
```

The study, 2026-10-18: 56 pages, 27,860 spans (9,828 tool executions,
9,828 tool spans, 8,204 model requests), equal to the census; 27,854
attach to the 600 counted runs, the other 6 belong to the aborted run.
The pages and the merged file are kept under `docs/results/study/spans/`
beside the root-span rows.

## The first run of each model and setup (exploratory)

`mcp_savings_explore.py --min-reads <file> --pilot-rows <file>` adds the
first run of each cell and whether it started cold. Two downloaded query
results, kept in `docs/results/study/cache/` (pulled 2026-10-18):

- `min-request-cache-read.json`: one aggregate query, `usage_mode: true`,
  filters `name = claude_code.llm_request` and `study.phase = study`,
  calculations `MIN(cache_read_tokens)` and `COUNT`, breakdown
  `study.run_id`, limit 1000, window `2026-10-17T23:40:00Z` to
  `2026-10-18T01:48:00Z` (601 run ids, the aborted one included; the
  analysis keeps only the counted runs).
- `pilot-root-spans.json`: raw rows of the pilot's root spans (`name =
  efficiency.run`, `study.phase = pilot`, `study.harness_sha = 9d4ec85`,
  `trace.parent_id` does not exist), columns `study.run_id`,
  `study.model_requested`, `study.arm`, `study.wall_s`, `study.worker`,
  `study.aborted`, window `2026-10-17T23:00:00Z` to `23:45:00Z` (36 rows).

```
uv run python scripts/experiment/mcp_savings_explore.py --phase study --rows docs/results/study/rows.jsonl --harness-sha 9d4ec85 --out docs/results/study --spans docs/results/study/spans/spans.jsonl --min-reads docs/results/study/cache/min-request-cache-read.json --pilot-rows docs/results/study/cache/pilot-root-spans.json
```
