# mcp-for-kibana profiles

Preconfigured **profiles** — one per persona — so you can point an LLM agent at the
slice of Kibana that matches your job instead of hand-assembling config. A profile
is just a choice on two axes:

- **Toolboxes** (`KIBANA_MCP_TOOLBOXES`) — *what's visible*: which groups of tools
  the server advertises. Mirrors Kibana's own solution views.
- **Tier** (`KIBANA_MCP_TIER` = `read` | `write` | `destructive`) — *what you can
  do*: a cap that hides every tool above it.

A third axis exists for a surface no toolbox set fits: **a hand-picked tool list**
(`KIBANA_MCP_TOOLS`). When set, it replaces the toolbox choice and the server
publishes exactly the listed tools the tier allows — see
[Tool selection](../docs/configuration.md#tool-selection). A list can name a
**named list** with `@`: the server ships the personas below as built-in lists
(`@read-only-explorer`, `@dashboards-analyst`), and you can keep your own in a
TOML file — see [Named lists](../docs/configuration.md#named-lists). Mix them
with single tools, e.g. `KIBANA_MCP_TOOLS=@dashboards-analyst,create_alert_rule`.

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
snippet until its toolboxes ship (it graduates to a `.mcp.json` then) · ⬛
**withdrawn** — the capability that defines the persona needs a paid Elastic
subscription, which is out of scope, so it will never graduate.

## 🟩 Live profiles

Each profile picks its surface on whichever axis says it exactly:
read-only-explorer is a hand-picked list across three toolboxes, so its profile
names the built-in list; dashboards-analyst is two whole toolboxes at `write`
(the server's default), so its profile names the toolboxes and the tier — raising
the tier is how it gains the delete tools; `fleet-admin` is the whole `fleet`
toolbox at `destructive`. The tools of the two built-in lists are listed once, in
[Built-in lists](../docs/configuration.md#built-in-lists); a unit test keeps each
list equal to its profile's surface.

### [`read-only-explorer.mcp.json`](./read-only-explorer.mcp.json) — 11 tools · `read`
The small-/local-model reference. Explore existing dashboards, library
visualizations and data views and check stack health; change nothing. Tools: the
built-in list `@read-only-explorer` (`KIBANA_MCP_TOOLS=@read-only-explorer`) —
the read tools of `dashboards` and `data-management` plus `platform-health`.
*Persona:* an analyst or on-device model browsing what's already in Kibana.

### [`dashboards-analyst.mcp.json`](./dashboards-analyst.mcp.json) — 21 tools · `write`
Turn plain-English asks into real Lens visualizations and dashboards (idempotent —
re-creating the same title updates rather than duplicates). Toolboxes: `dashboards`
+ `data-management` at `write`: their read tools plus the creates and in-place
updates. The same 21 tools are the built-in list `@dashboards-analyst`, for
mixing with tools from other toolboxes. This is the project's MVP default.
*Persona:* data/BI analyst, dashboard builder.
*(Deletion tools exist but are `destructive`-tier — raise `KIBANA_MCP_TIER` to
`destructive` only if you want the model to delete dashboards, panels or library
visualizations.)*

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
out of scope and why is in the [roadmap](../docs/roadmap.md), which also tracks the
open work on the page itself rather than as GitHub issues.

| Profile | Toolboxes (intended) | Tier | Prerequisites | Unblocked by |
|---|---|---|---|---|
| ⬛ **observability-sre** | observability, alerting, cases | write | Platinum (SLOs) | **withdrawn** — all three toolboxes are built, but SLOs are out of scope |
| ⬛ **soc-analyst** | security-detections, cases, security-ai | write | Enterprise `.gen-ai` connector | **withdrawn** — `security-ai` is out of scope |
| **platform-admin** | platform-admin, data-management | **destructive** | admin API key; HTTP | Wave 4 |

**Future variants** (documented, not first-cut): `detection-engineer`,
`soc-responder` (+ endpoint response, destructive), `logs-engineer` (+ Streams,
tech-preview). Variants that needed entity analytics or Agent Builder / Workflows
are dropped — both are subscription-gated and out of scope.

## Build order (leverage-first)

1. **Wave 1** — ✅ `platform-health` (read-only, live) · ✅ `data-management` (live — extracted `data_views` from `dashboards`; grew *read-only-explorer* to 8 and *dashboards-analyst* to 11; `saved_objects` export/import deferred, see the packaging design).
2. **Wave 2** — `alerting` (#10) · `cases` (both cross-persona).
3. **Wave 3** — `observability` · `security-detections` (`security-ai` dropped: out of scope).
4. **Wave 4** — `fleet` · `platform-admin` · `security-response` (☠️) · `streams` (preview). (`security-entity-analytics` and `ai-automation` dropped: out of scope.)

**Not planned** (kibana-py 0.3.1 gaps): ML job/model management, the Search
solution, and scheduled Reporting have no client surface to wrap.
