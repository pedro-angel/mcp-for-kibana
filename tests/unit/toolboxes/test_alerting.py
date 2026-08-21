import pytest
from fastmcp import Client, FastMCP

from kibana_mcp.toolboxes.alerting.toolbox import AlertingToolbox
from kibana_mcp.toolboxes.base import ToolboxDeps
from tests.fakes import FakeGateway

RULE = {"name": "r", "rule_type_id": ".es-query", "consumer": "stackAlerts", "params": {}}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    AlertingToolbox().register(server, deps)
    return server


async def test_exposes_expected_tools(mcp):
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {
        "list_alert_rules", "get_alert_rule", "get_alerting_health", "list_connectors",
        "create_alert_rule", "enable_alert_rule", "disable_alert_rule", "create_connector",
        "delete_alert_rule", "delete_connector", "execute_connector",
    }


async def test_create_rule_defaults_disabled(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_alert_rule", RULE)
    assert created.data["enabled"] is False  # inert by default
    assert gateway.alert_rules[created.data["id"]].enabled is False


async def test_rule_lifecycle(mcp, gateway):
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", RULE)).data["id"]
        await client.call_tool("enable_alert_rule", {"rule_id": rid})
        assert gateway.alert_rules[rid].enabled is True
        assert (await client.call_tool("get_alert_rule", {"rule_id": rid})).data["name"] == "r"
        assert len((await client.call_tool("list_alert_rules", {})).data) == 1
        deleted = await client.call_tool("delete_alert_rule", {"rule_id": rid})
    assert deleted.data == {"id": rid, "deleted": True}
    assert gateway.alert_rules == {}


async def test_connector_lifecycle_and_execute(mcp, gateway):
    async with Client(mcp) as client:
        c = await client.call_tool(
            "create_connector", {"name": "c", "connector_type_id": ".server-log"}
        )
        cid = c.data["id"]
        assert c.data["connector_type_id"] == ".server-log"
        ex = await client.call_tool(
            "execute_connector", {"connector_id": cid, "params": {"message": "hi"}}
        )
        assert ex.data["status"] == "ok"
        assert (await client.call_tool("list_connectors", {})).data[0]["id"] == cid
        await client.call_tool("delete_connector", {"connector_id": cid})
    assert gateway.connectors == {}


async def test_alerting_health(mcp):
    async with Client(mcp) as client:
        h = await client.call_tool("get_alerting_health", {})
    assert h.data["has_permanent_encryption_key"] is True
    assert h.data["status"] == "ok"


async def test_space_threads_to_factory_and_echoes(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    AlertingToolbox().register(server, deps)
    async with Client(server) as client:
        created = await client.call_tool("create_alert_rule", {**RULE, "space": "sales"})
    assert calls == ["sales"]
    assert created.data["space"] == "sales"


async def test_no_space_keeps_default_shape(mcp):
    async with Client(mcp) as client:
        created = await client.call_tool("create_alert_rule", RULE)
    assert "space" not in created.data


async def test_tier_tags(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert tools["list_alert_rules"].annotations.readOnlyHint is True
    assert tools["create_alert_rule"].annotations.destructiveHint is False
    assert tools["delete_alert_rule"].annotations.destructiveHint is True
    # execute_connector fires the real external action: destructive AND
    # non-idempotent AND open-world (distinct from the plain _DESTRUCTIVE hints).
    exec_ann = tools["execute_connector"].annotations
    assert exec_ann.destructiveHint is True
    assert exec_ann.idempotentHint is False
    assert exec_ann.openWorldHint is True
