"""The security-detections toolbox: 11 read tools + a v2/v4 write/destructive tier
(create/delete/replace/enable/disable rules, exception lists/items, value lists +
items) over the detection engine. Driven through the in-memory fastmcp Client
against a FakeGateway."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.security_detections.toolbox import SecurityDetectionsToolbox
from tests.fakes import FakeGateway

READ_TOOLS = {
    "find_detection_rules", "get_detection_rule", "get_prepackaged_rules_status",
    "list_detection_rule_tags", "search_detection_alerts", "find_exception_lists",
    "get_exception_list", "find_exception_items", "find_value_lists",
    "find_value_list_items", "find_timelines",
}
WRITE_TOOLS = {"create_detection_rule", "create_exception_list", "create_exception_item",
               "update_detection_rule", "replace_detection_rule", "enable_detection_rule",
               "disable_detection_rule", "create_value_list", "create_value_list_item"}
DESTRUCTIVE_TOOLS = {"delete_detection_rule", "delete_exception_list", "delete_exception_item",
                     "delete_value_list", "delete_value_list_item"}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    SecurityDetectionsToolbox().register(server, deps)
    return server


async def test_tiers_carry_correct_annotations(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS | DESTRUCTIVE_TOOLS
    for name in READ_TOOLS:
        assert tools[name].annotations.readOnlyHint is True
    for name in WRITE_TOOLS:
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
    for name in DESTRUCTIVE_TOOLS:
        assert tools[name].annotations.destructiveHint is True


async def test_create_detection_rule_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("create_detection_rule", {
            "name": "block bad IPs", "description": "d", "query": "*:*",
            "index": ["logs-*"], "severity": "high", "risk_score": 73,
        })
    assert result.data["name"] == "block bad IPs"
    assert result.data["severity"] == "high"
    assert result.data["risk_score"] == 73
    assert result.data["enabled"] is False  # safe default


async def test_create_detection_rule_rejects_empty_index(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_detection_rule", {
                "name": "x", "description": "d", "query": "*:*", "index": [],
            })


async def test_create_detection_rule_rejects_out_of_range_risk_score(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_detection_rule", {
                "name": "x", "description": "d", "query": "*:*",
                "index": ["logs-*"], "risk_score": 101,
            })


async def test_create_exception_item_maps_entries(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("create_exception_item", {
            "list_id": "exc-1", "name": "allow host", "description": "d",
            "entries": [{"field": "host.name", "value": "trusted", "operator": "included"}],
        })
    assert result.data["list_id"] == "exc-1"
    # the toolbox maps each ExceptionEntry -> a full {field,operator,type:"match",value}
    # entry (the per-entry type discriminator is required or Kibana rejects the item).
    assert gateway.last_exception_entries == [
        {"field": "host.name", "operator": "included", "type": "match", "value": "trusted"}
    ]


async def test_create_exception_item_rejects_empty_entries(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_exception_item", {
                "list_id": "exc-1", "name": "x", "description": "d", "entries": [],
            })


async def test_delete_detection_rule_returns_deleted(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("delete_detection_rule", {"rule_id": "rule-1"})
    assert result.data["deleted"] is True
    assert gateway.deleted == [("rule", "rule-1")]


async def test_delete_detection_rule_requires_exactly_one_id(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):  # neither
            await client.call_tool("delete_detection_rule", {})
        with pytest.raises(ToolError):  # both
            await client.call_tool("delete_detection_rule", {"rule_id": "a", "id": "b"})


async def test_delete_exception_item_returns_deleted(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("delete_exception_item", {"item_id": "item-1"})
    assert result.data["deleted"] is True
    assert gateway.deleted == [("excitem", "item-1")]


async def test_find_detection_rules_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("find_detection_rules", {})
    assert result.data[0]["rule_id"] == "rule-1"
    assert result.data[0]["risk_score"] == 73


async def test_get_detection_rule_by_rule_id(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_detection_rule", {"rule_id": "rule-1"})
    assert result.data["name"] == "Suspicious login"


async def test_get_detection_rule_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_detection_rule", {"rule_id": "nope"})


async def test_list_detection_rule_tags(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_detection_rule_tags", {})
    assert result.data == ["auth", "network"]


async def test_find_exception_items_by_list(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("find_exception_items", {"list_id": "exc-1"})
    assert result.data[0]["item_id"] == "item-1"


async def test_find_exception_items_missing_list_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("find_exception_items", {"list_id": "nope"})


async def test_prepackaged_status_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_prepackaged_rules_status", {})
    assert result.data["rules_custom_installed"] == 1
    assert result.data["timelines_not_installed"] == 10


# --- write extras (#60): update rule + value lists ---


async def test_update_detection_rule_requires_a_field(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_detection_rule", {"rule_id": "r"})


async def test_update_detection_rule_requires_exactly_one_id(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_detection_rule", {"rule_id": "r", "id": "i", "name": "x"})


async def test_update_detection_rule_forwards_fields(mcp, gateway):
    async with Client(mcp) as client:
        r = await client.call_tool("update_detection_rule", {"rule_id": "r", "tags": ["a"]})
    assert r.data["rule_id"] == "r"
    assert gateway.patched == {"tags": ["a"]}  # only the set field forwarded


async def test_update_detection_rule_missing_is_tool_error(mcp, gateway):
    gateway.missing_rules = {"nope"}
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_detection_rule", {"rule_id": "nope", "name": "x"})


async def test_update_detection_rule_bad_severity_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_detection_rule", {"rule_id": "r", "severity": "sev0"})


async def test_create_value_list_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("create_value_list",
                                   {"name": "n", "description": "d", "type": "keyword"})
    assert r.data["type"] == "keyword"


async def test_create_value_list_blank_type_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_value_list", {"name": "n", "description": "d", "type": ""})


async def test_create_value_list_duplicate_id_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):  # 'vl-1' is seeded in the fake -> conflict
            await client.call_tool("create_value_list",
                                   {"name": "n", "description": "d", "type": "ip", "id": "vl-1"})


async def test_delete_value_list_returns_deleted(mcp, gateway):
    gateway.create_value_list("n", "d", "keyword", "vlx")
    async with Client(mcp) as client:
        r = await client.call_tool("delete_value_list", {"id": "vlx"})
    assert r.data == {"deleted": True, "id": "vlx"}


async def test_delete_value_list_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("delete_value_list", {"id": "does-not-exist"})


async def test_update_detection_rule_out_of_range_risk_score_rejected(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_detection_rule", {"rule_id": "r", "risk_score": 200})


# --- write follow-ups (#73): RMW replace/enable/disable + value-list items ---


async def test_replace_detection_rule_forwards_fields(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool(
            "replace_detection_rule", {"rule_id": "rule-1", "name": "renamed", "tags": ["x"]})
    assert r.data["name"] == "renamed"
    assert r.data["tags"] == ["x"]


async def test_replace_detection_rule_requires_exactly_one_id(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):  # neither
            await client.call_tool("replace_detection_rule", {"name": "x"})
        with pytest.raises(ToolError):  # both
            await client.call_tool(
                "replace_detection_rule", {"rule_id": "a", "id": "b", "name": "x"})


async def test_replace_detection_rule_requires_a_field(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("replace_detection_rule", {"rule_id": "rule-1"})


async def test_replace_detection_rule_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "replace_detection_rule", {"rule_id": "nope", "name": "x"})


async def test_enable_detection_rule_sets_enabled_true(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("enable_detection_rule", {"rule_id": "rule-1"})
    assert r.data["enabled"] is True


async def test_disable_detection_rule_sets_enabled_false(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool("disable_detection_rule", {"rule_id": "rule-1"})
    assert r.data["enabled"] is False


async def test_create_value_list_item_tool(mcp):
    async with Client(mcp) as client:
        r = await client.call_tool(
            "create_value_list_item", {"list_id": "vl-1", "value": "1.2.3.4"})
    assert r.data["list_id"] == "vl-1"
    assert r.data["value"] == "1.2.3.4"
    assert r.data["type"] == "ip"  # inherited from the parent value list


async def test_find_value_list_items_by_list(mcp, gateway):
    gateway.create_value_list_item(list_id="vl-1", value="1.2.3.4")
    async with Client(mcp) as client:
        r = await client.call_tool("find_value_list_items", {"list_id": "vl-1"})
    assert r.data[0]["value"] == "1.2.3.4"


async def test_delete_value_list_item_returns_deleted(mcp, gateway):
    item = gateway.create_value_list_item(list_id="vl-1", value="1.2.3.4")
    async with Client(mcp) as client:
        r = await client.call_tool("delete_value_list_item", {"item_id": item.id})
    assert r.data == {"deleted": True, "item_id": item.id}
    assert gateway.deleted == [("value-list-item", item.id)]


async def test_delete_value_list_item_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("delete_value_list_item", {"item_id": "does-not-exist"})


# --- space targeting (spec addendum 2026-08-18) ---


async def test_space_threads_to_factory_and_echoes(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    SecurityDetectionsToolbox().register(server, deps)
    async with Client(server) as client:
        result = await client.call_tool(
            "get_detection_rule", {"rule_id": "rule-1", "space": "sales"})
    assert calls == ["sales"]
    assert result.data["space"] == "sales"


async def test_no_space_keeps_default_shape(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_detection_rule", {"rule_id": "rule-1"})
    assert "space" not in result.data


async def test_guard_fires_before_factory_even_with_space(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    SecurityDetectionsToolbox().register(server, deps)
    async with Client(server) as client:
        with pytest.raises(ToolError):  # no update field -> guard, never the factory
            await client.call_tool("update_detection_rule", {"rule_id": "r", "space": "sales"})
    assert calls == []
