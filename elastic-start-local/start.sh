#!/bin/sh
# Start script for start-local (near-copy; mcp-for-kibana deltas: no trial/expire
# branch, no .env.local sourcing, explicit -p mcp-for-kibana-stack).
# More information: https://github.com/elastic/start-local
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"
. ./.env
# Check disk space
available_gb=$(($(df -k / | awk 'NR==2 {print $4}') / 1024 / 1024))
required=$(echo "${ES_LOCAL_DISK_SPACE_REQUIRED}" | grep -Eo '[0-9]+')
if [ "$available_gb" -lt "$required" ]; then
  echo "----------------------------------------------------------------------------"
  echo "WARNING: Disk space is below the ${required} GB limit. Elasticsearch will be"
  echo "executed in read-only mode. Please free up disk space to resolve this issue."
  echo "----------------------------------------------------------------------------"
  # Only prompt when stdin is a terminal; under make/CI (non-interactive) a bare
  # `read` returns EOF and set -eu would abort up before the stack even starts.
  if [ -t 0 ]; then
    echo "Press ENTER to confirm."
    # shellcheck disable=SC2034
    read -r line
  fi
fi
docker compose -p mcp-for-kibana-stack up --wait
