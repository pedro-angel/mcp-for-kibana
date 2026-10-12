# Agent observability signals

This catalogue defines the signals the efficiency study derives for every agent
run. It has three tiers:

1. **OpenTelemetry GenAI semantic conventions** (semconv 1.41). They are used
   wherever a standard attribute exists.
2. **`agent.*`**: generic agent-health signals. They are not specific to this
   study and apply to any tool-using agent. They are grouped by the question an
   operator asks.
3. **`study.*`**: the dimensions of this experiment (model, arm, block, verdict).

`efficiency_analyze.py` derives every signal from the run transcripts (`claude
-p` stream-json) and writes it to `runs/efficiency/full/analysis.json`.
`efficiency_honeycomb.py` sends it to the Honeycomb dataset `mcp-for-kibana-study`.
Run-level values come from `analysis.json`, and call-level values come from the
same functions, so the report and the traces show the same numbers.

## Trace shape

```text
invoke_agent claude-code <arm>      one per run: outcome, totals, safety, study.*
├── chat <model>                    one per model call (one assistant message id)
└── execute_tool <tool>             one per tool call, timed tool_use -> tool_result
```

Every span carries `gen_ai.conversation.id` (= the run id) and `study.model`,
`study.arm`, `study.block` and `study.run_id`, so any span can be grouped by run
or by experimental cell without a join. A signal sits on the span where the
thing happened: a secret found in a tool result marks that `execute_tool` span,
and a secret in the model's own text marks that `chat` span. The run totals sit
on `invoke_agent`.

The scope is every transcript event up to the **first** `result` event, which
marks the model's own end of turn. Events after it come from harness clean-up,
such as a killed background task re-entering the loop. They are reported as
anomalies (`agent.harness.result_events`) and not as agent work.

**Levels:** R = run (`invoke_agent`), M = model call (`chat`), T = tool call
(`execute_tool`).

## Tier 1: OpenTelemetry GenAI semantic conventions

| Attribute | Level | Value here |
|---|---|---|
| `gen_ai.operation.name` | R M T | `invoke_agent` / `chat` / `execute_tool` |
| `gen_ai.conversation.id` | R M T | run id |
| `gen_ai.agent.name`, `gen_ai.agent.id` | R M T | `claude-code <arm>`, `claude-code/<arm>` |
| `gen_ai.agent.version` | R | Claude Code version from the `init` event |
| `gen_ai.provider.name` | R M T | `anthropic` |
| `gen_ai.request.model` | R M T | requested model |
| `gen_ai.response.model`, `gen_ai.response.id` | M | model and message id of the reply |
| `gen_ai.usage.input_tokens` | M | input tokens of the call, **including** cache reads and writes (semconv: SHOULD include cached) |
| `gen_ai.usage.cache_read.input_tokens` | M | input served from the prompt cache |
| `gen_ai.usage.cache_creation.input_tokens` | M | input written to the prompt cache |
| `gen_ai.usage.output_tokens` | R | run total (per-call output tokens are a gap; see below) |
| `gen_ai.usage.reasoning.output_tokens` | R | thinking tokens, run total |
| `gen_ai.response.finish_reasons` | M | `tool_use` if the reply calls a tool, else `end_turn` (inferred, because the stream's `stop_reason` is null) |
| `gen_ai.response.time_to_first_chunk` | M | first call only, from the result event's `ttft_ms` (seconds) |
| `gen_ai.input.messages` | M | only the messages **new** to this call (the prompt, or the tool results since the last call), redacted, at most 16 KB |
| `gen_ai.output.messages` | M | the reply: text, tool calls and a thinking marker (thinking content is encrypted), redacted, at most 16 KB |
| `gen_ai.tool.name`, `gen_ai.tool.call.id` | T | tool name and call id |
| `gen_ai.tool.type` | T | `extension` (MCP) or `function` (built-in) |
| `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` | T | redacted, at most 8 KB |
| `error.type` | R T | `task_failed` on a failed run; the error class on a failed tool call |

Each token count is recorded on exactly one span level: input-side on `chat`,
output on `invoke_agent`. A `SUM` over all spans of a run therefore equals the
run total and does not double-count. The `agent.usage.*` attributes repeat the
run totals of every kind on `invoke_agent` for per-run queries.

## Tier 2: `agent.*` generic agent-health signals

### Outcome: did it finish, did it succeed, how did it end?

| Signal | Lvl | Type | Definition | Why an operator cares | Derived here |
|---|---|---|---|---|---|
| `agent.task.success` | R | bool | An independent check confirms the goal state | The only outcome that matters. "The agent said done" is not success | `study.success_strict` (the runner's post-run Kibana check) |
| `agent.run.end_reason` | R | string | How the run ended (`completed`, `max_turns`, `error`, …) | Separates task failure from harness failure | `terminal_reason` of the first result |
| `agent.run.stop_reason` | R | string | The model's last stop reason | `max_tokens` or `refusal` endings need different fixes | `stop_reason` of the first result |
| `agent.run.is_error` | R | bool | The harness reports the run as errored | Pages someone. These are not task failures | `is_error` |
| `agent.run.api_error_status` | R | int | Provider API error that ended the run | Provider outage versus agent bug | `api_error_status` |
| `agent.run.safety_stops` | R | int | Turns stopped by a provider safety system | Prompts or content that trip classifiers | `safety_stops` |

### Effort and cost: how much work, and what did it cost?

| Signal | Lvl | Type | Definition | Why | Derived here |
|---|---|---|---|---|---|
| `agent.model.calls` | R | int | Model calls (distinct responses) | The main cost driver. Cost ≈ calls × tokens per call | assistant message ids |
| `agent.harness.turns` | R | int | The harness's own turn count | Cross-check on `agent.model.calls` | `num_turns` |
| `agent.tool.calls` (`_mcp`, `_shell`, `_file`, `_other`) | R | int | Tool calls, total and by category | Shows how the agent acts: through curated tools or the shell | tool_use blocks |
| `agent.tool.distinct_tools` | R | int | Distinct tools used | Breadth of the toolset actually exercised | |
| `agent.usage.input_tokens` | R | int | All input tokens, cache included | Context volume processed | first result `usage` |
| `agent.usage.output_tokens` / `reasoning_tokens` | R | int | Output and thinking tokens | Output is the expensive side | `usage` |
| `agent.usage.cache_read_tokens` / `cache_creation_tokens` / `uncached_input_tokens` | R | int | Input split by cache treatment | A cache write costs 12.5× a cache read; uncached input 10× | `usage` |
| `agent.usage.cache_read_share` | R | double | cache read ÷ input | A falling share means the prompt prefix is unstable | |
| `agent.usage.input_tokens_per_call` | R | double | input ÷ model calls | Separates "more calls" from "fatter calls" | |
| `agent.usage.tool_result_bytes` | R | int | Bytes of tool output fed back to the model | Context bloat: verbose tools grow every later call | tool_result text |
| `agent.cost.usd` | R | double | Run cost at list price | The budget line. GenAI semconv has no cost attribute | `total_cost_usd` |
| `agent.model.input_tokens`, `cache_read_tokens`, `new_input_tokens` | M | int | Per-call input, cached part, and new part (uncached + cache write) | Finds the call that blew up the context | message `usage` |
| `agent.model.tool_calls` | M | int | Tool calls requested by this response | More than 1 means parallel tool use | |
| `agent.model.thinking` | M | bool | The response contains a thinking block | Reasoning effort per step | |
| `agent.tool.result_bytes` | T | int | Size of this tool's output | Finds the verbose tool | |

### Time: where does the wall time go?

| Signal | Lvl | Type | Definition | Why | Derived here |
|---|---|---|---|---|---|
| `agent.time.wall_ms` | R | double | Run duration as the harness measures it | User-facing latency | `duration_ms` |
| `agent.time.model_ms` | R | double | Time inside model API calls | Usually dominant. Fix it with fewer or smaller calls | `duration_api_ms` |
| `agent.time.tool_ms` | R | double | Union of tool-call intervals (parallel calls are not double-counted) | Slow tools or slow backends | tool spans |
| `agent.time.tool_busy_ms` | R | double | `tool_ms` without deliberate waits and timed-out calls | Real tool execution cost | |
| `agent.time.wait_ms` | R | double | Time in calls that deliberately wait (`sleep N`, `watch`, `wait`) | Polling loops look like slow tools but are agent choices | shell command text |
| `agent.time.other_ms` | R | double | wall − model − tool | Harness overhead. Negative values mean streaming overlapped tool start | |
| `agent.time.model_share`, `tool_share` | R | double | Shares of wall time | One number for "what to optimise" | |
| `agent.time.first_tool_ms` | R | double | Run start until the first tool call | Start-up cost (MCP server launch, tool discovery, planning) | from `ts_start` (includes CLI start) |
| `agent.time.ttft_ms` | R | double | Time to first token of the first model call | Perceived responsiveness | `ttft_ms` |
| `agent.model.duration_ms` | M | double | From the call's input to its last streamed block | Slow calls. Approximate (see gaps) | event timestamps |
| `agent.tool.duration_ms` | T | double | tool_use → tool_result | Slow tool calls | event timestamps |

### Reliability: do tool calls work, and does the agent recover?

| Signal | Lvl | Type | Definition | Why | Derived here |
|---|---|---|---|---|---|
| `agent.tool.error` | T | bool | The harness flagged the result as an error (`is_error`) | The signal most dashboards stop at | `is_error` |
| `agent.tool.failed` | T | bool | The call did not achieve its effect: flagged error, **or** an HTTP ≥ 400 visible in a "successful" output, a timeout, a refusal or no result | Shell tools exit 0 on an HTTP 404. Without this, shell agents look healthier than they are | see error classes |
| `agent.tool.hidden_failure` | T | bool | Failed, but the harness reported success | Measures how blind `is_error` is | `error_source = output` |
| `agent.tool.error_class` (also `error.type`) | T | enum | `validation`, `http_4xx`, `http_5xx`, `timeout`, `refused`, `unknown_tool`, `command_failed`, `other` | Each class has a different owner: tool schema, backend, harness, model | rules below |
| `agent.tool.error_source` | T | enum | `harness`, `output`, `timeout`, `no_result` | Where the failure was detected | |
| `agent.tool.http_status` | T | int | HTTP error status seen in the output | Backend health per endpoint | |
| `agent.tool.target` | T | string | What the call acts on: the tool name, or for a shell HTTP call `Bash <METHOD> <path>` (ids folded) | Makes retries and first-attempt failures meaningful for a generic shell tool | |
| `agent.tool.first_attempt` / `first_attempt_failed` | T | bool | First call on this target in the run, and whether it failed | A tool that fails first time and works on retry has a schema or description problem | |
| `agent.tool.retry_of` / `agent.tool.retry` | T | string / bool | Call id of the failed call on the same target that this call follows | Retry chains | |
| `agent.tool.recovered` | T | bool | Set on a failed call: a later call on the same target succeeded | Separates self-healing errors from abandoned paths | |
| `agent.tool.duplicate_of` | T | string | Call id of an earlier call with identical tool and arguments | Loops and stuck agents | canonical JSON of the arguments |
| `agent.tool.errors`, `failures`, `hidden_failures`, `failure_rate` | R | int / double | Run counts and failed ÷ calls | Reliability per run | |
| `agent.tool.failures_<class>` | R | int | Failures per error class | | |
| `agent.tool.first_attempt_failures`, `retries`, `failures_recovered`, `failures_unrecovered` | R | int | As above, per run. `unrecovered` = no later success on that target (an abandoned path, not necessarily a failed task) | | |
| `agent.tool.duplicate_calls`, `max_identical_calls` | R | int | Identical repeats, and the largest repeat group | Loop detector | |

Error classes, in order of precedence:

- `refused`: a permission denial (the stream's `permission_denied` or a result's `permission_denials`), or a harness guard block (`<tool_use_error>Blocked:`).
- `timeout`: the harness moved the call to the background after its time limit (`timedOutAfterMs`/`backgroundTaskId`), or the error text says it timed out.
- `unknown_tool`: the model called a tool that does not exist.
- `validation`: a non-shell tool rejected the arguments (schema or `invalid …`), or an HTTP 400/422 whose body names invalid input.
- `http_4xx` / `http_5xx`: other HTTP error statuses. The status is read from Kibana's and Elasticsearch's compact error bodies or from raw `HTTP/x NNN` status lines.
- `command_failed`: a shell call with a non-zero exit and none of the above.
- `other`: anything else.

### Safety: did secrets leak, was anything denied, did it reach outside its sandbox?

| Signal | Lvl | Type | Definition | Why | Derived here |
|---|---|---|---|---|---|
| `agent.secret.exposed` | R M T | bool | A credential value reached the model's context (tool result), was written by the model (arguments, text), or both | A secret in context can be echoed, logged or exfiltrated | scan below |
| `agent.secret.known_exposed` | R M T | bool | A **registered** secret value (exact match) appeared | No false positives. The strongest signal | the run's Kibana API key, never printed |
| `agent.secret.kind` | M T | string | Kinds found: `known_secret`, `anthropic_key`, `auth_header`, `credential_assignment`, `opaque_token` | Triage, and the fix differs per kind | |
| `agent.secret.location` | M T | string | `tool_result`, `tool_arguments`, `model_text` | Result = the environment leaked it. Arguments or text = the model handled it | |
| `agent.secret.count` | M T | int | Matches on the span | | |
| `agent.secret.exposures` | R | int | Spans with a finding | | |
| `agent.secret.kind_<kind>`, `location_<loc>` | R | int | Matches per kind, spans per location | | |
| `agent.secret.known_locations` | R | string | Locations of the registered secret | | |
| `agent.tool.env_enumeration` / `agent.tool.env_enumeration_calls` | T / R | bool / int | The call dumps the environment wholesale (`env`, `printenv`, `/proc/*/environ`, `export -p`) | Leading indicator of secret exposure, even when the agent tries to redact | shell command text |
| `agent.permission.denials` / `agent.tool.permission_denied` | R / T | int / bool | Calls the permission system refused | Shows intent beyond policy, and policy friction | denial events ∪ every result's list |
| `agent.fs.outside_workdir_calls`, `outside_workdir_paths`, `outside_workdir_kinds` | R | int / int / string | Calls that name absolute paths outside the working directory and the harness's own dir (kinds `home`, `system`, `temp`) | Sandbox drift: reading the host, other runs' dirs or config | `/home`, `/root`, `/etc`, `/var`, `/opt`, `/proc`, `/srv`, `/mnt`, `/usr/local`, `/tmp` paths in arguments |
| `agent.tool.outside_workdir` (+ `_paths`) | T | bool / string | Same, per call | | |
| `agent.tool.shell_http` / `agent.tool.shell_http_calls` | T / R | bool / int | A shell call that makes HTTP requests itself (`curl`, `wget`, URLs, `urllib`, `requests`) | When curated tools exist, this is a **bypass**. It skips their validation, safety defaults and audit | shell command text |

Secret kinds: `known_secret` is an exact match of a registered value. The
generic kinds are `anthropic_key` (`sk-ant-…`), `auth_header` (an
`Authorization`/`X-Api-Key` header with a literal value of 16 or more
characters), `credential_assignment` (`*_KEY=`, `*TOKEN=`, `*SECRET=`,
`*PASSWORD=` or a JSON `"api_key"`/`"token"`/`"secret"`/`"password"` field with
a literal value of 8 or more characters) and `opaque_token` (a mixed-case
alphanumeric token of 32 or more characters right after a credential word). A
`$VAR` reference never counts. When generic matches overlap, the first kind in
that order wins. The scan covers every tool result, every tool's arguments and
all assistant text.

### Harness and runtime anomalies

| Signal | Lvl | Type | Definition | Why | Derived here |
|---|---|---|---|---|---|
| `agent.harness.result_events` | R | int | Result events in the transcript | More than 1 means the loop re-entered after the agent finished (for example, a killed background task). Naive parsers then read the wrong totals | `result` events |
| `agent.tool.backgrounded` / `agent.harness.backgrounded_calls` | T / R | bool / int | The harness moved the call to the background | Hidden concurrency. Output arrives later or never | `backgroundTaskId` |
| `agent.tool.timed_out` / `agent.harness.timed_out_calls` | T / R | bool / int | The call hit the harness's time limit | Hung commands (for example, waiting on stdin) | `timedOutAfterMs` |
| `agent.harness.background_tasks_ended_abnormally` | R | int | Background tasks that ended `stopped`, `killed` or `failed` | | `task_notification` |
| `agent.harness.unanswered_calls` | R | int | tool_use without a tool_result before the end | Lost work | |
| `agent.harness.rate_limited_events` | R | int | Rate-limit events not `allowed` | Throttling, as distinct from slowness | `rate_limit_event` |
| `agent.harness.version` | R | string | Harness version (also `gen_ai.agent.version`) | Regressions across upgrades | `init` |
| `agent.tool.deliberate_wait` / `agent.tool.wait_calls` | T / R | bool / int | The call sleeps or polls on purpose | Separates polling from slowness | |

## Tier 3: `study.*` experiment dimensions

| Signal | Lvl | Definition |
|---|---|---|
| `study.run_id`, `study.block`, `study.model`, `study.arm` | R M T | Run identity and experimental cell (block = randomisation block of 6 runs) |
| `study.success_strict` | R | Dashboard and rule exist and are correct, rule enabled (`record.json` `success`) |
| `study.success_lenient` | R | Strict, or the only failure reason is "rule disabled" |
| `study.failure_reason` | R | The runner's failure reasons, `;`-separated |
| `study.rule_enablement` | R | How the rule got enabled: `enabled_true_on_create`, `enable_alert_rule_call`, `enabled_other` (no-mcp: the HTTP API's default `enabled: true`), `left_disabled`, `no_rule`. The server creates rules disabled by default, for safety |
| `study.kibana_version`, `study.claude_code_version` | R | Versions under test |
| `study.runner_commit` | R | Commit of the runner that recorded the run |
| `study.analysis_commit` | R | Commit of the `analysis.json` the traces were built from |

## What the study showed with these signals

All values below are from `runs/efficiency/full/analysis.json`, under
`signals.summary`, `signals.pooled` and `signals.comparison` (90 runs per arm, 30
per model × arm). Ratios are with-mcp ÷ no-mcp medians with a bootstrap CI95.

- **Savings come from fewer calls, not cheaper calls.** Model calls per run:
  ratio 0.44 (CI 0.38–0.50); model time 0.55 (0.50–0.58). Input tokens per
  call: pooled 1.13 (1.00–1.25). Per model, Haiku 0.94, Opus 1.13 (0.86–1.28)
  and Sonnet 1.45 (1.41–1.54). Calls get no cheaper, and with Sonnet they get
  heavier (the server's tool schemas sit in every call). Tool output fed back
  to the model is 0.066× (median 2.5 KB vs 38.6 KB per run).
- **Wall time is model time.** The median model share of wall time is 0.81
  (with-mcp) and 0.84 (no-mcp). Busy tool time is about 3–4 s per run in five
  of six cells (median 3.0–4.0 s). Haiku no-mcp is the exception at 6.2 s.
  Long tool time in no-mcp comes from deliberate `sleep` polling (27 runs; 998 s
  in total for Haiku) and 12 calls that hit the 120 s timeout. The MCP arm
  reaches its first tool call about 1.2 s later (`first_tool_ms` ratio 1.38),
  which is the server's start-up cost.
- **The first `create_dashboard` call fails in 90 of 90 with-mcp runs**
  (`validation`: a `date_histogram` without `time_field`, or a time field that
  does not match the data view). Every run then recovers. That is 103 of the
  111 harness-flagged with-mcp tool errors, which points at the tool's schema
  and description, not at the models.
- **`is_error` hides most shell failures.** In no-mcp, 236 of 295 failed
  calls (0.80) exited 0 with an HTTP error in the output. The harness error
  rate is 0.031, but the failure rate is 0.194.
- **Curated tools get bypassed.** 49 of 90 with-mcp runs also called Kibana
  through `curl` (Haiku 30/30, Opus 19/30, Sonnet 0/30): 149 shell HTTP
  calls. 113 of them failed, and 109 of those failures were invisible to
  `is_error`. 117 of the calls went to query endpoints (console proxy,
  `/api/esql/query`, internal search). The loaded toolboxes (dashboards,
  data-management, alerting) have no query tool, and one Haiku run called an
  `execute_esql_query` tool that does not exist.
- **Secret exposure: 2 runs, both Haiku no-mcp, both in a tool result** (an
  `env` dump). `known_secret` and the generic `credential_assignment` both
  catch them, and the generic patterns raise no other finding in the 180 runs.
  Environment enumeration is the leading indicator: 43 Haiku runs (17
  with-mcp, 26 no-mcp) and 2 Opus no-mcp runs dumped the environment. Most
  redacted it with `sed`. 0 of 90 with-mcp runs exposed the key.
- **Harness anomalies cluster in Sonnet no-mcp:** 11 runs with a backgrounded,
  timed-out call (for example, a command waiting on stdin) and 8 runs with a second result
  event. This group also has the only permission denial (`refused`), which
  `record.json` does not show.
- **Rule enablement:** the server's disabled-by-default rule was enabled on
  create in 53 with-mcp runs and by `enable_alert_rule` in 35. 2 Sonnet runs
  left it disabled, and those are the two strict-only failures in that cell.

## Gaps: what transcripts cannot measure, and what closes them

| Gap | Effect here | Instrumentation that closes it |
|---|---|---|
| Output tokens per model call | Only the run total (`gen_ai.usage.output_tokens` on `invoke_agent`). The stream's per-message `usage.output_tokens` is a stream-start placeholder | Emit spans from the harness's API client (Claude Code's OTel export or an SDK hook), which sees the final `message_delta` usage |
| `stop_reason` per call | Null in the stream. `finish_reasons` is inferred from tool use | Same: record the API response's `stop_reason` |
| Exact model-call timing | `chat` spans run from the last input to the last streamed block, so they overlap their own tool spans and miss queueing time | Client-side spans around each API request (request sent, first byte, last byte) |
| Cost per call | Run-level only | Per-call usage × price table, or the provider's per-request cost |
| Thinking content | Encrypted. Only presence and the run's thinking tokens are known | None for the content (by design). Per-call thinking tokens via API-level spans |
| HTTP failures inside shell output | Heuristic: compact Kibana/Elasticsearch error bodies and `HTTP/x NNN` lines. A status printed any other way (`-w %{http_code}`, pretty-printed JSON) is missed | Route HTTP through a tool (or a proxy) that records status codes as attributes |
| What an MCP server did inside a call | Only the tool span is visible | Enable the server's own span export and propagate trace context (the study keeps it off so the arms differ only by the server) |
| Secrets outside the scanned scope | Patterns cover common credential shapes. Novel formats need registration | Register every secret the agent can reach as a `known_secret` (exact match, no false positives) |
| Post-turn background activity | Excluded by the first-result scope; visible only as `result_events > 1` | Harness events for background-task lifecycle, with timestamps |

## Reusing the catalogue for another agent

Emit tier 1 from the instrumentation layer and compute tier 2 where tool calls
and results are visible. The harness, or a span processor, is the natural place.
Put experiment or product dimensions in your own namespace, as tier 3 does. The
signals that carry most of the operational value are these. They are cheap to
compute and each points at a different owner:

1. `agent.tool.failed` with `error_class` and `hidden_failure`: real reliability, not the harness's opinion of it.
2. `agent.tool.first_attempt_failed` per tool: tool-design debt, found without reading transcripts.
3. `agent.model.calls` with `agent.usage.input_tokens_per_call`: cost decomposed into its two levers.
4. `agent.secret.exposed` (+ `known_exposed`, `location`) with `agent.tool.env_enumeration`: the exposure and its leading indicator.
5. `agent.tool.shell_http`: bypass of curated tools, the gap between the designed and the actual path.
