# Live-telemetry rerun of the efficiency study

The first study (180 `claude -p` runs, STUDY_DESIGN.md) built its signals from
transcripts after the runs. The rerun gets its signals at run time from the
components themselves, and keeps the transcript pipeline
(`efficiency_analyze.py`, `efficiency_honeycomb.py`) as the second,
independent computation of every number.

Everything here is opt-in: `efficiency_run.py` without `--live-telemetry`
behaves exactly as in the first study.

## What is emitted, by whom, and where it lands

All producers export OTLP/HTTP to a loopback **relay** the runner starts per
run (`live_telemetry.Relay`). The relay scrubs the run's Kibana key and the
Honeycomb key out of every string, drops Claude Code's account identity
attributes (`user.email`, `user.account_uuid`, `user.account_id`,
`organization.id`; keep them with `--keep-identity`), adds the GenAI
semantic-convention names Honeycomb's Agent Timeline needs, keeps a copy
(`telemetry.jsonl` in the run directory, readable with
`otlp_sink.py summary`) and forwards to Honeycomb. The Honeycomb key stays in
the runner: Claude Code, the model's shell, the hook and the server never get
it.

| Component | Signal | How it is produced | Lands in |
|---|---|---|---|
| Claude Code | spans `claude_code.interaction` > `claude_code.llm_request`, `claude_code.tool` > `claude_code.tool.blocked_on_user`, `claude_code.tool.execution`; span events `gen_ai.request.attempt`, `tool.output` | `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`, `OTEL_TRACES_EXPORTER=otlp` | `--dataset` (traces) |
| Claude Code | log events `user_prompt` (prompt text), `assistant_response` (response text), `api_request` (tokens, cost per call), `tool_decision`, `tool_result` (tool input), `mcp_server_connection`, `plugin_loaded`, `managed_settings_resolved` | `OTEL_LOGS_EXPORTER=otlp`, `OTEL_LOG_USER_PROMPTS=1`, `OTEL_LOG_ASSISTANT_RESPONSES=1`, `OTEL_LOG_TOOL_DETAILS=1`, `OTEL_LOG_TOOL_CONTENT=1`; `OTEL_LOG_RAW_API_BODIES` is never set | `--dataset` (logs) |
| Claude Code | metrics `claude_code.session.count`, `cost.usage`, `token.usage`, `active_time.total` | off by default; `--with-metrics` | the environment's shared `metrics` dataset, whatever the dataset header says |
| MCP server | FastMCP spans per request (`tools/call <tool>`, `tools/list`, ...; `exception` events with `exception.type`/`exception.message` on failures) | `KIBANA_MCP_OTEL_ENABLED`, `KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV` (new, default off) in the server's MCP config env | `--dataset` (traces), parented under the runner's root span |
| Agent-signals hook | span `agent.tool <tool>` per tool call with the `agent.*` signals; span `agent.turn_end` at the end of the turn | `agent_signals_hook.py`, a silent command hook on PostToolUse, PostToolUseFailure, PermissionDenied and Stop, passed with `--settings` | `--dataset` (traces), child of `claude_code.interaction` |
| Relay | `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.provider.name`, `gen_ai.usage.*`, `gen_ai.tool.name` added to Claude Code's spans; `agent.secret.*` on `assistant_response` records | `live_telemetry.enrich`, `mark_model_text_secrets` (before scrubbing) | with the record it enriches |
| Runner | root span `efficiency.run` (`study.*` dimensions and outcome, server revision, the hook's per-run `agent.*` rollups) and children `efficiency.agent`, `efficiency.score`, `efficiency.reset` | `live_telemetry.runner_spans`, sent at run end | `--dataset` (traces), trace root |

Every span, log record and metric point carries `study.run_id`,
`study.model`, `study.arm`, `study.block`, `study.component` and
`gen_ai.conversation.id` (= the run id). `service.name` is the dataset name for
every component, so a run lives in one dataset.

## Smoke-test findings (2026-10-11, Claude Code 2.1.296, claude-haiku-5-5)

Local sink first (`otlp_sink.py serve`), then Honeycomb EU, environment `test`,
dataset `mcp-for-kibana-smoke-live`.

**(a) Flush.** A 9.5 s `-p` run delivered traces, logs and metrics before it
exited. With the default intervals (metrics 60 s, logs and traces 5 s) a 6.9 s
run still delivered all three: Claude Code flushes on exit. The runner uses
5 s / 1 s / 1 s anyway. The server's batch exporter is set to 500 ms and the
runner waits 1 s before stopping the relay; the relay drains its queue on stop.

**(b) Routing.** `OTEL_SERVICE_NAME` replaces `service.name` (`claude-code` by
default) on spans, logs and metrics. In Honeycomb everything from both live
smoke runs is in `mcp-for-kibana-smoke-live`; the `claude-code` dataset's
newest event (21:03Z) predates them. Exception: OTLP **metrics** went to the
environment's shared `metrics` dataset (Metrics 2.0) despite
`x-honeycomb-dataset`; hence metrics are off unless `--with-metrics`. Every
metric value also exists per call on `api_request` events and
`claude_code.llm_request` spans.

**(c) Resource attributes.** `OTEL_RESOURCE_ATTRIBUTES` lands in the resource
block of all three signals, and Claude Code also copies it onto every span,
log record and metric point. Claude Code passes no `OTEL_*` variable to MCP
servers, hooks or Bash, but the MCP config's own `env` block reaches the
server, so the server gets its own `OTEL_RESOURCE_ATTRIBUTES` there.

**(d) Parenting.** With `TRACEPARENT` set, `claude_code.interaction` is a
child of that span (`parent.source=env`), and log records carry the trace id.

**(e) The stdio server.** It inherits the claude process's `TRACEPARENT` (the
runner's root, not the tool span); the `tools/call` request `_meta` has no
`traceparent` (only `progressToken`, client info and
`claudecode/toolUseId`). Without a change the server's spans start their own
traces. The smallest change, now in the server (default off):
`KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV=1` attaches the environment's
`TRACEPARENT` as the current context before the event loop starts, so every
FastMCP span joins the run's trace under the root (verified locally and in
Honeycomb). The server's spans already carry what only the server knows:
`create_dashboard` failures arrive as `exception.type`
`fastmcp.exceptions.ValidationError` (schema) or `ToolError` (the server's own
check, e.g. "time_field '@timestamp' does not match the data view's time field
'timestamp'"), with the full message. No further server change was needed.

**(f) Content.** Prompt text: `user_prompt` log (`prompt`, `prompt_text`) and
the `claude_code.interaction` span (`user_prompt`). Response text:
`assistant_response` log (`response`), one per model reply. Tool input:
`tool_result` log (`tool_input`, `tool_parameters`) and the
`claude_code.tool` span (`full_command`, `file_path`). Tool output: the
`tool.output` span event (`output`, `bash_command`) for Bash and Read only, on
success only; MCP tool output is not emitted (the hook sees it). No
`api_request_body`/`api_response_body` events appear.

**(g) `gen_ai.conversation.id`.** Claude Code emits none, and its `session.id`
is the parent session's id, shared by every run launched from it (all 180
first-study transcripts carry the same `session_id`). Set through
`OTEL_RESOURCE_ATTRIBUTES` it lands as a field on every event. The Agent
Timeline still listed **no** conversation for the first live run; after the
relay added `gen_ai.operation.name` (`invoke_agent`/`chat`/`execute_tool`)
`list_aiconversations` shows the run (9 events, 1 error, agent
`claude-code with-mcp`).

**(h) Secrets.** Bash subprocesses inherit the whole environment of the claude
process, `TRACEPARENT` and `HONEYCOMB_API_KEY` included (the first study's
runner passed `HONEYCOMB_API_KEY` to the model's environment in both arms; the
current key does not appear in any committed transcript). Live mode strips
`HONEYCOMB_*`, `OTEL_*` and ambient `TRACEPARENT` from the child environment.
With tool content on, anything the model prints (an `env` dump) would reach
the telemetry: the relay replaced the Kibana key once in the 15-call study-task
smoke run (`secrets_redacted: 1`). Every event also carries the signed-in
account (`user.email`, account and organization ids); the relay dropped 656
such attributes in that run. `user.id` (a hash) and `session.id` stay.

**Hooks.** Claude Code gives a hook the `TRACEPARENT` of `claude_code.interaction`
when tracing is on (of the inbound context when it is off), the payload's
`duration_ms`, the tool response (Bash: `stdout`/`stderr`/`interrupted`; MCP:
content blocks) and, on PostToolUseFailure, `error`. A silent hook (exit 0, no
output) adds `hook_started`/`hook_response` system events to the stream-json
transcript (empty `stdout`) and nothing to the model's context; `--restricted`
still applies `--settings`. Hook latency: 45–54 ms wall per call measured
offline (interpreter start with `-I -S` ~12 ms, imports, classification ~12–16
ms, loopback POST), so roughly 0.1 s per tool call on a run's critical path.

Smoke runs: `20261011T212909Z-claude-haiku-5-5-with-mcp-4a52c9` (study task,
trace `99ac617e7bc99f25bbbdd1910ae95119`, PASS, 15 tool calls, 47 requests
forwarded, no hook) and `20261011T213643Z-smoke-04334c` (`live_smoke.py`
probe, trace `0236ecd391ebaba5f966c76fb71072e9`, all checks PASS: the curl
404 arrived with `agent.tool.hidden_failure=true`, `http_status=404`,
`error_class=http_4xx`; the invalid `create_dashboard` with
`first_attempt_failed=true`, `error_class=validation`, next to the server's
`ValidationError` span). Both ran against a working tree with uncommitted
server changes (`study.server_dirty=true`). The metrics of the second run were
sent twice (a manual replay while finding where metrics land).

## The agent.* signals: who produces each one live

Producers: **A** Claude Code's own telemetry, **B** the hook, **C** the MCP
server, **D** the runner's root span (rollups, outcome), **E** a Honeycomb
derived column, **R** the relay (enrichment of A's records), **—** not live
(the transcript pipeline only).

| Signal (SIGNALS.md) | Producer | Field |
|---|---|---|
| `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.provider.name` | R | added to A's spans |
| `gen_ai.conversation.id` | A | resource attribute (set by the runner) |
| `gen_ai.request.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons` | A | `claude_code.llm_request` |
| `gen_ai.usage.input/output/cache_*_tokens` per call | A (+R names) | `llm_request` `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`; `api_request` log |
| `gen_ai.response.time_to_first_chunk` | A | `llm_request` `ttft_ms` |
| `gen_ai.input.messages` / `output.messages` | A | `user_prompt`, `assistant_response` text; tool input on `tool_result`; Bash/Read output on `tool.output` |
| `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type` | A, B | `claude_code.tool` `tool_name`, `gen_ai.tool.call.id`; the hook span repeats them (join key) |
| `error.type` | B, C | hook span (error class); server span (exception class) |
| `agent.task.success`, `study.failure_reason` | D | `study.success`, `study.failure_reason` |
| `study.success_lenient` | E | `OR($study.success, EQUALS($study.failure_reason, "rule disabled"))` |
| `agent.run.end_reason`, `stop_reason`, `is_error`, `api_error_status`, `safety_stops` | D (end_reason, is_error recorded; others not yet on the span), A (`stop_reason` per call) | first result event |
| `agent.model.calls` | A | COUNT of `claude_code.llm_request` per trace (query, not a column) |
| `agent.harness.turns`, `agent.cost.usd`, `agent.usage.*` run totals, `agent.time.wall_ms`, `model_ms` | D | `study.num_turns`, `total_cost_usd`, token fields, `duration_ms`, `duration_api_ms` |
| `agent.usage.cache_read_share` | E | from D's token fields |
| `agent.usage.input_tokens_per_call` | — | needs the model-call count (a query across spans) |
| `agent.model.input_tokens`, `cache_read_tokens`, `new_input_tokens`, `duration_ms` | A | `llm_request` |
| `agent.model.tool_calls`, `agent.model.thinking`, per-call reasoning tokens | — | not in Claude Code's telemetry |
| `agent.tool.calls` (+ `_mcp`, `_shell`, `_file`, `_other`), `distinct_tools`, `agent.usage.tool_result_bytes`, `agent.time.tool_ms`, `tool_busy_ms`, `wait_ms` | D (from B) | hook rollups on the root span |
| `agent.tool.duration_ms`, `result_bytes`, `category`, `target` | B (+A `duration_ms`, `result_tokens`) | hook span |
| `agent.tool.error`, `failed`, `hidden_failure`, `error_class`, `error_source`, `http_status` | B | hook span |
| `agent.tool.first_attempt`, `first_attempt_failed`, `retry_of`, `retry`, `duplicate_of` | B | hook span (depend only on earlier calls) |
| `agent.tool.recovered` | B (as `agent.tool.recovers` on the later call), D (counts) | an event cannot be updated once sent |
| run counts: `errors`, `failures`, `failure_rate`, `hidden_failures`, `failures_<class>`, `first_attempt_failures`, `retries`, `failures_recovered`, `failures_unrecovered`, `duplicate_calls`, `max_identical_calls` | D (from B) | root span |
| `agent.secret.*` on tool calls (`exposed`, `known_exposed`, `kind`, `location`, `count`) | B | hook span (arguments and result) |
| `agent.secret.*` in model text | R, B | `assistant_response` records (every reply); `agent.turn_end` (final reply) |
| `agent.secret.*` run counts | D (from B) | root span (tool locations; model-text count from R by query) |
| `agent.tool.env_enumeration`, `shell_http`, `deliberate_wait`, `outside_workdir` (+ paths), `backgrounded`, `timed_out`, `permission_denied` | B | hook span |
| `agent.fs.*`, `env_enumeration_calls`, `shell_http_calls`, `wait_calls`, `permission.denials`, `harness.backgrounded_calls`, `timed_out_calls` | D (from B) | root span; `study.n_permission_denials` from the stream |
| `agent.time.first_tool_ms`, `ttft_ms`, `other_ms`, shares | E / — | shares are E from D's fields; `first_tool_ms`: A's first `claude_code.tool` start minus the root start (query) |
| `agent.harness.result_events` | D | `study.n_result_events` |
| `agent.harness.unanswered_calls`, `background_tasks_ended_abnormally`, `rate_limited_events` | — | stream-only events; not wired |
| `agent.harness.version` | A | `service.version` |
| `study.rule_enablement` | — | transcript pipeline |
| `study.run_id`, `block`, `model`, `arm`, `kibana_version`, `claude_code_version`, `runner_commit` | A (resource), D | resource attributes; `study.claude_cli`, `study.server_rev` on the root |

Counted by row: A 12 rows, B 9, C 1 (`error.type` detail; the server adds no
`agent.*` signal and needs none), D 10, E 3, R 2, not live 5 (per-call
`tool_calls`/thinking/reasoning tokens, `input_tokens_per_call`,
`first_tool_ms` as a column, three stream-only harness counters,
`rule_enablement`).

How the hook works: on every PostToolUse/PostToolUseFailure/PermissionDenied
it appends the call to a per-run state file (under the run's temporary
directory, keyed by the runner's config, not by `session_id`, which every run
shares), re-runs `efficiency_analyze.annotate` over the calls so far and sends
the last call's signals. The live verdict and the transcript verdict therefore
come from the same function. Two known differences: calls are ordered by
completion, not by `tool_use` order (parallel calls), and model text is
scanned per reply by the relay rather than per message.

## Command sequence for the fresh session

```bash
make setup
ES_LOCAL_VERSION=9.5.5 scripts/stack.sh up && scripts/stack.sh seed
export HONEYCOMB_API_KEY=...            # never echo it

# 1. Smoke: one probe run, all checks must PASS (dataset: a new one per attempt)
uv run python scripts/experiment/live_smoke.py --dataset mcp-for-kibana-live-smoke

# 2. Pilot: one run per model and arm (6 runs)
for m in claude-haiku-5-5 claude-sonnet-5-5 claude-opus-5-5; do
  for a in with-mcp no-mcp; do
    uv run python scripts/experiment/efficiency_run.py --live-telemetry \
      --dataset mcp-for-kibana-live --model $m --arm $a --runs 1 --block pilot \
      --server-label <R3 choice> [--key-out-of-model-env]
  done
done

# 3. Full: 30 blocks of 6 (3 models x 2 arms), order within a block as in the
#    first study (STUDY_DESIGN.md), same flags, --block b01 ... b30. Output:
#    scripts/experiment/runs/efficiency/live/<block>/<run_id>/
#    (record.json, transcript.jsonl, telemetry.jsonl, artifacts.json)

# 4. Cross-check: the transcript pipeline on the same runs, then compare
uv run python scripts/experiment/efficiency_analyze.py --root scripts/experiment/runs/efficiency/live
#    and compare its per-run agent.* values with the root spans' rollups and
#    the hook spans in Honeycomb (efficiency_crosscheck.py is the pattern).
```

## Decisions for the owner

- **R3, server build.** Run the server as in study 1, or after the
  `create_dashboard` first-call fix. Either way the root span records
  `study.server_rev`, `study.server_dirty` and `--server-label`.
- **R4, key in the model's environment.** `--key-out-of-model-env` leaves
  `KIBANA_TEST_API_KEY` out of the with-mcp arm's environment (the server
  still gets it). The prompt stays identical and still names the variable, so
  a with-mcp run that reaches for curl finds it empty.
- **Prompt.** Live mode defaults to prompt version 2 (the rule must be
  enabled); `--prompt-version 1` keeps study 1's wording.
- **Identity.** Dropped by default; `--keep-identity` keeps the account
  attributes Claude Code stamps on every event.
- **Metrics.** Off by default because they land in the shared `metrics`
  dataset; `--with-metrics` turns them on.
- **Smoke datasets to delete** (the API key cannot): `mcp-for-kibana-smoke-live`.
  The smoke metrics are in the shared `metrics` dataset, `service.name` =
  `mcp-for-kibana-smoke-live`.
