from datetime import UTC, datetime, timedelta

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

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
        "list_rule_types", "get_connector",
        "create_alert_rule", "update_alert_rule", "enable_alert_rule", "disable_alert_rule",
        "create_connector", "update_connector",
        "mute_alert_rule", "unmute_alert_rule", "mute_alert", "unmute_alert",
        "snooze_alert_rule", "unsnooze_alert_rule", "update_alert_rule_api_key",
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


async def test_list_rule_types_returns_types_with_their_consumers(mcp, gateway):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        result = await client.call_tool("list_rule_types", {})
    assert tools["list_rule_types"].annotations.readOnlyHint is True
    es = next(t for t in result.structured_content["result"] if t["id"] == ".es-query")
    assert "stackAlerts" in es["consumers"]
    assert es["enabled_in_license"] is True
    assert "space" not in es  # list results never echo space


# --- in-place maintenance: update_alert_rule, get_connector, update_connector ---


async def test_maintenance_tools_are_tagged_read_and_write(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert tools["get_connector"].annotations.readOnlyHint is True
    for name in ("update_alert_rule", "update_connector"):
        ann = tools[name].annotations
        assert ann.readOnlyHint is False and ann.destructiveHint is False
    # the tier tags themselves are pinned per tier in test_server_assembly


async def test_update_alert_rule_changes_only_passed_fields(mcp, gateway):
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", {**RULE, "tags": ["a"]})).data["id"]
        updated = await client.call_tool(
            "update_alert_rule", {"rule_id": rid, "schedule_interval": "5m"}
        )
    assert updated.data["schedule_interval"] == "5m"
    assert updated.data["name"] == "r" and updated.data["tags"] == ["a"]  # kept
    assert updated.data["enabled"] is False  # never toggled by an update
    assert "space" not in updated.data
    assert gateway.rule_updates == [{
        "rule_id": rid, "name": None, "tags": None, "schedule_interval": "5m",
        "params": None, "actions": None,
    }]


async def test_get_alert_rule_reads_params_and_actions_before_and_after_an_update(mcp):
    params = {"index": ["logs-*"], "threshold": [10000], "thresholdComparator": ">"}
    action = {"id": "c1", "group": "query matched", "params": {"message": "hi"}}
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", {**RULE, "params": params})).data["id"]
        before = await client.call_tool("get_alert_rule", {"rule_id": rid})
        await client.call_tool("update_alert_rule", {
            "rule_id": rid, "params": {"threshold": [100]}, "actions": [action],
        })
        after = await client.call_tool("get_alert_rule", {"rule_id": rid, "space": "sales"})
        listed = (await client.call_tool("list_alert_rules", {})).structured_content["result"]
    assert before.data["params"] == params and before.data["actions"] == []
    assert after.data["params"] == {**params, "threshold": [100]}  # merged, the rest kept
    assert after.data["actions"] == [action]
    assert after.data["name"] == "r" and after.data["space"] == "sales"
    assert "params" not in listed[0] and "actions" not in listed[0]  # the list stays concise


async def test_get_and_update_alert_rule_descriptions_point_to_each_other(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t.description for t in await client.list_tools()}
    assert "update_alert_rule" in tools["get_alert_rule"]
    assert "`params`" in tools["get_alert_rule"] and "`actions`" in tools["get_alert_rule"]
    assert "`uuid`" in tools["get_alert_rule"]  # passed back, it keeps the action's identity
    assert "get_alert_rule" in tools["update_alert_rule"]
    assert "get_alert_rule" in tools["list_alert_rules"]


async def test_update_alert_rule_has_no_enabled_parameter(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    props = tools["update_alert_rule"].inputSchema["properties"]
    assert "enabled" not in props
    assert set(props) == {
        "rule_id", "name", "tags", "schedule_interval", "params", "actions", "space"
    }


async def test_update_alert_rule_needs_a_field_before_the_gateway(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    AlertingToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    )
    async with Client(server) as client:
        with pytest.raises(ToolError, match=r"provide at least one field .*enable_alert_rule"):
            await client.call_tool("update_alert_rule", {"rule_id": "r1", "space": "sales"})
        with pytest.raises(ToolError, match=r"provide at least one field to update: name, config or secrets"):
            await client.call_tool("update_connector", {"connector_id": "c1", "space": "sales"})
    assert calls == []  # guard ran before the factory: no space validation


async def test_update_alert_rule_threads_space_and_echoes(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    AlertingToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    )
    async with Client(server) as client:
        rid = (await client.call_tool("create_alert_rule", RULE)).data["id"]
        updated = await client.call_tool(
            "update_alert_rule", {"rule_id": rid, "name": "renamed", "space": "sales"}
        )
    assert calls == [None, "sales"]
    assert updated.data["space"] == "sales" and updated.data["name"] == "renamed"


async def test_get_connector_returns_config_and_never_secrets(mcp, gateway):
    async with Client(mcp) as client:
        cid = (await client.call_tool("create_connector", {
            "name": "idx", "connector_type_id": ".index",
            "config": {"index": "alerts-out"}, "secrets": {"token": "s3cr3t"},
        })).data["id"]
        got = await client.call_tool("get_connector", {"connector_id": cid})
    assert got.data["config"] == {"index": "alerts-out"}
    assert got.data["connector_type_id"] == ".index"
    assert got.data["is_preconfigured"] is False
    assert "secrets" not in got.data and "s3cr3t" not in str(got.content)
    assert "space" not in got.data


async def test_update_connector_merges_config_and_never_echoes_secrets(mcp, gateway):
    async with Client(mcp) as client:
        cid = (await client.call_tool("create_connector", {
            "name": "idx", "connector_type_id": ".index",
            "config": {"index": "a", "refresh": False},
        })).data["id"]
        updated = await client.call_tool("update_connector", {
            "connector_id": cid, "config": {"index": "b"}, "secrets": {"token": "s3cr3t"},
            "space": "sales",
        })
    assert updated.data["config"] == {"index": "b", "refresh": False}  # merged
    assert updated.data["name"] == "idx"  # kept
    assert updated.data["space"] == "sales"
    assert gateway.connector_secrets[cid] == {"token": "s3cr3t"}  # reached the gateway
    assert "secrets" not in updated.data and "s3cr3t" not in str(updated.content)


async def test_connector_not_found_surfaces_as_tool_error(mcp):
    async with Client(mcp) as client:
        for tool, args in (
            ("get_connector", {"connector_id": "nope"}),
            ("update_connector", {"connector_id": "nope", "name": "x"}),
            ("update_alert_rule", {"rule_id": "nope", "name": "x"}),
        ):
            with pytest.raises(ToolError, match=r"not found"):
                await client.call_tool(tool, args)


@pytest.mark.parametrize("tool,args", [
    ("get_connector", {"connector_id": ""}),
    ("update_connector", {"connector_id": "", "name": "x"}),
    ("update_connector", {"connector_id": "c1", "name": ""}),
    ("update_alert_rule", {"rule_id": "", "name": "x"}),
    ("update_alert_rule", {"rule_id": "r1", "name": ""}),
    ("update_alert_rule", {"rule_id": "r1", "schedule_interval": ""}),
])
async def test_empty_identifiers_and_names_fail_the_schema(mcp, tool, args):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(tool, args)


# --- rule lifecycle: mute, snooze, API key, untrack (B1) ---

LIFECYCLE_TOOLS = (
    "mute_alert_rule", "unmute_alert_rule", "mute_alert", "unmute_alert",
    "snooze_alert_rule", "unsnooze_alert_rule", "update_alert_rule_api_key",
)


def _counting_server(gateway):
    calls = []

    def factory(space=None):
        calls.append(space)
        return gateway

    server = FastMCP("test")
    AlertingToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601")
    )
    return server, calls


async def test_lifecycle_tools_are_write_tier_and_say_which_repeat_safely(mcp):
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in LIFECYCLE_TOOLS:
        ann = tools[name].annotations
        assert ann.readOnlyHint is False and ann.destructiveHint is False, name
        assert ann.openWorldHint is False, name
    # a repeat of a mute, an unmute or an API-key refresh changes nothing more;
    # each snooze adds a schedule (Kibana keeps at most 5), so it is not idempotent
    for name in ("mute_alert_rule", "unmute_alert_rule", "mute_alert", "unmute_alert",
                 "unsnooze_alert_rule", "update_alert_rule_api_key"):
        assert tools[name].annotations.idempotentHint is True, name
    assert tools["snooze_alert_rule"].annotations.idempotentHint is False


async def test_mutes_show_in_get_alert_rule_and_unmutes_clear_them(mcp, gateway):
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", RULE)).data["id"]
        assert (await client.call_tool(
            "mute_alert", {"rule_id": rid, "alert_id": "JetBeats"})).data == {
            "rule_id": rid, "alert_id": "JetBeats", "muted": True}
        got = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
        assert got["muted"] is False and got["muted_alert_ids"] == ["JetBeats"]
        assert (await client.call_tool(
            "unmute_alert", {"rule_id": rid, "alert_id": "JetBeats"})).data == {
            "rule_id": rid, "alert_id": "JetBeats", "muted": False}
        assert (await client.call_tool("mute_alert_rule", {"rule_id": rid})).data == {
            "id": rid, "muted": True}
        got = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
        assert got["muted"] is True and got["muted_alert_ids"] == []
        with pytest.raises(ToolError, match=r"muted as a whole"):
            await client.call_tool("mute_alert", {"rule_id": rid, "alert_id": "JetBeats"})
        assert (await client.call_tool("unmute_alert_rule", {"rule_id": rid})).data == {
            "id": rid, "muted": False}
        got = (await client.call_tool("get_alert_rule", {"rule_id": rid})).data
    assert got["muted"] is False and got["muted_alert_ids"] == []
    assert gateway.alert_rules[rid].enabled is False  # muting never starts or stops a rule


async def test_snooze_returns_the_schedule_id_unsnooze_takes(mcp, gateway):
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", RULE)).data["id"]
        before = datetime.now(UTC)
        snoozed = (await client.call_tool(
            "snooze_alert_rule", {"rule_id": rid, "duration": "2h"})).data
        after = datetime.now(UTC)
        sid = snoozed["schedule_id"]
        assert snoozed["rule_id"] == rid and snoozed["duration"] == "2h"
        assert set(snoozed) == {"rule_id", "schedule_id", "duration", "start", "timezone",
                                "recurring"}
        sent = gateway.rule_snoozes[rid][sid]["start"]
        assert sent.tzinfo is not None and before <= sent <= after  # default: now, in UTC
        gone = (await client.call_tool(
            "unsnooze_alert_rule", {"rule_id": rid, "schedule_id": sid})).data
        assert gone == {"rule_id": rid, "schedule_id": sid, "unsnoozed": True}
        assert gateway.rule_snoozes[rid] == {}
        with pytest.raises(ToolError, match=r"not found"):
            await client.call_tool("unsnooze_alert_rule", {"rule_id": rid, "schedule_id": sid})


@pytest.mark.parametrize("start,expected", [
    ("2026-10-25T00:00:00Z", datetime(2026, 10, 25, tzinfo=UTC)),
    ("2026-10-25T02:00:00+02:00", datetime(2026, 10, 25, tzinfo=UTC)),
    ("2026-10-25T00:00:00.000Z", datetime(2026, 10, 25, tzinfo=UTC)),
    ("2026-10-25T00:00", datetime(2026, 10, 25, tzinfo=UTC)),  # no offset: read as UTC
    ("2026-10-25", datetime(2026, 10, 25, tzinfo=UTC)),
])
async def test_snooze_start_takes_any_iso_8601_instant(mcp, gateway, start, expected):
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", RULE)).data["id"]
        sid = (await client.call_tool("snooze_alert_rule", {
            "rule_id": rid, "duration": "30m", "start": start,
            "recurring": {"every": "1d", "occurrences": 3, "end": start},
        })).data["schedule_id"]
    sent = gateway.rule_snoozes[rid][sid]
    assert sent["start"] == expected and sent["start"].utcoffset() == timedelta(0)
    assert sent["recurring"] == {"every": "1d", "occurrences": 3, "end": expected}


@pytest.mark.parametrize("args,message", [
    ({"duration": "1w"}, r"duration '1w' is not a snooze duration — .*'30m', '2h', '1d'"),
    ({"duration": "90x"}, r"duration '90x' is not a snooze duration"),
    ({"duration": "1.5h"}, r"duration '1.5h' is not a snooze duration .*'90m'"),
    ({"duration": "0h"}, r"duration '0h' is not a snooze duration"),
    ({"duration": "1h", "start": "tomorrow"}, r"start 'tomorrow' is not an ISO-8601 date"),
    ({"duration": "1h", "recurring": {"every": "1d", "end": "soon"}},
     r"recurring\.end 'soon' is not an ISO-8601 date"),
])
async def test_snooze_arguments_are_checked_before_the_gateway(gateway, args, message):
    server, calls = _counting_server(gateway)
    async with Client(server) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool("snooze_alert_rule", {"rule_id": "r1", **args, "space": "s"})
    assert calls == []


async def test_disable_untrack_is_off_by_default_and_echoed_only_when_on(mcp, gateway):
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", {**RULE, "enabled": True})).data["id"]
        plain = (await client.call_tool("disable_alert_rule", {"rule_id": rid})).data
        untracked = (await client.call_tool(
            "disable_alert_rule", {"rule_id": rid, "untrack": True})).data
        tools = {t.name: t for t in await client.list_tools()}
    assert plain == {"id": rid, "enabled": False}  # byte-identical to before untrack existed
    assert untracked == {"id": rid, "enabled": False, "untracked": True}
    assert gateway.disable_untracks == [False, True]
    untrack = tools["disable_alert_rule"].inputSchema["properties"]["untrack"]
    assert untrack["default"] is False
    assert "untrack" not in tools["disable_alert_rule"].inputSchema.get("required", [])


async def test_update_alert_rule_api_key_reports_the_refresh(mcp, gateway):
    async with Client(mcp) as client:
        rid = (await client.call_tool("create_alert_rule", RULE)).data["id"]
        out = (await client.call_tool("update_alert_rule_api_key", {"rule_id": rid})).data
    assert out == {"id": rid, "api_key_updated": True}
    assert gateway.api_key_updates == [rid]
    assert "api_key" not in str(out).replace("api_key_updated", "")  # no key material


async def test_lifecycle_tool_descriptions_name_their_partners(mcp):
    async with Client(mcp) as client:
        d = {t.name: " ".join(t.description.split()) for t in await client.list_tools()}
    assert "unsnooze_alert_rule" in d["snooze_alert_rule"]
    assert "at most 5" in d["snooze_alert_rule"] and "schedule_id" in d["snooze_alert_rule"]
    assert "snooze_alert_rule" in d["unsnooze_alert_rule"]
    assert "unmute_alert_rule" in d["mute_alert_rule"] and "snooze_alert_rule" in d["mute_alert_rule"]
    assert "get_alert_rule" in d["mute_alert"] and "muted_alert_ids" in d["unmute_alert"]
    assert "mute_alert_rule" in d["get_alert_rule"]  # the read half shows the mute state
    assert "untrack" in d["disable_alert_rule"]


@pytest.mark.parametrize("tool,args", [
    ("mute_alert_rule", {"rule_id": ""}),
    ("unmute_alert_rule", {"rule_id": ""}),
    ("mute_alert", {"rule_id": "r1", "alert_id": ""}),
    ("unmute_alert", {"rule_id": "", "alert_id": "a"}),
    ("snooze_alert_rule", {"rule_id": "", "duration": "1h"}),
    ("unsnooze_alert_rule", {"rule_id": "r1", "schedule_id": ""}),
    ("update_alert_rule_api_key", {"rule_id": ""}),
])
async def test_lifecycle_tools_refuse_empty_ids_at_the_schema(gateway, tool, args):
    server, calls = _counting_server(gateway)
    async with Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool(tool, args)
    assert calls == []


async def test_lifecycle_tools_on_a_missing_rule_surface_not_found(mcp):
    async with Client(mcp) as client:
        for tool, args in (
            ("mute_alert_rule", {"rule_id": "nope"}),
            ("unmute_alert", {"rule_id": "nope", "alert_id": "a"}),
            ("snooze_alert_rule", {"rule_id": "nope", "duration": "1h"}),
            ("update_alert_rule_api_key", {"rule_id": "nope"}),
        ):
            with pytest.raises(ToolError, match=r"not found"):
                await client.call_tool(tool, args)
