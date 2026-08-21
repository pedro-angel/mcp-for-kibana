"""Streams disable/enable certification on an isolated ephemeral stack.

This is the tooling-certified proof (DoD criterion `streams_ephemeral`) that
disable_streams deletes the wired framework and enable_streams recovers the
roots — a path that cannot run against the shared dev stack. disable's live
effect was never probed before (env-research P9), so these assertions are the
first live characterization; relaxed where a cold single-node stack may differ
from the docs (e.g. a subset of roots)."""

import uuid

import pytest

pytestmark = pytest.mark.ephemeral


def _names(gw):
    return {s.name for s in gw.list_streams()}


def test_disable_then_enable_roundtrip(eph_gateway):
    gw = eph_gateway
    # A fresh stack may need enable to create the wired roots.
    assert gw.enable_streams().result in {"created", "noop"}
    assert "logs.ecs" in _names(gw)  # at least the ecs root exists after enable

    child = f"logs.ecs.eph{uuid.uuid4().hex[:8]}"
    gw.fork_stream("logs.ecs", child, "service.name", "eph")
    assert child in _names(gw)

    gw.disable_streams()  # gateway call (no toolbox confirm gate at this layer)
    after_disable = _names(gw)
    assert "logs.ecs" not in after_disable  # wired root defs deleted cluster-wide
    assert child not in after_disable  # ...including the forked child

    gw.disable_streams()  # idempotent-safe second call
    assert gw.enable_streams().result in {"created", "noop"}
    assert "logs.ecs" in _names(gw)  # root recreated
    assert child not in _names(gw)  # a forked child is NOT restored by enable
