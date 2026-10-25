"""Dashboard settings for update_dashboard — pure, framework-free.

Kibana's Dashboards API stores `time_range` strings without parsing them
(probed on 9.5.4: "yesterday", and a `from` later than `to`, were both
stored with a 200), and the dashboard app then parses them with
@kbn/datemath. So the grammar below is that parser's, read from Kibana
9.5.4's bundled `@kbn/datemath`: `now` or an ISO 8601 anchor (optionally
followed by `||`), then any number of `+N<unit>`, `-N<unit>` or `/<unit>`
steps (a rounding step takes no number but 1), unit one of
ms s m h d w M y.
"""

import re
from datetime import datetime
from typing import Any

from kibana_mcp.core.dashboards.identity import normalize

_UNIT = r"(?:ms|[smhdwMy])"
_STEPS = re.compile(rf"(?:[+-]\d*{_UNIT}|/1?{_UNIT})*")

DATE_MATH_HINT = (
    "use date math such as now, now-24h, now-7d/d or now/w (units ms s m h d w M y), "
    "or an ISO 8601 timestamp such as 2026-01-31T00:00:00Z"
)

# Kibana's dashboard schema caps a dashboard's tags at 100 (9.5.4).
MAX_DASHBOARD_TAGS = 100

# The longest refresh the dashboard app can wait: 9.5.4's timefilter passes
# the stored milliseconds straight to window.setTimeout, whose delay is a
# signed 32-bit integer (a longer one fires at once, refreshing nonstop).
MAX_REFRESH_SECONDS = 2_147_483.647


def _iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse(value: str) -> tuple[bool, datetime | None]:
    """(valid, absolute instant when the value is a bare ISO timestamp)."""
    if value.startswith("now"):
        return _STEPS.fullmatch(value[3:]) is not None, None
    anchor, sep, steps = value.partition("||")
    instant = _iso(anchor)
    if instant is None:
        return False, None
    if not sep:
        return True, instant
    return _STEPS.fullmatch(steps) is not None, None


def time_range_errors(from_: str, to: str) -> list[str]:
    """Every reason Kibana's dashboard app could not use this time range."""
    errors: list[str] = []
    parsed: dict[str, datetime | None] = {}
    for name, value in (("from", from_), ("to", to)):
        valid, instant = _parse(value)
        if not valid:
            errors.append(f"time_range.{name} '{value}' is not a time Kibana can read")
        parsed[name] = instant
    start, end = parsed["from"], parsed["to"]
    if (
        start is not None and end is not None
        and (start.tzinfo is None) == (end.tzinfo is None)
        and start >= end
    ):
        errors.append(f"time_range.from '{from_}' is not before time_range.to '{to}'")
    return errors


def refresh_seconds(stored: Any) -> int | float | None:
    """A stored refresh_interval in update_dashboard's own terms: None when
    none is stored, 0 when paused, else the interval in seconds (a fraction
    when the stored milliseconds are not whole seconds: Kibana stores 1 ms)."""
    if not isinstance(stored, dict):
        return None
    if stored.get("pause", True):
        return 0
    value = stored.get("value", 0)
    if not isinstance(value, (int, float)):
        return None
    seconds = value / 1000
    return int(seconds) if seconds == int(seconds) else seconds


def refresh_milliseconds(seconds: float) -> int:
    """`seconds` as the whole milliseconds Kibana stores."""
    return round(seconds * 1000)


def refresh_interval_for(seconds: float, stored: Any) -> dict[str, Any]:
    """The refresh_interval to store for `seconds` (0 pauses, keeping a
    stored interval's value, as Kibana's own pause does)."""
    if seconds == 0:
        value = stored.get("value", 0) if isinstance(stored, dict) else 0
        return {"pause": True, "value": value}
    return {"pause": False, "value": refresh_milliseconds(seconds)}


def merged_tags(current: list[str], add: list[str], remove: list[str]) -> list[str]:
    """Current tags minus `remove`, then `add` appended in order; no repeats."""
    kept = [t for t in dict.fromkeys(current) if t not in remove]
    return kept + [t for t in dict.fromkeys(add) if t not in kept]


# The settings update_dashboard edits, in its result's order. Each is
# reported in the tool's own argument terms, so `previous` can be passed back.
SETTINGS = ("title", "description", "time_range", "refresh_interval_seconds", "tags")


def settings_view(data: dict[str, Any]) -> dict[str, Any]:
    """A dashboard's editable settings in update_dashboard's terms. The
    refresh is None on a dashboard that stores no time range: Kibana keeps a
    refresh there, but its dashboard app applies one only with the stored
    time, so it is reported as the dashboard opens."""
    stored = data.get("time_range")
    time_range = (
        {"from": stored.get("from"), "to": stored.get("to")}
        if isinstance(stored, dict) else None
    )
    return {
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "time_range": time_range,
        "refresh_interval_seconds": (
            refresh_seconds(data.get("refresh_interval")) if time_range else None),
        "tags": list(data.get("tags") or []),
    }


def apply_settings(
    data: dict[str, Any],
    *,
    title: str | None,
    description: str | None,
    time_range: dict[str, str] | None,
    clear_time_range: bool,
    refresh_interval_seconds: float | None,
    tags: list[str] | None,
) -> dict[str, Any]:
    """`data` with the given settings replaced and nothing else touched
    (panels, sections, pinned panels, filters, query and options stay as
    read). None leaves a setting as stored."""
    new = dict(data)
    if title is not None:
        new["title"] = title
    if description is not None:
        new["description"] = description
    if clear_time_range:
        new.pop("time_range", None)
    elif time_range is not None:
        new["time_range"] = dict(time_range)
    if refresh_interval_seconds is not None:
        new["refresh_interval"] = refresh_interval_for(
            refresh_interval_seconds, data.get("refresh_interval"))
    if tags is not None:
        new["tags"] = list(tags)
    return new


def _time_range_accepted(time_range: dict[str, Any]) -> bool:
    from_, to = time_range.get("from"), time_range.get("to")
    return (isinstance(from_, str) and isinstance(to, str)
            and not time_range_errors(from_, to))


def _refresh_accepted(seconds: int | float) -> bool:
    """The checks update_dashboard applies to refresh_interval_seconds."""
    return seconds == 0 or (
        0 < seconds <= MAX_REFRESH_SECONDS and refresh_milliseconds(seconds) > 0)


def undo_arguments(
    before: dict[str, Any], after: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """(the update_dashboard arguments that put `after` back to `before`,
    the settings left out of them). Both are settings views. Each value in
    the undo passes the tool's own argument checks; a value before that they
    refuse — one Kibana stored but the tool would not write: a blank title, a
    time the dashboard app cannot read, a negative, under-1 ms or over-limit
    refresh, an empty tag id — is left out and named, as is a refresh that
    could then only come back without a time range. Two checks read Kibana
    when the undo runs, so it can still be refused there: a title another
    dashboard's create_dashboard id holds, and a removed tag id that names
    no tag. A time range that was not stored is
    undone with clear_time_range. A refresh is reported only beside a stored
    time range, so one in `before` is restored beside it; a refresh that was
    not there can only be paused (0), and is left out when the undo clears
    the time range (the tool refuses a refresh with it, and the app ignores a
    refresh without a time range). A paused refresh comes back paused at the
    interval the call set: 0 carries no interval."""
    undo: dict[str, Any] = {}
    omitted: list[str] = []
    if after["title"] != before["title"]:
        if normalize(before["title"]):
            undo["title"] = before["title"]
        else:
            omitted.append("title")
    if after["description"] != before["description"]:
        undo["description"] = before["description"]
    if after["time_range"] != before["time_range"]:
        if before["time_range"] is None:
            undo["clear_time_range"] = True
        elif _time_range_accepted(before["time_range"]):
            undo["time_range"] = before["time_range"]
        else:
            omitted.append("time_range")
    seconds = before["refresh_interval_seconds"]
    if after["refresh_interval_seconds"] != seconds:
        timed = "time_range" in undo or (
            after["time_range"] is not None and not undo.get("clear_time_range"))
        if seconds is None:
            if timed:
                undo["refresh_interval_seconds"] = 0
        elif timed and _refresh_accepted(seconds):
            undo["refresh_interval_seconds"] = seconds
        else:
            omitted.append("refresh_interval_seconds")
    added = [t for t in after["tags"] if t not in before["tags"]]
    # A stored "" tag id is never removed (tags_to_remove takes no empty id),
    # so `removed` holds only ids tags_to_add accepts.
    removed = [t for t in before["tags"] if t not in after["tags"]]
    if removed:
        undo["tags_to_add"] = removed
    if added:
        undo["tags_to_remove"] = added
    return undo, omitted
