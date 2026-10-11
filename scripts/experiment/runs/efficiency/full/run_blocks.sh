#!/usr/bin/env bash
# Efficiency study driver: 30 blocks x 6 cells (3 models x 2 arms) = 180 runs, one run per cell per block.
# Models run haiku, sonnet, opus; arm order alternates per block (odd: with-mcp then no-mcp, even: no-mcp then with-mcp).
# Usage (from the repo root): scripts/experiment/runs/efficiency/full/run_blocks.sh FIRST LAST   (1 <= FIRST <= LAST <= 30)
#
# Additive options (the call above is unchanged):
#   --three-arms   9 cells per block (3 models x with-mcp, no-mcp, with-mcp-es); the arm order
#                  rotates by block: b%3==1 with-mcp no-mcp with-mcp-es, b%3==2 no-mcp with-mcp-es
#                  with-mcp, b%3==0 with-mcp-es with-mcp no-mcp, so each arm takes each position
#                  equally often over every 3 blocks.
#   -- ARGS...     passed to every efficiency_run.py call after the driver's own flags (a later
#                  --out wins), e.g. -- --live-telemetry --dataset mcp-for-kibana-live --out DIR
set -euo pipefail

USAGE="usage: $0 [--three-arms] FIRST LAST [-- EFFICIENCY_RUN_ARGS...]  (integers, 1 <= FIRST <= LAST <= 30)"
THREE=0
if [ "${1:-}" = "--three-arms" ]; then THREE=1; shift; fi
[ "$#" -ge 2 ] || { echo "$USAGE" >&2; exit 2; }
FIRST=$1; LAST=$2; shift 2
EXTRA=()
if [ "$#" -gt 0 ]; then
  [ "$1" = "--" ] || { echo "$USAGE" >&2; exit 2; }
  shift; EXTRA=("$@")
fi
case "$FIRST$LAST" in *[!0-9]*|'') echo "$USAGE" >&2; exit 2 ;; esac
FIRST=$((10#$FIRST)); LAST=$((10#$LAST))
if [ "$FIRST" -lt 1 ] || [ "$LAST" -gt 30 ] || [ "$FIRST" -gt "$LAST" ]; then
  echo "$USAGE" >&2; exit 2
fi
[ -f scripts/experiment/efficiency_run.py ] || { echo "FAIL: run from the repo root" >&2; exit 2; }

OUT=scripts/experiment/runs/efficiency/full
MODELS=(claude-haiku-5-5 claude-sonnet-5-5 claude-opus-5-5)
THREE_ARMS=(with-mcp no-mcp with-mcp-es)
failures=0

for ((b = FIRST; b <= LAST; b++)); do
  block=$(printf 'b%02d' "$b")
  if ((THREE)); then
    r=$(((b - 1) % 3))
    arms=("${THREE_ARMS[@]:r}" "${THREE_ARMS[@]:0:r}")
  elif ((b % 2 == 1)); then arms=(with-mcp no-mcp); else arms=(no-mcp with-mcp); fi
  echo "=== block $block ($(date -u +%FT%TZ)) arm order: ${arms[*]} ==="
  for model in "${MODELS[@]}"; do
    for arm in "${arms[@]}"; do
      if ! uv run python scripts/experiment/efficiency_run.py \
          --model "$model" --arm "$arm" --runs 1 --block "$block" --out "$OUT" ${EXTRA[@]+"${EXTRA[@]}"}; then
        failures=$((failures + 1))
        echo "RUN FAILED (exit non-zero): block=$block model=$model arm=$arm — continuing" >&2
      fi
    done
  done
done

echo "done: blocks $FIRST..$LAST, $failures failed run(s)"
