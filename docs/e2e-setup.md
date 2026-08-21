# E2E harness setup (one-time)

The E2E test (`tests/e2e/test_lmstudio.py`) drives a REAL local model in LM
Studio that uses mcp-for-kibana over MCP against the docker Kibana stack (Task
13). This is the MVP success gate: a plain-English request, through a real
model, through our server, into a real Kibana dashboard.

## One-time setup

1. Start and seed the stack:

   ```bash
   scripts/stack.sh up && scripts/stack.sh seed
   ```

   This writes `elastic-start-local/.env.seed` (gitignored) with `KIBANA_URL` and
   `KIBANA_TEST_API_KEY`. On this checkout the stack's Kibana listens on
   **`http://localhost:15601`**, not the "natural" 5601 — see
   `elastic-start-local/` for why (host-port remap to avoid clashing with an
   unrelated local Kibana on 5601/9200).

2. In LM Studio (GUI): load a tool-use-capable model —
   `google/gemma-4-12b-qat` is the reference model (measured 5/5 on the
   dashboards space chain and 2/2 on the alerting-space gate; the code
   fallback default remains `openai/gpt-oss-20b`, which drops the `space`
   parameter mid-chain 3/5 and fails the space gates — override with
   `LMSTUDIO_MODEL`). Put your machine's choice in `.env.local`
   (user-owned; no script ever writes or deletes it) as
   `LMSTUDIO_MODEL=<model-id>`.
   Its native tool grammar is parser-enforced, which made it 3/3 reliable
   on this gate; `qwen/qwen2.5-coder-14b` also completes the flow but
   corrupts its tool-call markers roughly one run in three
   (`tool_format_generation_error` 500 from LM Studio).

   **The context you load with is the context the test gets.** The harness
   deliberately sends no `context_length`, so it reuses whatever you loaded
   rather than spawning its own instance. LM Studio's default
   (`defaultContextLength: {"type": "max"}`) gives the model its maximum, which
   is what you want here — the endpoint docs note that *"higher values [are]
   recommended for MCP usage"*. (How many tools that means depends on your
   config: the default `KIBANA_MCP_TOOLBOXES` of `dashboards,data-management`
   with `KIBANA_MCP_TIER=write` advertises 16 — verified by listing them over
   stdio — not the 133 the full ten-toolbox set would.) If you
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
   own frozen tool surface (24 tools for the dashboards gate, 16 for
   alerting); one shared entry cannot satisfy both experiment designs.

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
   make test-e2e       # starts + seeds the stack; tests load elastic-start-local/.env.seed + .env.local themselves
   ```

   (Raw form: `uv run pytest -m e2e -v` — the conftest loads `elastic-start-local/.env.seed`
   and `.env.local` for you.)

## Env vars the harness reads

`KIBANA_URL` and `KIBANA_TEST_API_KEY` come from `elastic-start-local/.env.seed` (machine-written
by `scripts/stack.sh seed`); the `LMSTUDIO_*` vars belong in `.env.local`
(user-owned) or your shell environment.

| Var | Default | Required |
|---|---|---|
| `LMSTUDIO_URL` | `http://localhost:1234` | no |
| `LMSTUDIO_API_TOKEN` | (none) | no — sent as `Bearer` only if set |
| `LMSTUDIO_MODEL` | `openai/gpt-oss-20b` (code fallback; `google/gemma-4-12b-qat` is the measured reference — see step 2) | no |
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
a real `fastmcp.Client` over real stdio replays the recorded turn above against
the real server and real Kibana. It needs only the docker stack, so unlike this
page's live harness it runs on every PR (`e2e_replay_green`).

It certifies that the tools still exist under the same names, that arguments a
real model produced still validate against the live input schemas, that
arguments the runtime rejected are still rejected, that the guidance strings
survive, and that the corrected call still builds the dashboard — asserted with
the same helper the live test uses.

It does **not** certify that a model can choose those calls. That claim needs a
real model and belongs to this page's `e2e_green` alone.
