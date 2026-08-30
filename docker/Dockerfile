FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS build
COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
# the wheel force-includes these as in-band MCP docs resources
COPY docs/user-guide.md docs/tools.md ./docs/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5
RUN useradd --create-home --uid 10001 mcp-for-kibana
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
USER mcp-for-kibana
LABEL org.opencontainers.image.source="https://github.com/pedro-angel/mcp-for-kibana" \
      org.opencontainers.image.description="MCP server for Kibana" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV KIBANA_MCP_TRANSPORT=http \
    KIBANA_MCP_HOST=0.0.0.0 \
    KIBANA_MCP_PORT=8000
EXPOSE 8000
# Self-describing liveness for whatever runs the image (compose, Swarm, ECS;
# Kubernetes uses its own probes and ignores this). Deliberately the same
# assertion scripts/checks/image-smoke.sh makes — "the server is listening and
# answers HTTP" — not a health contract about Kibana connectivity: the server
# starts fine with Kibana unreachable, and reporting it unhealthy for that
# would make orchestrators restart-loop a container that has nothing wrong
# with it. Any HTTP status counts; only a connection failure is unhealthy.
#
# Uses the venv's own python, so the image gains no curl/wget and no new
# packages, and the check inherits the same interpreter the server runs on.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import contextlib,os,urllib.error,urllib.request\nport=os.environ.get('KIBANA_MCP_PORT','8000')\nwith contextlib.suppress(urllib.error.HTTPError): urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2)"]
ENTRYPOINT ["/app/.venv/bin/mcp-for-kibana"]
