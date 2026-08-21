#!/usr/bin/env bash
# Study L driver — the server-usability study (local models).
#
#   scripts/experiment/run_study_l.sh
#
# Per model: unload everything, one TIMED explicit load (its own JSONL line —
# load time is not run time, the baseline conflated them in run 1), then
# $RUNS gate runs (failures are data; the driver never stops on one). The
# gemma-4-31b attempt runs ONCE at the end to record the documented
# ENVIRONMENT failure (86 GB at default context on a 64 GB machine) rather
# than five times.
#
# Preconditions: stack up + seeded; LM Studio running with the
# mcp-for-kibana entry exposing dashboards,data-management,platform-admin.
set -u
cd "$(dirname "$0")/../.."

MODELS=(
  openai/gpt-oss-20b
  google/gemma-4-12b-qat
  google/gemma-4-26b-a4b-qat
  mistralai/devstral-small-2-2512
  mistralai/ministral-3-3b
  nvidia/nemotron-3-nano-omni
  prism-ml/bonsai-27b
  mistralai/magistral-small-2509
  mistralai/ministral-3-14b-reasoning
  nvidia/nemotron-3-nano-4b
  ibm/granite-4-h-tiny
)
RUNS="${RUNS:-5}"
OUT="scripts/experiment/runs/study-l.jsonl"
export KIBANA_MCP_EXPERIMENT_LOG="$PWD/$OUT"   # pin study data to the study file

record_load() { # model seconds ok
  python3 - "$1" "$2" "$3" "$OUT" <<'PY'
import json, sys
from datetime import UTC, datetime
model, secs, ok, out = sys.argv[1:5]
with open(out, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "study": "L-load", "model": model, "load_s": int(secs),
        "load_ok": ok == "ok", "ts": datetime.now(UTC).isoformat(),
    }, sort_keys=True) + "\n")
PY
}

for m in "${MODELS[@]}"; do
  echo "==== $m ===="
  lms unload --all >/dev/null 2>&1 || true
  t0=$(date +%s)
  if lms load "$m" -y >/dev/null 2>&1; then
    record_load "$m" $(( $(date +%s) - t0 )) ok
  else
    record_load "$m" $(( $(date +%s) - t0 )) fail
    echo "LOAD FAILED for $m — skipping its runs (recorded)"
    continue
  fi
  for i in $(seq 1 "$RUNS"); do
    echo "-- $m run $i/$RUNS --"
    LMSTUDIO_MODEL="$m" uv run pytest tests/e2e/test_lmstudio_space.py -m e2e -q || true
  done
done

# gemma-4-31b: one documented attempt (decision: "same 12 as baseline" —
# it stays unexercised, with the environment failure on record, not asserted 5x).
echo "==== google/gemma-4-31b-qat (single documented attempt) ===="
lms unload --all >/dev/null 2>&1 || true
LMSTUDIO_MODEL=google/gemma-4-31b-qat uv run pytest tests/e2e/test_lmstudio_space.py -m e2e -q || true

lms unload --all >/dev/null 2>&1 || true
echo "Study L driver complete."
