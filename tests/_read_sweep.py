"""The read-tool sweep's arguments: one row per read-tier tool (D60).

tests/contract/test_read_tool_sweep.py calls every read-tagged tool once,
through a fastmcp Client against the seeded stack, with the arguments built
here. A row takes the ids the sweep gathered (`Ids`: objects read from the
seeded stack plus the throwaway ones the sweep creates and removes) and
returns the call's arguments. tests/unit/test_read_sweep_coverage.py and the
contract test both require the rows to match the registry's read tools
exactly, so a new read tool cannot ship without a live call.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

Ids = Mapping[str, str]
Row = Callable[[Ids], dict[str, Any]]


def _none(_: Ids) -> dict[str, Any]:
    return {}


def _one(param: str, key: str) -> Row:
    return lambda ids: {param: ids[key]}


SWEEP_ROWS: Mapping[str, Row] = MappingProxyType({
    # alerting
    "get_alert_rule": _one("rule_id", "alert_rule"),
    "get_alerting_health": _none,
    "get_connector": _one("connector_id", "connector"),
    "list_alert_rules": _none,
    "list_connectors": _none,
    "list_rule_types": _none,
    # cases
    "get_case": _one("case_id", "case"),
    "list_cases": _none,
    # dashboards
    "get_dashboard": _one("dashboard_id", "dashboard"),
    "get_visualization": _one("visualization_id", "visualization"),
    "search_dashboards": _none,
    "search_visualizations": _none,
    # data-management
    "describe_data_view": _one("data_view", "data_view"),
    "export_saved_objects": lambda ids: {"objects": [{"type": "dashboard", "id": ids["dashboard"]}]},
    "find_saved_objects": lambda ids: {"types": ["dashboard", "index-pattern"]},
    "get_default_data_view": _none,
    "get_saved_object": lambda ids: {"type": "dashboard", "id": ids["dashboard"]},
    "list_data_views": _none,
    "resolve_short_url": _one("slug", "short_url_slug"),
    # fleet
    "check_fleet_permissions": _none,
    "get_agent": _one("agent_id", "agent"),
    "get_agent_policy": _one("agent_policy_id", "agent_policy"),
    "get_agent_status_summary": _none,
    "get_enrollment_key": _one("key_id", "enrollment_key"),
    "get_fleet_settings": _none,
    "get_output_health": _one("output_id", "output"),
    "get_package": _one("name", "package"),
    "get_package_policy": _one("package_policy_id", "package_policy"),
    "list_agent_diagnostics": _one("agent_id", "agent"),
    "list_agent_policies": _none,
    "list_agent_tags": _none,
    "list_agent_versions": _none,
    "list_agents": _none,
    "list_enrollment_keys": _none,
    "list_fleet_server_hosts": _none,
    "list_installed_packages": _none,
    "list_outputs": _none,
    "list_package_categories": _none,
    "list_package_policies": _none,
    "list_packages": _none,
    "list_uninstall_tokens": _none,
    # observability
    "get_apm_agent_config": lambda ids: {"service_name": ids["apm_service"]},
    "get_synthetic_monitor": _one("monitor_id", "synthetic_monitor"),
    "get_uptime_settings": _none,
    "list_apm_agent_configs": _none,
    "list_apm_environments": _none,
    "list_apm_sourcemaps": _none,
    "list_synthetic_monitors": _none,
    "list_synthetic_params": _none,
    "list_synthetic_private_locations": _none,
    "search_apm_annotations": lambda ids: {
        "service_name": ids["apm_service"],
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
    # platform-admin
    "get_role": _one("role_name", "role"),
    "get_space": lambda ids: {"space_id": "default"},
    "get_upgrade_status": _none,
    "list_roles": _none,
    "list_spaces": _none,
    # platform-health
    "get_kibana_stats": _none,
    "get_kibana_status": _none,
    "get_task_manager_health": _none,
    # security-detections
    "export_exception_list": _one("list_id", "exception_list"),
    "find_detection_rules": _none,
    "find_exception_items": _one("list_id", "exception_list"),
    "find_exception_lists": _none,
    "find_timelines": _none,
    "find_value_list_items": _one("list_id", "value_list"),
    "find_value_lists": _none,
    "get_detection_rule": _one("rule_id", "detection_rule"),
    "get_exception_list": _one("list_id", "exception_list"),
    "get_prepackaged_rules_status": _none,
    "list_detection_rule_tags": _none,
    "search_detection_alerts": _none,
    # streams
    "get_stream": _one("name", "stream"),
    "get_stream_ingest": _one("name", "stream"),
    "list_streams": _none,
})


# List tools whose `result` must hold rows: the seeded stack holds them, or
# the sweep made one (a list tool that comes back empty there shapes nothing).
NON_EMPTY: frozenset[str] = frozenset({
    "list_alert_rules", "list_connectors", "list_rule_types", "list_cases",
    "search_dashboards", "search_visualizations", "list_data_views",
    "list_agent_policies", "list_agent_versions", "list_agents", "list_enrollment_keys",
    "list_installed_packages", "list_outputs", "list_package_categories",
    "list_package_policies", "list_packages", "list_uninstall_tokens",
    "list_apm_agent_configs", "list_synthetic_monitors", "list_synthetic_private_locations",
    "list_roles", "list_spaces",
    "find_detection_rules", "find_exception_items", "find_exception_lists",
    "find_value_list_items", "find_value_lists", "list_streams",
})


def coverage_problem(read_tools: set[str]) -> str | None:
    """None when the rows cover exactly the registry's read tools, else the
    failure text naming the fix."""
    missing = sorted(read_tools - SWEEP_ROWS.keys())
    stale = sorted(SWEEP_ROWS.keys() - read_tools)
    if not missing and not stale:
        return None
    parts = []
    if missing:
        parts.append(
            f"read tool(s) {missing} have no sweep row: add one to SWEEP_ROWS in "
            "tests/_read_sweep.py (arguments built from the sweep's ids; a new id "
            "comes from the matching list tool in tests/contract/test_read_tool_sweep.py)")
    if stale:
        parts.append(
            f"sweep row(s) {stale} name no read tool: remove them from SWEEP_ROWS "
            "in tests/_read_sweep.py (renamed, removed or moved to another tier)")
    return "; ".join(parts)
