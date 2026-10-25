# Configuration

All settings are read once, in `kibana_mcp.config.Settings`
(`src/kibana_mcp/config.py`), and are set via env vars (prefix
`KIBANA_MCP_` for the deployment-shaped ones; `KIBANA_URL`,
`KIBANA_API_KEY`, and `KIBANA_PUBLIC_URL` are recognized both bare and
prefixed, matching how Kibana's own tooling names them):

| Var | Default | Meaning |
|---|---|---|
| `KIBANA_URL` | `http://localhost:5601` | Kibana base URL the server connects to. |
| `KIBANA_PUBLIC_URL` | (falls back to `KIBANA_URL`) | URL used to build human-clickable dashboard links, if different from the URL the server itself reaches Kibana on (e.g. behind a proxy). |
| `KIBANA_API_KEY` | (none) | stdio mode: the API key used for every request. HTTP mode: ignored unless `KIBANA_MCP_ALLOW_ENV_KEY_HTTP=true` — each caller sends their own key. |
| `KIBANA_MCP_TOOLBOXES` | `dashboards,data-management` | Comma-separated list of toolboxes to register (of the 10 available). |
| `KIBANA_MCP_TIER` | `write` | Max tool tier to register: `read`, `write`, or `destructive`. |
| `KIBANA_MCP_TOOLS` | (none — off) | Comma-separated allow list of tool names. When set, the server publishes exactly the listed tools the tier allows and nothing else, and `KIBANA_MCP_TOOLBOXES` is ignored. An `@name` entry stands for a named list's tools (`@dashboards-analyst`). See [Tool selection](#tool-selection). |
| `KIBANA_MCP_TOOL_LISTS_FILE` | (none — off) | Path to a TOML file of your own named tool lists, used as `@name` in `KIBANA_MCP_TOOLS`. Read at startup only when `KIBANA_MCP_TOOLS` is set; a file that cannot be read or does not validate fails startup. See [Your own lists](#your-own-lists-the-list-file). |
| `KIBANA_MCP_EXPORT_DIR` | (none — a fresh, unguessable 0700 temp dir per run) | Directory where saved-objects export/import NDJSON files are written. An explicit path is created 0700, a symlink there is refused, and a pre-existing directory is tightened to 0700. |
| `KIBANA_MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `KIBANA_MCP_HOST` | `127.0.0.1` | Bind host (HTTP transport only). |
| `KIBANA_MCP_PORT` | `8000` | Bind port (HTTP transport only). |
| `KIBANA_MCP_ALLOW_ENV_KEY_HTTP` | `false` | HTTP mode only: opt in to letting `KIBANA_API_KEY` act as a shared fallback credential when a request has no `Authorization` header. A per-request `Authorization` header always takes precedence even when this is on — the env key is only a fallback for requests that omit one, never an override. Off by default so a single env key can't silently become a shared credential across callers. |
| `KIBANA_MCP_ENV_FILE` | (none) | Path to a `KEY=value` file loaded at startup so a launcher need not hard-copy credentials. Loaded with *setdefault* — explicit process env always wins. See [Loading credentials from a file](#loading-credentials-from-a-file) below. Additive and off by default. |
| `KIBANA_MCP_OTEL_ENABLED` | `false` | Export OpenTelemetry traces (a span per tool call). Off by default. Needs the `otel` extra. See [OpenTelemetry](#opentelemetry) below. |
| `KIBANA_MCP_OTEL_ENDPOINT` | `http://localhost:18200` | OTLP/HTTP endpoint base (the server appends `/v1/traces`). Default targets the stack's opt-in APM. |
| `KIBANA_MCP_OTEL_SECRET_TOKEN` | (none) | Bearer token for the OTLP endpoint (the APM endpoint is token-gated). |
| `KIBANA_MCP_OTEL_SERVICE_NAME` | `mcp-for-kibana` | `service.name` the traces appear under. |
| `KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV` | `false` | Parent every span under the W3C `TRACEPARENT` the server was started with, so a stdio client that passes trace context only through the environment (Claude Code in `-p` mode) sees the server's spans in its own trace. Needs `KIBANA_MCP_OTEL_ENABLED`. |

This table is kept in sync with the one in the project
[README](https://github.com/pedro-angel/mcp-for-kibana#configuration) — if you
edit one, edit both.

## Tier semantics

`KIBANA_MCP_TIER` caps the highest tool tier registered on the server —
`Tier.allowed` (`config.py`) computes a tier as "everything at or below
it" (`read` ⊂ `write` ⊂ `destructive`):

Every tool in every toolbox is tagged with its tier (across all 10 toolboxes:
74 read, 70 write, 39 destructive):

- `read` — read-only tools (74 of 183), e.g. `list_data_views`,
  `describe_data_view`, `search_dashboards`, `get_dashboard`.
- `write` (**default**) — read tools plus creates/updates (70), e.g.
  `create_dashboard`, `create_visualization`, `add_panel`, `update_panel`.
  Destructive tools do not exist as far as the model can tell.
- `destructive` — everything (39 more), including `delete_dashboard`,
  `delete_panel`, and the other irreversible actions.

At server assembly, tools above the configured tier are not merely
rejected if called — they are registered internally like every other tool,
then hidden from the tool list via FastMCP's tag-based visibility API
(`mcp.disable(tags={tier_tag})` in `server.py`), so a model at the `write`
tier never even sees that deletion tools exist. Full detail, including the
MCP annotation hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`)
set per tool, is in the [Tool reference](tools.md#tiers-and-annotations).

## Toolbox selection

`KIBANA_MCP_TOOLBOXES` (default `dashboards,data-management`) is a comma-separated
list of toolbox names to register. The registry
(`src/kibana_mcp/toolboxes/__init__.py`) has ten entries:

```python
TOOLBOXES: dict[str, Toolbox] = {
    "dashboards": DashboardsToolbox(),
    "data-management": DataManagementToolbox(),
    "alerting": AlertingToolbox(),
    "cases": CasesToolbox(),
    "platform-health": PlatformHealthToolbox(),
    "observability": ObservabilityToolbox(),
    "security-detections": SecurityDetectionsToolbox(),
    "platform-admin": PlatformAdminToolbox(),
    "streams": StreamsToolbox(),
    "fleet": FleetToolbox(),
}
```

Requesting an unknown toolbox name fails server startup immediately with a
`ValueError` listing what's available, rather than silently ignoring it.
This is the seam the [composable toolboxes](architecture.md#toolboxes)
pillar is built on: a small local model can be handed a narrower tool
surface (fewer toolboxes, or a lower tier) than a larger, more capable one,
without any code change — only configuration.

## Tool selection

`KIBANA_MCP_TOOLS` (default: unset, feature off) is a comma-separated allow
list of tool names. **The server publishes the listed tools that the tier
allows, and nothing else.** For example, a dashboard-building surface for a
small local model:

```bash
KIBANA_MCP_TIER=write
KIBANA_MCP_TOOLS=list_data_views,describe_data_view,search_dashboards,get_dashboard,create_dashboard,add_panel,update_panel,create_data_view
```

publishes exactly those 8 tools; the same list with `KIBANA_MCP_TIER=read`
publishes only its 4 read tools.

- **The list alone decides.** The server registers whichever toolboxes own the
  listed tools, so `KIBANA_MCP_TOOLBOXES` is not needed. If it is set as well,
  it is ignored and one startup line on stderr says so.
- **A listed tool above the tier is not published.** The tier still caps the
  surface; one startup line on stderr names each such tool and the tier that
  would publish it. Startup does not fail, so one list can be reused at a
  lower tier.
- **A name that is not a tool fails startup** with a `ValueError` naming every
  unknown name, with a "did you mean" suggestion where one is close. The
  [Tool reference](tools.md) lists every tool name.
- **Unset or empty** (`KIBANA_MCP_TOOLS=`, or only commas and spaces) is the
  feature off: the server assembles exactly as without the setting. Repeated
  names are counted once.
- **Start time only.** The list is read when the server starts; changing it
  means restarting the server.
- **An entry starting with `@` names a list of tools** — a built-in one or
  one of your own — and can be mixed with plain names, e.g.
  `KIBANA_MCP_TOOLS=@dashboards-analyst,create_alert_rule`. See
  [Named lists](#named-lists).
- **Tools the list leaves out but needs are named on stderr.** See
  [Coherence warnings](#coherence-warnings).

The list only hides tools — it is applied with the same visibility mechanism
as the tier (`mcp.disable(...)` in `server.py`), never by enabling, so it
cannot publish a tool the tier hid. Like toolboxes and tiers, it is curation,
**not a security boundary**: the Kibana API key's RBAC is the real limit on
what the server can do.

### Named lists

An `@name` entry in `KIBANA_MCP_TOOLS` stands for the tools of the list
called `name`, in the list's order. The server expands every such entry
before it checks names, so the rules above apply to the expanded list
unchanged: the published set is still the listed tools the tier allows, a
repeated tool counts once, and a list tool above the tier is named on stderr
like any listed tool. A list name that does not exist fails startup with a
`ValueError` naming every unknown `@name` (with a "did you mean" where one is
close) and the lists that do exist. A list is a set of tool names only: it
carries no tier, so `@dashboards-analyst` at `KIBANA_MCP_TIER=read` publishes
its read tools. Without any `@` entry the published tools are the same as
before named lists existed; the only differences are on stderr (the
[coherence warnings](#coherence-warnings)) and that a list file you name is
still read and validated whenever `KIBANA_MCP_TOOLS` is set. With
`KIBANA_MCP_TOOLS` unset, none of this applies and no list file is read.

#### Built-in lists

The server ships two [profiles](https://github.com/pedro-angel/mcp-for-kibana/tree/main/profiles)
as lists, so their tools can be mixed with tools from other toolboxes. Each
list holds exactly the tools its profile publishes with toolboxes and a tier:
`read-only-explorer` is `dashboards`, `platform-health` and `data-management`
at `read`; `dashboards-analyst` is `dashboards` and `data-management` at
`write`, the default selection. The lists live as data in
`src/kibana_mcp/tool_lists.py` (`BUILTIN_LISTS`); unit tests check this table
against them and each list against its profile.

| List | Tools | Meant for tier |
|---|---|---|
| `read-only-explorer` | `list_data_views`, `describe_data_view`, `resolve_short_url`, `export_saved_objects`, `find_saved_objects`, `get_saved_object`, `get_default_data_view`, `search_dashboards`, `get_dashboard`, `search_visualizations`, `get_visualization`, `get_kibana_status`, `get_kibana_stats`, `get_task_manager_health` | `read` (14 tools) |
| `dashboards-analyst` | `list_data_views`, `describe_data_view`, `create_data_view`, `update_data_view`, `resolve_short_url`, `create_short_url`, `export_saved_objects`, `import_saved_objects`, `find_saved_objects`, `get_saved_object`, `get_default_data_view`, `upsert_runtime_field`, `set_default_data_view`, `search_dashboards`, `get_dashboard`, `create_dashboard`, `add_panel`, `update_panel`, `add_esql_metric_panel`, `add_esql_table_panel`, `add_esql_xy_panel`, `search_visualizations`, `get_visualization`, `create_visualization`, `update_visualization`, `add_library_panel` | `write` (26 tools — the default selection) |

The `fleet-admin` persona has no list: it is the whole `fleet` toolbox at
`destructive`, which `KIBANA_MCP_TOOLBOXES=fleet` already says exactly.

#### Your own lists: the list file

`KIBANA_MCP_TOOL_LISTS_FILE` names a [TOML](https://toml.io) file of your
own lists; `@name` then finds them beside the built-in ones. The file is read
when the server starts, and only when `KIBANA_MCP_TOOLS` is set (otherwise it
is ignored, with one line on stderr saying so). Give an absolute path: a
leading `~` is expanded, but a relative path resolves against the directory
the MCP client starts the server in, which is often not your shell's.

```toml
version = 1

[lists.alert-triage]
tools = ["list_alert_rules", "get_alert_rule", "update_alert_rule",
         "enable_alert_rule", "disable_alert_rule"]

[lists.space-builder]
tools = ["list_spaces", "create_space"]
```

```bash
KIBANA_MCP_TOOL_LISTS_FILE=/etc/mcp-for-kibana/lists.toml
KIBANA_MCP_TOOLS=@dashboards-analyst,@space-builder
```

**The format is a compatibility contract**, versioned like the environment
variables themselves: a version-1 file keeps working, unchanged, on every
later release that reads version 1, and a change that would break one gets a
new `version` number. Version 1 is exactly:

- `version` — required, the integer `1`.
- `lists` — required, at least one table `[lists.<name>]`. A name is
  lowercase letters, digits, `-` and `_`, starting with a letter or digit
  (it must survive the comma-separated variable after `@`).
- each list holds one key, `tools`: a non-empty array of tool names.
  Surrounding spaces are trimmed and a repeated name counts once. A list
  cannot name another list (`@...`); write out its tools.

Validation is strict, and every problem fails startup with an error that
starts `KIBANA_MCP_TOOL_LISTS_FILE='<path>':` and names the fix: a file that
cannot be read (missing, unreadable, not UTF-8) or is not valid TOML, a
missing or different `version`, any key not listed above, a list with no
tools or with a tool entry that is not a string, a name outside the grammar,
and **a list with the name of a built-in list** — refused rather than
allowed to replace it, so `@dashboards-analyst` means the same surface on
every deployment. Tool names are checked when a list is used: an unknown
one fails startup like any unknown name, with `from list '@<name>'` after it
and the `[lists.<name>]` table and file to edit.

### Coherence warnings

Some tools need another tool to be useful: `update_panel` takes a panel
index that only `get_dashboard` shows, `create_dashboard` takes field names
that only `describe_data_view` shows, and `update_alert_rule` merges into
params that only `get_alert_rule` shows. When `KIBANA_MCP_TOOLS` is set, the
server checks the published tools against these relationships (followed
transitively) and, if any are missing, logs **one** line on stderr naming
each missing tool and the tools that rely on it. For example,
`KIBANA_MCP_TOOLS=list_data_views,describe_data_view,update_panel` logs:

```text
KIBANA_MCP_TOOLS leaves out 2 tool(s) that published tools rely on: get_dashboard (relied on by update_panel), search_dashboards (relied on by get_dashboard); add them to KIBANA_MCP_TOOLS, or ignore this if the model gets those inputs another way
```

The server still starts — same posture as a listed tool above the tier —
because a model can sometimes get the input elsewhere (an id the user
pastes, a dashboard it created in the same session). Tools hidden by the
tier are not checked, and selections made with `KIBANA_MCP_TOOLBOXES` alone
are not checked. Every built-in list passes the check.

The relationships are data with one owner, `DEPENDENCIES` in
`src/kibana_mcp/tool_lists.py`: an edge from tool A to tool B means A takes
an input that only B shows the model — an index or field name, the current
state an in-place edit merges into, an export handle, or an existing
object's id that A's not-found error tells the model to look up with B. An
edge always points at a tool of the same or a lower tier, so the warning's
fix is always "list it". A unit test checks that every name in the map is a
real tool.

## Server instructions

At connection the server sends MCP *instructions*, a short text a client may
show the model before any tool call. They follow what is published after the
toolbox, tier and tool selection: the dashboards walk-through
(`list_data_views` → `describe_data_view` → `create_dashboard`) is sent only
when all three of those tools are published, as in the default selection;
any other surface gets a generic text that names no tool, so the model is
never pointed at a tool it cannot call.

## Space targeting

Every tool in the `dashboards`, `data-management`, `alerting`, `cases`, and
`security-detections` toolboxes takes an
optional `space` parameter that targets a Kibana space by id — see the
[Tool reference](tools.md#space-targeting) for
the parameter's shape, the result echo, and the error/URL/short-URL
consequences of setting it. This section covers the deployment-level
mechanics: what the server itself validates, and what it deliberately
doesn't.

- **`"default"` equivalence, with one exception.** Passing `space="default"`
  targets the default space, same as omitting `space` — **except** under
  the pinned-URL restriction below, where omitting `space` still works but
  `space="default"` is refused exactly like any other explicit value. The
  equivalence does not survive pinning.
- **Space-pinned base URLs.** If `KIBANA_URL` (the URL the server itself
  connects on) or `KIBANA_PUBLIC_URL` (if set — the URL used to build
  clickable links) already points at a specific space (a path ending in
  `/s/<space-id>`), the `space` parameter cannot be used with that
  deployment at all: any call that sets `space` is rejected before it makes
  a single HTTP request, naming whichever URL is pinned. A pinned
  deployment that never passes `space` behaves exactly as it did before this
  parameter existed. Detection is **syntactic only** — it checks whether the
  URL's path (trailing slashes stripped) ends in a segment shaped like
  `/s/<space-id>`; a reverse-proxy base path that happens to end the same way
  (e.g. `.../apps/s/kibana`) false-positives, and a proxy that *rewrites* an
  unpinned URL to a space-pinned one server-side is invisible to this check.
  Both are accepted limitations, not bugs to report.
- **Spaces-read privilege.** Targeting a space validates its existence via a
  Kibana spaces-read call before the tool's own work runs (fail-closed: see
  the [Tool reference](tools.md#space-targeting)
  for why). This means the API key needs privilege to **read spaces**, not
  only to perform the tool's own action — a key that can write dashboards in
  a space but cannot read spaces gets an auth error naming this precheck, on
  a call whose underlying write would otherwise have succeeded.
- **The guidance names tools that may not be enabled.** A nonexistent-space
  error points the model at `list_spaces` and `create_space` as the next
  step — those tools exist only when `KIBANA_MCP_TOOLBOXES` includes
  `platform-admin`. With a narrower toolbox selection, the guidance still
  names them even though the model has no way to call them.
- **Validate-then-act window.** Space existence is checked once, at the
  start of the call — there is no check that it still exists at the moment
  the write actually happens. A space deleted in that window lets the write
  land in an invisible orphan namespace with an ordinary success response;
  nothing after the fact detects or reports this.
- **Per-call latency cost, unamortized.** kibana-py re-validates a space on
  every request against a per-client cache (5-minute TTL), but this server
  builds a fresh client per tool call — so that cache never has a chance to
  hit across calls. Every scoped call pays one full extra Kibana round trip
  (the space-existence check) beyond what the same call costs without
  `space`.

## Loading credentials from a file

Set `KIBANA_MCP_ENV_FILE` to the path of a `KEY=value` file and the server reads
it at startup (`Settings.load()`), filling any variable not already set in the
process environment — **explicit env always wins** (it is loaded with
`setdefault`, the same precedence the test env-loader uses). The file is parsed,
never shell-sourced: plain `KEY=value` lines, `#` comments and blank lines
skipped, values taken verbatim (no quotes). A path that is set but unreadable is
a hard startup error rather than a silent skip.

Its purpose is to end credential-copying toil in **local development**. The local
stack's API key is ephemeral — `scripts/stack.sh down` wipes Elasticsearch's
security index, so every recreate mints a new key into the machine-written
`elastic-start-local/.env.seed`. Point a launcher (e.g. LM Studio's `~/.lmstudio/mcp.json`) at that
file instead of pasting the key:

```json
{
  "command": "uv",
  "args": ["--directory", "/ABSOLUTE/PATH/TO/mcp-for-kibana", "run", "mcp-for-kibana"],
  "env": {
    "KIBANA_MCP_ENV_FILE": "/ABSOLUTE/PATH/TO/mcp-for-kibana/elastic-start-local/.env.seed",
    "KIBANA_MCP_TIER": "write"
  }
}
```

`elastic-start-local/.env.seed` names the key `KIBANA_TEST_API_KEY`; the loader bridges that name to
`KIBANA_API_KEY` **only for values read from the file** (never for an ambient env
var, so a stray exported test key can't silently credential a real run). Now
`stack.sh seed` is the only place the key lives — the launcher config never
changes again. For a **real deployment** keep passing `KIBANA_URL` /
`KIBANA_API_KEY` directly (or, over HTTP, per-request `Authorization`); the
env-file is a dev convenience, not a secrets-management mechanism.

## `KIBANA_MCP_ALLOW_ENV_KEY_HTTP` explained

mcp-for-kibana is stateless and multi-tenant-aware over HTTP: every request is
meant to carry the *caller's own* Kibana API key via
`Authorization: ApiKey <key>`, so that Kibana's RBAC and audit log see and
enforce the real, individual identity behind each write — not a shared
service identity.

`KIBANA_API_KEY` is also read from the environment (bare or as
`KIBANA_MCP_API_KEY`). In **stdio** mode this is the normal, expected path —
one process per user, so the env key *is* that user's identity, and it's
used unconditionally as the fallback when no per-request header applies
(there's no "per request" in stdio; it's one long-lived session per
process).

In **HTTP** mode, the same env var, if left enabled by default, would turn
into a shared credential silently backing every caller who didn't (or
couldn't) send their own `Authorization` header — defeating the entire
point of per-request auth. So `resolve_api_key`/`_env_key_fallback`
(`server.py`) ignore `KIBANA_API_KEY` in HTTP mode unless
`KIBANA_MCP_ALLOW_ENV_KEY_HTTP=true` is explicitly set. Set that flag only
if you deliberately want a fallback identity for callers that don't send
their own key (e.g. a trusted internal caller, or a transitional
deployment) — understand that every request without its own
`Authorization` header will then act as that one shared key.

## OpenTelemetry

The server emits an OpenTelemetry span per tool call (FastMCP instruments this
itself). Export is **additive and off by default**: with `KIBANA_MCP_OTEL_ENABLED`
unset, spans are non-recording — no cost, and the OpenTelemetry SDK is never
imported. To turn it on:

```sh
pip install 'mcp-for-kibana[otel]'          # SDK + OTLP exporter
export KIBANA_MCP_OTEL_ENABLED=1
export KIBANA_MCP_OTEL_SECRET_TOKEN=... # the OTLP endpoint's Bearer token
```

Defaults target the stack's opt-in APM (`KIBANA_MCP_STACK_APM=1 scripts/stack.sh
up`, on `http://localhost:18200` — see [Testing & E2E](e2e-setup.md#local-apm-opentelemetry-backend)),
so a local run needs only the enable flag and the token. Point
`KIBANA_MCP_OTEL_ENDPOINT` at any OTLP/HTTP collector for a real deployment.
Standard `OTEL_*` env vars (e.g. `OTEL_RESOURCE_ATTRIBUTES`) are also honored by
the SDK. The `.env.otel.example` template documents the full set.

Spans leave the process 500 ms after they end (the SDK's own default is 5 s;
set `OTEL_BSP_SCHEDULE_DELAY` to choose another delay), and the queue is
flushed when the server exits, on SIGTERM too. A stdio client can stop its
server within half a second of the last call (Claude Code sends SIGINT, then
SIGTERM, then SIGKILL), and the 5 s delay lost the last spans of a session
(D44). The SIGTERM flush waits at most 2 s, so an unreachable collector delays
the exit by that much rather than by the exporter's 10 s timeout, and a second
SIGTERM ends the process at once. The HTTP transport exits through the same
flush: uvicorn re-raises the SIGTERM it caught after its graceful shutdown.

A stdio server has no request headers to carry trace context, and Claude Code
puts none in the MCP request `_meta`; its child processes inherit only a
`TRACEPARENT` environment variable. With `KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV=1`
the server takes that variable as the parent of all its spans, so they land in
the caller's trace.

The `otel` extra also pulls the OTLP **gRPC** exporter — not for our export (we
use HTTP) but because kibana-py 0.4.2's observability import requires it once the
OpenTelemetry SDK is present. **Caveat:** if `opentelemetry-sdk` ends up installed
*without* that gRPC exporter (e.g. dragged in by an unrelated package), kibana-py's
import breaks and the server fails to start *even with OTEL disabled*. Install the
`otel` extra (which bundles both) rather than the bare SDK, and be aware of this
when adding OpenTelemetry to an existing environment.

See [Deployment](deployment.md#per-request-auth-model) for the request-flow
picture of this.
