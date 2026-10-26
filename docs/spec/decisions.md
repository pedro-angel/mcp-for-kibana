# Decisions ledger

Status: Draft v1.17 (2026-10-25) — regeneration corpus. Consumes
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
  custom toolboxes (a profile file with coherence warnings) were left open
  here; they shipped as named tool lists (D41). Pinned by
  `tests/unit/test_tool_allow_list.py`.
- **D41 (2026-10-17, owner: Q10, Q11, Q12, Q15)** — Named tool lists, inside
  the D34 allow list. **Syntax**: an entry of `KIBANA_MCP_TOOLS` that starts
  with `@` names a list and stands for its tools, mixable with plain names
  (`@dashboards-analyst,create_alert_rule`); the server expands every `@`
  entry, in order, first occurrence wins, **before** it validates names, so
  every D34 rule applies to the expanded list unchanged (published = listed ∩
  tier; unknown tool fails startup, now with `from list '@<name>'` when a
  list brought it in; above-tier tools logged). An unknown `@name` fails
  startup naming every unknown one, a difflib "did you mean", and the
  available lists. **What changes without an `@` entry**: with
  `KIBANA_MCP_TOOLS` unset, none of this runs — the list file is not opened
  (one stderr line if it is named) and the server is byte-identical. With a
  plain list, the published tools — names, order, descriptions, schemas — are
  unchanged (checked once by comparing full tool dumps at every tier with the
  pre-change tree; names and order are pinned by a unit test), and so are the
  instructions, which follow the published set; what is new is on stderr (the
  coherence warning below) and the list file, which is read and validated
  whenever `KIBANA_MCP_TOOLS` is set, so a broken file fails startup even when
  no `@` entry uses it. **Sources** (owner,
  Q10/Q15): built-in lists plus the operator's own file. The built-ins are
  two shipped personas as tool names, for mixing with tools from other
  toolboxes — `read-only-explorer` (14: `dashboards`, `platform-health` and
  `data-management` at `read`) and `dashboards-analyst` (26: `dashboards` and
  `data-management` at `write`, the default selection, D39) — held as data in
  `src/kibana_mcp/tool_lists.py` (`BUILTIN_LISTS`); `profiles/README.md`
  points to the list table in `docs/configuration.md` instead of repeating
  names. **Profiles stay on toolbox + tier**, the axes that state each
  persona exactly; a list is a frozen copy. A unit test keeps each list equal
  to its profile's toolbox + tier surface, so a tool added to those toolboxes
  at that tier fails the test until the list names it. A profile that named
  its own list would break that pin (the test would compare the list with
  itself) and a new read tool would silently stay out of the persona — the
  drift that keeps `fleet-admin` from being a list: it is the whole `fleet`
  toolbox at `destructive`, which the toolbox axis says exactly. For
  dashboards-analyst, raising the tier is also how the profile gains the
  delete tools. (The first cut pointed `profiles/read-only-explorer.mcp.json`
  at `@read-only-explorer`; review found the persona unpinned and it went
  back to `dashboards,platform-health,data-management` at `read` before any
  release.) **File**: `KIBANA_MCP_TOOL_LISTS_FILE` names a
  TOML file (stdlib `tomllib`, no new dependency) — `version = 1` and
  `[lists.<name>] tools = [...]`, nothing else — documented in
  `docs/configuration.md` as a compatibility contract (a version-1 file keeps
  working on every release that reads version 1; a breaking change takes a new
  version). A leading `~` is expanded; a relative path resolves against the
  server's working directory, which the MCP client chooses, so an unreadable
  file's error says what it resolved against and asks for the absolute path
  of a readable file. Validation is strict, every error names the fix:
  unreadable or non-UTF-8 file, bad TOML, missing or other `version` (a TOML
  boolean is not `1`), unknown keys at either level, empty `tools`,
  non-string or blank entries, a list name outside `[a-z0-9][a-z0-9_-]*`
  (matched in full, so a quoted name ending in a newline is refused), a
  nested `@` reference. **Clash rule: a file list named like a built-in is refused**, so
  `@dashboards-analyst` means one surface on every deployment; refusing is
  the safer of the two (overriding would let a file silently redefine a
  documented persona). The file is read by `main()` (`load_tool_lists`), not
  by `build_server`, which takes the parsed lists and does no I/O; and only
  when `KIBANA_MCP_TOOLS` is set (otherwise ignored, one stderr line), so the
  default path never opens it. Tool names in a list are checked when the list
  is used; an unknown one from the file names the `[lists.<name>]` table and
  the file to edit. The published set that the instructions and this check
  read is worked out from the registered tools' tier tags and the allow list —
  the same two rules as the disables — not from a server-level `tools/list`,
  which would open a telemetry span at startup when OTEL is on; a unit test
  keeps it equal to what clients list. **Warning rule** (owner, Q11): when `KIBANA_MCP_TOOLS` is set — named
  or plain — and a published tool relies on a tool that is not published, the
  server logs **one** stderr line naming each missing tool and what relies on
  it, and starts anyway, the posture of a listed tool above the tier (the model
  may get the input elsewhere, e.g. an id the user pastes). Dependencies are
  followed transitively; tools hidden by the tier are not checked; toolbox
  selections are not checked. **Dependency map**: one owner, data —
  `DEPENDENCIES` in `src/kibana_mcp/tool_lists.py`, 79 edges from 69 tools.
  An edge A → B means A takes an input only B shows the model and A's own
  description or error guidance sends the model there: an index or field name
  (`update_panel`/`delete_panel` → `get_dashboard`; every VizSpec tool →
  `describe_data_view`, whose validation error says "Use describe_data_view"),
  the state an in-place edit merges into (`update_alert_rule` →
  `get_alert_rule`, D40; `update_connector` → `get_connector`, D35;
  `upsert_apm_agent_config` → `get_apm_agent_config`, D47), an export
  handle (`import_saved_objects`/`overwrite_saved_objects` →
  `export_saved_objects`; D50, `import_exception_list`/
  `overwrite_exception_list` → `export_exception_list`), or an existing object's id that the not-found
  guidance says to look up ("call search_dashboards to find it": dashboards,
  visualizations, data views, alert rules, connectors, Synthetics monitors,
  parameters and private locations, APM agent configurations; and, D48, the
  runtime-field and default-data-view writes → `list_data_views`,
  `get_saved_object` → `find_saved_objects`; D49, the four tools that move saved
  objects between spaces → `find_saved_objects` and `list_spaces`; D50, the
  exception-list get, item find, update, duplicate and export → `find_exception_lists`;
  D51, the agent-tag and diagnostics tools → `list_agents`, `install_package` →
  `list_packages`, `uninstall_package` → `list_installed_packages`; D52,
  `update_dashboard` → `search_dashboards` and, for tag ids,
  `find_saved_objects`), plus ids and values descriptions name
  (`get_space` → `list_spaces`, `get_synthetic_monitor` →
  `list_synthetic_monitors`, `create_synthetic_monitor` →
  `list_synthetic_private_locations`, `create_synthetic_private_location` →
  `list_agent_policies`, `upgrade_agent` → `list_agent_versions`,
  package-policy writes → `get_package`). Not seeded, with reasons: the
  space-not-found guidance (`space` is optional everywhere), refusal
  redirects (`update_panel` naming `update_visualization`), follow-ups
  (`create_alert_rule` naming `enable_alert_rule`, which `enabled=True`
  replaces), server-side read-modify-write tools that need no model-side read
  (`update_case`, `update_space`, fleet updates), and streams fork lifecycles
  (no streams tool names a partner for an input it takes). Edges point at the
  same or a lower tier, so the warning's fix — list the tool — is never
  blocked by the tier. Unit tests pin that both lists and the map name only
  real tools, that edges respect tiers, and that every built-in list passes
  its own check. The map grows when a tool's guidance starts naming another.
  Pinned by `tests/unit/test_tool_lists.py`, `tests/unit/test_profiles.py`.

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
  global by nature; a space parameter would validate and then lie). (D49:
  the platform-admin tools that move saved objects between spaces name the
  spaces as arguments of the operation — `source_space`, `target_spaces`,
  `spaces` — so the toolbox still takes no routing `space`.)
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
- **D43 (2026-10-17, probes on 9.5.4)** — Creating a data view checks its time
  field as D36 checks an edit, and a refused create leaves no view. Observed:
  the data-view create stores any string as the time field (`@timestamp` over
  `kibana_sample_data_flights`, whose only date field is `timestamp`; reported
  by study run `20261017T110643Z-b089-claude-haiku-5-5-with-mcp-996208`, where
  `create_dashboard` later refused the view's time field and the `write` tier of
  0.3.0 had no repair); its response carries the view's `fields`, where a
  `date_nanos` mapping is typed `date` (`esTypes` keeps `date_nanos`); a
  pattern matching no index returns no fields; and there is no public route
  that lists a pattern's fields without creating a view
  (`/api/index_patterns/_fields_for_wildcard` is 404 and
  `/api/data_views/_fields_for_wildcard` answers 400 "not available with the
  current configuration", as any unknown `/api/data_views/` path does). So
  `create_data_view` creates, checks the time field against the returned
  fields, and on a mismatch deletes the new view and fails; when that delete
  fails, the error names the view's id and the `update_data_view` /
  `delete_data_view` call that fixes or removes it. Kibana refuses a second
  view under a used name (the name defaults to the pattern) with a 400
  `Duplicate data view: <name>`, while a second view over the same pattern
  under another name is accepted; the error now adds the holding view's id
  (one list call, best effort) and names `update_data_view`. Rejected
  alternative: checking before the write through the internal
  fields-for-wildcard route (internal, as D36 records). Contract:
  [data-management](toolboxes/data-management.md).
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
- **D40 (2026-10-17, probe on 9.5.4)** — *Amended 2026-10-17: each read
  action now also carries its `uuid`; the "`uuid` because the tool does not
  take it" clause below is superseded.* `get_alert_rule` returns the rule's
  `params` and `actions`; it is the read half of `update_alert_rule` (D35),
  as `get_dashboard` is `update_panel`'s. Observed: GET
  `/api/alerting/rule/{id}` carries the `params` Kibana stores (with the
  defaults it adds, e.g. `aggType`, `groupBy`, `excludeHitsFromPreviousRun`
  on `.es-query`) and `actions` (`group`, `id`, `params`,
  `connector_type_id`, `uuid`, and `frequency` when set), plus bookkeeping
  (`created_by`, `api_key_owner`, `api_key_created_by_user`, `revision`,
  `running`, ...) and no secret: the rule's API key is never in the body,
  enabled or not, and connector secrets are not on rules; the `_find` rows
  have the same keys. kibana-py 0.6.0 returns the raw body, so no client
  change is needed. Decision: the result adds `params` verbatim and each
  action in the shape `update_alert_rule` takes (`id`, `group`, `params`,
  `frequency`, the last two only when sent), built from named keys —
  `connector_type_id` is dropped because the update rejects it, ~~`uuid`
  because the tool does not take it~~ (superseded: see the amendment below). `list_alert_rules` and `update_alert_rule` keep
  the summary. Why: with params invisible, the first counted Claude block's
  `fix_rule_threshold` answers all said the model could not see the rule's
  params (`scripts/experiment/runs/claude-e2e/counted-2026-10-17/`
  transcripts). Not done here, left open: returning the detail from
  `update_alert_rule` as well (as `update_connector` returns `get_connector`'s
  shape); the change keeps to the read tool. Evidence after the change: the
  counted block `scripts/experiment/runs/claude-e2e/counted-2026-10-17-d40/`
  (same pre-registered tasks, `SUMMARY.md` generated from its `runs.jsonl`);
  every `fix_rule_threshold` transcript in it calls `get_alert_rule` before and
  after `update_alert_rule`. Contract: [alerting](toolboxes/alerting.md).
  **Amendment (2026-10-17, probe on 9.5.4, independent review of PR #25).**
  The premise for dropping `uuid` was wrong: `update_alert_rule` passes its
  `actions` through to the PUT, which accepts `uuid`, so a read →
  update round trip without it made Kibana mint a new uuid for every action.
  Observed on a `.es-query` rule with two `.server-log` actions: a PUT whose
  actions omit `uuid` replaced both uuids with new ones; a PUT that sends the
  read uuids kept both; and `get_alert_rule` → `update_alert_rule` with the
  read actions (now carrying `uuid`) kept both. Decision: each read action
  carries `uuid` when Kibana sent it, and `get_alert_rule`'s description says
  to pass it back; `connector_type_id` is still dropped. Evidence: the live
  assertions in
  `tests/contract/test_gateway_contract.py::test_get_alert_rule_reads_params_before_and_after_a_threshold_fix_live`
  (round trip keeps the uuids, an action sent without one gets a new uuid).
- **D45 (2026-10-24, probes on 9.5.4, Basic)** — A rule's notification
  lifecycle ships as seven `write` tools plus an additive, default-off
  `untrack` on `disable_alert_rule`: `mute_alert_rule`, `unmute_alert_rule`,
  `mute_alert`, `unmute_alert`, `snooze_alert_rule`, `unsnooze_alert_rule`,
  `update_alert_rule_api_key` (the plan's names kept: verb_noun, the
  toolbox's `_alert_rule` suffix; `mute_alert` names the per-alert object).
  **Tiers, by the repo rule (irreversible or data-losing → destructive):**
  none deletes or loses data and each has an inverse — unmute, unsnooze, a
  second key refresh by a caller with the right privileges, re-enable — so
  all are `write`. (Amended after review, same day: Kibana's rule-wide mute
  and unmute wipe the per-alert mutes, and no public route lists a rule's
  alert ids, so as first built those two lost state. Both now read the rule
  first and return the wiped ids as `cleared_alert_ids`, which `mute_alert`
  takes back. That keeps them on `write`, with two limits stated in the
  spec: only the call that clears the mutes reports them (a repeat or the
  inverse returns `[]`), and the read and the write are not atomic, so a
  per-alert mute landing between them is lost unreported. Moving them to destructive was
  rejected: an unmute is the inverse of a mute and belongs at the same tier.
  Refusing an unmute on a rule that is not muted was rejected too: Kibana's
  call is also the one way to clear every per-alert mute at once.) `untrack` was weighed for destructive and kept on `write`:
  the alert documents stay in the index (status `untracked`), and re-enabling
  the rule raises alerts afresh; it ends the alerts' lifecycle, which is what
  disabling a rule is for. The key refresh changes who the rule runs as (the
  server's API key); it is `write` because nothing is lost and its
  description says so. **Observed** (rule `.es-query` over
  `kibana_sample_data_flights`): `_mute_all`, `_unmute_all`, `_mute`,
  `_unmute`, `_update_api_key`, `_disable` and the snooze DELETE answer 204
  with no body, and repeat with 204 (hence `idempotentHint=true`, except the
  snooze, whose every call adds a schedule); a missing rule is a 404 "Saved
  object [alert/<id>] not found" everywhere, answered with D17's rule
  guidance. Kibana's rule-wide mute and unmute both reset `muted_alert_ids`
  to `[]` (the unmute does so even on a rule not muted as a whole), so both
  tools return the ids they cleared, and a per-alert mute on a rule muted as a whole answers 204 and
  records nothing — so `mute_alert` reads the rule first and refuses that
  case, naming `unmute_alert_rule` then `mute_alert`, rather than reporting a
  mute Kibana discarded. A per-alert mute of an id the rule has not raised is
  a 404 ("Alert instance with id ... does not exist") under Kibana's
  `validate_alerts_existence` default, which the tool keeps (a typo is
  refused, not silently stored; `false` stored it, observed); an ungrouped
  `.es-query` rule's alert id is `query matched`, a grouped one's the term
  value (`JetBeats`); unmuting an alert that is not muted is a 204. The GET
  carries `mute_all` and `muted_alert_ids`, so `get_alert_rule` returns them
  as `muted` and `muted_alert_ids`, the read half of the mute tools (D40's
  pattern). **Snoozes are invisible on the public API**: absent from the
  GET, from `_find` and from `_find?fields=snooze_schedule`; only
  `/internal/alerting/rule/{id}` (internal, the UI's route) returns them. A
  rule holds at most 5 and expired ones count (a 2020 window blocked a sixth,
  "Rule cannot have more than 5 snooze schedules"). So `snooze_alert_rule`
  returns the stored schedule with its `schedule_id`, its description says to
  keep it, the limit's error names `unsnooze_alert_rule` and Kibana's Rules
  page, and an unknown id (404 "Rule has no snooze schedule with id <id>."
  while others exist, 400 "Rule has no snooze schedules." with none) names
  `snooze_alert_rule`. Kibana takes `start`/`recurring.end` only as
  `YYYY-MM-DDTHH:mm:ss.sssZ` (`...:00Z` and `+02:00` are 400s; a past start is
  accepted) and `duration` only as a whole number of `s`/`m`/`h`/`d` (`1w`,
  `1M`, `1y`, `1.5h`, `2H`, `-1` are 400s; `0h` is accepted and refused here
  as meaningless); `recurring.every` takes `h`/`d`/`w`/`M`/`y` and must exceed
  `duration`. So the tool takes any ISO-8601 instant (no offset read as UTC,
  default now) and the adapter sends the strict UTC form; the duration is
  checked before the gateway with an error naming the fix. `timezone` is not
  an argument (Kibana stores `UTC`). The key refresh bumped `updated_at` and
  left the definition and `enabled` as they were. `untrack`: a default
  disable left the rule's 3 alerts `active` in `.alerts-stack.alerts-default`;
  `{"untrack": true}` turned them `untracked`; so the request without the
  argument stays the pre-existing one (no body), and the result gains
  `"untracked": true` only when asked. Rejected alternatives: reading snoozes
  or alert ids through internal routes or the alerts index (internal, as D36
  and D43 record; the alerts index is not a Kibana API); exposing
  `validate_alerts_existence` (no task needs pre-muting an alert, and the
  default catches typos). Contract: [alerting](toolboxes/alerting.md).
- **D46 (2026-10-25, probes on 9.5.4, Basic)** — The Synthetics write tier
  ships in the `observability` toolbox as seven `write` tools —
  `create_synthetic_monitor`, `update_synthetic_monitor`,
  `test_synthetic_monitor`, `create_synthetic_param`, `update_synthetic_param`,
  `create_synthetic_private_location`, `update_synthetic_private_location` —
  and three `destructive` ones: `delete_synthetic_monitor`,
  `delete_synthetic_param`, `delete_synthetic_private_location`. The plan's
  `create/update/delete_private_location` gained the toolbox's `synthetic_`
  prefix to match `list_synthetic_private_locations`. **Tiers, by the repo
  rule (irreversible or data-losing → destructive):** each delete is
  irreversible — a recreated object gets a new id, and a deleted parameter
  loses its key, its object and its value at once, with nothing left to edit
  back (the value cannot be read back on any route) — so all three are
  `destructive`; every create and edit has an inverse (edit back, delete),
  so they are `write`. (Amended after review, same day: first written, this
  justified the delete by the unreadable value while the update could
  overwrite that same value at `write`. The two differ in what is left: an
  update keeps the parameter, and the caller supplies the new value, so the
  inverse is another update — which, for the value, needs the caller to hold
  the old one; the update's description now says no tool can recover a
  replaced value. That follows `update_connector`, whose write-only secrets
  are edited at `write`. The run was also first justified as making "the
  requests the monitor already makes on its schedule" and as "changing
  nothing", both wrong: Kibana sends the run's config enabled, so a paused
  monitor's requests reach its target, and each run adds a
  `LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN` package policy to the location's agent
  policy. So the run now carries `openWorldHint=true` and
  `idempotentHint=false`, like `execute_connector`, but `destructiveHint=false`
  and stays `write`: it deletes and overwrites nothing, and Kibana removes its
  package policy itself — its clean-up task deletes lightweight test-now
  policies older than 2 minutes, browser ones older than 15, runs right after
  each run and then every 20 minutes while any remain (Kibana 9.5.4's
  `clean_up_task`, and observed: a run's policy, left on its agent policy
  after the location was deleted, was gone after the next pass). The review
  also found leading-zero schedules (`05m`) passing the check and failing in
  Kibana ("Invalid schedule 05 minutes"), now sent canonical, and confirmed
  that an update's `tags` replace the list (now asserted live with a
  non-superset edit).) Monitors start enabled
  (Kibana's default; `enabled=False` pauses one) — unlike alert rules, which
  start disabled because their actions notify people, a monitor only checks
  the target the caller named. **No `space`** on any of them: D10's uniformity
  rule keeps the toolbox's mixed-axis exclusion, so the new tools follow the
  reads (default space only). **Observed:** a private location needs a Fleet
  agent policy that no other location uses (400 "Private location with
  agentPolicyId … already exists"), labels are unique (400 "… with label …
  already exists"), a missing policy is a 400 "Agent policy with id … not
  found in space default"; only the label is editable; a location used by
  monitors is a 400 "… cannot be deleted because it is used by 1 monitor(s)",
  an unknown one a 400 "… does not exist." Monitors take a location by id or
  by label; with none, "At least one location is required"; no
  Elastic-managed location exists on a self-managed stack ("No Elastic
  managed location available to use"), so only `private_locations` is
  offered. Kibana validates the target per type ("Invalid field \"url\", must be
  a non-empty string") but its message drops that detail, and it stores
  another type's field (a `url` on a tcp monitor answered 200 and was
  stored), so the tools check both before the write. The schedule number is
  checked against Kibana's minute list (1, 2, 3, 5, 10, 15, 20, 30, 60, 120,
  240) whatever the unit — `15s` and `240s` pass, `45s`, `90s` and `7m` fail —
  and the 400 drops the list, so the tool checks it and names the list. Names
  are unique (400 "Monitor name must be unique"). The monitor PUT is partial:
  a tags-only or enabled-only update kept everything else, so the update sends
  only the given fields and reads the monitor first only to learn its type
  for the target check; the type cannot change (400). A monitor or parameter
  DELETE of an unknown id answers 200 with `deleted: false`, so the adapter
  turns that into D17's not-found. **Parameters:** Kibana stores a second
  parameter with an existing key and accepts any key text, so a key in use
  is refused (`${key}` would be ambiguous); the create response carries no
  value, but the update response echoes the *previous* value, so the shape
  has no value field and a payload rejection is scrubbed of the value; an
  empty value is a 400, an update with no field a 400 "Request body cannot be
  empty" (refused here first). **The run executes:** with a location on the
  demo agent's policy, `POST /api/synthetics/monitor/test/{id}` answered
  `{"testRunId": …}` and the agent's heartbeat ran the check; with the Fleet
  output pointed at `http://elasticsearch:9200` for the probe (the stack's
  default output is `localhost:9200`, unreachable from the agent container),
  a document with that `test_run_id` and `monitor.status: up` reached
  `synthetics-http-default`. So `test_synthetic_monitor` ships; the contract
  tier asserts only the accepted run, since re-proving execution would rewire
  the shared demo policy. No public route reads a run's result, so the tool
  returns the id and says where the result lands. `get_synthetic_monitor`
  takes the same D17 not-found text, since `update_synthetic_monitor` reads
  through it (Kibana's own is "Monitor id <id> not found!"). Each run adds a
  `LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN` package policy to the agent policy,
  which outlives the location until Kibana's clean-up task removes it (see
  the amendment above); a location's PUT on an unknown id is a 404 "… does
  not exist." (its DELETE a 400), both read as not-found. Rejected alternatives:
  `space` on the write tools only (D10); exposing every monitor field or
  kibana-py's `fields` passthrough (no task needs them; each is an additive
  argument later); checking agent enrollment before a run (it costs a
  location and an agent-policy read per location, and an assigned agent is
  not a running one, so a pass would still promise nothing — the description
  states the condition instead). Contract:
  [observability](toolboxes/observability.md).
- **D47 (2026-10-25, probes on 9.5.4, Basic)** — APM central configuration
  gains one `write` tool, `upsert_apm_agent_config`, and one `destructive`
  one, `delete_apm_agent_config`, in the `observability` toolbox (the plan's
  names kept: verb_noun with the toolbox's `apm_agent_config` noun, as the
  reads `list_apm_agent_configs`/`get_apm_agent_config` have it; "upsert"
  because Kibana's one PUT creates or replaces). **The plan's third tool,
  `create_apm_annotation`, is dropped as out of scope (D28):** Kibana answers
  `POST /api/apm/services/{service}/annotation` on Basic with 403
  "Annotations require at least a gold license or a trial license." with and
  without environment, message and tags (a malformed `@timestamp` is a 400
  first: the body is checked before the licence). **Tiers, by the repo rule
  (irreversible or data-losing → destructive):** the upsert loses nothing —
  it reads the configuration it would replace and returns its settings as
  `previous_settings`, so a second upsert restores it (the etag and
  `applied_by_agent` are Kibana's to recompute), and it overwrites only a
  configuration it read: with none it sends no `overwrite`, so one created in
  between is Kibana's 400 "A configuration already exists … Use
  ?overwrite=true", answered with "call upsert_apm_agent_config again" rather
  than replaced unreported — so it is `write`, with `idempotentHint=true` (a
  repeat stores the same map). (Amended after review, same day: the read and
  the overwrite are not atomic, and cannot be made so — Kibana's PUT takes
  no etag or version, probed: an `etag` in the body is a 400 "Excess keys are
  not allowed: body.etag", and its route overwrites the document it finds by
  id — so a change another client stores between the call's read and its
  PUT is replaced unreported and `previous_settings` is the map the call
  read. That limit is stated in the spec and the tier kept, as for D45's
  rule-wide mutes: the window is within one call, and the alternative of
  refusing replacements entirely would leave no tool to change a setting.) The delete is `destructive` like every delete
  here: the stored configuration is gone and agents fall to whatever else
  matches them; it returns the deleted settings so a caller can recreate it,
  which is a recovery path, not an inverse (a new document). Rejected: merge
  semantics for `settings` (Kibana's PUT replaces the map, probed: a
  `{"capture_body": "all"}` overwrite of `{"transaction_sample_rate": "0.1",
  "recording": "true"}` stored only `capture_body`; a merge would need a null
  to remove a key, and the replaced map is returned anyway); `overwrite=true`
  on every call (it would replace a configuration the tool never read).
  **Observed:** the PUT answers `{}`, so the tool reads the configuration back;
  the view and the delete match `service.name`/`service.environment`
  exactly — a `p-x/prod` configuration is a 404 for `name=p-x` alone — and
  their 404 says only "Not Found", so both (and `get_apm_agent_config`, which
  the upsert reads through) answer "no APM agent configuration for service
  '…', all environments — service_name and environment must match a
  configuration exactly (an omitted one means all); call
  list_apm_agent_configs to see them". Values must be strings (a number
  `0.5` and a boolean `false` are 400s "does not match expected type
  string"), so the tool converts them (`false` → `"false"`, numbers by
  `str()`). Kibana validates the values of the settings it knows
  (`transaction_sample_rate` `"7"`, `"1e-05"`, `"0.123456"`; `log_level`
  `"loud"`; `api_request_time` `"10x"`; `recording` `"maybe"`) but its
  message can omit which setting failed ("Must be a number between 0.0000
  and 1"), so the error adds the settings sent; it stores names it does not
  know (`no_such_setting` and an empty map both answered 200). An empty map
  is refused here (a configuration that sets nothing; the error names the
  delete); an unknown name is not, because no public route serves Kibana's
  setting definitions and a list kept here would drift from the agents' — the
  description says to take names from the agent's docs. Kibana stores
  `ALL_OPTION_VALUE` as a literal environment (`list_apm_environments` then
  marks it `alreadyConfigured`, though no agent would match it), so the
  upsert refuses to create one under it, for either name (amended after
  review, same day: first built, both tools refused it outright and told the
  caller to omit the name — but the view and the delete match a literal
  `ALL_OPTION_VALUE` exactly, probed beside the service's all-environments
  configuration, so that advice pointed the delete at the other, real
  all-environments configuration and left the literal one unreachable; now
  the delete matches it like any name, and the upsert replaces one that is
  stored, refusing only after its read finds none); it stores `service.name: ""` and then cannot
  reach it again (the view and the delete read `""` as omitted, so the
  probe's one was removed from `.apm-agent-configuration` with a temporary
  restricted-index role), so `""` is refused at the schema. Kibana's PUT
  drops a stored `agent_name` (the UI writes one) unless it is sent again
  (probed: `agent_name: "java"` vanished after an overwrite without it), so a
  replacement sends the stored one, and the read shape gains `agent_name`
  (`null` when none, the key being absent then). (Amended after review, same
  day: the delete returned only the settings and the upsert took no
  `agent_name`, so recreating a deleted configuration lost it; the delete
  now returns `agent_name` and the upsert takes an optional one — sent on a
  create, and on a replacement in place of the stored one — default none,
  so earlier calls behave as before. Amended after a second review, same
  day: a replacement that took a caller's `agent_name` in place of the stored
  one lost what it read, since the result returns only `previous_settings`;
  a replacement now keeps the stored `agent_name` and refuses a different one
  (none stored included) after its read, before any write, naming
  delete-and-recreate — so the upsert loses nothing again, and Kibana's UI
  does not edit it either. Returning a `previous_agent_name` instead was
  rejected: a replacement that added one where none was stored could not be
  undone, because an omitted `agent_name` keeps the stored one. The sentinel
  refusal also names every argument holding `ALL_OPTION_VALUE`: with both
  given it said to omit `service_name` only, which keeps `environment`
  literal and would replace a configuration stored under {all services,
  literal `ALL_OPTION_VALUE`}.) **No `space`** (D10: APM
  configuration is cluster-global, the toolbox keeps its mixed-axis
  exclusion). Contract: [observability](toolboxes/observability.md).
- **D48 (2026-10-25, probes on 9.5.4, Basic)** — The data-management toolbox
  gains three `read` tools (`find_saved_objects`, `get_saved_object`,
  `get_default_data_view`), two `write` ones (`upsert_runtime_field`,
  `set_default_data_view`, both `idempotentHint=true`) and one `destructive`
  one (`delete_runtime_field`); the plan's names kept (verb_noun; "upsert"
  because Kibana's runtime-field PUT creates or replaces). All six take
  `space` (D10: the toolbox is space-aware; the default data view is a
  per-space setting, and the saved-objects routes are space-scoped), so 86
  tools are space-aware. Nothing was dropped: every route is Basic and
  worked live. **Tiers, by the repo rule (irreversible or data-losing →
  destructive):** the runtime-field upsert loses nothing — it reads the view,
  returns the definition it replaces as `previous` (a second call with its
  type and script puts it back), re-sends the stored custom label and
  description that Kibana's PUT drops unless sent again (probed: a PUT
  without them left `fieldAttrs` empty; so did the partial POST), and refuses
  what it could not put back, a stored composite field — so it is `write`.
  Its read and write are not atomic (D45/D47 precedent): a create uses the
  POST, which 400s "Field [name = x] already exists." for a name added in
  between, answered "call upsert_runtime_field again"; a replacement uses the
  PUT, so a change stored in between is replaced and `previous` is what the
  call read. The default-data-view set returns `previous_id`, and a second
  call restores it (`view_id=null` restores none), so it is `write`; a
  previous id that names no view (a deleted default's, kept by Kibana)
  cannot be passed back, since the set refuses such ids, so the result adds
  `previous_exists` and `view_id=null` is the undo then: Kibana treats an
  id that names no view as no default (its data-views service
  `getDefaultDataViewId` removes it and takes the first view, read in the
  9.5.4 source), so only the dangling string is lost (review fix); the
  runtime-field delete is `destructive` like every delete here, returning
  the deleted definition as a recovery path. **Observed, and what each
  shaped:** Kibana's runtime-field routes store any script without compiling
  it (`this is not painless(((` answered 200), and Elasticsearch fails a
  whole search whose `runtime_mappings` carry a script that does not
  compile, even when the search does not use the field (`script_exception:
  compile error`, probed against the flights index), so a broken script
  breaks the searches Kibana runs on the view; no public Kibana route
  compiles a script (`/api/console/proxy` answered 400 "not available with
  the current configuration"; field preview is internal), so the
  description warns and names the way back instead of checking. The type
  list is Kibana's (`keyword`, `long`, `double`, `date`, `ip`, `boolean`,
  `geo_point`, `composite`, from its 400); `composite` needs subfields
  ("Can't add composite runtime field … without subfields") and Kibana keeps
  labels per subfield, so it is deleted but not created or replaced here
  (roadmap). A name the view maps is a 400 "Only runtime fields can be
  updated" on the PUT and "Field [name = Carrier] already exists." on the
  POST — refused before the write with the fix. A field without a script
  is stored (it reads the `_source` value of its name), so `script` is
  optional. The DELETE answers 404 "Field [index_pattern = <id>, field =
  <name>] not found." for a missing name, a mapped field and a composite's
  subfield alike, so the delete reads the view first and says which; Kibana
  keeps the field's count and format in `fieldAttrs`/`fieldFormats` after a
  delete. **The default data view:** `POST /api/data_views/default` stores
  any id (`bogus-id` answered 200, and so did another space's view id), so
  the set reads the view first and a miss is D17's data-view guidance;
  without `force` it answers 200 `{"acknowledged": true}` and keeps a
  default already set, so the tool always sends `force: true`; `null` with
  `force` clears it (`{"data_view_id": ""}`). Kibana makes the first data
  view created in a space with no default its default (observed in a fresh
  space), and keeps the id of a deleted default (the shared stack's default
  space held one, `de4dee2d-…`, of a view since deleted), so the get
  returns `exists: false` for it rather than a not-found. **The saved-objects
  reads** use `GET /api/saved_objects/_find` and `GET
  /api/saved_objects/{type}/{id}`, which Kibana 9.5.4 serves with `warning:
  299 Kibana-9.5.4 "This endpoint is deprecated"` and kibana-py marks
  deprecated. They are built anyway, superseding the data-management
  contract's earlier "granular find/get deferred" line on this evidence: no
  public route replaces a cross-type find or a `has_reference` search (the
  one open on the roadmap since D37 for `delete_visualization`), and the
  contract tier catches a removal; raw saved-objects writes stay out
  (deprecated, and the type-specific tools cover them). The find answers
  200 with no hits for an unknown type (`bogus`), for `*` and for hidden
  types (`action`, `alert`), so an empty result carries a `note` and `*` is
  refused (`export_saved_objects` takes it); it 400s past
  `index.max_result_window` ("Result window is too large … 10000") and on
  `page=0` ("[from] parameter cannot be negative"), so `page ≥ 1`,
  `per_page ≤ 100` and `page * per_page ≤ 10,000` are checked first; it
  400s sorting by a text field (`sort_field=title`: "Fielddata is
  disabled"), so no sort is offered; `fields=title&fields=name` keeps each
  hit's attributes small (the flights dashboard's are about 35 KB). The get
  answers 404 "Saved object [action/x] not found" for hidden types as for
  missing ids, so the miss names `find_saved_objects`; it strips encrypted
  attributes (a Synthetics parameter's `value` is absent, probed) but only
  those a plugin registers with encryptedSavedObjects, and some types it
  serves keep credentials in plain attributes: `fleet-proxy` (`hidden:
  false`, not registered) returned a probe proxy's `certificate_key` and
  `proxy_headers` live, and `ingest-outputs` maps `config_yaml`,
  `otel_exporter_config_yaml` and a top-level `service_token` outside its
  encrypted set (`ssl`, `password`, `kibana_api_key`) — so the get reads a
  fixed allowlist of analytics content types (`READABLE_TYPES`) and refuses
  every other type, hidden ones included, before calling Kibana (review
  fix: the fleet contract's field-allowlist rule, applied per type here;
  a denylist would miss the next plugin's type). The refusal names
  only ways forward that work for the type (review fix: it named find,
  "the type's own tools" and export for every type, but the export route
  400s Fleet's and Synthetics' types, "Trying to export non-exportable
  type(s): ingest-outputs" and `fleet-proxy`/`synthetics-param` likewise,
  probed, find answers hidden `action`/`alert` an empty page, and no tool
  reads `fleet-proxy`): `list_outputs`, `list_connectors`,
  `list_alert_rules`, `list_synthetic_params`, agent/package-policy tools,
  or "no tool reads" per known type, and for any other type find and export
  each with the condition under which it holds. (Second review, same day:
  the per-type list also covers the legacy `ingest-agent-policies`/
  `ingest-package-policies`, `fleet-fleet-server-host`, the Synthetics
  monitor and private-location types, each 400 on export, probed; the text
  names the toolbox of the tools it names and how to enable it
  (`KIBANA_MCP_TOOLBOXES`/`KIBANA_MCP_TOOLS`), because `read-only-explorer`
  and `dashboards-analyst` ship `get_saved_object` without them; the
  fallback says no tool reads the type instead of "the type's own tools";
  the live test refuses `config` too, so the fallback's export runs.) Without `search_fields`
  the find matches `search` against every attribute (`JetBeats`, only in
  the flights dashboard's description, found it), so it sends
  `search_fields=[title, name]`, the attributes a hit shows (review fix:
  the description said title words; it also closes a word oracle on
  attributes the tools never return). A dashboard's `panelsJSON` (33,809 characters on the
  flights sample) would fill the context, so attributes over 4,000
  characters are listed by size in `omitted_attributes` and references are
  returned as the distinct `{type, id}` pairs (27 references, 4 objects).
  **The profiles grow:** `read-only-explorer` and `dashboards-analyst` are
  whole toolboxes at a tier (D39, D41), so they gain the new tools (14 and
  26 tools; the dependency map 57 edges from 52 tools); the 26 passes the
  ~20-tool heuristic, which the ops-admin plan's final batch reviews with
  the owner rather than this batch moving tools out of their toolbox.
  Rejected: validating scripts against Elasticsearch (the server reaches
  Kibana only); `composite` in the upsert (subfield labels and their
  carry-over, for no task yet); a `sort_field` argument (text fields 400);
  returning whole attributes from `get_saved_object` (size), or only an
  allow-list of attributes (it would differ per type and drift; the type
  allowlist above is coarser and fixed); a denylist of credential-bearing
  types (it fails open on a type added later). Contract:
  [data-management](toolboxes/data-management.md).
- **D49 (2026-10-25, probes on 9.5.4, Basic)** — The platform-admin toolbox
  gains saved objects between spaces: `copy_saved_objects_to_spaces` and
  `share_saved_objects_to_spaces` (`write`; the share `idempotentHint=true`),
  `overwrite_saved_objects_in_spaces` and `unshare_saved_objects_from_spaces`
  (`destructive`), over kibana-py's `spaces.copy_saved_objects`,
  `get_shareable_references` and `update_objects_spaces`, all Basic and all
  working live. **Names:** the plan's `copy_saved_objects_to_space` and
  `overwrite_saved_objects_in_space` became plural (`…_to_spaces`,
  `…_in_spaces`): Kibana copies into a list of spaces in one call, and the
  pair now reads like the share/unshare pair. **Tiers, by the repo rule
  (irreversible or data-losing → destructive):** the copy always sends
  `createNewCopies` (Kibana's default, probed: new ids for the object and
  each reference), so nothing existing changes and deleting the listed
  `destination_id`s undoes it — `write`, not idempotent (a repeat makes more
  copies); the share only adds spaces and a repeat answers the same spaces
  (probed), and the unshare of the `added` ones undoes it — `write`; the
  overwrite sends `overwrite` without `createNewCopies` (Kibana 400s the two
  together: "cannot use [overwrite] with [createNewCopies]") and replaced
  an edited copy's content with the source's (probed: a data view renamed
  in the target came back), so the edit is lost — `destructive`; the unshare
  removes access, which breaks objects in that space that use it, and Kibana
  **deletes** an object left in no space (spaces `[]`, its `.kibana_analytics`
  document gone, probed) — `destructive`, and removing an object's last
  space is refused unless `force=True` (the plan's rule). **Spaces as
  arguments, not routing (D10 kept):** the toolbox still takes no `space`;
  the four tools take `source_space` (where the objects are read, validated
  at gateway construction like `space`, D5), `target_spaces` and `spaces`,
  and echo `source_space` only when passed (D7's rule). A `/s/<id>`
  `KIBANA_URL` is the source when `source_space` is omitted and refuses it
  (D8), so the adapter learns the pinned id at connect to know the source.
  **Observed, and what each shaped:** the copy and share routes accept a
  space id that names no space and write into it without an error — a copy
  into `b5-nope` answered success, and the data view appeared once a space
  with that id was created; a share answered `spaces: ["b5-ghost", …]` — so
  every space written into is checked against the space list first (`*`
  excepted, D5); the unshare writes nothing new and is not checked, so it can
  clean such an id. kibana-py serves `spaces` unscoped on a space-scoped
  client and never prefixes these paths, so a share from a throwaway space
  read its own data view as `isMissing` (probed through kibana-py); the
  adapter sends them under `/s/<source>` itself (a `SpacesClient` subclass,
  unit-pinned by path, contract-pinned live). The copy answers 200 with
  per-object failures — `missing_references` (the flights dashboard copied
  without its references, nothing written), `unsupported_type` (Fleet's
  `ingest-outputs`), `ambiguous_conflict` with `destinations` (overwrite
  into a space holding two copies of one source), `conflict` (only without
  either mode, which no tool sends) — so each failure carries Kibana's type
  and a `fix`, and 404 "Saved objects not found" with the misses for a
  missing object or an unknown or hidden type, worded with
  `find_saved_objects`; overwriting an object already shared to the target
  answers success with `successCount: 0` and nothing else, reported as
  `unchanged`. Copying into the source space duplicates the objects there
  and overwriting into it changes nothing (probed), so the source space is
  refused as a target. Kibana 400s a repeated object and a repeated space
  on the copy ("duplicate objects are not allowed", "duplicate space ids are
  not allowed") and answers 409 for the repeat on the share, so repeats are
  refused first for all four. `_get_shareable_references` answers `spaces:
  []` for a type kept in one space (dashboard, visualization, search, map,
  tag, config on 9.5.4) and `isMissing: true` for an object the source does
  not hold; `_update_objects_spaces` answers per-object errors inside a 200
  ("dashboard doesn't support multiple namespaces", 404 for a missing one),
  so the share and unshare read first and refuse a missing object, a
  one-space type, a removal that removes nothing, and an unforced last-space
  removal before any write, and a per-object error after the write is
  returned on that object (the others were updated). Sharing into a space
  that holds a copy made from the object leaves both there (probed), so the
  result passes on `spaces_with_matching_origins`. `include_references`
  defaults `true` (Kibana's default is false, under which a dashboard copied
  alone fails). The share's and unshare's read and write are not atomic
  (D45/D47 precedent): a change made between them is not seen, so the
  last-space check reads the state before the write. Dependency map: each
  tool → `find_saved_objects` (the objects' type and id) and `list_spaces`
  (the space ids), 8 edges. Rejected: the resolve route
  (`_resolve_copy_saved_objects_errors`: per-object destination choice and
  ignoring missing references — no task yet, roadmap); `compatibilityMode`
  and disabling legacy URL aliases (no task); following related objects on a
  share (the read lists them, but sharing what was not named would widen the
  call); a type allowlist like D48's (the copy returns titles and ids only,
  never attributes). Contract: [platform-admin](toolboxes/platform-admin.md).
  **Amended (2026-10-25, B5 review):** when the import into one target space
  fails as a whole (Kibana's copy route catches the throw and answers that
  space with a bare Boom error `{statusCode, error, message}`, no object —
  e.g. a key that cannot write saved objects there), every requested object
  not otherwise reported in that space is a `failed` entry with `error:
  "space_error"` and a `fix` naming Kibana's status and message, never
  `unchanged`, and the other target spaces keep their results (their
  `destination_id`s are the undo). Probed on 9.5.4 with a user whose role
  grants `all` in one target and `read` in the other: 200, the first space
  copied, the second `{success: false, successCount: 0, errors: [{statusCode:
  403, error: "Forbidden", message: "Unable to bulk_create index-pattern"}]}`;
  unit-pinned with that body, and live-pinned by a contract test that makes
  such a key (a throwaway role, a user, and a key that user makes for itself:
  Elasticsearch gives a key made by another key no privileges). The guidance never offers `source_space` on a
  deployment that refuses it (a pinned `KIBANA_URL` or public URL: the
  factory tells the adapter), and the refusal of `source_space` names both
  sources — the space `KIBANA_URL` pins, or the default space when only the
  public URL is pinned. **Amended (2026-10-25, B5 review round 2):** the
  copy and share guidance on such a deployment names the URL that fixes the
  source (`KIBANA_URL`, or `KIBANA_PUBLIC_URL` with the source the default
  space), never "the Kibana URL" when only the public URL is pinned; the 403
  `space_error` fix asks for a new API key, not only a role change:
  a key keeps the privileges its owner had when it was created (probed live
  in that contract test: the role granted `all` in the refused space, the
  same key still answers 403 there, which the test pins along with the copy
  into the space the key could already write). Final review, same day: the
  fix names both ways a key reaches the server, `Authorization: ApiKey
  <key>` and `KIBANA_API_KEY` (as `adapters/mcp/auth.py` does), because in
  HTTP mode the env key is ignored unless `KIBANA_MCP_ALLOW_ENV_KEY_HTTP`.
- **D50 (2026-10-25, probes on 9.5.4, Basic)** — The security-detections
  toolbox gains exception-list edit, copy, export and import:
  `update_exception_list`, `duplicate_exception_list` and
  `import_exception_list` (`write`), `export_exception_list` (`read`) and
  `overwrite_exception_list` (`destructive`), over kibana-py's
  `exception_lists.update`, `duplicate`, `export` and `import_lists`, all
  Basic and all working live; the plan's names kept. All five take `space`
  (D10: the toolbox is space-aware), so 91 tools are space-aware;
  `get_exception_list` and `find_exception_items` gain an optional
  `namespace_type` (default `"single"`, additive per D7): a list made with
  `namespace_type="agnostic"` answers 404 to a `"single"` read (probed), so
  without it no tool could read what the new tools edit. Nothing dropped.
  **Tiers, by the repo rule (irreversible or data-losing → destructive):**
  the update changes only name, description and tags and returns the
  `previous` ones (passing them back undoes it) — `write`; the duplicate and
  the import only create a list (deleting it undoes them; a repeat makes
  another) — `write`, not idempotent; the export changes nothing in Kibana
  and keeps a file on the server like `export_saved_objects` (#37) — `read`;
  the overwrite replaces the list's fields and **deletes** every item the
  export does not hold (an item added after the export was gone, probed) —
  `destructive`, `idempotentHint=true` (a repeat restores the same state).
  **Observed, and what each shaped:** Kibana's list PUT requires name,
  description and type (400 without them), keeps omitted tags and clears
  them on `[]`, accepts a type change (`detection` → `endpoint`, probed; the
  tool never sends one) and ignores `os_types` (`["windows"]` left
  `["linux"]`), so the update reads the list, re-sends its stored fields
  under the caller's and never sends os_types; the PUT honours `_version`
  (a stale one: 409 `version_conflict_engine_exception`), so the update
  sends the read's and a change in between writes nothing — the
  read-then-write race B3 could not close (D47) is closed here. The
  duplicate route answers 405 "unable to duplicate exception list with
  list_id: … - action not allowed" for an `endpoint` list, which kibana-py
  raises as a bare `ApiError` (it would read as an outage), so the tool
  reads the list and refuses `endpoint*` types first, and the adapter maps a
  405 to the same fix (export, then import); it copies `rule_default` and
  agnostic lists, names the copy `"<name> [Duplicate]"`, generates the
  `list_id` and item ids, and honours `include_expired_exceptions` (an item
  with a past `expire_time` was left out). The export route needs both `id`
  and `list_id` (400 with one) and answers an unknown pair with a 400 "does
  not exist", so the tool reads the list first for both ids and a clean
  404; its body is the list line, the item lines and a details line, kept
  under a new `el-` handle kind beside the `so-` one (same 0600 file, 20 kept
  per kind; each import reads only its kind, because Kibana's exception
  import 400s a saved object and the reverse would hand the saved-objects
  import an exception list, and the refusal names the right tools).
  Kibana's `as_new_list=true` import keeps every source `item_id` (two lists
  then held items `b6-item`; a GET by `item_id` answered one of them) and
  does not report the new `list_id`, so the import tool rewrites the export
  instead — the caller's `list_id` or a fresh uuid on the list and every
  item, a fresh `item_id` per item, `id`/`_version`/`tie_breaker_id` dropped
  (the import mints its own, probed with the source still present) — and
  sends a plain import. A plain import onto an existing `list_id` answers
  the list with a 409 "already exists … skipped" but **still adds the
  items to that list** (2 items became 4, probed), so the tool refuses an
  existing `list_id` before sending anything (the check and the import are
  two calls; no conditional create exists). An import into a space id that
  names no space wrote there (the list appeared once the space was created),
  which D5's connect-time check already refuses. The overwrite import
  (`overwrite=true`) kept the list's saved-object `id`, reverted its name,
  description and tags, and replaced its items (new item `id`s, same
  `item_id`s; an added one deleted); after a delete it recreated the list
  under a **new** `id` — rules reference a list by `id` too, so the result
  says `previous_id` (null then) and the description warns. The tool reads
  the current items first and returns those the export lacks as
  `removed_item_ids`. Neither import mode carries `os_types` (both copies
  came back `[]`). Per-object import failures come in a 200; a 400's message
  echoes the values it refused (`Invalid value "index-pattern" supplied to
  "type"`), so errors are returned as `"list_id[/item_id]: <status> <kind>"`
  and a rejected import names a fresh export, never Kibana's text (#37's
  rule). Dependency map: the get, `find_exception_items`, update, duplicate
  and export → `find_exception_lists` (the get's and the item find's
  not-found name it), the import and overwrite → `export_exception_list`
  (7 edges; 72 from 63 tools). Rejected: Kibana's `as_new_list` (above);
  sending both `overwrite` and `as_new_list` (accepted by Kibana, meaning
  unclear); editing `os_types` (Kibana ignores it) or the type (it would
  change which engine reads the list, for no task); listing agnostic lists
  in `find_exception_lists` in this batch (an existing tool's result would
  grow; roadmap); wrapping item edits (no task). Final review, same day:
  the not-found of the tools that take only `list_id`
  (`find_exception_items`, `duplicate_exception_list`) says to pass the
  list's `list_id` field and not its uuid `id`, the likeliest wrong value,
  instead of the either-id text `get_exception_list` keeps; an import that
  loses the check-then-import race returns `name: null`, not the export's
  name beside a `list_id` that holds another list. Contract:
  [security-detections](toolboxes/security-detections.md).

- **D51 (2026-10-25, probes on 9.5.4, Basic)** — The fleet toolbox gains
  agent tags, diagnostics and package install/uninstall: `list_agent_tags`
  and `list_agent_diagnostics` (`read`), `install_package`,
  `update_agent_tags` and `request_agent_diagnostics` (`write`) and
  `uninstall_package` (`destructive`), over kibana-py's
  `fleet_agents.get_tags`, `bulk_update_tags`, `request_diagnostics`,
  `get_uploads` and `fleet_epm.install_package`/`uninstall_package`, all
  Basic and all working live; the plan's names kept. `list_agent_diagnostics`
  is added to the plan's five: without it the model could ask for a bundle
  but never see it arrive. `list_agents`/`get_agent` gain each agent's `tags`
  (additive). No `space` parameter (the toolbox has none); 183 tools
  (74/70/39), still 91 space-aware. Nothing dropped. **Tiers, by the repo
  rule (irreversible or data-losing → destructive):** the tag edit changes
  Fleet-side labels only and returns the tags before and after (passing the
  difference back undoes it) — `write`, `idempotentHint=true`; the
  diagnostics request makes the agent collect and upload a bundle and changes
  nothing it collects or ships, unlike the reassign/upgrade/unenroll commands
  that sit at `destructive` — `write`, not idempotent (a bundle per call); the
  install creates a package's assets and only for a package that is not
  installed, so the uninstall undoes it — `write`, `idempotentHint=true` (a
  repeat is `already_installed`); the uninstall removes the assets and
  **deletes the package's package policies** — `destructive`. **Observed,
  and what each shaped:** `bulk_update_agent_tags` with three ids, one
  unknown, answered 200, tagged two and counted two actioned, none failed;
  an id list of only unknown ids also answered 200 — so the tool reads every
  agent first and refuses an unknown id before sending anything. The update
  is applied before Kibana answers (an immediate GET showed it) and an
  offline agent was tagged at once (the action is Fleet-side, acked at
  once), so the tool reads each agent back and reports
  `previous_tags`/`tags`, and `not_applied_agent_ids` for any agent the
  read-back shows unchanged (a hosted agent was not available to probe). An
  empty tag (`""`) was stored and a tag in both lists was kept (add wins),
  so tags have `min_length=1` and the overlap is refused; with neither list
  Kibana makes an action that changes nothing, refused too. The id list is
  capped at 100 to bound the reads before and after. A diagnostics request
  to the online demo agent was READY within 6 s, an offline agent's stays
  `IN_PROGRESS` until the 3-hour expiry, an unknown id is a 404 on both
  routes, `additional_metrics` accepts only `"CPU"` (400 otherwise), so the
  tool takes `include_cpu_metrics`, reads the agent first for its status and
  a not-found naming `list_agents`, and the upload listing drops `filePath`
  (the bundle carries the agent's logs and configuration; Kibana's UI
  downloads it). An install without a version **upgraded** `tcp` from 2.2.0
  to 2.3.1 and `rollback_package` answered 403 "Rollback integration
  requires an enterprise license", an older version answered 400
  "tcp-2.2.0 is out-of-date" (unless forced) and a repeat install of the
  installed version 200 with nothing changed, so the tool reads the package
  and installs only one that is not installed: an installed one at the
  requested version, or at any version when none is given, returns
  `already_installed=true` with nothing sent, another version is refused
  (the UI, or uninstall then install), and the out-of-date and unknown
  version (404 "tcp@99.0.0 not found") errors name the latest version. An
  unknown package name answered the install with a **500** "Package key
  parsing failed" (it would read as an outage) and the read with a 404, so
  the read names `list_packages`. `item.version` is the installed version
  once installed (`latestVersion` beside it). An input package (`tcp`)
  installs its index templates and pipelines only when a package policy
  uses it. The uninstall of a package with a package policy on an agent
  policy without agents went through **without `force` and deleted the
  package policy**; with an agent on it, 400 "Unable to remove package …
  with existing package policy(s) in use by agent(s)", and `force=true`
  deleted it — so the tool lists the package's package policies first,
  refuses any unless `force=True` (naming each with its agent policy and
  agent count) and sends Kibana's `force` with it; one on a managed or the
  default Fleet Server agent policy is refused regardless of `force`, as
  `delete_agent_policy` guards those policies (uninstalling `fleet_server`
  would strand the Fleet Server), and so is any `fleet_server` package policy
  on any agent policy, default or not, for the same reason (the refusal names
  every guarded one). Kibana's `force` is sent only for a package with package
  policies, the one case it exists for. Kibana uninstalled `elastic_agent`
  without `force` or any check while an agent policy had monitoring on (it
  removed the monitoring data streams' index templates and ingest pipelines),
  so it is refused, whatever `force` says, while any agent policy has
  `monitoring_enabled`, naming them and `update_agent_policy
  monitoring_enabled=[]`. A `fleet_server` package policy on no agent policy
  is guarded too, so every `fleet_server` package policy refuses the
  uninstall. **Fleet space awareness (B7 review 2):** the dev stack's Fleet
  settings read `use_space_awareness_migration_status: success`; an agent
  policy with `monitoring_enabled` and a `tcp` package policy made in a
  throwaway space were absent from the default space's listings, and
  Kibana's uninstall from the default space deleted that package policy
  (forced or not; a GET by id after it answered 404 — a listing read right
  after still showed it, which first read as "kept"). So with the status set
  the uninstall reads the agent and package policies of every space (the
  spaces listing; each other space's read with its `space_id`, an agent
  policy in its own space), names a policy's space in each refusal, and
  for a policy in another space says it is changed there (the toolbox has no
  `space` parameter); a space-pinned `KIBANA_URL` refuses while another
  space exists, since it cannot read one (fail closed), and a key that cannot
  read a space fails the call — *superseded by the final review below: a key
  that cannot read a space is not told it exists.* `include_cpu_metrics` sends
  `additional_metrics=["CPU"]`, the only value Kibana accepts (400 "expected
  value to equal [CPU]"); that bundle took ~66 s and the agent takes one
  diagnostics request a minute (a second ended `FAILED` "rate limit
  exceeded"). An uninstall of a package that is not
  installed (or unknown) answered 400 "… is not installed", so it is read
  first and named `list_installed_packages`. The uninstall reported
  `logs@custom` among removed assets yet left a user-edited one in place,
  so results carry no asset list; a document written to
  `logs-tcp.generic-default` was still there after a forced uninstall (the
  data stream is kept), which the description states; installing `nginx` also created the
  managed `logs-*` and `metrics-*` data views in every space (`namespaces:
  ["*"]`) and its uninstall kept them, which both descriptions state (the
  live test installs `tcp`, which creates neither). Installs took 4–5 s (`nginx`,
  `kubernetes` with 126 assets) against the client's 10 s default timeout
  (roadmap). Dependency map: the tag and diagnostics tools →
  `list_agents`, `install_package` → `list_packages`, `uninstall_package` →
  `list_installed_packages` (5 edges; 77 from 68 tools). Rejected: upgrades
  through `install_package` (irreversible on Basic); exposing Kibana's kuery
  form of the tag update (the toolbox never sweeps, as the bulk tools);
  returning the bundle's download path; an asset list in the results (it
  names templates Kibana keeps). **Amended (B7 final review, probes on 9.5.4):
  with Fleet space awareness on, `uninstall_package` is refused, whatever
  `force` says, before any policy is read; the every-space reading above is
  withdrawn.** It failed open: Kibana's spaces listing returns only the spaces
  the API key is authorized in (a 403 only when there are none), so a space
  the key cannot read was skipped unseen. Probed with a throwaway space
  holding a `tcp` package policy and a throwaway API key granting
  `feature_fleetv2.all` and `feature_fleet.all` on `space:default` only: the
  key's listing showed only `default` (`include_authorized_purposes` too), a
  GET of the other space answered 403, and Kibana's uninstall with that key,
  no `force`, answered 200 and deleted the package policy there (404 by id
  after) — so a no-`force` call would have deleted it and left it out of
  `deleted_package_policies`, and a `/s/<space>` URL with a key limited to
  that space (whose listing shows one space) was let through outright. No
  Kibana route the server calls proves a key reads every space: the package
  `stats` route and `withPackagePoliciesCount` counted 0 package policies
  from the default space for the other space's one (space-scoped, as the
  `elastic` user too), `_bulk_get` and a by-id read of the hidden policy
  answered empty and 404, `/api/stats` carries no usage, and
  `/internal/security/me` names the key, not its grants. Elasticsearch's
  `_has_privileges` would (resource `*`: false for the limited key, true for
  the test key), but the server takes no Elasticsearch URL, and Kibana's
  console proxy is an internal route needing the `console` privilege (403 for
  the limited key). So any value of `use_space_awareness_migration_status`
  (a migration under way or failed may already scope policies) refuses;
  `force` is no way past it, since it could only delete package policies no
  read reported; the refusal says why and names Kibana's Integrations UI, as
  a user whose role grants Fleet and Integrations in all spaces. With
  awareness off one listing holds every space's policies, and the guards
  above apply as before. A fresh 9.5.4 stack has awareness on unasked, so the
  uninstall now runs only where it is off; an optional Elasticsearch URL to
  prove an all-spaces key is on the roadmap. The space-named refusals, the
  pinned-URL refusal and the other-space fix text go with the every-space
  reading (no fleet refusal names another space now). Rejected: proceeding
  under `force` with a warning that unreported package policies may be
  deleted (an uninstall the caller cannot review); an operator setting that
  vouches for the key (an assertion, not a proof). Contract:
  [fleet](toolboxes/fleet.md).

- **D52 (2026-10-25, probes on 9.5.4, Basic)** — The dashboards toolbox
  gains `update_dashboard` (`write`, `idempotentHint=true`), over
  kibana-py's `dashboards.update` (Basic, works live); the plan's name kept.
  It changes a dashboard's `title`, `description`, `time_range` (or
  `clear_time_range=true`), `refresh_interval_seconds` and tags
  (`tags_to_add`/`tags_to_remove`), and writes back the panels, sections,
  pinned panels, filters, query and options exactly as read, behind the same
  unsupported-panels refusal as `update_panel`; `space` like every tool
  here. 184 tools (74/71/39), 92 space-aware; dashboards 17 (4/10/3);
  dependency map 79 edges from 69 tools (`update_dashboard` →
  `search_dashboards`, `find_saved_objects`). The `dashboards-analyst`
  profile and list (the default selection) are whole toolboxes at `write`
  (D39, D41), so they gain it: 27 tools, reviewed with the owner in the
  plan's final batch (D48's note). Nothing dropped. **Tier, by the repo
  rule:** every change is undone by passing the result's `undo` arguments
  back (title, description, a time range — one that was not stored is undone
  with `clear_time_range` — and tags through the add and remove lists;
  `previous` beside it holds the values before), panels are never written differently from how they were
  read, and nothing is written when no setting changes — `write`, as
  `update_panel`, which writes the same whole-dashboard PUT. The `undo` is
  the whole call: `dashboard_id` and the `space` passed are in it
  (`create_dashboard` ids derive from the title alone, so the same id can
  name a dashboard in the default space too, which an undo without the space
  would edit). Each value in it passes the tool's own argument checks; a
  previous value Kibana stored that they refuse (a blank title, a time its
  app cannot read, a negative, under-1 ms or over-limit refresh) is left
  out of it and named in `undo_omitted`, with `previous`
  still holding it, as is a refresh that could then only come back without
  a time range (final review: a stored `""` tag is never in the undo, since
  no call can remove it; `undo` is `{}` also when every changed value is
  left out). A refresh is reported only beside a stored time range, so
  an undo never pairs one with `clear_time_range`. Three undos are
  approximate: a refresh where none was stored can be paused (0), not
  removed; a paused refresh comes back paused at the interval the call set
  (0 carries no interval); and an undo that clears the time range leaves the
  refresh the call set, stored but not applied. Two undos are refused by
  checks that read Kibana when the undo runs, each refusal naming the way
  out: renaming back onto a title whose `create_dashboard` id another
  dashboard holds is the rename the title check refuses (a dashboard that
  shared that title before the call), and re-adding a removed tag id that
  names no tag is refused by the tag check (Kibana stores such ids; the
  removal reads no tags, and a tag can be deleted before the undo). The read-then-write race is not closed: the PUT checks no version
  (a stale `version` query parameter and an `If-Match` header were ignored
  with a 200, a body `version` 400 "Additional properties are not allowed"),
  shared with every read-modify-write tool here (D45/D47 precedent: tier
  kept). **Observed, and what each shaped:** the PUT replaces the whole
  dashboard and an omitted field reverts (an omitted `time_range` stores
  `timeRestore: false`), so the tool sends everything it read and changes
  only the keys it was given; panel ids, grid, sections and a library `ref_id`
  came back unchanged (contract test). Kibana stored `time_range`
  `{"from": "yesterday"}` and a `from` later than `to` with a 200 and dropped
  a `mode`; the server schema is only `from`/`to` strings, and the dashboard
  app parses them with `@kbn/datemath` (read from 9.5.4's bundled module: `now`
  or a moment-parsed ISO 8601 anchor with optional `||`, then `+N`, `-N` or
  `/` steps, units `ms s m h d w M y`, a rounding step only of one unit) — so
  the tool checks that grammar and the order of two timestamps before any
  call, naming the forms it takes. `refresh_interval` without a time range
  was stored (`timeRestore: false`, `refreshInterval` kept), and 9.5.4's
  dashboard app bundle applies a stored refresh only when `timeRestore` is
  set (`t.value&&(f.value&&d.setTime(f.value),g.value&&d.setRefreshInterval(g.value))`),
  so a refresh on a dashboard left without a time range is refused, naming
  `time_range` (with `clear_time_range`, before any call, naming both ways
  out), and the result reports such a stored refresh as `null` (Kibana's GET
  returns it, live); seconds rather than Kibana's `{pause, value ms}` (0 pauses,
  keeping the stored value, as Kibana's own pause does; `ge=0`, negative and
  1 ms values were stored by Kibana, a negative one live in the contract
  test), a number so a stored 1500 ms reads and is passed back as 1.5
  (whole milliseconds are sent; under 1 ms refuses; live in the contract
  test), at most 2147483.647 s (`le`): 9.5.4's timefilter hands the stored
  milliseconds straight to `window.setTimeout` (read from the bundle's
  auto-refresh loop), whose delay is a signed 32-bit integer, so a longer
  one fires at once (MDN; not driven in a browser here), and the bound also
  keeps a huge number from overflowing to infinity before the 1 ms check. Tags are tag saved-object ids; Kibana
  stored an unknown id, a tag's *name* and a default-space tag on a
  dashboard in another space, each a dangling reference (`tag-ref-<id>`),
  deduplicated repeats, and stored `""` — so each id being added that the
  dashboard does not carry is read with a saved-objects bulk get in the same
  space (a miss is a per-object 404 inside a 200) and an unknown one refuses
  the call naming `find_saved_objects(types=["tag"])`; ids already on it are
  kept unread; 100 tags is Kibana's schema cap. Add/remove lists, not a
  replacement list, so a model can tag without reading the current tags
  first (B7's `update_agent_tags` precedent); an overlap is refused.
  `create_dashboard` derives its id from the title, so a rename onto a title
  whose id another existing dashboard holds would leave two same-titled
  dashboards and `create_dashboard` editing the other: refused, naming it;
  any other rename leaves `create_dashboard` finding the dashboard by its old
  title (the description says so). The flights sample dashboard, with map
  and legacy panels the API drops, is refused untouched (live). Rejected:
  tag names as input (a name is a label Kibana does not key by; ids are
  what it stores and the find shows both); a tag-creation tool and settings
  in `get_dashboard`'s result (roadmap); Kibana's raw `refresh_interval` and
  `time_range.mode`; the same time check in `create_dashboard` (a behavior
  change outside this batch; roadmap). Contract:
  [dashboards](toolboxes/dashboards.md).

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
  `LMSTUDIO_MODEL` fallback in every live-model test. Since D31 the three
  LM Studio tests run on demand (`make test-e2e-local` since D38); since D42
  `e2e_green` runs the Claude tier.
- **D19 (2026-08-19)** — Replay transcripts carry recorded bytes, never
  hand-written arguments; error steps are recorded with their guidance
  needles; server-generated ids (alert rules) bind at replay time from the
  live create response since they cannot be re-derived. Provenance is
  per-transcript: most are real model turns, one (the authored
  space-dashboard sequence) has a hand-chosen call ORDER over real
  recorded wire bytes — its order carries no evidence about model behavior
  and each transcript's `recorded` block says which kind it is.
- **D31 (2026-10-04)** — *The on-demand rule below now runs `make test-e2e`,
  the Claude tier, superseded by D38; the gate decision is superseded for the
  local `dod.config` by D42 (`e2e_green = required` there) and stands for
  every `.github/dod` config.* The
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
- **D33 (2026-10-12)** — *Its scope is narrowed by D38: it governs the
  LM Studio tier (`make test-e2e-local`) only. The Claude tier
  (`make test-e2e`) drives `CLAUDE_E2E_MODEL`, which defaults to the `haiku`
  alias; the no-privileged-model principle stands.* No model is privileged: the live-model tests drive
  whatever `LMSTUDIO_MODEL` names and fail with the fix when it is unset.
  Supersedes D18's reference-model status (owner, 2026-10-12): a model chosen
  on 2026-08-19 kept a default role because it had once been named, not
  because a later measurement chose it again. D18's numbers remain evidence
  about the models it measured; which model to drive is the caller's choice.
- **D38 (2026-10-16, owner)** — *Its "`e2e_green` stays `n/a` in every DoD
  config … outside every gate" is superseded for the local `dod.config` by
  D42; the rest stands.* The live-model tier is driven by Claude.
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
  which is the default selection at `write`, listed 21 then; 26 since D48, which records that growth).
- **D42 (2026-10-17, owner: Q16)** — The local Definition-of-Done gate
  requires the Claude live-model run: `e2e_green = required` in `dod.config`
  (the gate a cloud session or the owner's laptop runs), and the criterion runs
  `make test-e2e`'s selection — `pytest -m e2e_claude`, headless Claude Code
  (`claude -p`) with only this server's tools through the pre-registered tasks
  (`scripts/experiment/CLAUDE_E2E.md`, D38), model `CLAUDE_E2E_MODEL`, default
  the `haiku` alias. The runner this criterion kept while it was `n/a` drove
  the LM Studio tests; it now runs the Claude tier, and `make test-e2e-local`
  is outside every gate. The gate mirrors the pytest line rather than nesting
  `make` (as for `image_smoke`) and, like `contract_green`, takes a seeded
  stack as its precondition instead of starting one; its run records go to the
  gate's log directory, not into `scripts/experiment/runs/`. A missing `claude`
  CLI, or a CLI that `claude auth status` does not report signed in, is NO-GO
  with a message naming the fix (install Claude Code; `claude auth login`) —
  never a skip and never a pass. `e2e_green` stays `n/a` in every
  `.github/dod` config: CI has no Claude sign-in, and no CI job or secret is
  added. Why (owner): the old rule required an LM Studio run on the owner's
  laptop that in practice never ran on this branch; the Haiku run needs only a
  Claude sign-in, takes about 5–6 minutes per gate run, and costs about $0.04
  per 20-run block on the owner's subscription; cost is not a factor. A pass
  still certifies only the model that ran (D31's caveat stands). Evidence: the
  two committed blocks passed 40 of 40, every run valid
  (`scripts/experiment/runs/claude-e2e/counted-2026-10-17/SUMMARY.md`, $0.0393;
  `scripts/experiment/runs/claude-e2e/counted-2026-10-17-d40/SUMMARY.md`,
  $0.0425; median wall time 14–19 s per run). **Supersedes D31 in part**: the
  part that kept `e2e_green` `n/a` in the local `dod.config` (and D38's
  restatement of it). D31's replay tiers stand, and so does the on-demand
  rule: re-run `make test-e2e` when a change touches tool names, descriptions
  or schemas, without waiting for the gate.

- **D44 (2026-10-24, live run on 9.5.4)** — The Claude tier can export the
  server's per-tool spans, behind a default-off switch. Observed: the harness
  (`tests/_claude_cli.py`) builds the server's `env` block from four fixed
  variables and drops every `KIBANA*` variable from the model's environment,
  so an exported `KIBANA_MCP_OTEL_ENABLED` never reached the server and
  `make test-e2e` sent no spans. Now, with `KIBANA_MCP_OTEL_ENABLED` truthy in
  the runner's environment (the spellings pydantic accepts for
  `otel_enabled`), every `KIBANA_MCP_OTEL_*` and `OTEL_*` variable is copied
  into the server's `env` block, in the same mode-0600 file as the Kibana key,
  ahead of the four fixed keys, so it can never change the URL, the key, the
  toolboxes or the tier. `TRACEPARENT` is not copied: a parent session's trace
  never adopts the runs. While the switch is on, the model's environment also
  drops `OTEL_*` and `HONEYCOMB_*`, so the exporter's credentials sit in the
  server entry only, as the Kibana key does. With the switch unset, the config
  and the model's environment are unchanged. With no
  `KIBANA_MCP_OTEL_SECRET_TOKEN` the OTLP exporter reads its headers from
  `OTEL_EXPORTER_OTLP_HEADERS`, which is how a Honeycomb team key
  (`x-honeycomb-team`) is supplied. Coverage by tier: the Claude tier and the
  replay tier start the server through `main()`, the only place telemetry is
  configured (replay passes the runner's whole environment to its stdio
  server, so it needs nothing new). The contract tier runs tools in-process
  and sends no spans. Span delivery on exit (`src/kibana_mcp/telemetry.py`):
  the batch processor exports 500 ms after a span ends, not after the SDK's
  5 s, unless `OTEL_BSP_SCHEDULE_DELAY` is set, and SIGTERM flushes the queue
  before the process dies (a normal exit, SIGINT included, already flushes
  through `atexit`). The SIGTERM flush runs in a daemon thread and the
  handler waits for it at most 2 s (the SDK's `force_flush` exports in the
  caller with no deadline, so an unreachable collector held the exit for the
  exporter's 10 s timeout); the handler restores SIGTERM's default action
  before flushing, so a second SIGTERM, also one that lands during an
  `atexit` flush after the first, ends the process at once. On the HTTP
  transport uvicorn restores this handler after its graceful shutdown and
  re-raises the SIGTERM, so that exit takes the same path. Why: Claude Code stops a stdio server with SIGINT, then
  SIGTERM about 100 ms later, then SIGKILL about 400 ms after that (probed
  with a signal-logging server under `claude -p`, Claude Code 2.1.310), and
  SIGTERM's default action skips `atexit`. The first 20-run block, with the
  5 s default and no SIGTERM flush, recorded 80 server tool calls but sent 72
  spans. The 8 lost were final calls of their runs (`create_dashboard` 5 of
  10, `get_alert_rule` 3 of 15); every other tool matched
  (`scripts/experiment/runs/claude-e2e/baseline-2026-10-24-otel/`,
  `mcp.run=b0-baseline`,
  [query](https://ui.eu1.honeycomb.io/pedro-angel/environments/test/datasets/mcp-for-kibana-roadmap/result/po-w3GY92re2)).
  Evidence for the fix: a second 20-run block with the switch on, over server
  code identical to `main` at `a628515` except for this change to
  `telemetry.py` (its records name `f354eda` with src dirty, because the
  block ran before the change was committed), passed 20 of 20 with every run
  valid and recorded 78 tool calls. Honeycomb dataset `mcp-for-kibana-roadmap`
  (environment `test`, resource attribute `mcp.run=b0-baseline-flush`) holds
  78 `tools/call <tool>` spans, the same count as the records for each of the
  12 tools, none with an error status
  (`scripts/experiment/runs/claude-e2e/baseline-2026-10-24-otel-flush/SUMMARY.md`).
  That block, not the first, is the reference for the ops-admin-tools
  branch's error-rate and latency comparison:
  [query](https://ui.eu1.honeycomb.io/pedro-angel/environments/test/datasets/mcp-for-kibana-roadmap/result/Cf-Hue16ykFq).
  Rejected alternative: the efficiency study's relay
  (`scripts/experiment/live_telemetry.py`), which keeps the key out of the
  server too. It brings a per-run HTTP relay into the gate's path, and the
  e2e tier needs only the server's spans. Telemetry is never a DoD criterion.
  Docs: [e2e-setup](../e2e-setup.md#exporting-the-servers-spans-from-the-claude-tier-default-off).

- **D53 (2026-10-25, owner: the DoD runs on the newest supported version)** —
  The DoD gate checks the running stack's Kibana version before the
  seeded-stack criteria (`contract_green`, `e2e_replay_green`,
  `wheel_replay_green`, `e2e_green`), inside them rather than as a criterion of
  its own, so no config can declare it away. `tests/_stack_version.py` reads
  `version.number` from `/api/status` with the credentials the suites use
  (shell env, else `elastic-start-local/.env.seed`; since D56 a shell value
  that differs from the seed is refused, so it reads the seed's) and requires the
  `ES_LOCAL_VERSION` pin in `elastic-start-local/.env.example` — the newest
  version kibana-py supports (D30, `tests/unit/test_stack_pin.py`) — or, in CI
  (`GITHUB_ACTIONS=true`), the matrix's `ES_LOCAL_VERSION`, which must be in
  `kibana.SUPPORTED_VERSIONS`. A shell `ES_LOCAL_VERSION` does not move the
  local target. A missing seed, an unreachable Kibana or another version is
  NO-GO on each of those criteria with one line naming the fix (`scripts/stack.sh
  down && scripts/stack.sh up && scripts/stack.sh seed`, prefixed by `unset
  ES_LOCAL_VERSION` when the shell exports one); each GO line names the version
  it certified (`GO    contract_green  (Kibana 9.5.4)`). One HTTP call, about
  1 s per gate run. `test_stack_pin.py` also covers `scripts/cloud-setup.sh`'s
  pre-pull fallback version. Why: the gate checked only that `.env.seed`
  existed, so a stack left on a study's 9.5.5 (outside the supported set; 516
  committed files under `scripts/experiment/runs/` record `"kibana_version":
  "9.5.5"`) or on
  9.4.7 after `ES_LOCAL_VERSION=9.4.7 make test-contract` read `VERDICT: GO`
  without saying which Kibana it certified. Evidence (2026-10-25, dev stack on
  9.5.4): with a fake `/api/status` reporting 9.5.5 the gate printed `NO-GO
  e2e_replay_green (the seeded stack runs Kibana 9.5.5, but the gate certifies
  9.5.4 ...)`; with the pin doctored to 9.4.7, and in CI mode with a 9.4.7
  matrix version, the same NO-GO; on the real tree `GO e2e_replay_green
  (Kibana 9.5.4)`. Extends D30.
- **D54 (2026-10-25)** — `changelog_entry` is diff-based. The DoD gate and a
  `checks.yml` step on pull requests run `scripts/checks/changelog-entry.sh`:
  the change is every path that differs from the base — `git merge-base HEAD
  origin/main`, else the latest `v*` tag — in the working tree, plus untracked
  files that are not ignored. If any of them is outside the exemption list
  (`scripts/experiment/runs/` run records, the working plans under
  `.claude/plans/`; renames are off, so a file moved into an exempt path counts
  at its old one), CHANGELOG.md must gain an entry under `## [Unreleased]`: more
  entry lines (non-blank, not a heading) there than at the base, at least one
  with text the base changelog does not hold — or a `## [x.y.z]` heading whose
  version the base lacks (a release cut moves entries there). A line under an
  older release, above the first section, an amended old heading, an empty
  sub-heading, or a reworded, re-spaced or moved entry does not count.
  `uv.lock` is not exempt: the image installs from it, and lock-file-only CVE
  fixes get a `### Security` entry. A git failure while listing the paths or
  reading the base changelog is NO-GO naming `git fsck; git fetch origin main`,
  never an empty passing list. With neither `origin/main` nor a `v*` tag the check is NO-GO naming
  `git fetch origin main`; a shallow clone without the merge-base names `git
  fetch --unshallow origin main`; a missing entry names `add an entry under ##
  [Unreleased] in CHANGELOG.md` and lists up to five of the paths. The GO line
  names the base. The CI step fetches `main` itself and skips Dependabot PRs
  (trusted bumps, as in the commit-msg step); the `.github/dod/*.config` tiers
  keep the criterion `n/a` because `checks.yml` runs it. Why: the criterion
  grepped for a `## [Unreleased]` or release heading, and CHANGELOG.md always
  has one, so it read GO on every tree while `dod.config` said it "refuses any
  completion claim that leaves the changelog untouched"; 10 of the last 60
  non-merge commits before the review did not touch CHANGELOG.md (chores, a
  test-timing fix, Dependabot bumps). Evidence (2026-10-25): on a code change
  with no entry the old gate printed `GO    changelog_entry` and the new one
  NO-GO naming the fix; on the real tree GO (the H2 commit message has the
  lines). An adversarial review then found 8 ways through the first version
  (the `uv.lock` exemption, amended headings, edited or moved lines, empty
  sub-headings, renames, quoted non-ASCII paths, a swallowed git failure and a
  `docs/plans/` exemption for a directory that does not exist); each has a
  unit test in `tests/unit/test_changelog_entry.py`.
- **D55 (2026-10-26)** — The DoD verdict is bound to a tree. At start and at
  end the gate computes the content tree id of the working tree: a copy of the
  index in a temporary file, `git add -A --sparse` (git 2.34+), `git
  write-tree` (tracked edits and
  untracked, non-ignored files included; the real index is never touched;
  under a second on this repo). The copy keeps the index file's mtime (`cp
  -p`), so git's racy-clean check still rehashes a same-size edit made in the
  second of the last index write, and it drops every assume-unchanged bit
  and the skip-worktree bit of every entry whose file is present (C-quoted
  names unquoted for that check), since `git add -A` never rehashes an entry
  carrying either (an absent skip-worktree entry keeps its bit and is not
  recorded as deleted only when `core.sparseCheckout` is on; anywhere else,
  like an absent assume-unchanged file, it is a deletion). `--sparse` also
  takes edits and untracked files outside the sparse-checkout cone, which
  plain `git add -A` skips or fails on. Known limits, left as is: a
  submodule counts by its checked-out commit only (its uncommitted work is
  not in the id; the repo has no submodules), and in a sparse checkout a
  file inside the cone that someone marked skip-worktree by hand and deleted
  reads clean, as it does in `git status` (telling it apart means matching
  every entry against the sparse patterns, more code than the case is
  worth). The header line and the `VERDICT:` line name
  HEAD, that tree id and `clean` (tree id equals `HEAD^{tree}`) or `dirty`. If
  the id at end differs from the one at start the gate prints `NO-GO
  tree_stable` naming both ids and the fix (commit or stash, leave the tree
  alone until the gate finishes, re-run `make dod`; the `hygiene_hooks` log
  when that criterion ran, since its fixers rewrite files). `tree_stable` is
  part of the gate, not a criterion: no config row, so no config can declare
  it away, and it applies to the CI tiers too. A dirty tree is reported, not
  refused: the GO certifies the recorded tree, and a claim "GO at `<sha>`"
  holds only when `git rev-parse <sha>^{tree}` equals it (AGENTS.md says how to
  check). The log dir `/tmp/dod-<repo>/` is cleared at the start of each run
  and ends with `verdict.json`: verdict, HEAD, tree id at start and end, dirty,
  config path and sha256, each known criterion's result (`GO`, `NO-GO`, `n/a`)
  plus `tree_stable` (and `cycle_stack` when it failed), Kibana version and
  Claude model when those tiers ran, UTC start and finish times. A git failure
  while computing the id is a FAIL (exit 2) at start and NO-GO at end, naming
  `git status && git fsck`. Why: the gate called git only for `rev-parse
  --show-toplevel` and printed a bare `VERDICT: GO`, so a GO over untracked
  helpers or uncommitted fixes read as a GO for whatever was committed later;
  an edit saved during the 30-plus-minute run split the criteria across two
  trees under one GO; and logs of earlier runs stayed in the log dir
  (`scripts/experiment/runs/claude-e2e/baseline-2026-10-24-otel-flush/runs.jsonl`
  has 20/20 records with `"server_dirty": true`). Evidence (2026-10-26): on a
  fixture repo whose only required criterion writes a file, the old gate
  printed `GO changelog_entry` and `VERDICT: GO`; the new one `NO-GO
  tree_stable (the tree changed during the run: ... — commit or stash your
  edits ...)` (the H3 commit message has the lines); on the real tree GO with
  the tree id. `tests/unit/test_dod_verdict.py` runs the gate on fixtures.
- **D56 (2026-10-26)** — The DoD suites run as declared, and their lines show
  how much ran. (a) The gate clears `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`,
  `PYTEST_DEBUG`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `COVERAGE_RCFILE` and
  `KIBANA_MCP_SERVER_BIN` (which makes `e2e_replay_green` replay an installed
  executable instead of the checkout; `wheel-replay.sh` sets it on its own
  command line) with `MAKEFLAGS`/`MFLAGS` before any criterion, and its header names the ones that
  were set (`note: cleared PYTEST_ADDOPTS ...`). Cleared, not refused: the
  gate's own command lines and `pyproject.toml` (committed, in the tree id)
  declare what runs, so a stale export is not an error to fix first.
  `COVERAGE_RCFILE` is on the list because it replaces the config pytest-cov
  reads `fail_under = 90` from (measured: with an rc file that sets
  `fail_under = 0`, `pytest --cov` over three tests no longer fails the floor).
  (b) `run_suite` parses every outcome of the pytest summary and prints `N
  passed / M collected` on the GO and NO-GO lines (M = passed + failed +
  skipped + deselected + xfailed + xpassed + errors), before the Kibana
  version on the seeded-stack lines (`GO    contract_green  (N passed / M
  collected; Kibana 9.5.4)`). Deselected tests are shown, not refused: the
  default addopts deselect the live tiers from `unit_green`, and each live tier
  deselects the units. (c) `tests/_stack_version.py`, the seeded-stack
  precondition (D53), refuses a shell `KIBANA_URL` or `KIBANA_TEST_API_KEY`
  that differs from `elastic-start-local/.env.seed` (an empty one included)
  before any HTTP call, naming the variables (never their values) and `unset
  KIBANA_URL KIBANA_TEST_API_KEY`: the gate certifies the seeded local stack.
  It also refuses a `KIBANA_API_KEY`, `KIBANA_PUBLIC_URL` or
  `KIBANA_MCP_PUBLIC_KIBANA_URL` set in the shell or `.env.local`: the replay
  servers inherit the suite's environment and `Settings.load` fills the seed's
  key in only by `setdefault`, so these would re-credential (or re-link) the
  server under test; the fix adds them to the `unset` (or names the
  `.env.local` line to remove). `KIBANA_MCP_API_KEY` and
  `KIBANA_MCP_KIBANA_URL` are not refused: the seed-filled first alias wins
  over them (measured). Outside the gate the suites keep shell-wins precedence
  with a warning that names the variable, not its value
  (`tests/_stack_env.py`), so `make test-contract` against another stack still
  works; the refusal is the gate's. This supersedes D53's "credentials the
  suites use (shell env, else .env.seed)" and its `unset KIBANA_URL ...`
  prefix on a version mismatch, which the refusal makes unreachable. (d) The
  gate header and AGENTS.md say that `KIBANA_MCP_DOD_CYCLE_STACK=1` only stops
  the dev stack between the halves, never starts or restarts it, and leaves it
  stopped (integrity-6 of the 2026-10-25 review, dropped as a gate gap, tripped
  the review's verifier). Why: the gate cleared only `MAKEFLAGS`, counted only
  `passed` and `skipped`, and the suites let shell credentials win with a
  `warnings.warn` in a `-q` log, so a shell export could shrink every suite,
  drop the coverage floor, or aim the mutating suites at another Kibana, and
  still read `VERDICT: GO`. Evidence (2026-10-26, dev stack on 9.5.4): with
  `PYTEST_ADDOPTS='-k test_stack_pin --no-cov'` the old gate printed `GO
  unit_green` over `3 passed, 2261 deselected` and the new one runs the whole
  suite and prints its counts; with `KIBANA_URL=http://127.0.0.1:15601` (the
  seed says `localhost`) the old gate printed `GO e2e_replay_green (Kibana
  9.5.4)` and the new one NO-GO naming the unset (the H4 commit message has
  the lines). `tests/unit/test_dod_suite_env.py` runs the gate on fixtures;
  `tests/unit/test_stack_version.py` covers the refusal.

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

- ~~D33's scope as every live-model test (`LMSTUDIO_MODEL` required, no
  default, for all of them)~~ — narrowed by D38: D33 governs the LM Studio tier
  (`make test-e2e-local`); the Claude tier defaults `CLAUDE_E2E_MODEL` to the
  `haiku` alias.

- ~~D40's dropping of each read action's `uuid` from `get_alert_rule`~~ —
  superseded by D40's own amendment: the uuid is returned so a read → update
  round trip keeps each action's identity.

- ~~D31's on-demand live-model rule as the LM Studio tests (`make test-e2e` =
  the three LM Studio tests)~~ — superseded by D38: `make test-e2e` is the
  Claude tier and the LM Studio tests are `make test-e2e-local`.

- ~~D31's gate decision for the local `dod.config` (`e2e_green = n/a`, the
  Definition of Done needs no LLM)~~ — superseded by D42: the local gate
  requires the Claude run. `e2e_green = n/a` stands in every `.github/dod`
  config.

- ~~D29 (2026-09-21): track the newest 9.4 patch and defer Kibana 9.5~~ —
  deferred 9.5 until kibana-py released its supported-set module, and on the
  premise that the `streams` toolbox wraps `streams.upsert`, whose body 9.4
  requires `queries` in and 9.5 rejects. The module shipped in kibana-py 0.6.0,
  and the premise was wrong: the toolbox never calls `streams.upsert` (its
  writes go through `fork` and `update_ingest`). Superseded by D30.
- ~~Space scoping via base-URL suffixing (probe P1)~~ — worked but
  validated nothing; superseded by D5/D6 (validate-at-construction) once
  P7 exposed the silent-orphan hazard.
