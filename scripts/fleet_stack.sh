#!/bin/sh
# Bring up Fleet Server + one enrolled demo agent as an ALWAYS-ON part of the
# dev/test stack. scripts/stack.sh `up` calls this after the base ES+Kibana are
# ready. The MCP server exposes a `fleet` toolbox, so fleet must be running and
# contract-tested — not opt-in (unlike the APM overlay).
#
# Steps (all against the running Kibana, plain HTTP, superuser elastic creds):
#   1. POST /api/fleet/setup                  (idempotent; creates Fleet indices)
#   2. create a fleet-server policy + a demo-agent policy, both fixed-id and
#      monitoring_enabled:[] — the default Fleet output points at localhost:9200,
#      which is unreachable inside a container, so self-monitoring would leave the
#      agents 'degraded'; disabling it keeps them cleanly 'online' (confirmed by
#      bringing the stack up both ways).
#   3. mint a service token (fleet-server -> ES) + an enrollment token
#      (demo agent -> fleet-server) into the gitignored .env.fleet
#   4. compose up fleet-server + fleet-agent (both env files), poll readiness
#
# Portable POSIX sh; zero deps beyond docker + curl + python3 (already required
# by scripts/stack.sh's callers). Concept mirrors the APM overlay block.
set -eu
cd "$(dirname "$0")/.."
STACK_DIR=elastic-start-local
PROJECT=mcp-for-kibana-stack
FLEETENV="$STACK_DIR/.env.fleet"
# Same proxy-CA overlay as scripts/stack.sh: composed only where the CA exists.
# Explicit -f flags here (unlike the vendored start.sh), so it is an argument.
PROXY_CA="${KIBANA_MCP_PROXY_CA:-/root/.ccr/ca-bundle.crt}"
PROXY_CA_ARG=""
if [ -f "$PROXY_CA" ]; then
  export KIBANA_MCP_PROXY_CA="$PROXY_CA"
  PROXY_CA_ARG="-f docker-compose.proxy-ca.yml"
fi

[ -f "$STACK_DIR/.env" ] || { echo "FAIL: run 'scripts/stack.sh up' first (no $STACK_DIR/.env)" >&2; exit 1; }
v() { grep "^$1=" "$STACK_DIR/.env" | cut -d= -f2-; }
ESPW=$(v ES_LOCAL_PASSWORD); KBPORT=$(v KIBANA_LOCAL_PORT); FLPORT=$(v FLEET_LOCAL_PORT)
FSNAME=$(v FLEET_LOCAL_CONTAINER_NAME)
KB="http://localhost:${KBPORT}"

# One Kibana Fleet API call with the internal-origin header 9.4.3 requires.
api() {  # api METHOD PATH [JSON]
  if [ -n "${3:-}" ]; then
    curl -s -u "elastic:${ESPW}" -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: Kibana' \
      -H 'Content-Type: application/json' -X "$1" "$KB$2" -d "$3"
  else
    curl -s -u "elastic:${ESPW}" -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: Kibana' \
      -X "$1" "$KB$2"
  fi
}
jget() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

echo "fleet: setup + policies..."
api POST /api/fleet/setup >/dev/null
# Fixed-id policies, monitoring OFF. A 409 (already created on a prior up) is fine.
api POST /api/fleet/agent_policies \
  '{"id":"fleet-server-policy","name":"Fleet Server","namespace":"default","has_fleet_server":true,"monitoring_enabled":[]}' >/dev/null 2>&1 || true
api POST /api/fleet/agent_policies \
  '{"id":"fleet-agent-policy","name":"Demo Agent","namespace":"default","monitoring_enabled":[]}' >/dev/null 2>&1 || true

echo "fleet: minting service token + enrollment token -> $FLEETENV ..."
TOKEN=$(api POST /api/fleet/service_tokens | jget "d.get('value','')")
[ -n "$TOKEN" ] || { echo "FAIL: could not mint fleet service token (is Kibana up at $KB?)" >&2; exit 1; }
ENROLL=$(api POST /api/fleet/enrollment_api_keys '{"policy_id":"fleet-agent-policy"}' | jget "d.get('item',{}).get('api_key','')")
[ -n "$ENROLL" ] || { echo "FAIL: could not mint enrollment token" >&2; exit 1; }

# Atomic write of the gitignored machine file (temp name matches the .env.*
# gitignore pattern; trap armed before mktemp so a signal mid-create cleans up).
TMP=
trap 'rm -f "$TMP"' EXIT INT TERM
TMP=$(mktemp "$STACK_DIR/.env.fleet.tmp.XXXXXX")
printf 'FLEET_SERVER_SERVICE_TOKEN=%s\nFLEET_ENROLLMENT_TOKEN=%s\n' "$TOKEN" "$ENROLL" > "$TMP"
mv "$TMP" "$FLEETENV"

echo "fleet: starting fleet-server + demo agent..."
# Both --env-file flags: once any is given, compose stops auto-reading .env, so
# pass .env (stack constants) AND .env.fleet (the minted tokens) explicitly.
# shellcheck disable=SC2086  # intentional split: PROXY_CA_ARG is empty or one flag pair
( cd "$STACK_DIR" && docker compose -p "$PROJECT" \
    -f docker-compose.yml -f docker-compose.fleet.yml $PROXY_CA_ARG \
    --env-file .env --env-file .env.fleet up --wait fleet-server fleet-agent ) || true

# --wait returns on "running" (no compose healthcheck on the agent image); poll
# the fleet-server status endpoint for real readiness, then wait for both agents
# to enroll so the contract fixture is deterministic (fail loud on timeout).
FL="http://localhost:${FLPORT}"
ready=; i=0
while [ "$i" -lt 60 ]; do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$FL/api/status" 2>/dev/null || true)" = "200" ] && { ready=1; break; }
  i=$((i + 1)); sleep 2
done
[ -n "$ready" ] || { echo "FAIL: fleet-server never answered 200 at $FL/api/status — check: docker logs $FSNAME" >&2; exit 1; }

echo "fleet: waiting for both agents to enroll..."
n=0; i=0
while [ "$i" -lt 60 ]; do
  n=$(api GET "/api/fleet/agents?showInactive=true" | jget "d.get('total',0)")
  [ "${n:-0}" -ge 2 ] && break
  i=$((i + 1)); sleep 3
done
[ "${n:-0}" -ge 2 ] || { echo "FAIL: only ${n:-0} fleet agent(s) enrolled after wait (expected 2: fleet-server + demo agent)" >&2; exit 1; }

# Enrolled is not ready: a fresh stack holds both agents in 'updating' while the
# initial policy applies, and a consumer that reads agent status right away sees
# online=0 (observed live: CI integration run 29681587295 failed the contract
# fixture's online floor exactly this way; a warm stack probes online=2/updating=0).
# Both policies disable self-monitoring precisely so the agents settle cleanly
# 'online' (step 2 above) — wait for that, bounded (~5 min; a fresh CI stack takes
# 1-3 min, a warm local stack passes on the first probe).
echo "fleet: waiting for both agents to report online..."
on=0; i=0
while [ "$i" -lt 100 ]; do
  on=$(api GET /api/fleet/agent_status | jget "d.get('results',{}).get('online',0)")
  [ "${on:-0}" -ge 2 ] && break
  i=$((i + 1)); sleep 3
done
[ "${on:-0}" -ge 2 ] || { echo "FAIL: only ${on:-0}/2 fleet agents online after wait — check: docker logs $FSNAME" >&2; exit 1; }
echo "fleet: server healthy at $FL, ${n} agents enrolled and online"
