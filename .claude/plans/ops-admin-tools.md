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
| B2 | [ ] | observability (Synthetics write tier) | `create_synthetic_monitor`, `update_synthetic_monitor`, `create_synthetic_param`, `update_synthetic_param`, `create_private_location`, `update_private_location` (write); `delete_synthetic_monitor`, `delete_synthetic_param`, `delete_private_location` (destructive); `test_synthetic_monitor` (write) **only if** it executes live | `synthetics.*` |
| B3 | [ ] | observability (APM config) | `upsert_apm_agent_config`, `create_apm_annotation` (write); `delete_apm_agent_config` (destructive) | `apm.create_or_update_agent_configuration/create_annotation/delete_agent_configuration` |
| B4 | [ ] | data-management | `find_saved_objects`, `get_saved_object`, `get_default_data_view` (read); `upsert_runtime_field`, `set_default_data_view` (write); `delete_runtime_field` (destructive) | `saved_objects.find/get`, `data_views.create_or_update_runtime_field/delete_runtime_field/get_default/set_default` |
| B5 | [ ] | platform-admin | `copy_saved_objects_to_space` (write, new copies only), `share_saved_objects_to_spaces` (write, add only); `overwrite_saved_objects_in_space` (destructive, overwrite=True), `unshare_saved_objects_from_spaces` (destructive — removing the last space deletes the object; refuse that unless `force=True`) | `spaces.copy_saved_objects/update_objects_spaces/get_shareable_references` |
| B6 | [ ] | security-detections | `update_exception_list`, `duplicate_exception_list`, `import_exception_list` (write, as new); `export_exception_list` (read, handle + summary like `export_saved_objects`); `overwrite_exception_list` (destructive, import with overwrite) | `exception_lists.update/duplicate/export/import_lists` |
| B7 | [ ] | fleet | `list_agent_tags` (read); `install_package`, `request_agent_diagnostics`, `update_agent_tags` (explicit ids) (write); `uninstall_package` (destructive, refuse a package with package policies unless `force=True`) | `fleet_epm.install_package/uninstall_package`, `fleet_agents.request_diagnostics/bulk_update_tags/get_tags` |
| B8 | [ ] | dashboards | `update_dashboard` — title, description, time_range, refresh_interval, tags; panels untouched; same unsupported-panels guard as `update_panel` (write, idempotent upsert) | `dashboards.update` |
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
