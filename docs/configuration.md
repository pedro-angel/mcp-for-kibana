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

This table is kept in sync with the one in the project
[README](https://github.com/pedro-angel/mcp-for-kibana#configuration) — if you
edit one, edit both.

## Tier semantics

`KIBANA_MCP_TIER` caps the highest tool tier registered on the server —
`Tier.allowed` (`config.py`) computes a tier as "everything at or below
it" (`read` ⊂ `write` ⊂ `destructive`):

Every tool in every toolbox is tagged with its tier (across all 10 toolboxes:
64 read, 40 write, 29 destructive):

- `read` — read-only tools (64 of 133), e.g. `list_data_views`,
  `describe_data_view`, `search_dashboards`, `get_dashboard`.
- `write` (**default**) — read tools plus creates/updates (40), e.g.
  `create_dashboard`, `create_visualization`, `add_panel`, `update_panel`.
  Destructive tools do not exist as far as the model can tell.
- `destructive` — everything (29 more), including `delete_dashboard`,
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

The `otel` extra also pulls the OTLP **gRPC** exporter — not for our export (we
use HTTP) but because kibana-py 0.4.2's observability import requires it once the
OpenTelemetry SDK is present. **Caveat:** if `opentelemetry-sdk` ends up installed
*without* that gRPC exporter (e.g. dragged in by an unrelated package), kibana-py's
import breaks and the server fails to start *even with OTEL disabled*. Install the
`otel` extra (which bundles both) rather than the bare SDK, and be aware of this
when adding OpenTelemetry to an existing environment.

See [Deployment](deployment.md#per-request-auth-model) for the request-flow
picture of this.
