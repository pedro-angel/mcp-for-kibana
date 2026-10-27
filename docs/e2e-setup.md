# E2E harness setup

Two live-model tiers drive a REAL model that uses mcp-for-kibana over MCP
against the docker Kibana stack: a plain-English request, through a real
model, through our server, into real Kibana objects. Both send the same task
prompts and score Kibana's state afterwards, never the model's text
(`tests/_live_tasks.py`; the Claude tier's per-toolbox tasks are in
`tests/_toolbox_tasks.py`).

| Target | Model | Needs |
|---|---|---|
| `make test-e2e` | Claude via headless Claude Code (`claude -p`); `CLAUDE_E2E_MODEL`, default `haiku` | a Claude sign-in for the `claude` CLI (no API key) |
| `make test-e2e-local` | a small local model in LM Studio; `LMSTUDIO_MODEL`, required | LM Studio with the one-time setup below |

Both tiers run on demand, outside every gate (D61, superseding D42):
`e2e_green` is `n/a` in `dod.config` and in every `.github/dod` config. Run
`make test-e2e` when a change touches tool names, descriptions or schemas
(D38). What `make dod` and CI run instead is [the replay
tier](#the-replay-tier-e2e_replay): recorded real-model turns for every
toolbox. A config that marks `e2e_green` `required` still gets the old
criterion: the same `pytest -m e2e_claude`, records in the gate's log
directory (`/tmp/dod-<repo>/e2e_green-runs/`), and a NO-GO naming the fix for
a missing `claude` CLI or sign-in.

## The Claude tier (`make test-e2e`)

`tests/e2e/test_claude_live.py` runs eleven tasks, five runs each by default:
the three LM Studio tasks below (flights dashboard, dashboard in a new space,
alert rule in a new space); `fix_rule_threshold`, where the harness seeds a
disabled alert rule with a wrong threshold and the model must fix it in place;
one task for each toolbox no model had been offered before (cases,
security-detections, observability, streams, fleet, platform-health), each
with a server that loads only that toolbox; and `delete_one_dashboard`, the
one task at the `destructive` tier, where the harness seeds two marker
dashboards and the model must delete exactly the one it is told to, by its id.
Tasks, pass criteria and N are pre-registered in
`scripts/experiment/CLAUDE_E2E.md`.

One-time setup — the fixes the `e2e_green` NO-GO lines point to:

```bash
npm install -g @anthropic-ai/claude-code   # installs the `claude` CLI (Claude Code)
claude auth login                          # signs the CLI in to your Claude account (no API key)
claude auth status                         # the gate requires "loggedIn": true here
claude -p "say ok" --model haiku           # optional: confirms a headless run works
```

Then:

```bash
make test-e2e                      # starts + seeds the stack, then pytest -m e2e_claude
```

Where a config requires `e2e_green`, its GO line names the model and runs per
task it ran with (`CLAUDE_E2E_MODEL` / `CLAUDE_E2E_RUNS`), so a GO from an
override — say a single run per task — is visible in the verdict, and the
gate's `verdict.json` records the model (D55).

The model gets this server's tools and nothing else: `--tools ""` removes
every built-in tool (no shell, files or web), `--strict-mcp-config` loads only
the harness's MCP config, and `--permission-mode dontAsk` with
`--allowedTools mcp__mcp-for-kibana` runs those tools without a prompt and
denies anything else. A run offered any other tool fails. The Kibana API key
goes only into the MCP server's `env` block, in a mode-0600 config file in a
temporary directory; the model's environment drops every `KIBANA*` variable,
the prompt never names the key, and each transcript is searched for it — a hit
is redacted on disk and fails the run, without printing the key.

| Var | Default | Meaning |
|---|---|---|
| `CLAUDE_E2E_MODEL` | `haiku` | model or alias passed to `--model`; each record names the model the CLI reports |
| `CLAUDE_E2E_RUNS` | `5` | runs per task |
| `CLAUDE_E2E_BUDGET_USD` | `5` | the session starts no run once its cumulative cost reaches this; each run is also capped at $0.75 |
| `CLAUDE_E2E_OUT` | `scripts/experiment/runs/claude-e2e/adhoc` | run-record directory |

Each run writes `prompt.txt`, `transcript.jsonl` (stream-json),
`record.json` and, when non-empty, `stderr.txt` to its own directory, and
appends its record to `runs.jsonl`. Summarise a directory with
`uv run python scripts/experiment/claude_e2e_report.py <dir>`.

### Exporting the server's spans from the Claude tier (default off)

The server writes one span per tool call when `KIBANA_MCP_OTEL_ENABLED` is on
([Configuration › OpenTelemetry](configuration.md#opentelemetry)). The harness
builds the server's environment from its MCP entry, so a plain `export` does
not reach it on its own. Set `KIBANA_MCP_OTEL_ENABLED` in the shell that runs
`make test-e2e` and the harness copies every `KIBANA_MCP_OTEL_*` and `OTEL_*`
variable into the server's `env` block, which sits in the same mode-0600
file as the Kibana key. They can never replace the URL, the key, the
toolboxes or the tier. While the switch is on, the model's environment loses
every `OTEL_*` and `HONEYCOMB_*` variable, so only the server holds the
exporter's credentials. With the switch unset, the config and the model's
environment are the same as before (D44).

To send the spans to Honeycomb, for example:

```bash
export KIBANA_MCP_OTEL_ENABLED=1
export KIBANA_MCP_OTEL_ENDPOINT=https://api.eu1.honeycomb.io   # your team's region; a key from another region gets 401
export OTEL_EXPORTER_OTLP_HEADERS="x-honeycomb-team=$HONEYCOMB_API_KEY"  # leave KIBANA_MCP_OTEL_SECRET_TOKEN unset
export KIBANA_MCP_OTEL_SERVICE_NAME=mcp-for-kibana-roadmap      # service.name, so the Honeycomb dataset
export OTEL_RESOURCE_ATTRIBUTES="vcs.ref.head.revision=$(git rev-parse HEAD),deployment.environment=dev"
make test-e2e
```

With no `KIBANA_MCP_OTEL_SECRET_TOKEN`, the exporter takes its headers from
`OTEL_EXPORTER_OTLP_HEADERS`. Each tool call is a span named
`tools/call <tool>`, and a run's last call arrives too: the server exports
500 ms after a span ends and flushes on SIGTERM, inside the half second
Claude Code gives a stdio server before SIGKILL. To compare blocks, tag each
one with a resource attribute of your own (for example
`OTEL_RESOURCE_ATTRIBUTES=...,mcp.run=<block>`) and check that the span count
per tool equals the tool calls in its `runs.jsonl`.

Two other tiers start the server and can export too:

- **Replay** (`make test-e2e-replay`) starts the server over stdio and passes
  the runner's whole environment, so the same exports work there unchanged.
- **Contract** (`make test-contract`) runs the tools in-process and never
  calls `main()`, which is where telemetry is set up, so that tier sends no
  spans.

## The LM Studio tier (`make test-e2e-local`): one-time setup

`tests/e2e/test_lmstudio*.py` drive a local model in LM Studio, for testing
small local models. The first of them (`test_lmstudio.py`) was the MVP success
gate.

1. Start and seed the stack:

   ```bash
   scripts/stack.sh up && scripts/stack.sh seed
   ```

   This writes `elastic-start-local/.env.seed` (gitignored) with `KIBANA_URL` and
   `KIBANA_TEST_API_KEY`. On this checkout the stack's Kibana listens on
   **`http://localhost:15601`**, not the "natural" 5601 — see
   `elastic-start-local/` for why (host-port remap to avoid clashing with an
   unrelated local Kibana on 5601/9200).

2. In LM Studio (GUI): load a tool-use-capable model
   and name it in `LMSTUDIO_MODEL` (shell, or `.env.local` — user-owned; no
   script ever writes or deletes it). The tests have no default model and
   fail with this fix when it is unset (D33). Measured on Kibana 9.5.4
   (D18): `google/gemma-4-12b-qat` 5/5 on the dashboards space chain and 5/5
   on the alerting-space gate. Other models:
   `openai/gpt-oss-20b` drops the `space` parameter mid-chain 3/5 and fails
   the space gates; `google/gemma-4-26b-a4b-qat` is faster but built one
   dashboard in 5 in the default space; `qwen/qwen2.5-coder-14b` completes
   the flights flow but corrupts its tool-call markers roughly one run in
   three (`tool_format_generation_error` 500 from LM Studio).

   `make test-e2e-local` runs all three — the flights dashboard
   (`test_lmstudio.py`) and the dashboards and alerting space chains — needs
   both server entries from step 5, and takes about four minutes with
   `google/gemma-4-12b-qat`.

   **The context you load with is the context the test gets.** The harness
   deliberately sends no `context_length`, so it reuses whatever you loaded
   rather than spawning its own instance. LM Studio's default
   (`defaultContextLength: {"type": "max"}`) gives the model its maximum, which
   is what you want here — the endpoint docs note that *"higher values [are]
   recommended for MCP usage"*. (How many tools that means depends on your
   config: the default `KIBANA_MCP_TOOLBOXES` of `dashboards,data-management`
   with `KIBANA_MCP_TIER=write` advertises 27 — verified by listing them over
   stdio — not the 184 the full ten-toolbox set would.) If you
   load with a small context, a reasoning-heavy model can burn its whole budget
   thinking and get truncated before it emits a tool call; the test then fails
   on `assert tool_calls` (MEASURED with `nvidia/nemotron-3-nano-omni` at 8000).

   **If a run fails with an LM Studio memory error**, the machine had no room
   for the model — the gate never exercised it. Check what else is resident with
   `lms ps` and free it with `lms unload <identifier>`; never `lms unload --all`,
   which evicts models loaded for unrelated work. Note that LM Studio's
   Auto-Evict (on by default) already keeps at most one *JIT-loaded* model in
   memory; models you loaded yourself in the GUI are never auto-evicted.

3. GUI > Developer (the `>_` icon in the left sidebar) > Settings (gear by
   the server status): enable **Require Authentication** first — the
   mcp.json toggle below is greyed out until it's on. Then enable
   **Allow calling servers from mcp.json**. Then **Manage Tokens** >
   **Create Token**, tick the **"Use MCP Servers"** permission, and export
   the token as `LMSTUDIO_API_TOKEN` (the harness sends it as a `Bearer`
   header; without a token, LM Studio rejects mcp.json plugin usage once
   Require Authentication is on).

4. GUI > Developer > Server Settings: enable **"Allow calling servers from
   mcp.json"** (the exact wording may vary by LM Studio version — look for
   the toggle that gates the `integrations` field on `/api/v1/chat`). The
   GUI is one way to set it, not the only one: the state persists in
   `~/.lmstudio/.internal/permissions-store.json` (verified live, CLI commit
   6041ae0) — `serverPermissions.pluginUse: "allowAll"` is this toggle,
   `tokenMode: "required"` is *Require Authentication* (step 3), and each
   API token carries its own `permissions.pluginUse`. Re-verified 2026-08-09
   on the green run: the store's root holds `tokenMode: "required"` beside
   `serverPermissions.pluginUse: "allowAll"`, and the token used by the
   harness carries `permissions.pluginUse: "allowAll"`.

   The `lms` CLI exposes no command for it. Pre-seeding the file headlessly
   for a CI e2e job remains **unverified** — no bring-up from a pre-seeded
   file has been run. That path is also no longer on the critical path:
   CI covers this MCP surface through the deterministic replay tier below,
   which needs no LM Studio at all.

   **Resolved 2026-08-09.** With the toggle ON, the harness runs green
   end to end (`uv run pytest -m e2e` → 1 passed). The 403 below is the
   symptom of the toggle being OFF, kept as a diagnostic — not an
   outstanding blocker. With the toggle off, `POST /api/v1/chat` with
   `"integrations": ["mcp/mcp-for-kibana"]` returns HTTP 403:

   ```json
   {
     "error": {
       "message": "Permission denied to use plugin 'mcp/mcp-for-kibana'. Ensure that the server configuration allows plugin usage and, if using an API token, it has the necessary permissions.",
       "type": "invalid_request",
       "param": "integrations",
       "code": null
     }
   }
   ```

   If you see this exact error, the fix is the toggle above (via the GUI or
   the persisted `permissions-store.json`), not an MCP-server code change —
   do not try to work around it from the API side.

5. Add the server to `~/.lmstudio/mcp.json` (merge into the existing
   `mcpServers` object — do not clobber other entries). See
   [`docs/examples/mcp.json`](examples/mcp.json) for the shape; the entry
   name must be `mcp-for-kibana` because LM Studio's integration id is
   `mcp/<server name from mcp.json>` — i.e. `mcp/mcp-for-kibana`. Point it at
   this checkout, and use the **test stack's** `KIBANA_URL`
   (`http://localhost:15601`, not the example's `5601` placeholder) and the
   `KIBANA_TEST_API_KEY` from `elastic-start-local/.env.seed`:

   ```json
   {
     "mcpServers": {
       "mcp-for-kibana": {
         "command": "uv",
         "args": ["--directory", "/ABSOLUTE/PATH/TO/mcp-for-kibana", "run", "mcp-for-kibana"],
         "env": {
           "KIBANA_URL": "http://localhost:15601",
           "KIBANA_API_KEY": "<value of KIBANA_TEST_API_KEY from elastic-start-local/.env.seed>",
           "KIBANA_MCP_TIER": "write"
         }
       }
     }
   }
   ```

   `~/.lmstudio/mcp.json` is user config outside this repo — the real API
   key goes there, never into any file under this checkout.

   The alerting-space gate (`test_lmstudio_alerting_space.py`) needs a
   **second** entry named `mcp-for-kibana-alerting` — same command and env,
   but `KIBANA_MCP_TOOLBOXES: "alerting,platform-admin"`. Each gate keeps its
   own toolbox set (designed on 24 tools for the dashboards gate and 17 for
   alerting; data-management and alerting have grown since); one shared
   entry cannot satisfy both experiment designs.

   > Need a fresh API key? Delete `elastic-start-local/.env.seed` (or its `KIBANA_TEST_API_KEY`
   > line) and re-run `scripts/stack.sh seed` — your `.env.local` is a
   > separate file and is never touched.

   `KIBANA_MCP_TIER: write` (not `destructive`) is a deliberate choice: it
   keeps `delete_dashboard`/`delete_panel` off the model's tool list for
   this harness, so an autonomous local model can create and edit but not
   destroy — a smaller blast radius for an unattended E2E run than the
   `destructive` example in `docs/examples/mcp.json`.

6. Run:

   ```bash
   make test-e2e-local   # starts + seeds the stack; tests load elastic-start-local/.env.seed + .env.local themselves
   ```

   (Raw form: `uv run pytest -m e2e -v` — the conftest loads `elastic-start-local/.env.seed`
   and `.env.local` for you.)

## Env vars the LM Studio harness reads

`KIBANA_URL` and `KIBANA_TEST_API_KEY` come from `elastic-start-local/.env.seed` (machine-written
by `scripts/stack.sh seed`); the `LMSTUDIO_*` vars belong in `.env.local`
(user-owned) or your shell environment.

| Var | Default | Required |
|---|---|---|
| `LMSTUDIO_URL` | `http://localhost:1234` | no |
| `LMSTUDIO_API_TOKEN` | (none) | no — sent as `Bearer` only if set |
| `LMSTUDIO_MODEL` | (none) | yes — for `make test-e2e-local` (step 2) |
| `KIBANA_URL` | (none) | yes — from `elastic-start-local/.env.seed` |
| `KIBANA_TEST_API_KEY` | (none) | yes — from `elastic-start-local/.env.seed` |

## Local APM / OpenTelemetry backend

The stack can also run a local **APM server** — the OpenTelemetry backend the
instrumented MCP server exports to, so no separate observability stack is needed
(the same local Elasticsearch stores the traces, in `traces-apm*`). Two paths, by
design:

- **`make stack-start`** (the dev facade) brings APM up **by default**, so
  telemetry is always active while developing.
- **`scripts/stack.sh up`** (the lean path used by `make test-contract` /
  `test-e2e` and CI) leaves it **off** — the contract/E2E path never needs it and
  must not couple to a test-irrelevant service. Start it there explicitly with
  `KIBANA_MCP_STACK_APM=1 scripts/stack.sh up`.

Either way you get `apm-server` (image `docker.elastic.co/apm/apm-server`) as a
project addition on `http://localhost:18200` — an APM-intake **and** native OTLP
endpoint, both gated by `ELASTIC_APM_SECRET_TOKEN` (default `mcp-for-kibana-dev-apm-token`,
a non-secret local dev constant in `elastic-start-local/.env.example`). Port
`18200` (not the natural `8200`) so it coexists with an unrelated local Elastic
stack. `scripts/stack.sh down` / `status` cover it automatically (same compose
project). To emit spans, run the server with `KIBANA_MCP_OTEL_ENABLED=1` and the
`otel` extra (see [Configuration › OpenTelemetry](configuration.md#opentelemetry)).

## On the response shape

Observed live on 2026-08-09 (`openai/gpt-oss-20b`), so the filter is pinned to
a real response rather than to written research. `/api/v1/chat` returns
`output` as a list of typed items:

| `type` | Carries | Notes |
|---|---|---|
| `reasoning` | `content` | the model's chain of thought |
| `tool_call` | `tool`, `arguments`, `output`, `provider_info` | an **executed** call; `provider_info.plugin_id` is `mcp/mcp-for-kibana` |
| `invalid_tool_call` | `reason`, `metadata.tool_name`, `metadata.arguments` | rejected by LM Studio's own schema validation — **never reached the server** |
| `message` | `content` | the final assistant text |

The test counts `tool_call` only. `invalid_tool_call` is deliberately excluded:
it is a turn the runtime refused before mcp-for-kibana saw it, so counting it would
credit the server for a call it never received.

## What that first green run actually showed

The model did not succeed on its first attempt, and that is the interesting
part. It took five turns, recovering each time from the server's error text
alone:

1. `create_dashboard` with `time_range: "now-7d/d to now"` — rejected by LM
   Studio against the tool's JSON Schema; never reached the server.
2. `create_dashboard` with `data_view: "flights"` — server: *"data view
   'flights' not found — call list_data_views to see what exists"*.
3. `list_data_views` — discovers `Kibana Sample Data Flights`.
4. `create_dashboard` with `field: "price"` / `"carrier"` — server: *"field
   'price' does not exist … available fields include: AvgTicketPrice, … — did
   you mean 'Carrier'?"*.
5. `create_dashboard` with `AvgTicketPrice` / `Carrier` — **created**.

So the error messages are not diagnostics for humans; they are the recovery
path for a model with no other way to learn the schema of your data. That is
why `tests/e2e_replay/` replays this exact transcript in CI and asserts the
guidance strings survive — see [the replay tier](#the-replay-tier-e2e_replay).

## The replay tier (`e2e_replay`)

`make test-e2e-replay` runs the same MCP surface with **no model in the loop**:
a real `fastmcp.Client` over real stdio replays the recorded turn above — and
the other recorded turns in `tests/e2e_replay/transcripts/`, one or more per
toolbox (D61) — against the real server and real Kibana. It needs only the
docker stack, so unlike this page's live harness it runs in every gate run
and on every PR (`e2e_replay_green`).

The per-toolbox turns are Claude runs of the pre-registered tasks in
`tests/_toolbox_tasks.py`, converted from their committed run records by
`tests/e2e_replay/record_claude_turn.py` (call order and arguments are the
model's own; the needles, the id bindings and the `schema_rejected` entry are
authored and say so). The replay scores them with the live tier's own ladder,
state rungs only. `tests/unit/test_replay_coverage.py` fails `unit_green` for
a toolbox no transcript reaches, naming the steps to record one.

`wheel_replay_green` runs the same replay against the **installed package**
rather than the checkout: `scripts/checks/wheel-replay.sh` builds the wheel,
installs it into a fresh venv with `pip` (dependencies resolved from the
`pyproject.toml` ranges, as a PyPI install resolves them), and points the suite
at that venv's `mcp-for-kibana` through `KIBANA_MCP_SERVER_BIN`. It is the only
live tier that exercises what `pip install mcp-for-kibana` delivers.

`image_replay_green` runs it once more against the **built Docker image over
HTTP** (D62): `scripts/checks/image-replay.sh` points the suite at the image
through `KIBANA_MCP_SERVER_IMAGE`, and each transcript runs in its own
container (`--network host`, the seed's Kibana URL, no API key), its client
sending `Authorization: ApiKey <seed key>` on every request.
`tests/e2e_replay/test_image_http.py` adds the other half: a call without the
header, or with another scheme, is refused naming the fix, even with a
`KIBANA_API_KEY` in the container. It is the only tier that runs the image's
Python, its `--no-dev` venv and the per-request credential path HTTP users
rely on.

It certifies that the tools still exist under the same names, that arguments a
real model produced still validate against the live input schemas, that
arguments the runtime rejected are still rejected, that the guidance strings
survive, and that the corrected call still builds the dashboard — asserted with
the same helper the live test uses.

It does **not** certify that a model can choose those calls. That claim needs a
real model and belongs to this page's live-model tiers alone.
