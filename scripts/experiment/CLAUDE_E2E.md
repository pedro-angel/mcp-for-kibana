# Claude live-model tier: pre-registration

`make test-e2e` drives Claude through this server and scores what lands in
Kibana. This file fixes the tasks, the pass criteria, the number of runs and
the analysis **before any counted run**, in the way the studies beside it did
(STUDY_DESIGN.md, LOCAL_STUDY.md). Decision D38 in
[docs/spec/decisions.md](../../docs/spec/decisions.md) records why the tier
exists; the harness is `tests/e2e/test_claude_live.py` with
`tests/_claude_cli.py` (the `claude -p` driver) and `tests/_live_tasks.py`
(the tasks, shared with the LM Studio tier).

Registered 2026-10-17, by the owner's decisions of 2026-10-16.

## Question

Can Claude, given only this server's tools at the default `write` tier, carry
out each live-model task from one plain-English prompt, judged by Kibana's
state afterwards? The owner accepted a Claude run as enough live-model
evidence for merging PR #25 (Q13).

## Setup

| Item | Fixed value |
|---|---|
| Model | `CLAUDE_E2E_MODEL` unset: `--model haiku`, the alias. Each record names the model the CLI reports (`models_reported`). |
| Client | Claude Code headless: `claude -p <prompt> --output-format stream-json --verbose --restricted --disable-slash-commands --tools "" --strict-mcp-config --mcp-config <file> --no-session-persistence --permission-mode dontAsk --allowedTools mcp__mcp-for-kibana --max-budget-usd 0.75` |
| Tools offered | This server's tools only. `--tools ""` removes every built-in tool (no shell, no files, no web). The init event's tool list is recorded per run (`offered_tools`, `non_server_tools`). |
| Server | This checkout (`uv run mcp-for-kibana`, stdio), `KIBANA_MCP_TIER=write`, toolboxes per task (below). |
| API key | Only in the MCP server's `env` block, in a mode-0600 config file in a temporary directory. The model's environment drops every `KIBANA*` variable and any value holding the key; no prompt names it. |
| Kibana | The dev stack (`scripts/stack.sh up && scripts/stack.sh seed`), flights sample data; version recorded per run. |
| Working directory | A fresh empty temporary directory per run. |
| Runs | **N = 5 per task**, 20 counted runs, in task order, in one `make test-e2e` session. |
| Records | `scripts/experiment/runs/claude-e2e/counted-2026-10-17/`: per run `prompt.txt`, `transcript.jsonl`, `record.json` (and `stderr.txt` when non-empty); all records in `runs.jsonl`. |
| Budget | The session stops starting runs once its cumulative cost reaches $5 (`CLAUDE_E2E_BUDGET_USD`); each run is capped at $0.75. |

## Tasks

`<marker>` is `E2E <8 hex>` and `<space>` is `e2e-<model slug>-<8 hex>`
(`e2e-haiku-…` here), both fresh per run. The prompt texts are pinned byte for byte by
`tests/unit/test_live_tasks.py`; the first three are the texts the LM Studio
tier has always sent.

| Task | Toolboxes (tools at `write`) | Prompt |
|---|---|---|
| `flights_dashboard` | dashboards, data-management, platform-admin (29) | Using the kibana tools, create a dashboard titled '`<marker>` flight prices' with a bar chart of the average ticket price by carrier over the last 7 days, using the flights sample data. Make exactly one create-dashboard call, then stop. |
| `space_dashboard` | dashboards, data-management, platform-admin (29) | Using the kibana tools, create a new Kibana space with id '`<space>`'. Inside that space, create a data view for the index pattern 'kibana_sample_data_flights', then create a dashboard titled '`<marker>` flight prices' with a bar chart of the average ticket price by carrier over the last 7 days, using that data view. |
| `alerting_space` | alerting, platform-admin (20) | Using the kibana tools, create a new Kibana space with id '`<space>`'. Inside that space, create an '.es-query' alert rule named '`<marker>` flight watch' with consumer 'stackAlerts' that fires when more than 0 documents match in the index 'kibana_sample_data_flights' (time field 'timestamp') over the last 5 minutes, then enable that rule. |
| `fix_rule_threshold` (new) | alerting, platform-admin (20) | Using the kibana tools, fix the existing alert rule named '`<marker>` late flights'. It should fire when more than 100 documents match, but its threshold was set to 10000 by mistake. Correct the threshold of that rule in place and change nothing else: do not create, delete, enable or disable any rule. |

Before a `fix_rule_threshold` run the harness seeds the broken state in the
default space: a **disabled** `.es-query` rule named '`<marker>` late
flights', consumer `stackAlerts`, schedule `1m`, tags `["e2e"]`, params index
`kibana_sample_data_flights`, time field `timestamp`, query
`{"query": {"match": {"FlightDelay": true}}}`, size 100, threshold `[10000]`,
comparator `>`, window 5 m. The rule is read back after creation; that
snapshot (with the defaults Kibana adds) is the baseline.

## Pass criteria

Every rung is read from Kibana after the run, never from the model's text. A
run passes when every rung of its task holds **and** the run is valid.

| Task | Rungs, in order |
|---|---|
| `flights_dashboard` | S1 a dashboard with the marker exists; S2 `assert_model_built_dashboard` accepts it (first panel `xy`, y = average of `AvgTicketPrice`, x = `Carrier`) |
| `space_dashboard` | S1 the space exists; S2 a data view for `kibana_sample_data_flights` exists in it; S3 a marker dashboard exists in it; S4 its panel passes the same assertion (`tests/_space_ladder.py`) |
| `alerting_space` | S1 the space exists; S2 a marker-named `.es-query` rule exists in it; S3 that rule is enabled (`tests/_alerting_space_ladder.py`) |
| `fix_rule_threshold` | S1 the seeded rule (same id) still exists; S2 its threshold is `[100]` with comparator `>`; S3 its name, tags, schedule, enabled state and every other param equal the baseline; S4 no other marker-named rule exists |

A run is **valid** when the harness raised no error, the API key does not
appear in its transcript or stderr, the model was offered no tool but this
server's, the CLI reported no permission denial, and the CLI ended with a
result event (or the run timed out, which is a valid failed run). An invalid
run counts as not passed. No run is re-run or dropped.

## Analysis

Generated from `runs.jsonl` by `claude_e2e_report.py` into `SUMMARY.md`
beside the records: passes per task out of 5, median cost and turns per run,
total cost, and every failed or invalid run with its first missing rung and
the record's error fields. No threshold was set for "good enough": the counts
are the result.

## Changes after counted runs start

None to the tasks, prompts, seed, criteria or N. If a harness bug is found
after counted runs start, the block is kept as it is, the bug is fixed, the
fix and the reason are stated here, and all 20 runs start again in a new
block.

## Before registration

Four uncounted dry runs (one per task, same harness, same prompts) shook the
harness down on 2026-10-16/17; their records are in
`scripts/experiment/runs/claude-e2e/dryrun/` and they are not part of any
result.
