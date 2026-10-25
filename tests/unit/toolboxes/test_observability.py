"""The observability toolbox: 10 read tools over synthetics + uptime + apm-config,
and the Synthetics write tier (7 write, 3 destructive; D46). Driven through the
in-memory fastmcp Client against a FakeGateway."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.models import ApmAnnotation
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.observability.toolbox import ObservabilityToolbox
from tests.fakes import FakeGateway

OBSERVABILITY_READ = {
    "list_synthetic_monitors", "get_synthetic_monitor", "list_synthetic_params",
    "list_synthetic_private_locations", "get_uptime_settings",
    "list_apm_agent_configs", "get_apm_agent_config", "list_apm_environments",
    "list_apm_sourcemaps", "search_apm_annotations",
}
OBSERVABILITY_WRITE = {
    "create_synthetic_monitor", "update_synthetic_monitor", "test_synthetic_monitor",
    "create_synthetic_param", "update_synthetic_param",
    "create_synthetic_private_location", "update_synthetic_private_location",
}
OBSERVABILITY_DESTRUCTIVE = {
    "delete_synthetic_monitor", "delete_synthetic_param", "delete_synthetic_private_location",
}
OBSERVABILITY_TOOLS = OBSERVABILITY_READ | OBSERVABILITY_WRITE | OBSERVABILITY_DESTRUCTIVE


@pytest.fixture()
def gateway():
    return FakeGateway()


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    ObservabilityToolbox().register(server, deps)
    return server


async def test_observability_tools_and_hints(mcp):
    # The tier gating itself is pinned in test_server_assembly.
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == OBSERVABILITY_TOOLS
    for name, t in tools.items():
        tier = "read" if name in OBSERVABILITY_READ else (
            "write" if name in OBSERVABILITY_WRITE else "destructive")
        assert t.annotations.read_only_hint is (tier == "read"), name
        assert t.annotations.open_world_hint is False, name
        if tier != "read":
            assert t.annotations.destructive_hint is (tier == "destructive"), name
        # D10: the observability toolbox takes no `space` on any tool.
        assert "space" not in t.input_schema.get("properties", {}), name
    idempotent = {n for n, t in tools.items() if t.annotations.idempotent_hint}
    assert idempotent - OBSERVABILITY_READ == {
        "update_synthetic_monitor", "update_synthetic_param", "update_synthetic_private_location",
    } | OBSERVABILITY_DESTRUCTIVE


async def test_list_synthetic_monitors_serializes(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_synthetic_monitors", {})
    assert result.data[0]["id"] == "mon-1"
    assert result.data[0]["schedule"] == "10m"
    assert result.data[0]["target"] == "https://example.com"


async def test_get_synthetic_monitor_found(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_synthetic_monitor", {"monitor_id": "mon-1"})
    assert result.data["name"] == "home"


async def test_get_synthetic_monitor_missing_is_tool_error(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_synthetic_monitor", {"monitor_id": "nope"})


async def test_list_apm_environments_surfaces_sentinel(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("list_apm_environments", {})
    assert result.data[0]["name"] == "ALL_OPTION_VALUE"


async def test_get_uptime_settings_serializes_nested_email(mcp):
    async with Client(mcp) as client:
        result = await client.call_tool("get_uptime_settings", {})
    assert result.data["heartbeat_indices"] == "heartbeat-*"
    assert result.data["default_email"] == {"to": [], "cc": [], "bcc": []}


async def test_search_apm_annotations_defaults_environment(mcp, gateway):
    gateway.apm_annotations = [
        ApmAnnotation(id="a1", timestamp="2026-07-12T00:00:00Z", text="v2 deploy", type="deployment")
    ]
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_apm_annotations",
            {"service_name": "checkout", "start": "2026-07-01T00:00:00Z", "end": "2026-07-02T00:00:00Z"},
        )
    assert result.data[0]["id"] == "a1"
    assert result.data[0]["type"] == "deployment"


# --- Synthetics write tier (D46) ---------------------------------------------

HTTP = {"type": "http", "name": "api health", "url": "https://example.com/health",
        "private_locations": ["loc-1"]}


async def test_create_synthetic_monitor_returns_the_read_shape(mcp, gateway):
    async with Client(mcp) as client:
        created = (await client.call_tool("create_synthetic_monitor", {
            **HTTP, "schedule": "5m", "tags": ["api"],
        })).data
        got = (await client.call_tool("get_synthetic_monitor", {"monitor_id": created["id"]})).data
    assert created == got
    assert created["name"] == "api health" and created["type"] == "http"
    assert created["schedule"] == "5m" and created["target"] == "https://example.com/health"
    assert created["enabled"] is True and created["tags"] == ["api"]
    assert gateway.monitor_writes[-1] == ("create", {
        "type": "http", "name": "api health", "private_locations": ["loc-1"],
        "url": "https://example.com/health", "host": None, "inline_script": None,
        "schedule": "5m", "enabled": True, "tags": ["api"],
    })


async def test_create_synthetic_monitor_takes_a_location_label(mcp, gateway):
    async with Client(mcp) as client:
        created = (await client.call_tool(
            "create_synthetic_monitor", {**HTTP, "private_locations": ["dc1"]})).data
    assert created["locations"] == ["dc1"]


@pytest.mark.parametrize(("monitor_type", "field"), [
    ("http", "url"), ("tcp", "host"), ("icmp", "host"), ("browser", "inline_script"),
])
async def test_create_synthetic_monitor_requires_the_types_target(mcp, gateway, monitor_type, field):
    args = {"type": monitor_type, "name": "m", "private_locations": ["loc-1"]}
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=rf"an? {monitor_type} monitor needs `{field}`"):
            await client.call_tool("create_synthetic_monitor", args)
    assert gateway.monitor_writes == []


async def test_create_synthetic_monitor_refuses_another_types_target(mcp, gateway):
    # Kibana stores a url on a tcp monitor (probed on 9.5.4): refuse it here.
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"`url` does not apply to a tcp monitor .*`host`"):
            await client.call_tool("create_synthetic_monitor", {
                "type": "tcp", "name": "db", "host": "db:5432", "url": "http://db",
                "private_locations": ["loc-1"],
            })
    assert gateway.monitor_writes == []


@pytest.mark.parametrize("schedule", ["7m", "45s", "90m", "1h", "0m", "10", "m", "1.5m", "300m"])
async def test_create_synthetic_monitor_refuses_a_schedule_kibana_rejects(mcp, gateway, schedule):
    async with Client(mcp) as client:
        with pytest.raises(
            ToolError, match=r"not a Synthetics schedule — .*1, 2, 3, 5, 10, 15, 20, 30, 60, 120 or 240"
        ):
            await client.call_tool("create_synthetic_monitor", {**HTTP, "schedule": schedule})
    assert gateway.monitor_writes == []


@pytest.mark.parametrize("schedule", ["1m", "240m", "10s", "30s", "3m"])
async def test_create_synthetic_monitor_accepts_kibanas_schedules(mcp, schedule):
    async with Client(mcp) as client:
        created = (await client.call_tool(
            "create_synthetic_monitor", {**HTTP, "schedule": schedule})).data
    assert created["schedule"] == schedule


async def test_create_synthetic_monitor_names_unknown_locations(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=(
            r"private location\(s\) \['nope'\] not found — pass an id or label from "
            r"list_synthetic_private_locations: loc-1 \(dc1\)"
        )):
            await client.call_tool("create_synthetic_monitor", {**HTTP, "private_locations": ["nope"]})
    assert gateway.monitor_writes == []


async def test_create_synthetic_monitor_without_any_location_names_the_create_tool(mcp, gateway):
    gateway.synthetic_private_locations = []
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"no private location exists — create one with "
                                            r"create_synthetic_private_location"):
            await client.call_tool("create_synthetic_monitor", HTTP)


async def test_create_synthetic_monitor_schema_refuses_empty_locations(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("create_synthetic_monitor", {**HTTP, "private_locations": []})
    assert gateway.monitor_writes == []


async def test_update_synthetic_monitor_sends_only_the_given_fields(mcp, gateway):
    async with Client(mcp) as client:
        updated = (await client.call_tool("update_synthetic_monitor", {
            "monitor_id": "mon-1", "enabled": False, "schedule": "15m",
        })).data
    assert updated["enabled"] is False and updated["schedule"] == "15m"
    assert updated["name"] == "home"  # kept: Kibana's PUT is partial (probed)
    assert gateway.monitor_writes[-1] == ("update", "mon-1", {"enabled": False, "schedule": "15m"})


async def test_update_synthetic_monitor_needs_a_field(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"provide at least one field to update"):
            await client.call_tool("update_synthetic_monitor", {"monitor_id": "mon-1"})
    assert gateway.monitor_writes == []


async def test_update_synthetic_monitor_refuses_another_types_target(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=(
            r"monitor 'mon-1' is an http monitor: it takes `url`, not `host` — the type cannot "
            r"change; create a tcp or an icmp monitor with create_synthetic_monitor"
        )):
            await client.call_tool("update_synthetic_monitor", {"monitor_id": "mon-1", "host": "x:1"})
    assert gateway.monitor_writes == []


async def test_update_synthetic_monitor_checks_new_locations(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"\['dc9'\] not found"):
            await client.call_tool(
                "update_synthetic_monitor", {"monitor_id": "mon-1", "private_locations": ["dc9"]})
    assert gateway.monitor_writes == []


async def test_update_synthetic_monitor_missing_is_tool_error(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="not found"):
            await client.call_tool("update_synthetic_monitor", {"monitor_id": "nope", "enabled": True})
        with pytest.raises(ToolError, match="not found"):  # through the type pre-read
            await client.call_tool("update_synthetic_monitor", {"monitor_id": "nope", "url": "http://x"})


async def test_test_synthetic_monitor_returns_the_run_id(mcp, gateway):
    async with Client(mcp) as client:
        run = (await client.call_tool("test_synthetic_monitor", {"monitor_id": "mon-1"})).data
    assert run == {"monitor_id": "mon-1", "test_run_id": "run-1"}


async def test_delete_synthetic_monitor(mcp, gateway):
    async with Client(mcp) as client:
        deleted = (await client.call_tool("delete_synthetic_monitor", {"monitor_id": "mon-1"})).data
        with pytest.raises(ToolError, match="not found"):
            await client.call_tool("delete_synthetic_monitor", {"monitor_id": "mon-1"})
    assert deleted == {"id": "mon-1", "deleted": True}
    assert gateway.synthetic_monitors == []


async def test_create_synthetic_param_never_returns_the_value(mcp, gateway):
    async with Client(mcp) as client:
        created = await client.call_tool("create_synthetic_param", {
            "key": "db_password", "value": "s3cret-value", "description": "db", "tags": ["db"],
        })
    assert created.data == {"id": "p-2", "key": "db_password", "description": "db", "tags": ["db"]}
    assert "s3cret-value" not in str(created.content)
    assert gateway.param_writes[-1][0] == "create"


async def test_create_synthetic_param_refuses_a_key_in_use(mcp, gateway):
    # Kibana stores a second param with the same key (probed on 9.5.4).
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=(
            r"a parameter with key 'api_token' already exists \(id 'p-1'\) — change it with "
            r"update_synthetic_param"
        )) as refused:
            await client.call_tool("create_synthetic_param", {"key": "api_token", "value": "v-123"})
    assert "v-123" not in str(refused.value)
    assert gateway.param_writes == []


async def test_update_synthetic_param_never_returns_a_value(mcp, gateway):
    async with Client(mcp) as client:
        updated = await client.call_tool(
            "update_synthetic_param", {"param_id": "p-1", "value": "rotated-value"})
    assert updated.data == {"id": "p-1", "key": "api_token", "description": "RUM token",
                            "tags": ["shared"]}
    assert "rotated-value" not in str(updated.content)
    assert gateway.param_writes[-1] == ("update", "p-1", {"value": "rotated-value"})


async def test_update_synthetic_param_needs_a_field(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"provide at least one field to update: key, value, "
                                            r"description or tags"):
            await client.call_tool("update_synthetic_param", {"param_id": "p-1"})


async def test_update_synthetic_param_refuses_another_params_key(mcp, gateway):
    gateway.synthetic_params.append(
        gateway.synthetic_params[0].__class__(id="p-9", key="other", description="", tags=()))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"key 'other' already exists \(id 'p-9'\)"):
            await client.call_tool("update_synthetic_param", {"param_id": "p-1", "key": "other"})
        # Its own key is not a clash.
        same = (await client.call_tool(
            "update_synthetic_param", {"param_id": "p-1", "key": "api_token"})).data
    assert same["key"] == "api_token"


async def test_delete_synthetic_param(mcp, gateway):
    async with Client(mcp) as client:
        deleted = (await client.call_tool("delete_synthetic_param", {"param_id": "p-1"})).data
        with pytest.raises(ToolError, match="not found"):
            await client.call_tool("delete_synthetic_param", {"param_id": "p-1"})
    assert deleted == {"id": "p-1", "deleted": True}


async def test_create_synthetic_private_location(mcp, gateway):
    async with Client(mcp) as client:
        created = (await client.call_tool("create_synthetic_private_location", {
            "label": "dc2", "agent_policy_id": "ap-2", "tags": ["eu"],
        })).data
    assert created == {"id": "loc-2", "label": "dc2", "agent_policy_id": "ap-2",
                       "is_invalid": False, "tags": ["eu"]}


async def test_create_synthetic_private_location_refuses_a_taken_label(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"a private location labelled 'dc1' already exists "
                                            r"\(id 'loc-1'\) — pick another label"):
            await client.call_tool(
                "create_synthetic_private_location", {"label": "dc1", "agent_policy_id": "ap-2"})


async def test_create_synthetic_private_location_refuses_a_taken_policy(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=(
            r"agent policy 'ap-1' already backs private location 'dc1' \(id 'loc-1'\) — each "
            r"private location needs its own agent policy: create one with create_agent_policy"
        )):
            await client.call_tool(
                "create_synthetic_private_location", {"label": "dc2", "agent_policy_id": "ap-1"})


async def test_update_synthetic_private_location_renames(mcp, gateway):
    async with Client(mcp) as client:
        renamed = (await client.call_tool(
            "update_synthetic_private_location", {"location_id": "loc-1", "label": "dc1-east"})).data
        # Its own label is not a clash.
        again = (await client.call_tool(
            "update_synthetic_private_location", {"location_id": "loc-1", "label": "dc1-east"})).data
    assert renamed["label"] == "dc1-east" and renamed["agent_policy_id"] == "ap-1"
    assert again == renamed


async def test_update_synthetic_private_location_refuses_another_locations_label(mcp, gateway):
    gateway.synthetic_private_locations.append(gateway.synthetic_private_locations[0].__class__(
        id="loc-9", label="dc9", agent_policy_id="ap-9", is_invalid=False, tags=()))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"labelled 'dc9' already exists \(id 'loc-9'\)"):
            await client.call_tool(
                "update_synthetic_private_location", {"location_id": "loc-1", "label": "dc9"})


async def test_delete_synthetic_private_location(mcp, gateway):
    async with Client(mcp) as client:
        deleted = (await client.call_tool(
            "delete_synthetic_private_location", {"location_id": "loc-1"})).data
    assert deleted == {"id": "loc-1", "deleted": True}
    assert gateway.synthetic_private_locations == []


@pytest.mark.parametrize(("tool", "arg"), [
    ("update_synthetic_monitor", "monitor_id"), ("test_synthetic_monitor", "monitor_id"),
    ("delete_synthetic_monitor", "monitor_id"), ("update_synthetic_param", "param_id"),
    ("delete_synthetic_param", "param_id"), ("update_synthetic_private_location", "location_id"),
    ("delete_synthetic_private_location", "location_id"),
])
async def test_empty_ids_are_refused_at_the_schema(mcp, gateway, tool, arg):
    extra = {"update_synthetic_monitor": {"enabled": True}, "update_synthetic_param": {"tags": []},
             "update_synthetic_private_location": {"label": "x"}}.get(tool, {})
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(tool, {arg: "", **extra})
    assert gateway.monitor_writes == [] and gateway.param_writes == []
