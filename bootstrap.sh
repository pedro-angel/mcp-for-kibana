#!/bin/sh
# One-command setup: get pre-commit and install the git hooks, without polluting your
# system. Order of preference, so it works on almost any machine:
#   1. prek        — a single static binary, ZERO Python (fastest, nothing to bootstrap)
#   2. pre-commit  — if one is already on PATH (pipx / brew / system)
#   3. a project-local .venv with a pinned pre-commit — needs only python3
#
# Usage:  ./bootstrap.sh
set -eu

PRECOMMIT_VERSION="4.6.0"   # pinned for reproducibility; bump deliberately

if command -v prek >/dev/null 2>&1; then
  echo "==> using prek (no Python needed)"
  prek install --install-hooks
  prek run --all-files
  exit 0
fi

if command -v pre-commit >/dev/null 2>&1; then
  echo "==> using pre-commit already on PATH ($(pre-commit --version))"
  pre-commit install --install-hooks
  pre-commit run --all-files
  exit 0
fi

echo "==> no prek/pre-commit found — bootstrapping a local .venv"
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: need python3, or install prek (https://github.com/j178/prek) and re-run." >&2
  exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet "pre-commit==${PRECOMMIT_VERSION}"
.venv/bin/pre-commit install --install-hooks
.venv/bin/pre-commit run --all-files

echo ""
echo "Done. The hooks are installed and will run on every commit."
echo "To run all checks manually later:  .venv/bin/pre-commit run --all-files"
echo "(or 'source .venv/bin/activate' once, then just 'pre-commit run --all-files')"
