#!/bin/sh
# Definition-of-Done gate: tooling-certified GO/NO-GO over the criteria declared in
# dod.config. The author (human or agent) never self-certifies "done" — this script
# does. Run it before claiming completion and ALWAYS before a release.
#
# Criteria marked `required` must pass; `n/a` are skipped (declare, don't delete, so
# the omission is a visible decision). Full logs land in /tmp/dod-<repo>/<criterion>.log
# (per-repo so sibling gates don't clobber each other's diagnostics).
# contract/e2e need local infrastructure: scripts/stack.sh up && seed (at the pinned
# Kibana version: the gate refuses another line, D53), plus a signed-in
# `claude` CLI for e2e_green (D42, docs/e2e-setup.md). CI runs the fast criteria on every
# PR (checks.yml) and the live tiers minus e2e_green through this same gate over per-tier
# configs in .github/dod/ (integration.yml); this gate over dod.config remains the full
# superset a human or a cloud session runs where all the infrastructure lives.
#
# One-shot GO on a machine with the dev stack up:
#   scripts/stack.sh up && scripts/stack.sh seed
#   KIBANA_MCP_DOD_CYCLE_STACK=1 make dod
# The flag lets the gate stop the dev stack between the suites that need it and
# the ephemeral tiers that need it gone (see the block above streams_ephemeral).
# Without it the run is honest but two-pass: fleet_ephemeral NO-GOs while the dev
# stack is up.
#
# Portable POSIX sh; zero deps beyond the project's own tooling (uv).
# Concept adapted from cmanaha/extended-superpowers (MIT).
set -u

# `make dod` runs this gate; a leaked MAKEFLAGS/MFLAGS from a parent make
# (`make -i dod`, `-k`, `-j` jobserver) can weaken sub-processes and flip the
# gate to a false GO. This repo's criteria call `uv`/`pytest` directly, so this
# is defense-in-depth (only the outer `make dod` wrapper leaks) — cleared so the
# gate always runs in a clean environment, matching the sibling kibana-py gate.
unset MAKEFLAGS MFLAGS

cd "$(git rev-parse --show-toplevel)" || exit 2
cfg="${1:-dod.config}"
[ -f "$cfg" ] || { echo "FAIL: no DoD config found at: $cfg"; exit 2; }

# Fail closed on config typos AND omissions: every KNOWN criterion must appear
# exactly once as `<name> = required` or `<name> = n/a`. A misspelled name/value
# (e.g. `requird`, a `==` typo), an unknown criterion, or a *missing* known row
# each exit 2 — never a silent skip that could still read GO (`declare, don't
# delete`). The value is anchored to the FIRST `=` so it matches `req()`'s
# single-`=` grammar below. Keep `known` in sync with the criteria dispatched.
known="unit_green ruff_clean types_clean import_contracts hygiene_hooks docs_strict \
audit_clean sast_clean vocabulary_conformant image_smoke contract_green e2e_replay_green wheel_replay_green e2e_green streams_ephemeral fleet_ephemeral changelog_entry"
seen=" "
while IFS= read -r line || [ -n "$line" ]; do            # tolerate a missing final newline
  line=$(printf '%s' "$line" | tr -d '\r')               # CRLF-safe
  trimmed=$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  case "$trimmed" in ''|\#*) continue ;; esac            # blank / whitespace-only / comment
  crit=$(printf '%s' "$trimmed" | sed 's/[[:space:]]*=.*//')
  val=$(printf '%s' "$trimmed" | sed 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*#.*$//; s/[[:space:]]*$//')
  case " $known " in *" $crit "*) : ;; *) echo "FAIL: unknown criterion in $cfg: '$crit'"; exit 2 ;; esac
  case "$val" in required|n/a) : ;; *) echo "FAIL: criterion '$crit' has invalid value '$val' (use: required | n/a)"; exit 2 ;; esac
  seen="$seen$crit "
done <"$cfg"
for k in $known; do
  case "$seen" in *" $k "*) : ;; *) echo "FAIL: criterion '$k' missing from $cfg (declare it as required or n/a — don't delete)"; exit 2 ;; esac
done

req()    { grep -qE "^$1[[:space:]]*=[[:space:]]*required([[:space:]]|$)" "$cfg"; }
nogo=0
logdir="/tmp/dod-$(basename "$(pwd)")"   # namespaced by repo dir-name so a sibling repo (kibana-py) doesn't clobber
mkdir -p "$logdir" || { echo "FAIL: cannot create log dir $logdir"; exit 2; }
run() {
  name="$1"; shift
  if "$@" >"$logdir/$name.log" 2>&1; then
    echo "  GO    $name"
  else
    echo "  NO-GO $name  (log: $logdir/$name.log)"
    nogo=1
  fi
}

# Suite criteria (unit/contract/e2e) invoke pytest directly, so exit 0 is not
# enough: a fully-skipped or empty suite also exits 0 and would read a false GO.
# Assert passed>=1 AND skipped=0 from the pytest summary so a green-but-empty or
# all-skipped run reads NO-GO. contract/e2e only reach here past the seeded-stack
# guard (seeded_stack below), so a skip there means a claimed stack never
# exercised the suite. (No kibana-py analog — its suites hide behind `make test`
# and check exit code only.)
suite_note=  # optional detail on a suite's GO line; set only around one run_suite
run_suite() {
  name="$1"; shift
  out="$logdir/$name.log"
  if ! "$@" >"$out" 2>&1; then
    echo "  NO-GO $name  (log: $out)"; nogo=1; return
  fi
  summary=$(grep -E '[0-9]+ (passed|skipped|failed|error)|no tests ran' "$out" | tail -1)
  passed=$(printf '%s\n' "$summary" | grep -oE '[0-9]+ passed'  | grep -oE '^[0-9]+')
  skipped=$(printf '%s\n' "$summary" | grep -oE '[0-9]+ skipped' | grep -oE '^[0-9]+')
  : "${passed:=0}" "${skipped:=0}"
  if [ "$passed" -ge 1 ] && [ "$skipped" -eq 0 ]; then
    echo "  GO    $name${suite_note:+  ($suite_note)}"
  else
    echo "  NO-GO $name  (passed=$passed skipped=$skipped: empty or all-skipped suite reads NO-GO; log: $out)"
    nogo=1
  fi
}

# Seeded-stack precondition (D53), shared by contract_green, e2e_replay_green,
# wheel_replay_green and e2e_green: .env.seed exists AND the Kibana it points at
# runs the version this gate certifies — the elastic-start-local/.env.example pin
# (the newest supported version, D30) locally, the matrix's ES_LOCAL_VERSION in
# CI. tests/_stack_version.py reads /api/status with the seed key (about 1 s,
# once per gate run); a mismatch, an unreachable stack or a missing seed is NO-GO
# on every seeded-stack criterion with one line naming the fix, and each GO line
# names the version it certified.
stack_state=        # '' unchecked | ok | bad
stack_version=
stack_reason=
seeded_stack() {
  if [ -z "$stack_state" ]; then
    if [ ! -f elastic-start-local/.env.seed ]; then
      stack_state=bad
      stack_reason="no elastic-start-local/.env.seed — run: scripts/stack.sh up && scripts/stack.sh seed"
    elif stack_version=$(uv run python -m tests._stack_version 2>"$logdir/stack_version.log"); then
      stack_state=ok
    else
      stack_state=bad
      stack_reason=${stack_version:-"the stack-version check crashed; log: $logdir/stack_version.log"}
      stack_version=
    fi
  fi
  [ "$stack_state" = ok ]
}
stack_nogo() { echo "  NO-GO $1  ($stack_reason)"; nogo=1; }

echo "Definition-of-Done gate ($cfg)"

if req unit_green;       then run_suite unit_green uv run pytest -q --cov=kibana_mcp --cov-report=term-missing; fi
if req ruff_clean;       then run ruff_clean       uv run ruff check; fi
if req types_clean;      then run types_clean      uv run mypy; fi
if req import_contracts; then run import_contracts uv run lint-imports; fi
if req hygiene_hooks;    then run hygiene_hooks    uv run pre-commit run --all-files; fi
if req docs_strict;      then run docs_strict      uv run --group docs mkdocs build --strict; fi
if req audit_clean;      then run audit_clean      uv run pip-audit; fi
if req sast_clean;       then run sast_clean       uv run bandit -r src/ -ll -q; fi
# The checker and its manifest are consumed as a pinned pre-commit hook, not
# vendored, so no path is passed: the hook resolves the manifest that ships
# beside it. hygiene_hooks runs the whole set; this names the one criterion the
# gate reports separately, so a vocabulary failure is legible in the verdict.
if req vocabulary_conformant; then run vocabulary_conformant uv run pre-commit run vocabulary-conformance --all-files; fi

if req image_smoke; then
  # Ship artifact: build then smoke-test the Docker image. The two commands are
  # the exact recipe lines of `make build` (mirror, not a nested make — keeps the
  # gate self-contained and the MAKEFLAGS clearing above meaningful). Needs docker
  # but no claimed stack (.env.seed), so it runs before the live-stack suites.
  # Not a pytest suite -> plain `run` (exit-code), not run_suite.
  run image_smoke sh -c 'scripts/checks/image-build.sh mcp-for-kibana:dev && scripts/checks/image-smoke.sh mcp-for-kibana:dev'
fi

if req contract_green; then
  if seeded_stack; then
    suite_note="Kibana $stack_version"
    run_suite contract_green uv run pytest -m contract -q
    suite_note=
  else
    stack_nogo contract_green
  fi
fi

if req e2e_replay_green; then
  # The MCP surface a model actually sees, exercised with NO model in the loop:
  # a recorded real-model turn is replayed through a real MCP client over real
  # stdio against the real server and real Kibana. Deterministic, so unlike
  # e2e_green it runs in CI. Needs a seeded stack, same precondition as contract.
  if seeded_stack; then
    suite_note="Kibana $stack_version"
    run_suite e2e_replay_green uv run pytest -m e2e_replay -q
    suite_note=
  else
    stack_nogo e2e_replay_green
  fi
fi

if req wheel_replay_green; then
  # The same replay, with the server launched from the built wheel installed
  # into a fresh venv: the artifact a PyPI user installs, its dependencies
  # resolved the way that user's install resolves them. Every other live tier
  # runs the checkout. Same seeded-stack precondition as e2e_replay_green.
  if seeded_stack; then
    suite_note="Kibana $stack_version"
    run_suite wheel_replay_green scripts/checks/wheel-replay.sh
    suite_note=
  else
    stack_nogo wheel_replay_green
  fi
fi

if req e2e_green; then
  # The live-model criterion (D42): headless Claude Code (`claude -p`) with only
  # this server's tools runs the pre-registered tasks (scripts/experiment/
  # CLAUDE_E2E.md; four tasks, CLAUDE_E2E_RUNS each, default 5) and Kibana's
  # state is scored afterwards. The model is CLAUDE_E2E_MODEL (default the
  # `haiku` alias, D38). This is `make test-e2e`'s pytest selection, mirrored
  # rather than nested (like image_smoke) and without its stack up+seed: the
  # .env.seed precondition below claims a seeded stack, as for contract_green.
  # A missing CLI or sign-in is NO-GO naming the fix — never a skip or a pass.
  # Run records go to this gate's log dir, never into the tracked
  # scripts/experiment/runs/ (committed blocks are written on purpose, with
  # `make test-e2e` and an explicit CLAUDE_E2E_OUT).
  if ! command -v claude >/dev/null 2>&1; then
    echo "  NO-GO e2e_green  (no \`claude\` CLI on PATH — install Claude Code (npm install -g @anthropic-ai/claude-code), then sign in: claude auth login; docs/e2e-setup.md)"
    nogo=1
  elif ! claude auth status 2>/dev/null | grep -qE '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
    echo "  NO-GO e2e_green  (the \`claude\` CLI is not signed in — run: claude auth login, then check with: claude auth status; docs/e2e-setup.md)"
    nogo=1
  elif ! seeded_stack; then
    stack_nogo e2e_green
  else
    # Env loading lives in tests/_stack_env.py (fixture-time). A fresh records
    # dir per gate run, so its runs.jsonl holds only this run's records.
    rm -rf "$logdir/e2e_green-runs"
    # The model and runs per task come from CLAUDE_E2E_MODEL / CLAUDE_E2E_RUNS
    # when set, so the GO line names them: a GO from an overridden model or a
    # single run per task is visible, not silent. Resolved by the harness's own
    # defaults (tests/_claude_cli.py), never restated here.
    suite_note=$(uv run python -c 'from tests import _claude_cli as c; print(f"model {c.claude_model()}, {c.runs_per_task()} runs per task")' 2>/dev/null) \
      || suite_note="model and runs per task unresolved"
    suite_note="Kibana $stack_version, $suite_note"
    run_suite e2e_green env CLAUDE_E2E_OUT="$logdir/e2e_green-runs" \
      uv run pytest -m e2e_claude -q
    suite_note=
  fi
fi

# The two ephemeral tiers below need the shared dev stack DOWN: their harnesses
# stand up isolated stacks, and the Docker VM cannot hold both (measured
# 2026-08-09: dev stack 4.9GB of a 11.7GB VM, and the VM is shared with whatever
# else the developer is running). But contract/e2e/e2e_replay above need that
# same dev stack UP — so a single `make dod` cannot satisfy both halves and the
# gate could never reach GO in one pass.
#
# Opt-in fixes that, default-off so the gate never touches infrastructure it was
# not explicitly told it may. With KIBANA_MCP_DOD_CYCLE_STACK=1 the gate stops
# the dev stack HERE, between the two halves, once the suites that need it are
# done. `stop` (not `down`) is deliberate: volumes and the seeded API key in
# elastic-start-local/.env.seed survive, so `make stack-start` brings the same
# stack back. Without the flag, behaviour is unchanged — fleet_ephemeral's own
# guard reports NO-GO with an actionable message, which is the honest outcome
# rather than a silent skip.
if [ "${KIBANA_MCP_DOD_CYCLE_STACK:-0}" = "1" ] && { req streams_ephemeral || req fleet_ephemeral; }; then
  if [ -n "$(docker compose -p mcp-for-kibana-stack ps -q 2>/dev/null)" ]; then
    echo "  ...   stopping the dev stack for the ephemeral tiers (KIBANA_MCP_DOD_CYCLE_STACK=1)"
    if ! scripts/stack.sh stop >"$logdir/cycle_stack.log" 2>&1; then
      echo "  NO-GO cycle_stack  (could not stop the dev stack; log: $logdir/cycle_stack.log)"
      nogo=1
    fi
  fi
fi

if req streams_ephemeral; then
  # The harness owns its own isolated stack lifecycle (up -> pytest -m ephemeral
  # -> down), so unlike contract/e2e it has no .env.seed precondition. run_suite
  # parses the wrapped pytest summary so an all-skipped run can't read a false GO.
  run_suite streams_ephemeral scripts/ephemeral_stack.sh
fi

if req fleet_ephemeral; then
  # Same contract as streams_ephemeral: the harness owns its own isolated 2-agent
  # fleet stack lifecycle (up -> pytest -m fleet_ephemeral -> down), so no .env.seed
  # precondition. run_suite parses the wrapped pytest summary so an all-skipped run
  # can't read a false GO.
  run_suite fleet_ephemeral scripts/fleet_ephemeral.sh
fi

if req changelog_entry; then
  # Diff-based (D54): every path changed since `git merge-base HEAD origin/main`
  # (fallback: the latest v* tag), untracked files included, outside a short
  # exemption list needs a line added under `## [Unreleased]`. Without a base the
  # check fails closed naming `git fetch origin main`. checks.yml runs the same
  # script on every PR, so the rule is not only local.
  if changelog_reason=$(scripts/checks/changelog-entry.sh 2>"$logdir/changelog_entry.log"); then
    echo "  GO    changelog_entry  ($changelog_reason)"
  else
    echo "  NO-GO changelog_entry  (${changelog_reason:-the changelog check crashed; log: $logdir/changelog_entry.log})"
    nogo=1
  fi
fi

if [ "$nogo" -eq 0 ]; then
  echo "VERDICT: GO"
else
  echo "VERDICT: NO-GO (fix the criteria above, or mark them n/a in $cfg as a visible decision)"
  exit 1
fi
