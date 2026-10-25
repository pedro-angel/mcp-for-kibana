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

### Owner decisions needed before the batch that needs them

| Batch | Decision | Default if the owner says "your call" |
|---|---|---|
| H8 | Surface budget numbers (default selection, profiles, per-toolbox, per-tool description/schema caps) | Today's values + ~10 % headroom, each line citing its decision |
| H9 | Widen the live-model tier: new pre-registered tasks (amends `scripts/experiment/CLAUDE_E2E.md`) and one destructive-tier precision task (supersedes the write-only setup) | Replay transcripts for all toolboxes (no pre-registration change) now; new live tasks as a registered amendment with a new decision entry |
| H10 | How to get a Fleet stack with space awareness OFF if 9.5.4 has no switch (8.19 → 9.5.4 upgrade without migration, snapshot) | Probe first; if only the upgrade path works and costs > ~10 min per run, keep it CI-only and record that |
| PR #29 leftovers | `uninstall_package` under awareness; default selection at 27 | Not part of this plan unless the owner folds them in |

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
| H1 | [ ] | environment-2 | Fail-closed **stack-version check** before the seeded-stack criteria: Kibana version from `/api/status` (seed key) must equal the `ES_LOCAL_VERSION` pin (local) or the matrix version (CI); NO-GO names `scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed`; version printed on every seeded-stack GO line. Fix `WRAP_UP.md`'s "start the stack at 9.5.5" and say in docs that run records at 9.5.5 are outside the supported set | S |
| H2 | [ ] | integrity-1 | `changelog_entry` diff-based: changed paths since `git merge-base HEAD origin/main` (fallback: latest `v*` tag) outside an exemption list require an added line under `## [Unreleased]`; fail closed without `origin/main` naming `git fetch origin main`; same check as a `checks.yml` step on PRs. Fix the `dod.config` comment to match | S |
| H3 | [ ] | integrity-2 | Verdict bound to a tree: content tree id (temporary index, `git add -A && git write-tree`) at start and end, HEAD and dirty flag in the header and VERDICT line, NO-GO if the tree changed mid-run, `$logdir/verdict.json` (sha, tree id, config sha256, criteria, Kibana version, Claude model, timestamps), `$logdir` cleared at start. AGENTS.md: how to check a GO claim (`git rev-parse HEAD^{tree}` == recorded tree id) | S |
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

## Log

- 2026-10-25: plan written from the DoD adequacy review (18 verified findings); H0 found while resuming (dockerd blocked by a stale containerd pidfile; fixed by hand in this session).
- 2026-10-25: H0 done — hook removes the stale containerd pidfile too; reproduced with a live unrelated PID planted in `/run/docker/containerd/containerd.pid`: old hook "dockerd did not come up", new hook "started dockerd".
