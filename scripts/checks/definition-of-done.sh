#!/bin/sh
# Definition-of-Done gate: tooling-certified GO/NO-GO over the criteria declared in
# dod.config. The author (human or agent) never self-certifies "done" — this script
# does. Run it before claiming completion and ALWAYS before a release.
#
# Criteria marked `required` must pass; `n/a` are skipped (declare, don't delete, so
# the omission is a visible decision). Full logs land in /tmp/dod-<repo>/<criterion>.log
# (per-repo so sibling gates don't clobber each other's diagnostics).
# contract/e2e need local infrastructure: scripts/stack.sh up && seed, plus LM Studio
# for e2e (docs/e2e-setup.md). CI runs the fast criteria on every PR (checks.yml) and
# the live tiers minus e2e through this same gate over per-tier configs in .github/dod/
# (integration.yml); this gate over dod.config remains the full superset a human runs
# where all the infrastructure lives.
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
audit_clean sast_clean vocabulary_conformant image_smoke contract_green e2e_replay_green e2e_green streams_ephemeral fleet_ephemeral changelog_entry"
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
# all-skipped run reads NO-GO. contract/e2e only reach here past the .env.seed
# guard, so a skip there means a claimed stack never exercised the suite. (No
# kibana-py analog — its suites hide behind `make test` and check exit code only.)
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
    echo "  GO    $name"
  else
    echo "  NO-GO $name  (passed=$passed skipped=$skipped: empty or all-skipped suite reads NO-GO; log: $out)"
    nogo=1
  fi
}

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
  run image_smoke sh -c 'docker build -f docker/Dockerfile -t mcp-for-kibana:dev . && scripts/checks/image-smoke.sh mcp-for-kibana:dev'
fi

if req contract_green; then
  if [ -f elastic-start-local/.env.seed ]; then
    run_suite contract_green uv run pytest -m contract -q
  else
    echo "  NO-GO contract_green  (no elastic-start-local/.env.seed — run: scripts/stack.sh up && scripts/stack.sh seed)"
    nogo=1
  fi
fi

if req e2e_replay_green; then
  # The MCP surface a model actually sees, exercised with NO model in the loop:
  # a recorded real-model turn is replayed through a real MCP client over real
  # stdio against the real server and real Kibana. Deterministic, so unlike
  # e2e_green it runs in CI. Needs a seeded stack, same precondition as contract.
  if [ -f elastic-start-local/.env.seed ]; then
    run_suite e2e_replay_green uv run pytest -m e2e_replay -q
  else
    echo "  NO-GO e2e_replay_green  (no elastic-start-local/.env.seed — run: scripts/stack.sh up && scripts/stack.sh seed)"
    nogo=1
  fi
fi

if req e2e_green; then
  if [ -f elastic-start-local/.env.seed ]; then
    # Env loading lives in tests/_stack_env.py (fixture-time) — the gate
    # only pre-checks that a stack is claimed at all.
    # Deliberately ONLY the deterministic flights gate: the space gate
    # (test_lmstudio_space.py) is an experiment driver whose default model
    # passes 2/5 by measurement — a stochastic test cannot gate GO/NO-GO.
    run_suite e2e_green uv run pytest tests/e2e/test_lmstudio.py -m e2e -q
  else
    echo "  NO-GO e2e_green  (no elastic-start-local/.env.seed — see docs/e2e-setup.md)"
    nogo=1
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
  if [ -f CHANGELOG.md ] && grep -qiE '^## (\[?unreleased|\[?[0-9]+\.[0-9]+\.[0-9]+)' CHANGELOG.md; then
    echo "  GO    changelog_entry"
  else
    echo "  NO-GO changelog_entry  (CHANGELOG.md missing or has no release section)"
    nogo=1
  fi
fi

if [ "$nogo" -eq 0 ]; then
  echo "VERDICT: GO"
else
  echo "VERDICT: NO-GO (fix the criteria above, or mark them n/a in $cfg as a visible decision)"
  exit 1
fi
