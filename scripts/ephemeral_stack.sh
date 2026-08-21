#!/bin/sh
# Ephemeral isolated stack for the streams disable/enable DoD certification.
#
# disable_streams deletes ALL wired streams + data cluster-wide, so it can't run
# against the shared dev stack. This spins up a throwaway single-node ES+Kibana
# 9.4.3 under a DISTINCT compose project (mcp-for-kibana-ephemeral), with distinct
# container names + ports (29200/25601) + creds from .env.ephemeral — so it never
# collides with the dev stack (mcp-for-kibana-stack, 19200/15601) even when that is up.
# Minted creds go to the test PROCESS ENV only, never elastic-start-local/.env.seed
# (which contract/e2e depend on, and which is machine-guarded to exactly two keys).
#
# Wired into the DoD gate as the `streams_ephemeral` criterion (dod.config); also
# `make streams-ephemeral`. Portable POSIX sh; zero deps beyond docker + uv.
set -eu
cd "$(git rev-parse --show-toplevel)"
STACK_DIR=elastic-start-local
PROJECT=mcp-for-kibana-ephemeral
ENVFILE="$STACK_DIR/.env.ephemeral"

cp "$STACK_DIR/.env.ephemeral.example" "$ENVFILE"
# shellcheck disable=SC1090
. "$ENVFILE"

compose() {
  ( cd "$STACK_DIR" && docker compose -p "$PROJECT" --env-file .env.ephemeral -f docker-compose.yml "$@" )
}
teardown() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENVFILE"
}
# Register teardown BEFORE bring-up so a failure mid-boot still cleans up.
trap teardown EXIT INT TERM

compose down -v --remove-orphans >/dev/null 2>&1 || true   # pre-clean a stale run
# --wait-timeout so a resource-starved boot fails fast+clear instead of hanging the
# gate: the ephemeral Kibana needs ~3GB free — stop other Elastic stacks (the dev
# stack: `scripts/stack.sh stop`) if this times out on a memory-constrained Docker VM.
if ! compose up --wait --wait-timeout 300; then
  echo "FAIL: ephemeral stack did not become healthy in 300s. Likely insufficient" >&2
  echo "      Docker memory for a 3rd Elastic stack — stop the dev stack (scripts/stack.sh" >&2
  echo "      stop) or another stack and retry." >&2
  exit 1
fi

KB="http://localhost:${KIBANA_LOCAL_PORT}"
ES="http://localhost:${ES_LOCAL_PORT}"

# Bounded readiness poll on /api/status (auth-blind; a real 200 means Kibana +
# its plugins are up). Fail loud on timeout — never an unbounded hang in the gate.
code=000
i=0
while [ "$i" -lt 60 ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$KB/api/status" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && break
  i=$((i + 1))
  sleep 2
done
[ "$code" = "200" ] || { echo "FAIL: ephemeral Kibana not ready ($KB/api/status=$code)" >&2; exit 1; }

KEY=$(curl -fsSu "elastic:${ES_LOCAL_PASSWORD}" -X POST "$ES/_security/api_key" \
  -H 'Content-Type: application/json' -d '{"name":"streams-ephemeral"}' \
  | sed -n 's/.*"encoded":"\([^"]*\)".*/\1/p')
[ -n "$KEY" ] || { echo "FAIL: could not mint ephemeral API key at $ES" >&2; exit 1; }

KIBANA_URL="$KB" KIBANA_TEST_API_KEY="$KEY" uv run pytest -m ephemeral -q
