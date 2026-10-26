# Plan — DoD hardening (from the 2026-10-25 DoD adequacy review)

Working document for a multi-session build. **Delete this file, its findings
appendix and the batch workflow in the last commit before the PR.**

Source: read-only review of the Definition-of-Done gate from 6 lenses plus an
adversarial verifier (workflow `wf_c8a827c8-6b9` at `426e724`). 29 findings, 18
survived; evidence, scenario, proposal and cost for each are in
[dod-hardening-findings.md](dod-hardening-findings.md) — read the finding
before building its batch, and re-verify it against the current tree first (the
review ran before PR #29 merged).

## Goal and owner decisions (2026-10-25)

- Implement **all 18 verified findings** in the order below: cheap,
  high-value gate fixes first, then privilege and secret coverage, then the
  expensive tiers.
- **The DoD runs on the newest Kibana version the server supports** (owner,
  2026-10-25; already D30: kibana-py's `SUPPORTED_VERSIONS`, today 9.5.4). H1
  makes the gate *enforce* it on the running stack, not only on the pin.
  Kibana 9.5.5 exists in Elastic's registry but no kibana-py release supports
  it yet: moving to it needs a kibana-py release first (out of scope here).
- Every new check is a `required` DoD criterion or lives inside an existing
  one (`unit_green`, `contract_green`, …); CI configs (`.github/dod/*.config`)
  mark it `required` where CI can run it, `n/a` with a comment where not.
- Error text of every new check names the fix (house rule).
- Branch: `claude/ops-admin-tools`, restarted from `main` after PR #29 merged
  (session branch rule; same name).

### Owner decisions (answered on the whiteboard, 2026-10-25)

| Batch | Owner's answer | What the batch builds |
|---|---|---|
| H8 | A (caps), with a rule: "the growth should be pondered, not just adding because of new tools" | Caps at today's values + headroom; raising a cap needs a decision entry saying why the growth is worth it — a new tool alone is not a reason |
| H9 | Replays for **all** toolboxes (cheap, every gate run and CI); live-model tasks **on demand** | One recorded replay per unmodelled toolbox; new live tasks only in `make test-e2e`, not in the gate. **Open follow-up** (whiteboard): does the existing live tier (`e2e_green`, D42) also leave the gate? Options A keep / B on demand / C only when the diff touches tool names, descriptions or schemas (recommended). Ask before H9 |
| H10 | A (find a 9.5.4 switch); if none, C (unit tests only). No 8.19 upgrade path | Probe; if no switch, record the evidence in the roadmap and keep the off branches unit-tested. Stays on 9.5.4: 9.5.5 needs a kibana-py release first (0.6.0 is the newest on PyPI and lists 9.5.4, 9.4.7) |
| PR #29 | `uninstall_package`: keep as shipped | No change |

## Done criteria (PR gate)

1. `KIBANA_MCP_DOD_CYCLE_STACK=1 make dod` → `VERDICT: GO` on the final tree,
   **with the new criteria included** and the stack-version check reading the
   newest supported version.
2. `make test-e2e` green (H9 changes the live tier).
3. CI green on the PR head (`checks.yml`, `integration.yml`).
4. Each new check proven to bite: for every finding, a commit-message line
   showing the check failing on a reproduced instance of the defect class
   (mutation or a temporary revert) and passing after.
5. This file, the findings appendix and the batch workflow deleted.

## Batches (in order — they share `definition-of-done.sh`, `dod.config`, docs)

Status: `[ ]` todo · `[~]` in progress · `[x]` committed + pushed (add sha).

| # | Status | Findings | What | Size |
|---|---|---|---|---|
| H0 | [x] | (session infra) | `scripts/cloud-session-start.sh` also removes a stale `/run/docker/containerd/containerd.pid` (same stale-PID class it already handles for `/var/run/docker.pid`; dockerd failed to start on resume 2026-10-25 with "process with PID 218 is still running") | S |
| H1 | [x] `3b0c516` | environment-2 | Fail-closed **stack-version check** before the seeded-stack criteria: Kibana version from `/api/status` (seed key) must equal the `ES_LOCAL_VERSION` pin (local) or the matrix version (CI); NO-GO names `scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed`; version printed on every seeded-stack GO line. Fix `WRAP_UP.md`'s "start the stack at 9.5.5" and say in docs that run records at 9.5.5 are outside the supported set | S |
| H2 | [x] `448b0ce` + `69a7ecd` | integrity-1 | `changelog_entry` diff-based: changed paths since `git merge-base HEAD origin/main` (fallback: latest `v*` tag) outside an exemption list require an added line under `## [Unreleased]`; fail closed without `origin/main` naming `git fetch origin main`; same check as a `checks.yml` step on PRs. Fix the `dod.config` comment to match | S |
| H3 | [x] `15a6ab5` | integrity-2 | Verdict bound to a tree: content tree id (temporary index, `git add -A && git write-tree`) at start and end, HEAD and dirty flag in the header and VERDICT line, NO-GO if the tree changed mid-run, `$logdir/verdict.json` (sha, tree id, config sha256, criteria, Kibana version, Claude model, timestamps), `$logdir` cleared at start. AGENTS.md: how to check a GO claim (`git rev-parse HEAD^{tree}` == recorded tree id) | S |
| H4 | [ ] | integrity-3 | Unset `PYTEST_ADDOPTS`/`PYTEST_PLUGINS`/`PYTEST_DEBUG` with `MAKEFLAGS`; parse collected/deselected and print `N passed / M collected`; NO-GO when shell `KIBANA_URL`/`KIBANA_TEST_API_KEY` differ from `.env.seed`. Also document what `KIBANA_MCP_DOD_CYCLE_STACK` does (stops the stack between halves, does not start or restart it) in AGENTS.md and the gate header — the verifier session tripped on it | S |
| H5 | [ ] | docs-1, docs-2, docs-6 | `tests/unit/test_docs_counts.py` (totals, per tier, per toolbox, space-aware, edges, list sizes computed from the registry; scanned patterns in README, docs, specs, profiles; short allowlist for historical figures) — fix the stale `86` in alerting/cases/data-management specs; extend `test_docs_tool_reference.py` (one row per tool in its own toolbox section, tier cell = registry tier, annotation tables = real hints, section counts); test that every test name/path cited in docs resolves (fix `test_read_only_explorer_profile_is_nine_read_tools` in platform-health.md) | M |
| H6 | [ ] | privilege-1, privilege-2 | Shared `restricted_key(grants)` fixture in `tests/contract/conftest.py` (role + user + self-minted key, no ES admin needed); replace both ad hoc fixtures; `tests/contract/test_privilege_matrix.py` over ≥3 keys (default-space-only `all`; `read` in every space; no feature privileges) × every privilege-dependent tool (`list_spaces`, every `space`/`target_spaces`/`spaces`/`source_space` tool, `uninstall_package`) asserting fail-closed results and fix-naming errors; code fix for privilege-2: a target space that exists but the key cannot see is reported as a privilege problem (`spaces.get` 403 vs 404), not "create it"; `list_spaces` description says "spaces this key can view"; D49 + platform-admin spec reconciled | M |
| H7 | [ ] | privilege-4 | Cross-cutting **secret scan**: autouse hook around the contract tool-server clients and the replay client scanning every tool result for secret-bearing keys and the literal test key; `scripts/stack.sh seed` adds a canary Fleet output (dummy `service_token`) and a canary connector; denylist starts from the seeded canaries | S–M |
| H8 | [ ] | surface-3, usability-2, usability-3 | Contract **tool-layer sweep**: full server at destructive against the seeded stack, `call_tool` on every read tool once (ids from the matching list tool), no `isError`, valid structured output, coverage asserted against every read-tagged tool; ephemeral suites drive tools through a FastMCP Client (confirm refusals first). **Surface budget** table + `tests/unit/test_surface_budget.py` (owner numbers). **Dependency completeness** test: every id-taking tool has an edge or a reasoned exemption, and its description names a source tool; coherence check over every toolbox × tier and every shipped profile | M–L |
| H9 | [ ] | environment-3, environment-4, surface-1, surface-2 | `image_replay_green`: the built image over HTTP with `Authorization: ApiKey` replays the e2e transcripts against the seeded stack (+ one negative call without the header); ephemeral scripts honour `ES_LOCAL_VERSION` and CI runs them on every supported line; **model coverage**: one recorded replay transcript per unmodelled toolbox (cases, security-detections, observability, streams, fleet, platform-health), then (owner decision) new pre-registered live tasks and one destructive precision task | L |
| H10 | [ ] | environment-1 | Fleet **space awareness OFF** tier: probe for a 9.5.4 switch; else 8.19 → 9.5.4 upgrade without the migration, snapshotted; point the fleet contract tests at it with the anti-skip rule so the off branches (`test_fleet_contract.py` uninstall, guards, monitoring) are mandatory there; new criterion `fleet_unaware_ephemeral` (local) and a CI job. If no practical way exists, record the evidence in the roadmap and keep the off-branches covered by unit tests only (owner told) | L |
| H11 | [ ] | final | Full DoD with all new criteria, `make test-e2e`, docs/spec reconciliation (design.md invariants, decisions D53+, CHANGELOG), push, CI green, delete plan files, PR | — |

## Per-batch definition of done (each batch = one commit, plus review-fix commits)

1. **Re-verify the finding** on the current tree (the evidence may have moved).
2. **Prove the check bites**: reproduce the defect class (mutation, temporary
   revert, a doctored fixture), show the new check NO-GO/failing with a message
   that names the fix, then show it passing on the real tree. Paste both lines
   in the commit message.
3. **TDD** where it is code (unit tests for helpers; shell logic kept small and
   exercised by a unit test that runs the gate function on fixtures where
   practical).
4. **Specs reconciled in the same commit**: AGENTS.md (gate rules), `dod.config`
   and `.github/dod/*.config` comments, `docs/spec/design.md` (invariants),
   a new decision `D53+` per batch that adds or changes a criterion,
   `docs/roadmap.md`, `CHANGELOG.md` under `[Unreleased]` (Changed/Added for
   the gate).
5. **Gates before commit**: `make check` exit 0, `make test-contract`,
   `make test-e2e-replay`; the full `make dod` at least after H1–H4 (the gate
   itself changes there) and at H11.
6. Conventional Commits, Why/Change/Evidence/Gates body, the session trailers;
   never `--no-verify`; push after every batch; update this table (status + sha).
7. Shared stack hygiene: restricted roles/users/keys and canaries are created
   and removed by the tests themselves; nothing is left behind.

## How to run a batch

Reuse the ops-admin batch workflow shape (implement → adversarial review → fix,
minor findings fixed too, a final report-only review): copy it to
`.claude/workflows/dod-hardening-batch.js`, point `PLAN` at this file, and run
`Workflow({scriptPath: ".claude/workflows/dod-hardening-batch.js", args: {batch: "H<n>"}})`.
One writer at a time on the branch. Re-run `make check` yourself before marking
a batch `[x]`.

## Child sessions (cloud env `mcp-for-kibana-authorized`)

Owner-authorized (2026-10-24, restated 2026-10-25): each batch runs in a child
session there, without permission prompts. `environment_id` =
`env_01Q9Nd8PNwPhKgVNBAd468jM` (`create_session`, `source_url` = repo,
`source_revision` = `outcome_branch` = `claude/ops-admin-tools`;
`permission_mode` omitted so it inherits `auto` — never `plan`).

- **One writer at a time**: the child owns its batch end to end (runs the batch
  workflow or the same implement → review → fix loop, commits, pushes); the
  parent does not push while a child is open. Never two batches in parallel.
- **Parent stays small**: it launches, checks, reads verdict lines and commit
  messages — not diffs — runs `make check` on the fetched head and marks the
  batch `[x]` here and on the whiteboard.
- A child that finishes cleanly does not report back: check it with
  `get_session` (`status_bucket`) and `list_events` (kinds `assistant`,
  `result`), then `git fetch origin claude/ops-admin-tools`.
- A child that hits an owner decision stops and reports "blocked"; the parent
  puts the question on the whiteboard and waits.
- **Independent final check (H11)**: a fresh child on a clean clone of the
  final head runs `scripts/stack.sh up && scripts/stack.sh seed`, then
  `KIBANA_MCP_DOD_CYCLE_STACK=1 make dod` and `make test-e2e`, and reports the
  verdict lines.

## Resuming

1. `git fetch origin claude/ops-admin-tools && git status -sb` — the branch
   head and this table agree, or the table is fixed first.
2. Check for an open child (`list_sessions`, title `DoD hardening H<n>`);
   never start the next batch while one is working.
3. Status and owner decisions live on the whiteboard: Claude Docs
   "MCP-for-Kibana-live", tab "DoD hardening"
   (https://claude.ai/artifact/7ibdLQpA5WSVuLwqx5cBSE). Read it before writing.

## Log

- 2026-10-25: plan written from the DoD adequacy review (18 verified findings); H0 found while resuming (dockerd blocked by a stale containerd pidfile; fixed by hand in this session).
- 2026-10-25: H0 done — hook removes the stale containerd pidfile too; reproduced with a live unrelated PID planted in `/run/docker/containerd/containerd.pid`: old hook "dockerd did not come up", new hook "started dockerd".
- 2026-10-25: H1 done — seeded-stack criteria run behind a stack-version check (`tests/_stack_version.py`, D53; inside the criteria, no new row); reproduced: old gate `GO e2e_replay_green` with the pin doctored to 9.4.7, new gate NO-GO naming `scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed` (doctored pin, fake 9.5.5 `/api/status`, CI matrix 9.4.7), GO `(Kibana 9.5.4)` on the real tree; cloud-setup fallback now pinned by `test_stack_pin.py`. Review (0 blocking, 3 minor) fixed: a missing `version.number` names the key and `scripts/stack.sh seed`; shell `KIBANA_URL`/`KIBANA_TEST_API_KEY` overrides are named in the fix; an `ES_LOCAL_VERSION` equal to the target is not.
- 2026-10-25: owner answered H8/H9/H10/uninstall_package on the whiteboard (table above); `make check` exit 0 on the parent at c08f49c. H1 note: the workflow's implement agent returned before its DoD finished, so the workflow review did not run; the child ran one review agent itself (0 blocking, 3 minor, fixed).
- 2026-10-25: H2 done — `changelog_entry` runs `scripts/checks/changelog-entry.sh` (D54; diff vs merge-base with origin/main, fallback latest v* tag, untracked included; exempt runs/, docs/plans/, uv.lock at first); also a checks.yml PR step (Dependabot skipped). Reproduced on origin/main + a src change with no entry: old gate `GO changelog_entry`, new gate NO-GO naming `add an entry under ## [Unreleased] in CHANGELOG.md`; a clone without origin/main NO-GO naming `git fetch origin main`; GO on the real tree. The implement agent held the push (it read the environment-wide agent instructions as forbidding it) and returned blocked, so the workflow review did not run again; the child ran one review agent itself (1 blocking, 7 minor, all fixed in the follow-up commit: `uv.lock` no longer exempt, `.claude/plans/` exempt instead of the non-existent `docs/plans/`, net-gain/new-text entry rule so amended headings, edited/moved lines and empty sub-headings do not count, `--no-renames`, quoted non-ASCII untracked paths, git failures NO-GO); 8 new unit tests fail on 448b0ce's script and pass now.
- 2026-10-26: H3 done — the verdict is bound to a tree (D55; `tree_stable` is part of the gate, no config row): header and VERDICT line name HEAD, the content tree id (temporary index, `git add -A && git write-tree`) and clean/dirty; NO-GO when the tree changed mid-run; `/tmp/dod-<repo>/` cleared at start, `verdict.json` written there; AGENTS.md says how to check a GO claim. Reproduced on a fixture whose only criterion writes a file: old gate `VERDICT: GO`, new gate `NO-GO tree_stable` naming `commit or stash ... re-run make dod`; 8 unit tests in `tests/unit/test_dod_verdict.py`.
- 2026-10-26: H3 review fixes — the content tree id missed two kinds of edit, both reproduced on fixtures and now unit-tested (`tests/unit/test_dod_verdict.py`, 12 tests, the 3 new ones fail on the old gate): a racily clean same-size edit (plain `cp` gave the temporary index a fresh mtime, hiding it from git's racy-clean check; now `cp -p`) and an edit to a file marked skip-worktree or assume-unchanged (`git add -A` never rehashes those; the temporary index now drops both bits on present files, keeping them on absent sparse-checkout entries). D55, AGENTS.md and the CHANGELOG entry say so.
