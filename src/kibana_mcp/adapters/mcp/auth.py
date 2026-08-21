"""Per-request credential resolution. HTTP mode: 'Authorization: ApiKey <key>'
per request, never session state (matches Elastic's own Kibana MCP convention
and the MCP spec's stateless direction). stdio mode: KIBANA_API_KEY env."""

from collections.abc import Mapping

from fastmcp.server.dependencies import get_http_headers

from kibana_mcp.core.errors import KibanaAuthError

_GUIDANCE = "send 'Authorization: ApiKey <key>' or set KIBANA_API_KEY"


def parse_api_key(headers: Mapping[str, str], fallback: str | None) -> str:
    auth = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    if auth:
        scheme, _, key = auth.partition(" ")
        if scheme.lower() != "apikey" or not key.strip():
            raise KibanaAuthError(
                f"Authorization header must use the 'ApiKey <key>' scheme; {_GUIDANCE}"
            )
        return key.strip()
    if fallback:
        key = fallback.strip()
        if not key or any(c in key for c in "\r\n"):
            raise KibanaAuthError(
                f"the configured KIBANA_API_KEY is not a valid API key value; {_GUIDANCE}"
            )
        return key
    raise KibanaAuthError(f"no Kibana credentials: {_GUIDANCE}")


def resolve_api_key(fallback: str | None) -> str:
    # get_http_headers() strips the 'authorization' header by default (it's meant
    # for safely forwarding headers to downstream services); opt it back in here.
    return parse_api_key(get_http_headers(include={"authorization"}), fallback)
