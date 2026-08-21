#!/bin/sh
# Smoke-test a mcp-for-kibana docker image: the container must stay running and
# answer HTTP on its port. "Any HTTP response" is the assertion (server
# listening), not a health contract. usage: image-smoke.sh <image>
# Portable POSIX sh; zero deps beyond docker + curl.
# No --rm on docker run: auto-remove would destroy the failure evidence
# (docker logs); the trap owns removal on every path.
set -eu
IMAGE="${1:?usage: image-smoke.sh <image>}"
PORT="${SMOKE_PORT:-18300}"
NAME=mcp-for-kibana-smoke

cleanup() { docker rm -f "$NAME" 2>/dev/null || true; }
# A crashed prior run must not wedge every future run.
cleanup >/dev/null
trap cleanup EXIT INT TERM

docker run -d --name "$NAME" -p "$PORT:8000" "$IMAGE" >/dev/null

# 15 probes at 1s intervals — the server listens in under 5s, so this is 3x margin.
i=0
while [ "$i" -lt 15 ]; do
  sleep 1
  if ! docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; then
    echo "FAIL: container exited during smoke" >&2
    docker logs "$NAME" >&2 || true
    exit 1
  fi
  # --max-time caps each probe so an accept-but-never-answer port can't
  # hang forever (~15s nominal; <=45s worst-case across 15 probes).
  if curl -s -o /dev/null --max-time 2 "http://localhost:$PORT/"; then
    echo "smoke OK: $IMAGE answers on :$PORT"
    exit 0
  fi
  i=$((i+1))
done
echo "FAIL: no HTTP response on :$PORT after 15 probes" >&2
docker logs "$NAME" >&2 || true
exit 1
