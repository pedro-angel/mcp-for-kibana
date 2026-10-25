# observability — behavior contract

Status: Draft v1.2 (2026-10-25) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#observability-toolbox · Enforcement: named per section below

## Purpose & persona

Reconnaissance over the observability surface reachable on a Basic license — Synthetics
monitoring, Uptime app settings, APM central (agent) configuration — answering "what's being
monitored, and how is APM configured", plus the upkeep of Synthetics itself: create, edit, run
once and delete monitors, global parameters and private locations (D46). Read leg of the
`observability-sre` persona (paired with `alerting` + `cases`), which is withdrawn (see Deliberate
exclusions) — so any deployment enabling `observability` gets this toolbox standalone, read-only at
`KIBANA_MCP_TIER=read`.

## Surface

20 tools: 10 `read` (`readOnlyHint=true`) — the Synthetics, Uptime and APM-configuration reads;
7 `write` — `create_synthetic_monitor`, `update_synthetic_monitor`, `test_synthetic_monitor`,
`create_synthetic_param`, `update_synthetic_param`, `create_synthetic_private_location`,
`update_synthetic_private_location`; 3 `destructive` — `delete_synthetic_monitor`,
`delete_synthetic_param`, `delete_synthetic_private_location`. The three updates and three deletes
carry `idempotentHint=true`; the creates and the run do not. Every tool sets `openWorldHint=false`.
Tiers by the repo rule (D46): a delete is irreversible (a recreated object gets a new id; a
parameter's value cannot be read back), so it is `destructive`; every create and edit has an
inverse, and a run adds a result without changing anything, so they are `write`. Full tool list,
descriptions, return shapes: docs/tools.md#observability-toolbox. What's deliberately not covered —
APM telemetry, SLOs, an AI-assistant tool, Synthetics surface left out — is under Deliberate
exclusions, not repeated here.

## Behavioral guarantees

- **No space targeting.** None of the 20 tools accept `space`; calls always resolve through the
  gateway port's (`ports/gateway.py`) default-scope factory call — no space echo in results, no scoped
  not-found suffix, no fail-closed space check. Deliberate absence (see Deliberate exclusions), not
  an oversight to fix by copying the space-aware pattern.
- **Error translation is the shared contract, plus D17 guidance on the write tier.** Every domain
  error crosses the same gateway-error-translation boundary every toolbox uses, becoming a
  plain-text tool error with no secrets or stack traces. The reads author no tool-specific guidance
  — their not-found reads as exactly "the requested id/service+environment doesn't exist" — except
  `get_synthetic_monitor`, whose miss shares the write tier's text (D46). The write tier names the
  fix: a wrong `monitor_id`, `param_id` or `location_id` says where ids come
  from ("… is the id that create_synthetic_monitor or list_synthetic_monitors returns, not the
  monitor's name; call list_synthetic_monitors to find it"), and each refusal below names the call
  that resolves it.
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

- **Monitors: each type takes its own target, checked before the write.** `url` for http, `host`
  for tcp (with its port) and icmp, `inline_script` for browser. A missing target and another
  type's field are refused before any request — Kibana stores a `url` on a tcp monitor without
  complaint (probed). On update the monitor is read first, only when a target field is passed, to
  learn its type; the type itself cannot change (Kibana: "Monitor type cannot be changed").
- **Monitors run only on private locations, named by id or label and checked first.** Each entry
  of `private_locations` must match an existing location's id or label
  (`list_synthetic_private_locations`); with none, the error names
  `create_synthetic_private_location`. Elastic-managed `locations` are not offered.
- **A schedule is the read shape and Kibana's numbers.** `<n>m` or `<n>s`, `n` in 1, 2, 3, 5, 10,
  15, 20, 30, 60, 120, 240 — Kibana checks the number against its minute list whatever the unit,
  and its own 400 drops the list from the message a tool shows, so the check is the tool's.
- **Monitor and update results are the read shape.** `create_synthetic_monitor` and
  `update_synthetic_monitor` return what `get_synthetic_monitor` returns, mapped from Kibana's
  response. An update sends only the fields passed (Kibana's PUT keeps the rest, probed); an
  update with no field is refused before the gateway.
- **A run is accepted, not awaited.** `test_synthetic_monitor` returns `{monitor_id, test_run_id}`.
  The check runs on the Elastic Agents enrolled in the location's agent policy; with none running
  Kibana still accepts it. The result is not read back (no public route).
- **A parameter's value is write-only.** Neither create nor update returns it (Kibana's update
  response echoes the previous value; the shape has no field for it), and a payload rejection is
  scrubbed of it before it reaches the model. A key already held by another parameter is refused
  (Kibana stores the duplicate, probed), naming `update_synthetic_param`.
- **Private locations: one per agent policy, labels unique, both checked first.** A missing
  policy is Kibana's 400 plus where policy ids come from (`list_agent_policies`,
  `create_agent_policy` in the fleet toolbox). Only the label is editable.
- **Deletes report what they did, and a miss is a not-found.** Each delete returns
  `{id, deleted: true}`. Kibana answers a monitor or parameter delete of an unknown id with
  `200 [{"deleted": false}]` and a private-location one with a 400 "does not exist" — all read
  as D17 not-found here. A location still used by monitors is refused by Kibana; the error adds
  `update_synthetic_monitor` (move them) and `delete_synthetic_monitor`. The agent policy behind a
  deleted location stays.

## Invariants

- The 10 reads MUST stay `read` tier; every Synthetics delete MUST be `destructive`; no write-tier tool may delete an object.
- `list_synthetic_params`'s shape MUST NOT include a parameter value field, regardless of caller privilege.
- `list_apm_environments` MUST always include the `ALL_OPTION_VALUE` sentinel.
- `list_synthetic_monitors` and `list_apm_sourcemaps` MUST return the full result in one call, terminating pagination on the running total, never an absolute/global total field. (For sourcemaps the total-is-grand-total assumption is live-unverified — only the empty response has been observed on the test stack; verify against a real artifact before leaning on it.)
- No tool of the toolbox may accept or forward a `space` parameter (D10).
- A write-tier result or error MUST NOT carry a Synthetics parameter's value.
- `create_synthetic_monitor` and `update_synthetic_monitor` MUST refuse, before any request, a schedule outside Kibana's numbers, an unknown private location, and a target field of another monitor type.
- A Synthetics delete of an id that matches nothing MUST raise not-found, never report `deleted: true`.
- `get_synthetic_monitor` and `get_apm_agent_config` MUST raise a not-found error — never an empty/default object — when their target doesn't exist.
- `get_apm_agent_config` MUST treat `service_name` and `environment` as independently optional, including the both-omitted case.
- A `type="browser"` monitor's `target` MUST be `null`, never fabricated.
- Every domain-level Kibana error reaching a tool MUST be translated to a plain-text tool error with no secrets/stack traces, via the same boundary every other toolbox uses.

## Deliberate exclusions & caveats

- **APM telemetry is out of scope, not deferred to a later tier.** Service/transaction/trace/service-map
  data lives behind internal-only Kibana endpoints on the targeted version, rejected for an external
  client — no supported path to wrap it. Only APM *configuration* is scoped.
- **SLO reads are out of scope, license-gated** — Platinum-class, rejected on Basic, and
  subscription-gated surfaces are not built here. `observability-sre` is **withdrawn**: its
  three toolboxes are all built, but the SLOs that define the persona are out of scope.
- **An observability AI-assistant tool is out of scope**, gated on an Enterprise-only
  generative-AI connector, and Tech-Preview besides.
- **Space targeting is deliberately absent, not an oversight.** Synthetics and Uptime objects are
  space-scoped; APM central configuration is cluster-global. A uniform `space` parameter across all
  20 tools would misrepresent the APM tools' scope, so reconciling the mix is deferred rather than
  shipped half-right.
- **The Synthetics write tier leaves part of the namespace out (D46, roadmap).** Not offered:
  Elastic-managed locations (none on a self-managed stack to probe), reading a test run's result
  (no public route), the bulk calls, and the monitor fields past the core set (alert toggles,
  timeout, labels, monitor params, `retest_on_failure`, namespace, `service.name`, type-specific
  fields), a parameter's `share_across_spaces`, a location's `geo`/`spaces`. Browser monitors are
  created like the others but run only on the `elastic-agent-complete` image; the stack's agent
  is not one, so only lightweight checks were seen to run. APM configuration writes (agent-config
  upsert, annotation creation) are separate backlog, not part of this tier.
- **`list_apm_sourcemaps`' shape is unverified against a live artifact** — a real RUM sourcemap
  could not be seeded on the target instance, so (`identifier`, `created`) is exercised only against
  a fabricated response: directionally right, not confirmed.

## Enforcement

- `tests/unit/toolboxes/test_observability.py` — registers all 20 tools against a fake gateway
  and pins their hints and the absence of `space`; pins `get_synthetic_monitor`'s not-found,
  `list_apm_environments`' sentinel, `get_uptime_settings`' nested-email shape,
  `search_apm_annotations`' default environment; and each write-tier refusal (target per type,
  schedule numbers, unknown or absent locations, empty updates, duplicate keys and labels, a taken
  agent policy), the read-shape results, and that no result carries a parameter value.
- `tests/unit/adapters/test_observability_gateway.py` — pins the response-mapping contract per
  tool: a real captured monitor body, the browser-monitor null-target case, both
  pagination-to-exhaustion cases (the monitors case regression-guarded with deliberately
  divergent total values; the sourcemaps case exercises the page walk without a divergent-total
  guard), the param value never carried through, the uptime-settings
  nested-email mapping, the sourcemap `created` coercion.
- `tests/unit/test_server_assembly.py` — confirms the toolbox boots with exactly these 20 names:
  10 at `read`, 17 at the default `write`, 20 at `destructive`.
- `tests/contract/test_gateway_contract.py` (observability section) — live, hard-asserted (no
  skips) against a running Basic-license Kibana: every list tool returns an empty-but-typed array
  on the seeded stack, `get_synthetic_monitor` / `get_apm_agent_config` raise not-found live,
  `get_uptime_settings`' numeric thresholds and index pattern are confirmed live, and the
  `ALL_OPTION_VALUE` sentinel is confirmed present live.
- `tests/unit/adapters/test_observability_gateway.py` (write tier) — the request each write sends
  (only the given fields; the schedule as `{number, unit}`), the 200-`deleted: false` and
  400-"does not exist" misses as D17 not-found, the in-use location's added fix, the
  agent-policy hint, the update response's echoed value dropped, a rejection scrubbed of the value.
- `tests/contract/test_gateway_contract.py` (Synthetics write tier) — live on a throwaway agent
  policy: a parameter's lifecycle with the value absent from every result and the duplicate key
  refused; a private location and a monitor placed on it by label, the partial edit, a run
  request, the in-use refusal, each delete and its miss; everything removed after.
- No e2e-replay transcript exercises this toolbox: behavior is pinned at the unit, adapter, and
  live-contract tiers above, not by a recorded model turn.
