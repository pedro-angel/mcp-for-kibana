"""update_dashboard (B8): a dashboard's title, description, time range,
refresh interval and tags, with its panels untouched. The shapes are those
Kibana 9.5.4 returned: `time_range` {from, to}, `refresh_interval`
{pause, value ms}, `tags` a list of tag saved-object ids."""

from copy import deepcopy

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kibana_mcp.core.dashboards.identity import derive_dashboard_id
from kibana_mcp.core.errors import KibanaSpaceNotFound
from kibana_mcp.core.models import SavedObjectDetail
from kibana_mcp.toolboxes.base import ToolboxDeps
from kibana_mcp.toolboxes.dashboards.toolbox import DashboardsToolbox
from tests.fakes import FakeGateway

PANELS = [
    {"type": "vis", "grid": {"x": 0, "y": 0, "w": 24, "h": 10}, "id": "p-1",
     "config": {"title": "inline", "type": "xy", "layers": []}},
    {"type": "vis", "grid": {"x": 24, "y": 0, "w": 24, "h": 10}, "id": "p-2",
     "config": {"ref_id": "viz-lib"}},
    {"title": "Section", "collapsed": False, "panels": [
        {"type": "markdown", "grid": {"x": 0, "y": 0}, "config": {"content": "hi"}, "id": "p-3"},
    ]},
]
STORED = {
    "title": "Ops", "description": "old", "panels": PANELS,
    "options": {"use_margins": True}, "filters": [{"meta": {}}],
    "query": {"expression": "a:b", "language": "kql"},
    "pinned_panels": [{"type": "options_list_control"}], "tags": ["t-old"],
}


def _tag(tag_id, name):
    return SavedObjectDetail(
        type="tag", id=tag_id, namespaces=("default",), created_at="", updated_at="",
        managed=False, attributes={"name": name, "color": "#000000"}, references=(),
    )


class _Spy(FakeGateway):
    def __init__(self):
        super().__init__()
        self.writes: list[tuple[str, dict]] = []
        self.tag_reads: list[list[str]] = []
        self.factory_spaces: list[str | None] = []

    def update_dashboard(self, dashboard_id, data):
        self.writes.append((dashboard_id, deepcopy(data)))
        super().update_dashboard(dashboard_id, data)

    def get_tag_names(self, tag_ids):
        self.tag_reads.append(list(tag_ids))
        return super().get_tag_names(tag_ids)


@pytest.fixture()
def gateway():
    gw = _Spy()
    gw.dashboards["d1"] = deepcopy(STORED)
    gw.saved_objects += [_tag("t-old", "old"), _tag("t-prod", "production"), _tag("t-x", "x")]
    return gw


@pytest.fixture()
def mcp(gateway):
    server = FastMCP("test")

    def factory(space=None):
        gateway.factory_spaces.append(space)
        return gateway

    DashboardsToolbox().register(
        server, ToolboxDeps(gateway_factory=factory, public_kibana_url="http://kb:5601"))
    return server


async def _call(mcp, **args):
    async with Client(mcp) as client:
        return (await client.call_tool("update_dashboard", {"dashboard_id": "d1", **args})).data


async def _refused(mcp, match, **args):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=match):
            await client.call_tool("update_dashboard", {"dashboard_id": "d1", **args})


async def test_annotations_and_tier(mcp):
    async with Client(mcp) as client:
        tool = next(t for t in await client.list_tools() if t.name == "update_dashboard")
    a = tool.annotations
    assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint, a.openWorldHint) == (
        False, False, True, False)
    assert set(tool.meta["fastmcp"]["tags"]) == {"dashboards", "write"}


async def test_title_and_description_change_and_panels_stay_byte_for_byte(mcp, gateway):
    got = await _call(mcp, title="Ops v2", description="")
    stored = gateway.dashboards["d1"]
    assert stored["panels"] == PANELS  # ids, sections and the library ref untouched
    for key in ("options", "filters", "query", "pinned_panels", "tags"):
        assert stored[key] == STORED[key]
    assert (stored["title"], stored["description"]) == ("Ops v2", "")
    assert got == {
        "id": "d1", "url": "http://kb:5601/app/dashboards#/view/d1", "status": "updated",
        "title": "Ops v2", "description": "", "time_range": None,
        "refresh_interval_seconds": None, "tags": ["t-old"],
        "changed": ["title", "description"],
        "previous": {"title": "Ops", "description": "old"},
        "undo": {"title": "Ops", "description": "old"},
    }


async def test_undo_passed_back_restores_every_setting(mcp, gateway):
    gateway.dashboards["d1"]["time_range"] = {"from": "now-7d", "to": "now"}
    gateway.dashboards["d1"]["refresh_interval"] = {"pause": True, "value": 60000}
    stored = deepcopy(gateway.dashboards["d1"])
    got = await _call(
        mcp, title="Ops v2", description="new", time_range={"from": "now-1h"},
        refresh_interval_seconds=30, tags_to_add=["t-prod"], tags_to_remove=["t-old"],
    )
    assert got["undo"] == {
        "title": "Ops", "description": "old", "time_range": {"from": "now-7d", "to": "now"},
        "refresh_interval_seconds": 0, "tags_to_add": ["t-old"], "tags_to_remove": ["t-prod"],
    }
    back = await _call(mcp, **got["undo"])
    assert back["status"] == "updated"
    restored = gateway.dashboards["d1"]
    # paused again, but at the interval the call set: 0 carries no interval,
    # so the stored 60 s is not restored (D52 states this undo is approximate)
    assert restored["refresh_interval"] == {"pause": True, "value": 30000}
    assert {k: v for k, v in restored.items() if k != "refresh_interval"} == {
        k: v for k, v in stored.items() if k != "refresh_interval"}


async def test_undo_of_settings_that_were_not_stored(mcp, gateway):
    got = await _call(mcp, time_range={"from": "now-1h"}, refresh_interval_seconds=30)
    # no time range was stored: the undo clears it, and leaves the refresh out
    # (the dashboard app ignores a refresh without a stored time range)
    assert got["undo"] == {"clear_time_range": True}
    await _call(mcp, **got["undo"])
    assert "time_range" not in gateway.dashboards["d1"]
    # a refresh that was not stored, on a stored time range, comes back paused
    await _call(mcp, time_range={"from": "now-1h"})
    gateway.dashboards["d1"].pop("refresh_interval")
    assert (await _call(mcp, refresh_interval_seconds=30))["undo"] == {
        "refresh_interval_seconds": 0}


async def test_same_values_are_unchanged_and_write_nothing(mcp, gateway):
    got = await _call(mcp, title="Ops", description="old", tags_to_add=["t-old"],
                      tags_to_remove=["t-gone"])
    assert got["status"] == "unchanged" and got["changed"] == [] and got["previous"] == {}
    assert got["undo"] == {}
    assert gateway.writes == []


async def test_time_range_set_then_cleared(mcp, gateway):
    got = await _call(mcp, time_range={"from": "now-24h", "to": "now"})
    assert gateway.dashboards["d1"]["time_range"] == {"from": "now-24h", "to": "now"}
    assert got["previous"] == {"time_range": None} and got["changed"] == ["time_range"]
    cleared = await _call(mcp, clear_time_range=True)
    assert "time_range" not in gateway.dashboards["d1"]
    assert cleared["previous"] == {"time_range": {"from": "now-24h", "to": "now"}}
    assert cleared["time_range"] is None


async def test_time_range_to_defaults_to_now(mcp, gateway):
    await _call(mcp, time_range={"from": "2026-01-01T00:00:00Z"})
    assert gateway.dashboards["d1"]["time_range"] == {"from": "2026-01-01T00:00:00Z", "to": "now"}


async def test_unreadable_time_range_is_refused_before_any_call(mcp, gateway):
    await _refused(
        mcp,
        r"time_range.from 'yesterday' is not a time Kibana can read — use date math such "
        r"as now, now-24h",
        time_range={"from": "yesterday", "to": "now"},
    )
    assert gateway.factory_spaces == [] and gateway.writes == []


async def test_time_range_and_clear_together_refused(mcp, gateway):
    await _refused(mcp, "pass time_range or clear_time_range=true, not both",
                   time_range={"from": "now-1h"}, clear_time_range=True)
    assert gateway.factory_spaces == []


async def test_refresh_needs_a_stored_time_range(mcp, gateway):
    await _refused(mcp, r"takes effect only on a dashboard that stores a time range .* "
                   "pass time_range too", refresh_interval_seconds=30)
    assert gateway.writes == []
    got = await _call(mcp, refresh_interval_seconds=30, time_range={"from": "now-1h"})
    assert gateway.dashboards["d1"]["refresh_interval"] == {"pause": False, "value": 30000}
    assert got["refresh_interval_seconds"] == 30
    assert got["previous"] == {"time_range": None, "refresh_interval_seconds": None}
    # the stored time range now counts, and 0 pauses keeping the value
    paused = await _call(mcp, refresh_interval_seconds=0)
    assert gateway.dashboards["d1"]["refresh_interval"] == {"pause": True, "value": 30000}
    assert paused["previous"] == {"refresh_interval_seconds": 30}
    # with clear_time_range the fix is to drop one of the two, refused first
    calls = len(gateway.factory_spaces)
    await _refused(
        mcp, r"would not take effect with clear_time_range=true .* leave "
        r"refresh_interval_seconds out, or pass time_range instead of clear_time_range",
        refresh_interval_seconds=60, clear_time_range=True)
    assert len(gateway.factory_spaces) == calls


async def test_a_refresh_without_a_time_range_reads_as_none_and_undoes(mcp, gateway):
    # Kibana keeps a refresh on a dashboard with no time range (live, 9.5.4),
    # which the app does not apply: it reads as none, so the undo of setting
    # both is only clear_time_range, which the tool accepts.
    gateway.dashboards["d1"]["refresh_interval"] = {"pause": False, "value": 30000}
    got = await _call(mcp, time_range={"from": "now-1h"}, refresh_interval_seconds=60)
    assert got["previous"] == {"time_range": None, "refresh_interval_seconds": None}
    assert got["undo"] == {"clear_time_range": True}
    back = await _call(mcp, **got["undo"])
    assert (back["time_range"], back["refresh_interval_seconds"]) == (None, None)


async def test_clearing_the_time_range_reports_the_refresh_and_undoes_exactly(mcp, gateway):
    gateway.dashboards["d1"]["time_range"] = {"from": "now-7d", "to": "now"}
    gateway.dashboards["d1"]["refresh_interval"] = {"pause": False, "value": 30000}
    stored = deepcopy(gateway.dashboards["d1"])
    cleared = await _call(mcp, clear_time_range=True)
    assert cleared["changed"] == ["time_range", "refresh_interval_seconds"]
    assert cleared["refresh_interval_seconds"] is None
    assert cleared["undo"] == {
        "time_range": {"from": "now-7d", "to": "now"}, "refresh_interval_seconds": 30}
    await _call(mcp, **cleared["undo"])
    assert gateway.dashboards["d1"] == stored


async def test_a_sub_second_refresh_round_trips(mcp, gateway):
    # Kibana stores any whole milliseconds (1500 live, 9.5.4): the seconds
    # come back as a fraction, which the tool takes as given.
    gateway.dashboards["d1"]["time_range"] = {"from": "now-7d", "to": "now"}
    gateway.dashboards["d1"]["refresh_interval"] = {"pause": False, "value": 1500}
    got = await _call(mcp, refresh_interval_seconds=30)
    assert got["undo"] == {"refresh_interval_seconds": 1.5}
    await _call(mcp, **got["undo"])
    assert gateway.dashboards["d1"]["refresh_interval"] == {"pause": False, "value": 1500}
    await _refused(mcp, r"under the 1 ms Kibana stores — pass 0 to turn auto-refresh off",
                   refresh_interval_seconds=0.0001)


async def test_tags_added_and_removed_after_checking_new_ids(mcp, gateway):
    got = await _call(mcp, tags_to_add=["t-prod", "t-old", "t-prod"], tags_to_remove=["t-x"])
    assert gateway.tag_reads == [["t-prod"]]  # only the ids not already on it
    assert gateway.dashboards["d1"]["tags"] == ["t-old", "t-prod"]
    assert got["previous"] == {"tags": ["t-old"]} and got["tags"] == ["t-old", "t-prod"]
    removed = await _call(mcp, tags_to_remove=["t-old"])
    assert gateway.dashboards["d1"]["tags"] == ["t-prod"]
    assert removed["previous"] == {"tags": ["t-old", "t-prod"]}


async def test_unknown_tag_id_refused_naming_the_lookup(mcp, gateway):
    await _refused(
        mcp,
        r"tag id\(s\) \['production'\] name no tag — tags_to_add takes tag ids: call "
        r"find_saved_objects\(types=\[\"tag\"\]\) to find them",
        tags_to_add=["production"],
    )
    assert gateway.writes == []


async def test_tag_overlap_refused(mcp, gateway):
    await _refused(mcp, r"tag\(s\) \['t-x'\] are in both", tags_to_add=["t-x"],
                   tags_to_remove=["t-x"])
    assert gateway.factory_spaces == []


async def test_tag_cap(mcp, gateway):
    gateway.dashboards["d1"]["tags"] = [f"t{i}" for i in range(100)]
    await _refused(mcp, "at most 100 tags; this change leaves 101", tags_to_add=["t-prod"])
    assert gateway.writes == []


async def test_nothing_to_change_and_blank_title_refused_first(mcp, gateway):
    await _refused(mcp, "nothing to change: pass at least one of title")
    await _refused(mcp, "title cannot be blank", title="   ")
    assert gateway.factory_spaces == []


async def test_unsupported_panels_refused_untouched(mcp, gateway):
    gateway.warnings = ["dropped_panel: map"]
    await _refused(mcp, "Modify this dashboard in the Kibana UI instead", title="x")
    gateway.warnings = []
    gateway.dashboards["d1"]["esql_approximation"] = True  # a key the PUT would drop
    await _refused(mcp, "cannot round-trip", title="x")
    assert gateway.writes == []


async def test_unknown_dashboard_names_search(mcp, gateway):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="dashboard 'nope' not found"):
            await client.call_tool("update_dashboard", {"dashboard_id": "nope", "title": "x"})


async def test_rename_onto_a_title_another_dashboard_holds_is_refused(mcp, gateway):
    other = derive_dashboard_id("Revenue")
    gateway.dashboards[other] = {"title": "Revenue", "panels": []}
    await _refused(
        mcp,
        rf"dashboard '{other}' \('Revenue'\) is the one create_dashboard edits for the "
        r"title 'revenue!' — renaming 'd1' to it would leave two dashboards",
        title="revenue!",
    )
    assert gateway.writes == []
    # a free title, and a case-only change of its own title, go through
    assert (await _call(mcp, title="Margins"))["status"] == "updated"
    gateway.dashboards[derive_dashboard_id("Margins")] = {"title": "Margins", "panels": []}
    assert (await _call(mcp, title="MARGINS"))["previous"] == {"title": "Margins"}


async def test_undo_of_a_rename_off_a_shared_title_is_refused_like_a_rename(mcp, gateway):
    # Two dashboards titled Ops, one of them create_dashboard's: renaming the
    # other away works, renaming it back is the rename the check refuses
    # (D52 states this undo is refused; the fix text names the way out).
    gateway.dashboards[derive_dashboard_id("Ops")] = {"title": "Ops", "panels": []}
    got = await _call(mcp, title="Ops archive")
    assert got["undo"] == {"title": "Ops"}
    await _refused(mcp, "would leave two dashboards with that title; choose another title",
                   **got["undo"])


async def test_rename_of_the_dashboard_create_dashboard_made(mcp, gateway):
    own = derive_dashboard_id("Ops")
    gateway.dashboards[own] = deepcopy(STORED)
    async with Client(mcp) as client:
        got = (await client.call_tool(
            "update_dashboard", {"dashboard_id": own, "title": "Ops renamed"})).data
    assert got["title"] == "Ops renamed"


async def test_rename_check_never_swallows_a_missing_space(mcp, gateway, monkeypatch):
    def boom(dashboard_id):
        raise KibanaSpaceNotFound("space 'gone' not found — call list_spaces")

    monkeypatch.setattr(gateway, "get_dashboard", boom)
    await _refused(mcp, "space 'gone' not found", title="Elsewhere")
    assert gateway.writes == []


async def test_space_threads_once_echoes_and_prefixes_the_url(mcp, gateway):
    got = await _call(mcp, description="d", space="team-a")
    assert gateway.factory_spaces == ["team-a"]
    assert got["space"] == "team-a"
    assert got["url"] == "http://kb:5601/s/team-a/app/dashboards#/view/d1"


async def test_unknown_tag_names_the_space(mcp, gateway):
    await _refused(mcp, r"name no tag in space 'team-a'", tags_to_add=["nope"], space="team-a")


async def test_refresh_seconds_schema_rejects_negative(mcp):
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "update_dashboard", {"dashboard_id": "d1", "refresh_interval_seconds": -1})
