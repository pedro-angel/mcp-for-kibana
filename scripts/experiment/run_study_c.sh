#!/usr/bin/env bash
# Study C driver — the server-usability study (Claude arms).
#
#   scripts/experiment/run_study_c.sh
#
# 5 blocks x 9 cells (3 models x 3 arms), ONE run per cell per block, with
# the ARM ORDER ROTATED between blocks so time-of-day and API-load variance
# spreads across arms (plan, Phase 3). Failures are data; the driver never
# stops on one. Zero-denial enforcement is inside the runner.
set -u
cd "$(dirname "$0")/../.."

MODELS=(claude-haiku-4-5 claude-sonnet-5 claude-opus-5)
ARMS=(with-mcp no-mcp with-mcp-directed)
BLOCKS="${BLOCKS:-5}"

for b in $(seq 1 "$BLOCKS"); do
  echo "==== block b$b ===="
  for mi in 0 1 2; do
    model="${MODELS[$mi]}"
    for k in 0 1 2; do
      arm="${ARMS[$(( (k + b - 1) % 3 ))]}"   # rotate arm order per block
      echo "-- b$b $model $arm --"
      uv run python scripts/experiment/claude_arm.py \
        --model "$model" --arm "$arm" --runs 1 --block "b$b" || true
    done
  done
done
echo "Study C driver complete."
