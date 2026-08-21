#!/bin/sh
# Stop script for start-local (mcp-for-kibana delta: explicit -p mcp-for-kibana-stack).
# More information: https://github.com/elastic/start-local
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"
docker compose -p mcp-for-kibana-stack stop
