# observability — behavior contract

Status: Draft v1.0 (2026-08-19) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#observability-toolbox · Enforcement: named per section below

## Purpose & persona

Read-only reconnaissance over the observability surface reachable on a Basic license: Synthetics
monitoring, Uptime app settings, APM central (agent) configuration. Answers "what's being
monitored, and how is APM configured" with no write/delete risk. Read leg of the planned
`observability-sre` persona (paired with `alerting` + `cases`), which stays planned, not shipped
(see Deliberate exclusions) — so today any deployment enabling `observability` gets exactly this
read-only slice, usable standalone by any persona wanting monitoring visibility.

## Surface

10 tools, all `read` tier (`readOnlyHint=true`, `openWorldHint=false`); 0 at `write`, 0 at
`destructive`. Nothing sits above `read`, so every tool stays visible regardless of
`KIBANA_MCP_TIER`. Full tool list, descriptions, return shapes: docs/tools.md#observability-toolbox.
What's deliberately not covered — APM telemetry, SLOs, an AI-assistant tool — is under Deliberate
exclusions, not repeated here.

## Behavioral guarantees

- **No space targeting.** None of the 10 tools accept `space`; calls always resolve through the
  gateway port's (`ports/gateway.py`) default-scope factory call — no space echo in results, no scoped
  not-found suffix, no fail-closed space check. Deliberate absence (see Deliberate exclusions), not
  an oversight to fix by copying the space-aware pattern.
- **Error translation is the shared, generic contract only.** Every domain error crosses the same
  gateway-error-translation boundary every toolbox uses, becoming a plain-text tool error with no
  secrets or stack traces. This toolbox authors no tool-specific guidance copy beyond that — a
  not-found reads as exactly "the requested id/service+environment doesn't exist," not a tailored
  suggestion.
- **Not-found is a genuine miss, never a fabricated default.** `get_synthetic_monitor` errors on an
  unmatched `monitor_id`; `get_apm_agent_config` errors when no configuration matches
  `service_name`/`environment`, including the all-omitted all-services/all-environments case when
  none exists. `service_name` and `environment` are independently optional — all four combinations
  are distinct, valid queries; omitting one is never shorthand for the other.
- **The `ALL_OPTION_VALUE` sentinel is always present** in `list_apm_environments` — a synthetic
  "every environment" entry, never a real environment a service was deployed to; must never be
  filtered out or read as real data.
- **Synthetics parameter values are never surfaced.** `list_synthetic_params` returns
  id/key/description/tags only — structurally, the shape has no value field to populate, even for a
  caller privileged enough that the underlying Kibana API would return one.
- **Two list tools page to exhaustion internally.** `list_synthetic_monitors` and
  `list_apm_sourcemaps` walk the paged API to completion, returning the complete set in one call,
  judging exhaustion by the running/incremental total — never a separate "absolute total" field the
  response may also carry, since the two can disagree and the wrong one truncates or over-collects.
- **Browser-type monitors carry no target.** `type="browser"` monitors have neither URL nor host;
  `target` on one is `null`, never fabricated.
- **Return shapes are bare and uniform** — plain JSON array for every list tool (no envelope, no
  wrapper key), plain JSON object for every get/settings tool, matching every other read tool in
  the server.

## Invariants

- All 10 tools MUST stay `read` tier — none may write or delete Kibana state.
- `list_synthetic_params`'s shape MUST NOT include a parameter value field, regardless of caller privilege.
- `list_apm_environments` MUST always include the `ALL_OPTION_VALUE` sentinel.
- `list_synthetic_monitors` and `list_apm_sourcemaps` MUST return the full result in one call, terminating pagination on the running total, never an absolute/global total field. (For sourcemaps the total-is-grand-total assumption is live-unverified — only the empty response has been observed on the test stack; verify against a real artifact before leaning on it.)
- None of the 10 tools MUST accept or forward a `space` parameter.
- `get_synthetic_monitor` and `get_apm_agent_config` MUST raise a not-found error — never an empty/default object — when their target doesn't exist.
- `get_apm_agent_config` MUST treat `service_name` and `environment` as independently optional, including the both-omitted case.
- A `type="browser"` monitor's `target` MUST be `null`, never fabricated.
- Every domain-level Kibana error reaching a tool MUST be translated to a plain-text tool error with no secrets/stack traces, via the same boundary every other toolbox uses.

## Deliberate exclusions & caveats

- **APM telemetry is out of scope, not deferred to a later tier.** Service/transaction/trace/service-map
  data lives behind internal-only Kibana endpoints on the targeted version, rejected for an external
  client — no supported path to wrap it. Only APM *configuration* is scoped.
- **SLO reads are deferred, license-gated** — Platinum-class, rejected on Basic. `observability-sre`
  (this toolbox + `alerting` + `cases`) stays planned until SLOs land.
- **An observability AI-assistant tool is deferred**, gated on both an Enterprise-only generative-AI
  connector and Tech-Preview status.
- **Space targeting is deliberately absent, not an oversight.** Synthetics and Uptime objects are
  space-scoped; APM central configuration is cluster-global. A uniform `space` parameter across all
  10 tools would misrepresent the APM tools' scope, so reconciling the mix is deferred rather than
  shipped half-right.
- **A Synthetics write tier is planned as an additive tier of this same toolbox** —
  monitor/param/private-location create-update-delete plus a monitor-test action; until it ships
  those namespaces are read-only. APM configuration writes (agent-config upsert, annotation
  creation) are unscheduled backlog, not committed to an order.
- **`list_apm_sourcemaps`' shape is unverified against a live artifact** — a real RUM sourcemap
  could not be seeded on the target instance, so (`identifier`, `created`) is exercised only against
  a fabricated response: directionally right, not confirmed.

## Enforcement

- `tests/unit/toolboxes/test_observability.py` — registers all 10 tools at `read` tier against a
  fake gateway; pins `get_synthetic_monitor`'s not-found, `list_apm_environments`' sentinel,
  `get_uptime_settings`' nested-email shape, `search_apm_annotations`' default environment.
- `tests/unit/adapters/test_observability_gateway.py` — pins the response-mapping contract per
  tool: a real captured monitor body, the browser-monitor null-target case, both
  pagination-to-exhaustion cases (the monitors case regression-guarded with deliberately
  divergent total values; the sourcemaps case exercises the page walk without a divergent-total
  guard), the param value never carried through, the uptime-settings
  nested-email mapping, the sourcemap `created` coercion.
- `tests/unit/test_server_assembly.py` — confirms the toolbox boots with exactly these 10 names,
  all visible at the server's default (`write`) tier.
- `tests/contract/test_gateway_contract.py` (observability section) — live, hard-asserted (no
  skips) against a running Basic-license Kibana: every list tool returns an empty-but-typed array
  on the seeded stack, `get_synthetic_monitor` / `get_apm_agent_config` raise not-found live,
  `get_uptime_settings`' numeric thresholds and index pattern are confirmed live, and the
  `ALL_OPTION_VALUE` sentinel is confirmed present live.
- No e2e-replay transcript exercises this toolbox: behavior is pinned at the unit, adapter, and
  live-contract tiers above, not by a recorded model turn.
