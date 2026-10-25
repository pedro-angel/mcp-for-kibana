"""update_dashboard's pure settings helpers (core/dashboards/settings.py).

The time grammar is Kibana 9.5.4's @kbn/datemath: Kibana's API stores any
string (probed), so the tool checks what the dashboard app can read."""

import pytest

from kibana_mcp.core.dashboards.settings import (
    merged_tags,
    refresh_interval_for,
    refresh_seconds,
    settings_view,
    time_range_errors,
    undo_arguments,
)


@pytest.mark.parametrize("value", [
    "now", "now-7d", "now-7d/d", "now/w", "now+1h", "now-15m", "now-1ms", "now-d",
    "now/1d", "now-1y/y", "now-1M+3d/d",
    "2026-01-31", "2026-01-31T00:00:00Z", "2026-01-31T00:00:00.000+02:00",
    "2026-01-31T00:00:00Z||-1d", "2026-01-31||/M",
])
def test_kibana_readable_times_pass(value):
    assert time_range_errors(value, "now") == []
    assert time_range_errors("now-1y", value) == []


@pytest.mark.parametrize("value", [
    "yesterday", "last 7 days", "now-7days", "now - 7d", "now-7", "now/2d", "now-1q",
    "nowish", "7d", "2026-13-01", "Jan 31 2026", "2026-01-31||yesterday", "",
])
def test_unreadable_times_name_the_field(value):
    errors = time_range_errors(value, "now")
    assert errors == [f"time_range.from '{value}' is not a time Kibana can read"]


def test_both_fields_reported_at_once():
    assert time_range_errors("yesterday", "today") == [
        "time_range.from 'yesterday' is not a time Kibana can read",
        "time_range.to 'today' is not a time Kibana can read",
    ]


def test_absolute_range_must_run_forwards():
    assert time_range_errors("2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z") == [
        "time_range.from '2026-02-01T00:00:00Z' is not before time_range.to "
        "'2026-01-01T00:00:00Z'"
    ]
    assert time_range_errors("2026-01-01", "2026-01-01") != []
    # relative values are not ordered here (the parser alone decides them)
    assert time_range_errors("now", "now-7d") == []
    # a naive and an aware timestamp are not compared
    assert time_range_errors("2026-02-01T00:00:00", "2026-01-01T00:00:00Z") == []


def test_refresh_seconds_reads_kibana_shapes():
    assert refresh_seconds(None) is None
    assert refresh_seconds({"pause": True, "value": 60000}) == 0
    assert refresh_seconds({"pause": False, "value": 30000}) == 30
    assert isinstance(refresh_seconds({"pause": False, "value": 30000}), int)
    assert refresh_seconds({"pause": False, "value": 1500}) == 1.5


def test_refresh_interval_for_pauses_keeping_the_value():
    assert refresh_interval_for(30, None) == {"pause": False, "value": 30000}
    assert refresh_interval_for(1.5, None) == {"pause": False, "value": 1500}
    assert refresh_interval_for(30.0, None)["value"] == 30000
    assert isinstance(refresh_interval_for(30.0, None)["value"], int)
    assert refresh_interval_for(0, {"pause": False, "value": 60000}) == {
        "pause": True, "value": 60000,
    }
    assert refresh_interval_for(0, None) == {"pause": True, "value": 0}


def test_merged_tags_keeps_order_and_never_repeats():
    assert merged_tags(["a", "b"], ["c", "a", "c"], []) == ["a", "b", "c"]
    assert merged_tags(["a", "b", "a"], [], ["a", "zzz"]) == ["b"]
    assert merged_tags([], [], []) == []


def _view(**data):
    return settings_view({"title": "t", "description": "", **data})


def test_undo_arguments_restore_each_setting():
    before = _view(time_range={"from": "now-7d", "to": "now"},
                   refresh_interval={"pause": False, "value": 60000}, tags=["a", "b"])
    after = _view(title="t2", time_range={"from": "now-1h", "to": "now"},
                  refresh_interval={"pause": True, "value": 60000}, tags=["b", "c"])
    assert undo_arguments(before, after) == {
        "title": "t", "time_range": {"from": "now-7d", "to": "now"},
        "refresh_interval_seconds": 60, "tags_to_add": ["a"], "tags_to_remove": ["c"],
    }
    assert undo_arguments(before, before) == {}


def test_a_refresh_reads_as_none_without_a_stored_time_range():
    untimed = _view(refresh_interval={"pause": False, "value": 30000})
    assert untimed["refresh_interval_seconds"] is None
    timed = _view(time_range={"from": "now-1h", "to": "now"},
                  refresh_interval={"pause": False, "value": 30000})
    assert timed["refresh_interval_seconds"] == 30
    # so an undo never pairs a refresh with clear_time_range
    after = _view(time_range={"from": "now-1h", "to": "now"},
                  refresh_interval={"pause": False, "value": 60000})
    assert undo_arguments(untimed, after) == {"clear_time_range": True}
    assert undo_arguments(timed, untimed) == {
        "time_range": {"from": "now-1h", "to": "now"}, "refresh_interval_seconds": 30}


def test_undo_arguments_for_settings_not_stored():
    before = _view()
    assert undo_arguments(before, _view(time_range={"from": "now-1h", "to": "now"})) == {
        "clear_time_range": True}
    both = _view(time_range={"from": "now-1h", "to": "now"},
                 refresh_interval={"pause": False, "value": 5000})
    assert undo_arguments(before, both) == {"clear_time_range": True}
    timed = _view(time_range={"from": "now-1h", "to": "now"})
    timed_refresh = _view(time_range={"from": "now-1h", "to": "now"},
                          refresh_interval={"pause": False, "value": 5000})
    assert undo_arguments(timed, timed_refresh) == {"refresh_interval_seconds": 0}
