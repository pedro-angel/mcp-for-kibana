# mcp-for-kibana profiles

Preconfigured **profiles** — one per persona — so you can point an LLM agent at the
slice of Kibana that matches your job instead of hand-assembling config. A profile
is just a choice on two axes:

- **Toolboxes** (`KIBANA_MCP_TOOLBOXES`) — *what's visible*: which groups of tools
  the server advertises. Mirrors Kibana's own solution views.
- **Tier** (`KIBANA_MCP_TIER` = `read` | `write` | `destructive`) — *what you can
  do*: a cap that hides every tool above it.

> **Toolbox + tier are curation, not a security boundary.** The real boundary is
> the Kibana **API key** you supply: the server acts only within that key's RBAC.
> Scope the key to least privilege (and, over HTTP, per caller).

**Why profiles and not "enable everything"?** Model tool-selection accuracy drops
sharply past ~20 tools, and small/local models degrade earlier — so a focused
surface is *more* capable, not less. Each profile below lists its tool count.

## How to use

1. Pick the profile that matches your job.
2. Copy its `.mcp.json` into your MCP client config (Claude Desktop, LM Studio, `claude mcp add …`).
   See [`docs/examples/mcp.json`](../docs/examples/mcp.json) for the base shape and the HTTP variant.
3. Set `KIBANA_URL` and `KIBANA_API_KEY` (and, for stdio, the `--directory` path to your checkout).

Status legend: 🟩 **live** — boots today · ⬜ **planned** — documented target; the
server fail-fasts on an unbuilt toolbox, so a planned profile has no runnable
snippet until its toolboxes ship (it graduates to a `.mcp.json` then).

## 🟩 Live profiles

The first two prove the two-axis model: they differ in **both** their toolbox set
(read-only-explorer adds `platform-health`; dashboards-analyst raises the tier)
and their tier. `fleet-admin` adds the `fleet` toolbox (read-first v1).

### [`read-only-explorer.mcp.json`](./read-only-explorer.mcp.json) — 9 tools · `read`
The small-/local-model reference. Explore existing dashboards and data views and
check stack health; change nothing. Toolboxes: `dashboards` (read) +
`platform-health` + `data-management` (read). Tools: `list_data_views`,
`describe_data_view`, `resolve_short_url`, `search_dashboards`, `get_dashboard`,
`export_saved_objects`, `get_kibana_status`, `get_kibana_stats`, `get_task_manager_health`.
*Persona:* an analyst or on-device model browsing what's already in Kibana.

### [`dashboards-analyst.mcp.json`](./dashboards-analyst.mcp.json) — 16 tools · `write`
Turn plain-English asks into real Lens visualizations and dashboards (idempotent —
re-creating the same title updates rather than duplicates). Toolboxes: `dashboards`
+ `data-management`. Adds `create_dashboard`, `create_visualization`, `add_panel`,
`update_panel`, `create_data_view`, `create_short_url` to the read tools. This is
the project's MVP default. *Persona:* data/BI analyst, dashboard builder.
*(Deletion tools exist but are `destructive`-tier — raise `KIBANA_MCP_TIER` to
`destructive` only if you want the model to delete dashboards/panels.)*

### [`fleet-admin.mcp.json`](./fleet-admin.mcp.json) — 35 tools · `destructive`
Administer the Elastic Agent fleet: agents, agent + integration policies, enrolled
integrations (EPM), enrollment keys (metadata only), outputs, and full CRUD on
policies and outputs. Toolbox: `fleet` (read + write + destructive). *Persona:*
a platform/fleet admin managing agent and integration state at scale.
*(Enrollment-key and output secrets are never returned.)*

## ⬜ Planned profiles (drive the [roadmap](../docs/roadmap.md))

Documented targets; each graduates to a runnable snippet when its toolboxes land.
Profiles are organised persona-first — the question is "what does this job need
to see?", not "what does this API namespace expose" — so a profile ships only
once every toolbox it names is built and contract-tested. What is built, what is
deferred and why is in the [roadmap](../docs/roadmap.md); open work is tracked as
GitHub issues.

| Profile | Toolboxes (intended) | Tier | Prerequisites | Unblocked by |
|---|---|---|---|---|
| **observability-sre** | observability, alerting, cases | write | Platinum (SLOs); LLM connector (obs-AI) | Waves 2–3 |
| **soc-analyst** | security-detections, cases, security-ai | write | LLM connector (security-AI) | Waves 2–3 |
| **platform-admin** | platform-admin, data-management | **destructive** | admin API key; HTTP | Wave 4 |

**Future variants** (documented, not first-cut): `detection-engineer`,
`soc-responder` (+ endpoint response, destructive), `threat-hunter / insider-risk`,
`logs-engineer` (+ Streams, tech-preview), `developer-automation` (Agent Builder /
Workflows).

## Build order (leverage-first)

1. **Wave 1** — ✅ `platform-health` (read-only, live) · ✅ `data-management` (live — extracted `data_views` from `dashboards`; grew *read-only-explorer* to 8 and *dashboards-analyst* to 11; `saved_objects` export/import deferred, see the packaging design).
2. **Wave 2** — `alerting` (#10) · `cases` (both cross-persona).
3. **Wave 3** — `observability` · `security-detections` + `security-ai` (unlock the SRE & SOC profiles).
4. **Wave 4** — `fleet` · `platform-admin` · `security-response` (☠️) · `security-entity-analytics` · `streams` (preview) · `ai-automation`.

**Not planned** (kibana-py 0.3.1 gaps): ML job/model management, the Search
solution, and scheduled Reporting have no client surface to wrap.
