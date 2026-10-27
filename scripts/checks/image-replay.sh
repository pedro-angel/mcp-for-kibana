#!/bin/sh
# The replay tier against the BUILT DOCKER IMAGE over HTTP (D62) — what an
# HTTP user of the published image runs: the image's Python, its
# `uv sync --no-dev` venv, the streamable-HTTP transport and the per-request
# `Authorization: ApiKey <key>` credential path. Every transcript is replayed
# into its own container of <image> (tests/e2e_replay/test_replay.py honours
# KIBANA_MCP_SERVER_IMAGE), started with the seed's Kibana URL and no API key,
# and the client sends the seed key in the header; tests/e2e_replay/
# test_image_http.py adds the calls without the header (or with another
# scheme) that must be refused naming the fix.
# Needs a seeded stack, like e2e_replay_green, and the image built
# (scripts/checks/image-build.sh <image>). usage: scripts/checks/image-replay.sh <image>
set -eu
cd "$(dirname "$0")/../.."
IMAGE="${1:?usage: image-replay.sh <image>}"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "FAIL: no image $IMAGE — build it first: scripts/checks/image-build.sh $IMAGE" >&2
  exit 1
fi
# Evidence in the gate log that the suite drives the image, and on which versions.
docker run --rm --entrypoint /app/.venv/bin/python "$IMAGE" -c \
  'import sys, importlib.metadata as m; print("image python", sys.version.split()[0], "| mcp-for-kibana", m.version("mcp-for-kibana"), "| fastmcp", m.version("fastmcp"), "| kibana-py", m.version("kibana-py"))'
KIBANA_MCP_SERVER_IMAGE="$IMAGE" uv run pytest -m "e2e_replay or image_replay" -q
