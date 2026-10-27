"""Unit coverage for the shared live-model tasks (tests/_live_tasks.py) and the
`claude -p` driver (tests/_claude_cli.py).

The prompts are pre-registered, so their bytes are pinned here: the three
LM Studio prompts must stay what the local tier has always sent, and the
fourth what scripts/experiment/CLAUDE_E2E.md registered. The scorers and the
key-isolation helpers run only inside live tiers, where a broken branch would
silently mis-classify runs or leak a key, so each branch is pinned against
fakes.
"""

import json

import pytest

from tests import _claude_cli as cli
from tests import _live_tasks as tasks
from tests.fakes import FakeGateway

MARKER = "E2E cafe0123"
SPACE = "e2e-haiku-0badf00d"


def test_prompts_are_the_frozen_texts():
    assert tasks.flights_dashboard_prompt(MARKER) == (
        "Using the kibana tools, create a dashboard titled 'E2E cafe0123 flight prices' "
        "with a bar chart of the average ticket price by carrier over the last 7 days, "
        "using the flights sample data. Make exactly one create-dashboard call, then stop."
    )
    assert tasks.space_dashboard_prompt(SPACE, MARKER) == (
        "Using the kibana tools, create a new Kibana space with id 'e2e-haiku-0badf00d'. "
        "Inside that space, create a data view for the index pattern "
        "'kibana_sample_data_flights', then create a dashboard titled 'E2E cafe0123 flight "
        "prices' with a bar chart of the average ticket price by carrier over "
        "the last 7 days, using that data view."
    )
    assert tasks.alerting_space_prompt(SPACE, MARKER) == (
        "Using the kibana tools, create a new Kibana space with id 'e2e-haiku-0badf00d'. "
        "Inside that space, create an '.es-query' alert rule named "
        "'E2E cafe0123 flight watch' with consumer 'stackAlerts' that fires when "
        "more than 0 documents match in the index 'kibana_sample_data_flights' (time "
        "field 'timestamp') over the last 5 minutes, then enable that rule."
    )
    assert tasks.fix_rule_threshold_prompt(MARKER) == (
        "Using the kibana tools, fix the existing alert rule named "
        "'E2E cafe0123 late flights'. It should fire when more than 100 documents "
        "match, but its threshold was set to 10000 by mistake. Correct the threshold "
        "of that rule in place and change nothing else: do not create, delete, enable "
        "or disable any rule."
    )


def _stored_rule(**changes):
    """A rule as Kibana's GET returns it after the seed (defaults added)."""
    seed = tasks.fix_rule_seed_args(MARKER)
    body = {
        "id": "r1",
        "name": seed["name"],
        "tags": seed["tags"],
        "enabled": seed["enabled"],
        "schedule": {"interval": seed["schedule_interval"]},
        "params": {**seed["params"], "aggType": "count", "groupBy": "all",
                   "excludeHitsFromPreviousRun": True},
    }
    params = changes.pop("params", {})
    body.update(changes)
    body["params"] = {**body["params"], **params}
    return body


def test_seed_is_disabled_and_wrong():
    seed = tasks.fix_rule_seed_args(MARKER)
    assert seed["enabled"] is False
    assert seed["params"]["threshold"] == [tasks.FIX_RULE_WRONG_THRESHOLD]
    assert seed["name"] == "E2E cafe0123 late flights"


def test_fix_rule_full_pass():
    before = tasks.rule_snapshot(_stored_rule())
    after = tasks.rule_snapshot(_stored_rule(params={"threshold": [100]}))
    out = tasks.score_fix_rule_run(before, after, ["r1"])
    assert out["passed"] and out["first_missing"] is None


def test_fix_rule_deleted_scores_s1():
    before = tasks.rule_snapshot(_stored_rule())
    out = tasks.score_fix_rule_run(before, None, [])
    assert out["first_missing"] == "s1_rule_kept" and not out["passed"]


def test_fix_rule_recreated_scores_s1():
    before = tasks.rule_snapshot(_stored_rule())
    after = tasks.rule_snapshot(_stored_rule(id="r2", params={"threshold": [100]}))
    out = tasks.score_fix_rule_run(before, after, ["r2"])
    assert out["first_missing"] == "s1_rule_kept"


@pytest.mark.parametrize("params", [{"threshold": [10000]}, {"threshold": [100], "thresholdComparator": ">="},
                                    {"threshold": 100}])
def test_fix_rule_wrong_threshold_scores_s2(params):
    before = tasks.rule_snapshot(_stored_rule())
    after = tasks.rule_snapshot(_stored_rule(params=params))
    assert tasks.score_fix_rule_run(before, after, ["r1"])["first_missing"] == "s2_threshold"


@pytest.mark.parametrize("changes", [
    {"enabled": True},
    {"name": "renamed"},
    {"tags": []},
    {"schedule": {"interval": "5m"}},
    {"params": {"threshold": [100], "timeWindowSize": 15}},
])
def test_fix_rule_collateral_change_scores_s3(changes):
    before = tasks.rule_snapshot(_stored_rule())
    changes = {**changes, "params": {"threshold": [100], **changes.get("params", {})}}
    after = tasks.rule_snapshot(_stored_rule(**changes))
    assert tasks.score_fix_rule_run(before, after, ["r1"])["first_missing"] == "s3_untouched"


def test_fix_rule_duplicate_scores_s4():
    before = tasks.rule_snapshot(_stored_rule())
    after = tasks.rule_snapshot(_stored_rule(params={"threshold": [100]}))
    assert tasks.score_fix_rule_run(before, after, ["r1", "r9"])["first_missing"] == "s4_no_duplicate"


def _flights_dashboard(gw, xfield="Carrier"):
    gw.dashboards["d1"] = {
        "title": f"{MARKER} flight prices", "description": "",
        "panels": [{
            "type": "vis", "grid": {"x": 0, "y": 0, "w": 48, "h": 12},
            "config": {"type": "xy", "layers": [{
                "data_source": {"index_pattern": "kibana_sample_data_flights"},
                "x": {"operation": "terms", "fields": [xfield], "limit": 10},
                "y": [{"operation": "average", "field": "AvgTicketPrice"}],
            }]},
        }],
    }


def test_flights_ladder():
    gw = FakeGateway()
    assert tasks.score_flights_dashboard_run(gw, MARKER)["first_missing"] == "s1_dashboard"
    _flights_dashboard(gw, xfield="DestCountry")
    assert tasks.score_flights_dashboard_run(gw, MARKER)["first_missing"] == "s2_panel"
    _flights_dashboard(gw)
    assert tasks.score_flights_dashboard_run(gw, MARKER)["passed"]


# --- tests/_claude_cli.py ---------------------------------------------------

KEY = "c2VjcmV0LWtleS12YWx1ZQ=="


def test_model_env_drops_the_key_everywhere():
    env = cli.model_env({
        "PATH": "/bin",
        "KIBANA_TEST_API_KEY": KEY,
        "KIBANA_URL": "http://localhost:15601",
        "kibana_mcp_tools": "list_spaces",
        "SOMETHING": f"prefix-{KEY}",
        "CLAUDE_CODE_SESSION_ID": "parent",
        "HOME": "/root",
    }, KEY)
    assert env == {"PATH": "/bin", "HOME": "/root"}


def test_mcp_config_holds_the_key_and_is_private(tmp_path):
    path = cli.write_mcp_config(tmp_path / "mcp.json", "/repo", "http://k", KEY, "alerting")
    assert path.stat().st_mode & 0o777 == 0o600
    entry = json.loads(path.read_text())["mcpServers"][cli.MCP_SERVER]
    assert entry["env"] == {"KIBANA_URL": "http://k", "KIBANA_API_KEY": KEY,
                            "KIBANA_MCP_TOOLBOXES": "alerting", "KIBANA_MCP_TIER": "write"}


def test_mcp_config_tier_is_write_unless_a_task_asks(tmp_path):
    # Only the destructive precision task (tests/_toolbox_tasks.py, D61) asks.
    path = cli.write_mcp_config(
        tmp_path / "mcp.json", "/repo", "http://k", KEY, "dashboards", tier="destructive")
    entry = json.loads(path.read_text())["mcpServers"][cli.MCP_SERVER]
    assert entry["env"]["KIBANA_MCP_TIER"] == "destructive"


HC = "hcaik_01telemetry-team-key"


def test_server_telemetry_env_is_off_by_default():
    assert cli.server_telemetry_env({"OTEL_EXPORTER_OTLP_HEADERS": f"x-honeycomb-team={HC}"}) == {}
    assert cli.server_telemetry_env({"KIBANA_MCP_OTEL_ENABLED": "0"}) == {}
    assert cli.server_telemetry_env({"KIBANA_MCP_OTEL_ENABLED": "false"}) == {}


def test_server_telemetry_env_passes_otel_settings_through_when_enabled():
    env = cli.server_telemetry_env({
        "KIBANA_MCP_OTEL_ENABLED": "1",
        "KIBANA_MCP_OTEL_ENDPOINT": "https://api.eu1.honeycomb.io",
        "KIBANA_MCP_OTEL_SERVICE_NAME": "mcp-for-kibana-roadmap",
        "OTEL_EXPORTER_OTLP_HEADERS": f"x-honeycomb-team={HC}",
        "OTEL_RESOURCE_ATTRIBUTES": "vcs.ref.head.revision=abc",
        "KIBANA_MCP_TOOLS": "list_spaces",  # selection never rides along
        "TRACEPARENT": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",  # parent session's trace
        "HONEYCOMB_API_KEY": HC,
        "PATH": "/bin",
    })
    assert env == {
        "KIBANA_MCP_OTEL_ENABLED": "1",
        "KIBANA_MCP_OTEL_ENDPOINT": "https://api.eu1.honeycomb.io",
        "KIBANA_MCP_OTEL_SERVICE_NAME": "mcp-for-kibana-roadmap",
        "OTEL_EXPORTER_OTLP_HEADERS": f"x-honeycomb-team={HC}",
        "OTEL_RESOURCE_ATTRIBUTES": "vcs.ref.head.revision=abc",
    }


def test_model_env_drops_telemetry_secrets_only_when_enabled():
    base = {"PATH": "/bin", "HONEYCOMB_API_KEY": HC, "OTEL_EXPORTER_OTLP_HEADERS": f"x-honeycomb-team={HC}"}
    # off: the model environment is exactly what it was before the pass-through
    assert cli.model_env(base, KEY) == base
    on = cli.model_env({**base, "KIBANA_MCP_OTEL_ENABLED": "true"}, KEY)
    assert on == {"PATH": "/bin"}


def test_mcp_config_carries_telemetry_env_only_when_given(tmp_path):
    tele = {"KIBANA_MCP_OTEL_ENABLED": "1", "OTEL_EXPORTER_OTLP_HEADERS": f"x-honeycomb-team={HC}"}
    path = cli.write_mcp_config(tmp_path / "mcp.json", "/repo", "http://k", KEY, "alerting", tele)
    assert path.stat().st_mode & 0o777 == 0o600
    entry = json.loads(path.read_text())["mcpServers"][cli.MCP_SERVER]
    assert entry["env"] == {**tele, "KIBANA_URL": "http://k", "KIBANA_API_KEY": KEY,
                            "KIBANA_MCP_TOOLBOXES": "alerting", "KIBANA_MCP_TIER": "write"}
    # telemetry can never move the server's surface or credentials
    hostile = {"KIBANA_MCP_OTEL_ENABLED": "1", "KIBANA_MCP_TIER": "destructive"}
    entry = json.loads(cli.write_mcp_config(
        tmp_path / "m2.json", "/repo", "http://k", KEY, "alerting", hostile).read_text()
    )["mcpServers"][cli.MCP_SERVER]
    assert entry["env"]["KIBANA_MCP_TIER"] == "write"


def test_command_offers_only_the_server():
    cmd = cli.command("do it", "haiku", "/tmp/mcp.json")
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__mcp-for-kibana"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in cmd and "--restricted" in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert not any(KEY in part for part in cmd)


def test_redact():
    assert cli.redact("nothing here", KEY) == ("nothing here", False)
    text, leaked = cli.redact(f'{{"k": "{KEY}"}} and {KEY}', KEY)
    assert leaked and KEY not in text and text.count(cli.REDACTED) == 2


def test_parse_stream():
    events = [
        {"type": "system", "subtype": "init", "model": "claude-haiku-5-5",
         "permissionMode": "dontAsk", "mcp_servers": [{"name": "mcp-for-kibana", "status": "connected"}],
         "tools": ["mcp__mcp-for-kibana__list_spaces", "mcp__mcp-for-kibana__create_space"]},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "name": "mcp__mcp-for-kibana__list_spaces",
             "input": {"query": "x"}}]}},
        {"type": "result", "subtype": "success", "num_turns": 2, "total_cost_usd": 0.01,
         "is_error": False, "usage": {"input_tokens": 5, "output_tokens": 7},
         "modelUsage": {"claude-haiku-5-5": {}}, "permission_denials": [], "result": "done"},
    ]
    out = cli.parse_stream("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    assert out["offered_tools"] == ["create_space", "list_spaces"]
    assert out["non_server_tools"] == []
    assert out["tool_calls"] == ["list_spaces"]
    assert out["tool_inputs"] == [{"name": "list_spaces", "input": {"query": "x"}}]
    assert out["num_turns"] == 2 and out["total_cost_usd"] == 0.01
    assert out["models_reported"] == ["claude-haiku-5-5"]
    assert out["permission_denials"] == 0 and out["has_result"]


def test_parse_stream_flags_foreign_tools_and_denials():
    events = [
        {"type": "system", "subtype": "init", "tools": ["Bash", "mcp__mcp-for-kibana__list_spaces"]},
        {"type": "system", "subtype": "permission_denied", "tool_use_id": "t1"},
        {"type": "result", "permission_denials": [{"tool_use_id": "t1"}, {"tool_use_id": "t2"}]},
    ]
    out = cli.parse_stream("\n".join(json.dumps(e) for e in events))
    assert out["non_server_tools"] == ["Bash"]
    assert out["permission_denials"] == 2


def test_parameters_default_and_validate():
    assert cli.claude_model({}) == "haiku"
    assert cli.claude_model({"CLAUDE_E2E_MODEL": " claude-sonnet-5-5 "}) == "claude-sonnet-5-5"
    assert cli.runs_per_task({}) == 5
    assert cli.runs_per_task({"CLAUDE_E2E_RUNS": "2"}) == 2
    assert cli.budget_usd({}) == 5.0
    with pytest.raises(pytest.fail.Exception, match="CLAUDE_E2E_RUNS"):
        cli.runs_per_task({"CLAUDE_E2E_RUNS": "0"})
    with pytest.raises(pytest.fail.Exception, match="CLAUDE_E2E_BUDGET_USD"):
        cli.budget_usd({"CLAUDE_E2E_BUDGET_USD": "lots"})
