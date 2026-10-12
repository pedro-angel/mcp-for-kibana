# Local-model study: runbook

This runbook takes study 5, the local-model study, from a clean Mac to
committed results. A Claude Code session on the owner's Mac (Apple M5 Pro,
64 GB, LM Studio) follows it step by step. The harness is tested against fake
LM Studio, Kibana, Elasticsearch and Honeycomb servers; the Mac session runs
the real pilot and the real runs.

The study asks: how reliably do local models that can do the work complete
the tasks through mcp-for-kibana, and how long do they take? The tasks are
the four of [STUDY_DESIGN.md](STUDY_DESIGN.md) decision 12, shared with
study 4 (Claude Code).

| File | Role |
|---|---|
| `local_study.json` | The study's config: models, stages, pass mark, runs, LM Studio, Honeycomb |
| `tasks.json`, `study_tasks.py` | The four shared tasks: prompts, the seeded starting state, the success checks |
| `local_run.py` | `preflight`, `install-sample`, `pilot`, `stage1`, `stage2`, `status`, `send-pending` |
| `local_analyze.py` | `analysis.json`, Honeycomb query specs, `capture`, `crosscheck` |
| `runs/study5/` | Run records: `pilot/pilot/<run>/`, `stage1/bNN/<run>/`, `stage2/bNN/<run>/` |
| `tests/unit/test_local_study.py` | The harness end to end against the fakes in `tests/_local_study_fakes.py` |

The earlier local study (chain-length levels, `runs/local/`) is kept as it
ran; `local_report.py` reports on it.

## The design, as configured

| Key | Value | Meaning |
|---|---|---|
| `models` | `nvidia/nemotron-3-nano-4b`, `google/gemma-4-12b-qat`, `qwen/qwen3.8-27b` | LM Studio model keys, from `lms ls`: three vendors and architectures, each already seen completing a Kibana task |
| `tasks.file` | `scripts/experiment/tasks.json` | The shared task definition |
| `tasks.stage1` | `weblogs` | Run for every model |
| `tasks.stage2` | `change`, `lookup`, `flights` | Run only for the models that passed stage 1 |
| `tasks.pass_mark` | 14 | A model passes stage 1 with at least 14 successes in 20 runs of every stage-1 task (the Wilson lower bound is then above 0.5). Set before any run |
| `server.entry` | `mcp-for-kibana-study` | The `~/.lmstudio/mcp.json` server the model gets, as `integrations: ["mcp/<entry>"]` |
| `server.toolboxes`, `server.tier`, `server.expected_tools` | `dashboards,data-management,alerting`, `write`, 25 | The same tool surface as study 4's server arm |
| `runs.pilot_per_cell` | 3 | Pilot runs per model × stage-1 task |
| `runs.per_cell` | 20 | Runs per model × task in each stage, one per block `b01`..`b20` |
| `runs.order` | `rotate` | Block *b* rotates the model order and the task order by *b* |
| `runs.max_attempts` | 3 | Tries per cell when the environment fails (an invalid run) |
| `runs.budget_hours` | 12 | Machine time that `status` sizes stage 1 against |
| `lmstudio.load` | `harness` | `harness`: the harness loads each model at its maximum context before its runs (`POST /api/v1/models/load`) and unloads the previous study model, for an LM Studio with JIT loading off. `jit` (the default when absent): the request loads the model |
| `lmstudio.stream` | `true` | Stream the chat, for tool timings. `false` falls back to one JSON response |
| `lmstudio.run_timeout_s`, `idle_timeout_s` | 1800, 600 | Per-run limit (a timed-out run is a failure), and the limit on silence in the stream |
| `kibana.version` | 9.5.5 | Preflight fails on any other version |
| `honeycomb.dataset`, `endpoint`, `required` | `mcp-for-kibana-local`, EU, `true` | Live traces. With `required`, runs refuse to start without the key |

The four tasks (the full prompts: `uv run python scripts/experiment/study_tasks.py prompt <task> server`):

| Task | The prompt asks for | Checks |
|---|---|---|
| `weblogs` | Name the web-logs data view's time field; a dashboard `<marker> Web logs` with exactly two panels (requests over time, requests by response code); an enabled rule `<marker> 5xx errors` (every minute, more than 10 5xx in 5 minutes); retitle the response-code panel `Responses by code` and read the rule back. Ends with `TIME_FIELD:` and `RULE_ENABLED:` lines | The time field matches the data view's; the dashboard has 2 panels on web logs, one about `response`; the rule is enabled, checks every `1m`, fires above 10 in 5 m, runs on the data view's time field, and its filter selects exactly the 5xx documents; the panel title; `RULE_ENABLED` matches the rule's real state |
| `flights` | The same four steps on the flights data: flights over time, flights by carrier, more than 10 cancelled flights in 5 minutes, retitle the carrier panel `Flights by carrier` | The same checks on the flights index and `Cancelled` |
| `change` | Add a requests-by-host panel to the seeded dashboard 'Site health' and enable the seeded rule 'Server errors', in place | The seeded ids survive; 3 panels, one about `host`; the rule enabled; no new dashboard or rule |
| `lookup` | Report the panel count of 'Site health' and the 5xx rule's name, interval and enabled state, changing nothing. Ends with four answer lines | Each line matches Kibana's value; the seeded objects unchanged; nothing new |

A run passes when every check of its task passes. `steps_passed` counts the
leading steps whose checks all pass, so a failed run still says how far it
got. Every check's result, with its expected and actual value, is in the
record (`criteria`) and on the trace's root span (`study.check.*`). The
seeded tasks restore 'Site health' and 'Server errors' at fixed ids before
each run. Each request carries only `model`, `input`, `integrations` and
`stream`. The harness sends no `context_length` and no sampling parameters,
so every model runs as loaded, with its own defaults
([docs/e2e-setup.md](../../docs/e2e-setup.md) explains why).

A run is **invalid** when the environment failed rather than the model: LM
Studio could not load the model, refused the token or the mcp.json plugin, or
did not answer. Invalid runs are kept, excluded from the rates and retried up to
`max_attempts`. A timeout is a valid, failed run. So is LM Studio's
`tool_format_generation_error`, which is the model's own malformed tool call.

## What the cloud session verified, and what only the Mac can

Verified against the fakes (`uv run pytest tests/unit/test_local_study.py`):
records written for every run, scoring through the shared checks with every
check on the root span, the stage-1 verdict and stage 2 running only the
models that passed, the seed restored before seeded tasks, the first
`chat.end` read even when a second one follows, invalid runs retried, a
timed-out stream's calls kept, Kibana reset to its pre-run state with
existing objects untouched, resume after a crash, redaction of the Kibana key
on disk and in traces, refusal to send when redaction misses the key,
preflight reporting a fix for each failure, mcp.json never written, listing
the real server's 25 tools over stdio, and the analysis (Wilson intervals,
pass^k, re-derivation consistency). The checks themselves are verified on the
live stack (STUDY_DESIGN.md decision 12).

Only the Mac verifies:

- **LM Studio's stream.** The event names (`tool_call.start`,
  `tool_call.success`, `tool_call.failure`, `reasoning.delta`,
  `message.delta`, `chat.end` carrying `result`) are the harness's reading of
  LM Studio's streaming API, and the fakes encode the same reading. The pilot
  confirms them (step 7).
- **The `stats` fields** (`input_tokens`, `total_output_tokens`,
  `reasoning_output_tokens`, `tokens_per_second`,
  `time_to_first_token_seconds`, `model_load_time_seconds`) and the
  `/api/v1/models` fields (`key`, `loaded_instances[].config.context_length`,
  `max_context_length`).
- **The checks against what local models produce**: a correct result that a
  check rejects is a harness bug (step 7).
- **Real model behaviour, JIT loading and memory** across about 19 models.
- **Live ingest** into Honeycomb.

## Step by step

All commands run from the repository root.

### 0. Prerequisites

- Docker Desktop is running.
- LM Studio 0.4 or later, with the `lms` CLI on the PATH (`lms version`).
- `uv` is installed. The checkout is on this branch: `git pull --no-rebase`,
  then `make setup`.

### 1. The Kibana stack

```bash
ES_LOCAL_VERSION=9.5.5 scripts/stack.sh up && scripts/stack.sh seed
uv run python scripts/experiment/local_run.py install-sample
```

Seed after every start of the stack. The seed loads only the flights sample
data, so `install-sample` installs web logs once. It checks
`GET /api/sample_data` first and does nothing when both are there. Runs
never reinstall them. The checker reads the stack's Elasticsearch as the
`elastic` user (from the stack's `.env`); that password reaches no model,
server or trace.

### 2. LM Studio settings

These follow [docs/e2e-setup.md](../../docs/e2e-setup.md), steps 3 and 4.

1. Developer > Settings: turn on **Require Authentication**, then **Allow
   calling servers from mcp.json**.
2. **Manage Tokens** > **Create Token** with the **Use MCP Servers**
   permission. Put it in `.env.local` as `LMSTUDIO_API_TOKEN=...` (the file
   is yours, and the harness only reads it), or export it in the shell.
3. With `lmstudio.load: harness` the JIT setting does not matter: the
   harness loads each model at its maximum context and unloads the previous
   study model, never any other model. With `jit`, leave JIT loading and
   Auto-Evict on: the request loads the model, and Auto-Evict keeps one JIT
   model in memory. Keep the default context length at its maximum
   (`defaultContextLength: {"type": "max"}`).
4. Check what is resident with `lms ps`. Unload any study model loaded with
   a small context, and anything the study does not need, with
   `lms unload <identifier>`. Never use `lms unload --all`.

### 3. The mcp.json server entry

Add this entry by hand to the `mcpServers` object of `~/.lmstudio/mcp.json`,
beside the existing entries. No script writes this file.

```json
"mcp-for-kibana-study": {
  "command": "uv",
  "args": ["--directory", "/ABSOLUTE/PATH/TO/mcp-for-kibana", "run", "mcp-for-kibana"],
  "env": {
    "KIBANA_MCP_ENV_FILE": "/ABSOLUTE/PATH/TO/mcp-for-kibana/elastic-start-local/.env.seed",
    "KIBANA_MCP_TOOLBOXES": "dashboards,data-management,alerting",
    "KIBANA_MCP_TIER": "write"
  }
}
```

`KIBANA_MCP_ENV_FILE` reads the URL and key from the seed file, so a re-minted
key needs no edit. An entry with literal `KIBANA_URL` and `KIBANA_API_KEY`
also works, and preflight checks that both match the seed. Leave
`KIBANA_MCP_OTEL_ENABLED` off, as in the Claude study.

### 4. The config

`local_study.json` holds the settled values of the table above. Any change
before the pilot is the owner's, committed before the runs.

### 5. The Honeycomb key

```bash
export HONEYCOMB_API_KEY=...   # an ingest key for the 'test' environment (EU)
```

The key stays in the shell and is never written to disk; a
`HONEYCOMB_API_KEY=` line in `.env.local` (yours) works too, and is read on
each send. The dataset `mcp-for-kibana-local` is created by the first send.

With `honeycomb.required: false` runs start without the key; each run's
spans stay in its `spans.json` and its record says `otel: skipped`. Once the
key is there, send them:

```bash
uv run python scripts/experiment/local_run.py send-pending
```

### 6. Preflight

```bash
uv run python scripts/experiment/local_run.py preflight --load
```

Each check prints `PASS`, `WARN` or `FAIL`, and each failure names its fix.
The checks cover: the config (tasks known, pass mark within the runs), the
seed, Kibana up at 9.5.5, the key, web logs and flights installed, no
leftover `LOC-*` rules, the checker's Elasticsearch, LM Studio reachable, the
token, each
model present and its context length, the mcp.json entry (toolboxes, tier,
URL, key), the entry starting and listing 25 tools, Honeycomb accepting the
key, and the output directory. `--load` also loads every model once through
the integration with a one-word prompt, which catches memory failures and a
missing mcp.json permission. Continue only when the last line reads
`preflight: PASS`.

### 7. Pilot

```bash
uv run python scripts/experiment/local_run.py pilot
uv run python scripts/experiment/local_run.py status
```

The pilot runs the web-logs task 3 times per model (9 runs). Then check the
harness against the real runtime, before stage 1:

1. `status` prints no `WARN ... no chat.end stream event`. If it does, read
   one run's `events.jsonl` to see the real event names. Either set
   `"stream": false` in `local_study.json`, or fix the names in
   `local_run.py` and the fake. Then delete `runs/study5/pilot/` and rerun
   the pilot.
2. In a run's `record.json`, `tool_names` holds plain tool names
   (`describe_data_view`, …), and `input_tokens`, `output_tokens` and
   `ttft_s` are filled in.
3. Read the `criteria` of every run, with its `artifacts.json`. A check that
   rejects a correct result is a harness bug: fix it on `doc/study` (the
   shared checks), test it with `study_tasks.py replay`, and write the fix
   down for the study board.
4. In Honeycomb (dataset `mcp-for-kibana-local`), count the `invoke_agent`
   spans: one per run, `gen_ai.conversation.id` equal to the run id, and the
   `study.check.*` attributes present.
5. Read the `failure_reason` of every failed run. Invalid runs (`INVALID` in
   the output) name the environment problem: fix it and rerun `pilot`, which
   retries only those cells.

Then freeze the harness: commit, and run stages 1 and 2 at that commit (the
records carry it as `git_sha`).

### 8. Stage 1

```bash
caffeinate -dims uv run python scripts/experiment/local_run.py stage1 2>&1 | tee -a scripts/experiment/runs/study5/run.log
```

`status` estimates stage 1 from the pilot before it starts (60 runs: 3
models × 20). `caffeinate -dims` keeps the Mac and its display awake while
the command runs. Progress is in `runs/study5/progress-stage1.json` and in
`status`, which prints each model's stage-1 successes against the pass mark.

The run resumes. After a crash, a sleep, Ctrl-C or a reboot, start the same
command again. Finished cells are skipped. A run cut off mid-way leaves a
directory without `record.json`. The next start resets Kibana to that run's
`before.json`, marks the directory `abandoned.json` and runs the cell again. A
lock file stops a second runner from starting.

### 9. Stage 2

```bash
caffeinate -dims uv run python scripts/experiment/local_run.py stage2 2>&1 | tee -a scripts/experiment/runs/study5/run.log
```

Stage 2 refuses to start until stage 1 is complete, then runs the change,
lookup and flights tasks 20 times each for the models that passed (up to 180
runs). It prints the models it runs; with none, it does nothing. It resumes
like stage 1.

If Docker restarted, run `scripts/stack.sh up && scripts/stack.sh seed`, then
`preflight`, before you resume.

### 10. Commit the records in chunks

Commit every few hours and at the end. Each run directory is under 1 MB:
streamed deltas are merged before they are written, and `runs.jsonl` holds
one short line per run.

```bash
git add scripts/experiment/runs/study5
git commit -m "chore(experiment): record study-5 runs through stage-1 b03"
git pull --no-rebase && git push
```

Never pass `--no-verify`. If the large-file hook rejects a file, stop and look
at that run: a file over 1 MB means a runaway model output, which is a finding.

### 11. Analyse, and cross-check with Honeycomb

```bash
uv run python scripts/experiment/local_analyze.py                  # stage 1
uv run python scripts/experiment/local_analyze.py --phase stage2
```

Each writes `runs/study5/<phase>/analysis.json`. Per model × task it holds
the pass rate, Wilson 95 % interval, pass^k, the first failing step, the
failing checks, and medians and IQR of time, tokens and tool calls, per run
and per task step. For stage 1 it holds each model's verdict against the pass
mark. Under `consistency`, every metric re-derived from the stored responses
must equal its `record.json` value. Commit `analysis.json`.

The Honeycomb cross-check follows the Claude study's method:

```bash
uv run python scripts/experiment/local_analyze.py queries       # the query specs
# run each with the Honeycomb MCP run_query on dataset mcp-for-kibana-local,
# with a time range that covers the runs; then, per query:
uv run python scripts/experiment/local_analyze.py capture q01_runs <run_pk> '<query_result_json url>'
uv run python scripts/experiment/local_analyze.py capture q02_sums <run_pk> '<url>'
uv run python scripts/experiment/local_analyze.py crosscheck
```

Pass `--phase stage2` to each for stage 2. `crosscheck` writes
`runs/study5/<phase>/honeycomb_crosscheck.json` and exits
non-zero if any value differs. Commit the captured results and the
cross-check.

### 12. The study board

Write one entry for the local-model part. Take every number from
`analysis.json` or `honeycomb_crosscheck.json`, never by hand:

- Dates of the runs, Kibana version, LM Studio version (`lms version`), and
  the commit of `local_study.json`.
- The models, each with its loaded context length (`context_length` in the
  runs), and N per cell.
- Invalid runs: how many, and why.
- Stage 1: each model's successes against the pass mark, with the Wilson
  interval; which models went on to stage 2.
- Per model and task: the pass rate with its Wilson interval, median seconds
  per run, steps reached.
- The checks that failed most, per task (`failed_checks`).
- Any change to the harness or the checks after the pilot, and why.
- The Honeycomb dataset and the cross-check summary.

## Signals

Each run is one trace in `mcp-for-kibana-local`. It uses the names of
[SIGNALS.md](SIGNALS.md) wherever the signal exists for an LM Studio run:

```text
invoke_agent lmstudio server     run totals, outcome, study.*
├── chat <model>                 one per inferred model generation
└── execute_tool <tool>          one per tool call, including calls LM Studio rejected
```

- `gen_ai.conversation.id` is the run id on every span. `gen_ai.provider.name`
  is `lmstudio`.
- LM Studio reports token usage per run only, so `gen_ai.usage.input_tokens`,
  `output_tokens` and `reasoning.output_tokens` sit on `invoke_agent`, and so
  do `agent.usage.*`.
- The `agent.*` signals that apply: `agent.task.success`,
  `agent.run.end_reason`, `agent.model.calls` (inferred: one generation at the
  start and one after each tool batch it continued from),
  `agent.tool.calls`/`calls_mcp`/`distinct_tools`/`failures`/`failure_rate`/`failures_<class>`,
  `agent.time.wall_ms`/`ttft_ms`/`tool_ms`/`model_ms`/`first_tool_ms`,
  `agent.permission.denials`, `agent.harness.result_events`,
  `agent.secret.*`, and per call `agent.tool.failed`/`error_class`/`error_source`/`duration_ms`/`duplicate_of`.
- New for local runs: `agent.tool.invalid_calls` and
  `agent.tool.rejected_by_runtime` (calls LM Studio's own schema check
  rejected; they never reached the server), `agent.model.tokens_per_second`,
  `agent.model.load_ms`, and `agent.harness.timed_out`.
- `study.*`: `model`, `arm` (`server`), `phase`, `block`, `task`,
  `steps_total`, `run_id`, `success_strict`, `steps_passed`,
  `first_failed_step`, `failure_reason`, `valid`, `invalid_reason`,
  `attempt`, `kibana_version`, `toolboxes`, `server_entry`, `context_length`,
  `runner_commit`, `checks_total`, `checks_passed`, and per check
  `study.check.<step>.<check>.ok`, `.expected` and `.actual`.

A tool call counts as failed when its result is not JSON. The server answers
every successful call with JSON and returns errors as plain-text guidance.
The error class uses the Claude study's rules.

Before each send, the payload is redacted: the Kibana key, the LM Studio
token, the Honeycomb key, then generic credential patterns. It is then
scanned, and the send is refused if any secret or pattern remains. The record
says `otel: refused: ...` and the run continues. The files on disk carry
`[REDACTED_KNOWN_SECRET]` in place of any known secret, and
`known_secret_exposures` counts where the key appeared.

Not measured: tokens and stop reasons per model call (LM Studio reports run
totals), and the exact boundaries of model generations. Seconds per step and
tokens per step are run totals divided by the task's steps or the tool calls.
