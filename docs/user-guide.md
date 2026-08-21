# User guide — your first dashboards by talking

mcp-for-kibana lets you describe a chart in plain language and get a real,
clickable Kibana dashboard back. This guide takes you from nothing to your
first dashboard in about ten minutes, using a disposable local Kibana with
sample data — you can't break anything.

No Kibana or Lens knowledge required. That's the point.

## What you need

- **Docker** (for the disposable Kibana playground)
- **uv** (`brew install uv`) — runs the server, no manual Python setup
- **An MCP client with a model**: [LM Studio](https://lmstudio.ai) (chat
  with a local model) or [Claude Code](https://claude.com/claude-code).
  Both paths are below; LM Studio is the fully-local one.

## Step 1 — Start the Kibana playground

From the repo root:

```bash
scripts/stack.sh up      # Elasticsearch + Kibana 9.4.3 (first run pulls ~2GB)
scripts/stack.sh seed    # loads the flights sample dataset; mints an API key only if needed
```

When it finishes you have:

- Kibana at **http://localhost:15601** (log in as `elastic`; the dev password is
  `ES_LOCAL_PASSWORD` in `elastic-start-local/.env.example`)
- a dataset of ~13k sample flights to play with
- an API key in `elastic-start-local/.env.seed` — show it any time with `scripts/stack.sh env`

> The playground uses ports **19200/15601** instead of the usual 9200/5601
> so it never collides with a Kibana you already run.

## Step 2 — Connect a client

### Option A: LM Studio (local model, chat UI)

1. Add the server to `~/.lmstudio/mcp.json` (create the file if missing),
   filling in your checkout path and the API key from `scripts/stack.sh env`:

   ```json
   {
     "mcpServers": {
       "mcp-for-kibana": {
         "command": "uv",
         "args": ["--directory", "/ABSOLUTE/PATH/TO/mcp-for-kibana", "run", "mcp-for-kibana"],
         "env": {
           "KIBANA_URL": "http://localhost:15601",
           "KIBANA_API_KEY": "<KIBANA_TEST_API_KEY from elastic-start-local/.env.seed>",
           "KIBANA_MCP_TIER": "write"
         }
       }
     }
   }
   ```

2. Load a **tool-capable model**. `openai/gpt-oss-20b` is the one this
   project's end-to-end suite passes with reliably; qwen2.5-coder-14b also
   works but occasionally garbles its tool-call syntax and the request
   errors out (just retry).

3. Open a new chat and enable the **mcp-for-kibana** integration for that chat
   (the tools/plug control in the chat input area). The first time a tool
   runs, LM Studio asks for confirmation — allow it (or "always allow" the
   read-only ones).

### Option B: Claude Code

```bash
claude mcp add kibana \
  --env KIBANA_URL=http://localhost:15601 \
  --env KIBANA_API_KEY=<KIBANA_TEST_API_KEY from elastic-start-local/.env.seed> \
  --env KIBANA_MCP_TIER=write \
  -- uv --directory /ABSOLUTE/PATH/TO/mcp-for-kibana run mcp-for-kibana
```

Then start `claude` and talk.

## Step 3 — Say things

Copy-paste these into the chat, in order or not:

**Find out what data exists:**

> What data do I have available to visualize?

**Your first dashboard:**

> Create a dashboard called "My first dashboard" with a bar chart of the
> average ticket price by carrier over the last 7 days.

The reply includes a URL like
`http://localhost:15601/app/dashboards#/view/<id>` — open it (log in as
`elastic` with the `ES_LOCAL_PASSWORD` dev password from
`elastic-start-local/.env.example`) and there's your chart.

**Build it out:**

> Add a pie chart of flights by destination country to that dashboard.

> What fields does the flights data have?

**Something more ambitious, in one go:**

> Create a dashboard called "Flight ops" with: a line chart of flights over
> time, a single big number showing the total flight count, and a table of
> the top 20 carriers with their average ticket price and flight count.

**When you get it wrong on purpose** (try it — the errors are the feature):

> Create a dashboard with the average of the "carrier" field.

The server checks your request against the real data before touching
Kibana, so instead of a broken chart you get back something like *"metric
'average' needs a number field but 'Carrier' is string"* — and the model
corrects itself.

## What's actually happening

The model only sees sixteen small tools (`list_data_views`,
`describe_data_view`, `create_dashboard`, …). It fills in a simple spec —
chart type, fields, groupings, filters — and the server does the hard part:
validating the fields against your data and translating the spec into
Kibana's real Lens API payloads. The model never writes Lens JSON, which is
why small local models handle this reliably.

## Safety rails

- Tools are tiered **read / write / destructive**. The default tier is
  `write`: the model can create and modify dashboards but the delete tools
  **don't exist** as far as it knows. Set `KIBANA_MCP_TIER=destructive` in
  the client config if you want it to be able to delete;
  `KIBANA_MCP_TIER=read` makes it look-but-don't-touch.
- Every request runs with *your* API key, so Kibana's own permissions and
  audit log always apply.
- Dashboards that contain panel types the Kibana 9.4 API can't round-trip
  (maps, ML panels, …) are refused for modification rather than silently
  damaged.
- Re-creating a dashboard with the same title overwrites it in place rather
  than duplicating it — so if you (or the model) get a dashboard wrong, the
  fix is just to re-create it with the same title.

## Using your own Kibana instead

Needs **Kibana 9.4 or newer** (the modern Dashboards API is a 9.4
Technical Preview). Create an API key in Kibana (Stack Management → API
keys), then point the client env at it:

```json
"env": {
  "KIBANA_URL": "https://your-kibana.example.com",
  "KIBANA_API_KEY": "<your key>"
}
```

For a shared, multi-user server over HTTP (each caller sends their own
key), see [Deployment](deployment.md#docker).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Model says a data view/field doesn't exist | Ask it to run `list_data_views` / `describe_data_view` first — field names are case-sensitive (`Carrier`, not `carrier`). |
| `401` / "API key invalid" | Key expired or wrong. Re-seed: `scripts/stack.sh seed` mints a fresh key only when the current one no longer authenticates; then update the client env. |
| LM Studio: `403 Permission denied to use plugin 'mcp/mcp-for-kibana'` | One-time LM Studio setup: enable **Require Authentication** then **Allow calling servers from mcp.json** (Developer page → server Settings). Full walkthrough in [e2e-setup.md](e2e-setup.md). |
| LM Studio: `tool_format_generation_error` (500) | The model garbled its tool-call syntax (qwen does this ~1 in 3). Retry, or switch to `openai/gpt-oss-20b`. |
| "dashboard contains unsupported panels or fields" | That dashboard has content the 9.4 API can't safely round-trip. Edit it in the Kibana UI; the refusal is protecting it. |
| Nothing at localhost:15601 | `scripts/stack.sh up` again — and give Kibana a minute; `docker ps` should show two healthy containers. |

## Cleaning up

```bash
scripts/stack.sh down    # removes containers, volumes, and elastic-start-local/.env.seed
```

Your dashboards live inside the playground's volume, so this deletes them
too. That's what makes it a playground.

Coming back later? `scripts/stack.sh up && scripts/stack.sh seed` rebuilds
it. Seeding keeps your existing API key when it still authenticates, so a
key you copied into `~/.lmstudio/mcp.json` or the Claude Code entry stays
valid; seeding mints a fresh one only when the current key no longer works —
in the normal `down`→`seed` cycle, or after you force a rotation (see the
re-mint hatch in [e2e-setup.md](e2e-setup.md)) — copy it from
`scripts/stack.sh env`. Your LM Studio settings live in `.env.local`,
which `down` never touches.
