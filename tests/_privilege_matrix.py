"""The privilege matrix's rows, as plain data (D58).

A tool is privilege-dependent when what it does depends on which spaces the
calling key can see: it takes `space`, `source_space`, `target_spaces` or
`spaces`, or it acts on a space listing (LISTING_GUARDS). The contract matrix
(tests/contract/test_privilege_matrix.py) calls every one of them under
restricted keys; tests/unit/test_privilege_matrix_rows.py holds this set to
the registry, so a new tool that takes a space cannot ship without a row.
"""

from typing import Any

SPACE_PARAMS = ("space", "source_space", "target_spaces", "spaces")

# Tools that take no space but read or act on what a space listing shows.
LISTING_GUARDS = frozenset({"list_spaces", "uninstall_package"})

# Arguments the schema alone cannot make valid (a value Kibana must parse, or
# a choice the generic filler gets wrong). Merged over the generated ones.
_PANEL = {"title": "ct matrix", "chart_type": "metric", "data_view": "mcp-for-kibana-ct-matrix",
          "metrics": [{"agg": "count"}]}
_ONE_OF_LIST = {"list_id": "mcp-for-kibana-ct-matrix"}
_ONE_OF_RULE = {"rule_id": "mcp-for-kibana-ct-matrix"}
OVERRIDES: dict[str, dict[str, Any]] = {
    "create_dashboard": {"panels": [_PANEL]},
    "create_visualization": {"spec": _PANEL},
    "update_visualization": {"spec": _PANEL},
    "add_panel": {"panel": _PANEL},
    "update_panel": {"panel": _PANEL},
    "update_dashboard": {"description": "ct matrix"},
    "get_saved_object": {"type": "dashboard"},
    "export_saved_objects": {"types": ["dashboard"]},
    # a well-formed handle that names no export: refused once the space is read
    "import_saved_objects": {"handle": "so-000000000000"},
    "overwrite_saved_objects": {"handle": "so-000000000000"},
    "import_exception_list": {"handle": "el-000000000000"},
    "overwrite_exception_list": {"handle": "el-000000000000"},
    "update_data_view": {"name": "ct matrix"},
    "create_short_url": {"locator_id": "LEGACY_SHORT_URL_LOCATOR",
                         "params": {"url": "/app/dashboards"}},
    "update_alert_rule": {"name": "ct matrix"},
    "update_connector": {"name": "ct matrix"},
    "snooze_alert_rule": {"duration": "1h"},
    "update_case": {"title": "ct matrix"},
    "export_exception_list": _ONE_OF_LIST,
    "update_exception_list": {**_ONE_OF_LIST, "name": "ct matrix"},
    "duplicate_exception_list": _ONE_OF_LIST,
    "delete_exception_list": _ONE_OF_LIST,
    "update_detection_rule": {**_ONE_OF_RULE, "name": "ct matrix"},
    "replace_detection_rule": {**_ONE_OF_RULE, "name": "ct matrix"},
    "delete_detection_rule": _ONE_OF_RULE,
    "delete_exception_item": {"item_id": "mcp-for-kibana-ct-matrix"},
}

# The handles OVERRIDES names; the matrix writes each into its export directory
# so the call reads the file and reaches the space check.
EXPORTS = {
    "so-000000000000": '{"type":"index-pattern","id":"mcp-for-kibana-ct-matrix",'
                       '"attributes":{"title":"kibana_sample_data_flights","name":"ct matrix"},'
                       '"references":[]}\n{"exportedCount":1,"missingRefCount":0,'
                       '"missingReferences":[]}',
    "el-000000000000": '{"list_id":"mcp-for-kibana-ct-matrix","name":"ct matrix",'
                       '"description":"ct matrix","type":"detection"}\n'
                       '{"exported_exception_list_count":1,"exported_exception_list_item_count":0}',
}


def space_params(schema: dict[str, Any]) -> frozenset[str]:
    return frozenset(schema.get("properties", {})) & frozenset(SPACE_PARAMS)


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = defs[node["$ref"].rsplit("/", 1)[1]]
    if "anyOf" in node:  # Optional[X]: take the first non-null branch
        node = next(b for b in node["anyOf"] if b.get("type") != "null")
        return _resolve(node, defs)
    return node


def _value(node: dict[str, Any], defs: dict[str, Any], name: str) -> Any:
    node = _resolve(node, defs)
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    kind = node.get("type")
    if kind == "string":
        if node.get("format") == "date-time":
            return "2026-01-01T00:00:00Z"
        return "mcp-for-kibana-ct-matrix"
    if kind == "integer":
        return max(1, node.get("minimum", 1))
    if kind == "number":
        return max(1, node.get("minimum", 1))
    if kind == "boolean":
        return False
    if kind == "array":
        item = _value(node.get("items", {"type": "string"}), defs, name)
        return [item] * max(1, node.get("minItems", 1))
    if kind == "object" or "properties" in node:
        return _object(node, defs)
    return "mcp-for-kibana-ct-matrix"


def _object(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    props = node.get("properties", {})
    return {k: _value(props[k], defs, k) for k in node.get("required", ()) if k in props}


def minimal_args(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The tool's required arguments, filled from its schema, plus OVERRIDES.
    The values name nothing real: every row expects a refusal or a curated
    error, never a change."""
    args = _object(schema, schema.get("$defs", {}))
    args.update(OVERRIDES.get(name, {}))
    return args
