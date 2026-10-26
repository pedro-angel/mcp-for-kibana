"""The privilege matrix's rows held to the registry (D58).

tests/contract/test_privilege_matrix.py builds a row for every tool that takes
`space`, `source_space`, `target_spaces` or `spaces`, from the same listing, so
no such tool can ship without one. What can rot is the row's arguments: if a
schema or a guard changes so the generated ones stop before the space is
read, the live row would pass on a validation error and test nothing. Here
every row runs against a factory that refuses any space with a sentinel, and
must reach it.
"""

import asyncio

import pytest
from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.core.errors import KibanaAuthError
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests._privilege_matrix import EXPORTS, LISTING_GUARDS, minimal_args, space_params
from tests._registry import registry
from tests.fakes import FakeGateway

_SENTINEL = "matrix-sentinel"


def _factory(space=None):
    if space is not None:
        raise KibanaAuthError(f"{_SENTINEL}: {space}")
    return FakeGateway()


async def _reached(tmp_path):
    for handle, body in EXPORTS.items():
        (tmp_path / f"{handle}.ndjson").write_text(body)
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"))
    reached, stopped = set(), {}
    async with Client(build_server(settings, _factory, export_dir=tmp_path)) as client:
        for tool in await client.list_tools():
            params = space_params(tool.input_schema)
            if not params:
                continue
            args = minimal_args(tool.name, tool.input_schema)
            args["space" if "space" in params else "source_space"] = "hidden"
            result = await client.call_tool(tool.name, args, raise_on_error=False)
            text = "".join(getattr(c, "text", "") for c in result.content)
            if f"{_SENTINEL}: hidden" in text:
                reached.add(tool.name)
            else:
                stopped[tool.name] = text[:200]
    return reached, stopped


def test_every_space_taking_tool_has_a_row_that_reaches_the_space_check(tmp_path):
    reached, stopped = asyncio.run(_reached(tmp_path))
    assert not stopped, (
        "these privilege-matrix rows stop before the space is read, so the live matrix "
        "would test nothing for them — add arguments to OVERRIDES in "
        f"tests/_privilege_matrix.py: {stopped}")
    space_aware = {t.name for t in registry().values() if t.space_aware}
    assert space_aware <= reached and len(reached) >= len(space_aware) + 4  # + the 4 copy/share


@pytest.mark.parametrize("guard", sorted(LISTING_GUARDS))
def test_every_listing_guard_is_a_registered_tool(guard):
    assert guard in registry(), f"LISTING_GUARDS names {guard!r}, which no toolbox registers"
