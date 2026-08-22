#!/usr/bin/env bash
# cloud-setup.sh -- setup script for the mcp-for-kibana Claude Code cloud environment.
#
# Runs as root on the session VM (Ubuntu 24.04, x86_64) BEFORE Claude Code starts, once
# per environment-cache generation. Anthropic snapshots the filesystem afterwards, so what
# this script leaves on disk -- apt packages, pulled Docker images -- is already present in
# every later session. Running processes are NOT snapshotted: the stack itself comes up per
# session via scripts/stack.sh, scripts/ephemeral_stack.sh or scripts/fleet_stack.sh.
#
# Wire it up by pasting this bootstrap into the environment's "Setup script" field:
#
#   #!/bin/bash
#   curl -fsSL https://raw.githubusercontent.com/pedro-angel/mcp-for-kibana/main/scripts/cloud-setup.sh \
#     -o /tmp/cloud-setup.sh && bash /tmp/cloud-setup.sh
#
# Two platform constraints, and how this script answers them:
#   * It must exit 0 -- a non-zero exit fails session start. Every step is fail-open; a step
#     that cannot run logs why and is skipped.
#   * It must finish in roughly five minutes -- overrunning means no cache is built, so every
#     session pays the full pull. Pulls run against a wall-clock deadline; completed layers
#     still cache, and anything missed is pulled on demand inside the session.
#
# Environment variables (set them in the cloud environment's variables field):
#   KIBANA_MCP_STACK_VERSIONS  space-separated stack versions to pre-pull. Unset is the
#                              normal case: the version is read from the repo's own
#                              elastic-start-local/.env.example, so there is no second
#                              place to bump when the stack moves.
#   KIBANA_MCP_PULL_BUDGET     seconds allowed for all image pulls. Default: 210.

set -uo pipefail   # deliberately not -e: a failed step must not fail session start

log_file=/var/log/mcp-for-kibana-cloud-setup.log
touch "$log_file" 2>/dev/null || log_file=/tmp/mcp-for-kibana-cloud-setup.log
exec > >(tee -a "$log_file") 2>&1

log() { printf '[cloud-setup] %s\n' "$*"; }

registry="docker.elastic.co"
# The four images the compose files name: elastic-start-local/docker-compose.yml (ES, Kibana),
# docker-compose.apm.yml (APM server, used by `make stack-start`), docker-compose.fleet.yml
# (elastic-agent, used by `make fleet-ephemeral`).
images="elasticsearch/elasticsearch kibana/kibana apm/apm-server elastic-agent/elastic-agent"
fallback_version="9.4.3"
budget="${KIBANA_MCP_PULL_BUDGET:-210}"

# --- Version: read from the repo rather than duplicated here. raw.githubusercontent.com is
# --- on the Trusted default allowlist, so this works at any access level that keeps it.
versions="${KIBANA_MCP_STACK_VERSIONS:-}"
if [ -z "$versions" ]; then
  env_example=$(curl -fsSL --max-time 20 \
    https://raw.githubusercontent.com/pedro-angel/mcp-for-kibana/main/elastic-start-local/.env.example 2>/dev/null)
  versions=$(printf '%s\n' "$env_example" | sed -n 's/^ES_LOCAL_VERSION=//p' | tr -d '"' | head -1)
  if [ -n "$versions" ]; then
    log "stack version ${versions} (read from elastic-start-local/.env.example)"
  else
    versions="$fallback_version"
    log "could not read the repo's ES_LOCAL_VERSION -- falling back to ${versions}"
  fi
else
  log "stack version(s) ${versions} (from KIBANA_MCP_STACK_VERSIONS)"
fi

# The budget feeds arithmetic below. Under `set -u` a non-numeric value would abort the
# script -- precisely the non-zero exit it must never produce. It comes from the
# environment's variables field, so validate rather than trust.
case "$budget" in
  ''|*[!0-9]*)
    log "KIBANA_MCP_PULL_BUDGET='${budget}' is not a whole number of seconds -- using 210"
    budget=210
    ;;
esac

image_count=$(wc -w <<<"$images")

# --- gh: listed in the session image's utilities, but this repo is maintained through
# --- `gh api`, `gh run` and `gh release`, so verify rather than assume.
if command -v gh >/dev/null 2>&1; then
  log "gh $(gh --version 2>/dev/null | head -1 | awk '{print $3}') already present"
else
  log "installing gh"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 \
    || log "apt-get update failed -- continuing without it"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gh >/dev/null 2>&1 \
    || log "gh install failed -- built-in GitHub tools still work"
fi

# --- Docker: required for every live tier (contract, streams-ephemeral, fleet-ephemeral).
dockerd_log=/var/log/mcp-for-kibana-dockerd.log
touch "$dockerd_log" 2>/dev/null || dockerd_log=/tmp/mcp-for-kibana-dockerd.log

wait_for_daemon() {
  for _ in $(seq 1 "$1"); do
    docker info >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

if ! docker info >/dev/null 2>&1; then
  # PID 1 on the session VM is a Firecracker init shim, not systemd, so `service docker
  # start` is a silent no-op there and waiting on it only burns pull budget.
  if [ -d /run/systemd/system ]; then
    log "docker daemon not responding -- trying the service wrapper"
    service docker start 2>&1 | sed 's/^/[cloud-setup]   service: /' || true
    wait_for_daemon 10 || true
  fi
fi
if ! docker info >/dev/null 2>&1; then
  log "launching dockerd directly"
  nohup dockerd >>"$dockerd_log" 2>&1 &
  wait_for_daemon 20 || true
fi
if ! docker info >/dev/null 2>&1; then
  log "docker unavailable at setup time -- skipping the image pre-pull"
  log "dockerd's own last words follow. A permissions or cgroup error means this"
  log "environment class cannot run nested containers and no script can fix it;"
  log "anything else is a start problem that can be fixed:"
  tail -n 20 "$dockerd_log" 2>/dev/null | sed 's/^/[cloud-setup]   dockerd: /'
  [ -s "$dockerd_log" ] || log "  dockerd wrote nothing -- it never started"
  exit 0
fi
log "docker daemon up (server $(docker version --format '{{.Server.Version}}' 2>/dev/null))"

# --- Pre-pull the stack images so the snapshot carries them.
# Elastic images come from docker.elastic.co, which is NOT on the Trusted default allowlist.
# The environment must use Custom network access covering *.elastic.co -- the wildcard
# matters: naming docker.elastic.co alone still fails every pull, because the registry's 401
# challenge sends the client to docker-auth.elastic.co for a token and the proxy refuses the
# CONNECT to that separate host.
deadline=$(( SECONDS + budget ))
pulled=0
missed=0

for version in $versions; do
  remaining=$(( deadline - SECONDS ))
  if [ "$remaining" -le 15 ]; then
    log "budget of ${budget}s exhausted before ${version} -- skipping it"
    missed=$(( missed + image_count ))
    continue
  fi
  log "pulling ${version} images in parallel (${remaining}s of budget left)"
  pids=""
  for image in $images; do
    timeout "$remaining" docker pull --quiet "${registry}/${image}:${version}" >/dev/null 2>&1 &
    pids="${pids} $!"
  done
  for pid in $pids; do
    if wait "$pid"; then
      pulled=$(( pulled + 1 ))
    else
      missed=$(( missed + 1 ))
    fi
  done
done

log "cached ${pulled} image(s); ${missed} left to pull on demand"
docker images --format '{{.Repository}}:{{.Tag}} ({{.Size}})' 2>/dev/null \
  | grep "^${registry//./\\.}" | sed 's/^/[cloud-setup]   /' || true
log "finished in ${SECONDS}s (this transcript: ${log_file})"

exit 0
