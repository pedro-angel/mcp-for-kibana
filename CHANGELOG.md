# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

New entries go under **[Unreleased]**; cutting a release moves them into a dated
version section.

## [Unreleased]

### Added

- **Dashboards: edit a dashboard's settings** (one `write` tool, 184 tools in
  all; it takes `space`). `update_dashboard` changes a dashboard's title,
  description, time range (or clears it, so the dashboard opens with the time
  picker's current time), auto-refresh interval in seconds (0 turns it off)
  and tags (`tags_to_add`/`tags_to_remove`, tag ids from
  `find_saved_objects(types=["tag"])`), and leaves its panels exactly as they
  are. Only what is passed changes; when nothing would, nothing is written
  (`unchanged`). The result returns the settings now, what changed, their
  previous values, and `undo`: the whole `update_dashboard` call that puts
  them back, dashboard id and space included, to pass as given (auto-refresh
  reads as none on a dashboard without a stored time range, where Kibana's
  dashboard app does not apply it, and a stored sub-second interval as a
  fraction of a second; a paused refresh comes back paused at the interval
  just set). A previous value Kibana stored that the tool itself refuses — a
  time its dashboard app cannot read, a negative or out-of-range refresh, a
  blank title — is left out of `undo` and named in
  `undo_omitted`. Renaming back onto a title `create_dashboard` maps to
  another dashboard, or re-adding a removed tag id that names no tag, is
  refused like any such call. Auto-refresh is at most 2147483.647 seconds,
  the longest the dashboard app's timer can wait. It refuses, before writing: a dashboard holding panels the API cannot write back (as
  `update_panel` does), a time Kibana's dashboard app cannot read (Kibana's
  API would store `"yesterday"`), auto-refresh on a dashboard without a
  stored time range or with `clear_time_range` (Kibana would store it and
  never apply it), a tag id that
  names no tag in that space (Kibana would store it), more than 100 tags, and
  a rename onto a title `create_dashboard` already maps to another
  dashboard. It joins the default selection (`dashboards-analyst`, now 27
  tools). Decision D52.
- **Fleet: agent tags, diagnostics, and integration install and uninstall**
  (two `read`, three `write` and one `destructive` tool, 183 tools in all).
  `list_agent_tags` lists the tags set on agents, and `list_agents`/`get_agent`
  now show each agent's `tags`. `update_agent_tags` adds and removes tags on
  the exact agent ids given (up to 100, never a query): it refuses an id that
  names no agent before changing anything (Kibana would skip it silently) and
  returns each agent's tags before and after, plus any agent Kibana did not
  update. `request_agent_diagnostics` asks one agent for a diagnostics bundle
  (optionally with a CPU profile) and returns its status; an offline agent
  runs it at check-in. `list_agent_diagnostics` shows each bundle's status
  (`READY` once uploaded), never its content: the bundle is downloaded in
  Kibana's UI. `install_package` installs an integration that is not
  installed, at its latest version or the one given; an installed one is left
  as it is (`already_installed`), and a version change is refused, because an
  upgrade cannot be undone on a Basic license. `uninstall_package`
  (`destructive`) removes an integration and the package policies Kibana
  deletes with it: it refuses a package that has package policies unless
  `force=True`, naming them, and refuses one with a package policy on a managed
  or the default Fleet Server agent policy whatever `force` says, as well as
  `fleet_server` with a package policy on any agent policy and `elastic_agent`
  while an agent policy has monitoring on (Fleet itself runs on both; Kibana
  removed `elastic_agent` under a monitoring policy without a check), and
  Kibana's `force` is sent only to delete package policies. Every
  `fleet_server` package policy counts, one on no agent policy too. With
  Fleet space awareness on (a fresh 9.5.4 stack has it on unasked), every
  uninstall is refused, whatever `force` says: Kibana's uninstall deletes the package
  policies of every space, while Kibana's spaces listing leaves out, without
  an error, each space the API key cannot read (a key limited to the default
  space saw only it, and its uninstall deleted another space's package
  policy), and nothing this server calls proves that a key reads every
  space. The error says to uninstall in Kibana's Integrations UI as a user
  with Fleet and Integrations in all spaces. Unknown
  agents, packages and versions are errors that name the tool to look them up
  with. Decision D51.
- **Security detections: edit, copy, export and import exception lists**
  (three `write`, one `read` and one `destructive` tool, 177 tools in all;
  every one takes `space`). `update_exception_list` changes a list's name,
  description or tags and returns the previous ones; it writes nothing if the
  list changed after its read and says to call again.
  `duplicate_exception_list` copies a list and its items into a new list in
  the same space (Kibana refuses endpoint lists; the error names export and
  import instead). `export_exception_list` keeps the list and its items in a
  file on the server and returns an `el-` handle and a summary, never the
  content; `import_exception_list` creates a new list from it, in any space,
  with new item ids (unlike Kibana's own "as new list" mode, which reuses the
  source's item ids), and refuses a `list_id` that already exists (Kibana
  would add the items to that list); `overwrite_exception_list` restores the
  list in place and returns the ids of the items it deleted (those added
  after the export). Both imports take the export's namespace, so an agnostic
  export makes or restores a list shared across every space (their
  descriptions say so), and an expired or unknown `el-` handle is refused
  with the fix (export again), also on a server that masks error details.
  `get_exception_list` and `find_exception_items` take an optional
  `namespace_type` so agnostic lists can be read; their not-found names
  `find_exception_lists` and the namespace a list made with the other
  `namespace_type` is found in (and, for `find_exception_items`, that it
  takes the list's `list_id` field, not its `id`), so a `KIBANA_MCP_TOOLS` list that leaves
  `find_exception_lists` out gets the dependency warning. Import errors come back
  per object as ids and status only. Kibana neither edits nor imports a
  list's `os_types`. Decision D50.
- **Platform admin: copy and share saved objects between spaces** (two
  `write` and two `destructive` tools, 172 tools in all; none takes `space`,
  each takes `source_space`). `copy_saved_objects_to_spaces` copies objects
  (and, by default, what they use) into other spaces as new objects with new
  ids and returns each copy's `destination_id`; `overwrite_saved_objects_in_spaces`
  replaces the objects earlier copies made there instead.
  `share_saved_objects_to_spaces` makes the same object show in more spaces
  (`*` for every space) and returns the spaces it added;
  `unshare_saved_objects_from_spaces` removes spaces and, because Kibana
  deletes an object left in no space, refuses removing the last one unless
  `force=true`. Every space written into is checked first (Kibana writes into
  a space that does not exist without an error); the share and unshare read
  each object's spaces first and refuse a missing object, a type Kibana keeps
  in one space (dashboards, visualizations and others: copy them instead) and
  a removal that removes nothing; copy failures come back per object with
  Kibana's error and a fix. A target space Kibana refuses as a whole (a key
  that cannot write there) fails its objects with `space_error` and the fix,
  and the copies made in the other spaces are still returned; on a 403 the fix
  asks for a new API key (sent as `Authorization: ApiKey <key>` or set in `KIBANA_API_KEY`, the latter ignored in HTTP mode by default) (a key keeps the privileges its owner had
  when it was made, so changing a role does not help). The guidance never
  suggests `source_space` where the deployment refuses it (a space-pinned
  Kibana or public URL) and names the URL that fixes the source instead.
  Decision D49.
- **Data management: runtime fields, the default data view, and saved-objects
  reads** (three `read`, two `write` and one `destructive` tool, 168 tools in
  all; every one takes `space`). `upsert_runtime_field` creates or replaces a
  single-value runtime field (`keyword`, `long`, `double`, `date`, `ip`,
  `boolean`, `geo_point`, with an optional Painless `script`) and returns the
  definition it replaced as `previous`; it refuses a name the view maps and a
  composite field before any write, keeps a replaced field's custom label and
  description (Kibana's PUT drops them), and its description warns that
  Kibana stores a script without compiling it, so a broken one fails the
  view's searches until it is fixed or deleted. `delete_runtime_field`
  returns the deleted definition, refuses a mapped field and a composite's
  subfield naming what to delete instead, and its miss lists the view's
  runtime fields. `get_default_data_view` reads the space's default data view and
  says when its stored id names no view (Kibana keeps a deleted one's);
  `set_default_data_view` sets it (always replacing: without `force` Kibana
  keeps the old one and still answers 200) or clears it with `view_id=null`,
  refuses an id that names no view, and returns `previous_id` with
  `previous_exists` (a deleted view's id cannot be passed back; `view_id=null`
  undoes the change then, as Kibana treats such an id as no default).
  `find_saved_objects` finds objects of given types by words of their title
  or name (no other attribute) and/or by reference (`has_reference`: e.g. the dashboards that use a visualization)
  and returns one page of summaries with a `note` when the page is empty
  (Kibana also answers empty for unknown and hidden types);
  `get_saved_object` returns one object of an analytics content type
  (dashboard, lens, visualization, search, index-pattern, map, tag, …) with
  attributes over 4,000 characters listed by size instead
  (`omitted_attributes`) and its distinct references; it refuses every other
  type before calling Kibana, because Kibana strips only registered encrypted
  attributes and Fleet's proxy and output objects keep certificate keys,
  proxy headers, `config_yaml` and service tokens in plain ones; the refusal
  names only what works for the type (`list_outputs` for Fleet outputs,
  `list_connectors`/`list_alert_rules` for connectors and rules, no tool for
  Fleet proxies, the Fleet policy, Fleet Server host and Synthetics tools
  for their types, each with the toolbox to enable), never an export Kibana
  refuses or a find that answers a hidden type empty. The `read-only-explorer` and `dashboards-analyst` profiles and
  built-in lists grow to 14 and 26 tools. Decision D48.
- **Observability: create, replace and delete APM agent configurations**
  (one `write` and one `destructive` tool, 162 tools in all).
  `upsert_apm_agent_config` creates or replaces the central configuration of a
  service and environment (omit either for all): `settings` replaces the
  stored map, values go out as strings (`0.5` → `"0.5"`, `false` →
  `"false"`), and the result adds `created` and `previous_settings` (the map
  it read and replaced, which a second call puts back; Kibana's PUT takes no
  etag, so a change another client stores between that read and the write is
  replaced unreported). It overwrites only a configuration it read in the same
  call, takes an optional `agent_name` on a create, keeps a stored one on a
  replacement (refusing a different one, which the result could not give
  back), refuses an empty map and `""` as a name, refuses to create a
  configuration named `ALL_OPTION_VALUE` (but replaces one another client
  stored under that literal name, rather than pointing at the all-* one; the
  refusal says to omit every name holding it), and names the settings
  sent when Kibana rejects a value. `delete_apm_agent_config` deletes one
  configuration, matched exactly (a literal `ALL_OPTION_VALUE` included), and
  returns its settings and `agent_name`, which the upsert takes back to
  recreate it. No tool takes `space`. Creating
  APM annotations is not offered: Kibana requires a Gold license for it (403
  on Basic). Decision D47.
- **APM agent configurations show their `agent_name`** in
  `list_apm_agent_configs` and `get_apm_agent_config` (`null` when none was
  stored). Decision D47.
- **`get_apm_agent_config` names the fix on a miss**: "no APM agent
  configuration for service '<name>', all environments — service_name and
  environment must match a configuration exactly (an omitted one means all);
  call list_apm_agent_configs to see them", instead of Kibana's "Not Found".
  Decision D47.
- **Observability: create, edit, run and delete Synthetics objects** (seven
  `write` and three `destructive` tools, 160 tools in all). Monitors:
  `create_synthetic_monitor` (http, tcp, icmp or browser, on private
  locations given by id or label; `schedule` such as `10m` or `30s`),
  `update_synthetic_monitor` (only the fields passed change; `enabled=False`
  pauses it; `tags` and `private_locations` replace the list),
  `test_synthetic_monitor` (runs it once now, a paused monitor too, so its
  requests reach the target; returns the `test_run_id`; the result is written
  to the `synthetics-*` data streams, and no tool reads it; marked
  `openWorldHint=true`; each run adds a `LIGHTWEIGHT_SYNTHETICS_TEST_NOW_RUN`
  package policy to the location's agent policy, which Kibana removes itself
  about 20 minutes later) and `delete_synthetic_monitor`. A schedule's number
  goes out without leading zeros (`05m` is sent as `5m`; Kibana refuses
  `"05"`). Global parameters: `create_synthetic_param`,
  `update_synthetic_param` and `delete_synthetic_param`; no result ever
  carries a value, so a value the update replaces cannot be recovered by any
  tool (keep it if you may set it back), and a key already in use is
  refused. Private locations: `create_synthetic_private_location`
  (on a Fleet agent policy no other location uses),
  `update_synthetic_private_location` (the label) and
  `delete_synthetic_private_location` (refused while monitors use it). Each
  type's target field, the schedule and the locations are checked before
  Kibana is called, and a wrong id names the list tool that shows it. No tool
  takes `space`. Decision D46.
- **`get_synthetic_monitor` names the fix on a wrong id**: "synthetic monitor
  '<id>' not found — monitor_id is the id that create_synthetic_monitor or
  list_synthetic_monitors returns, not the monitor's name; call
  list_synthetic_monitors to find it", instead of Kibana's "Monitor id <id>
  not found!". Decision D46.
- **Alerting: mute, snooze and re-key a rule** (seven `write` tools, 150 tools
  in all). `mute_alert_rule` / `unmute_alert_rule` silence a whole rule with no
  end time, `mute_alert` / `unmute_alert` one of its alerts,
  `snooze_alert_rule` / `unsnooze_alert_rule` a time window (any ISO-8601
  `start`, default now; `duration` such as `30m`, `2h`, `1d`; optional
  `recurring`), and `update_alert_rule_api_key` regenerates the rule's key
  from the server's credentials. None changes the rule's definition or
  whether it runs. `get_alert_rule` now also returns `muted` and
  `muted_alert_ids`. Kibana never lists a rule's snoozes and keeps at most 5
  (expired ones count), so keep the `schedule_id` the snooze returns.
  `mute_alert` refuses a rule muted as a whole, where Kibana would accept the
  mute and record nothing. Kibana's rule-wide mute and unmute clear every
  per-alert mute, so both return the cleared alert ids in `cleared_alert_ids`;
  pass each to `mute_alert` to restore it. Only the call that clears them
  reports them: a repeat, or the unmute after the mute, returns an empty list.
  Decision D45.
- **`disable_alert_rule` takes `untrack`** (default off). `untrack=True` also
  marks the rule's active alerts untracked; without it the request and the
  result are unchanged. Decision D45.

- **The Claude live-model tier can export the server's spans** (default off).
  With `KIBANA_MCP_OTEL_ENABLED` set in the shell that runs `make test-e2e`,
  the harness copies every `KIBANA_MCP_OTEL_*` and `OTEL_*` variable into the
  server's private MCP config. One span per tool call then goes to the OTLP
  endpoint you name, Honeycomb included through `OTEL_EXPORTER_OTLP_HEADERS`.
  While it is on, the model's environment drops `OTEL_*` and `HONEYCOMB_*`.
  Unset, nothing changes. Setup in
  [docs/e2e-setup.md](docs/e2e-setup.md#exporting-the-servers-spans-from-the-claude-tier-default-off);
  decision D44.

### Changed

- **The DoD gate certifies only the newest supported Kibana version** (D53).
  Before `contract_green`, `e2e_replay_green`, `wheel_replay_green` and
  `e2e_green` it reads the running Kibana's version from `/api/status` with
  the seed key and requires the `ES_LOCAL_VERSION` pin in
  `elastic-start-local/.env.example` (9.5.4; in CI, the contract matrix's
  version). A stack on any other line — a study's 9.5.5, or 9.4.7 left over
  from `ES_LOCAL_VERSION=9.4.7 make test-contract` — is NO-GO on each of them,
  naming `scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh
  seed`; before, any seeded stack read GO. Each of their GO lines now names the
  version (`GO    contract_green  (Kibana 9.5.4)`). `test_stack_pin.py` also
  checks `scripts/cloud-setup.sh`'s fallback version, and the study notes
  (`WRAP_UP.md`, `LOCAL_STUDY.md`) say the 9.5.5 study stack is outside the
  supported set.
- **The DoD's `changelog_entry` checks that this change added an entry**
  (D54). It used to check only that CHANGELOG.md had a `## [Unreleased]`
  heading, which it always has, so it read GO on every tree. Now
  `scripts/checks/changelog-entry.sh` diffs the tree (untracked files included)
  against the merge-base with `origin/main` (fallback: the latest `v*` tag) and,
  when anything outside `scripts/experiment/runs/` and `.claude/plans/`
  changed (`uv.lock` included), requires a new entry under `## [Unreleased]` —
  a reworded, re-spaced or moved line, an amended old heading or an empty
  sub-heading is not one; otherwise NO-GO naming the fix. Without a base, or
  when git fails, it fails closed naming `git fetch origin main`. `checks.yml` runs the same script on every non-Dependabot PR.
- **The DoD verdict is bound to a tree** (D55). The gate's header and its
  `VERDICT:` line now name HEAD, the content tree id of the working tree
  (uncommitted and untracked files included, computed in a temporary index so
  the real one is untouched; racily clean edits and files marked
  skip-worktree or assume-unchanged included; an assume-unchanged file deleted
  from disk counts as deleted, an absent skip-worktree entry counts as
  deleted unless `core.sparseCheckout` is on, and paths outside the sparse
  cone are included; a submodule counts by its checked-out commit) and `clean` or `dirty`. A tree that changed while
  the criteria ran — an edit saved mid-gate, a `hygiene_hooks` fixer rewriting
  a file — is NO-GO `tree_stable`, naming the fix; before, the gate printed one
  GO over criteria that had certified two trees. `/tmp/dod-<repo>/` is cleared
  at the start of each run, so no earlier log sits beside the new ones, and
  ends with `verdict.json` (HEAD, tree id at start and end, dirty, config and
  its sha256, every criterion's result, Kibana version, Claude model,
  timestamps). A "GO at `<sha>`" claim is checked with `git rev-parse
  <sha>^{tree}` against the recorded tree id (AGENTS.md).
- **The DoD suites run as declared** (D56). The gate now clears
  `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `PYTEST_DEBUG`,
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD` and `COVERAGE_RCFILE` along with `MAKEFLAGS`
  and names the ones it cleared in its header; before, a leftover
  `PYTEST_ADDOPTS='-k test_stack_pin --no-cov'` ran 3 unit tests with no
  coverage floor and read `GO unit_green`. Every suite line shows `N passed /
  M collected` (deselected tests counted in M). A shell `KIBANA_URL` or
  `KIBANA_TEST_API_KEY` that differs from `elastic-start-local/.env.seed` is
  now NO-GO on the seeded-stack criteria naming `unset KIBANA_URL
  KIBANA_TEST_API_KEY`; before, the suites certified (and mutated) whatever
  Kibana the shell pointed at, with only a warning in a log. Both names
  count in any letter case and may not be set in `.env.local` in any case (a
  lowercase `kibana_url` beside the seed's `KIBANA_URL` sent the seed key to
  another Kibana). A
  `KIBANA_API_KEY`, `KIBANA_PUBLIC_URL` or `KIBANA_MCP_PUBLIC_KIBANA_URL` in the
  shell or `.env.local`, in any letter case, is refused the same way (the
  replayed server read `KIBANA_API_KEY` ahead of the seed's key; `Settings`
  reads names case-insensitively, so `kibana_public_url` re-linked it too), the gate also clears `KIBANA_MCP_SERVER_BIN` (a
  leftover one replayed an installed server instead of the checkout), a
  `UV_*`, `PYTHON*`, `LD_*` or `KIBANA_MCP_SERVER_BIN` line in `.env.local` is refused
  (merged back in after the clearing, a `UV_ENV_FILE` there loaded another key
  into the replayed server), and the
  suites' shadowing warning no longer prints the shell value. AGENTS.md and the
  gate header now say that `KIBANA_MCP_DOD_CYCLE_STACK=1` only stops the dev
  stack between the halves, never starts or restarts it, and leaves it stopped.
- **The docs are held to the registry** (D57), inside `unit_green`.
  `tests/unit/test_docs_counts.py` computes the tool counts from the registry
  and fails on any "N tools", "N-tool", "(R / W / D)", "N read/write/destructive"
  or "N edges from M tools" (also when wrapped across a line break, a
  Markdown hard break included) in README,
  `profiles/README.md`, `docs/` or the specs (not `decisions.md`, which records
  past figures) that the registry does not give in that place (a toolbox's
  spec may state only its own figures, the total and the space contract),
  naming the file, the line and each figure the registry gives there with what
  it counts; past figures sit in its `HISTORICAL` table, each exempting only
  its own figure. `test_docs_tool_reference.py`
  now requires one row per tool in its own toolbox's table with the registry's
  tier, the dashboards annotation table to match the real hints, and each
  "N reads + M writes" sentence to match; before, it only checked that each
  name appeared somewhere on the page served to models as `docs://tools`.
  `tests/unit/test_docs_test_citations.py` requires every test the docs cite
  to exist. Fixed what they found: the shared space contract is 92 tools, not
  86, in the alerting, cases and data-management specs; data-management's
  unit test pins 17 tools, not 11; platform-health.md cites
  `test_read_only_explorer_profile_is_fourteen_read_tools` (renamed). design.md
  now says `docs://tools` is deliberately not tier-gated.

### Fixed

- **A lowercase `kibana_url` in the shell no longer fails the unit tests.**
  `Settings` reads env names in any letter case, but the unit tests cleared
  only the uppercase ones, so a shell `kibana_url` or `kibana_api_key` failed
  5 of them and made `unit_green` NO-GO (never a false GO). An autouse
  fixture in `tests/unit/conftest.py` now drops every `KIBANA*` name not in
  uppercase.
- **OpenTelemetry export no longer loses the last spans of a stdio session.**
  The server now exports 500 ms after a span ends instead of the SDK's 5 s
  (`OTEL_BSP_SCHEDULE_DELAY` still wins), and flushes its queue on SIGTERM as
  well as on a normal exit. Claude Code stops a stdio server within about
  half a second (SIGINT, SIGTERM, SIGKILL), and a 20-run `make test-e2e`
  block had sent 72 spans for 80 tool calls, the missing ones its runs' final
  calls. A second block sent 78 of 78. The SIGTERM flush waits at most 2 s,
  so an unreachable collector cannot hold the exit up, and a second SIGTERM
  ends the process at once. Only affects `KIBANA_MCP_OTEL_ENABLED=1`;
  decision D44.
- **`make build` (and the DoD gate's `image_smoke`) works inside a
  TLS-intercepting sandbox.** The image's `uv sync` failed there with
  "invalid peer certificate: UnknownIssuer". `scripts/checks/image-build.sh`,
  now the one build recipe, passes the proxy CA as a BuildKit secret and
  builds on the host network only when that CA exists
  (`KIBANA_MCP_PROXY_CA`, default `/root/.ccr/ca-bundle.crt`, as
  `scripts/stack.sh` decides); the Dockerfile's sync step reads it from
  `/run/secrets` and no image layer keeps it. Elsewhere the build is
  unchanged.
- **A resumed Claude Code cloud session starts Docker again.** The session
  hook removed the snapshot's stale `/var/run/docker.pid` but not the
  containerd pidfile beside it, so when a reused PID sat behind it dockerd
  failed with "failed to start containerd: ... process with PID N is still
  running" and every stack command failed. The hook now removes both when no
  dockerd runs.

### Security

- Locked dependencies moved past advisories `pip-audit` now reports:
  aiohttp 3.14.5, certifi 2026.10.23, fastmcp 4.1.3, filelock 4.2.4 and
  virtualenv 21.14.13 (with yarl, packaging and openapi-pydantic as resolved).
  `make check`'s audit step fails without them.

## [0.4.0] - 2026-10-17

### Added

- **Named tool lists in `KIBANA_MCP_TOOLS`** (default off). An entry starting
  with `@` stands for a list of tools and mixes with plain names:
  `KIBANA_MCP_TOOLS=@dashboards-analyst,create_alert_rule`. Two lists are
  built in — `read-only-explorer` (11 tools) and `dashboards-analyst` (21, the
  default selection at `write`) — and `KIBANA_MCP_TOOL_LISTS_FILE` names a
  TOML file of your own (`version = 1`, `[lists.<name>] tools = [...]`; the
  format is a documented compatibility contract, validated strictly; a list
  named like a built-in is refused). Lists expand before names are checked,
  so the published set is still the listed tools the tier allows; an unknown
  `@name` fails startup naming the lists that exist. With `KIBANA_MCP_TOOLS`
  unset nothing changes and the file is never read. With a plain list (no `@`
  entry) the published tools are unchanged; what is new is the coherence
  warning below on stderr, and a named list file is still read and validated,
  so a broken one fails startup. A leading `~` in the file path is expanded;
  a relative path resolves against the directory the MCP client starts the
  server in, which an unreadable-file error names. The profiles keep naming
  toolboxes and a tier, and a unit test keeps each built-in list equal to its
  profile, so a new tool in those toolboxes cannot drift out of the list
  unnoticed; `profiles/README.md` points to the list table instead of
  repeating tool names.
- **Coherence warnings for `KIBANA_MCP_TOOLS`.** When a published tool relies
  on a tool the list left out — `update_panel` on `get_dashboard` for panel
  indexes, `create_dashboard` on `describe_data_view` for field names,
  `update_alert_rule` on `get_alert_rule` for the params it merges into, and
  31 more relationships kept as data in one map — one stderr line names each
  missing tool and what relies on it. The server still starts. Decision D41.
- **`KIBANA_MCP_TOOLS`** (default off): a comma-separated allow list of tool
  names. When set, the server publishes the listed tools the tier allows and
  nothing else, registering whichever toolboxes own them;
  `KIBANA_MCP_TOOLBOXES` is then ignored (one stderr line if it was set). A
  listed tool above `KIBANA_MCP_TIER` stays hidden and is named on stderr with
  the tier that would publish it; a name that is not a tool fails startup with
  a "did you mean" suggestion. Read at start time only. The list only hides
  tools, so it can never publish one the tier hid; it is curation, not a
  security boundary — the API key's RBAC is. Decision D34.
- **`update_alert_rule`, `update_connector` (alerting, write) and
  `get_connector` (alerting, read).** An agent at the default `write` tier can
  now fix a rule or connector in place instead of deleting (destructive) and
  recreating it. Both updates read the object and re-send what the caller did
  not pass, because Kibana's updates are full replaces that would otherwise
  empty a rule's actions and tags or a connector's config; a rule's `params`
  and a connector's `config` merge key by key. `update_alert_rule` never
  changes `enabled`; actions passed with a per-action `frequency` replace a
  rule-level `notify_when`/`throttle`, which Kibana refuses beside them.
  `get_connector` returns the non-secret config and flags,
  never secrets. `update_connector` refuses preconfigured connectors, and
  refuses a call without `secrets` on any type except `.index` and
  `.server-log`, since Kibana resets omitted secrets and cannot return them.
  All three take `space`. Decision D35. The three tools join the local
  study's server entry (dashboards, data-management, alerting at `write`);
  `local_study.json`'s `expected_tools` and its runbook follow (33 tools once
  this release's additions are counted, below).
- **`update_data_view` (data-management, write).** An agent at the default
  `write` tier can now fix a data view's `name`, `index_pattern` or
  `time_field` in place, by id, instead of deleting it (destructive) and
  recreating it. Only the fields passed change. Kibana stores any time field,
  even one that does not exist or is not a date, and every chart built on the
  view then inherits it, so the tool refuses a time field that is not
  a date field of the view and lists the ones that are. When the index
  pattern changes, the time field is checked against the new pattern's
  fields, and a mismatch writes the old pattern back, so a refused call
  changes nothing; if a write after the pattern's fails, the error says the
  pattern changed and names the call that undoes it. A pattern that matches no index yet is accepted with
  `field_count` 0. A wrong id names `list_data_views`. Takes `space`.
  Decision D36. The tool joins the `dashboards-analyst` profile and the local
  study's server entry; `local_study.json` and its runbook follow (final
  counts below).
- **Library visualizations: `search_visualizations`, `get_visualization`
  (read), `update_visualization`, `add_library_panel` (write) and
  `delete_visualization` (destructive), in the dashboards toolbox.** The id
  `create_visualization` returns can now be used: find a library chart by
  title, read it back as a VizSpec (chart type, data view, metrics,
  group_by, filter query), replace it in place with a new spec (validated
  against the real data view first, like every chart tool; a wrong id is
  refused and never creates one), delete it, and show it on an existing
  dashboard. `add_library_panel` links the chart by reference rather than
  copying it, so `update_visualization` changes it on every dashboard that
  shows it. A dashboard holding such a panel stays editable by every other
  dashboards tool; `get_dashboard` reports the panel with a
  `visualization_id` (inline panels are unchanged); `update_panel` on it
  refuses and names `update_visualization`, or `delete_panel` when the chart
  it shows was deleted. Kibana lets a chart be deleted
  while dashboards show it, leaving a broken panel there;
  `delete_visualization`'s description says so and names `delete_panel`.
  All five take `space`. Decision D37. The `dashboards-analyst` profile now
  lists 21 tools, `read-only-explorer` 11, and the local study's server
  entry 33; `local_study.json` and its runbook follow.

### Changed

- **`get_alert_rule` now returns the rule's `params` and `actions`** beside
  its summary fields, so an agent can read a rule before `update_alert_rule`
  merges into its params and read it again to confirm the edit. `params` are
  what Kibana stores, defaults included; each action is `id` (the connector
  id), its `uuid`, `group`, `params` and `frequency` when set — the shape
  `update_alert_rule` takes, so an action can be read, edited and passed back
  (Kibana's `connector_type_id`, which the update rejects, is left out). The
  `uuid` comes back so a read → update round trip keeps each action's
  identity: Kibana keeps a uuid the update sends and mints a new one for an
  action sent without it (probed on 9.5.4; amendment to D40, after an
  independent review found the first version dropped it). Nothing secret
  comes back: a rule holds none (secrets live on connectors), and the GET
  response carries no API key, only `api_key_owner`, which is not returned
  either (probed on 9.5.4).
  `list_alert_rules` and `update_alert_rule` still return the summary; the two
  descriptions point to `get_alert_rule`. Decision D40. Why: in the first
  counted Claude block every `fix_rule_threshold` final answer said the
  model could not see the rule's params
  (`scripts/experiment/runs/claude-e2e/counted-2026-10-17/`). A second
  counted block with the same pre-registered tasks, run after the change, is
  committed with its generated summary in
  `scripts/experiment/runs/claude-e2e/counted-2026-10-17-d40/`; in it every
  `fix_rule_threshold` run reads the rule back after the edit.
- **The local Definition-of-Done gate requires the Claude live-model run**
  (D42, the owner's answer to Q16; supersedes D31 in part). `e2e_green` is
  `required` in `dod.config`, and the gate now runs the Claude tier for it —
  `make test-e2e`'s `pytest -m e2e_claude`, model `CLAUDE_E2E_MODEL`, default
  `haiku` — against the seeded stack, writing its run records to the gate's
  log directory; it no longer runs the LM Studio tests, which stay on demand
  with `make test-e2e-local`. A missing `claude` CLI or sign-in reads NO-GO
  and names the fix (install Claude Code; `claude auth login`). `make dod`
  therefore needs a signed-in `claude` CLI and about 5–6 more minutes. The
  criterion's GO line names the model and runs per task it ran with
  (`CLAUDE_E2E_MODEL` / `CLAUDE_E2E_RUNS`), so a GO from an override is
  visible. Every
  `.github/dod` config keeps `e2e_green = n/a`: CI has no Claude sign-in, and
  no CI job or secret is added. Why: the old rule's LM Studio run never ran on
  this branch in practice, while the Claude run needs only a sign-in and
  passed 40 of 40 across the two committed blocks
  (`scripts/experiment/runs/claude-e2e/counted-2026-10-17*/SUMMARY.md`).
- **`make test-e2e` is now the live-model tier driven by Claude; the LM Studio
  tests moved to `make test-e2e-local`, unchanged.** `make test-e2e` runs
  headless Claude Code (`claude -p`) with this server's tools and nothing
  else — no shell, files or web; `--strict-mcp-config`; `--permission-mode
  dontAsk` with only `mcp__mcp-for-kibana` allowed — through four tasks, five
  runs each: the three LM Studio tasks, whose prompts and scoring now live in
  one shared module, and `fix_rule_threshold`, where a seeded alert rule has a
  wrong threshold and the model must fix it in place. Scoring reads Kibana's
  state, never the model's text. The model is `CLAUDE_E2E_MODEL`, default
  `haiku`; `CLAUDE_E2E_RUNS`, `CLAUDE_E2E_BUDGET_USD` (default $5 per session)
  and `CLAUDE_E2E_OUT` set the rest. The Kibana API key reaches only the MCP
  server's environment, and every transcript is searched for it: a hit is
  redacted on disk and fails the run. Each run writes its prompt, transcript
  and record; `scripts/experiment/claude_e2e_report.py` summarises a block.
  Tasks, criteria and N are pre-registered in
  `scripts/experiment/CLAUDE_E2E.md`. Needs a Claude sign-in, no API key; off
  by default and outside CI's gates; the local DoD gate requires it (D42).
  Decisions D38 (supersedes D31's on-demand rule in part) and D39 (the
  default `write` surface of 21 tools, past the ~20-tool heuristic, is
  accepted). The first
  counted block (Haiku, Kibana 9.5.4) is committed with its generated summary
  in `scripts/experiment/runs/claude-e2e/counted-2026-10-17/`.

### Fixed

- **`create_data_view` refuses a time field the index does not have.** Kibana
  stores any string as a new view's time field, so
  `create_data_view(index_pattern="kibana_sample_data_flights",
  time_field="@timestamp")` succeeded although flights' date field is
  `timestamp`, and `create_dashboard` refused the view later (reported by a
  study run on 0.3.0). The tool now checks the time field the way
  `update_data_view` does (D36): it must be a date field (`date_nanos`
  included) of the pattern, else the error lists the pattern's date fields and
  no data view is left — Kibana's public API cannot list a pattern's fields
  without creating a view, so the view is created, checked and deleted again;
  if that delete fails, the error names the view's id and the
  `update_data_view` / `delete_data_view` call that fixes or removes it. A
  pattern that matches no index yet is still accepted unchecked. A create under
  a name already used (the name defaults to the pattern) now adds the existing
  view's id and names `update_data_view` to Kibana's `Duplicate data view`
  error. See D43 and the [tool reference](docs/tools.md#data-management-toolbox).
- **A Claude-tier run that never started reports why.** When seeding the task
  or launching `claude` raised (no CLI on PATH, Kibana down), the test's first
  assertion read the missing leak flag as a leak and said the API key appeared
  in the output. The harness error is now asserted first and names the fix
  (`claude auth status`; `scripts/stack.sh up && scripts/stack.sh seed`), and
  the test also asserts the run is valid — the CLI ended with a result event
  or timed out — as `scripts/experiment/CLAUDE_E2E.md` requires for a pass.
- **Docs match the shipped tiers and tools.** `CONTRIBUTING.md` names Claude
  for `make test-e2e` and lists `make test-e2e-local`; the README lists
  `e2e_green` among the criteria that need the dev stack up and says the
  LM Studio tier runs three of the four tasks (not `fix_rule_threshold`);
  `docs/e2e-setup.md` covers installing Claude Code, `claude auth login` and
  `claude auth status`, which the gate's NO-GO text points to; the tool
  reference lists the three `add_esql_*_panel` write tools in the dashboards
  and annotations tables; D33 carries the scope note D38 narrowed it with; and
  the roadmap files `update_alert_rule` returning the rule's detail as open
  work, with the addressed `params` item marked done.

- **The server's MCP instructions follow the published tools.** They always
  described the dashboards flow (`list_data_views` → `describe_data_view` →
  `create_dashboard`), even on a surface without those tools — the `alerting`
  toolbox alone, the `read` tier, or a `KIBANA_MCP_TOOLS` list without them.
  That text is now sent only when all three tools are published; any other
  surface gets a generic text that names no tool. The default selection keeps
  the exact text it had. See
  [Server instructions](docs/configuration.md#server-instructions).
- **The streams ephemeral certification waits for Kibana's own Streams
  startup.** `scripts/ephemeral_stack.sh` started the test as soon as
  `/api/status` answered 200, about 2 s before Kibana's Streams plugin
  finished creating the wired roots under its lock, so the first call could
  fail with a 409 ("Could not acquire lock for applying changes") or
  `no_shard_available_action_exception`. It now waits up to 120 s for Kibana
  to log `Streams preconfigured successfully`, and fails naming that line if
  it never appears.
- **The replay tier no longer inherits an exported tool selection.** The
  harness passed the whole environment to the replayed server, so an exported
  `KIBANA_MCP_TOOLS` (or `KIBANA_MCP_TOOLBOXES`) silently replaced the surface
  each transcript was recorded against and failed every replay. It now drops
  `KIBANA_MCP_TOOLS`, `KIBANA_MCP_TOOLBOXES` and `KIBANA_MCP_TIER` from the
  inherited environment before applying the transcript's own settings.
- **Docs describe tool selection as it ships.** The architecture page, the
  tool reference, the regeneration guide and the bug-report template now name
  `KIBANA_MCP_TOOLS` beside `KIBANA_MCP_TOOLBOXES`; the tool reference's
  "auto-filled `time_field`" heading and the sentences around it, garbled in
  an earlier edit, are restored, so links to that section work again; the
  live space gates' docstrings and the roadmap state the current tool counts
  of their toolbox sets (29 and 20 at `write`).
- **The local-study results page no longer states a tool count the runs did
  not record.** It printed `local_study.json`'s current `expected_tools` (now
  33) as the count the committed runs used (they ran with 25 and did not
  record it). `local_run.py` now lists the server entry's tools once per
  `pilot`/`full` invocation and stamps the count on every run record as
  `server_tools`; `local_analyze.py` carries it into `analysis.json`; and
  `local_report.py` states the count(s) from the records, or says in words
  that the records do not carry one.

## [0.3.0] - 2026-10-12

### Added

- **`list_rule_types` (alerting, read).** Lists the rule types this Kibana
  offers with the consumers the caller may create each under and its licence
  status, so `create_alert_rule`'s `rule_type_id` and `consumer` no longer have
  to be known in advance. It does not describe `params`: Kibana's API publishes
  no params schema. Shipped from the alerting toolbox's recorded next work.
- **`KIBANA_MCP_OTEL_TRACEPARENT_FROM_ENV`** (default off): with OTel export
  on, the server parents all its spans under the W3C `TRACEPARENT` it was
  started with, so a stdio client that passes trace context only through the
  environment (Claude Code in `-p` mode) sees the server's spans in its trace.
- **Three-arm analysis and the live cross-check for the rerun.**
  `efficiency_analyze.py` adds `with-mcp-es` to the summaries when its runs
  are present and writes its comparisons with `no-mcp` and `with-mcp` under
  `pairs` (own RNG streams; two-arm output byte-identical to before).
  `live_crosscheck.py` (`specs`, `capture`, `build`) compares the transcript
  pipeline with what Claude Code, the hook and the runner emitted live into
  the Honeycomb dataset `mcp-for-kibana-live`, and the hook's per-run rollups
  with the transcript values, run by run.
- **`live_report.py`**: the live rerun's results page (three arms, the live
  telemetry and what Honeycomb reproduced), every number read from
  `runs/efficiency/live/analysis.json`, `honeycomb_crosscheck.json` and the
  run records; reuses `efficiency_report.py`'s helpers and style.
- **`run_blocks.sh --skip-done`** (additive): skips a cell whose run directory
  already holds a `record.json`, so an interrupted chunk of the efficiency
  study resumes where it stopped.
- **A third arm for the efficiency study rerun**, `with-mcp-es` (additive;
  `with-mcp` and `no-mcp` are unchanged). `efficiency_run.py --arm with-mcp-es`
  registers mcp-for-kibana plus Elastic's Elasticsearch MCP server
  (elastic/mcp-server-elasticsearch v0.4.6, Docker image pinned by digest,
  stdio) so the model can look at the data through a tool instead of curling
  Kibana's console proxy. The runner mints a per-run Elasticsearch API key that
  can only read the web-logs data stream, passes it only to that server's
  config and invalidates it after the run; the relay scrubs it and the record
  keeps the key id, privileges and whether it was invalidated or seen. The
  agent-signals hook names the answering server (`agent.tool.mcp_server`).
  `run_blocks.sh --three-arms` runs nine cells per block with the arm order
  rotated by block, and passes extra runner flags after `--`. Setup, the
  server's caveats and the smoke run: `scripts/experiment/LIVE_RERUN.md`.
- **Local-model study: harness loading and late sends.** `lmstudio.load:
  harness` loads each study model at its maximum context through LM Studio's
  REST API and unloads the previous study model, so the study runs with JIT
  loading off; `local_run.py send-pending` sends the stored spans of runs made
  without the Honeycomb key.
- **Local-model study results page.** `scripts/experiment/local_report.py`
  builds the page from `runs/local/<phase>/analysis.json`, the config and the
  recorded environment: pass rate by chain length per model with Wilson
  intervals, where chains break, tool-call failures and cost per step.
- **Live telemetry for the efficiency study rerun** (opt-in).
  `efficiency_run.py --live-telemetry` gets every signal at run time from the
  component that owns it: Claude Code's own spans and log events (prompt,
  response and tool content; never raw API bodies), the server's tool-call
  spans, a silent Claude Code hook (`agent_signals_hook.py`) that emits the
  `agent.*` signals of SIGNALS.md per tool call with the same classifier as
  the transcript pipeline, and a runner root span with the outcome and per-run
  rollups, all in one trace per run. A loopback relay (`live_telemetry.py`)
  scrubs the Kibana and Honeycomb keys and the account identity attributes,
  keeps a copy per run and forwards to a study dataset; the Honeycomb key
  never reaches Claude Code, the model's shell or the server. The mode also
  turns on the fixes parked after the first study (first result event, denials
  from the whole stream, sample data checked not reinstalled, prompt v2) and
  offers `--key-out-of-model-env` and `--server-label` for the open decisions.
  `live_smoke.py` proves the path in one probe run; `otlp_sink.py` records
  what any OTLP exporter sends. Findings and the command sequence:
  `scripts/experiment/LIVE_RERUN.md`. Without the flag the runner is unchanged.
- **Cloud sessions start ready to work.** A SessionStart hook in
  `.claude/settings.json` runs `scripts/cloud-session-start.sh`, which starts
  `dockerd` (the environment snapshot keeps the images, not the daemon) and runs
  `make setup` (the clone is fresh each session). It exits at once outside a
  Claude Code cloud session. Ported from kibana-py. `.claude/settings.json` is
  the one tracked file under `.claude/`; the rest stays ignored.
- **Efficiency study runner.** `scripts/experiment/efficiency_run.py` drives
  `claude -p` through one end-to-end web-logs task (explore the data view,
  build a dashboard, create a 5xx alert rule) with and without the server, on
  an identical prompt and identical built-in tools. Each run keeps its
  transcript, metrics, success check and produced objects under
  `scripts/experiment/runs/efficiency/`, resets Kibana to its pre-run state,
  and sends one OpenTelemetry trace to Honeycomb when `HONEYCOMB_API_KEY` is
  set. `.claude/settings.json` allows that one command, so a cloud session can
  launch the runs.
- **Efficiency study analysis.** `scripts/experiment/efficiency_analyze.py`
  (stdlib only) re-derives every full-study run's metrics from its transcript
  and writes `scripts/experiment/runs/efficiency/full/analysis.json`: per-run
  metrics, a per model × arm summary (strict and lenient pass counts; median,
  mean and IQR of cost, tokens, turns, tool calls and duration) and the
  with-minus-without difference and ratio of medians per model and pooled,
  with a seeded 95% bootstrap CI on all runs and on passing runs. It measures
  at the first `result` event, because `parse_stream` keeps only the last one
  and 8 no-mcp runs carry a second, one-turn result after a hung background
  task was killed; the all-results totals are kept as a sensitivity check.
  Permission denials are counted from the stream, and each run records how its
  alert rule got enabled.
- **Agent observability signals for the efficiency study.**
  `scripts/experiment/SIGNALS.md` defines a three-tier signal catalogue:
  OpenTelemetry GenAI semconv 1.41 attributes, generic `agent.*` agent-health
  signals grouped by operator question (outcome; effort and cost; where time
  goes; tool reliability with error classes, hidden failures, retries and
  recovery; safety: secret exposure, environment enumeration, permission
  denials, out-of-workdir paths, curated-tool bypass; harness anomalies), and
  `study.*` dimensions. `efficiency_analyze.py` now derives these signals for
  every run and writes them to `analysis.json`, together with per model × arm
  summaries and bootstrap comparisons; the existing metrics and comparisons
  are unchanged. The secret scan matches the runs' Kibana API key exactly
  (without writing it anywhere) and generic credential patterns. It finds the
  key in 2 of 90 no-mcp runs and in 0 with-mcp runs. The new
  `scripts/experiment/efficiency_honeycomb.py` sends the study to the Honeycomb
  dataset `mcp-for-kibana-study` as `invoke_agent`/`chat`/`execute_tool`
  traces carrying these signals. It redacts content, refuses to send if the
  key or any credential pattern remains, checks token and tool-call totals
  against `analysis.json`, and has `--check` and `--dry-run` modes.
- **Study wrap-up.** `scripts/experiment/WRAP_UP.md` is the single overview
  after the 10–12 October sessions: the three studies' status (study 1
  complete; the live-telemetry rerun and the local study stopped early), what
  study 1 showed, the lessons for designing and running the next studies, the
  owner's open items and where every file lives. The partial data of studies
  2 and 3 and the Honeycomb datasets stay until the next studies complete.
- **The study names the agent, not just the models.** The results page and
  the design record state that every run drove the Claude Code agent headless
  (`claude -p`), so the numbers describe Claude Code with each model, not the
  bare models.
- **Efficiency study design record.** `scripts/experiment/STUDY_DESIGN.md`
  holds the study's ten decisions with the question, the options considered
  and what was settled, the background research (brief, statistics for
  repeated agent runs, Kibana release check), the findings from the runs and
  the facts checked during the design. The study's working board keeps only
  the current state and links here.
- **Efficiency study results page.** `scripts/experiment/efficiency_report.py`
  (stdlib only, deterministic) writes `<out>/index.html`, a claude.ai artifact
  body, and copies the chosen snapshot PNGs to `<out>/img/`. Every number is
  read from `analysis.json`, the run records or the snapshots directory, and
  the page stamps the commit and SHA-256 of the `analysis.json` it read. It
  answers first, then covers what was compared (with a diagram of both arms),
  results by model with a ratio chart, why the server arm is cheaper (model
  calls against tokens per call, a to-scale timeline of a typical Opus run per
  arm built from its transcript, and a time-split chart), reliability, safety,
  the snapshots, and method. Tokens per call are reported as measured (1.13×
  pooled, 1.45× for Sonnet), and the page notes that the API key was also in
  the shell environment of the server arm.
- **Honeycomb cross-check of the efficiency study.**
  `scripts/experiment/efficiency_crosscheck.py` recomputes 320 of the study's
  numbers with 18 Honeycomb queries over the run traces: `capture` stores each
  query result (spec, rows, run link) under
  `scripts/experiment/runs/efficiency/full/honeycomb/`, and `build` compares
  them with `analysis.json` into `honeycomb_crosscheck.json`. 153 match
  exactly, 156 differ only by Honeycomb's percentile rule (the observation at
  index floor(p·n), verified against our runs), 2 differ for an explained
  reason (COUNT_DISTINCT is an estimate; the AI conversation view counts tokens
  on chat spans only), 8 bootstrap CIs are not expressible and 1 value was
  never sent; none disagrees. `scripts/experiment/honeycomb_report.py` renders
  the cross-check as a report page (verdict, method-difference chart, each
  query explained with its link, the full table, gaps and instrumentation
  recommendations).
- **Local-model study harness.** `scripts/experiment/local_run.py` drives LM
  Studio models through mcp-for-kibana on a web-logs chain of growing length
  (L1 describe the data view; L2 adds a two-panel dashboard; L3 adds an enabled
  5xx alert rule; L4 adds a panel edit and a read-back of the rule), with a
  strict check per step. Every study value lives in one config,
  `scripts/experiment/local_study.json`. `preflight` runs read-only checks and
  names the fix for each failure (it reads `~/.lmstudio/mcp.json` and never
  writes it). `pilot` and `run` resume after a crash, reset Kibana after every
  run, retry runs where the environment failed, and keep each run's prompt,
  stream, response, artifacts and spans. Each run is sent live to Honeycomb as
  an OpenTelemetry GenAI trace with the agent.* and study.* signals, after
  redaction and a check that no secret remains. The runner reads the first
  final stream event, counts denials from the stream, checks sample data with
  `GET /api/sample_data` instead of reinstalling it per run, and keeps the
  Kibana key out of the prompt. `scripts/experiment/local_analyze.py` writes
  pass rates per model × level with Wilson intervals, pass^k, medians per step
  and the chain-length breakdown, and supports the Honeycomb cross-check.
  `scripts/experiment/LOCAL_STUDY.md` is the runbook for the Mac session. The
  harness is tested end to end against fake LM Studio, Kibana and Honeycomb
  servers (`tests/unit/test_local_study.py`); the real pilot runs on the Mac.

### Changed

- **No model is privileged in the live-model tests (D33).** `make test-e2e`
  drives the model `LMSTUDIO_MODEL` names and fails with the fix when it is
  unset; `google/gemma-4-12b-qat` is no longer the fallback or "the reference
  model". D18's measurements stay as evidence; its reference status is
  superseded.
- **The Definition of Done needs no LLM** (D31). `e2e_green` is `n/a` in
  `dod.config`, as it already was in every `.github/dod` config, so `make dod`
  can reach GO on CI and in a Claude Code cloud session. The replay tiers
  (`e2e_replay_green`, `wheel_replay_green`) carry the per-change guarantee; the
  three live-model tests run on demand with `make test-e2e` when a change
  touches tool names, descriptions or schemas.

### Fixed

- **`create_dashboard` accepts a time-series panel without `time_field`.**
  The panel schema says `time_field` is filled in from the data view when
  omitted, but a `date_histogram` panel without it was rejected before the
  server could fill it in. In the efficiency study this failed the first
  `create_dashboard` call in all 90 with-server runs. The server now fills
  in the data view's time field first. The pre-flight rejects a
  `date_histogram` only when the data view has no default time field, and
  the error names the field to set (`time_field: '<field>'`). This applies
  to `create_visualization`, `add_panel` and `update_panel` too. Tool names,
  descriptions and schemas are unchanged (D32).
- **A wrong dashboard or alert-rule id names the fix.** An empty `dashboard_id`
  reached the caller as a bare "Error calling tool" (kibana-py's unmapped
  ValueError), and an id that matched nothing returned Kibana's "Saved object
  [alert/<name>] not found". Both now say where a valid id comes from
  (create/search or create/list tools) and that a title or name is not an id.
  Seen in the local-model study: small models passed dashboard titles, rule
  names and empty strings as ids.
- **The local study expects the alerting toolbox's 25 tools.** Its server
  entry (dashboards, data-management, alerting at `write`) gained
  `list_rule_types`; `local_study.json`'s `expected_tools` and the runbook
  said 24, so preflight failed.
- **No-mcp runs in live mode now land in one trace.** Claude Code 2.1.296
  starts its first query before its tracer is ready when it has no MCP server
  to connect: no `claude_code.interaction` span, `TRACEPARENT` ignored, every
  model and tool span its own trace (found in the rerun's smoke run). In live
  mode the runner now connects a tool-less stdio MCP server
  (`scripts/experiment/trace_anchor_server.py`) in the no-mcp arm; the model's
  tools and first prompt are unchanged (5,047 tokens with and without it).
  `--no-trace-anchor` leaves it out; the record says `trace_anchor`.
  Details: `scripts/experiment/LIVE_RERUN.md`, "The no-mcp trace anchor".
- **Span message fitting always terminates.** `efficiency_honeycomb.fit` re-truncated
  the same 221-character leaf forever once every leaf sat at its 200-character
  floor and the JSON still exceeded the cap; a 75-tool-call local-study run
  stalled the full run for four hours. It now falls back to the byte cut when
  truncation makes no progress.
- **Local-study preflight reads Kibana's version with the key.** Real Kibana
  answers an anonymous `/api/status` without the version, so preflight failed
  on a correct 9.5.5 stack.
- **The commit-msg stage runs only the message hooks.** Without
  `default_stages`, every file hook also ran at commit-msg, so CI's per-commit
  message loop re-ran the full file-hook set for each commit (about 26 s per
  commit) and the 89-commit 0.3.0 PR exceeded the job's 10-minute limit.
  `default_stages: [pre-commit]` leaves the pre-commit hook set unchanged; the
  commit-msg pass now runs Conventional Commit and the provenance trailer
  check (0.19 s).

### Security

- **multidict 6.7.1 → 6.9.1 (CVE-2026-104874).** A transitive dependency
  (`kibana-py[async]` → `aiohttp`/`yarl`); `pip-audit` failed the
  `audit_clean` gate on main. Lockfile regenerated with
  `uv lock --upgrade-package multidict`; `make audit` is clean again.

## [0.2.0] - 2026-10-04

### Added

- **Kibana 9.5 is supported alongside 9.4** (D30, superseding D29). The
  supported set is kibana-py's `kibana.SUPPORTED_VERSIONS` — today 9.5.4 and
  9.4.7 — and the CI contract job runs once per version in it, contract tier and
  e2e replay both. `scripts/stack.sh up` takes an `ES_LOCAL_VERSION` override, so
  one script provisions either line (`CONTRIBUTING.md` shows how). No server code
  changed: on 9.5.4 the contract tier passed 125 tests, the e2e replay 4 and the
  live-model gate 1 (2026-10-04).
- **`wheel_replay_green`: the replay tier against the installed package.** Every
  other live tier runs the checkout; this DoD criterion builds the wheel,
  installs it into a fresh venv with `pip` (dependencies resolved from the
  `pyproject.toml` ranges, as a PyPI install resolves them, not from `uv.lock`)
  and replays the recorded sessions with the server launched from that venv
  (`scripts/checks/wheel-replay.sh`, `KIBANA_MCP_SERVER_BIN`). Required locally
  and in CI's contract job, on every supported Kibana line.

### Changed

- **kibana-py 0.6.0 is the minimum** (was 0.5.0), and older releases are no
  longer supported: the project is pre-production and carries no compatibility
  burden for earlier client versions. 0.6.0 is the first release that supports
  Kibana 9.5 — it gives `dashboards.get_all()` the same `dashboards`/`total`
  envelope on 9.5 that `search_dashboards` reads on 9.4, where 0.5.0 on 9.5
  would return an empty list without an error. No gateway code changed: the
  four Fleet routes whose answers changed in 0.6.0 and the significant-events
  methods that now raise `KibanaVersionError` on 9.5 are not called by this
  server. Contract tier green on 9.4.7 — 125 passed, 2026-10-04.
- **fastmcp 4 is supported**: the range is `fastmcp>=3.4.7,<5` (was `<4`),
  and the lock and the container image use 4.0.10. No server code changed;
  the unit, contract, ephemeral and live-model tiers are green on it.
- **The reference stack tracks the newest supported Kibana, 9.5.4** (was
  9.4.3), in `.env.example`, `.env.ephemeral.example` and the cloud-setup
  fallback — the line the dev stack, both ephemeral tiers and the live-model gate
  run on. `tests/unit/test_stack_pin.py` fails when a kibana-py release moves its
  newest supported version, until both pins follow. The version claims in the
  README, the docs site, the architecture notes, the user guide, the brief and
  two toolbox contracts now name 9.4 and 9.5 (tested on 9.4.7 and 9.5.4).
- **Scope is Basic-only, and both front doors say so.** `README.md` and
  `docs/index.md` gain a *Licensing and scope* section: every tool this server
  registers works on a Basic license, and functionality that needs a paid Elastic
  subscription is out of scope. The corollary is that Kibana ships its own MCP
  server as part of Agent Builder, whose APIs require an Enterprise subscription,
  so a deployment on that tier already has an official one.
- **Subscription-gated surfaces are recorded as out of scope, not deferred.** The
  roadmap no longer tracks them as open work, and the spec corpus
  (`brief.md` non-goals; the `alerting`, `cases`, `observability`,
  `security-detections` and `platform-admin` contracts, all bumped to Draft v1.1),
  `docs/tools.md`, `profiles/README.md` and the three toolbox module docstrings
  now say out of scope where they said deferred. Nothing was ever built for these
  surfaces, so no tool changed: observability SLO reads, `platform-admin` logstash
  pipeline reads, the observability AI-assistant tool, the `security-ai` toolbox,
  `security-entity-analytics`, the `ai-automation` toolbox, and `alerting`
  maintenance windows.
- **The scope decision is in the ledger.** `docs/spec/decisions.md` gains **D28**
  (Basic-only is the boundary, not a stage) under *Scope and licensing*, so a
  rebuild does not re-file the dropped surfaces; `brief.md` and the roadmap cite
  it. The ledger bumps to Draft v1.1.
- **Two personas are withdrawn rather than planned.** `observability-sre` and
  `soc-analyst` can never graduate to a runnable profile — the capabilities that
  define them (Platinum SLOs; an Enterprise `.gen-ai` connector) are out of scope —
  so `profiles/README.md` gains a ⬛ *withdrawn* status instead of leaving them
  listed as forthcoming.
- **`e2e_green` runs all three live-model gates**: the flights dashboard plus
  the dashboards and alerting space chains, one per capability D18 names. The
  reference model `gemma-4-12b-qat` passed both space chains 5/5 on Kibana
  9.5.4 (2026-10-04, `scripts/experiment/runs/adhoc.jsonl`) and is now the
  `LMSTUDIO_MODEL` fallback in every live-model test (was `gpt-oss-20b`, which
  fails the space gates). The gate writes its run records to its own log dir,
  so `make dod` never modifies the tracked run files. `gemma-4-26b-a4b-qat` was
  measured as a faster alternative and not adopted: it built one dashboard in
  five in the default space.

### Security

- **`make audit` is clean again.** Four transitive dependencies carried
  published advisories; the lock moves each past every fixed version: anyio
  4.14.1 → 4.15.1 (3 advisories), pyjwt 2.13.0 → 2.15.1 (12), urllib3 2.7.0 →
  2.8.0 (3), virtualenv 21.6.0 → 21.14.5 (4; python-discovery 1.4.4 → 1.6.1
  follows it). None is a direct dependency, so `pyproject.toml` does not change
  for them.

## [0.1.0] - 2026-08-21

### Added

- **Regeneration corpus** (`docs/spec/` + root `AGENTS.md`): the durable
  source of truth for rebuilding the server from documents — charter, design
  doctrine with system invariants, a decisions ledger distilling the live
  probes and recorded choices, per-toolbox behavior contracts, and a
  regeneration brief that ranks what is authoritative when documents and
  code disagree.

- **MCP server for Kibana 9.4+** over a hexagonal (ports-and-adapters) core,
  powered by [kibana-py](https://github.com/pedro-angel/kibana-py). Runs as a
  stdio server for a single local user or a stateless streamable-HTTP server in a
  container for multiple users; each request carries the caller's own Kibana API
  key so Kibana RBAC and audit logging stay per-user.
- **10 composable toolboxes / 133 tools**, enabled per deployment via
  `KIBANA_MCP_TOOLBOXES` (default `dashboards,data-management`):
  `dashboards` (11), `data-management` (10), `alerting` (11), `cases` (6),
  `platform-health` (3), `observability` (10), `security-detections` (25),
  `platform-admin` (10), `streams` (12), and `fleet` (35).
- **Tiered write safety.** Every tool is classified `read` / `write` /
  `destructive` (64 / 40 / 29); `KIBANA_MCP_TIER` caps which tiers are visible,
  and a call to a tool above the cap is rejected server-side, not merely hidden.
- **VizSpec → Lens translation.** A flat declarative chart spec is translated
  server-side into Kibana Lens payloads across 12 chart types, so small local
  models never have to emit deeply-nested Lens JSON.
- **In-band documentation resources** (`docs://user-guide`, `docs://tools`,
  `docs://troubleshooting`) served by the running server.
- **Optional OpenTelemetry** span export per tool call (`otel` extra,
  `KIBANA_MCP_OTEL_ENABLED`, off by default).
- **Container image** published to `ghcr.io/pedro-angel/mcp-for-kibana`.
- **Machine-enforced architecture**: import-linter contracts keep the hexagonal
  boundary; the domain core and ports import no framework or `kibana-py`.
- **Typed public API.** The package is checked with `mypy --strict` and ships a
  `py.typed` marker, so consumers type-check *through* it: a `dict` in a port
  signature is `dict[str, Any]`, not an implicit `Any`. Enforced by the
  `types_clean` gate and by `make lint` / `make check`.
- **Deterministic e2e replay tier** (`make test-e2e-replay`, marker
  `e2e_replay`). A real MCP client replays a *recorded* real-model turn over
  real stdio against a real server and a real Kibana, with no LLM in the loop —
  so the surface a model actually touches is certified on every PR, not only
  where a GPU happens to be. It asserts that the tools still exist under the
  same names, that arguments a real model produced still validate against the
  live input schemas, that arguments the runtime rejected are still rejected,
  and that the server's error guidance still says what lets a model
  self-correct. It does **not** certify that a model can choose those calls;
  that remains the live `e2e` tier's claim alone.
- **Container healthcheck.** The image declares liveness (the venv's own
  interpreter, so no new packages) asserting only that the server answers
  HTTP — deliberately not Kibana connectivity, which would restart-loop a
  healthy container whenever Kibana was unreachable.
- **One-shot Definition-of-Done runs.** `KIBANA_MCP_DOD_CYCLE_STACK=1` lets the
  gate stop the dev stack between the tiers that need it up and the ephemeral
  tiers that need it gone, so `make dod` can reach GO in a single pass.
- **Tooling**: a self-documenting `make` facade, a Definition-of-Done gate
  (`make dod`), and six test tiers (unit, contract, e2e-replay, e2e,
  streams-ephemeral, fleet-ephemeral) against live Kibana 9.4.3.
- **Server-usability report** ([`docs/server-usability.md`](./docs/server-usability.md)):
  twelve local models measured against the live e2e gate, three runs each. Seven
  drive the dashboards path 3/3, including a 3B model — the toolbox design, not one
  model's tool grammar, carries the path. Includes a control arm running the same
  task through the raw Kibana REST API plus its navigable OpenAPI specification:
  0 of 27 attempts produced a valid dashboard, against 8 of 9 through the curated
  tools, using triple the output tokens and triple the wall clock.
  Also measures what the tool surface *costs*: three Claude models driving a real
  coding agent against the same Kibana, with and without this server, 27 runs at
  zero permission denials. On `claude-opus-5` the median run without the server
  took 31 turns, 30,650 output tokens, $1.88 and 447 s; with it, 11 turns, 4,025
  tokens, $0.44 and 66 s. `claude-sonnet-5` shows the same shape. Reported with
  the failures: `claude-haiku-4-5` fails most runs in every arm, and one earlier
  configuration made the server *counter-productive* — a prompt that spelled out
  the curl recipe led the agent to ignore the registered tools entirely.
- **Space-targeted dashboards + data-management tools.** All 21 tools in the
  `dashboards` and `data-management` toolboxes — including the 5
  destructive-tier ones — gain an optional `space` parameter that
  fail-closed targets a Kibana space via kibana-py's
  `client.space(id, validate=True)`; a nonexistent space is rejected with
  guidance before any other work runs, rather than silently landing in an
  invisible orphan namespace. Additive and default-off: a call without
  `space` is byte-identical to before, in behavior and result shape. When
  `space` is set, dict-returning results echo `"space"`, not-found errors
  gain a `(in space '<id>')` suffix, dashboard links carry a `/s/<space>`
  prefix, `create_short_url` rejects a `/s/`-prefixed path, and saved-objects
  export/import can clone or overwrite across spaces. See
  [Tool reference](docs/tools.md#space-targeting)
  and [Configuration](docs/configuration.md#space-targeting).
- **Space targeting extended to alerting, cases, and security-detections.**
  All 42 tools in those three toolboxes gain the same optional `space`
  parameter — 63 space-aware tools in all — with the identical fail-closed
  validation, dict-result `"space"` echo, and `(in space '<id>')` not-found
  suffix, and no gateway or adapter changes (kibana-py scopes every
  namespace they use). Surfaces that stay instance-global under a space are
  documented rather than papered over: `get_alerting_health`'s report,
  preconfigured connectors, and `namespace_type="agnostic"` exception
  lists; value-list backing indices are per-space and auto-created on first
  write. The remaining toolboxes deliberately take no `space` —
  `platform-admin` manages global objects (spaces, roles),
  `platform-health` and `streams` wrap instance/cluster-global state,
  `fleet`'s space awareness is an opt-in migration, and `observability`
  (half space-scoped, half global APM config) is deferred as a follow-up.
  See [Tool reference](docs/tools.md#space-targeting).

### Changed

- **The distribution is now `mcp-for-kibana`** — the `kibana-mcp` name is owned by unrelated projects on both PyPI and npm, and the "MCP for Kibana" form is the referential construction Elastic's trademark guidelines permit. The import package remains `kibana_mcp`; the console script is `mcp-for-kibana`.
- **Supply chain**: the live-stack CI tiers run only for same-repo refs, matching the image job's fork posture; the container image carries OCI source/license labels and CI attests build provenance; a new wheel gate proves an installed package serves the in-band docs without the repo checkout; the weekly autoupdate workflow documents that its bot PR does not trigger CI.
- **Docs truth pass for the public flip**: corrected the default tool count, documented `KIBANA_MCP_EXPORT_DIR`, stated the export-handle statelessness exception and the endpoint-auth posture, replaced instructions that pointed at a PyPI name this project does not own, added Requirements, coordinated-disclosure and untrusted-content sections, and gave the Code of Conduct a genuinely private reporting channel.
- **docs/roadmap.md no longer cites issue numbers** — the public repo starts with an empty tracker.
- **The project no longer references its own build process.** Design specs,
  implementation plans, environment-research probes and retrospectives were
  being published as documentation pages and pointed at from code comments, the
  README, the Makefile and `pyproject.toml`. Every such pointer now states the
  fact it stood for, the artifacts are removed from the tree (git history is the
  archive), and `tests/unit/test_no_dev_artifact_references.py` keeps them out.
- **CI runs the live tiers per pull request**, not only on pushes to `main`, so
  the `dod-live` fan-in can gate merges.
- **The lint gate declares its own rule set.** `[tool.ruff.lint] select` now
  pins `E4, E7, E9, F` rather than inheriting ruff's implicit defaults, which
  change between releases — ruff 0.16 dropped `E402` and added `I`/`B`/`C4`/`S`/
  `RUF`, so a patch-level dependency bump reported 72 findings in unchanged
  code. The gate's meaning is now the project's to change, not the upgrade's.

### Fixed

- **Dashboards correctness**: data-view names/ids now resolve to the index pattern before Lens translation (was: silently empty charts); `search_dashboards` returns every page (was: first 20); `create_dashboard` preserves filters/query/tags/options on an existing dashboard (was: silently wiped); collapsible sections can no longer be deleted as if they were single panels, and appending panels works on dashboards that contain sections; Streams writes returning `acknowledged:false` now raise; `list_installed_packages` walks the full cursor; ambiguous data-view lookups are rejected with the candidate ids.
- **Security hardening before going public**: untranslated exception text is masked (a malformed env key could previously echo into the caller-visible error and the log); the env fallback key is validated; KQL filter values escape backslashes; empty tool identifiers are rejected up front; HTTP mode enables MCP host/origin protection on loopback binds; connectivity errors no longer disclose the internal Kibana endpoint; `set_stream_processing` requires `confirm=True` to clear all steps; the container healthcheck honours `KIBANA_MCP_PORT`; base images are digest-pinned.
- **LICENSE named the wrong copyright holder** (a copy-paste from the kibana-py sibling) and had a one-word deviation from the canonical Apache-2.0 text; NOTICE now also covers the Elastic-licensed images the dev stack pulls.
- **Package author email was a non-deliverable placeholder** (`noreply@users.noreply.github.com`); now the maintainer's allocated GitHub noreply address, so PyPI metadata carries a real (still private) contact.
- **`KibanaPyGateway` now satisfies its own `KibanaGateway` protocol.**
  `create_agent_policy` required three arguments the port declares optional, so
  a consumer coding against the published port would have hit a `TypeError`.
  Surfaced by the new type gate. No call site changed — every in-repo caller
  already passed them by keyword.
- **Dependency vulnerabilities**: `aiohttp` 3.14.1 → 3.14.3 (PYSEC-2026-3545,
  -3546, -3547) and `cryptography` 49.0.0 → 50.0.0 (PYSEC-2026-3552).
- **Base images are watched.** Dependabot covers the `docker` ecosystem, so the
  image's own OS layer receives security updates rather than only its Python
  dependencies.
- **The e2e harness no longer pins `context_length`.** It sent `8000`, which LM
  Studio treats as a *load* parameter: a value differing from the loaded instance
  spawns a second model instance at that context rather than reusing the
  operator's. Omitting it reuses the loaded model and hands it the operator's full
  context. `nvidia/nemotron-3-nano-omni` went from 0/3 to 3/3 on this change alone
  — at 8000 it spent its budget reasoning and was truncated mid-tool-call.
- **LM Studio load failures now report as environment failures.** A model the
  machine has no memory for was never exercised, yet surfaced as an opaque
  `HTTPStatusError: 400`. The gate now names the cause and points at `lms ps` and
  `lms unload <identifier>`. It fails rather than skips, so the skip-green hole
  guarded in `tests/_stack_env.py` is not reopened here.

[Unreleased]: https://github.com/pedro-angel/mcp-for-kibana/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/pedro-angel/mcp-for-kibana/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/pedro-angel/mcp-for-kibana/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/pedro-angel/mcp-for-kibana/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/pedro-angel/mcp-for-kibana/releases/tag/v0.1.0
