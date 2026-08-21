import pytest
from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.core.errors import KibanaRejected
from kibana_mcp.server import _env_key_fallback, build_gateway_factory, build_server
from kibana_mcp.toolboxes.base import with_space
from tests.fakes import FakeGateway

# dashboards toolbox (data-view tools moved to data-management in #28).
DASHBOARDS_TOOLS = {
    "search_dashboards", "get_dashboard",
    "create_dashboard", "create_visualization", "add_panel", "update_panel",
    "add_esql_metric_panel", "add_esql_table_panel", "add_esql_xy_panel",
    "delete_dashboard", "delete_panel",
}
DASHBOARDS_WRITE = {
    "create_dashboard", "create_visualization", "add_panel", "update_panel",
    "add_esql_metric_panel", "add_esql_table_panel", "add_esql_xy_panel",
}
DASHBOARDS_DESTRUCTIVE = {"delete_dashboard", "delete_panel"}

# data-management toolbox (#28).
DATA_MGMT_TOOLS = {
    "list_data_views", "describe_data_view", "resolve_short_url", "export_saved_objects",
    "create_data_view", "create_short_url", "import_saved_objects",
    "delete_data_view", "delete_short_url", "overwrite_saved_objects",
}
DATA_MGMT_READ = {"list_data_views", "describe_data_view", "resolve_short_url", "export_saved_objects"}
DATA_MGMT_WRITE = {"create_data_view", "create_short_url", "import_saved_objects"}
DATA_MGMT_DESTRUCTIVE = {"delete_data_view", "delete_short_url", "overwrite_saved_objects"}

PLATFORM_HEALTH_TOOLS = {"get_kibana_status", "get_kibana_stats", "get_task_manager_health"}

# The default server pairs dashboards + data-management (config.py).
DEFAULT_TOOLS = DASHBOARDS_TOOLS | DATA_MGMT_TOOLS
DEFAULT_WRITE = DASHBOARDS_WRITE | DATA_MGMT_WRITE
DEFAULT_DESTRUCTIVE = DASHBOARDS_DESTRUCTIVE | DATA_MGMT_DESTRUCTIVE


async def visible_tools(settings):
    mcp = build_server(settings, lambda space=None: FakeGateway())
    async with Client(mcp) as client:
        return {t.name for t in await client.list_tools()}


async def test_default_tier_write_hides_destructive():
    names = await visible_tools(Settings(tier=Tier.WRITE))
    assert names == DEFAULT_TOOLS - DEFAULT_DESTRUCTIVE


async def test_read_tier_shows_only_reads():
    names = await visible_tools(Settings(tier=Tier.READ))
    assert names == DEFAULT_TOOLS - DEFAULT_WRITE - DEFAULT_DESTRUCTIVE


async def test_destructive_tier_shows_everything():
    names = await visible_tools(Settings(tier=Tier.DESTRUCTIVE))
    assert names == DEFAULT_TOOLS


ALERTING_TOOLS = {
    "list_alert_rules", "get_alert_rule", "get_alerting_health", "list_connectors",
    "create_alert_rule", "enable_alert_rule", "disable_alert_rule", "create_connector",
    "delete_alert_rule", "delete_connector", "execute_connector",
}
ALERTING_READ = {"list_alert_rules", "get_alert_rule", "get_alerting_health", "list_connectors"}


async def test_unknown_toolbox_fails_fast():
    with pytest.raises(ValueError, match="no-such-toolbox"):
        build_server(Settings(toolboxes=["no-such-toolbox"]), lambda space=None: FakeGateway())


async def test_alerting_toolbox_boots_all_tiers():
    names = await visible_tools(Settings(toolboxes=["alerting"], tier=Tier.DESTRUCTIVE))
    assert names == ALERTING_TOOLS
    read_only = await visible_tools(Settings(toolboxes=["alerting"], tier=Tier.READ))
    assert read_only == ALERTING_READ


CASES_TOOLS = {
    "list_cases", "get_case", "create_case", "update_case", "add_case_comment", "delete_case",
}


async def test_cases_toolbox_boots_all_tiers():
    names = await visible_tools(Settings(toolboxes=["cases"], tier=Tier.DESTRUCTIVE))
    assert names == CASES_TOOLS
    read_only = await visible_tools(Settings(toolboxes=["cases"], tier=Tier.READ))
    assert read_only == {"list_cases", "get_case"}


async def test_platform_health_toolbox_boots_read_only():
    names = await visible_tools(Settings(toolboxes=["platform-health"]))
    assert names == PLATFORM_HEALTH_TOOLS  # all read-tier, visible at default write tier


OBSERVABILITY_TOOLS = {
    "list_synthetic_monitors", "get_synthetic_monitor", "list_synthetic_params",
    "list_synthetic_private_locations", "get_uptime_settings",
    "list_apm_agent_configs", "get_apm_agent_config", "list_apm_environments",
    "list_apm_sourcemaps", "search_apm_annotations",
}


async def test_observability_toolbox_boots_read_only():
    names = await visible_tools(Settings(toolboxes=["observability"]))
    assert names == OBSERVABILITY_TOOLS  # all read-tier, visible at default write tier


SECURITY_DETECTIONS_READ = {
    "find_detection_rules", "get_detection_rule", "get_prepackaged_rules_status",
    "list_detection_rule_tags", "search_detection_alerts", "find_exception_lists",
    "get_exception_list", "find_exception_items", "find_value_lists",
    "find_value_list_items", "find_timelines",
}
SECURITY_DETECTIONS_WRITE = {
    "create_detection_rule", "create_exception_list", "create_exception_item",
    "update_detection_rule", "replace_detection_rule", "enable_detection_rule",
    "disable_detection_rule", "create_value_list", "create_value_list_item",
}
SECURITY_DETECTIONS_DESTRUCTIVE = {
    "delete_detection_rule", "delete_exception_list", "delete_exception_item",
    "delete_value_list", "delete_value_list_item",
}
SECURITY_DETECTIONS_TOOLS = (
    SECURITY_DETECTIONS_READ | SECURITY_DETECTIONS_WRITE | SECURITY_DETECTIONS_DESTRUCTIVE
)


async def test_security_detections_default_tier_hides_destructive():
    # default (write) tier: reads + writes visible, destructive hidden.
    names = await visible_tools(Settings(toolboxes=["security-detections"]))
    assert names == SECURITY_DETECTIONS_READ | SECURITY_DETECTIONS_WRITE


async def test_security_detections_read_tier_shows_only_reads():
    names = await visible_tools(Settings(toolboxes=["security-detections"], tier=Tier.READ))
    assert names == SECURITY_DETECTIONS_READ


async def test_security_detections_destructive_tier_shows_everything():
    names = await visible_tools(
        Settings(toolboxes=["security-detections"], tier=Tier.DESTRUCTIVE)
    )
    assert names == SECURITY_DETECTIONS_TOOLS


PLATFORM_ADMIN_READ = {
    "list_spaces", "get_space", "list_roles", "get_role", "get_upgrade_status",
}
PLATFORM_ADMIN_WRITE = {"create_space", "update_space", "create_or_update_role"}
PLATFORM_ADMIN_DESTRUCTIVE = {"delete_space", "delete_role"}


async def test_platform_admin_tier_boundary_acceptance():
    # The tier boundary is the safety mechanism: the space/role delete tools must be
    # HIDDEN (not merely registered) below the destructive tier.
    assert await visible_tools(Settings(toolboxes=["platform-admin"], tier=Tier.READ)) == (
        PLATFORM_ADMIN_READ)
    assert await visible_tools(Settings(toolboxes=["platform-admin"], tier=Tier.WRITE)) == (
        PLATFORM_ADMIN_READ | PLATFORM_ADMIN_WRITE)  # destructive hidden at default write tier
    assert await visible_tools(Settings(toolboxes=["platform-admin"], tier=Tier.DESTRUCTIVE)) == (
        PLATFORM_ADMIN_READ | PLATFORM_ADMIN_WRITE | PLATFORM_ADMIN_DESTRUCTIVE)


STREAMS_READ = {"list_streams", "get_stream", "get_stream_ingest"}
STREAMS_WRITE = {
    "enable_streams", "resync_streams", "fork_stream",
    "set_stream_processing", "deactivate_fork",
}
STREAMS_DESTRUCTIVE = {
    "set_stream_retention", "delete_stream", "disable_streams", "activate_fork",
}


async def test_streams_tier_boundary_acceptance():
    # The tier boundary is the streams write tier's safety mechanism: destructive
    # tools (activate_fork included -- it diverts live documents) must be HIDDEN
    # (not merely registered) below the destructive tier.
    assert await visible_tools(Settings(toolboxes=["streams"], tier=Tier.READ)) == STREAMS_READ
    writes = await visible_tools(Settings(toolboxes=["streams"], tier=Tier.WRITE))
    assert writes == STREAMS_READ | STREAMS_WRITE  # activate_fork hidden at the write tier
    assert "activate_fork" not in writes
    dest = await visible_tools(Settings(toolboxes=["streams"], tier=Tier.DESTRUCTIVE))
    assert len(dest) == 12
    assert dest == STREAMS_READ | STREAMS_WRITE | STREAMS_DESTRUCTIVE


FLEET_READ = {
    "get_fleet_settings", "check_fleet_permissions",
    "list_agents", "get_agent", "get_agent_status_summary", "list_agent_versions",
    "list_agent_policies", "get_agent_policy", "list_package_policies", "get_package_policy",
    "list_enrollment_keys", "get_enrollment_key", "list_uninstall_tokens",
    "list_packages", "list_installed_packages", "get_package", "list_package_categories",
    "list_outputs", "get_output_health", "list_fleet_server_hosts",
}
FLEET_WRITE = {
    "create_agent_policy", "update_agent_policy",
    "create_package_policy", "update_package_policy",
    "create_output", "update_output",
}
FLEET_DESTRUCTIVE = {
    "delete_agent_policy", "delete_package_policy", "delete_output",
    "reassign_agent", "upgrade_agent", "unenroll_agent",
    "bulk_reassign", "bulk_upgrade", "bulk_unenroll",
}


async def test_fleet_toolbox_boots_all_tiers():
    # The tier boundary is the fleet write/destructive tier's safety mechanism:
    # destructive tools (which command live agents or delete objects) must be
    # HIDDEN (not merely registered) below the destructive tier.
    reads = await visible_tools(Settings(toolboxes=["fleet"], tier=Tier.READ))
    assert len(reads) == 20 and reads == FLEET_READ and not (reads & FLEET_DESTRUCTIVE)
    writes = await visible_tools(Settings(toolboxes=["fleet"], tier=Tier.WRITE))
    assert FLEET_WRITE <= writes and not (writes & FLEET_DESTRUCTIVE)  # destructive HIDDEN
    assert writes == FLEET_READ | FLEET_WRITE
    dest = await visible_tools(Settings(toolboxes=["fleet"], tier=Tier.DESTRUCTIVE))
    assert len(dest) == 35 and FLEET_DESTRUCTIVE <= dest
    assert dest == FLEET_READ | FLEET_WRITE | FLEET_DESTRUCTIVE


async def test_data_management_toolbox_boots_all_tiers():
    names = await visible_tools(Settings(toolboxes=["data-management"], tier=Tier.DESTRUCTIVE))
    assert names == DATA_MGMT_TOOLS
    read_only = await visible_tools(Settings(toolboxes=["data-management"], tier=Tier.READ))
    assert read_only == DATA_MGMT_READ


async def test_platform_health_combines_with_dashboards():
    names = await visible_tools(
        Settings(toolboxes=["dashboards", "platform-health"], tier=Tier.DESTRUCTIVE)
    )
    assert names == DASHBOARDS_TOOLS | PLATFORM_HEALTH_TOOLS


async def test_read_only_explorer_profile_is_nine_read_tools():
    # read-only-explorer: dashboards + platform-health + data-management at read tier
    # (data-management read grew by export_saved_objects with #37).
    names = await visible_tools(
        Settings(
            toolboxes=["dashboards", "platform-health", "data-management"], tier=Tier.READ
        )
    )
    assert names == {"search_dashboards", "get_dashboard"} | PLATFORM_HEALTH_TOOLS | DATA_MGMT_READ
    assert len(names) == 9


def _probe_toolbox(box_name, retval):
    """A throwaway toolbox that registers one read tool named 'shared_probe'."""

    class _Probe:
        name = box_name

        def register(self, mcp, deps):
            @mcp.tool(tags={box_name, "read"})
            def shared_probe() -> str:
                return retval

    return _Probe()


async def test_overlapping_tools_dedupe_first_registered_wins(monkeypatch):
    # Two toolboxes exposing the same tool name compose without crashing; the
    # first one in KIBANA_MCP_TOOLBOXES wins, deterministically and silently.
    from kibana_mcp.toolboxes import TOOLBOXES

    monkeypatch.setitem(TOOLBOXES, "box_a", _probe_toolbox("box_a", "A"))
    monkeypatch.setitem(TOOLBOXES, "box_b", _probe_toolbox("box_b", "B"))
    mcp = build_server(Settings(toolboxes=["box_a", "box_b"]), lambda space=None: FakeGateway())
    async with Client(mcp) as client:
        names = [t.name for t in await client.list_tools()]
        assert names.count("shared_probe") == 1  # deduped, not duplicated
        result = await client.call_tool("shared_probe", {})
    assert result.data == "A"  # box_a registered first -> it wins


def test_env_key_fallback_stdio_with_key_returns_key(monkeypatch):
    monkeypatch.setenv("KIBANA_API_KEY", "sekret")
    settings = Settings(transport="stdio")
    assert _env_key_fallback(settings) == "sekret"


def test_env_key_fallback_http_with_key_no_flag_returns_none(monkeypatch):
    monkeypatch.setenv("KIBANA_API_KEY", "sekret")
    settings = Settings(transport="http", allow_env_key_http=False)
    assert _env_key_fallback(settings) is None


def test_env_key_fallback_http_with_key_and_flag_returns_key(monkeypatch):
    monkeypatch.setenv("KIBANA_API_KEY", "sekret")
    settings = Settings(transport="http", allow_env_key_http=True)
    assert _env_key_fallback(settings) == "sekret"


def test_env_key_fallback_stdio_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("KIBANA_API_KEY", raising=False)
    settings = Settings(transport="stdio")
    assert _env_key_fallback(settings) is None


def _settings(**over):
    # public_kibana_url pinned to None: BaseSettings would otherwise read
    # ambient KIBANA_PUBLIC_URL and trip the wrong guard on some shells
    base = dict(kibana_url="http://kb:5601", api_key="k", transport="stdio",
                public_kibana_url=None)
    base.update(over)
    return Settings(**base)


def test_factory_refuses_pinned_kibana_url_with_space():
    factory = build_gateway_factory(_settings(kibana_url="http://kb:5601/s/team-a"))
    # the distinguishing prefix: proves CONNECT's guard fired, not the
    # factory's public-URL half (which would say "public Kibana URL")
    with pytest.raises(KibanaRejected, match="KIBANA_URL is already space-pinned"):
        factory("sales")   # raised by connect — no HTTP happens before the guard


def test_factory_refuses_pinned_public_url_with_space():
    factory = build_gateway_factory(
        _settings(public_kibana_url="https://pub.example.com/s/team-a")
    )
    with pytest.raises(KibanaRejected, match="public Kibana URL"):
        factory("sales")   # raised by the factory's own half


def test_factory_neither_pinned_builds_scoped_gateway(monkeypatch):
    import kibana_mcp.adapters.kibana.gateway as gateway_mod
    class _Scoped:
        pass
    class _Stub:
        def space(self, space_id, validate=True):
            assert (space_id, validate) == ("sales", True)
            return _Scoped()
        def close(self): pass
    monkeypatch.setattr(gateway_mod.kibana, "Kibana", lambda url, api_key: _Stub())
    gw = build_gateway_factory(_settings())("sales")
    assert gw._space == "sales"


def test_with_space_echo_helper():
    assert with_space({"id": "x"}, None) == {"id": "x"}
    src = {"id": "x"}
    out = with_space(src, "sales")
    assert out == {"id": "x", "space": "sales"}
    assert src == {"id": "x"}  # copy, never in-place
