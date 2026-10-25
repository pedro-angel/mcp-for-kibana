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

_UNIT = r"(?:ms|[smhdwMy])"
_STEPS = re.compile(rf"(?:[+-]\d*{_UNIT}|/1?{_UNIT})*")

DATE_MATH_HINT = (
    "use date math such as now, now-24h, now-7d/d or now/w (units ms s m h d w M y), "
    "or an ISO 8601 timestamp such as 2026-01-31T00:00:00Z"
)

# Kibana's dashboard schema caps a dashboard's tags at 100 (9.5.4).
MAX_DASHBOARD_TAGS = 100


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
    none is stored, 0 when paused, else the interval in seconds."""
    if not isinstance(stored, dict):
        return None
    if stored.get("pause", True):
        return 0
    value = stored.get("value", 0)
    if not isinstance(value, (int, float)):
        return None
    seconds = value / 1000
    return int(seconds) if seconds == int(seconds) else seconds


def refresh_interval_for(seconds: int, stored: Any) -> dict[str, Any]:
    """The refresh_interval to store for `seconds` (0 pauses, keeping a
    stored interval's value, as Kibana's own pause does)."""
    if seconds == 0:
        value = stored.get("value", 0) if isinstance(stored, dict) else 0
        return {"pause": True, "value": value}
    return {"pause": False, "value": seconds * 1000}


def merged_tags(current: list[str], add: list[str], remove: list[str]) -> list[str]:
    """Current tags minus `remove`, then `add` appended in order; no repeats."""
    kept = [t for t in dict.fromkeys(current) if t not in remove]
    return kept + [t for t in dict.fromkeys(add) if t not in kept]


# The settings update_dashboard edits, in its result's order. Each is
# reported in the tool's own argument terms, so `previous` can be passed back.
SETTINGS = ("title", "description", "time_range", "refresh_interval_seconds", "tags")


def settings_view(data: dict[str, Any]) -> dict[str, Any]:
    """A dashboard's editable settings in update_dashboard's terms."""
    time_range = data.get("time_range")
    return {
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "time_range": (
            {"from": time_range.get("from"), "to": time_range.get("to")}
            if isinstance(time_range, dict) else None
        ),
        "refresh_interval_seconds": refresh_seconds(data.get("refresh_interval")),
        "tags": list(data.get("tags") or []),
    }


def apply_settings(
    data: dict[str, Any],
    *,
    title: str | None,
    description: str | None,
    time_range: dict[str, str] | None,
    clear_time_range: bool,
    refresh_interval_seconds: int | None,
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


def undo_arguments(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """update_dashboard arguments that put `after` back to `before` (both
    settings views). A time range that was not stored is undone with
    clear_time_range; a refresh that was not stored can only be paused (0),
    and is left out when the undo clears the time range (Kibana's dashboard
    app ignores a refresh without one)."""
    undo: dict[str, Any] = {}
    for key in ("title", "description"):
        if after[key] != before[key]:
            undo[key] = before[key]
    if after["time_range"] != before["time_range"]:
        if before["time_range"] is None:
            undo["clear_time_range"] = True
        else:
            undo["time_range"] = before["time_range"]
    seconds = before["refresh_interval_seconds"]
    if after["refresh_interval_seconds"] != seconds:
        if seconds is not None:
            undo["refresh_interval_seconds"] = seconds
        elif not undo.get("clear_time_range"):
            undo["refresh_interval_seconds"] = 0
    added = [t for t in after["tags"] if t not in before["tags"]]
    removed = [t for t in before["tags"] if t not in after["tags"]]
    if removed:
        undo["tags_to_add"] = removed
    if added:
        undo["tags_to_remove"] = added
    return undo
