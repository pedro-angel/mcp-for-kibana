#!/bin/sh
# Manage the local Kibana test stack (elastic start-local near-copy) for
# contract/E2E tests. usage: scripts/stack.sh up|seed|status|stop|down|env
#
# Stack config (version, ports, container names) lives once in
# elastic-start-local/.env.example; `up` regenerates elastic-start-local/.env
# from it. Test creds (KIBANA_URL, KIBANA_TEST_API_KEY) live in
# elastic-start-local/.env.seed (seed-written, loader-read — foldered with the
# rest of the stack env, kibana-py-style). User config (LMSTUDIO_*) lives in
# .env.local (human-written, never touched here).
#
# Opt-in APM: `KIBANA_MCP_STACK_APM=1 scripts/stack.sh up` also starts the
# apm-server overlay (elastic-start-local/docker-compose.apm.yml) — the local
# OpenTelemetry backend. Off by default so the contract/E2E path stays cheap.
#
# Fleet: `up` ALWAYS brings up a Fleet Server + one enrolled demo agent
# (elastic-start-local/docker-compose.fleet.yml, via scripts/fleet_stack.sh) —
# the server ships a `fleet` toolbox, so fleet is a required, contract-tested
# part of the stack, not opt-in. The minted tokens live in the gitignored
# elastic-start-local/.env.fleet (never committed). The isolated ephemeral stack
# does NOT load this overlay, so it stays lean.
set -eu
cd "$(dirname "$0")/.."
STACK_DIR=elastic-start-local
PROJECT=mcp-for-kibana-stack
ENVFILE="$STACK_DIR/.env.seed"
USAGE='usage: stack.sh up|seed|status|stop|down|env'

# Echo http://localhost:<port> for a *_PORT var read from the stack .env
# (single source; avoids hardcoding 19200/15601 here). Never reads a *_URL var
# (those use the lazy ${..} form; we read the plain *_PORT literal).
stack_url() {
  port=$(grep "^$1=" "$STACK_DIR/.env" | cut -d= -f2-)
  # Pipe to cut masks a failed grep, so guard explicitly: a missing/renamed var
  # in a stale .env would otherwise build http://localhost: (curl hits port 80).
  [ -n "$port" ] || { echo "FAIL: $1 missing from $STACK_DIR/.env" >&2; exit 1; }
  echo "http://localhost:${port}"
}

case "${1:?$USAGE}" in
  up)
    # Regenerate stack config from the single source (lossless: constants).
    cp "$STACK_DIR/.env.example" "$STACK_DIR/.env"
    # Inside a TLS-intercepting sandbox (Claude Code cloud session) the containers do
    # not trust the proxy CA the VM trusts, so Kibana's outbound HTTPS fails. Compose
    # the overlay only when that CA exists — a no-op on a laptop or a GitHub runner.
    # start.sh is vendored and runs a bare `docker compose up`, so this travels as
    # COMPOSE_FILE rather than an edit to it.
    PROXY_CA="${KIBANA_MCP_PROXY_CA:-/root/.ccr/ca-bundle.crt}"
    if [ -f "$PROXY_CA" ]; then
      echo "proxy CA at $PROXY_CA — adding docker-compose.proxy-ca.yml"
      export KIBANA_MCP_PROXY_CA="$PROXY_CA"
      export COMPOSE_FILE="docker-compose.yml:docker-compose.proxy-ca.yml"
    fi
    sh "$STACK_DIR/start.sh"
    # APM server is an opt-in project addition for OpenTelemetry work (Phase C):
    # only KIBANA_MCP_STACK_APM=1 starts it, so the common (non-opted-in) `up`
    # path stays cheap and a test-irrelevant APM failure can't touch it. When
    # opted in, a failed start fails loud below. Run from the stack dir (like
    # start.sh) so compose auto-reads its .env; it joins -p mcp-for-kibana-stack, so
    # `down`/`status` already cover it.
    if [ "${KIBANA_MCP_STACK_APM:-0}" = "1" ]; then
      echo "starting APM server (KIBANA_MCP_STACK_APM=1)..."
      ( cd "$STACK_DIR" && docker compose -p "$PROJECT" \
          -f docker-compose.yml -f docker-compose.apm.yml up --wait apm-server )
      # The image ships no healthcheck, so --wait returns on "running", not ready
      # (a crash-looping/mis-wired apm-server would still be "running"). Poll the
      # endpoint for a real 200 before claiming readiness; fail loud otherwise.
      APM_URL=$(stack_url APM_LOCAL_PORT)
      ready=; i=0
      while [ "$i" -lt 30 ]; do
        if [ "$(curl -s -o /dev/null -w '%{http_code}' "$APM_URL/" 2>/dev/null || true)" = "200" ]; then
          ready=1; break
        fi
        i=$((i + 1)); sleep 1
      done
      if [ -n "$ready" ]; then
        echo "apm-server is up at $APM_URL"
      else
        APM_NAME=$(grep '^APM_LOCAL_CONTAINER_NAME=' "$STACK_DIR/.env" | cut -d= -f2-)
        echo "FAIL: apm-server started but never answered 200 at $APM_URL/ — check: docker logs $APM_NAME" >&2
        exit 1
      fi
    fi
    # Fleet Server + demo agent are ALWAYS part of the dev/test stack (the server
    # exposes a `fleet` toolbox that must be running + contract-tested). Delegated
    # to scripts/fleet_stack.sh (bootstrap + bring-up + readiness poll); a failure
    # aborts `up` under set -e (fail loud).
    sh scripts/fleet_stack.sh
    echo "kibana is up at $(stack_url KIBANA_LOCAL_PORT)"
    ;;
  seed)
    [ -f "$STACK_DIR/.env" ] || { echo "FAIL: run 'scripts/stack.sh up' first" >&2; exit 1; }
    ES=$(stack_url ES_LOCAL_PORT)
    KB=$(stack_url KIBANA_LOCAL_PORT)
    ESPW=$(grep '^ES_LOCAL_PASSWORD=' "$STACK_DIR/.env" | cut -d= -f2-)
    # Machine-only guard (before any network I/O; the reads above are local):
    # foreign lines are pre-split user data — abort, never silently drop
    # (move them to .env.local).
    if [ -f "$ENVFILE" ] && grep -qEv '^KIBANA_(URL|TEST_API_KEY)=|^[[:space:]]*$' "$ENVFILE"; then
      echo "FAIL: $ENVFILE has non-machine lines — move them to .env.local" >&2
      echo "      ($ENVFILE is machine-owned: exactly KIBANA_URL and KIBANA_TEST_API_KEY)" >&2
      exit 1
    fi
    echo "loading flights sample data..."
    # Kibana 9.4.3 enforces restrictInternalApis; /api/sample_data returns 400
    # ("exists but is not available") without the internal-origin header (probe P8).
    curl -fsSu "elastic:${ESPW}" -X POST "$KB/api/sample_data/flights" \
      -H 'kbn-xsrf: true' -H 'x-elastic-internal-origin: Kibana'
    echo ""
    # Keep the existing key when it still authenticates (/api/features because
    # /api/status is auth-blind); any non-200 routes to mint (benign re-mint).
    KEY=$(grep '^KIBANA_TEST_API_KEY=' "$ENVFILE" 2>/dev/null | cut -d= -f2- || true)
    STATUS=000
    [ -n "$KEY" ] && STATUS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      -H "Authorization: ApiKey $KEY" "$KB/api/features" || true)
    if [ "$STATUS" = "200" ]; then
      echo "existing API key still valid — keeping it"
    else
      echo "minting API key..."
      KEY=$(curl -fsSu "elastic:${ESPW}" -X POST "$ES/_security/api_key" \
        -H 'Content-Type: application/json' \
        -d '{"name":"mcp-for-kibana-tests"}' | sed -n 's/.*"encoded":"\([^"]*\)".*/\1/p')
      [ -n "$KEY" ] || { echo "FAIL: could not mint API key (is elasticsearch up at $ES?)" >&2; exit 1; }
    fi
    # Atomic rewrite (temp + rename): a concurrent reader never sees a
    # truncated file. Temp name matches the .env.* gitignore pattern. Arm the
    # trap before mktemp so a signal mid-create still cleans up.
    TMP=
    trap 'rm -f "$TMP"' EXIT INT TERM
    TMP=$(mktemp "$STACK_DIR/.env.seed.tmp.XXXXXX")
    printf 'KIBANA_URL=%s\nKIBANA_TEST_API_KEY=%s\n' "$KB" "$KEY" > "$TMP"
    mv "$TMP" "$ENVFILE"
    echo "wrote $ENVFILE (gitignored — verify: git check-ignore $ENVFILE)"
    ;;
  status)
    # -p alone lists the project by label — needs no compose file or .env, so
    # it's graceful whether the stack is up or already torn down. -a so a
    # `stop`ped-but-not-down stack still shows (plain ps hides stopped ones).
    docker compose -p "$PROJECT" ps -a
    ;;
  stop)
    # Non-destructive stop (keeps volumes); down -v is the destructive teardown.
    # Best-effort pause of an opt-in apm-server too (resolved by project label;
    # a clean no-op when it was never started), so a KIBANA_MCP_STACK_APM run
    # stops fully.
    docker compose -p "$PROJECT" stop apm-server fleet-server fleet-agent 2>/dev/null || true
    sh "$STACK_DIR/stop.sh"
    ;;
  env)
    if [ -f "$ENVFILE" ]; then cat "$ENVFILE"; else echo "no $ENVFILE — run: scripts/stack.sh up && scripts/stack.sh seed" >&2; fi
    # .env.local may hold the secret LMSTUDIO_API_TOKEN — report presence, never
    # print its contents. The non-secret defaults are readable in .env.local.example.
    if [ -f .env.local ]; then
      echo "# .env.local present ($(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' .env.local) var(s); values hidden — see .env.local.example)"
    fi
    ;;
  down)
    # -p alone (no -f/--env-file/.env): removes the project by label and is
    # re-runnable (a double-down is a clean no-op). `|| true` so the seed-file
    # cleanup below still runs even if compose exits nonzero (e.g. daemon down
    # when `down` is invoked just to purge .env.seed).
    docker compose -p "$PROJECT" down -v --remove-orphans || true
    rm -f "$ENVFILE" "$STACK_DIR/.env.seed.tmp."* "$STACK_DIR/.env" \
      "$STACK_DIR/.env.fleet" "$STACK_DIR/.env.fleet.tmp."*
    ;;
  *)
    echo "$USAGE" >&2
    exit 2
    ;;
esac
