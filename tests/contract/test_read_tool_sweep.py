"""The read-tool sweep (D60): every read-tier tool once, through the MCP tool layer.

The full server (every toolbox at `destructive`) runs against the seeded stack
and a fastmcp Client calls each read-tagged tool with the arguments in
tests/_read_sweep.py. Each call must come back without `isError`, with
structured content (valid against the tool's output schema, which for most
read tools only says "an object" or "a list of objects": they return
`dict[str, Any]`), and — for the tools in NON_EMPTY — with rows, so the layer
the unit tier only runs on FakeGateway (argument schemas, DTO shaping,
gateway_errors) meets real Kibana data for every read tool. The D59 secret
scan sees every result, the setup's and teardown's included.

Ids come from the seeded stack (the list tools' first rows) or from throwaway
objects the sweep creates through the write tools and deletes again, so the
shared stack is left as it was found; a delete that fails fails the run,
naming the object to remove by hand, and a run killed before its teardown is
cleaned up by the next (caller-chosen ids, the name prefix, and for the short
URL, which has neither, its marker path). One effect
outlives the run, as it does for the contract tier's own synthetics test:
Kibana installs the `synthetics` package for the first private location.
"""

import asyncio
import contextlib
import os
import uuid

import jsonschema
import kibana
import pytest
from fastmcp import Client

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests import _secret_scan
from tests._read_sweep import NON_EMPTY, SWEEP_ROWS, coverage_problem
from tests._registry import registry
from tests._stack_env import require_stack_env

pytestmark = pytest.mark.contract

FLIGHTS = "kibana_sample_data_flights"
_READ_TOOLS = sorted(name for name, t in registry().items() if t.tier == "read")
# Caller-chosen ids, removed before they are created, so a run killed before
# its teardown cannot break the next one (or the "empty" contract tests).
_VALUE_LIST = "mcp-sweep-values"
_EXCEPTION_LIST = "mcp-sweep-exceptions"
_DETECTION_RULE = "mcp-sweep-rule"
_APM_SERVICE = "mcp-sweep-service"
_PREFIX = "mcp-sweep-"  # every throwaway object's name starts with it
# The short URL has no name and no list tool: it is found by this path.
_SHORT_URL_PATH = "/app/dashboards#/list?mcp-sweep"
_SEED_FIX = "scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed"


def _server(export_dir):
    # tools=[] so a shell KIBANA_MCP_TOOLS cannot narrow the server.
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"), tools=[])
    return build_server(
        settings,
        lambda space=None: KibanaPyGateway.connect(
            os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space),
        export_dir=export_dir,
    )


async def _call(client, tool, args):
    r = await client.call_tool(tool, args, raise_on_error=False)
    assert not r.is_error, f"{tool}({args}) failed: {r.content[0].text if r.content else r}"
    return r.structured_content


def _first(rows, what, key="id"):
    assert rows, f"the seeded stack has no {what} — {_SEED_FIX}"
    return rows[0][key]


async def _seeded_ids(c):
    """Ids the seeded stack already holds, each from its list tool."""
    res = lambda sc: sc["result"]  # noqa: E731  (list tools wrap their rows)
    return {
        "dashboard": _first(res(await _call(c, "search_dashboards", {})), "dashboard"),
        "data_view": _first(res(await _call(c, "list_data_views", {})), "data view"),
        "agent": _first(res(await _call(c, "list_agents", {})), "Fleet agent"),
        "agent_policy": _first(res(await _call(c, "list_agent_policies", {})), "agent policy"),
        "enrollment_key": _first(res(await _call(c, "list_enrollment_keys", {})),
                                 "enrollment key"),
        "output": _first(res(await _call(c, "list_outputs", {})), "Fleet output"),
        "package": _first(res(await _call(c, "list_installed_packages", {})),
                          "installed package", "name"),
        "package_policy": _first(res(await _call(c, "list_package_policies", {})),
                                 "package policy"),
        "role": _first(res(await _call(c, "list_roles", {})), "role", "name"),
        "stream": _first(res(await _call(c, "list_streams", {})), "stream", "name"),
        "apm_service": _APM_SERVICE,
    }


async def _preclean(c):
    for tool, args in (
        ("delete_value_list", {"id": _VALUE_LIST, "force": True}),
        ("delete_exception_list", {"list_id": _EXCEPTION_LIST}),
        ("delete_detection_rule", {"rule_id": _DETECTION_RULE}),
        ("delete_apm_agent_config", {"service_name": _APM_SERVICE}),
    ):
        await c.call_tool(tool, args, raise_on_error=False)  # absent is fine
    # The rest carry server-made ids: found by the run's name prefix. Monitors
    # go before their locations, locations before their agent policies.
    for short_id in _marked_short_urls():
        await _call(c, "delete_short_url", {"short_url_id": short_id})
    for lister, name_key, delete, param, extra in (
        ("list_cases", "title", "delete_case", "case_id", {}),
        ("list_connectors", "name", "delete_connector", "connector_id", {}),
        ("list_alert_rules", "name", "delete_alert_rule", "rule_id", {}),
        ("search_visualizations", "title", "delete_visualization", "visualization_id", {}),
        ("list_synthetic_monitors", "name", "delete_synthetic_monitor", "monitor_id", {}),
        ("list_synthetic_private_locations", "label", "delete_synthetic_private_location",
         "location_id", {}),
        ("list_agent_policies", "name", "delete_agent_policy", "agent_policy_id",
         {"force": True}),
    ):
        for row in (await _call(c, lister, {}))["result"]:
            if str(row.get(name_key) or "").startswith(_PREFIX):
                await _call(c, delete, {param: row["id"], **extra})


def _marked_short_urls():
    """Ids of the short URLs a sweep made (their locator holds _SHORT_URL_PATH)."""
    with contextlib.closing(kibana.Kibana(
            os.environ["KIBANA_URL"], api_key=os.environ["KIBANA_TEST_API_KEY"])) as kb:
        found = kb.perform_request(
            "GET", "/api/saved_objects/_find", params={"type": "url", "per_page": 1000}).body
    return [o["id"] for o in found["saved_objects"]
            if _SHORT_URL_PATH in str(o.get("attributes", {}).get("locatorJSON", ""))]


async def _create(c, tag, undo):
    """The throwaway objects; each delete is queued as soon as it exists."""
    ids = {}

    async def make(key, tool, args, delete, id_key="id"):
        sc = await _call(c, tool, args)
        ids[key] = sc[id_key]
        undo.append((key, *delete(sc)))
        return sc

    await make("case", "create_case", {"title": f"{_PREFIX}{tag}", "description": "sweep"},
               lambda sc: ("delete_case", {"case_id": sc["id"]}))
    await make("connector", "create_connector",
               {"name": f"{_PREFIX}{tag}", "connector_type_id": ".server-log"},
               lambda sc: ("delete_connector", {"connector_id": sc["id"]}))
    await make("alert_rule", "create_alert_rule", {
        "name": f"{_PREFIX}{tag}", "rule_type_id": ".es-query", "consumer": "stackAlerts",
        "enabled": False, "params": {
            "searchType": "esQuery", "esQuery": '{"query":{"match_all":{}}}',
            "index": [FLIGHTS], "timeField": "timestamp", "threshold": [0],
            "thresholdComparator": ">", "timeWindowSize": 5, "timeWindowUnit": "m",
            "size": 1}},
        lambda sc: ("delete_alert_rule", {"rule_id": sc["id"]}))
    await make("visualization", "create_visualization", {"spec": {
        "title": f"{_PREFIX}{tag}", "chart_type": "bar", "data_view": FLIGHTS,
        "metrics": [{"agg": "count"}], "group_by": [{"field": "Carrier"}]}},
        lambda sc: ("delete_visualization", {"visualization_id": sc["id"]}))
    short = await make("short_url", "create_short_url", {
        "locator_id": "LEGACY_SHORT_URL_LOCATOR", "params": {"url": _SHORT_URL_PATH}},
        lambda sc: ("delete_short_url", {"short_url_id": sc["id"]}))
    ids["short_url_slug"] = short["slug"]
    await make("exception_list", "create_exception_list", {
        "name": f"{_PREFIX}{tag}", "description": "sweep", "list_id": _EXCEPTION_LIST},
        lambda sc: ("delete_exception_list", {"list_id": _EXCEPTION_LIST}), "list_id")
    # An item in each list, so the item finds return a real row (removed with its list).
    await _call(c, "create_exception_item", {
        "list_id": _EXCEPTION_LIST, "name": f"{_PREFIX}{tag}", "description": "sweep",
        "entries": [{"field": "host.name", "operator": "included", "value": "mcp-sweep"}]})
    await make("value_list", "create_value_list", {
        "name": f"{_PREFIX}{tag}", "description": "sweep", "type": "keyword",
        "id": _VALUE_LIST},
        lambda sc: ("delete_value_list", {"id": _VALUE_LIST, "force": True}))
    await _call(c, "create_value_list_item", {"list_id": _VALUE_LIST, "value": "mcp-sweep"})
    await make("detection_rule", "create_detection_rule", {
        "name": f"{_PREFIX}{tag}", "description": "sweep", "query": "*",
        "index": [FLIGHTS], "rule_id": _DETECTION_RULE, "enabled": False},
        lambda sc: ("delete_detection_rule", {"rule_id": _DETECTION_RULE}), "rule_id")
    await make("apm_config", "upsert_apm_agent_config", {
        "service_name": _APM_SERVICE, "settings": {"transaction_sample_rate": "0.5"}},
        lambda sc: ("delete_apm_agent_config", {"service_name": _APM_SERVICE}), "service_name")
    # A monitor needs a private location, which needs an agent policy of its own.
    await make("sweep_policy", "create_agent_policy", {
        "name": f"{_PREFIX}{tag}", "namespace": "default", "monitoring_enabled": []},
        lambda sc: ("delete_agent_policy", {"agent_policy_id": sc["id"], "force": True}))
    loc = await make("location", "create_synthetic_private_location", {
        "label": f"{_PREFIX}{tag}", "agent_policy_id": ids["sweep_policy"]},
        lambda sc: ("delete_synthetic_private_location", {"location_id": sc["id"]}))
    await make("synthetic_monitor", "create_synthetic_monitor", {
        "type": "http", "name": f"{_PREFIX}{tag}", "url": "http://kibana:5601/api/status",
        "private_locations": [loc["id"]], "enabled": False},
        lambda sc: ("delete_synthetic_monitor", {"monitor_id": sc["id"]}))
    return ids


async def _teardown(c, undo):
    left = []
    for key, tool, args in reversed(undo):
        r = await c.call_tool(tool, args, raise_on_error=False)
        if r.is_error:
            left.append(f"{key} via {tool}({args}): {r.content[0].text if r.content else r}")
    return left


@pytest.fixture(scope="module")
def sweep(tmp_path_factory):
    """(ids, output schemas, export dir): seeded ids plus the throwaway
    objects, removed again after the module. Its own calls run under the D59
    scan too (the autouse one is function-scoped and not active here)."""
    require_stack_env()
    export_dir = tmp_path_factory.mktemp("sweep-exports")
    undo: list = []

    async def setup():
        async with Client(_server(export_dir)) as c:
            schemas = {t.name: t.output_schema for t in await c.list_tools()}
            await _preclean(c)
            ids = await _seeded_ids(c)
            try:
                ids |= await _create(c, uuid.uuid4().hex[:8], undo)
            except BaseException as e:
                left = await _teardown(c, undo)
                if left:
                    e.add_note("the sweep could not remove what it created; delete these "
                               "by hand: " + "; ".join(left))
                raise
            return ids, schemas

    async def teardown():
        async with Client(_server(export_dir)) as c:
            return await _teardown(c, undo)

    with pytest.MonkeyPatch.context() as mp:
        leaks = _secret_scan.install(mp)
        ids, schemas = asyncio.run(setup())
    assert not leaks, "\n".join(leaks)
    yield ids, schemas, export_dir
    with pytest.MonkeyPatch.context() as mp:
        leaks = _secret_scan.install(mp)
        left = asyncio.run(teardown())
    assert not leaks, "\n".join(leaks)
    assert not left, (
        "the read-tool sweep could not remove what it created; delete these by hand "
        "before the next run: " + "; ".join(left))


def test_sweep_covers_every_read_tool():
    problem = coverage_problem(set(_READ_TOOLS))
    assert problem is None, problem


@pytest.mark.parametrize("tool", _READ_TOOLS)
async def test_read_tool_answers_through_the_tool_layer(tool, sweep):
    ids, schemas, export_dir = sweep
    assert tool in SWEEP_ROWS, coverage_problem(set(_READ_TOOLS))
    args = SWEEP_ROWS[tool](ids)
    async with Client(_server(export_dir)) as client:
        r = await client.call_tool(tool, args, raise_on_error=False)
    assert not r.is_error, (
        f"{tool}({args}) returned isError against the seeded stack: "
        f"{r.content[0].text if r.content else r} — fix the tool (or its sweep row in "
        "tests/_read_sweep.py when the arguments are wrong)")
    assert r.structured_content is not None, f"{tool} returned no structured content"
    if tool in NON_EMPTY:
        assert r.structured_content.get("result"), (
            f"{tool}({args}) returned no rows, though the sweep made or the seed holds "
            f"one — {_SEED_FIX}, or fix the tool if Kibana holds them")
    try:
        jsonschema.validate(r.structured_content, schemas[tool])
    except jsonschema.ValidationError as e:
        pytest.fail(f"{tool}'s structured content does not match its output schema: "
                    f"{e.message} at {list(e.absolute_path)} — fix the tool's return type "
                    "or its DTO shaping", pytrace=False)
