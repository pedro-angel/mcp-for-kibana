#!/usr/bin/env bash
# Efficiency study driver: 30 blocks x 6 cells (3 models x 2 arms) = 180 runs, one run per cell per block.
# Models run haiku, sonnet, opus; arm order alternates per block (odd: with-mcp then no-mcp, even: no-mcp then with-mcp).
# Usage (from the repo root): scripts/experiment/runs/efficiency/full/run_blocks.sh FIRST LAST   (1 <= FIRST <= LAST <= 30)
set -euo pipefail

USAGE="usage: $0 FIRST LAST  (integers, 1 <= FIRST <= LAST <= 30)"
[ "$#" -eq 2 ] || { echo "$USAGE" >&2; exit 2; }
FIRST=$1; LAST=$2
case "$FIRST$LAST" in *[!0-9]*|'') echo "$USAGE" >&2; exit 2 ;; esac
FIRST=$((10#$FIRST)); LAST=$((10#$LAST))
if [ "$FIRST" -lt 1 ] || [ "$LAST" -gt 30 ] || [ "$FIRST" -gt "$LAST" ]; then
  echo "$USAGE" >&2; exit 2
fi
[ -f scripts/experiment/efficiency_run.py ] || { echo "FAIL: run from the repo root" >&2; exit 2; }

OUT=scripts/experiment/runs/efficiency/full
MODELS=(claude-haiku-5-5 claude-sonnet-5-5 claude-opus-5-5)
failures=0

for ((b = FIRST; b <= LAST; b++)); do
  block=$(printf 'b%02d' "$b")
  if ((b % 2 == 1)); then arms=(with-mcp no-mcp); else arms=(no-mcp with-mcp); fi
  echo "=== block $block ($(date -u +%FT%TZ)) arm order: ${arms[*]} ==="
  for model in "${MODELS[@]}"; do
    for arm in "${arms[@]}"; do
      if ! uv run python scripts/experiment/efficiency_run.py \
          --model "$model" --arm "$arm" --runs 1 --block "$block" --out "$OUT"; then
        failures=$((failures + 1))
        echo "RUN FAILED (exit non-zero): block=$block model=$model arm=$arm — continuing" >&2
      fi
    done
  done
done

echo "done: blocks $FIRST..$LAST, $failures failed run(s)"
