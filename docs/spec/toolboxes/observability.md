# observability — behavior contract

Status: Draft v1.5 (2026-10-25) — regeneration corpus, reconciled to shipped code
Surface reference: docs/tools.md#observability-toolbox · Enforcement: named per section below

## Purpose & persona

Reconnaissance over the observability surface reachable on a Basic license — Synthetics
monitoring, Uptime app settings, APM central (agent) configuration — answering "what's being
monitored, and how is APM configured", plus the upkeep of Synthetics itself: create, edit, run
once and delete monitors, global parameters and private locations (D46), and of APM central
configuration: create or replace an agent configuration, and delete one (D47). Read leg of the
`observability-sre` persona (paired with `alerting` + `cases`), which is withdrawn (see Deliberate
exclusions) — so any deployment enabling `observability` gets this toolbox standalone, read-only at
`KIBANA_MCP_TIER=read`.

## Surface

22 tools: 10 `read` (`readOnlyHint=true`) — the Synthetics, Uptime and APM-configuration reads;
8 `write` — `create_synthetic_monitor`, `update_synthetic_monitor`, `test_synthetic_monitor`,
`create_synthetic_param`, `update_synthetic_param`, `create_synthetic_private_location`,
`update_synthetic_private_location`, `upsert_apm_agent_config`; 4 `destructive` —
`delete_synthetic_monitor`, `delete_synthetic_param`, `delete_synthetic_private_location`,
`delete_apm_agent_config`. The three Synthetics updates, the APM upsert and the four deletes
carry `idempotentHint=true`; the creates and the run do not. `test_synthetic_monitor` sets
`openWorldHint=true` (the run sends the monitor's requests to its target, outside Kibana); every
other tool sets `openWorldHint=false`.
Tiers by the repo rule (D46, amended): a delete is irreversible (a recreated object gets a new id;
a deleted parameter's key, object and value are gone, with nothing left to edit back), so it is
`destructive`; every create and edit has an inverse (an edit back — for a parameter's value only
when the caller holds the old one — or a delete), so they are `write`. The run changes no
Synthetics object and loses nothing, so it is `write` too, though it is not side-effect free: it
sends real requests to the target (a paused monitor's too) and adds a short-lived package policy
to the location's agent policy (below). The APM upsert is `write` because it loses nothing it
read: it reads the configuration it replaces and returns its settings as `previous_settings`,
which a second upsert puts back (read and PUT are not atomic — Kibana's PUT takes no etag, so a
change another client stores between them is replaced unreported; below); the APM delete is `destructive`, like every delete (D47). Full tool list,
descriptions, return shapes: docs/tools.md#observability-toolbox. What's deliberately not covered —
APM telemetry, SLOs, an AI-assistant tool, Synthetics surface left out — is under Deliberate
exclusions, not repeated here.

## Behavioral guarantees

- **No space targeting.** None of the 22 tools accept `space`; calls always resolve through the
  gateway port's (`ports/gateway.py`) default-scope factory call — no space echo in results, no scoped
  not-found suffix, no fail-closed space check. Deliberate absence (see Deliberate exclusions), not
  an oversight to fix by copying the space-aware pattern.
- **Error translation is the shared contract, plus D17 guidance on the write tier.** Every domain
  error crosses the same gateway-error-translation boundary every toolbox uses, becoming a
  plain-text tool error with no secrets or stack traces. The reads author no tool-specific guidance
  — their not-found reads as exactly "the requested id/service+environment doesn't exist" — except
  `get_synthetic_monitor`, whose miss shares the write tier's text (D46), and
  `get_apm_agent_config`, whose miss shares the APM writes' text (D47: "no APM agent configuration
  for service 'cart', all environments — service_name and environment must match a configuration
  exactly (an omitted one means all); call list_apm_agent_configs to see them"; Kibana's own 404
  says only "Not Found"). The write tier names the
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
  and its own 400 drops the list from the message a tool shows, so the check is the tool's. The
  number goes out canonical (`05m` is sent as `{number: "5"}`): Kibana compares the digits as
  text and refuses `"05"` (probed).
- **Monitor and update results are the read shape.** `create_synthetic_monitor` and
  `update_synthetic_monitor` return what `get_synthetic_monitor` returns, mapped from Kibana's
  response. An update sends only the fields passed (Kibana's PUT keeps the rest, probed); a list
  passed (`tags`, `private_locations`) replaces the stored one (probed: `["contract"]` edited to
  `["edited"]` reads back `["edited"]`). An update with no field is refused before the gateway.
- **A run is accepted, not awaited, and reaches outside Kibana.** `test_synthetic_monitor` returns
  `{monitor_id, test_run_id}`. The check runs on the Elastic Agents enrolled in the location's
  agent policy; with none running Kibana still accepts it. A paused monitor runs too (Kibana
  sends the run's config enabled), so a run can send requests its schedule would not; each call
  is a new run. Each run adds a `LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN` package policy
  (`BROWSER_SYNTHETICS_TEST_NOW_RUN` for browser) to the location's agent policy. It outlives the
  location if that is deleted, and Kibana's own clean-up task removes it once it is older than 2
  minutes (15 for browser); the task runs right after the run and then every 20 minutes while
  any remain. The result is not read back (no public route).
- **A parameter's value is write-only.** Neither create nor update returns it (Kibana's update
  response echoes the previous value; the shape has no field for it), and a payload rejection is
  scrubbed of it before it reaches the model. So a value `update_synthetic_param` replaces cannot
  be recovered by any tool; the description says so, and setting it back needs the caller to hold
  the old one. A key already held by another parameter is refused (Kibana stores the duplicate,
  probed), naming `update_synthetic_param`.
- **Private locations: one per agent policy, labels unique, both checked first.** A missing
  policy is Kibana's 400 plus where policy ids come from (`list_agent_policies`,
  `create_agent_policy` in the fleet toolbox). Only the label is editable; an unknown id on the
  update is Kibana's 404 "… does not exist.", read as D17 not-found.
- **An APM agent configuration is named exactly by `service_name` + `environment`.** An omitted
  one means all services or all environments, never "any" (Kibana's view and delete match exactly,
  probed). `ALL_OPTION_VALUE` (the `list_apm_environments` marker) is stored by Kibana as a
  literal name, which no agent reads as "all": the upsert refuses to create a configuration under
  it (after its read, before any write), saying to omit the argument for the all-* one; a
  configuration another client stored under the literal name is matched exactly by view and delete
  (probed), so the upsert replaces it and the delete removes it — telling the caller to omit the
  name there would point it at the different all-* configuration. `""` is refused at the schema — Kibana stores
  `service.name: ""` and its view and delete then read `""` as omitted, so nothing can reach that
  configuration again (probed; the probe's one had to be removed from the system index directly).
- **The upsert replaces the whole settings map and returns the one it replaced.**
  `upsert_apm_agent_config` reads the configuration first; with none it creates one without
  `overwrite` (so a configuration another caller creates in between is Kibana's 400, answered
  "call upsert_apm_agent_config again", never overwritten unreported); with one it replaces it
  with `overwrite=true`, sending the stored `agent_name` again (Kibana's PUT drops it otherwise,
  probed) unless the call gives one; an optional `agent_name` is sent on a create (none when
  omitted). The read and the overwrite are not atomic: Kibana's PUT takes no etag or version
  (probed: "Excess keys are not allowed: body.etag"; Kibana's own route overwrites by document id),
  so a change another client stores between them — within the one call — is replaced and not
  reported, and `previous_settings` is the map the call read. It returns the read shape plus `created` and `previous_settings` (`null` on a create).
  Values go out as strings — `true`/`false` for booleans, `str()` for numbers (Kibana rejects
  non-string values, probed). An empty map is refused before the gateway, naming
  `delete_apm_agent_config`. Kibana validates the values of the settings it knows; its 400 can omit
  which setting failed ("Must be a number between 0.0000 and 1"), so the error adds the settings
  sent. It stores setting names it does not know (probed), which agents ignore; the description
  says to take names from the agent's central-configuration docs.
- **The APM delete returns what it removed.** `delete_apm_agent_config` reads the configuration
  first (a miss is the D47 not-found, and nothing is deleted) and returns `{service_name,
  environment, deleted: true, settings, agent_name}` — what `upsert_apm_agent_config` needs to
  recreate it.
- **Deletes report what they did, and a miss is a not-found.** Each Synthetics delete returns
  `{id, deleted: true}`. Kibana answers a monitor or parameter delete of an unknown id with
  `200 [{"deleted": false}]` and a private-location one with a 400 "does not exist" — all read
  as D17 not-found here. A location still used by monitors is refused by Kibana; the error adds
  `update_synthetic_monitor` (move them) and `delete_synthetic_monitor`. The agent policy behind a
  deleted location stays, with any test-run package policy still on it until Kibana's clean-up.

## Invariants

- The 10 reads MUST stay `read` tier; every Synthetics delete and `delete_apm_agent_config` MUST be `destructive`; no write-tier tool may delete an object.
- `upsert_apm_agent_config` MUST NOT overwrite a configuration it did not read in the same call, and MUST return the settings it read as `previous_settings`; it MUST NOT claim more — Kibana has no conditional write, so a change stored between its read and its PUT is not detected.
- `upsert_apm_agent_config` MUST refuse to create a configuration named `ALL_OPTION_VALUE` (service or environment) before any write; the APM writes MUST refuse `""` before any request; a configuration stored under the literal `ALL_OPTION_VALUE` MUST be matched exactly, never redirected to the all-* one.
- A replacement MUST send a stored `agent_name` again unless the call gives one; `delete_apm_agent_config` MUST return the deleted `agent_name`.
- `list_synthetic_params`'s shape MUST NOT include a parameter value field, regardless of caller privilege.
- `list_apm_environments` MUST always include the `ALL_OPTION_VALUE` sentinel.
- `list_synthetic_monitors` and `list_apm_sourcemaps` MUST return the full result in one call, terminating pagination on the running total, never an absolute/global total field. (For sourcemaps the total-is-grand-total assumption is live-unverified — only the empty response has been observed on the test stack; verify against a real artifact before leaning on it.)
- No tool of the toolbox may accept or forward a `space` parameter (D10).
- `test_synthetic_monitor` MUST set `openWorldHint=true` and `idempotentHint=false`; its description MUST state that a paused monitor runs and that the run adds a package policy to the location's agent policy.
- A schedule MUST reach Kibana with its number in canonical form (no leading zeros).
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
  22 tools would misrepresent the APM tools' scope, so reconciling the mix is deferred rather than
  shipped half-right.
- **The Synthetics write tier leaves part of the namespace out (D46, roadmap).** Not offered:
  Elastic-managed locations (none on a self-managed stack to probe), reading a test run's result
  (no public route), the bulk calls, and the monitor fields past the core set (alert toggles,
  timeout, labels, monitor params, `retest_on_failure`, namespace, `service.name`, type-specific
  fields), a parameter's `share_across_spaces`, a location's `geo`/`spaces`. Browser monitors are
  created like the others but run only on the `elastic-agent-complete` image; the stack's agent
  is not one, so only lightweight checks were seen to run.
- **Creating APM annotations is out of scope, license-gated** — Kibana answers the create with 403
  "Annotations require at least a gold license or a trial license." on Basic (probed, D47), so
  `search_apm_annotations` reads them and no tool writes one. The APM writes also leave out
  the agents' own lookup (`search_agent_configurations`); `agent_name` on a new configuration is
  taken only from the caller (Kibana's UI derives it from ingested APM data, none on this stack).
- **`list_apm_sourcemaps`' shape is unverified against a live artifact** — a real RUM sourcemap
  could not be seeded on the target instance, so (`identifier`, `created`) is exercised only against
  a fabricated response: directionally right, not confirmed.

## Enforcement

- `tests/unit/toolboxes/test_observability.py` — registers all 22 tools against a fake gateway
  and pins their hints and the absence of `space`; pins `get_synthetic_monitor`'s not-found,
  `list_apm_environments`' sentinel, `get_uptime_settings`' nested-email shape,
  `search_apm_annotations`' default environment; and each write-tier refusal (target per type,
  schedule numbers and the canonical number sent, unknown or absent locations, empty updates,
  duplicate keys and labels, a taken agent policy), the read-shape results, and that no result
  carries a parameter value; the APM upsert's create (no overwrite) and replacement (the whole map,
  `previous_settings`, the stored `agent_name` sent again, a given one sent on a create or
  replacement), its string values, its empty-map, `""` and create-under-`ALL_OPTION_VALUE`
  refusals, a literal-`ALL_OPTION_VALUE` configuration replaced and deleted while the
  all-environments one stays, the delete's result (with `agent_name`, enough to recreate it) and
  both misses.
- `tests/unit/adapters/test_observability_gateway.py` — pins the response-mapping contract per
  tool: a real captured monitor body, the browser-monitor null-target case, both
  pagination-to-exhaustion cases (the monitors case regression-guarded with deliberately
  divergent total values; the sourcemaps case exercises the page walk without a divergent-total
  guard), the param value never carried through, the uptime-settings
  nested-email mapping, the sourcemap `created` coercion.
- `tests/unit/test_server_assembly.py` — confirms the toolbox boots with exactly these 22 names:
  10 at `read`, 18 at the default `write`, 22 at `destructive`.
- `tests/contract/test_gateway_contract.py` (observability section) — live, hard-asserted (no
  skips) against a running Basic-license Kibana: every list tool returns an empty-but-typed array
  on the seeded stack, `get_synthetic_monitor` / `get_apm_agent_config` raise not-found live,
  `get_uptime_settings`' numeric thresholds and index pattern are confirmed live, and the
  `ALL_OPTION_VALUE` sentinel is confirmed present live.
- `tests/unit/adapters/test_observability_gateway.py` (write tier) — the request each write sends
  (only the given fields; the schedule as `{number, unit}`, its number canonical), the 200-`deleted: false` and
  400-"does not exist" misses as D17 not-found, the in-use location's added fix, the
  agent-policy hint, the update response's echoed value dropped, a rejection scrubbed of the value.
- `tests/contract/test_gateway_contract.py` (Synthetics write tier) — live on a throwaway agent
  policy: a parameter's lifecycle with the value absent from every result and the duplicate key
  refused; a private location and a monitor placed on it by label (schedule `030s` stored as
  `30s`), the partial edit (`05m` stored as `5m`, the tag list replaced), a bad-id location
  rename, a run request and the test-now package policy it adds to the agent policy (still there
  after the location's delete), the in-use refusal, the move to a second location that lets the
  first be deleted, each delete and its miss; everything removed after.
- `tests/unit/adapters/test_observability_gateway.py` (APM writes) — the PUT with only the given
  fields then the read back, `overwrite` sent only when asked, `agent_name` mapped, the race 400
  and a value 400 with the settings added, the miss text on view and delete.
- `tests/contract/test_gateway_contract.py` (APM writes) — live, on throwaway `contract-apm-*`
  services: a create (a number sent as `"0.2"`), a replacement that replaces the map and returns
  the old one, a value Kibana rejects with the settings named, the delete with what it removed and
  its miss; a configuration written with `agent_name` keeping it through a replacement, the delete
  returning it and an upsert with it recreating it; and a literal-`ALL_OPTION_VALUE` configuration
  replaced and deleted beside the service's all-environments one, which stays, with a create under
  the literal name refused.
- No e2e-replay transcript exercises this toolbox: behavior is pinned at the unit, adapter, and
  live-contract tiers above, not by a recorded model turn.
