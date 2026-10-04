#!/bin/sh
# The replay tier against the INSTALLED wheel — what a PyPI user runs, not the
# checkout. Builds the wheel, installs it into a fresh venv with pip (so the
# dependencies resolve from the pyproject ranges at install time, the way a
# user's install resolves them; uv.lock does not apply), then runs the
# e2e_replay suite with the server launched from that venv
# (tests/e2e_replay/test_replay.py honours KIBANA_MCP_SERVER_BIN).
# Needs a seeded stack, like e2e_replay_green. usage: scripts/checks/wheel-replay.sh
set -eu
cd "$(dirname "$0")/../.."
dist=$(mktemp -d)
venv=$(mktemp -d)
trap 'rm -rf "$dist" "$venv"' EXIT
uv build --wheel --out-dir "$dist"
python3 -m venv "$venv"
"$venv/bin/pip" install --quiet "$dist"/*.whl
# Evidence in the gate log that the suite drives the wheel, and on which versions.
"$venv/bin/python" -c 'import kibana_mcp; print("installed kibana_mcp", kibana_mcp.__version__, "at", kibana_mcp.__file__)'
"$venv/bin/pip" list 2>/dev/null | grep -E '^(fastmcp|kibana-py|mcp|pydantic) ' || true
KIBANA_MCP_SERVER_BIN="$venv/bin/mcp-for-kibana" uv run pytest -m e2e_replay -q
