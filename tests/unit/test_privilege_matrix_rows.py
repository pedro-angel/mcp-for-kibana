"""The privilege matrix's rows held to the registry (D58).

tests/contract/test_privilege_matrix.py builds a row for every tool that takes
`space`, `source_space`, `target_spaces` or `spaces`, from the same listing, so
no such tool can ship without one. What can rot is the row's arguments: if a
schema or a guard changes so the generated ones stop before the space is
read, the live row would pass on a validation error and test nothing. Here
every row runs twice and must reach both sentinels: once against a factory
that refuses any space (the space is validated first), once against a
factory that accepts it and hands back the real adapter over a client whose
every call raises (no argument guard, in the tool or the adapter, stops the
row before a request to Kibana).
"""

import asyncio

import pytest
from fastmcp import Client

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.config import Settings, Tier
from kibana_mcp.core.errors import KibanaAuthError
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests._privilege_matrix import EXPORTS, LISTING_GUARDS, minimal_args, space_params
from tests._registry import registry
from tests.fakes import FakeGateway

_SENTINEL = "matrix-sentinel"
_GATEWAY_SENTINEL = "matrix-sentinel: gateway called"


def _refuse_space(space=None):
    if space is not None:
        raise KibanaAuthError(f"{_SENTINEL}: {space}")
    return FakeGateway()


class _Unreachable:
    """A kibana-py client whose every call raises: the real adapter's own
    guards run, and only a request to Kibana reaches the sentinel."""

    def close(self):
        pass

    def __getattr__(self, name):
        raise KibanaAuthError(_GATEWAY_SENTINEL)


def _accept_space(space=None):
    if space is None:
        return FakeGateway()
    return KibanaPyGateway(_Unreachable(), space)


async def _reached(tmp_path, factory, sentinel):
    for handle, body in EXPORTS.items():
        (tmp_path / f"{handle}.ndjson").write_text(body)
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"))
    reached, stopped = set(), {}
    async with Client(build_server(settings, factory, export_dir=tmp_path)) as client:
        for tool in await client.list_tools():
            params = space_params(tool.input_schema)
            if not params:
                continue
            args = minimal_args(tool.name, tool.input_schema)
            args["space" if "space" in params else "source_space"] = "hidden"
            result = await client.call_tool(tool.name, args, raise_on_error=False)
            text = "".join(getattr(c, "text", "") for c in result.content)
            if sentinel in text:
                reached.add(tool.name)
            else:
                stopped[tool.name] = text[:200]
    return reached, stopped


@pytest.mark.parametrize(("factory", "sentinel", "where"), [
    (_refuse_space, f"{_SENTINEL}: hidden", "before the space is read"),
    (_accept_space, _GATEWAY_SENTINEL, "before a request to Kibana (a guard after the space check)"),
])
def test_every_space_taking_tool_has_a_row_that_reaches_the_space_check(
    tmp_path, factory, sentinel, where
):
    reached, stopped = asyncio.run(_reached(tmp_path, factory, sentinel))
    assert not stopped, (
        f"these privilege-matrix rows stop {where}, so the live matrix would test "
        "nothing for them — add arguments to OVERRIDES in "
        f"tests/_privilege_matrix.py: {stopped}")
    space_aware = {t.name for t in registry().values() if t.space_aware}
    assert space_aware <= reached and len(reached) >= len(space_aware) + 4  # + the 4 copy/share


@pytest.mark.parametrize("guard", sorted(LISTING_GUARDS))
def test_every_listing_guard_is_a_registered_tool(guard):
    assert guard in registry(), f"LISTING_GUARDS names {guard!r}, which no toolbox registers"
