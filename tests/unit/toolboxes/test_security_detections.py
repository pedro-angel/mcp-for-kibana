"""The security-detections toolbox: 12 read tools + a v2/v5 write/destructive tier
(create/delete/replace/enable/disable rules, exception lists/items and their
edit/copy/export/import (D50), value lists + items) over the detection engine. Driven through the in-memory fastmcp Client
against a FakeGateway."""

import re

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.models import ExceptionItem, ExceptionList
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.security_detections.toolbox import SecurityDetectionsToolbox
from tests.fakes import FakeGateway

READ_TOOLS = {
    "find_detection_rules", "get_detection_rule", "get_prepackaged_rules_status",
    "list_detection_rule_tags", "search_detection_alerts", "find_exception_lists",
    "get_exception_list", "find_exception_items", "find_value_lists",
    "find_value_list_items", "find_timelines", "export_exception_list",
}
WRITE_TOOLS = {"create_detection_rule", "create_exception_list", "create_exception_item",
               "update_detection_rule", "replace_detection_rule", "enable_detection_rule",
               "disable_detection_rule", "create_value_list", "create_value_list_item",
               "update_exception_list", "duplicate_exception_list", "import_exception_list"}
DESTRUCTIVE_TOOLS = {"delete_detection_rule", "delete_exception_list", "delete_exception_item",
                     "delete_value_list", "delete_value_list_item", "overwrite_exception_list"}


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway, tmp_path):
    server = FastMCP("test")
    deps = ToolboxDeps(
        gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601",
        export_dir=tmp_path,
    )
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


# --- exception-list edit, copy, export and import (D50) ---


def _agnostic(gateway):
    gateway.exception_lists.append(ExceptionList(
        id="el-ag", list_id="exc-ag", name="Shared", type="detection",
        namespace_type="agnostic", tags=("s",), os_types=(), description="shared",
    ))


async def test_update_exception_list_changes_only_given_fields(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("update_exception_list", {
            "list_id": "exc-1", "name": "Allowlist v2"})
    assert result.data["name"] == "Allowlist v2"
    assert result.data["previous"] == {"name": "Allowlist", "description": "", "tags": []}
    [stored] = [x for x in gateway.exception_lists if x.list_id == "exc-1"]
    assert stored.name == "Allowlist v2" and stored.type == "detection"


async def test_update_exception_list_empty_tags_clear(mcp, gateway):
    _agnostic(gateway)
    async with Client(mcp) as client:
        result = await client.call_tool("update_exception_list", {
            "list_id": "exc-ag", "namespace_type": "agnostic", "tags": []})
    assert result.data["tags"] == []
    assert result.data["previous"]["tags"] == ["s"]


@pytest.mark.parametrize("args,match", [
    ({"name": "x"}, "exactly one of id or list_id"),
    ({"id": "el-1", "list_id": "exc-1", "name": "x"}, "exactly one of id or list_id"),
    ({"list_id": "exc-1"}, "at least one of name, description or tags"),
])
async def test_update_exception_list_guards(mcp, args, match):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=match):
            await client.call_tool("update_exception_list", args)


async def test_update_exception_list_conflict_says_call_again(mcp, gateway):
    gateway.exception_list_conflict = True
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="call update_exception_list again"):
            await client.call_tool("update_exception_list", {"list_id": "exc-1", "name": "x"})


async def test_update_exception_list_other_namespace_is_not_found(mcp, gateway):
    _agnostic(gateway)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="not found"):
            await client.call_tool("update_exception_list", {"list_id": "exc-ag", "name": "x"})


async def test_duplicate_exception_list_returns_the_copy(mcp, gateway):
    async with Client(mcp) as client:
        result = await client.call_tool("duplicate_exception_list", {
            "id": "el-1", "include_expired_exceptions": False})
    assert result.data["name"] == "Allowlist [Duplicate]"
    assert result.data["list_id"] != "exc-1"
    assert result.data["source_list_id"] == "exc-1"
    assert gateway.last_duplicate_include_expired is False
    assert len(gateway.exception_items[result.data["list_id"]]) == 1


async def test_duplicate_exception_list_refuses_endpoint_before_calling(mcp, gateway):
    gateway.exception_lists.append(ExceptionList(
        id="el-ep", list_id="ep", name="EP", type="endpoint", namespace_type="single",
        tags=(), os_types=()))
    before = len(gateway.exception_lists)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="export_exception_list then import_exception_list"):
            await client.call_tool("duplicate_exception_list", {"list_id": "ep"})
    assert len(gateway.exception_lists) == before


async def test_export_exception_list_returns_a_handle_not_content(mcp, gateway, tmp_path):
    async with Client(mcp) as client:
        result = await client.call_tool("export_exception_list", {"list_id": "exc-1"})
    data = result.data
    assert re.fullmatch(r"el-[0-9a-f]{12}", data["handle"])
    assert data["id"] == "el-1" and data["list_id"] == "exc-1" and data["item_count"] == 1
    assert data["byte_size"] == (tmp_path / f"{data['handle']}.ndjson").stat().st_size
    assert "entries" not in str(data) and "host.name" not in str(data)
    assert gateway.last_export_include_expired is True


async def test_export_exception_list_requires_one_id(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="exactly one of id or list_id"):
            await client.call_tool("export_exception_list", {})


async def _export(client, list_id="exc-1"):
    return (await client.call_tool("export_exception_list", {"list_id": list_id})).data["handle"]


async def test_import_exception_list_creates_a_new_list(mcp, gateway):
    async with Client(mcp) as client:
        handle = await _export(client)
        result = await client.call_tool("import_exception_list", {"handle": handle})
    data = result.data
    assert data["success"] is True and data["errors"] == []
    assert data["list_id"] != "exc-1" and data["source_list_id"] == "exc-1"
    assert data["id"] and data["imported_item_count"] == 1
    # the source is untouched; the copy's items carry new item ids
    assert [i.item_id for i in gateway.exception_items["exc-1"]] == ["item-1"]
    [copied] = gateway.exception_items[data["list_id"]]
    assert copied.item_id != "item-1"
    content, overwrite = gateway.last_exception_import
    assert overwrite is False and b'"id"' not in content


async def test_import_exception_list_takes_a_list_id(mcp, gateway):
    async with Client(mcp) as client:
        handle = await _export(client)
        result = await client.call_tool("import_exception_list", {
            "handle": handle, "list_id": "restored", "space": "b"})
    assert result.data["list_id"] == "restored" and result.data["space"] == "b"


async def test_import_exception_list_refuses_an_existing_list_id(mcp, gateway):
    async with Client(mcp) as client:
        handle = await _export(client)
        with pytest.raises(ToolError, match="already exists.*overwrite_exception_list"):
            await client.call_tool("import_exception_list", {
                "handle": handle, "list_id": "exc-1"})
    assert gateway.last_exception_import is None  # nothing sent


async def test_import_exception_list_reports_losing_the_race(mcp, gateway):
    # A list created under the same list_id between the check and the import:
    # Kibana skips the list (409) and adds the items to it (probed, D50).
    gateway.exception_list_created_before_import = ExceptionList(
        id="other-el", list_id="raced", name="other", type="detection",
        namespace_type="single", tags=(), os_types=(), description="",
    )
    async with Client(mcp) as client:
        handle = await _export(client)
        result = await client.call_tool("import_exception_list", {
            "handle": handle, "list_id": "raced"})
    data = result.data
    assert data["success"] is False and data["id"] is None
    assert data["errors"] == ["raced: 409 already exists"]
    assert data["imported_item_count"] == 1
    assert [i.list_id for i in gateway.exception_items["raced"]] == ["raced"]


async def test_import_exception_list_rejection_is_content_free(mcp, gateway):
    gateway.exception_import_rejected = True
    async with Client(mcp) as client:
        handle = await _export(client)
        with pytest.raises(ToolError) as e:
            await client.call_tool("import_exception_list", {"handle": handle})
    assert "secret-host" not in str(e.value)
    assert "export_exception_list" in str(e.value)


async def test_import_exception_list_names_the_tools_of_a_saved_objects_handle(mcp, tmp_path):
    from kibana_mcp.core.saved_objects import write_export

    so = write_export(tmp_path, b"{}")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="import_saved_objects"):
            await client.call_tool("import_exception_list", {"handle": so})
        with pytest.raises(ToolError, match="call export_exception_list again"):
            await client.call_tool("overwrite_exception_list", {"handle": "el-000000000000"})


async def test_missing_handle_names_the_fix_on_a_masking_server(gateway, tmp_path):
    # Production runs FastMCP(mask_error_details=True): only a ToolError's text
    # reaches the model, so the expired-handle KibanaNotFound must become one.
    server = FastMCP("test", mask_error_details=True)
    SecurityDetectionsToolbox().register(server, ToolboxDeps(
        gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601",
        export_dir=tmp_path,
    ))
    async with Client(server) as client:
        for tool in ("import_exception_list", "overwrite_exception_list"):
            with pytest.raises(ToolError, match="not found.*call export_exception_list again"):
                await client.call_tool(tool, {"handle": "el-000000000000"})
    assert gateway.last_exception_import is None


async def test_overwrite_exception_list_reports_removed_items(mcp, gateway):
    async with Client(mcp) as client:
        handle = await _export(client)
        gateway.exception_items["exc-1"].append(
            ExceptionItem(id="ei-2", item_id="added-later", name="new", list_id="exc-1"))
        await client.call_tool("update_exception_list", {"list_id": "exc-1", "name": "edited"})
        result = await client.call_tool("overwrite_exception_list", {"handle": handle})
    data = result.data
    assert data["success"] is True
    assert data["id"] == "el-1" and data["previous_id"] == "el-1"
    assert data["removed_item_ids"] == ["added-later"]
    assert data["restored_item_count"] == 1
    [stored] = [x for x in gateway.exception_lists if x.list_id == "exc-1"]
    assert stored.name == "Allowlist"
    assert [i.item_id for i in gateway.exception_items["exc-1"]] == ["item-1"]
    assert gateway.last_exception_import[1] is True


async def test_overwrite_exception_list_recreates_a_deleted_list(mcp, gateway):
    async with Client(mcp) as client:
        handle = await _export(client)
        gateway.exception_lists = [x for x in gateway.exception_lists if x.list_id != "exc-1"]
        result = await client.call_tool("overwrite_exception_list", {"handle": handle})
    assert result.data["previous_id"] is None
    assert result.data["id"] and result.data["id"] != "el-1"
    assert result.data["removed_item_ids"] == []


async def test_get_and_find_take_namespace_type(mcp, gateway):
    _agnostic(gateway)
    gateway.exception_items["exc-ag"] = []
    async with Client(mcp) as client:
        got = await client.call_tool("get_exception_list", {
            "list_id": "exc-ag", "namespace_type": "agnostic"})
        items = await client.call_tool("find_exception_items", {
            "list_id": "exc-ag", "namespace_type": "agnostic"})
    assert got.data["description"] == "shared"
    assert items.data == []
