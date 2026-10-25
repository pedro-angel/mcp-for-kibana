# Plan — platform-admin tool gaps (branch `claude/ops-admin-tools`)

Working document for a multi-session build. **Delete this file and
`.claude/workflows/ops-admin-batch.js` in the last commit before the PR.**

## Goal and owner decisions (2026-10-24)

Build every missing admin/config/maintenance tool below, end to end, battle-tested
live, on this one branch. Open the PR **only** when every available check passes.

- Tiers: decided by the agent with the repo rule (irreversible or data-losing →
  `destructive`; reversible create/edit → `write`; reads → `read`). Owner may
  revise after review.
- `test_synthetic_monitor`: if a live probe shows it does not execute on the
  stack's private location, **drop it**, record the deferral in
  `docs/roadmap.md` with the evidence, and continue.
- Telemetry: Honeycomb dataset **`mcp-for-kibana-roadmap`** (`mcp-for-kibana`
  already exists in env `test`). Honeycomb is an extra guarantee, never a DoD
  criterion.
- Library: kibana-py 0.6.0 (installed) has every method needed — table below.

## Done criteria (PR gate)

1. `KIBANA_MCP_DOD_CYCLE_STACK=1 make dod` → `VERDICT: GO` on the final tree.
2. `make test-e2e` green (tool names/descriptions change).
3. CI on the pushed branch head green (`checks.yml`, `integration.yml`).
4. Honeycomb: branch run vs. baseline shows no regression in error rate or
   per-tool latency for pre-existing tools (record the query links in the PR).
5. This file + the workflow file deleted.

## Batches (run in order — they share port/adapter/fakes/docs files)

Status: `[ ]` todo · `[~]` in progress · `[x]` committed + pushed (add sha).

| # | Status | Toolbox | Tools (tier) | kibana-py |
|---|---|---|---|---|
| B0 | [x] `f354eda` `bb469f8` | infra | OTel → Honeycomb wiring for live tiers; baseline run on `main` code | — |
| B1 | [x] `73fbd74` `250cec9` | alerting | `snooze_alert_rule`, `unsnooze_alert_rule`, `mute_alert_rule`, `unmute_alert_rule` (rule-wide), `mute_alert`, `unmute_alert`, `update_alert_rule_api_key` (all write); `disable_alert_rule` gains optional `untrack: bool = False` (additive, default off) | `alerting.snooze/unsnooze/mute_all/unmute_all/mute_alert/unmute_alert/update_api_key/disable(untrack=)` |
| B2 | [x] `5dfba64` `da54d81` | observability (Synthetics write tier) | `create_synthetic_monitor`, `update_synthetic_monitor`, `create_synthetic_param`, `update_synthetic_param`, `create_private_location`, `update_private_location` (write); `delete_synthetic_monitor`, `delete_synthetic_param`, `delete_private_location` (destructive); `test_synthetic_monitor` (write) **only if** it executes live | `synthetics.*` |
| B3 | [x] `fa94eb1` `f659545` `47fc30a` | observability (APM config) | `upsert_apm_agent_config` (write); `delete_apm_agent_config` (destructive); `create_apm_annotation` dropped (Gold-only, 403 on Basic) | `apm.create_or_update_agent_configuration/create_annotation/delete_agent_configuration` |
| B4 | [x] `5da1b18` `d1a09c6` `d897e7a` | data-management | `find_saved_objects`, `get_saved_object`, `get_default_data_view` (read); `upsert_runtime_field`, `set_default_data_view` (write); `delete_runtime_field` (destructive) | `saved_objects.find/get`, `data_views.create_or_update_runtime_field/delete_runtime_field/get_default/set_default` |
| B5 | [x] `32deb4e` `5e66bbd` `e699cd0` | platform-admin | `copy_saved_objects_to_space` (write, new copies only), `share_saved_objects_to_spaces` (write, add only); `overwrite_saved_objects_in_space` (destructive, overwrite=True), `unshare_saved_objects_from_spaces` (destructive — removing the last space deletes the object; refuse that unless `force=True`) | `spaces.copy_saved_objects/update_objects_spaces/get_shareable_references` |
| B6 | [x] `ff291a2` `d29e4cf` `e43cc83` | security-detections | `update_exception_list`, `duplicate_exception_list`, `import_exception_list` (write, as new); `export_exception_list` (read, handle + summary like `export_saved_objects`); `overwrite_exception_list` (destructive, import with overwrite) | `exception_lists.update/duplicate/export/import_lists` |
| B7 | [x] `7024d1f` `0a90fc4` `44716a0` `24bde1a` | fleet | `list_agent_tags`, `list_agent_diagnostics` (added) (read); `install_package`, `request_agent_diagnostics`, `update_agent_tags` (explicit ids) (write); `uninstall_package` (destructive, refuse a package with package policies unless `force=True`) | `fleet_epm.install_package/uninstall_package`, `fleet_agents.request_diagnostics/bulk_update_tags/get_tags` |
| B8 | [x] `a35838e` `acb0c14` | dashboards | `update_dashboard` — title, description, time_range, refresh_interval, tags; panels untouched; same unsupported-panels guard as `update_panel` (write, idempotent upsert) | `dashboards.update` |
| B9 | [ ] | final | profiles/tool lists review, docs counts, full DoD, `make test-e2e`, Honeycomb comparison, push, CI green, delete plan files, PR | — |

Names are proposals: keep them if they fit the repo's naming (verb_noun,
existing prefixes); a rename is recorded in the batch's decision entry.

## Per-batch definition of done (each batch = one commit)

Follow the exemplar commits `3f28c51` (alerting edit tools) and `afe9b17`
(library visualizations): same file set, same depth.

1. **Probe live first** (Kibana 9.5.4 dev stack, `scripts/stack.sh up && scripts/stack.sh seed`):
   real request/response shapes, error bodies, license (Basic) — evidence goes
   in the commit message and the decision entry. A surface that is not Basic
   is dropped and recorded, never stubbed.
2. **TDD**: unit tests first (`tests/unit/adapters/`, `tests/unit/toolboxes/`,
   space threading, id guidance, server assembly counts), then port
   (`ports/gateway.py`), adapter (`adapters/kibana/gateway.py`), core models,
   fake (`tests/fakes.py`), toolbox.
3. **Error text names the fix** (house rule). Credentials/secrets never echoed.
4. **Live contract tests** in `tests/contract/` for every tool; destructive ones
   on throwaway objects they create; anything that would harm the shared stack
   goes to an ephemeral tier (`scripts/ephemeral_stack.sh` / `fleet_ephemeral.sh`).
5. **Specs reconciled in the same commit**: `docs/spec/toolboxes/<toolbox>.md`
   (bump version), `docs/tools.md` (tables, Returns, counts — the drift test
   enforces names), `docs/configuration.md` tier counts, `docs/roadmap.md`,
   a new decision `D44+` in `docs/spec/decisions.md`, `CHANGELOG.md` under
   `[Unreleased]`.
6. **Gates before commit**: `make check` exit 0, `make test-contract`,
   `make test-e2e-replay`. Paste the summary lines in the commit message.
7. **Commit**: Conventional Commits, Why/Change/Evidence/Gates body like the
   exemplars, trailers from the session (hooks enforce). Never `--no-verify`.
   Push (`git push -u origin claude/ops-admin-tools`) after every batch.
8. Update this table (status + sha) in the same commit.

## B0 — telemetry wiring (ODD)

- Server already exports a span per tool call (`src/kibana_mcp/telemetry.py`).
  For Honeycomb: `KIBANA_MCP_OTEL_ENABLED=1`,
  `KIBANA_MCP_OTEL_ENDPOINT=https://api.eu1.honeycomb.io` (key is EU1; US
  returns 401), leave `KIBANA_MCP_OTEL_SECRET_TOKEN` unset and set
  `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=$HONEYCOMB_API_KEY` (exporter
  falls back to the env headers when `headers=None`),
  `KIBANA_MCP_OTEL_SERVICE_NAME=mcp-for-kibana-roadmap` (service.name → dataset),
  `OTEL_RESOURCE_ATTRIBUTES=vcs.ref.head.revision=<git sha>,deployment.environment=dev`.
- Gap: `tests/_claude_cli.py` builds the MCP server `env` from 4 fixed vars and
  strips every `KIBANA*` var from the model env, so `make test-e2e` never
  exports. Add an additive, default-off pass-through (e.g. when
  `KIBANA_MCP_OTEL_ENABLED` is set, copy `KIBANA_MCP_OTEL_*` into the server
  `env` block; `OTEL_*` already reaches it via inheritance — verify). Unit test
  + CHANGELOG + `docs/e2e-setup.md`.
- Check whether contract/e2e-replay tiers go through the server's telemetry
  setup; if not, record that only the e2e tier is observed.
- Baseline: run `make test-e2e` with telemetry at the branch base `a628515`
  code (before B1) → Honeycomb. Verify arrival with the Honeycomb MCP
  (`get_environment test`, then `run_query` on `mcp-for-kibana-roadmap`).
- Key has `queries: false` — query through the Honeycomb MCP, not the REST key.

## Resume after a session refresh

Prompt: *"Continúa el plan `.claude/plans/ops-admin-tools.md` en la rama
`claude/ops-admin-tools`."* Then:

1. `git fetch origin claude/ops-admin-tools && git checkout claude/ops-admin-tools`.
2. Read this file; pick the first batch not `[x]`.
3. Stack: `scripts/stack.sh up && scripts/stack.sh seed` (seed after every start).
4. Run the saved workflow: `Workflow({name: "ops-admin-batch", args: {batch: "B<n>"}})`
   — implement → adversarial review → fix (max 2 rounds). Then re-run
   `make check` yourself before marking the batch `[x]`.
5. Keep main-loop context small: read workflow results, not diffs.

## Child sessions (cloud env `mcp-for-kibana-authorized`)

Owner-authorized (2026-10-24): child sessions there run without permission
prompts. `environment_id` = `env_01Q9Nd8PNwPhKgVNBAd468jM`
(`create_session`, `source_url` = repo, `source_revision` = this branch,
`outcome_branch` = `claude/ops-admin-tools`, `permission_mode` never `plan`).

- **One writer at a time** on this branch: a child owns a batch end to end
  (same prompt as the workflow's implement step) while the parent does not
  push. Never two batches in parallel — they share port/adapter/fakes/docs.
- **Independent final check (B9)**: a child on a fresh clone of the final head
  runs `KIBANA_MCP_DOD_CYCLE_STACK=1 make dod` and `make test-e2e` and reports
  the verdict lines — a clean-container confirmation, not a self-certification.
- A child that finishes cleanly does not report back: check it with
  `get_session` (`status_bucket`) and `list_events` (kinds `assistant`,
  `result`), then `git fetch` the branch.

## Log

- 2026-10-24: plan written; branch created from `main` @ `a628515`.
- 2026-10-24: B0 done — e2e telemetry pass-through (D44); baseline 20/20 on `a628515` server code → Honeycomb `mcp-for-kibana-roadmap` (`mcp.run=b0-baseline`, query `po-w3GY92re2`); contract tier sends no spans.
- 2026-10-24: B0 review fix — first baseline lost 8 of 80 spans (final calls: 5 s batch delay vs Claude's SIGINT→SIGTERM→SIGKILL in ~0.5 s). Server now exports at 500 ms and flushes on SIGTERM; re-recorded baseline 20/20, 78/78 spans (`mcp.run=b0-baseline-flush`, query `Cf-Hue16ykFq`) — **this** is the B9 comparison reference; check span count per tool == runs.jsonl tool calls.
- 2026-10-24: B0 review round 2 (minor, fixed by the orchestrator): SIGTERM flush now bounded at 2 s in a daemon thread (SDK `force_flush` has no deadline; a dead collector held exit ~10 s) and a second SIGTERM ends the process at once; docstring corrected for uvicorn (it restores and re-raises into our handler). The `b0-baseline-flush` reference ran on fastmcp 4.1.3 (lock bump in `f354eda`), same as the branch — no dependency confound for B9.
- 2026-10-24: B1 done — alerting lifecycle (D45): 7 write tools + `disable_alert_rule(untrack=)`, `get_alert_rule` gains `muted`/`muted_alert_ids`; 150 tools (68/52/30), 80 space-aware. Probes: snoozes are not readable on any public route (keep `schedule_id`; max 5, expired count), strict `...sssZ` start, rule-wide mute/unmute clear per-alert mutes (so `mute_alert` refuses a muted rule). Nothing dropped; sha added by the next commit.
- 2026-10-25: B1 review round 2 (3 minor, fixed by the orchestrator): alerting spec v1.7 states the GET before the rule-wide mute/unmute and `mute_alert`, and the limits of `cleared_alert_ids` (only the clearing call reports them; read+write not atomic), D45 amended, `mute_alert_rule` description says a repeat returns `[]`; B1 shas recorded.
- 2026-10-25: B2 done — Synthetics write tier (D46): 7 write + 3 destructive tools in `observability`, `*_private_location` renamed `*_synthetic_private_location`; 160 tools (68/59/33), 80 space-aware (none of the new ones: D10). Probes: a test run executed on the demo agent (doc with its `test_run_id`, status up, once the Fleet output pointed at `elasticsearch:9200`), so `test_synthetic_monitor` shipped; Kibana stores another type's target field and duplicate param keys, checks schedule numbers against its minute list whatever the unit, and answers unknown-id deletes with 200 `deleted:false` — all handled before or after the call. Nothing dropped (managed locations, run results, bulk calls and extra monitor fields deferred in the roadmap); sha added by the next commit.
- 2026-10-25: B2 review fix — `test_synthetic_monitor` now `openWorldHint=true`, `idempotentHint=false` (a paused monitor runs and reaches its target; each run adds a `LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN` package policy that outlives a deleted location until Kibana's clean-up task removes it, ~20 min, probed); schedules sent canonical (`05m` → `5`, Kibana 400s `"05"`); `update_synthetic_param` warns a replaced value is unrecoverable and D46's tier rationale amended; contract test now asserts tag replacement, the A→B move then delete, the test-now package policy, and a bad-id location rename (Kibana answers 404 there, so the unit fixture was already right); tools.md unknown-id delete shape fixed; spec v1.3; B2 sha recorded.
- 2026-10-25: B3 done — APM agent-config writes (D47): `upsert_apm_agent_config` (write) + `delete_apm_agent_config` (destructive) in `observability`; 162 tools (68/60/34). `create_apm_annotation` dropped as out of scope (403 "Annotations require at least a gold license" on Basic; roadmap). Probes: the PUT replaces the whole map and drops a stored `agent_name` (carried over), view/delete match exactly and 404 "Not Found" (D17-style text added, `get_apm_agent_config` too), values must be strings (converted), unknown setting names and `ALL_OPTION_VALUE` are stored (the sentinel and `""` refused; a probe's `""` config was unreachable and removed via a temporary restricted-index role). Upsert overwrites only what it read and returns `previous_settings`. Sha added by the next commit.
- 2026-10-25: B3 review fix (3 minor) — a configuration stored under the literal `ALL_OPTION_VALUE` is now matched exactly (view and delete match it, probed beside the all-environments one), so the delete removes it and the upsert replaces it; the upsert refuses only to create one under the sentinel, after its read. `delete_apm_agent_config` returns `agent_name` and `upsert_apm_agent_config` takes an optional `agent_name` (sent on a create; on a replacement in place of the stored one), so a deleted configuration can be recreated whole. The read-then-overwrite race cannot be closed (Kibana's PUT 400s an `etag` in the body: "Excess keys are not allowed"), so spec v1.5, D47, tools.md and the roadmap state the limit and the overclaiming invariant is reworded; tier kept (D45 precedent). Contract tests: the agent_name round trip and the literal-sentinel case; B3 sha recorded.
- 2026-10-25: B3 review round 2 (1 blocking, 2 minor) — a replacement now keeps the stored `agent_name` and refuses a different one (none stored included) after its read, before any write, naming delete-and-recreate: taking the caller's in its place lost what it read, as the result returns only `previous_settings` (a `previous_agent_name` was rejected: adding one where none was stored could not be undone). The `ALL_OPTION_VALUE` refusal names every argument holding the sentinel and the configuration omitting them targets (both given, it said to omit `service_name` only, which would replace a stored {all services, literal sentinel}). Spec v1.6, D47 amended, tools.md; unit and contract tests for both, `make test-e2e` run for the changed schema and descriptions; B3 shas recorded.
- 2026-10-25: B4 done — data-view runtime fields, default data view, saved-objects reads (D48): 3 read + 2 write + 1 destructive in `data-management`, all with `space`; 168 tools (71/62/35), 86 space-aware; profiles grow (read-only-explorer 14, dashboards-analyst 26 — B9 reviews). Probes: Kibana stores runtime scripts without compiling (ES then fails every search on the view; no public compile route — description warns, `previous` undoes), the PUT drops custom labels (re-sent), the default set ignores a change without `force` and stores any id (forced, view read first), the first view in a space becomes its default and a deleted default's id stays (`exists: false`), find/get are deprecated routes with no replacement, unknown/hidden types answer an empty page. Nothing dropped (composite create/replace deferred); sha added by the next commit.
- 2026-10-25: B4 review fix (1 blocking, 5 minor) — `get_saved_object` now reads only a fixed allowlist of analytics content types (`READABLE_TYPES`) and refuses any other before calling Kibana: Kibana strips only registered encrypted attributes, and `fleet-proxy` returned a probe proxy's `certificate_key`/`proxy_headers` live (`ingest-outputs` maps `config_yaml`/`service_token` in plain text), which got around the fleet field allowlist. `find_saved_objects` sends `search_fields=[title, name]` (Kibana matched `JetBeats`, a description-only word). `set_default_data_view` adds `previous_exists`: a dangling previous id cannot be passed back, and `view_id=null` is the undo (Kibana's `getDefaultDataViewId` treats such an id as no default). Port docstrings and the FakeGateway now state/model the delete's mapped-field and subfield refusals; the live find test gained a no-object `has_reference` case (total 0) and the description-word case; stale 162/80/21 counts fixed. Spec v1.4, fleet spec v1.1, D48 amended; B4 sha recorded.
- 2026-10-25: B4 review round 2 (1 minor) — the `get_saved_object` refusal named find, "the type's own tools" and export for every type, and none worked for the types it was written for: Kibana 400s an export of `ingest-outputs`/`fleet-proxy`/`synthetics-param` ("Trying to export non-exportable type(s)", probed), find answers hidden `action`/`alert` an empty page, and no tool reads `fleet-proxy`. It now names per type only what works (`list_outputs`, `list_connectors`, `list_alert_rules`, `list_synthetic_params`, the agent/package-policy tools, or "no tool reads" for proxies) and, for any other type, find and export each with its condition. Unit tests per type; the live test asserts an export it names succeeds and the Fleet/Synthetics exports are refused. Spec v1.5, D48, tools.md, CHANGELOG; B4 shas recorded.
- 2026-10-25: B4 final review (4 minor, fixed by the orchestrator): `get_saved_object` refusal covers legacy Fleet policy, Fleet Server host and Synthetics monitor/location types, names the toolbox to enable for the tools it names, fallback no longer says "the type's own tools"; live test refuses `config` so the fallback's export path runs. Profiles at 14/26 tools: B9 review item.
- 2026-10-25: B5 done — saved objects between spaces (D49): `copy_saved_objects_to_spaces`, `share_saved_objects_to_spaces` (write; share idempotent), `overwrite_saved_objects_in_spaces`, `unshare_saved_objects_from_spaces` (destructive) in `platform-admin`, plural names (Kibana copies into a list); 172 tools (71/64/37), still 86 space-aware (they take `source_space`/`target_spaces`/`spaces`, never `space`, D10); dependency map 65 edges from 56 tools. Probes: copy and share write into a space id that names no space without an error (an orphan data view appeared once that space was created) → every target checked first; an object left in no space is deleted (document gone) → last-space removal refused unless `force`; kibana-py sends `spaces` unscoped on a space-scoped client (a share from a throwaway space read its own view as missing) → adapter routes under `/s/<source>`; dashboards/visualizations/searches/maps/tags/config are one-space types → share refused, copy offered; copy failures (`missing_references`, `unsupported_type`, `ambiguous_conflict`) come per object in a 200. Nothing dropped (resolve-errors route, compatibility mode, legacy aliases deferred); sha added by the next commit.
- 2026-10-25: B5 review fix (1 blocking, 2 minor) — a target space Kibana refuses as a whole (probed: a user with `all` in one target and `read` in the other gets 200, the first copied, the second a bare Boom `{statusCode: 403, error: "Forbidden", message: "Unable to bulk_create index-pattern"}` with no object) crashed the copy (`'str' object has no attribute 'get'`) and lost the other spaces' `destination_id`s; it now fails that space's requested objects with `space_error` and a fix naming Kibana's status and message, never `unchanged`, the other spaces reported as usual. The guidance no longer offers `source_space` where the deployment refuses it (the factory tells connect when the public URL is pinned, the adapter knows a pinned KIBANA_URL), and the `source_space` refusal names both sources (the space KIBANA_URL pins, or the default space when only the public URL is pinned). Spec v1.4, D49 amended, tools.md, CHANGELOG; B5 sha recorded.
- 2026-10-25: B5 review round 2 (4 minor, fixed): the guidance on a deployment that refuses `source_space` names the URL that fixes the source (`KIBANA_URL`, or `KIBANA_PUBLIC_URL` with the default space), never "the Kibana URL" when only the public URL is pinned; the 403 `space_error` fix asks for a new `KIBANA_API_KEY` (a key keeps its owner's creation-time privileges); a live contract test makes a restricted key (role, user, a key the user makes: a key made by a key has no privileges) and pins the whole-space 403 body and that a role change does not reach the key; tools.md's `source_space` paragraph matches spec v1.5. D49 amended, CHANGELOG; B5 shas recorded.
- 2026-10-25: B5 final review (2 minor, fixed by the orchestrator): the 403 `space_error` fix names `Authorization: ApiKey <key>` as well as `KIBANA_API_KEY` (HTTP mode ignores the env key by default); the live test pins the repeated 403 and the copy into the writable space.
- 2026-10-25: B6 done — exception-list edit, copy, export and import (D50): `update_exception_list`, `duplicate_exception_list`, `import_exception_list` (write), `export_exception_list` (read, `el-` handle + summary), `overwrite_exception_list` (destructive) in `security-detections`, plan names kept; `get_exception_list`/`find_exception_items` gain `namespace_type`; 177 tools (72/67/38), 91 space-aware; dependency map 70 edges from 61 tools. Probes: the list PUT needs name/description/type, ignores os_types and 409s a stale `_version` (sent → the update's race is closed); duplicate 405s endpoint lists (refused first); export needs both ids (read first); `as_new_list` keeps source item_ids and hides the new list_id (rewritten in core instead); a plain import onto an existing list_id still adds its items (refused first); overwrite deletes items added since the export (returned as `removed_item_ids`) and recreates a deleted list under a new id; imports drop os_types. Nothing dropped (os_types edits, agnostic listing in find, item edits deferred); sha added by the next commit.
- 2026-10-25: B6 review fix (1 blocking, 4 minor) — a missing or expired `el-` handle reached the model as FastMCP's masked "Error calling tool" (`read_export`'s KibanaNotFound was raised outside `gateway_errors`); `_load_export` now runs inside it, pinned by a `mask_error_details=True` unit test for both imports. `get_exception_list` → `find_exception_lists` edge added (its not-found names it; map 71 edges from 62 tools). Spec v1.3: the import invariant states the check-then-import race and what the result then shows; the namespace sentence (spec + tools.md) names the tools that take `namespace_type` and says both imports use the export's. Both import descriptions carry the agnostic warning; the agnostic contract test now imports and overwrites an agnostic export through another space. B6 sha recorded.
- 2026-10-25: B6 review round 2 (3 minor, fixed): `find_exception_items`' 404 reached the model as Kibana's raw text, though the spec said it names the other namespace; it now raises the same not-found as `get_exception_list` (ids, `find_exception_lists`, the other namespace), pinned by an adapter unit test and the live missing-list and agnostic-as-single contract checks, and gains the `find_exception_lists` edge (map 72 edges from 63 tools). The import-race invariant is now pinned: the live premise asserts Kibana's `success: false`, one 409 and the counted item for a plain import onto an existing list_id and the gateway's mapping of that body, and a unit test creates the list between the check and the import (`id: null`, `imported_item_count` 1). Spec v1.4. `make test-e2e` run for the two description changes of the previous fix: green. B6 shas recorded.
- 2026-10-25: B6 final review (2 minor, fixed by the orchestrator): list_id-only exception tools (`find_exception_items`, `duplicate_exception_list`) say to pass the `list_id` field, not the uuid `id`; a race-losing import returns `name: null`.
- 2026-10-25: B7 done — fleet advanced ops (D51): `list_agent_tags`, `list_agent_diagnostics` (read, added: the request alone never showed the bundle arrive), `install_package`, `update_agent_tags`, `request_agent_diagnostics` (write), `uninstall_package` (destructive) in `fleet`; `list_agents`/`get_agent` gain `tags`; 183 tools (74/70/39), 91 space-aware; dependency map 77 edges from 68 tools; fleet-admin 41. Probes: the tag update skips unknown ids with a 200 (refused first), applies an id list before answering (read back; `not_applied_agent_ids`), keeps `""` and a tag in both lists (refused); diagnostics READY in 6 s on the demo agent, 3 h expiry offline; an install without a version upgrades an older installed one and rollback is 403 Enterprise-only (version changes refused), an unknown name 500s the install (read first); the uninstall deletes the package's package policies, without `force` when no agent uses them (refused unless `force`, guarded Fleet Server/managed policies always). Nothing dropped (upgrades, bundle download, upload installs, k8s manifest deferred); sha added by the next commit.
- 2026-10-25: B7 review fix (3 minor) — `uninstall_package` refuses, whatever `force` says, any `fleet_server` package policy (a plain Fleet Server policy strands its Fleet Servers as the default one would; the refusal names every guarded one and `delete_package_policy` for a plain one) and `elastic_agent` while an agent policy has `monitoring_enabled` (probed: Kibana 9.5.4 has no required-package check and uninstalled it under a monitoring policy without `force`, removing the monitoring templates and pipelines), and sends Kibana's `force` only for a package with package policies. Live tests: both refusals (fleet_server never forced; elastic_agent left as found) and `include_cpu_metrics` bundled to READY (~66 s; sent first, as the agent takes one diagnostics request a minute — a second within it FAILED "rate limit exceeded", roadmap). Fleet spec v1.3, D51 amended, tools.md, CHANGELOG; B7 sha recorded.
- 2026-10-25: B7 review round 2 (2 minor, fixed) — `uninstall_package` guards a `fleet_server` package policy on no agent policy too, and with Fleet space awareness on (probed: the dev stack reads `success`; each space lists only its own agent and package policies, and Kibana's uninstall from the default space deleted another space's package policy — read by id; a listing right after read stale) reads every space's agent and package policies, names each policy's space, and refuses on a space-pinned `KIBANA_URL` while another space exists. Live: a monitoring policy and a `tcp` package policy only a throwaway space lists both refuse; fleet spec v1.4, D51 amended, roadmap (fleet tools space-blind otherwise); B7 shas recorded.
- 2026-10-25: B7 final review (1 blocking, 2 minor) — `uninstall_package` refuses, whatever `force` says, with Fleet space awareness on (fail closed): the spaces listing hides each space the key cannot read (probed: a key limited to 'default' listed only it, and Kibana's uninstall with it, no `force`, deleted a throwaway space's `tcp` package policy), and no Kibana route proves a key reads every space (`_has_privileges` needs Elasticsearch; roadmap), so the every-space reading, its space-named and pinned-URL refusals go; the fake follows the adapter for a package policy on no agent policy (fixed); the other-space fix for a guarded policy is moot (no refusal names another space now). Live: a throwaway default-only key (minted with the stack's `elastic` password) is refused and Kibana's own uninstall with it deletes the hidden package policy. Fleet spec v1.5, D51 amended, tools.md, CHANGELOG, roadmap.
- 2026-10-25: **Owner decision for B9/PR:** after the B7 fail-closed fix (`24bde1a`), `uninstall_package` refuses whenever Fleet space awareness is on (default on a fresh 9.5.4 stack), because no Kibana route lets the server prove its key reads every space and Kibana's uninstall deletes package policies in spaces the key cannot see (probed live). Options: keep as shipped (safe, works only with awareness off), drop the tool, or add the default-off ES URL opt-in on the roadmap (`_has_privileges` on resource `*`).
- 2026-10-25: B8 done — dashboard settings edit (D52): `update_dashboard` (write, idempotent) in `dashboards`: `title`, `description`, `time_range`/`clear_time_range`, `refresh_interval_seconds` (0 pauses), `tags_to_add`/`tags_to_remove` (tag ids), panels written back as read behind `update_panel`'s guard; result returns the settings, `changed`, `previous` and `undo` (arguments that put them back); 184 tools (74/71/39), 92 space-aware; dashboards-analyst 27 (default selection grows: B9 review); dependency map 79 edges from 69 tools. Probes: Kibana stores times its dashboard app cannot read (`"yesterday"`, from after to) → 9.5.4's `@kbn/datemath` grammar checked first; a refresh without a time range is stored (`timeRestore: false`) but the 9.5.4 app bundle applies it only with the stored time → refused; any string is stored as a tag id (unknown, a tag name, another space's tag) → each new id read in the same space; the PUT checks no version (stale `?version`/`If-Match` ignored) → race stated; a rename onto another dashboard's create_dashboard id refused. Nothing dropped; `make test-e2e` 20 passed (haiku); sha added by the next commit.
- 2026-10-25: B8 review (1 blocking, 4 minor; B8 sha recorded) — `update_dashboard`'s `undo` is always one the tool accepts: a refresh reads as `null` on a dashboard with no stored time range (probed: Kibana's GET returns a refresh kept after `clear_time_range`, which the app does not apply), so no undo pairs a refresh with `clear_time_range` (blocking, fixed; live: set time+refresh on such a dashboard, undo taken; the clear's own undo restores time and refresh); `refresh_interval_seconds` is a number, so a stored 1500 ms (probed: stored) reads and passes back as 1.5, under 1 ms refused (fixed); `clear_time_range` with a refresh refused before any call naming both ways out (fixed); a paused refresh coming back at the new interval, and a rename undo refused by the title check, stated in D52/tools.md/spec and pinned by tests (the undo cannot carry a paused interval in one call; telling an undo from a fresh rename onto a shared create_dashboard title would weaken the check).
- 2026-10-25: B8 review round 2 (4 minor, fixed; B8 shas recorded) — `update_dashboard`'s `undo` is the whole call (`dashboard_id` and the `space` passed: a create_dashboard id names the same title in every space, so an undo without the space edited the default space's twin); a previous value Kibana stores that the tool refuses (negative, under-1 ms or over-limit refresh, an unreadable time and the refresh beside it, a blank title, an empty tag id) is left out of `undo` and named in `undo_omitted` (a negative refresh stored live); `refresh_interval_seconds` gains `le=2147483.647` (9.5.4's timefilter hands the milliseconds to `window.setTimeout`, a signed 32-bit delay; `1e306` overflowed to a raw error); live `test_update_dashboard_refresh_values_live` pins 1.5 s ↔ 1500 ms and the under-1 ms refusal; a removed dangling tag id's undo, refused by the tag check, stated rather than read at removal (D52).
