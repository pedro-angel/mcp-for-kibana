# Roadmap

The one page you need to pick this project up: what is built, what is
deliberately not, and the facts that cost something to learn. Open items are
tracked on this page, under *Open work*, not as GitHub issues.

## Where the project stands

The server ships **10 toolboxes / 162 tools** (dashboards, data-management,
alerting, cases, security-detections, fleet, streams, observability,
platform-admin, platform-health) — see the [Tool reference](tools.md) for every
one. Every tool is classified read / write / destructive and contract-tested
against live Kibana 9.4.7 and 9.5.4.

**Toolbox-level Basic coverage is complete; method-level is not.** The 2026-07-16
catalog audit was toolbox-granular, and its "no Basic functionality left" claim
did not survive the **2026-08-19 method-level audit**: the server touches 26 of
the 27 kibana-py namespaces usable on Basic but wraps ~126 of their ~407
methods. The remaining Basic surface worth building is now filed in build order
under *Open work*; everything else still open is gated on infrastructure this
project does not run, or on a decision that has not been taken. Subscription-gated
work is not open work at all — it is out of scope (below).

Seven test tiers back that claim: unit (fakes), contract (live stack), e2e-replay
(a recorded model turn through a real MCP client), two live-model tiers — e2e
(Claude via `claude -p`, D38) and e2e-local (a local model in LM Studio) —
plus two ephemeral tiers that stand up throwaway stacks for the destructive
paths. Everything except the live-model tiers runs in CI; the local gate runs
the Claude tier too (D42).

The command that certifies completion is the Definition-of-Done gate — `make dod`,
criteria declared in `dod.config`. Nothing is "done" because its author says so.
A single pass needs the dev stack up for the live tiers and gone for the
ephemeral ones, so:

```bash
scripts/stack.sh up && scripts/stack.sh seed
KIBANA_MCP_DOD_CYCLE_STACK=1 make dod
```

Day to day: `make check` for the fast gates, `make dod` before any completion or
release claim, `make help` for the rest.

## Open work

As of this page:

- feat: `update_alert_rule` returns the rule's detail — **open (left open by
  D40)**. Its result is still the summary, so a model confirms an edit with a
  second call to `get_alert_rule`; returning `get_alert_rule`'s shape instead
  (as `update_connector` returns `get_connector`'s) would save that call.
  Additive to the result, but it changes what models see, so it re-runs
  `make test-e2e` (D31/D38).

**Buildable now (Basic license).** The *write-tier* campaign on the
existing read toolboxes is done, but a 2026-07-16 catalog audit found unbuilt
Basic-buildable **toolboxes** that had never been filed. Now filed, in build order:

- feat: `fleet` toolbox — **DONE**. Read-first v1 (20 tools) + **write/destructive tier shipped 2026-07-18**: 15 tools (agent/package-policy + output CRUD, single+bulk agent actions), `fleet-admin` profile now `destructive`, a `fleet_ephemeral` 2-agent battle-test harness + DoD criterion. Enrollment-key mint/revoke deferred (unsafe under api-key auth — inline leaks to context, on-disk breaks HTTP transport).
- feat: `streams` write follow-ups — **shipped 2026-07-18**. 3 tools (9→12): `set_stream_processing` (RMW the processing.steps facet), `deactivate_fork` (write), `activate_fork` (**destructive + confirm** — activation diverts live docs; already-routed docs stay in the child, per env-research). ILM-mode retention (no ILM policy on-stack) and raw full-config `update_ingest` (footgun) + `space_id` deferred.
- feat: `security-detections` write follow-ups — **shipped 2026-07-18**. 6 tools (19→25): value-list item CRUD, full-replace `replace_detection_rule` (RMW — omitting a field wipes it; also translates the `from_`↔`from` kwarg/body-key mismatch), and enable/disable. **Env-research corrected the "enable/disable is privilege-gated" premise**: `bulk_action`/`patch` do 403/500 under api-key auth, but `update_rule(enabled=…)` works (the path kibana-py's docstring recommends) — so it's buildable, and shipped.
- feat: `alerting` rule-lifecycle follow-ups — **DONE 2026-10-24** (D45). The kibana-py `alerting.rule` surface (with `health` and `rule_types`) is 16/16 wrapped (`alerting.backfill`, a separate client, is not): `mute_alert_rule`/`unmute_alert_rule` (rule-wide), `mute_alert`/`unmute_alert` (per alert), `snooze_alert_rule`/`unsnooze_alert_rule`, `update_alert_rule_api_key` (all write), and `disable_alert_rule(untrack=True)` (additive, default off); `get_alert_rule` reads the mute state back. Open, from the probes: Kibana's public API lists no snoozes (a lost `schedule_id` is cancelled only in Kibana's Rules page, and a rule holds at most 5, expired ones counting) and no alert ids (`mute_alert` takes the id from the user, the rule's grouping, or the `cleared_alert_ids` a rule-wide mute or unmute returns; a read of a rule's alerts would need the alerts-as-data index). The `rule_types` read shipped 2026-10-12 as `list_rule_types`; in-place rule edit (`update`) shipped 2026-10-12 as `update_alert_rule`, with connector `get`/`update` as `get_connector`/`update_connector` (D35). **Done 2026-10-17:** the Claude tier showed no alerting tool returned a rule's `params`, so after `update_alert_rule` a model could not read the new threshold back (`scripts/experiment/runs/claude-e2e/counted-2026-10-17/`); `get_alert_rule` now returns `params` and `actions`, each action with its `uuid` so a read → update round trip keeps it (D40 and its amendment). All Basic; same toolbox, additive tools.
- feat: `synthetics` write tier — **DONE 2026-10-25** (D46). The observability toolbox now wraps 14 of kibana-py's 19 `synthetics` methods: `create_synthetic_monitor`, `update_synthetic_monitor`, `test_synthetic_monitor`, `create_synthetic_param`, `update_synthetic_param`, `create_synthetic_private_location`, `update_synthetic_private_location` (write) and `delete_synthetic_monitor`, `delete_synthetic_param`, `delete_synthetic_private_location` (destructive); the plan's `*_private_location` names gained the toolbox's `synthetic_` prefix. All Basic. A test run was seen to execute on the stack's demo agent (a document with its `test_run_id`, status `up`, about 20 s later), so `test_synthetic_monitor` shipped. Open, from the probes: **no tool reads a run's result** (Kibana's public API has no route for it; the result lives in the `synthetics-*` data streams); **the contract tier does not re-prove execution** — the stack's default Fleet output is `localhost:9200`, unreachable from the agent container, so a check runs but cannot publish without rewiring the shared demo policy's output (the probe did that and restored it); **Elastic-managed locations are not offered** (a self-managed stack has none: "No Elastic managed location available to use"), so `locations` waits for a Cloud deployment to probe. Not wrapped, with reasons: the bulk calls (`bulk_delete_monitors`, `bulk_create_params`, `bulk_delete_params` — one-at-a-time tools cover them), `get_param` and `get_private_location` (the list reads return the same shape), and the monitor fields past the core set — alert toggles, timeout, labels, monitor params, `retest_on_failure`, namespace, APM `service.name`, the type-specific `fields` passthrough — plus `share_across_spaces` on a parameter and `geo`/`spaces` on a location: no task asks for them yet, and each is one optional argument when one does. Kibana adds a `LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN` package policy to the agent policy on every test run (a paused monitor's too); it outlives a deleted location until Kibana's own clean-up task removes it, about 20 minutes later (lightweight older than 2 minutes, browser older than 15; review of 2026-10-25, D46 amended), so the tools leave it to Kibana and their descriptions say so.
- feat: `apm` configuration writes — **DONE 2026-10-25** (D47). `upsert_apm_agent_config` (write: creates or replaces one agent configuration, returns `created` and the replaced `previous_settings`) and `delete_apm_agent_config` (destructive: returns the deleted settings and `agent_name`) in the observability toolbox; kibana-py's `apm.create_or_update_agent_configuration` and `apm.delete_agent_configuration`, both Basic. **Dropped: `create_apm_annotation`** — Kibana 9.5.4 on Basic answers `POST /api/apm/services/{service}/annotation` with 403 "Annotations require at least a gold license or a trial license." (probed 2026-10-25, with and without environment, message and tags), so it is out of scope under D28, not deferred; `search_apm_annotations` keeps reading them. Open, from the probes: **setting names are not checked** — Kibana validates the values of the settings it knows but stores unknown names (`no_such_setting` answered 200), and no public route serves its setting definitions, so a typo is stored and ignored by agents; the description says so. **A new configuration's `agent_name` comes only from the caller** (the upsert's optional `agent_name`; a replacement keeps the stored one when none is given): kibana-py's `apm.get_agent_name` derives it from ingested APM data, which this stack has none of to probe. **The upsert's read and write are not atomic** — Kibana's PUT takes no etag (an `etag` in the body is a 400), so a change another client stores between them is replaced unreported (review of 2026-10-25, D47 amended); a conditional write would need Kibana to accept one. Not wrapped: `apm.search_agent_configurations` (the lookup APM Server makes for its agents), `create_agent_key`, `save_server_schema`, the sourcemap upload/delete.
- **Deprioritized, with the reason on record (2026-08-19):** `cases` depth (comments management, files, configuration, tags/reporters, alert linkage — 6/22 methods wrapped). Core case CRUD is Basic and shipped; the depth that makes cases compelling is license-locked — external push needs Gold+ connector types, assignees need Platinum — so the remaining Basic-buildable slice is thin workflow plumbing. Not revisited: the depth that would make it compelling is subscription-locked, which is out of scope.
- **Parked as infra-gated (re-confirmed 2026-08-19):** `osquery` — the one whole Basic namespace with zero coverage (0/14 methods; live probe: saved-queries/packs answer 200 on Basic). Already classified under `security-response` below: useful only with the osquery_manager integration on an agent policy and hosts worth querying — the dockerized lab agents would return container-level trivia. Revisit when real endpoints enroll.
- feat: per-tool surface configuration — **filed 2026-08-20 (owner request);
  the allow list shipped 2026-10-12** as `KIBANA_MCP_TOOLS`
  ([Tool selection](configuration.md#tool-selection), decision
  [D34](spec/decisions.md)): published = listed ∩ tier, the list alone
  decides which toolboxes register, start time only, applied by disable only.
  A persona that crosses ownership boundaries (a dashboard in a fresh space:
  dashboards + data-management + platform-admin, 29 tools at write) can now be
  trimmed toward the ~20-tool heuristic. **Named tool lists with coherence
  warnings shipped 2026-10-17** ([Named lists](configuration.md#named-lists),
  decision [D41](spec/decisions.md)): `@name` entries in `KIBANA_MCP_TOOLS`
  name a built-in list (`read-only-explorer`, `dashboards-analyst`) or one
  from the operator's TOML file (`KIBANA_MCP_TOOL_LISTS_FILE`), and a list
  that leaves out a tool a published tool relies on gets one stderr line.
  The profiles keep naming toolboxes and a tier; a unit test pins each
  built-in list to its profile's surface. **Still open:** fork lifecycles are not in the dependency map — no streams
  tool names a partner for an input it takes, so there is no edge to record
  yet.
- feat: dashboards library loop — **shipped 2026-10-12** (D37):
  `search_visualizations`, `get_visualization`, `update_visualization`,
  `add_library_panel` (by reference) and `delete_visualization` make the id
  `create_visualization` returns usable. **Still open:** telling
  `delete_visualization`'s caller which dashboards still show the item
  (Kibana answers only through the deprecated saved-objects `has_reference`
  search; a scan of every dashboard per delete was not built), and a
  two-`group_by` `mosaic`, which VizSpec accepts but Kibana 9.5.4 rejects
  ("Only a single non-collapsed dimension is allowed for group_by", observed
  while probing D37) — either tighten the VizSpec rule to one or send the
  second as a collapsed dimension, after a probe on both lines.
- **Unordered Basic backlog from the 2026-08-19 audit** (file individually when picked up): `spaces.copy_saved_objects` + `update_objects_spaces` (natural extension of space targeting), `timeline` depth (1/19 wrapped), data-view runtime fields + default get/set (in-place data-view edit shipped 2026-10-12 as `update_data_view`, D36), exception-list update/duplicate/export/import, granular `saved_objects` reads (find/get — raw CRUD stays out: Kibana marks it deprecated), fleet advanced ops (package install/uninstall, diagnostics, tags, k8s manifest), streams queries/attachments/content (significant-events likely needs an AI connector — verify before filing).

`security-entity-analytics` is **out of scope** (subscription-gated). `security-response`
remains **classified (2026-07-18) and deferred** (infra-gated:
endpoint/osquery reads are Basic-GA but empty without an enrolled Elastic Defend endpoint +
osquery integration; `endpoint.get_actions_list` 403-privilege-gated; the response actions are
☠️ destructive on live hosts). `platform-admin`'s YAGNI non-goals stay unfiled (deliberate scope
cuts, not tracked work).

**Out of scope: anything that needs a paid Elastic subscription.** This project builds
only what a Basic license can run, so subscription-gated work is not tracked here and
will not be built — decision [D28](spec/decisions.md), stated for readers under
[Licensing and scope](index.md#licensing-and-scope). Dropped under
that rule (previously filed as blocked): observability SLO reads, `platform-admin`
logstash pipeline reads, the observability AI assistant tool, the `security-ai`
toolbox, `security-entity-analytics`, the `ai-automation` toolbox, and `alerting`
maintenance windows.

Closed with the **v0.1.0 public release (2026-08-21)** — the two items that
had been deferred until all functionality was done and battle-tested: PyPI
publishing + versioning/changelog (tag-triggered trusted publishing; `uvx
mcp-for-kibana` is live) and the public-flip checklist (repo public, docs on
ReadTheDocs with a canonical `site_url`, badges). The fork-PR CI hardening on
that checklist shipped with it: the `checks.yml` `image` job — which runs the
built container via `image-smoke.sh` — is restricted to same-repo refs, so an
untrusted fork PR never executes its own code in the runner.

Closed since this page's 2026-07-12 baseline: `platform-admin`
write/destructive tier (logstash reads split out separately),
`security-detections` update-rule + value lists, `streams`
write/destructive tier, `saved_objects` export/import +
destructive overwrite, ES|QL panels — `metric` v1 +
`table`/`xy` chart types. Also closed in the 2026-07-12 campaign: several
smaller fixes, including the Settings kwarg and auth-docs corrections closed
earlier still.

**Tool packaging & the toolbox roadmap.** Which toolboxes to build next is
decided persona-first: a catalog of ~14 candidate toolboxes over all 40
kibana-py namespaces, a set of
shippable persona [`profiles/`](https://github.com/pedro-angel/mcp-for-kibana/tree/main/profiles)
(3 live), and a leverage-first build order. **Wave 1
(`platform-health` + `data-management`) and Wave 2 (`alerting` + `cases`) are
done** (2026-07-12 campaign). **Wave 3 `observability` v1 is done** (read-first:
synthetics + uptime + apm-config reads, then the Synthetics write tier and the APM agent-config writes on 2026-10-25, D46 and D47; env-research → spec → adversarial review
→ implement → adversarial review → live contract tests on Basic). Scoped
honestly: the SRE-facing telemetry an operator expects is *not* buildable on the
public/Basic surface — APM services/traces/service-maps are internal-only
(`/internal/apm/*`, `400` externally, unwrapped by kibana-py) and SLOs need
Platinum (`403` on Basic). SLOs and the obs-AI assistant are out of scope (subscription-gated), so
`observability-sre` is withdrawn. **Wave 3 `security-detections` v1 is done**
(read-first: detection rules + alerts + rule-tags/prepackaged-status + exception
lists/items + value lists + timelines; 10 read tools; whole surface Basic-GA,
seeded-and-captured rule/exception shapes; env-research → spec → adversarial
review → live contract tests). Its AI half, `security-ai` (security_ai_assistant
+ attack_discovery), is out of scope — it needs an Enterprise-gated `.gen-ai`
connector; the `soc-analyst` profile is withdrawn. **Wave 3 core toolboxes are now
complete.** (Wave 4 — `platform-admin` + `streams` read-first, then their
write/destructive tiers plus the `security-detections` write tiers — has since landed;
see the dated updates above through **2026-07-16**, the newest of which is the current
state.)

Longer-horizon ideas deliberately not filed yet (file when real): a meta-tool
gateway toolbox for power users, OAuth 2.1 if the user base outgrows API keys.
(Kibana Spaces support graduated from this list: space targeting shipped on 21
tools in the 2026-08-15 wave and extended to 63 space-aware tools across
alerting, cases and security-detections on 2026-08-19.)

## Accepted nits (known, deliberately not fixed)

- `kibana_mcp.server` legitimately imports the kibana adapter (composition
  root); only *direct* `import kibana` is contract-forbidden
  (`allow_indirect_imports` contract in `pyproject.toml`).
- `DataViewDetail` is a frozen dataclass holding a dict — unhashable if
  ever hashed; nothing hashes it.
- `build_dashboard_data` drops an explicitly-empty `time_range={}`
  (truthiness check); unreachable from any tool.

## Hard-won facts a new session should not re-derive

- **Elastic AI features are Enterprise-gated, not just "needs a connector".** The
  Observability + Security AI Assistants, Attack Discovery, and the Generative-AI
  (`.gen-ai`) connector all require an **Enterprise** license (not Platinum). Kibana sets
  `minimumLicenseRequired: 'enterprise'` on the connector type, so Basic 403s connector
  creation and a local OpenAI-compatible LLM (LM Studio) cannot substitute — the connector
  **type** is the gate, not the model. Confirmed live 2026-07-16. This is why those
  features are out of scope rather than deferred: see [Licensing and scope](index.md#licensing-and-scope).
- **Contract tests are the payload authority.** Live Kibana 9.4.x rejects
  `time_range` inside Lens visualization configs (the OpenAPI spec
  suggested otherwise); type names are `data_table`, terms buckets use
  `fields` (plural) + `limit`, metric charts need `{"type": "primary"}`. First
  probed on 9.4.3; re-verified green on 9.4.7 (2026-09-21).
- **fastmcp 3.4.x:** `get_http_headers()` strips `authorization` unless
  `include={"authorization"}`; tier gating uses `mcp.disable(tags=...)`.
- **kibana-py:** `ApiError.__init__` reads `meta.status` unconditionally —
  test fixtures need a `meta` with a `.status`.
- **Local test stack** runs on host ports **19200/15601** (the machine's
  own Kibana dev stack owns the defaults). `scripts/stack.sh up|seed|status|stop|down|env`;
  credentials regenerate into gitignored `elastic-start-local/.env.seed`.
- **LM Studio (0.4.12):** the mcp.json toggle is gated behind *Require
  Authentication* and API tokens need the *Use MCP Servers* permission —
  full path in [e2e-setup](e2e-setup.md). `google/gemma-4-12b-qat`
  measured 5/5 on the dashboards space chain and 2/2 on the alerting-space
  gate (D18; no model is privileged since D33); `openai/gpt-oss-20b`
  characteristically drops the `space` parameter mid-chain (3/5 wrong-space) and was retired from this
  duty 2026-08-19; qwen2.5-coder-14b garbles tool syntax ~1 run in 3.
- **`/api/v1/chat` response shape (observed 2026-08-09, not inferred):**
  `output` is a list of typed items — `reasoning`, `tool_call` (`tool`,
  `arguments`, `output`, `provider_info`), `invalid_tool_call` (`reason`,
  `metadata.tool_name`, `metadata.arguments`) and `message`. Count `tool_call`
  only: `invalid_tool_call` was rejected by LM Studio's own schema validation
  and **never reached the server**, so counting it credits us for a call we
  never received.
- **Error guidance is a product feature, not a diagnostic.** On the first green
  e2e run the model failed three times and recovered from the error text alone:
  a bad `time_range` (rejected client-side), then `data view 'flights' not found
  — call list_data_views to see what exists`, then `field 'price' does not
  exist … did you mean 'Carrier'?`. A model has no other way to learn your data's
  schema. `tests/e2e_replay/` regression-protects those exact strings; degrading
  one to a bare "not found" fails the tier.
- **Ephemeral-stack headroom is measured, not assumed** (2026-08-09, 11.7GB VM):
  the dev stack alone is ~4.9GB (ES 3.2, Kibana 1.2, fleet-server 0.4). The guard
  in `fleet_ephemeral.sh` is sized by *headroom*, not by VM total — re-measure
  before relaxing it, and note the VM is shared with whatever else is running.
- **Honeycomb from the live tiers (D44):** the team key is EU1, so
  `KIBANA_MCP_OTEL_ENDPOINT=https://api.eu1.honeycomb.io` (the US endpoint
  answers 401). Leave `KIBANA_MCP_OTEL_SECRET_TOKEN` unset and pass the key in
  `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=…`. `service.name` names the
  dataset. The key has `queries: false`, so read results through the
  Honeycomb MCP. The contract tier runs tools in-process and sends no spans;
  the Claude and replay tiers do.
- **`uv sync` replaces `.venv`** and with it any bootstrap-installed git
  hooks — pre-commit is a dev dependency for exactly this reason; if hooks
  ever go quiet, `uv run pre-commit install --hook-type pre-commit
  --hook-type commit-msg`.

## Machine-local state (not in git, by design)

A session on a fresh machine recreates all of this from the docs:

- `elastic-start-local/.env.seed` — machine-written by `scripts/stack.sh seed` (exactly
  KIBANA_URL and KIBANA_TEST_API_KEY; deleted by `down`).
- `.env.local` — user-owned (LMSTUDIO_* etc.); no script writes or
  deletes it. Both are loader-parsed plain `VAR=value` files (no `export`
  prefix, not shell-sourced) read by the test suites at fixture time.
- `~/.lmstudio/mcp.json` — the mcp-for-kibana stdio entry
  ([example](examples/mcp.json)).
- The running docker test stack and loaded LM Studio model.
- The `claude` CLI's sign-in, for `make test-e2e` (no API key is needed).
