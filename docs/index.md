# mcp-for-kibana

An [MCP](https://modelcontextprotocol.io) server for [Kibana](https://www.elastic.co/kibana):
composable toolboxes over a hexagonal core, powered by
[kibana-py](https://github.com/pedro-angel/kibana-py).

> **Disclaimer:** This is an independent, community-driven project and is **not**
> officially affiliated with, endorsed by, or supported by Elastic N.V. or any of
> its subsidiaries. "Kibana" and "Elasticsearch" are trademarks of Elastic N.V.
> This project is provided "as is", without warranty of any kind. See the
> [LICENSE](https://github.com/pedro-angel/mcp-for-kibana/blob/main/LICENSE).

> **Technical Preview:** the dashboards toolbox is built entirely on Kibana
> 9.4's new public Dashboards API and Visualizations API, both marked
> `x-state: Technical Preview` by Elastic — their request/response shapes may
> still change in a later Kibana release without notice, and they carry no
> support SLA. This server targets **Kibana 9.4+ only**; earlier versions
> don't expose these APIs publicly at all.

## Developing

```bash
git clone https://github.com/pedro-angel/mcp-for-kibana && cd mcp-for-kibana
make setup   # uv sync + git hooks
make test    # unit suite
make help    # everything else, self-documented
```

## What it does

Lets an LLM work with Kibana through a small, reliable, purpose-built tool
surface instead of ~610 raw endpoints — 133 tools across 10 composable toolboxes
enabled per deployment. The flagship **dashboards** toolbox lets a user describe
the chart they want, and the server translates a simple declarative spec into
real Kibana Lens visualizations and dashboards via the modern Dashboards API
(Kibana 9.4+). The rest span data management, alerting, cases, security
detections, fleet, streams, observability, and platform admin/health.

## The four design pillars

- **Composable toolboxes** — groups of tools enabled/disabled per deployment
  via configuration; small local models get a small surface, big models can
  get more. See [Configuration](configuration.md#toolbox-selection).
- **Hexagonal core** — pure domain logic (spec → Lens translation) isolated
  from both the MCP adapter and the kibana-py gateway; every layer
  independently testable. See [Architecture](architecture.md).
- **Two run modes, one codebase** — stdio for a local single user,
  streamable HTTP in a container for multiple users. Stateless: each
  request carries the caller's own Kibana API key, so Kibana RBAC and
  audit logging stay per-user. No multi-tenancy. See
  [Deployment](deployment.md).
- **Tiered write safety** — every tool is classified read / write /
  destructive; deployment config caps the tier, and tools above the cap are
  never advertised to the model or callable by it (registered like every
  other tool, then hidden via FastMCP's visibility API — not just denied at
  call time). See [Tool reference](tools.md#tiers-and-annotations).

## Status

**v0.1.0 — 10 toolboxes, 133 tools, live-tested.** Every tool is classified
read / write / destructive and contract-tested against a live Kibana 9.4.3, and
the server is packaged as a stdio and container-runnable server. The flagship
path: an LLM goes from a plain-English request ("average ticket price by carrier,
last 7 days") to a real Kibana dashboard through the whole read → validate →
translate → write flow.

The final MVP gate **passed 3/3 on 2026-07-10**: a real local LLM in LM
Studio (`openai/gpt-oss-20b`) drove the full flow autonomously — plain
English in, correct live dashboard out, verified against the stored Lens
payload and cleaned up — three consecutive runs (16s/9s/7s wall). See
[Testing & E2E](e2e-setup.md) for the harness and one-time LM Studio setup.

## Quick links

| I want to... | Go to |
|---|---|
| Get a first dashboard running in ~10 minutes | [Getting started](user-guide.md) |
| See what the 133 tools (10 toolboxes) take and return | [Tool reference](tools.md) |
| Set every environment variable | [Configuration](configuration.md) |
| Run it in Docker / over HTTP | [Deployment](deployment.md) |
| Understand the hexagonal layers and VizSpec→Lens translation | [Architecture](architecture.md) |
| Run the test matrix, including the LM Studio E2E gate | [Testing & E2E](e2e-setup.md) |
| Understand why it is built this way | [Architecture](architecture.md) |

## Quick start

```bash
export KIBANA_URL=https://your-kibana.example.com
export KIBANA_API_KEY=<your Kibana API key>
uvx mcp-for-kibana
# or from a local checkout:
uv run mcp-for-kibana
```

Runs stdio by default — one process per user, talking to Kibana as that
user's own API key. See [Configuration](configuration.md) for every
setting.
