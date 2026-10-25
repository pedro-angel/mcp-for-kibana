#!/bin/sh
# Build the mcp-for-kibana docker image. usage: image-build.sh <tag>
# The one build recipe: `make build` and the DoD gate's image_smoke both call it.
#
# Inside a TLS-intercepting sandbox (Claude Code cloud session) the build's
# `uv sync` does not trust the proxy CA the VM trusts and cannot reach the
# proxy on the VM's loopback, so it fails "invalid peer certificate". Only when
# that CA exists (as scripts/stack.sh decides) the build runs on the host
# network with the proxy variables and the CA as a BuildKit secret, which the
# Dockerfile's sync step reads from /run/secrets and no layer keeps (the build
# stage is not part of the image). On a laptop or a GitHub runner the CA is
# absent and the build is the plain one.
set -eu
TAG="${1:?usage: image-build.sh <tag>}"
PROXY_CA="${KIBANA_MCP_PROXY_CA:-/root/.ccr/ca-bundle.crt}"

if [ -f "$PROXY_CA" ]; then
  echo "proxy CA at $PROXY_CA — building with it as a secret, on the host network"
  exec docker build --network host --secret id=proxy_ca,src="$PROXY_CA" \
    --build-arg HTTPS_PROXY --build-arg HTTP_PROXY --build-arg NO_PROXY \
    -f docker/Dockerfile -t "$TAG" .
fi
exec docker build -f docker/Dockerfile -t "$TAG" .
