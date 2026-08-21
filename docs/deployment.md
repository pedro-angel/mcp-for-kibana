# Deployment

## stdio vs streamable HTTP

mcp-for-kibana has two run modes sharing one codebase
(`KIBANA_MCP_TRANSPORT`, see [Configuration](configuration.md)):

- **stdio** (default) — one server process per user, launched directly by
  the MCP client (Claude Code, LM Studio, …) over stdin/stdout. The
  process's own `KIBANA_API_KEY` env var is that user's Kibana identity for
  the life of the process. This is the mode the
  [Getting started](user-guide.md) guide uses.
- **streamable HTTP** — one long-running server serving multiple users
  concurrently, each request carrying its own credentials (see
  [per-request auth model](#per-request-auth-model) below). This is the
  mode the Docker image always runs (`KIBANA_MCP_TRANSPORT=http` is baked
  in; see [Docker](#docker)).

Both modes run through the same `build_server(settings, gateway_factory)`
composition root (`server.py`) and register the exact same tools — only the
transport and credential-resolution path differ. HTTP mode runs
`stateless_http=True`: no session state is kept between requests, which is
what makes "each request carries its own key" both possible and required.

## Docker

The container always runs HTTP transport, stateless, on port 8000.
`KIBANA_API_KEY` is not needed at the container level: HTTP mode expects
each caller to send their own key per request, and by default ignores
`KIBANA_API_KEY` even if it's set (see
[`KIBANA_MCP_ALLOW_ENV_KEY_HTTP`](configuration.md#kibana_mcp_allow_env_key_http-explained)
to opt back into it as a shared fallback):

```bash
docker run -p 8000:8000 -e KIBANA_URL=https://your-kibana.example.com \
  ghcr.io/pedro-angel/mcp-for-kibana

curl http://localhost:8000/mcp \
  -H 'Authorization: ApiKey <your Kibana API key>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The image (`docker/Dockerfile`) is a two-stage build: `uv sync --frozen
--no-dev --no-editable` in the build stage, then only the built `.venv` is
copied into a slim `python:3.14-slim` runtime stage. It runs as a
**non-root user** (`mcp-for-kibana`, uid 10001) — `USER mcp-for-kibana` is set
before `ENTRYPOINT`, so the container process never runs as root even if
someone runs it without extra `--user` flags. The published image name is
**`ghcr.io/pedro-angel/mcp-for-kibana`** (CI's `image` job builds and, on
pushes to `main`, publishes `sha-<commit>` and `latest` tags to GHCR — see
`.github/workflows/checks.yml`).

## Per-request auth model

Every HTTP request must carry its own Kibana identity:

```
Authorization: ApiKey <caller's own Kibana API key>
```

`adapters/mcp/auth.py`'s `parse_api_key` reads the `Authorization` header
per request (`fastmcp`'s `get_http_headers` normally strips it for safe
downstream forwarding; the server explicitly opts it back in), requires the
`ApiKey` scheme, and rejects anything else — malformed scheme, or no header
at all with no fallback configured — with a clear auth error rather than a
silent 401 pass-through. This means:

- Kibana's own RBAC applies per caller, not per server — a caller can only
  do what *their* Kibana API key permits.
- Kibana's audit log records the real caller's identity on every write,
  since the API key used is theirs.
- There is deliberately **no multi-tenancy state** on the server side: no
  sessions, no stored credentials, no per-tenant routing. The request *is*
  the tenant boundary.
- Exception: saved-objects export handles live in one server-side directory
  shared by all callers — a handle is a bearer token for that export, and
  the 20-file retention cap is global, so concurrent HTTP callers can expire
  each other's handles.

## Env-key opt-in

See
[`KIBANA_MCP_ALLOW_ENV_KEY_HTTP`](configuration.md#kibana_mcp_allow_env_key_http-explained)
for the full explanation of why the env-var fallback is off by default in
HTTP mode and what turning it on means.

## TLS at the proxy

The server itself speaks plain HTTP — it does not terminate TLS. Run it
behind a reverse proxy (nginx, Traefik, your cloud load balancer, …) that
terminates TLS and forwards to the container's port 8000. This keeps the
container image simple (no certificate management inside it) and matches
how the container is meant to be deployed: as one stateless service behind
whatever ingress/proxy layer your environment already has, not as a
public-facing TLS endpoint on its own.

## Security posture summary

- No credentials are stored anywhere on the server (stdio: process env,
  scoped to that one user's session; HTTP: per-request header, never
  persisted).
- Deletion tools (`delete_dashboard`, `delete_panel`) are invisible to the
  model unless `KIBANA_MCP_TIER=destructive` is explicitly set — see
  [Tool reference](tools.md#tiers-and-annotations).
- The container runs as a non-root user and carries no TLS material.
- Put TLS, rate limiting, and network-level access control at the proxy —
  they are out of scope for the server itself.
- The MCP endpoint itself performs no authentication — tools/list,
  resources/read and the docs resources answer any peer that can reach the
  port; network-level access control at the proxy is mandatory, doubly so
  with `KIBANA_MCP_ALLOW_ENV_KEY_HTTP=true`.
