"""Prove the full HTTP path: header -> parse -> per-request gateway."""

import asyncio
import contextlib
import socket

import pytest
from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.adapters.mcp.auth import resolve_api_key
from tests.fakes import FakeGateway

PORT = 8765


def _free_port() -> int:
    """uvicorn.Server.serve() doesn't close its listening socket when its
    task is cancelled (no try/finally around main_loop reaches shutdown()),
    so a hardcoded port can still be bound from the previous test. Ask the
    OS for a free one each time instead."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
async def http_server():
    global PORT
    PORT = _free_port()
    seen_keys: list[str] = []

    def gateway_factory(space=None):
        seen_keys.append(resolve_api_key(None))
        return FakeGateway()

    mcp = build_server(Settings(tier=Tier.READ), gateway_factory)
    task = asyncio.create_task(
        mcp.run_async(transport="http", host="127.0.0.1", port=PORT, stateless_http=True)
    )
    await asyncio.sleep(0.5)  # let uvicorn bind
    yield seen_keys
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_per_request_key_reaches_gateway(http_server):
    seen_keys = http_server
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(
        f"http://127.0.0.1:{PORT}/mcp", headers={"Authorization": "ApiKey test-key-123"}
    )
    async with Client(transport) as client:
        result = await client.call_tool("list_data_views", {})
        assert result.data[0]["name"] == "flights"
    assert seen_keys == ["test-key-123"]


async def test_missing_key_is_actionable_tool_error(http_server):
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(f"http://127.0.0.1:{PORT}/mcp")
    async with Client(transport) as client:
        with pytest.raises(Exception, match="ApiKey"):
            await client.call_tool("list_data_views", {})
