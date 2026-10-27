#!/bin/sh
# Definition-of-Done gate: tooling-certified GO/NO-GO over the criteria declared in
# dod.config. The author (human or agent) never self-certifies "done" — this script
# does. Run it before claiming completion and ALWAYS before a release.
#
# Criteria marked `required` must pass; `n/a` are skipped (declare, don't delete, so
# the omission is a visible decision). Full logs land in /tmp/dod-<repo>/<criterion>.log
# (per-repo so sibling gates don't clobber each other's diagnostics), cleared at the
# start of every run, beside verdict.json: the record of this run (D55).
#
# The verdict is bound to a tree (D55): the header and the VERDICT line name HEAD,
# the content tree id (uncommitted and untracked work included) and clean/dirty,
# and a tree that changed during the run is NO-GO (tree_stable). A GO certifies
# commit <sha> only when `git rev-parse <sha>^{tree}` equals the recorded tree id.
# contract and the replays need local infrastructure: scripts/stack.sh up && seed (at
# the pinned Kibana version: the gate refuses another line, D53), and docker for
# image_smoke and image_replay_green. e2e_green (the live model, `claude -p`) is
# n/a in every config since D61: `make test-e2e` runs it on demand; its branch
# below still runs it when a config marks it required. CI runs the fast criteria on every
# PR (checks.yml) and the live tiers through this same gate over per-tier
# configs in .github/dod/ (integration.yml); this gate over dod.config remains the full
# superset a human or a cloud session runs where all the infrastructure lives.
#
# One-shot GO on a machine with the dev stack up:
#   scripts/stack.sh up && scripts/stack.sh seed
#   KIBANA_MCP_DOD_CYCLE_STACK=1 make dod
# The flag lets the gate STOP the dev stack between the suites that need it and
# the ephemeral tiers that need it gone (see the block above streams_ephemeral).
# It never starts or restarts the stack: start and seed it before the run, and
# after the run it stays stopped (bring it back with scripts/stack.sh up &&
# scripts/stack.sh seed). Without the flag the run is honest but two-pass:
# fleet_ephemeral NO-GOs while the dev stack is up.
#
# The suites run as declared (D56): the gate clears the variables that change
# what pytest selects, loads or enforces (PYTEST_ADDOPTS, PYTEST_PLUGINS,
# PYTEST_DEBUG, PYTEST_DISABLE_PLUGIN_AUTOLOAD, COVERAGE_RCFILE) and the replay's
# KIBANA_MCP_SERVER_BIN and KIBANA_MCP_SERVER_IMAGE, and names them; every suite line shows `N passed / M
# collected`; and a shell KIBANA_URL or KIBANA_TEST_API_KEY that differs from
# elastic-start-local/.env.seed, or a KIBANA_API_KEY, KIBANA_PUBLIC_URL or
# KIBANA_MCP_PUBLIC_KIBANA_URL in the shell or .env.local, or a UV_*, PYTHON*, LD_*,
# KIBANA_MCP_SERVER_BIN or KIBANA_MCP_SERVER_IMAGE line in .env.local, is NO-GO on the
# seeded-stack criteria.
#
# Portable POSIX sh; zero deps beyond the project's own tooling (uv).
# Concept adapted from cmanaha/extended-superpowers (MIT).
set -u

# `make dod` runs this gate; a leaked MAKEFLAGS/MFLAGS from a parent make
# (`make -i dod`, `-k`, `-j` jobserver) can weaken sub-processes and flip the
# gate to a false GO. This repo's criteria call `uv`/`pytest` directly, so this
# is defense-in-depth (only the outer `make dod` wrapper leaks) — cleared so the
# gate always runs in a clean environment, matching the sibling kibana-py gate.
# The pytest and coverage variables reshape the suites themselves (D56): a
# leftover PYTEST_ADDOPTS='-k ... --no-cov' ran three unit tests with no
# coverage floor and read GO, and COVERAGE_RCFILE swaps the floor's config.
# KIBANA_MCP_SERVER_BIN swaps the server e2e_replay_green replays for an
# installed executable (tests/e2e_replay/test_replay.py), and KIBANA_MCP_SERVER_IMAGE
# for a Docker image (D62); wheel-replay.sh and image-replay.sh set them on their
# own command lines, so clearing them here breaks nothing. The header
# names the ones that were set, so the clearing is visible.
suite_env="PYTEST_ADDOPTS PYTEST_PLUGINS PYTEST_DEBUG PYTEST_DISABLE_PLUGIN_AUTOLOAD COVERAGE_RCFILE KIBANA_MCP_SERVER_BIN KIBANA_MCP_SERVER_IMAGE"
cleared=
for v in $suite_env; do printenv "$v" >/dev/null 2>&1 && cleared="$cleared $v"; done
# shellcheck disable=SC2086  # word-splitting the list is the point
unset MAKEFLAGS MFLAGS $suite_env

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
audit_clean sast_clean vocabulary_conformant image_smoke contract_green e2e_replay_green wheel_replay_green \
image_replay_green e2e_green streams_ephemeral fleet_ephemeral changelog_entry"
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
# Cleared first, so no log or record of an earlier run sits beside this run's.
rm -rf "$logdir" && mkdir -p "$logdir" || { echo "FAIL: cannot clear and create log dir $logdir"; exit 2; }
results="$logdir/results.tsv"   # one '<criterion><TAB><GO|NO-GO>' line per result, read into verdict.json
: >"$results"
record() { printf '%s\t%s\n' "$1" "$2" >>"$results"; }

# The tree the verdict certifies (D55): the content tree id of the working tree
# — tracked edits and untracked, non-ignored files included — built in a
# temporary copy of the index, so the real index is never touched. Equal to
# `git rev-parse HEAD^{tree}` exactly when the tree is clean.
# `cp -p` keeps the index file's mtime: a fresh one would hide racily clean
# entries (a same-size edit in the second of the last index write) from git's
# racy-clean check, and `git add -A` would skip them. The copy then drops
# every assume-unchanged bit and the skip-worktree bit of every entry whose
# file is present, since `git add -A` never rehashes an entry carrying either;
# an absent skip-worktree entry keeps its bit (not recorded as deleted) only
# when core.sparseCheckout is true; otherwise, like an absent assume-unchanged
# file, it is a deletion. A submodule counts by its checked-out commit only.
# unquote_path: git's C-quoted form ("a\"b") back to the raw path, so the
# presence check sees the real name. A trailing 'x' keeps a final newline.
unquote_path() {
  _q=${1#\"}; _q=${_q%\"}
  _soh=$(printf '\001')   # a raw control character never appears in a quoted path
  _q=$(printf '%s\n' "$_q" | sed -e "s/\\\\\\\\/$_soh/g" -e 's/\\"/"/g' -e 's/\\\([0-7]\)/\\0\1/g' -e "s/$_soh/\\\\\\\\/g")
  _u=$(printf '%bx' "$_q") && printf '%s' "${_u%x}"
}
content_tree() {
  _idx=$(mktemp "${TMPDIR:-/tmp}/dod-index.XXXXXX") || return 1
  _real=$(git rev-parse --git-path index) || { rm -f "$_idx"; return 1; }
  if [ -f "$_real" ]; then cp -p "$_real" "$_idx" || { rm -f "$_idx"; return 1; }; else rm -f "$_idx"; fi
  _all=$(GIT_INDEX_FILE="$_idx" git -c core.quotepath=off ls-files -v 2>/dev/null) || { rm -f "$_idx"; return 1; }
  # Lowercase tags carry assume-unchanged; S and s carry skip-worktree. Paths
  # go to update-index in ls-files' form (C-quoted ones included): --stdin
  # unquotes them.
  _assumed=$(printf '%s\n' "$_all" | while IFS= read -r _l; do
    case "$_l" in [hmrcks]' '*) printf '%s\n' "${_l#??}" ;; esac
  done)
  # An absent skip-worktree entry keeps its bit (not a deletion) only in a
  # sparse checkout; anywhere else nothing explains the missing file.
  _sparse=$(git config --bool core.sparseCheckout 2>/dev/null) || _sparse=false
  _skipped=$(printf '%s\n' "$_all" | while IFS= read -r _l; do
    case "$_l" in
      [sS]' '*) _p=${_l#??}
        if [ "$_sparse" != true ]; then printf '%s\n' "$_p"; continue; fi
        case "$_p" in '"'*) _f=$(unquote_path "$_p") || continue ;; *) _f=$_p ;; esac
        if [ -e "$_f" ] || [ -L "$_f" ]; then printf '%s\n' "$_p"; fi ;;
    esac
  done)
  # One update-index call per bit: with --stdin only the last such option applies.
  if [ -n "$_assumed" ]; then
    printf '%s\n' "$_assumed" | GIT_INDEX_FILE="$_idx" git update-index --no-assume-unchanged --stdin >/dev/null 2>&1 \
      || { rm -f "$_idx"; return 1; }
  fi
  if [ -n "$_skipped" ]; then
    printf '%s\n' "$_skipped" | GIT_INDEX_FILE="$_idx" git update-index --no-skip-worktree --stdin >/dev/null 2>&1 \
      || { rm -f "$_idx"; return 1; }
  fi
  # --sparse: without it git add skips paths outside the sparse-checkout cone
  # (an edit there would read clean) and fails on an untracked file there.
  GIT_INDEX_FILE="$_idx" git add -A --sparse >/dev/null 2>&1 && GIT_INDEX_FILE="$_idx" git write-tree
  _rc=$?
  rm -f "$_idx"
  return $_rc
}
tree_fail="cannot compute the content tree id (git add -A --sparse into a temporary index failed; needs git 2.34 or newer: git --version) — run: git status && git fsck, fix what they report, then re-run make dod"
tree=$(content_tree) || { echo "FAIL: $tree_fail"; exit 2; }
head=$(git rev-parse -q --verify HEAD) || head=none
head_tree=$(git rev-parse -q --verify 'HEAD^{tree}') || head_tree=
if [ "$tree" = "$head_tree" ]; then dirty=false; state=clean
else dirty=true; state="dirty: uncommitted or untracked changes"; fi
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
claude_model=

run() {
  name="$1"; shift
  if "$@" >"$logdir/$name.log" 2>&1; then
    echo "  GO    $name"; record "$name" GO
  else
    echo "  NO-GO $name  (log: $logdir/$name.log)"; record "$name" NO-GO
    nogo=1
  fi
}

# Suite criteria (unit/contract/e2e) invoke pytest directly, so exit 0 is not
# enough: a fully-skipped or empty suite also exits 0 and would read a false GO.
# Assert passed>=1 AND skipped=0 from the pytest summary so a green-but-empty or
# all-skipped run reads NO-GO. Every line shows `N passed / M collected` (M sums
# every outcome, deselected included; D56), so a suite shrunk by a `-k` or a
# marker reads as such: the default addopts deselect the live tiers from
# unit_green, so deselected tests are shown, not refused. contract/e2e only reach here past the seeded-stack
# guard (seeded_stack below), so a skip there means a claimed stack never
# exercised the suite. (No kibana-py analog — its suites hide behind `make test`
# and check exit code only.)
suite_note=  # optional detail on a suite's GO line; set only around one run_suite
run_suite() {
  name="$1"; shift
  out="$logdir/$name.log"
  if ! "$@" >"$out" 2>&1; then
    echo "  NO-GO $name  (log: $out)"; record "$name" NO-GO; nogo=1; return
  fi
  summary=$(grep -E '[0-9]+ (passed|skipped|failed|errors?|deselected|xfailed|xpassed)|no tests ran' "$out" | tail -1)
  passed=$(printf '%s\n' "$summary" | grep -oE '[0-9]+ passed'  | grep -oE '^[0-9]+')
  skipped=$(printf '%s\n' "$summary" | grep -oE '[0-9]+ skipped' | grep -oE '^[0-9]+')
  : "${passed:=0}" "${skipped:=0}"
  collected=0
  for n in $(printf '%s\n' "$summary" | grep -oE '[0-9]+ (passed|failed|skipped|deselected|xfailed|xpassed|errors?)' | grep -oE '^[0-9]+'); do
    collected=$((collected + n))
  done
  counts="$passed passed / $collected collected"
  if [ "$passed" -ge 1 ] && [ "$skipped" -eq 0 ]; then
    echo "  GO    $name  ($counts${suite_note:+; $suite_note})"; record "$name" GO
  else
    echo "  NO-GO $name  (passed=$passed skipped=$skipped, $collected collected: empty or all-skipped suite reads NO-GO; log: $out)"
    record "$name" NO-GO; nogo=1
  fi
}

# Seeded-stack precondition (D53), shared by contract_green, e2e_replay_green,
# wheel_replay_green, image_replay_green and e2e_green: .env.seed exists AND the Kibana it points at
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
stack_nogo() { echo "  NO-GO $1  ($stack_reason)"; record "$1" NO-GO; nogo=1; }

echo "Definition-of-Done gate ($cfg)"
echo "  at HEAD $head, tree $tree ($state)"
[ -n "$cleared" ] && echo "  note: cleared$cleared from the environment — the gate runs each suite as declared (D56)"

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

if req image_replay_green; then
  # The same replay once more, against the built Docker image over HTTP (D62):
  # one container per transcript, the seed's Kibana URL and no API key in it,
  # and the seed key in an `Authorization: ApiKey` header on every request —
  # the image's Python, its --no-dev venv and the per-request credential path
  # that HTTP users of the published image run, which no other tier reaches;
  # plus calls without the header (or with another scheme) that must be
  # refused naming the fix. It builds the image first (the image_smoke recipe,
  # cached when image_smoke just built it), so the criterion stands alone in
  # CI's contract job. Same seeded-stack precondition as e2e_replay_green.
  if seeded_stack; then
    suite_note="Kibana $stack_version"
    run_suite image_replay_green sh -c 'scripts/checks/image-build.sh mcp-for-kibana:dev && scripts/checks/image-replay.sh mcp-for-kibana:dev'
    suite_note=
  else
    stack_nogo image_replay_green
  fi
fi

if req e2e_green; then
  # The live-model criterion, n/a in every config since D61 (the live model
  # runs on demand: `make test-e2e`), kept so a config can still require it.
  # From D42: headless Claude Code (`claude -p`) with only
  # this server's tools runs the pre-registered tasks (scripts/experiment/
  # CLAUDE_E2E.md; eleven tasks, CLAUDE_E2E_RUNS each, default 5) and Kibana's
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
    record e2e_green NO-GO; nogo=1
  elif ! claude auth status 2>/dev/null | grep -qE '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
    echo "  NO-GO e2e_green  (the \`claude\` CLI is not signed in — run: claude auth login, then check with: claude auth status; docs/e2e-setup.md)"
    record e2e_green NO-GO; nogo=1
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
    # verdict.json records the model too.
    if e2e_meta=$(uv run python -c 'from tests import _claude_cli as c; print(c.claude_model()); print(c.runs_per_task())' 2>/dev/null); then
      claude_model=$(printf '%s\n' "$e2e_meta" | sed -n 1p)
      suite_note="model $claude_model, $(printf '%s\n' "$e2e_meta" | sed -n 2p) runs per task"
    else
      suite_note="model and runs per task unresolved"
    fi
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
# Which line the ephemeral tiers run (D62): their scripts write an exported
# ES_LOCAL_VERSION over the .env.ephemeral.example pin (scripts/stack-version.sh),
# which CI's matrix relies on. Locally the gate certifies the newest line (D30,
# D53), so a shell override that differs from the pin is NO-GO on both tiers,
# naming `unset ES_LOCAL_VERSION`, before anything is stopped or started; their
# GO lines name the line they ran.
eph_state=        # '' unchecked | ok | bad
eph_version=
eph_reason=
ephemeral_line() {
  if [ -z "$eph_state" ]; then
    eph_pin=$(sed -n 's/^ES_LOCAL_VERSION=//p' elastic-start-local/.env.ephemeral.example 2>/dev/null | tail -1 | tr -d "\"' \r")
    eph_override=${ES_LOCAL_VERSION:-}
    if [ "${GITHUB_ACTIONS:-}" = true ] && [ -n "$eph_override" ]; then
      eph_state=ok; eph_version=$eph_override
    elif [ -z "$eph_pin" ]; then
      eph_state=bad
      eph_reason="no ES_LOCAL_VERSION pin in elastic-start-local/.env.ephemeral.example — restore it (git checkout -- elastic-start-local/.env.ephemeral.example)"
    elif [ -n "$eph_override" ] && [ "$eph_override" != "$eph_pin" ]; then
      eph_state=bad
      eph_reason="the shell exports ES_LOCAL_VERSION=$eph_override, which the ephemeral stacks would run instead of the $eph_pin pin, but the local gate certifies the newest line (D30, D53, D62) — unset ES_LOCAL_VERSION, then re-run make dod"
    else
      eph_state=ok; eph_version=$eph_pin
    fi
  fi
  [ "$eph_state" = ok ]
}

if [ "${KIBANA_MCP_DOD_CYCLE_STACK:-0}" = "1" ] && { req streams_ephemeral || req fleet_ephemeral; } && ephemeral_line; then
  if [ -n "$(docker compose -p mcp-for-kibana-stack ps -q 2>/dev/null)" ]; then
    echo "  ...   stopping the dev stack for the ephemeral tiers (KIBANA_MCP_DOD_CYCLE_STACK=1)"
    if ! scripts/stack.sh stop >"$logdir/cycle_stack.log" 2>&1; then
      echo "  NO-GO cycle_stack  (could not stop the dev stack; log: $logdir/cycle_stack.log)"
      record cycle_stack NO-GO; nogo=1
    fi
  fi
fi

if req streams_ephemeral; then
  # The harness owns its own isolated stack lifecycle (up -> pytest -m ephemeral
  # -> down), so unlike contract/e2e it has no .env.seed precondition. run_suite
  # parses the wrapped pytest summary so an all-skipped run can't read a false GO.
  if ephemeral_line; then
    suite_note="Kibana $eph_version"
    run_suite streams_ephemeral scripts/ephemeral_stack.sh
    suite_note=
  else
    echo "  NO-GO streams_ephemeral  ($eph_reason)"; record streams_ephemeral NO-GO; nogo=1
  fi
fi

if req fleet_ephemeral; then
  # Same contract as streams_ephemeral: the harness owns its own isolated 2-agent
  # fleet stack lifecycle (up -> pytest -m fleet_ephemeral -> down), so no .env.seed
  # precondition. run_suite parses the wrapped pytest summary so an all-skipped run
  # can't read a false GO.
  if ephemeral_line; then
    suite_note="Kibana $eph_version"
    run_suite fleet_ephemeral scripts/fleet_ephemeral.sh
    suite_note=
  else
    echo "  NO-GO fleet_ephemeral  ($eph_reason)"; record fleet_ephemeral NO-GO; nogo=1
  fi
fi

if req changelog_entry; then
  # Diff-based (D54): every path changed since `git merge-base HEAD origin/main`
  # (fallback: the latest v* tag), untracked files included, outside a short
  # exemption list needs a new entry under `## [Unreleased]`. Without a base the
  # check fails closed naming `git fetch origin main`. checks.yml runs the same
  # script on every PR, so the rule is not only local.
  if changelog_reason=$(scripts/checks/changelog-entry.sh 2>"$logdir/changelog_entry.log"); then
    echo "  GO    changelog_entry  ($changelog_reason)"; record changelog_entry GO
  else
    echo "  NO-GO changelog_entry  (${changelog_reason:-the changelog check crashed; log: $logdir/changelog_entry.log})"
    record changelog_entry NO-GO; nogo=1
  fi
fi

# The tree must not have changed while the criteria ran (D55): an edit saved
# mid-run, or a hygiene_hooks fixer rewriting a file, means the criteria above
# certified more than one tree. Part of the gate, not a criterion: no config can
# declare it away.
tree_at_end=$(content_tree) || tree_at_end=
if [ "$tree_at_end" = "$tree" ]; then
  record tree_stable GO
elif [ -z "$tree_at_end" ]; then
  echo "  NO-GO tree_stable  ($tree_fail)"; record tree_stable NO-GO; nogo=1
else
  fixer_hint=
  req hygiene_hooks && fixer_hint=" (a hygiene_hooks fixer may have rewritten a file: see $logdir/hygiene_hooks.log)"
  echo "  NO-GO tree_stable  (the tree changed during the run: $tree at start, $tree_at_end at end, so the criteria above certified more than one tree — commit or stash your edits, leave the tree alone until the gate finishes$fixer_hint, then re-run make dod)"
  record tree_stable NO-GO; nogo=1
fi

# verdict.json: the record of this run, beside its logs (D55).
json_str() { if [ -z "$1" ]; then printf 'null'; else printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; fi; }
cfg_sha=$( (sha256sum "$cfg" 2>/dev/null || shasum -a 256 "$cfg") | cut -d' ' -f1)
if [ "$nogo" -eq 0 ]; then verdict=GO; else verdict=NO-GO; fi
{
  printf '{\n'
  printf '  "verdict": "%s",\n' "$verdict"
  printf '  "head": %s,\n' "$(json_str "$head")"
  printf '  "tree": "%s",\n' "$tree"
  printf '  "tree_at_end": %s,\n' "$(json_str "$tree_at_end")"
  printf '  "dirty": %s,\n' "$dirty"
  printf '  "config": %s,\n' "$(json_str "$cfg")"
  printf '  "config_sha256": %s,\n' "$(json_str "$cfg_sha")"
  printf '  "kibana_version": %s,\n' "$(json_str "$stack_version")"
  printf '  "claude_model": %s,\n' "$(json_str "$claude_model")"
  printf '  "started_at": "%s",\n' "$started_at"
  printf '  "finished_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "criteria": {'
  sep=
  for k in $known; do
    if req "$k"; then
      r=$(awk -F'\t' -v k="$k" '$1 == k { r = $2 } END { print r }' "$results")
      : "${r:=not run}"
    else
      r=n/a
    fi
    printf '%s\n    "%s": "%s"' "$sep" "$k" "$r"; sep=,
  done
  for k in cycle_stack tree_stable; do
    r=$(awk -F'\t' -v k="$k" '$1 == k { r = $2 } END { print r }' "$results")
    [ -n "$r" ] && { printf '%s\n    "%s": "%s"' "$sep" "$k" "$r"; sep=,; }
  done
  printf '\n  }\n}\n'
} >"$logdir/verdict.json"

where="HEAD $(printf '%.12s' "$head"), tree $tree, $( [ "$dirty" = true ] && echo dirty || echo clean); record: $logdir/verdict.json"
if [ "$nogo" -eq 0 ]; then
  echo "VERDICT: GO  ($where)"
else
  echo "VERDICT: NO-GO  ($where) — fix the criteria above, or mark them n/a in $cfg as a visible decision"
  exit 1
fi
