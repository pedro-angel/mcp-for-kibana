# platform-health — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#platform-health-toolbox · Enforcement: named per section below

## Purpose & persona

Instance-level Kibana health, in three concise reads: overall status,
runtime resource usage, and Task Manager health. Serves the "look but don't
touch" persona — an analyst or a small/local model that needs to know
whether the stack is healthy, without parsing Kibana's large raw
status/stats/health payloads itself. One of the three toolboxes behind the
`read-only-explorer` profile (with `dashboards` and `data-management`, all
at `read` tier); composes unchanged into any other profile that wants a
health check alongside its other toolboxes.

## Surface

Three tools, all `read` tier: `get_kibana_status`, `get_kibana_stats`,
`get_task_manager_health`. Zero `write`, zero `destructive` — no write or
destructive registration path exists, so there is nothing for a tier cap to
hide; all three stay visible even at `KIBANA_MCP_TIER=read`, the most
restrictive setting. Full per-tool descriptions and example return shapes:
docs/tools.md#platform-health-toolbox.

What the tier hides is not tools but *fields*: each tool summarizes a
materially larger raw Kibana response into a small, fixed field set — that
summarization is the toolbox's value, not an incidental side effect (see
Behavioral guarantees).

## Behavioral guarantees

- **Summarize, never pass through.** Each tool returns a small, fixed-shape
  model, not the raw response body:
  - `get_kibana_status` returns `overall_level`, `overall_summary`,
    `version`, `unhealthy` — `{name, level, summary}` entries covering only
    core services/plugins whose level is **not** `"available"`. Healthy
    services are dropped entirely, not listed as healthy — a fully healthy
    stack returns `unhealthy: []`.
  - `get_kibana_stats` returns exactly `heap_used_bytes`,
    `heap_total_bytes`, `heap_size_limit_bytes`, `event_loop_delay_ms`,
    `concurrent_connections`. The rest of Kibana's runtime metrics tree
    (process/OS load, GC detail, etc.) is never surfaced.
  - `get_task_manager_health` returns exactly `status`, `timestamp`,
    `last_update`. Task Manager's larger internal stats tree is never
    surfaced.
- **No space targeting.** None of the three tools accept or echo a `space`
  parameter. This is one of the two toolboxes (with `platform-admin`) that
  docs/tools.md's space-targeting section names as managing instance-global
  state: process health, runtime resource usage, and Task Manager status do
  not vary by space, so reporting them per-space would misrepresent global
  state as isolated. Every call resolves through the gateway PORT's
  (`ports/gateway.py`) no-space path.
- **No parameters, so no input-shaped failure modes.** All three tools take
  zero arguments — no id to mistype, no filter to misapply, no space to
  fail closed on. The only failure path is the one every toolbox shares: a
  gateway-side failure (auth, unavailable, connectivity) becomes a
  plain-text tool error carrying the gateway's message, never a raw stack
  trace or response body. This toolbox defines no error-guidance strings of
  its own — there is no bad input to guide a caller away from.
- **No consequential-action machinery applies.** Every tool is
  `readOnlyHint=true`, `openWorldHint=false`, a single call with no side
  effect. Guard-before-gateway ordering, read-modify-write flows, confirm
  gates, handle-based flows, and derived identities — load-bearing
  elsewhere in the surface — do not apply here.

## Invariants

- MUST register exactly 3 tools — `get_kibana_status`, `get_kibana_stats`,
  `get_task_manager_health` — and MUST NOT register any write- or
  destructive-tier tool.
- MUST accept zero parameters on all 3 tools.
- MUST filter `get_kibana_status`'s `unhealthy` list to entries whose level
  is not `"available"`; a fully healthy stack MUST yield `unhealthy: []`.
- MUST expose on `get_kibana_status` exactly: `overall_level`,
  `overall_summary`, `version`, `unhealthy` (each entry: `name`, `level`,
  `summary`) — nothing else from the raw status tree.
- MUST expose on `get_kibana_stats` exactly: `heap_used_bytes`,
  `heap_total_bytes`, `heap_size_limit_bytes`, `event_loop_delay_ms`,
  `concurrent_connections`.
- MUST expose on `get_task_manager_health` exactly: `status`, `timestamp`,
  `last_update`.
- MUST NOT accept or echo a `space` parameter on any tool; all three always
  report instance-global state.
- MUST set `readOnlyHint=true` and `openWorldHint=false` on all 3 tools.
- MUST keep all 3 tools visible at every `KIBANA_MCP_TIER` setting,
  including `read`.
- MUST translate gateway-side failures into plain-text tool errors — no
  secrets, no stack traces, no raw response bodies reaching the caller.
- MUST leave the tool sets of every other enabled toolbox unchanged when
  `platform-health` is enabled alongside them.

## Deliberate exclusions & caveats

- **Summarization is the design, not a stopgap.** The toolbox exists so a
  small model gets a concise health signal instead of a deep metric tree; a
  rewrite must not "improve" these tools by returning the raw bodies.
- **The read-only shape is final, not v1.** No roadmap entry records a
  planned write or destructive tool (e.g. restarting a service, clearing a
  Task Manager error state) — health reporting has no natural write
  operation, so the omission is structural, not deferred.
- **No space parameter is structural, not an oversight.** Recorded
  alongside `platform-admin` in docs/tools.md's space-targeting section as
  managing instance-global objects; a `space` parameter here would
  contradict what the underlying signals are.
- **Out of scope for this file:** `get_alerting_health` (`alerting`
  toolbox) reports the same instance-wide-regardless-of-space shape, noted
  for context only — its contract lives in that toolbox's own document.

## Enforcement

- `tests/unit/toolboxes/test_platform_health.py` — pins the 3-tool set,
  `readOnlyHint=true` on all three, the returned field shapes, and the
  `unhealthy`-filtering behavior both ways (empty on a healthy fake,
  populated with a degraded entry on an unhealthy one).
- `tests/unit/adapters/test_platform_health_gateway.py` — pins the
  raw-to-domain-model translation at the gateway PORT boundary for all
  three methods against live-shaped raw bodies, including that healthy
  services are dropped and only non-`"available"` entries survive.
- `tests/unit/test_server_assembly.py` —
  `test_platform_health_toolbox_boots_read_only` pins the exact 3-tool set
  at the default tier; `test_platform_health_combines_with_dashboards` and
  `test_read_only_explorer_profile_is_nine_read_tools` pin additive
  composition with other toolboxes.
- `tests/contract/test_gateway_contract.py` — `test_kibana_status_live`,
  `test_kibana_stats_live`, `test_task_manager_health_live` pin all three
  gateway methods against a real, seeded Kibana 9.4.3 stack.
- No e2e-replay or live-model e2e transcript is pinned to this toolbox — it
  has no error-guidance strings and no multi-step flow to
  regression-protect at that tier; coverage is the unit and contract tiers
  above, certified through the DoD gate's `unit_green` and `contract_green`
  criteria (`dod.config`). No ephemeral-stack gate applies (unlike
  `streams_ephemeral` / `fleet_ephemeral`) since nothing here is destructive.
