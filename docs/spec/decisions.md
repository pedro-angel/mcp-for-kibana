# Decisions ledger

Status: Draft v1.9 (2026-10-17) — regeneration corpus. Consumes
[brief.md](brief.md); read alongside [design.md](design.md).

Each entry is a decision a rewrite must not silently re-litigate: what was
decided, when, and the evidence. Entries marked **probe** are live
observations against a real stack — observed behavior, not documentation.
Probes were taken on Kibana 9.4.3 unless noted; the stack pin is the newest
supported Kibana (9.5.4 since 2026-10-04) and the contract tier runs on every
supported line (D30). Raw probe records and the full process history
leave the public tree at release and live on in the private development
repository; every finding needed to rebuild is restated here in full, so
this ledger stands without them.

## Architecture and packaging

- **D1 (2026-07)** — Hexagonal with machine-enforced contracts. Layering
  and core purity are import-linter contracts in `pyproject.toml`, not
  convention. Why: the port seam is what makes toolbox-sized rewrites safe.
- **D2 (2026-07)** — Persona-first packaging. Toolboxes are chosen per
  deployment; profiles bundle toolbox+tier per persona. The stated
  rationale — tool-selection accuracy drops past ~20 tools, small local
  models earlier — is a recorded working heuristic (`profiles/README.md`),
  not a committed measurement; no controlled tool-count experiment exists
  in the run records. Study L's 11-model roster is the closest evidence.
- **D3 (2026-07)** — Three tiers, destructive hidden by default. Write
  safety is a **registration-time** tier: hidden tools never enter the
  model's context (list-time visibility is what the assembly tests pin;
  calling a hidden name fails as unknown — observed, unpinned). Curation
  is not a security boundary — the Kibana API key's RBAC is; the docs must
  always say so.
- **D4 (2026-07)** — Handle-based saved-object export/import: NDJSON stays
  server-side; a whole-space export never enters the model's context.

- **D30 (2026-10-04)** — The supported Kibana set is kibana-py's, and the
  stack pin is its newest version. `kibana.SUPPORTED_VERSIONS` (kibana-py ≥
  0.6.0; today 9.5.4 and 9.4.7) is the one declaration: the CI contract job fans
  out over it — contract tier and e2e replay on every line — and
  `scripts/stack.sh up` takes an `ES_LOCAL_VERSION` override, so one script
  provisions either line. The pin in `.env.example` and `.env.ephemeral.example`
  is always the newest supported version (owner rule); the local tiers, both
  ephemeral tiers and the live-model gate run on it, and
  `tests/unit/test_stack_pin.py` fails when a kibana-py bump moves the set until
  both pins follow. kibana-py below 0.6.0 is unsupported: on 9.5, 0.5.0 makes
  `search_dashboards` return an empty list without an error, because 9.5
  rewrapped the dashboards search envelope and only 0.6.0 restores the
  `dashboards`/`total` keys the gateway reads. Evidence (2026-10-04): contract
  tier 125 passed on 9.4.7 and 125 passed on 9.5.4; on 9.5.4 the e2e replay 4
  passed and the live-model gate (`gemma-4-12b-qat`) 1 passed — no gateway
  change. Supersedes D29.
- **D34 (2026-10-12)** — A per-tool allow list, `KIBANA_MCP_TOOLS` (owner
  spec), is the third selection axis beside toolbox and tier. Semantics:
  **published = listed ∩ tier**, nothing else. **The list alone decides**:
  the server registers the toolboxes that own the listed tools, and an
  explicit `KIBANA_MCP_TOOLBOXES` is ignored with one stderr line. A listed
  tool **above the tier stays hidden** with one stderr line naming it and the
  tier that would publish it — not an error, so one list is reusable at a
  lower tier. A listed name that is **not a tool fails startup**, all unknown
  names at once, each with a difflib "did you mean" and a pointer to
  `docs/tools.md`. Unset or empty is byte-identical to before (D7's additive
  rule). **Start time only**: runtime list changes are rejected for now —
  MCP clients' support for `notifications/tools/list_changed` is uneven and
  the HTTP transport is stateless, so a changed list would reach some
  clients and not others. **Mechanism: disable only.** The list hides every
  registered unlisted tool with `mcp.disable(names=…)` beside the tier's
  `mcp.disable(tags=…)`; it never calls `mcp.enable`, because FastMCP (4.0.10)
  documents that a later enable overrides an earlier disable, so an
  enable-based allow list could re-publish a destructive tool the tier hid.
  The names come from FastMCP's async tool listing over a throwaway server
  per toolbox, run on its own event loop in a worker thread (FastMCP has no
  synchronous public listing; registration does no I/O, so `build_server`
  stays pure). Not a security boundary (D3): the API key's RBAC is. Named
  custom toolboxes (a profile file with coherence warnings) remain open
  ([roadmap](../roadmap.md)). Pinned by `tests/unit/test_tool_allow_list.py`.

## Space targeting

- **D5 (2026-08-15, probe P7)** — Validate space existence **before**
  acting, never on the error path. Observed: calls under a nonexistent
  space id silently succeed — Kibana writes into an orphan namespace, no
  404 exists to intercept. This kills any lazy-validation design.
- **D6 (2026-08-15, probe P8)** — Space ids: pattern `^[a-z0-9_-]+$`, **no
  max length**. Observed: Kibana accepted a 300-character id live; the
  schema mirrors reality, not taste. Validation happens fail-closed at
  gateway construction; `default` is handled natively.
- **D7 (2026-08-15)** — Additive default-off: the `space` parameter absent
  means byte-identical pre-space behavior; dict results echo the effective
  space only when the caller chose one; list results carry no echo.
- **D8 (2026-08-15)** — A base URL already pinned to `/s/<id>` refuses the
  `space` parameter with guidance (two space authorities would lie).
- **D9 (2026-08-16, probes P9/P9b)** — Dashboard ids are title-derived and
  Kibana holds them globally unique across spaces: cross-space same-title
  creates conflict loudly (409); cross-space overwrite mints a new
  destination id on first restore. The feared twin-clobber hazard is
  impossible; deterministic cross-space title collisions are the real limit.
- **D10 (2026-08-18)** — Space uniformity rule: a toolbox gains the
  parameter on **all** its tools or none — partial coverage silently drops
  a model into the default space mid-flow. Consequences: `get_alerting_health`
  keeps a redundant-but-valid parameter; the five non-space toolboxes are
  excluded with reasons — `observability` (mixed axis), `fleet` (opt-in
  migration makes meaning deployment-dependent), `streams` (cluster-global
  objects; cosmetic space implies false containment), and
  `platform-admin`/`platform-health` (their objects and namespaces are
  global by nature; a space parameter would validate and then lie).
- **D11 (2026-08-18)** — Semantic caveats are contract text: preconfigured
  connectors are instance-global; `namespace_type="agnostic"` exception
  lists are shared across spaces; value-list backing indices are per-space,
  auto-created on first write.

## Security posture

- **D12 (2026-08-15)** — Mask untranslated exception text at the MCP
  boundary AND validate the env-fallback key (strip; reject CR/LF): a
  malformed env key was proven to echo verbatim into caller-visible errors
  and logs. Curated guidance errors pass the mask.
- **D13 (2026-08-15)** — KQL filter values escape backslashes before
  quotes; empty identifiers are rejected at the schema (`min_length=1`);
  connectivity errors are topology-free; HTTP mode enables host/origin
  protection on loopback binds; container base images are digest-pinned.
- **D14 (2026-07-18)** — Enrollment-key mint/revoke is deferred: under
  api-key auth an inline secret leaks into model context and an on-disk
  secret breaks the HTTP transport. Revisit only with a credential path
  that avoids both.
- **D15 (2026-08-19)** — Cloud-agent access uses a least-privilege
  fine-grained PAT scoped to this repository; branch protection (no
  deletions, no force pushes, required checks) is the enforcement locus,
  not agent instructions.
- **D35 (2026-10-12, probes on 9.5.4)** — In-place alerting edits are
  read-modify-write, and a connector edit never drops secrets silently.
  Observed: Kibana's rule PUT wipes omitted `actions`/`tags` to `[]`, rejects
  omitted `params`, resets an omitted `notify_when`, rejects GET's
  `actions[].connector_type_id` and any `enabled` key; its connector PUT resets
  omitted `config` to `{}` (`.index` then fails validation). So
  `update_alert_rule`/`update_connector` re-send every writable field and merge
  `params`/`config` key by key — except a rule's rule-level
  `notify_when`/`throttle` when the caller's `actions` carry a `frequency`:
  Kibana refuses that mix ("Cannot specify per-action frequency params when
  notify_when or throttle are defined at the rule level", observed), so the
  pair is left out and Kibana clears it. Secrets cannot be read back, and kibana-py
  0.6.0 documents that an omitted `secrets` is reset to `{}`; every type that
  holds secrets is Gold+ (`.webhook` 403s on Basic), so the wipe is not
  observable on the reference stack. Decision: only `.index` and
  `.server-log` — whose secrets schema rejects any key, observed — may be
  updated without `secrets`; any other type is refused before the write with
  guidance to pass the full secrets (`{}` clears deliberately), and results
  never carry secrets. Preconfigured connectors are refused client-side from
  `is_preconfigured` (no such connector on the stack to probe). Contract:
  [alerting](toolboxes/alerting.md).
- **D36 (2026-10-12, probes on 9.5.4)** — An in-place data-view edit checks
  the time field Kibana does not, and a refused edit changes nothing.
  Observed: the data-view update merges the keys it is sent (omitted ones
  survive), validates only types, and stores a time field that is missing or
  a keyword, an empty time field, and an empty title (which then matches
  every index); a view's fields are recomputed from its pattern on every
  read and write and are empty for a pattern that matches no index. So
  `update_data_view` sends only the changed keys, refuses empty values,
  and requires the time field to keep to be a `date` field of the view —
  checked before the write, or, when the pattern changes, after writing the
  pattern alone, with the old pattern written back on a mismatch; when a write
  after the pattern's fails (the restore, or the name/time-field write), the
  error keeps the cause and states the pattern the view now has and the call
  that undoes it. Rejected
  alternatives: re-sending every field (needless: the update merges);
  checking a new pattern's fields through Kibana's internal fields-for-wildcard
  route (internal, and absent from kibana-py). An empty field list skips the
  check, matching `create_data_view`'s acceptance of a pattern for data not
  yet ingested. Contract: [data-management](toolboxes/data-management.md).
- **D37 (2026-10-12, probes on 9.5.4)** — Library visualizations are placed
  on dashboards by reference, and every dashboards tool keeps working on a
  dashboard that holds one. Observed: the public Dashboards API stores a panel
  `{"type": "vis", "config": {"ref_id": "<visualization id>"}}` (plus an
  optional `title` override), records it as a `lens` saved-object reference,
  and reads it back unchanged with no warnings, so the unsupported-panels
  refusal does not trip and the existing read-modify-write tools carry it
  through untouched; it also stores a `ref_id` that names no visualization.
  Deleting a visualization a dashboard references succeeds (204), and the
  dashboard keeps the dangling ref and still round-trips. The visualization
  PUT is an upsert (an unknown id creates one). So `add_library_panel` checks
  the visualization exists, in the same scoped gateway, before it writes;
  `update_visualization` reads first, then fully pre-flights the new VizSpec
  against the real data view, and never creates; `get_dashboard` adds
  `visualization_id` to a by-reference panel only (inline panels keep their
  shape) and names it by the library item's title, `""` once that item is
  gone; `delete_visualization`'s description says a panel stays behind,
  broken. `update_panel` refuses a by-reference panel, naming
  `update_visualization` (or, when the item is gone, `delete_panel` then
  `add_panel`/`add_library_panel`), rather than silently unlinking it by
  writing an inline copy over it: the owner wants charts reused, and the change a user
  asks of a linked chart belongs on the chart. Rejected alternative: placing
  a by-value copy of the library config (it would work, but later edits to
  the library item would not reach the dashboard, which is what reuse means).
  Kibana 9.4.7 was not probed by hand; the contract tier covers it in CI
  (D30). Contract: [dashboards](toolboxes/dashboards.md).

## Reliability and models (measured)

- **D16 (2026-08-16)** — The server's value is measured, not asserted:
  with-MCP vs no-MCP on a frozen task = every model passing every run
  (15/15 across three Claude models × five runs) vs 5/15; 2.0–4.5× cost
  and 2.3–5.7× wall-clock reduction; every number generated from committed
  run records (`docs/server-usability.md`, `scripts/experiment/`).
- **D17 (2026-08-16)** — Error-guidance strings are product contract: real
  models recovered mid-turn *because* the error text named the fix; the
  e2e-replay tier replays those recorded turns (including a rejected call
  and its recovery) and fails on guidance regression.
- **D18 (2026-08-19)** — *The reference-model status is superseded by D33;
  the measurements stand.* The e2e reference model is `gemma-4-12b-qat`:
  5/5 on the dashboards space chain (Study L) and 2/2 on the alerting-space
  gate, while `gpt-oss-20b` characteristically drops the `space` parameter
  mid-chain (3/5 wrong-space on dashboards; on alerting 0/3 recorded — one
  of the three was a harness misconfiguration offering no alerting tools,
  0/2 with tools offered). Caveat: the gpt-oss and gemma alerting runs sit
  on different builds (the harness's server-entry fix landed between them),
  so that comparison is indicative, not controlled; the Study L dashboards
  numbers are the controlled ones. Space threading, tool-call discipline,
  and params authoring are three distinct model capabilities; gates must
  exercise all three. **Re-measured 2026-10-04 on Kibana 9.5.4**, same
  build, five runs per cell (`scripts/experiment/runs/adhoc.jsonl`):
  `gemma-4-12b-qat` 5/5 dashboards space chain and 5/5 alerting-space;
  `gemma-4-26b-a4b-qat` 4/5 and 5/5 — faster (median 66 s and 28 s against
  77 s and 84 s) but its one failure built the dashboard in the default space,
  the capability the space gates exist to catch, so the reference stays
  `gemma-4-12b-qat`. On that evidence `e2e_green` runs all three gates (the
  flights dashboard and both space chains), and the reference model is the
  `LMSTUDIO_MODEL` fallback in every live-model test. Since D31 the criterion
  is `n/a` in `dod.config`; the three tests run on demand.
- **D19 (2026-08-19)** — Replay transcripts carry recorded bytes, never
  hand-written arguments; error steps are recorded with their guidance
  needles; server-generated ids (alert rules) bind at replay time from the
  live create response since they cannot be re-derived. Provenance is
  per-transcript: most are real model turns, one (the authored
  space-dashboard sequence) has a hand-chosen call ORDER over real
  recorded wire bytes — its order carries no evidence about model behavior
  and each transcript's `recorded` block says which kind it is.
- **D31 (2026-10-04)** — *The on-demand rule below now runs `make test-e2e`,
  the Claude tier, superseded by D38; the gate decision stands.* The
  Definition of Done needs no LLM: `e2e_green` is
  `n/a` in `dod.config` as in every `.github/dod` config. Why: the gate must
  reach GO wherever the project is worked on — CI runners and Claude Code
  cloud sessions have no LM Studio runtime — and a live-model pass certifies
  only the model that ran, not the models users bring (D18's own numbers: one
  model 5/5 where another drops `space` 1 run in 5). The replay tiers carry
  the per-change guarantee: `e2e_replay_green` (recorded real-model turns
  through a real MCP client and live Kibana) and `wheel_replay_green` (the same
  against the installed wheel). What they cannot show is whether a model still
  chooses the right tools after a change to what models see, so the three
  live-model tests run on demand (`make test-e2e`) when a change touches tool
  names, descriptions or schemas — a written rule, not an automated check.
- **D32 (2026-10-11)** — An omitted `time_field` is valid on a
  `date_histogram` VizSpec; the pre-flight, not the schema, owns the
  no-time-field case. Evidence: in the efficiency study's 90 with-server
  runs (`scripts/experiment/runs/efficiency/full/`, Kibana 9.5.5) the first
  `create_dashboard` call failed in 90/90, all with the same schema error,
  `date_histogram group_by requires time_field`: a line/bar/area panel
  grouped by `date_histogram` on a date field with `time_field` omitted
  (85 runs with one such panel, 5 with two). The field's own description
  says "Auto-filled by the server if omitted", and the toolbox does fill it,
  but `VizSpec._chart_rules` rejected the call during argument parsing,
  before auto-fill could run. Every retry copied the `group_by` field into
  `time_field`; 13 Haiku runs grouped by `@timestamp` (a date field
  in the 9.5 sample logs, not the data view's time field `timestamp`) and
  failed a second time on the mismatch check. Now `time_field` is filled
  from the data view first, and `validate_spec` rejects a `date_histogram`
  only when the data view has no default time field and none was given,
  naming the field to set. The tool surface models see (names,
  descriptions, JSON schemas) is byte-identical, so D31's on-demand
  live-model run is not triggered.
- **D33 (2026-10-12)** — No model is privileged: the live-model tests drive
  whatever `LMSTUDIO_MODEL` names and fail with the fix when it is unset.
  Supersedes D18's reference-model status (owner, 2026-10-12): a model chosen
  on 2026-08-19 kept a default role because it had once been named, not
  because a later measurement chose it again. D18's numbers remain evidence
  about the models it measured; which model to drive is the caller's choice.
- **D38 (2026-10-16, owner)** — The live-model tier is driven by Claude.
  `make test-e2e` runs headless Claude Code (`claude -p`) with this server's
  tools and nothing else — `--tools ""` (no shell, files or web),
  `--strict-mcp-config`, `--permission-mode dontAsk` with the allow list
  `mcp__mcp-for-kibana` — through four tasks, five runs each, pre-registered in
  `scripts/experiment/CLAUDE_E2E.md` before any counted run: the three
  LM Studio tasks with their prompts and state-based scoring
  (`tests/_live_tasks.py`, shared by both tiers), and `fix_rule_threshold`,
  where the harness seeds a rule with a wrong threshold and the model must fix
  it in place with the update tools (D35). The model is a parameter,
  `CLAUDE_E2E_MODEL`, defaulting to `haiku` — the alias, not a dated id, so the
  default follows the current Haiku instead of keeping a model because it was
  once named (D33's concern), and every run record names the model the CLI
  reports. The Kibana API key reaches only the MCP server's environment (its
  entry in a mode-0600 config file); the model's environment drops every
  `KIBANA*` variable, and each transcript is searched for the key, a hit being
  redacted on disk and failing the run. The LM Studio tests move unchanged to
  `make test-e2e-local`, for testing small local models; D33 governs that tier
  (`LMSTUDIO_MODEL` required, no default). Why: a Claude run needs only a
  Claude sign-in, so it runs in a cloud session where LM Studio cannot, at
  under a cent per run in the four dry runs
  (`scripts/experiment/runs/claude-e2e/dryrun/`).
  Owner's answer to Q13: a Claude run is enough live-model evidence for merging
  PR #25. **Supersedes D31 in part**: D31's on-demand rule — run the live-model
  tests when a change touches tool names, descriptions or schemas — now means
  `make test-e2e` (Claude), not the LM Studio tests. D31's gate decision stands:
  `e2e_green` stays `n/a` in every DoD config; whether the gate should require
  the Claude run is open (Q16, [roadmap](../roadmap.md)). Off by default and
  outside every gate. Evidence (2026-10-17, Kibana 9.5.4, `claude-haiku-5-5`):
  the pre-registered 20 counted runs all passed and were all valid, none with
  the key in its output (`scripts/experiment/runs/claude-e2e/counted-2026-10-17/`,
  `SUMMARY.md` generated from its `runs.jsonl`).
- **D39 (2026-10-16, owner)** — The default surface may exceed the ~20-tool
  heuristic. The default selection (`dashboards,data-management` at `write`)
  grew from 16 tools to 21 with the update and library tools of D36–D37, past
  the ~20 tools that brief Goal 1 and D2 name as a working heuristic. Accepted
  (owner's answer to Q14). Why: the heuristic is recorded, not measured (D2);
  each added tool replaces a destructive delete-and-recreate that the default
  tier hides; and an operator who wants fewer tools lists them with
  `KIBANA_MCP_TOOLS` (D34). Evidence: the count is checked against a live
  listing by `tests/unit/test_profiles.py` (the `dashboards-analyst` profile,
  which is the default selection at `write`, lists 21).

## Scope and licensing (probed on Basic)

- **D20 (2026-07-16 / 2026-08-19)** — License gates are probed live, never
  assumed: `workflows`/`agent_builder` 403 "requires an Enterprise
  license"; SLOs 403 "Platinum license or higher"; `maintenance_windows`
  and `logstash` 403 "your basic license does not support";
  `entity_analytics` 403 "license does not support" (Platinum-class
  inferred from the message, not a tier Kibana named); osquery answers 200
  on Basic but is infra-gated. The Generative-AI connector type is
  hard-gated to Enterprise — a local LLM cannot substitute, the connector
  type is the gate.
- **D21 (2026-08-19)** — Method-level Basic coverage is recorded honestly:
  ~126 of ~407 Basic-tier client methods wrapped, 26 of 27 namespaces
  touched — a metric in the current client library's vocabulary; a rebuild
  on a different client re-derives it against that client's surface. The
  gap list and its build order live in the [roadmap](../roadmap.md).
  Coverage claims are audited at method level, not toolbox level.
- **D28 (2026-09-20)** — **Basic-only is the scope boundary, not a stage.**
  Functionality that needs a paid Elastic subscription is out of scope and
  will not be built: the surfaces D20 probed are recorded as *out of scope*,
  never *deferred*, and the roadmap stopped carrying them as open work.
  Why: this project runs on Basic, so it can neither build nor
  contract-test against a paid subscription — a 30-day trial certifies
  nothing that survives day 31 — and at the Enterprise tier Kibana ships
  its own MCP server as part of Agent Builder, so those surfaces are not
  worth duplicating. Consequences: `observability-sre` and `soc-analyst`
  are **withdrawn** personas rather than planned ones; a rebuild must not
  re-file SLO reads, logstash pipeline reads, the observability
  AI-assistant tool, `security-ai`, `security-entity-analytics`,
  `ai-automation`, or `alerting` maintenance windows. Licence *facts* stay
  in the corpus (D20); only the intent to build them is retired. The
  reader-facing statement is the *Licensing and scope* section carried by
  both front doors ([README](https://github.com/pedro-angel/mcp-for-kibana#licensing-and-scope),
  [docs](../index.md#licensing-and-scope)).
- **D22 (2026-07-18)** — Kibana's detection-rule bulk-action and patch
  endpoints fail 403/500 under api-key auth; rule enable/disable ships via
  full-object update instead — the api-key path constrains which Kibana
  endpoints are usable, whatever client wraps them.

## Release and identity

- **D23 (2026-08-15)** — Name: `mcp-for-kibana`. "for Kibana" is the
  referential construction Elastic's trademark policy permits; the mark
  never appears in a domain. PyPI/npm/GitHub availability verified; the
  README non-affiliation disclaimer is permanent.
- **D24 (2026-08-19)** — Claims come last: nothing is named, tagged,
  published, or reserved before every verification preceding it has
  passed; PyPI upload is rehearsed because versions are immutable.
- **D25 (2026-08-19)** — The public repository starts with exactly one
  commit on `main`, its only branch. History (including closed PRs, which
  remain reachable after a visibility flip) stays in the private
  development repository.

- **D26 (2026-08-19)** — Root `AGENTS.md` reintroduced as the regeneration
  corpus's agent entry point (the convention's standard name is the value).
  The earlier same-named methodology file stays removed; the dev-artifact
  reference guard dropped its ban on this name and keeps banning the other
  removed methodology filename (the guard's own pattern list names it —
  writing it here would trip that very guard).

- **D27 (2026-08-20)** — Single tool ownership: every tool name belongs to
  exactly one toolbox. Precedents: the data-view read tools were extracted
  from dashboards into data-management so the namespace is owned once; the
  planned AI toolbox prefixes its tools because fleet owns
  `list_agents`/`get_agent`. The server's duplicate-registration policy
  (ignore, first-in-wins) is a backstop for composition, not a sharing
  mechanism. Surfaced by owner review of this corpus — the toolbox is only
  a safe regeneration unit because contracts cannot overlap.
## Superseded

- ~~D18's reference-model status (`gemma-4-12b-qat` as the `LMSTUDIO_MODEL`
  fallback)~~ — superseded by D33; D18's measurements stand.

- ~~D31's on-demand live-model rule as the LM Studio tests (`make test-e2e` =
  the three LM Studio tests)~~ — superseded by D38: `make test-e2e` is the
  Claude tier and the LM Studio tests are `make test-e2e-local`. D31's
  `e2e_green = n/a` stands.

- ~~D29 (2026-09-21): track the newest 9.4 patch and defer Kibana 9.5~~ —
  deferred 9.5 until kibana-py released its supported-set module, and on the
  premise that the `streams` toolbox wraps `streams.upsert`, whose body 9.4
  requires `queries` in and 9.5 rejects. The module shipped in kibana-py 0.6.0,
  and the premise was wrong: the toolbox never calls `streams.upsert` (its
  writes go through `fork` and `update_ingest`). Superseded by D30.
- ~~Space scoping via base-URL suffixing (probe P1)~~ — worked but
  validated nothing; superseded by D5/D6 (validate-at-construction) once
  P7 exposed the silent-orphan hazard.
