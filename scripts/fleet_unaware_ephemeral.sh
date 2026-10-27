#!/bin/sh
# Isolated Fleet stack with space awareness OFF, for `fleet_unaware_ephemeral` (D63).
#
# uninstall_package refuses every call while Fleet space awareness is on, and a
# fresh 9.5.4 stack has it on unasked — so the package-policy listing, the Fleet
# Server and agent-monitoring guards and the forced delete run on no other tier.
# This boots a throwaway single-node ES+Kibana (the .env.ephemeral.example pin,
# or ES_LOCAL_VERSION)
# with docker-compose.fleet-unaware.yml (xpack.fleet.experimentalFeatures
# .useSpaceAwareness=false) under its own compose project, sets Fleet up with the
# same fixed Fleet Server policy as fleet_ephemeral.sh (no agents: the guards read
# policies, not agents), checks awareness really is off, runs
# `pytest -m fleet_unaware` with KIBANA_MCP_FLEET_UNAWARE=1 (the tests then fail,
# never skip, if they would take the awareness-on branch), and tears it all down.
#
# Minted creds go to the test PROCESS ENV only, never elastic-start-local/.env.seed.
# Also `make fleet-unaware-ephemeral`. Portable POSIX sh; docker + curl + python3 + uv.
set -eu
cd "$(git rev-parse --show-toplevel)"
STACK_DIR=elastic-start-local
PROJECT=mcp-for-kibana-fleet-unaware
ENVFILE="$STACK_DIR/.env.ephemeral"

# Same guard as fleet_ephemeral.sh: the Docker VM can't hold a 2nd Elastic stack.
if [ -n "$(docker compose -p mcp-for-kibana-stack ps -q 2>/dev/null)" ]; then
  echo "FAIL: the dev stack (mcp-for-kibana-stack) is running — the Docker VM can't" >&2
  echo "      hold a 2nd Elastic stack. Stop it first: make stack-stop" >&2
  echo "      (or run the whole gate with: KIBANA_MCP_DOD_CYCLE_STACK=1 make dod)" >&2
  exit 1
fi

# The .env.ephemeral.example pin, or an ES_LOCAL_VERSION from the environment
# (CI runs this tier on every supported line; scripts/stack-version.sh).
. scripts/stack-version.sh
env_with_version "$STACK_DIR/.env.ephemeral.example" "$ENVFILE"
# shellcheck disable=SC1090
. "$ENVFILE"

# The tests install integration packages, so Kibana reaches the Elastic Package
# Registry: inside a TLS-intercepting sandbox it needs the proxy CA, composed
# exactly as scripts/stack.sh does (only when the CA exists; a no-op elsewhere).
PROXY_CA="${KIBANA_MCP_PROXY_CA:-/root/.ccr/ca-bundle.crt}"
overlays="-f docker-compose.yml -f docker-compose.fleet-unaware.yml"
if [ -f "$PROXY_CA" ]; then
  echo "proxy CA at $PROXY_CA — adding docker-compose.proxy-ca.yml"
  export KIBANA_MCP_PROXY_CA="$PROXY_CA"
  overlays="$overlays -f docker-compose.proxy-ca.yml"
fi
compose() {
  # shellcheck disable=SC2086  # word-splitting the overlay list is the point
  ( cd "$STACK_DIR" && docker compose -p "$PROJECT" --env-file .env.ephemeral $overlays "$@" )
}
teardown() {
  docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENVFILE"
}
# Register teardown BEFORE bring-up so a failure mid-boot still cleans up.
trap teardown EXIT INT TERM

compose down -v --remove-orphans >/dev/null 2>&1 || true   # pre-clean a stale run
# A sibling harness's leaked stack would clash on names and ports: name its clean target.
. scripts/ephemeral-siblings.sh
refuse_leaked_siblings "$PROJECT"
if ! compose up --wait --wait-timeout 300; then
  echo "FAIL: fleet-unaware stack did not become healthy in 300s. Likely insufficient" >&2
  echo "      Docker memory for a 2nd Elastic stack — stop the dev stack (make stack-stop)" >&2
  echo "      or another stack and retry; check: docker logs $KIBANA_LOCAL_CONTAINER_NAME" >&2
  exit 1
fi

ES="http://localhost:${ES_LOCAL_PORT}"
KB="http://localhost:${KIBANA_LOCAL_PORT}"

# Bounded readiness poll on /api/status; fail loud on timeout.
code=000
i=0
while [ "$i" -lt 60 ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$KB/api/status" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && break
  i=$((i + 1))
  sleep 2
done
[ "$code" = "200" ] || { echo "FAIL: fleet-unaware Kibana not ready ($KB/api/status=$code)" >&2; exit 1; }

api() {  # api METHOD PATH [JSON]
  if [ -n "${3:-}" ]; then
    curl -s -u "elastic:${ES_LOCAL_PASSWORD}" -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: Kibana' \
      -H 'Content-Type: application/json' -X "$1" "$KB$2" -d "$3"
  else
    curl -s -u "elastic:${ES_LOCAL_PASSWORD}" -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: Kibana' \
      -X "$1" "$KB$2"
  fi
}

echo "fleet-unaware: setup + Fleet Server policy..."
api POST /api/fleet/setup >/dev/null
# The fixed Fleet Server policy fleet_ephemeral.sh creates (monitoring off). A 409 is fine.
api POST /api/fleet/agent_policies \
  '{"id":"fleet-server-policy","name":"Fleet Server","namespace":"default","has_fleet_server":true,"monitoring_enabled":[]}' >/dev/null 2>&1 || true

# The overlay must have taken: Fleet settings carry no migration status.
settings=$(api GET /api/fleet/settings)
status=$(printf '%s' "$settings" | python3 -c \
  "import sys,json;print(json.load(sys.stdin)['item'].get('use_space_awareness_migration_status') or 'off')" 2>/dev/null || echo unreadable)
if [ "$status" = "unreadable" ]; then
  echo "FAIL: could not read Fleet settings at $KB/api/fleet/settings, so whether space" >&2
  echo "      awareness is off is unknown. Answer: $(printf '%s' "$settings" | head -c 300)" >&2
  echo "      Check: docker logs $KIBANA_LOCAL_CONTAINER_NAME | grep -i fleet" >&2
  exit 1
fi
if [ "$status" != "off" ]; then
  echo "FAIL: Fleet space awareness is '$status' on the fleet-unaware stack (Kibana" >&2
  echo "      ${ES_LOCAL_VERSION}), so the awareness-off branches would not run. This stack is" >&2
  echo "      composed with $STACK_DIR/docker-compose.fleet-unaware.yml: check that its" >&2
  echo "      command line still carries --xpack.fleet.experimentalFeatures.useSpaceAwareness=false" >&2
  echo "      and that this Kibana line still honours it (Fleet's experimental_features.ts" >&2
  echo "      and settings.ts; D63)." >&2
  exit 1
fi
echo "fleet-unaware: Fleet space awareness is off"

KEY=$(curl -fsSu "elastic:${ES_LOCAL_PASSWORD}" -X POST "$ES/_security/api_key" \
  -H 'Content-Type: application/json' -d '{"name":"fleet-unaware-ephemeral"}' \
  | sed -n 's/.*"encoded":"\([^"]*\)".*/\1/p')
[ -n "$KEY" ] || { echo "FAIL: could not mint ephemeral API key at $ES" >&2; exit 1; }

KIBANA_URL="$KB" KIBANA_TEST_API_KEY="$KEY" KIBANA_MCP_FLEET_UNAWARE=1 \
  uv run pytest -m fleet_unaware -q
