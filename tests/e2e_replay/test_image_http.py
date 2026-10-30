"""The image's HTTP credential path, without a key (D62).

Runs only under scripts/checks/image-replay.sh (marker `image_replay`, which
the default addopts and `-m e2e_replay` leave out): the replay there proves
that `Authorization: ApiKey <key>` authenticates every call on the built
image; this proves the other half — a call without the header, or with
another scheme, is refused with the error that names the fix, even when the
container holds a KIBANA_API_KEY (in HTTP mode the environment key is a
shared credential, used only with KIBANA_MCP_ALLOW_ENV_KEY_HTTP).
"""

import os

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e_replay._image import image_server

pytestmark = pytest.mark.image_replay


@pytest.fixture(scope="module")
def image_url():
    image = os.environ.get("KIBANA_MCP_SERVER_IMAGE")
    if not image:
        pytest.fail(
            "KIBANA_MCP_SERVER_IMAGE is unset — this module runs under "
            "scripts/checks/image-replay.sh <image>, never on its own"
        )
    env = {
        "KIBANA_URL": os.environ["KIBANA_URL"],
        # Present on purpose: HTTP mode must not fall back to it.
        "KIBANA_API_KEY": os.environ["KIBANA_TEST_API_KEY"],
        "KIBANA_MCP_TOOLBOXES": "platform-health",
    }
    with image_server(image, env) as url:
        yield url


async def _call(url, headers):
    async with Client(StreamableHttpTransport(url, headers=headers)) as client:
        names = {t.name for t in await client.list_tools()}
        assert "get_kibana_status" in names  # listing needs no credential
        result = await client.call_tool("get_kibana_status", {}, raise_on_error=False)
        return result.is_error, "".join(b.text for b in result.content if hasattr(b, "text"))


async def test_a_call_without_the_header_is_refused_naming_the_fix(image_url):
    is_error, body = await _call(image_url, {})
    assert is_error, f"a call with no Authorization header succeeded on the image: {body[:300]}"
    assert "no Kibana credentials" in body and "Authorization: ApiKey <key>" in body, body


async def test_another_scheme_is_refused_naming_the_fix(image_url):
    is_error, body = await _call(image_url, {"Authorization": "Bearer not-a-kibana-key"})
    assert is_error, f"a Bearer header was accepted on the image: {body[:300]}"
    assert "'ApiKey <key>' scheme" in body, body


async def test_the_header_authenticates_the_same_call(image_url):
    is_error, body = await _call(
        image_url, {"Authorization": f"ApiKey {os.environ['KIBANA_TEST_API_KEY']}"})
    assert not is_error, (
        "the image refused a call that carries `Authorization: ApiKey <seed key>`: the "
        "per-request credential path (adapters/mcp/auth.py resolve_api_key, "
        "server.build_gateway_factory) no longer reaches Kibana with the header's key "
        f"over HTTP — fix it there and rebuild the image; body: {body[:500]}"
    )
    assert '"version":' in body, body[:500]
