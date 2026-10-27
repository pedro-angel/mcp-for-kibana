"""Streams disable/enable certification on an isolated ephemeral stack.

This is the tooling-certified proof (DoD criterion `streams_ephemeral`) that
disable_streams deletes the wired framework and enable_streams recovers the
roots — a path that cannot run against the shared dev stack. disable's live
effect was never probed before (env-research P9), so these assertions are the
first live characterization; relaxed where a cold single-node stack may differ
from the docs (e.g. a subset of roots). The actions go through the MCP tool
layer (`call_tool`, D60): disable_streams is first called without
confirm=True and must be refused; the gateway only observes."""

import uuid

import pytest
from fastmcp.exceptions import ToolError

pytestmark = pytest.mark.ephemeral


def _names(gw):
    return {s.name for s in gw.list_streams()}


async def test_disable_then_enable_roundtrip(eph_gateway, call_tool):
    gw = eph_gateway
    # A fresh stack may need enable to create the wired roots.
    assert (await call_tool("enable_streams"))["result"] in {"created", "noop"}
    assert "logs.ecs" in _names(gw)  # at least the ecs root exists after enable

    child = f"logs.ecs.eph{uuid.uuid4().hex[:8]}"
    await call_tool("fork_stream", {
        "parent_name": "logs.ecs", "child_name": child,
        "condition_field": "service.name", "condition_value": "eph"})
    assert child in _names(gw)

    with pytest.raises(ToolError, match="confirm=True"):
        await call_tool("disable_streams")  # the confirm gate refuses first
    assert child in _names(gw)  # ...and nothing was deleted

    await call_tool("disable_streams", {"confirm": True})
    after_disable = _names(gw)
    assert "logs.ecs" not in after_disable  # wired root defs deleted cluster-wide
    assert child not in after_disable  # ...including the forked child

    await call_tool("disable_streams", {"confirm": True})  # idempotent-safe second call
    assert (await call_tool("enable_streams"))["result"] in {"created", "noop"}
    assert "logs.ecs" in _names(gw)  # root recreated
    assert child not in _names(gw)  # a forked child is NOT restored by enable
