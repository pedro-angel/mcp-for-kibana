"""Dashboard payload assembly: deterministic grid layout on Kibana's 48-col grid."""

import copy
from typing import Any

_HALF_W, _ROW_H, _FULL_W, _FULL_H = 24, 10, 48, 12


def layout_panels(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(configs) == 1:
        grid = {"x": 0, "y": 0, "w": _FULL_W, "h": _FULL_H}
        return [{"type": "vis", "grid": grid, "config": configs[0]}]
    panels: list[dict[str, Any]] = []
    for i, cfg in enumerate(configs):
        grid = {
            "x": (i % 2) * _HALF_W,
            "y": (i // 2) * _ROW_H,
            "w": _HALF_W,
            "h": _ROW_H,
        }
        panels.append({"type": "vis", "grid": grid, "config": cfg})
    return panels


def build_dashboard_data(
    title: str,
    description: str,
    panels: list[dict[str, Any]],
    time_range: dict[str, Any] | None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"title": title, "description": description, "panels": panels}
    if time_range:
        data["time_range"] = time_range
    return data


def append_panel(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    new = copy.deepcopy(data)
    existing = new.get("panels", [])
    bottom = max(
        (
            p["grid"]["y"] + p["grid"].get("h", _ROW_H)
            for p in existing
            if isinstance(p.get("grid"), dict) and "y" in p["grid"]
        ),
        default=0,
    )
    existing.append(
        {"type": "vis", "grid": {"x": 0, "y": bottom, "w": _HALF_W, "h": _ROW_H}, "config": config}
    )
    new["panels"] = existing
    return new


def replace_panel_config(
    data: dict[str, Any], index: int, config: dict[str, Any]
) -> dict[str, Any]:
    new = copy.deepcopy(data)
    new["panels"][index]["config"] = config
    return new


def remove_panel(data: dict[str, Any], index: int) -> dict[str, Any]:
    new = copy.deepcopy(data)
    del new["panels"][index]
    return new
