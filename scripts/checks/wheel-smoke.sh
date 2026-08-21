#!/bin/sh
# Build the wheel and prove an installed (non-checkout) consumer gets the
# in-band docs resources — the repo-relative fallback must not be the thing
# making them work.
set -eu
dist=$(mktemp -d)
venv=$(mktemp -d)
trap 'rm -rf "$dist" "$venv"' EXIT
uv build --out-dir "$dist"
unzip -l "$dist"/*.whl | grep -q 'kibana_mcp/_docs/user-guide.md'
unzip -l "$dist"/*.whl | grep -q 'kibana_mcp/_docs/tools.md'
python3 -m venv "$venv"
"$venv/bin/pip" install --quiet "$dist"/*.whl
cd /  # off the repo so the fallback cannot rescue a broken package
"$venv/bin/python" -c "
from kibana_mcp.adapters.mcp import docs_resources
assert len(docs_resources.user_guide()) > 1000
assert len(docs_resources.tools()) > 1000
print('wheel-smoke: OK')
"
