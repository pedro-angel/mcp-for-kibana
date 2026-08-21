# mcp-for-kibana

An [MCP](https://modelcontextprotocol.io) server for [Kibana](https://www.elastic.co/kibana):
composable toolboxes over a hexagonal core, powered by [kibana-py](https://github.com/pedro-angel/kibana-py).

> **Disclaimer:** This is an independent, community-driven project and is **not**
> officially affiliated with, endorsed by, or supported by Elastic N.V. or any of
> its subsidiaries. "Kibana" and "Elasticsearch" are trademarks of Elastic N.V.
> This project is provided "as is", without warranty of any kind. See [LICENSE](LICENSE).
> The name "mcp-for-kibana" uses "Kibana" referentially — this is an MCP server
> *for* Kibana, per Elastic's trademark guidelines on referential use — and does
> not imply origin or endorsement.

The distribution is `mcp-for-kibana`; the Python import package is `kibana_mcp`.

> **Technical Preview:** the dashboards toolbox is built entirely on Kibana
> 9.4's new public Dashboards API and Visualizations API, both marked
> `x-state: Technical Preview` by Elastic — their request/response shapes may
> still change in a later Kibana release without notice, and they carry no
> support SLA. This server targets **Kibana 9.4+ only**; earlier versions
> don't expose these APIs publicly at all.

## Status

**v0.1.0 — 10 toolboxes, 133 tools, live-tested.** The server exposes 133 tools
across 10 composable toolboxes (dashboards, data management, alerting, cases,
security detections, fleet, streams, observability, platform admin, and platform
health), each classified read / write / destructive and contract-tested against a
live Kibana 9.4.3. It's packaged as a stdio and container-runnable server. See the
[Tool reference](#tool-reference) below.

The flagship path is **dashboards from plain English**: an LLM goes from a request
("average ticket price by carrier, last 7 days") to a real Kibana dashboard through
the read → validate → translate → write flow. That path passed its end-to-end gate
**3/3 on 2026-07-10**: a real local LLM in LM Studio (`openai/gpt-oss-20b`) drove it
autonomously — plain English in, correct live dashboard out, verified against the
stored Lens payload and cleaned up — three consecutive runs (16s/9s/7s wall). See
[`docs/e2e-setup.md`](docs/e2e-setup.md) for the one-time LM Studio setup
(permission toggles + API token) and `tests/e2e/test_lmstudio.py` for the
harness. Model note: gpt-oss-20b's parser-enforced tool grammar made it
3/3 reliable; qwen2.5-coder-14b completes the flow too but corrupts its
tool-call markers about one run in three.

## What it does

Lets an LLM work with Kibana through a small, reliable, purpose-built tool surface
instead of ~610 raw endpoints — 133 tools grouped into 10 composable toolboxes you
enable per deployment. The flagship **dashboards** toolbox lets a user describe the
chart they want, and the server translates a simple declarative spec into real
Kibana Lens visualizations and dashboards via the modern Dashboards API (Kibana
9.4+). The other toolboxes cover data management, alerting, cases, security
detections, fleet, streams, observability, and platform admin/health.

Design pillars:

- **Composable toolboxes** — groups of tools enabled/disabled per deployment via
  configuration; small local models get a small surface, big models can get more.
- **Hexagonal core** — pure domain logic (spec → Lens translation) isolated from both
  the MCP adapter and the kibana-py gateway; every layer independently testable.
- **Two run modes, one codebase** — stdio for a local single user, streamable HTTP in
  a container for multiple users. Stateless: each request carries the caller's own
  Kibana API key, so Kibana RBAC and audit logging stay per-user. No multi-tenancy.
  Exception: saved-objects export handles live in one server-side directory shared
  by all callers — a handle is a bearer token for that export, and the 20-file
  retention cap is global, so concurrent HTTP callers can expire each other's handles.
- **Tiered write safety** — every tool is classified read / write / destructive;
  deployment config caps the tier, and tools above the cap are never advertised
  to the model or callable by it (registered like every other tool, then hidden
  via FastMCP's visibility API — not just denied at call time).

## Requirements

- Python 3.12+ (the server itself is pure Python and OS-independent)
- For the dev workflow (Makefile, scripts, local stack): a POSIX shell, GNU make,
  and Docker with ~5 GB free memory for the Elasticsearch + Kibana stack.
  Windows: use WSL2.
- A Kibana 9.4+ deployment (or the bundled disposable local stack).

## Quick start

> **New here?** The [User Guide](docs/user-guide.md) walks you from zero to
> your first talked-into-existence dashboard in ~10 minutes, on a disposable
> local Kibana with sample data.

```bash
export KIBANA_URL=https://your-kibana.example.com
export KIBANA_API_KEY=<your Kibana API key>
uvx mcp-for-kibana          # once published to PyPI
# from a local checkout today:
uv run mcp-for-kibana
```

Runs stdio by default — one process per user, talking to Kibana as that
user's own API key. See [Configuration](#configuration) for every setting.

### Claude Code

```bash
claude mcp add kibana --env KIBANA_API_KEY=<your key> -- uv --directory /path/to/checkout run mcp-for-kibana
```

### LM Studio

Add an entry to `~/.lmstudio/mcp.json` — see
[`docs/examples/mcp.json`](docs/examples/mcp.json) for a stdio and an HTTP
example. For the full local E2E setup (real model, real Kibana, real
dashboard), see [`docs/e2e-setup.md`](docs/e2e-setup.md).

### Any other MCP client (streamable HTTP)

Point the client at `http://<host>:8000/mcp` with header
`Authorization: ApiKey <your Kibana API key>` — see [Docker](#docker) below
for running the HTTP server.

## Tool reference

**133 tools across 10 toolboxes**, each tool classified by tier — read / write /
destructive (64 / 40 / 29). Enable toolboxes per deployment with
`KIBANA_MCP_TOOLBOXES`; cap the tier with `KIBANA_MCP_TIER`. A tool above the cap
is not merely denied at call time — it never appears in the tool list (registered,
then hidden via FastMCP's visibility API).

| Toolbox | Tools | What it does |
|---|--:|---|
| `dashboards` | 11 | Build Lens visualizations + dashboards from a declarative spec — the flagship plain-English → dashboard path. |
| `data-management` | 10 | Data views + short URLs (the datasets you visualize). |
| `alerting` | 11 | Alerting rules and connectors. |
| `cases` | 6 | Kibana Cases: create, comment, update status. |
| `security-detections` | 25 | Detection rules, exception lists, value lists. |
| `fleet` | 35 | Fleet agents, agent/package policies, outputs, enrollment. |
| `streams` | 12 | Streams list / summary / ingest config + processing (Tech Preview). |
| `observability` | 10 | SLOs, alerts, and observability reads. |
| `platform-admin` | 10 | Spaces, roles, upgrade readiness. |
| `platform-health` | 3 | Cluster and Kibana status / health reads. |

A tier includes everything below it: `read` registers only read tools, `write`
(the default) adds write, `destructive` adds destructive. The default toolbox set
is `dashboards,data-management` (coupled — building a viz needs a data view).

See the **[full per-tool reference](docs/tools.md)** for every tool's arguments and
return shape.

## Configuration

All settings are read once, in `kibana_mcp.config.Settings`, and are set via
env vars (prefix `KIBANA_MCP_` for the deployment-shaped ones; `KIBANA_URL`,
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
| `KIBANA_MCP_ENV_FILE` | (none) | Path to a `KEY=value` file loaded at startup (setdefault — explicit process env wins) so a launcher can point at the machine-written `elastic-start-local/.env.seed` instead of hard-copying the ephemeral dev key. Additive and off by default; see [configuration docs](docs/configuration.md#loading-credentials-from-a-file). |
| `KIBANA_MCP_OTEL_ENABLED` | `false` | Export an OpenTelemetry span per tool call. Off by default (spans are non-recording, no SDK imported); needs the `otel` extra. `KIBANA_MCP_OTEL_ENDPOINT` / `KIBANA_MCP_OTEL_SECRET_TOKEN` / `KIBANA_MCP_OTEL_SERVICE_NAME` tune the OTLP export — see [configuration docs](docs/configuration.md#opentelemetry). |

## Docker

The container always runs HTTP transport (`KIBANA_MCP_TRANSPORT=http` is
baked into the image), stateless, on port 8000. `KIBANA_API_KEY` is not
needed at the container level: HTTP mode expects each caller to send their
own key per request, and by default ignores `KIBANA_API_KEY` even if it's
set (set `KIBANA_MCP_ALLOW_ENV_KEY_HTTP=true` to opt back into it as a
shared fallback):

```bash
docker run -p 8000:8000 -e KIBANA_URL=https://your-kibana.example.com \
  ghcr.io/pedro-angel/mcp-for-kibana

curl http://localhost:8000/mcp \
  -H 'Authorization: ApiKey <your Kibana API key>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The image runs as a non-root user (`mcp-for-kibana`, uid 10001).

## Development

```bash
make setup       # uv sync + git hooks (uv-native path)
make help        # every dev task, self-documented
```

No uv on the machine? `./bootstrap.sh` installs just the git hooks
(prek / pre-commit fallback chain).

The engineering conventions this project holds itself to are in
[CONTRIBUTING.md](CONTRIBUTING.md), and every one of them is enforced by a gate
rather than by convention alone — see the Definition-of-Done gate below. Git &
CI discipline comes from
[git-controls-starter](https://github.com/pedro-angel/git-controls-starter).

### Test matrix

Six tiers, from fastest/most-isolated to slowest/most-real:

```bash
make test              # unit — fakes only, no network; the default selection
make test-contract     # starts + seeds the docker Kibana stack itself, then -m contract
make test-e2e-replay   # replays a RECORDED model turn through a real MCP client
make test-e2e          # a REAL local model in LM Studio — see docs/e2e-setup.md
make streams-ephemeral # destructive streams path on its own throwaway stack
make fleet-ephemeral   # agent lifecycle on a throwaway 2-agent fleet stack
```

Everything except `test-e2e` runs in CI. The two ephemeral tiers own their
stack's whole lifecycle (up → test → down) because their paths are destructive:
they delete every wired stream, or mutate real enrolled agents, so they cannot
share the dev stack. They also cannot run *beside* it — see
`scripts/fleet_ephemeral.sh` for the measured memory headroom.

**On the two e2e tiers.** They are not redundant, and one is not a substitute
for the other:

| | `test-e2e` | `test-e2e-replay` |
|---|---|---|
| Who picks the tool calls | a real local LLM | a recorded transcript |
| Server, MCP transport, Kibana, ES | real | real |
| Runs in CI | no — needs a GPU + loaded model | **yes** |
| Proves a model can *reason* to the calls | **yes** | no |
| Proves the model-facing surface still works | yes | **yes, per-PR** |

The replay tier exists because the surface a model actually touches was
otherwise certified only on a maintainer's laptop. It replays a turn recorded
from a live `gpt-oss-20b` run — including the three failures that run recovered
from — and asserts the tools still exist, that arguments a real model produced
still validate against the live input schemas, and that the **error guidance
survives**. That guidance is load-bearing: a model has no other way to learn
your data's field names, and degrading `"field 'price' does not exist … did you
mean 'Carrier'?"` to a bare 400 breaks self-correction for every LLM using this
server. Four mutation tests confirm the tier fails when each guard is broken.

(Raw commands underneath: `uv run pytest [-m contract|-m e2e|-m e2e_replay]` —
the suites load `elastic-start-local/.env.seed` + `.env.local` in-process; the
Makefile is a thin facade.)

The docs site is a CI gate too: `uv sync --group docs` once, then
`uv run mkdocs build --strict` must pass with zero warnings
(`uv run mkdocs serve` to preview locally).

Before claiming a change "done" — and always before a release — run the
Definition-of-Done gate, which certifies completion from `dod.config`
instead of letting the author self-certify:

```bash
make dod         # GO/NO-GO over all declared criteria (definition-of-done.sh)
```

Every tier needing infrastructure is excluded from the default selection
(`addopts` in `pyproject.toml`), so a bare `pytest` never silently depends on a
stack. CI splits accordingly: `checks.yml` runs the content gates (lint, types,
audit, SAST, unit across Python 3.12–3.14, docs, image build+smoke), and
`integration.yml` provisions real stacks for the live tiers and certifies them
through *this same gate script* with per-tier configs in `.github/dod/` — so CI
verdicts and `make dod` cannot drift.

`make dod` needs the dev stack up for `contract`/`e2e`/`e2e_replay` and down for
the ephemeral tiers, so a plain run cannot satisfy both halves. To get a
one-shot GO, let the gate cycle the stack for you (default-off, so it never
touches infrastructure unasked):

```bash
scripts/stack.sh up && scripts/stack.sh seed
KIBANA_MCP_DOD_CYCLE_STACK=1 make dod
```

`e2e_green` is the one criterion CI cannot certify — it needs a real model. It
stays `required` in `dod.config` and `n/a` in every `.github/dod` tier, so the
local gate remains a strict superset of CI rather than CI quietly becoming the
definition of done.

## License

[Apache 2.0](LICENSE)
