"""The streams toolbox: 12 tools across read/write/destructive tiers over Kibana
Streams (Tech-Preview). Driven through the in-memory fastmcp Client against a FakeGateway."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.models import Stream
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.streams.toolbox import StreamsToolbox
from tests.fakes import FakeGateway

STREAMS_READ = {"list_streams", "get_stream", "get_stream_ingest"}
STREAMS_WRITE = {
    "enable_streams", "resync_streams", "fork_stream",
    "set_stream_processing", "deactivate_fork",
}
STREAMS_DESTRUCTIVE = {
    "set_stream_retention", "delete_stream", "disable_streams", "activate_fork",
}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    StreamsToolbox().register(server, deps)
    return server


async def test_streams_tools_are_tiered(mcp):
    # The unit fixture registers with no tier gating, so all 12 tools appear.
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == STREAMS_READ | STREAMS_WRITE | STREAMS_DESTRUCTIVE
    for n in STREAMS_READ:
        assert tools[n].annotations.readOnlyHint is True
    for n in STREAMS_DESTRUCTIVE:
        assert tools[n].annotations.destructiveHint is True
    for n in STREAMS_WRITE:  # both directions: write tools must NOT carry the destructive hint
        assert tools[n].annotations.destructiveHint is not True


async def test_enable_streams_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("enable_streams", {})
    assert r.data["result"] == "noop"


async def test_fork_stream_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("fork_stream", {
            "parent_name": "logs.ecs", "child_name": "logs.ecs.app",
            "condition_field": "service.name", "condition_value": "app"})
    assert r.data["result"] == "created"


async def test_fork_stream_bad_prefix_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("fork_stream", {
                "parent_name": "logs.ecs", "child_name": "other.app",
                "condition_field": "service.name", "condition_value": "app"})


async def test_set_stream_processing_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("set_stream_processing", {
            "name": "logs.ecs", "steps": [{"action": "set", "to": "x", "value": "1"}]})
    assert r.data["processing_step_count"] == 1


def _forked_child(name="logs.ecs.app"):
    return Stream(
        name=name, type="wired", description="", updated_at="",
        lifecycle="dsl", data_retention=None, processing_step_count=0,
        routing_count=0, field_count=0)


async def test_deactivate_fork_tool(gateway, mcp):
    gateway.streams.append(_forked_child())
    async with Client(mcp) as client:
        r = await client.call_tool(
            "deactivate_fork", {"parent": "logs.ecs", "child": "logs.ecs.app"})
    assert r.data["lifecycle"] == "dsl"


async def test_activate_fork_requires_confirm(gateway, mcp):
    gateway.streams.append(_forked_child())
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "activate_fork", {"parent": "logs.ecs", "child": "logs.ecs.app"})


async def test_activate_fork_tool(gateway, mcp):
    gateway.streams.append(_forked_child())
    async with Client(mcp) as client:
        r = await client.call_tool("activate_fork", {
            "parent": "logs.ecs", "child": "logs.ecs.app", "confirm": True})
    assert r.data["lifecycle"] == "dsl"


async def test_set_stream_retention_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("set_stream_retention", {"name": "logs.ecs", "retention": "30d"})
    assert r.data["data_retention"] == "30d"


async def test_set_stream_retention_bad_format_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("set_stream_retention", {"name": "logs.ecs", "retention": "banana"})


async def test_delete_stream_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("delete_stream", {"name": "logs.ecs.app"})
    assert r.data["result"] == "deleted"


async def test_disable_streams_requires_confirm(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("disable_streams", {})
        r = await client.call_tool("disable_streams", {"confirm": True})
    assert r.data["result"] == "deleted"


async def test_list_streams_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_streams", {})
    names = {s["name"]: s for s in result.data}
    assert names["logs.ecs"]["type"] == "wired"
    assert names["traces-apm-default"]["type"] == "classic"
    # StreamSummary carries only name/type/description (not the ingest counts).
    assert set(result.data[0]) == {"name", "type", "description"}


async def test_get_stream_found(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_stream", {"name": "logs.ecs"})
    assert result.data["type"] == "wired"
    assert result.data["lifecycle"] == "dsl"
    assert result.data["field_count"] == 2


async def test_get_stream_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_stream", {"name": "nope"})


async def test_get_stream_blank_name_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_stream", {"name": ""})


async def test_get_stream_ingest_serializes_field_map(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_stream_ingest", {"name": "logs.ecs"})
    assert result.data["lifecycle"] == "dsl"
    assert result.data["fields"] == {"@timestamp": "date", "host.name": "keyword"}


async def test_get_stream_ingest_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_stream_ingest", {"name": "nope"})


async def test_delete_stream_root_guard_via_tool(mcp):
    # The FakeGateway mirrors the wired-only root guard, so this exercises the
    # force plumbing hermetically (a toolbox regression hardcoding force=True would fail).
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("delete_stream", {"name": "logs.ecs"})  # root -> guarded
        r = await client.call_tool("delete_stream", {"name": "logs.ecs", "force": True})
    assert r.data["result"] == "deleted"


async def test_stream_write_error_translates_to_tool_error(gateway, mcp):
    from kibana_mcp.core.errors import KibanaRejected
    gateway.stream_error = KibanaRejected("kibana said no")  # injected domain error
    async with Client(mcp) as client:
        with pytest.raises(ToolError):  # gateway_errors() must wrap it, not leak the raw error
            await client.call_tool("fork_stream", {
                "parent_name": "logs.ecs", "child_name": "logs.ecs.app",
                "condition_field": "svc", "condition_value": "x"})
