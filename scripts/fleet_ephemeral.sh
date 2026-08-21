#!/bin/sh
# Isolated fleet harness for the agent-lifecycle battle-tests (`fleet_ephemeral`).
#
# The fleet write tools reassign / upgrade / unenroll REAL agents, so they can't
# be certified against the shared dev stack (that would mutate the always-on demo
# agent other contract tests depend on). This spins up a throwaway single-node
# ES+Kibana 9.4.3 + a Fleet Server + TWO sacrificial enrolled agents under a
# DISTINCT compose project (mcp-for-kibana-fleet-ephemeral), with distinct container
# names + ports + creds from .env.ephemeral — so it never collides with the dev
# stack (mcp-for-kibana-stack) even when that is up. Two agents so a lifecycle test can
# act on one while the other stays a live control.
#
# = the UNION of scripts/ephemeral_stack.sh (isolated project, bounded readiness
# poll, transient .env) and scripts/fleet_stack.sh (/api/fleet/setup, policies,
# minted service + enrollment tokens, compose-up, enrollment wait). It runs
# `pytest -m fleet_ephemeral` (NOT -m ephemeral), then tears everything down.
#
# Minted creds go to the test PROCESS ENV only (KIBANA_URL/KIBANA_TEST_API_KEY),
# never elastic-start-local/.env.seed (contract/e2e depend on it, machine-guarded
# to two keys). The fleet tokens go to the gitignored .env.fleet.ephemeral. This
# task wires the harness + a smoke test; the DoD gate wiring is a later task.
# Portable POSIX sh; zero deps beyond docker + curl + python3 + uv.
set -eu
cd "$(git rev-parse --show-toplevel)"
STACK_DIR=elastic-start-local
PROJECT=mcp-for-kibana-fleet-ephemeral
ENVFILE="$STACK_DIR/.env.ephemeral"
FLEETENV="$STACK_DIR/.env.fleet.ephemeral"

# Fail fast if the always-on dev stack is running: the Docker VM can't hold two
# Elastic stacks at once (a 2nd would OOM or fail to bind its ES/Kibana). The
# operator stops it first — same contract as ephemeral_stack.sh. `ps -q` (no -a)
# lists only RUNNING containers, so a merely `stop`ped dev stack does not trip it.
#
# Measured 2026-08-09 on an 11.7GB VM: the dev stack alone is ~4.9GB (ES 3.2,
# Kibana 1.2, fleet-server 0.4), leaving ~4.4GB once other local work is
# accounted for — under what a 2nd stack plus two agents needs. The guard is
# sized by that headroom, not by one VM total, so growing the VM does not
# automatically retire it; re-measure before relaxing this.
# `make dod` can cross this boundary in one pass via KIBANA_MCP_DOD_CYCLE_STACK=1,
# which stops the dev stack between the two halves (scripts/checks/definition-of-done.sh).
if [ -n "$(docker compose -p mcp-for-kibana-stack ps -q 2>/dev/null)" ]; then
  echo "FAIL: the dev stack (mcp-for-kibana-stack) is running — the Docker VM can't" >&2
  echo "      hold a 2nd Elastic stack. Stop it first: make stack-stop" >&2
  echo "      (or run the whole gate with: KIBANA_MCP_DOD_CYCLE_STACK=1 make dod)" >&2
  exit 1
fi

cp "$STACK_DIR/.env.ephemeral.example" "$ENVFILE"
# shellcheck disable=SC1090
. "$ENVFILE"

# Base ES+Kibana only (no fleet overlay yet — the tokens it needs don't exist
# until we mint them below).
compose() {
  ( cd "$STACK_DIR" && docker compose -p "$PROJECT" --env-file .env.ephemeral -f docker-compose.yml "$@" )
}
# Base + fleet overlay, both env files (post-mint). ES/Kibana are already up from
# `compose`; passing both -f files recomputes their config identically (the fleet
# tokens are unused by them), so they are NOT recreated — only the fleet services
# are started. Mirrors how fleet_stack.sh brings fleet up over the running stack.
fleet_compose() {
  ( cd "$STACK_DIR" && docker compose -p "$PROJECT" \
      --env-file .env.ephemeral --env-file .env.fleet.ephemeral \
      -f docker-compose.yml -f docker-compose.fleet.yml "$@" )
}
teardown() {
  # Label-based teardown (like scripts/stack.sh down): removes ALL project
  # containers/volumes/networks — ES, Kibana, fleet-server, both agents — whether
  # or not we got as far as minting the fleet tokens. Then drop the transient env.
  docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENVFILE" "$FLEETENV" "$STACK_DIR"/.env.fleet.ephemeral.tmp.* 2>/dev/null || true
}
# Register teardown BEFORE bring-up so a failure mid-boot still cleans up.
trap teardown EXIT INT TERM

compose down -v --remove-orphans >/dev/null 2>&1 || true   # pre-clean a stale run
# --wait-timeout so a resource-starved boot fails fast+clear instead of hanging:
# the ephemeral Kibana needs ~3GB free — stop other Elastic stacks if this times
# out on a memory-constrained Docker VM.
if ! compose up --wait --wait-timeout 300; then
  echo "FAIL: fleet-ephemeral base stack did not become healthy in 300s. Likely" >&2
  echo "      insufficient Docker memory for a 2nd Elastic stack — stop the dev" >&2
  echo "      stack (make stack-stop) or another stack and retry." >&2
  exit 1
fi

ES="http://localhost:${ES_LOCAL_PORT}"
KB="http://localhost:${KIBANA_LOCAL_PORT}"
FL="http://localhost:${FLEET_LOCAL_PORT}"

# Bounded readiness poll on /api/status (auth-blind; a real 200 means Kibana +
# its plugins are up). Fail loud on timeout — never an unbounded hang.
code=000
i=0
while [ "$i" -lt 60 ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$KB/api/status" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && break
  i=$((i + 1))
  sleep 2
done
[ "$code" = "200" ] || { echo "FAIL: fleet-ephemeral Kibana not ready ($KB/api/status=$code)" >&2; exit 1; }

# --- Fleet bootstrap (mirrors scripts/fleet_stack.sh, against the isolated KB) ---
# One Kibana Fleet API call with the internal-origin header 9.4.3 requires.
api() {  # api METHOD PATH [JSON]
  if [ -n "${3:-}" ]; then
    curl -s -u "elastic:${ES_LOCAL_PASSWORD}" -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: Kibana' \
      -H 'Content-Type: application/json' -X "$1" "$KB$2" -d "$3"
  else
    curl -s -u "elastic:${ES_LOCAL_PASSWORD}" -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: Kibana' \
      -X "$1" "$KB$2"
  fi
}
jget() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

echo "fleet-ephemeral: setup + policies..."
api POST /api/fleet/setup >/dev/null
# Fixed-id policies, monitoring OFF (the default output points at localhost:9200,
# unreachable in-container, which would leave agents 'degraded'). A 409 is fine.
api POST /api/fleet/agent_policies \
  '{"id":"fleet-server-policy","name":"Fleet Server","namespace":"default","has_fleet_server":true,"monitoring_enabled":[]}' >/dev/null 2>&1 || true
api POST /api/fleet/agent_policies \
  '{"id":"fleet-agent-policy","name":"Demo Agent","namespace":"default","monitoring_enabled":[]}' >/dev/null 2>&1 || true

echo "fleet-ephemeral: minting service token + enrollment token -> $FLEETENV ..."
TOKEN=$(api POST /api/fleet/service_tokens | jget "d.get('value','')")
[ -n "$TOKEN" ] || { echo "FAIL: could not mint fleet service token (is Kibana up at $KB?)" >&2; exit 1; }
ENROLL=$(api POST /api/fleet/enrollment_api_keys '{"policy_id":"fleet-agent-policy"}' | jget "d.get('item',{}).get('api_key','')")
[ -n "$ENROLL" ] || { echo "FAIL: could not mint enrollment token" >&2; exit 1; }

# Atomic write of the gitignored token file (temp name matches the .env.* ignore
# pattern; teardown also sweeps any stray .tmp. left by a signal mid-create).
TMP=$(mktemp "$STACK_DIR/.env.fleet.ephemeral.tmp.XXXXXX")
printf 'FLEET_SERVER_SERVICE_TOKEN=%s\nFLEET_ENROLLMENT_TOKEN=%s\n' "$TOKEN" "$ENROLL" > "$TMP"
mv "$TMP" "$FLEETENV"

echo "fleet-ephemeral: starting fleet-server + BOTH agents..."
# --wait returns on "running" (the agent image ships no compose healthcheck), so
# the readiness poll below is the real gate. `|| true` for the same reason.
fleet_compose up --wait fleet-server fleet-agent fleet-agent-2 || true

# Poll the fleet-server status endpoint for real readiness (fail loud on timeout).
ready=; i=0
while [ "$i" -lt 60 ]; do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$FL/api/status" 2>/dev/null || true)" = "200" ] && { ready=1; break; }
  i=$((i + 1)); sleep 2
done
[ -n "$ready" ] || { echo "FAIL: fleet-server never answered 200 at $FL/api/status — check: docker logs ${FLEET_LOCAL_CONTAINER_NAME}" >&2; exit 1; }

echo "fleet-ephemeral: waiting for all three agents to enroll (server + 2 agents)..."
n=0; i=0
while [ "$i" -lt 60 ]; do
  n=$(api GET "/api/fleet/agents?showInactive=true" | jget "d.get('total',0)")
  [ "${n:-0}" -ge 3 ] && break
  i=$((i + 1)); sleep 3
done
[ "${n:-0}" -ge 3 ] || { echo "FAIL: only ${n:-0} fleet agent(s) enrolled (expected 3: fleet-server + 2 sacrificial agents)" >&2; exit 1; }
echo "fleet-ephemeral: server healthy at $FL, ${n} agents enrolled"

# Mint a read/write ES API key for the test process env (never .env.seed).
KEY=$(curl -fsSu "elastic:${ES_LOCAL_PASSWORD}" -X POST "$ES/_security/api_key" \
  -H 'Content-Type: application/json' -d '{"name":"fleet-ephemeral"}' \
  | sed -n 's/.*"encoded":"\([^"]*\)".*/\1/p')
[ -n "$KEY" ] || { echo "FAIL: could not mint ephemeral API key at $ES" >&2; exit 1; }

KIBANA_URL="$KB" KIBANA_TEST_API_KEY="$KEY" uv run pytest -m fleet_ephemeral -q
