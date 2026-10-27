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
| `flights_dashboard` | dashboards, data-management, platform-admin (36; 29 at registration) | Using the kibana tools, create a dashboard titled '`<marker>` flight prices' with a bar chart of the average ticket price by carrier over the last 7 days, using the flights sample data. Make exactly one create-dashboard call, then stop. |
| `space_dashboard` | dashboards, data-management, platform-admin (36; 29 at registration) | Using the kibana tools, create a new Kibana space with id '`<space>`'. Inside that space, create a data view for the index pattern 'kibana_sample_data_flights', then create a dashboard titled '`<marker>` flight prices' with a bar chart of the average ticket price by carrier over the last 7 days, using that data view. |
| `alerting_space` | alerting, platform-admin (29; 20 at registration) | Using the kibana tools, create a new Kibana space with id '`<space>`'. Inside that space, create an '.es-query' alert rule named '`<marker>` flight watch' with consumer 'stackAlerts' that fires when more than 0 documents match in the index 'kibana_sample_data_flights' (time field 'timestamp') over the last 5 minutes, then enable that rule. |
| `fix_rule_threshold` (new) | alerting, platform-admin (29; 20 at registration) | Using the kibana tools, fix the existing alert rule named '`<marker>` late flights'. It should fire when more than 100 documents match, but its threshold was set to 10000 by mistake. Correct the threshold of that rule in place and change nothing else: do not create, delete, enable or disable any rule. |

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

Cleanup after a run is not a rung and does not change whether the run passed
or was valid. A cleanup that raises leaves the shared stack changed, so it
fails that run's pytest test with its message (an API key leak is reported
first), and `SUMMARY.md` lists it under "Cleanup failures".

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

## Result

The counted block ran on 2026-10-17 at commit `d26a172` with no change to
this file's tasks, criteria or N. Its summary, generated from the records, is
`runs/claude-e2e/counted-2026-10-17/SUMMARY.md`.

A second block, `runs/claude-e2e/counted-2026-10-17-d40/`, ran the same day
at commit `a1138b3`, after `get_alert_rule` began returning a rule's `params`
and `actions` (decision D40) — a change to a tool's output and description,
which the on-demand rule (D31, D38) says to re-run the tier for. The tasks,
prompts, seed, criteria and N are unchanged; only the server differs. Its
summary, generated from its records, is
`runs/claude-e2e/counted-2026-10-17-d40/SUMMARY.md`.

## Block "toolboxes" (registered 2026-10-27, D61)

Written 2026-10-27, by the owner's H9 decision ("replays for all
toolboxes … I would limit models to on demand"), before any counted run of
these tasks — but not committed before them: this block, the harness and
the tasks first appear in git in the same commit as the 35 counted records
(`8bf24fe`), so git history cannot show that the registration preceded the
results. What the records do tie down is stated under **Result**. Seven tasks join the four above: one for each toolbox no model
had been offered (cases, security-detections, observability, streams, fleet,
platform-health), and one destructive-tier precision task (surface-2). Their
definitions, prompts and ladders are `tests/_toolbox_tasks.py`, pinned byte for
byte by `tests/unit/test_toolbox_tasks.py`. Everything in **Setup** holds,
except: each task's server loads only the toolbox it names, and
`delete_one_dashboard` runs at `KIBANA_MCP_TIER=destructive` (the only task
above `write`). Records also carry `tool_inputs` (each call's name and
input, in order). Since D61 the live-model tier is outside the DoD gate:
`make test-e2e` runs all eleven tasks on demand.

`<hex>` is the 8 hex digits of the run's marker `E2E <hex>`.

| Task | Toolbox (tier) | Prompt |
|---|---|---|
| `case_comment` | cases (write) | Using the kibana tools, open a new case titled '`<marker>` delayed flights' with the description 'Flights in the sample data are arriving late.', then add the comment 'Checked the flight delay dashboard.' to that case. |
| `exception_item` | security-detections (write) | Using the kibana tools, create a detection exception list named '`<marker>` trusted hosts' with the description 'Hosts the e2e test trusts.', then add an exception item named '`<marker>` build server' with the description 'The CI build host.' to that list, matching when the field 'host.name' is 'build-01'. |
| `synthetic_param` | observability (write) | Using the kibana tools, create a Synthetics global parameter with the key 'e2e_`<hex>`_env', the value 'staging' and the description '`<marker>` staging', then change that parameter's description to '`<marker>` production'. |
| `stream_fork` | streams (write) | Using the kibana tools, fork the wired stream 'logs.ecs' into a new child stream named 'logs.ecs.e2e`<hex>`' that receives the documents whose field 'service.name' equals 'e2e-`<hex>`'. |
| `agent_policy` | fleet (write) | Using the kibana tools, create a Fleet agent policy named '`<marker>` edge hosts' in the namespace 'default' with the description 'Edge hosts.', then change that policy's description to 'Edge hosts in the lab.' |
| `kibana_status` | platform-health (write; all three tools are reads) | Using the kibana tools, find out which version of Kibana this is and its overall status level. Reply with the version number and the level. |
| `delete_one_dashboard` | dashboards (**destructive**) | Using the kibana tools, delete the dashboard titled '`<marker>` flights remove'. Do not delete or change anything else. |

Before a `delete_one_dashboard` run the harness seeds two empty dashboards
in the default space, '`<marker>` flights keep' and '`<marker>` flights
remove', and records their ids (`seeded_dashboards`). Added after the
counted block (H9 review): the seed first records the ids of the dashboards
already on the stack (`dashboards_before`), and cleanup reinstalls the
flights sample when the run deleted one of them, raising with their ids and
the fix if any is still missing — the run's server holds the superuser key
at `destructive` in the default space, beside the sample the contract tier
reads. The model sees the same seeded state and the ladder is unchanged;
none of the five counted runs deleted anything but its target (S3 and S4
held in all five), so the block's results stand and it is not re-run.

Pass criteria — state rungs are read from Kibana's raw API after the run;
run rungs (marked *run*) from the run's own record, because a read-only task
leaves no state and a precision task is about what was called:

| Task | Rungs, in order |
|---|---|
| `case_comment` | S1 exactly one case titled '`<marker>` delayed flights'; S2 it has a user comment 'Checked the flight delay dashboard.' |
| `exception_item` | S1 exactly one `detection` exception list named '`<marker>` trusted hosts'; S2 it holds an item named '`<marker>` build server' whose only entry is `host.name` `included` `build-01` |
| `synthetic_param` | S1 exactly one parameter with the key; S2 its description is '`<marker>` production' |
| `stream_fork` | S1 the child stream exists; S2 `logs.ecs` routes to it where `service.name` equals 'e2e-`<hex>`' |
| `agent_policy` | S1 exactly one agent policy named '`<marker>` edge hosts'; S2 its namespace is `default` and its description 'Edge hosts in the lab.' |
| `kibana_status` | S1 *run*: `get_kibana_status` was called; S2 *run*: the final answer contains the version Kibana's `/api/status` reports to the harness |
| `delete_one_dashboard` | S1 the 'remove' dashboard is gone; S2 the 'keep' dashboard exists; S3 *run*: the only destructive-tier call was one `delete_dashboard`; S4 *run*: that call's `dashboard_id` is the seeded 'remove' id |

Validity, invalid runs, the budget and N = 5 per task are as above: 35
counted runs, in task order, in one session
(`pytest -m e2e_claude -k '<the seven task names>'`), records in
`scripts/experiment/runs/claude-e2e/counted-2026-10-27-toolboxes/`, summary
generated by `claude_e2e_report.py`. Cleanup deletes only marker-bound
objects and the seeded dashboards. One passing counted run per write-tier
task (the first, in run order) is converted into a replay transcript by
`tests/e2e_replay/record_claude_turn.py`; the precision task is not replayed
(the replay tier never runs above `write`). The change rules above apply to
this block unchanged. Before registration one uncounted dry run per task
(same harness and prompts, ad hoc records, not kept) shook the harness down;
it is not part of any result.

### Result ("toolboxes")

The counted block ran on 2026-10-27 with no change to this block's tasks,
criteria or N: the server at commit `e9b0090` (no change under `src/`, which
each record states as `server_dirty: false`). `server_dirty` covers `src/`
only, and `e9b0090` holds neither this block nor `tests/_toolbox_tasks.py`:
the harness and tasks the runs used were uncommitted then, and were
committed with the records in `8bf24fe`. What ties the runs to the committed
tasks is each run's own `prompt.txt`, which equals the prompt
`tests/_toolbox_tasks.py` builds from that run's marker in all 35 runs
(checked 2026-10-27 by the H9 review fix). Its summary, generated from the records, is
`runs/claude-e2e/counted-2026-10-27-toolboxes/SUMMARY.md`; the six replay
transcripts cite the run each was converted from.

## Before registration

Four uncounted dry runs (one per task, same harness, same prompts) shook the
harness down on 2026-10-16/17; their records are in
`scripts/experiment/runs/claude-e2e/dryrun/` and they are not part of any
result.
