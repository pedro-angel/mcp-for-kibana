import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.cases.toolbox import CasesToolbox
from tests.fakes import FakeGateway


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    CasesToolbox().register(server, deps)
    return server


async def test_exposes_expected_tools(mcp):
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {
        "list_cases", "get_case", "create_case", "update_case", "add_case_comment", "delete_case",
    }


async def test_case_lifecycle(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool(
            "create_case", {"title": "Incident", "description": "desc", "severity": "high"}
        )
        cid = created.data["id"]
        assert created.data["status"] == "open" and created.data["severity"] == "high"
        upd = await client.call_tool("update_case", {"case_id": cid, "status": "in-progress"})
        assert upd.data["status"] == "in-progress"
        commented = await client.call_tool("add_case_comment", {"case_id": cid, "comment": "note"})
        assert commented.data["total_comments"] == 1
        assert (await client.call_tool("get_case", {"case_id": cid})).data["title"] == "Incident"
        assert len((await client.call_tool("list_cases", {})).data) == 1
        deleted = await client.call_tool("delete_case", {"case_id": cid})
    assert deleted.data == {"id": cid, "deleted": True}
    assert gateway.cases == {}


async def test_update_case_requires_at_least_one_field(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_case", {"title": "x", "description": "d"})
        with pytest.raises(ToolError, match="at least one field"):
            await client.call_tool("update_case", {"case_id": created.data["id"]})
    # the case is untouched
    assert gateway.cases[created.data["id"]].status == "open"


async def test_invalid_severity_is_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(Exception):  # pydantic Literal rejects at the tool boundary
            await client.call_tool(
                "create_case", {"title": "x", "description": "d", "severity": "urgent"}
            )


async def test_tier_tags(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert tools["get_case"].annotations.readOnlyHint is True
    assert tools["create_case"].annotations.destructiveHint is False
    assert tools["delete_case"].annotations.destructiveHint is True


# --- space parameter on all 6 cases tools ---


def _mcp_with_recording_factory(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    CasesToolbox().register(server, deps)
    return server, calls


async def test_create_case_in_space_threads_and_echoes(gateway):
    server, calls = _mcp_with_recording_factory(gateway)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_case", {"title": "Incident", "description": "desc", "space": "sales"}
        )
    assert calls == ["sales"] and result.data["space"] == "sales"


async def test_create_case_without_space_has_no_echo(mcp):
    # default-path pin: today's behavior, byte-identical
    async with Client(mcp) as client:
        result = await client.call_tool("create_case", {"title": "Incident", "description": "desc"})
    assert "space" not in result.data


async def test_update_case_guard_fires_before_factory(gateway):
    server, calls = _mcp_with_recording_factory(gateway)
    async with Client(server) as client:
        with pytest.raises(ToolError, match="at least one field"):
            await client.call_tool("update_case", {"case_id": "c1", "space": "sales"})
    assert calls == []
