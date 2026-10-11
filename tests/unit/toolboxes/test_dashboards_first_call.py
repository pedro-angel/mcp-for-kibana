"""Regression: the efficiency study's first create_dashboard calls.

In the 90 with-server runs of the efficiency study (Kibana 9.5.5; Claude Code
with Haiku, Sonnet and Opus 5.5) the FIRST create_dashboard call failed in
90/90, every time with "date_histogram group_by requires time_field": a
date_histogram panel that omitted `time_field`, which the VizSpec schema
documents as auto-filled from the data view. Every retry copied the
group_by field into `time_field`. The arguments below are the recorded
first calls (run directories under scripts/experiment/runs/efficiency/full/);
they must now pass pre-flight, with `time_field` auto-filled.
"""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.models import DataViewDetail
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from tests.fakes import FakeGateway

# describe_data_view("Kibana Sample Data Logs") as recorded in the study (9.5.5).
LOGS_DV = DataViewDetail(
    id="90943e30-9a47-11e8-b64d-95841ca0b247",
    name="Kibana Sample Data Logs",
    index_pattern="kibana_sample_data_logs",
    time_field="timestamp",
    fields={
        '@timestamp': 'date',
        '_id': 'string',
        '_ignored': 'string',
        '_index': 'string',
        '_score': 'number',
        '_source': '_source',
        'agent': 'string',
        'agent.keyword': 'string',
        'bytes': 'number',
        'bytes_counter': 'number',
        'bytes_gauge': 'number',
        'clientip': 'ip',
        'event.dataset': 'string',
        'extension': 'string',
        'extension.keyword': 'string',
        'geo.coordinates': 'geo_point',
        'geo.dest': 'string',
        'geo.src': 'string',
        'geo.srcdest': 'string',
        'host': 'string',
        'host.keyword': 'string',
        'index': 'string',
        'index.keyword': 'string',
        'ip': 'ip',
        'ip_range': 'ip_range',
        'machine.os': 'string',
        'machine.os.keyword': 'string',
        'machine.ram': 'number',
        'memory': 'number',
        'message': 'string',
        'message.keyword': 'string',
        'phpmemory': 'number',
        'referer': 'string',
        'request': 'string',
        'request.keyword': 'string',
        'response': 'string',
        'response.keyword': 'string',
        'tags': 'string',
        'tags.keyword': 'string',
        'timestamp': 'date',
        'timestamp_range': 'date_range',
        'url': 'string',
        'url.keyword': 'string',
        'utc_time': 'date',
        'hour_of_day': 'number',
    },
)

# b01/20261010T185026Z-claude-sonnet-5-5-with-mcp-9f67f5
SINGLE_DATE_HISTOGRAM = {'title': 'EFF-da765430 Web logs overview',
 'description': 'Key metrics of web traffic',
 'time_range': {'from': 'now-7d', 'to': 'now'},
 'panels': [{'title': 'Total requests',
             'chart_type': 'metric',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}]},
            {'title': 'Requests over time',
             'chart_type': 'line',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}],
             'group_by': [{'field': 'timestamp', 'kind': 'date_histogram'}]},
            {'title': 'Responses by status code',
             'chart_type': 'pie',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count'}],
             'group_by': [{'field': 'response.keyword', 'kind': 'terms', 'limit': 10}]},
            {'title': 'Average bytes by extension',
             'chart_type': 'bar',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'average', 'field': 'bytes', 'label': 'Avg bytes'}],
             'group_by': [{'field': 'extension.keyword',
                           'kind': 'terms',
                           'limit': 10}]},
            {'title': 'Top source countries',
             'chart_type': 'table',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count'}],
             'group_by': [{'field': 'geo.src', 'kind': 'terms', 'limit': 10}]}]}

# b03/20261010T191427Z-claude-haiku-5-5-with-mcp-10d57a
AT_TIMESTAMP = {'title': 'EFF-2ee14edd Web logs overview',
 'description': 'Key metrics for the web-logs sample traffic: volume, unique visitors, '
                'bytes, status codes, and geography.',
 'time_range': {'from': '2026-09-27T00:00:00.000Z', 'to': '2026-11-27T00:00:00.000Z'},
 'panels': [{'title': 'Total requests',
             'chart_type': 'metric',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}]},
            {'title': 'Unique visitors (client IPs)',
             'chart_type': 'metric',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'unique_count',
                          'field': 'clientip',
                          'label': 'Unique IPs'}]},
            {'title': 'Total bytes transferred',
             'chart_type': 'metric',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'sum', 'field': 'bytes', 'label': 'Bytes'}]},
            {'title': 'Requests over time by status code',
             'chart_type': 'bar',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}],
             'group_by': [{'field': '@timestamp', 'kind': 'date_histogram'},
                          {'field': 'response.keyword', 'kind': 'terms', 'limit': 5}]},
            {'title': 'Requests by status code',
             'chart_type': 'pie',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}],
             'group_by': [{'field': 'response.keyword', 'kind': 'terms', 'limit': 10}]},
            {'title': 'Top countries by requests',
             'chart_type': 'bar',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}],
             'group_by': [{'field': 'geo.dest', 'kind': 'terms', 'limit': 10}]}]}

# b07/20261010T194622Z-claude-sonnet-5-5-with-mcp-6f28f0
TWO_DATE_HISTOGRAMS = {'title': 'EFF-2b147a37 Web logs overview',
 'description': 'Key metrics of web traffic',
 'panels': [{'title': 'Total requests',
             'chart_type': 'metric',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}]},
            {'title': 'Requests over time',
             'chart_type': 'area',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count', 'label': 'Requests'}],
             'group_by': [{'field': 'timestamp', 'kind': 'date_histogram'}]},
            {'title': 'Responses by status code',
             'chart_type': 'pie',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count'}],
             'group_by': [{'field': 'response.keyword', 'kind': 'terms', 'limit': 10}]},
            {'title': 'Bytes transferred over time',
             'chart_type': 'line',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'sum', 'field': 'bytes', 'label': 'Bytes'}],
             'group_by': [{'field': 'timestamp', 'kind': 'date_histogram'}]},
            {'title': 'Top source countries',
             'chart_type': 'bar',
             'data_view': 'Kibana Sample Data Logs',
             'metrics': [{'agg': 'count'}],
             'group_by': [{'field': 'geo.src', 'kind': 'terms', 'limit': 10}]}]}


@pytest.fixture()
def gateway():
    gw = FakeGateway()
    gw.data_views[LOGS_DV.id] = LOGS_DV
    return gw


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")
    deps = ToolboxDeps(gateway_factory=lambda space=None: gateway, public_kibana_url="http://kb:5601")
    DashboardsToolbox().register(server, deps)
    return server


def _date_histogram_layers(stored):
    for panel in stored["panels"]:
        for layer in panel["config"].get("layers", []):
            if layer.get("x", {}).get("operation") == "date_histogram":
                yield layer


@pytest.mark.parametrize(
    ("args", "x_field"),
    [
        (SINGLE_DATE_HISTOGRAM, "timestamp"),  # 85/90 runs: one such panel
        (TWO_DATE_HISTOGRAMS, "timestamp"),  # 5/90 runs: two such panels
        (AT_TIMESTAMP, "@timestamp"),  # 13/90 (Haiku): x on the other date field
    ],
    ids=["single-date-histogram", "two-date-histograms", "at-timestamp"],
)
async def test_recorded_first_call_now_creates_the_dashboard(mcp, gateway, args, x_field):
    async with Client(mcp) as client:
        result = await client.call_tool("create_dashboard", args)
    assert result.data["status"] == "created"
    assert result.data["panel_count"] == len(args["panels"])
    layers = list(_date_histogram_layers(gateway.dashboards[result.data["id"]]))
    assert layers
    for layer in layers:
        assert layer["x"]["field"] == x_field
        # the data view's time field, auto-filled — not the x-axis field:
        assert layer["data_source"]["time_field"] == "timestamp"


async def test_date_histogram_on_untimed_data_view_names_the_fix(mcp, gateway):
    untimed = DataViewDetail(
        id="dv-untimed", name="untimed", index_pattern="untimed-*", time_field=None,
        fields={"event_time": "date", "host": "string"},
    )
    gateway.data_views[untimed.id] = untimed
    panel = {
        "title": "Events over time", "chart_type": "line", "data_view": "untimed",
        "metrics": [{"agg": "count"}],
        "group_by": [{"field": "event_time", "kind": "date_histogram"}],
    }
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="set time_field on this panel to a date field, "
                           "e.g. time_field: 'event_time'"):
            await client.call_tool("create_dashboard", {"title": "Untimed", "panels": [panel]})
        assert not gateway.dashboards  # pre-flight: nothing written
        result = await client.call_tool(
            "create_dashboard",
            {"title": "Untimed", "panels": [{**panel, "time_field": "event_time"}]},
        )
    assert result.data["status"] == "created"
