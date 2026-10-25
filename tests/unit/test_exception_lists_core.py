"""Exception-list export files (D50): parsing, the new-copy rewrite and the
content-free import errors. Lines are shaped like a 9.5.4 export (probed)."""

import json

import pytest

from kibana_mcp.core.exception_lists import (
    as_new_copy,
    duplicable,
    import_error_text,
    item_count,
    parse_export,
)
from kibana_mcp.core.saved_objects import to_ndjson

_LIST = {
    "_version": "WzI1MCwxXQ==", "id": "ba677e57", "list_id": "src", "name": "Src",
    "description": "d", "type": "detection", "namespace_type": "single",
    "os_types": ["linux"], "tags": ["t"], "tie_breaker_id": "77f3", "version": 7,
}
_ITEM = {
    "_version": "WzI0NCwxXQ==", "id": "d2e3", "item_id": "it-1", "list_id": "src",
    "name": "it", "description": "d", "type": "simple", "namespace_type": "single",
    "entries": [{"type": "match", "field": "host.name", "value": "x", "operator": "included"}],
    "tie_breaker_id": "cd7c",
}
_DETAILS = {"exported_exception_list_count": 1, "exported_exception_list_item_count": 2}
_BODY = [_LIST, _ITEM, {**_ITEM, "id": "e5", "item_id": "it-2"}, _DETAILS]


def test_parse_export_splits_list_items_details():
    export = parse_export(to_ndjson(_BODY))
    assert export.list_id == "src"
    assert export.namespace_type == "single"
    assert export.item_ids == ("it-1", "it-2")


@pytest.mark.parametrize("body", [[_ITEM, _DETAILS], [_LIST, {**_LIST, "list_id": "b"}]])
def test_parse_export_wants_exactly_one_list(body):
    with pytest.raises(ValueError, match="export_exception_list"):
        parse_export(to_ndjson(body))


def test_as_new_copy_fresh_ids_and_no_generated_fields():
    export = parse_export(to_ndjson(_BODY))
    content, new_list_id = as_new_copy(export, None)
    lines = [json.loads(x) for x in content.decode().splitlines()]
    assert new_list_id != "src" and len(new_list_id) == 36  # a uuid
    assert all(line["list_id"] == new_list_id for line in lines)
    items = [x for x in lines if "item_id" in x]
    assert len(items) == 2
    assert not {"it-1", "it-2"} & {x["item_id"] for x in items}  # never the source ids
    assert len({x["item_id"] for x in items}) == 2
    for line in lines:
        assert not {"id", "_version", "tie_breaker_id"} & set(line)
    assert lines[0]["name"] == "Src" and lines[0]["tags"] == ["t"]
    assert items[0]["entries"] == _ITEM["entries"]
    # the details line is not re-sent
    assert all("exported_exception_list_count" not in x for x in lines)


def test_as_new_copy_takes_a_chosen_list_id():
    _, new_list_id = as_new_copy(parse_export(to_ndjson(_BODY)), "chosen")
    assert new_list_id == "chosen"


def test_item_count_prefers_details_then_counts_lines():
    assert item_count(_BODY) == 2
    assert item_count([_LIST, _ITEM]) == 1


@pytest.mark.parametrize("t,ok", [
    ("detection", True), ("rule_default", True), ("endpoint", False),
    ("endpoint_trusted_apps", False),
])
def test_duplicable(t, ok):
    assert duplicable(t) is ok


def test_import_error_text_is_identity_and_status_only():
    assert import_error_text({
        "error": {"status_code": 409, "message": 'Found that list_id: "a" already exists.'},
        "list_id": "a",
    }) == "a: 409 already exists"
    assert import_error_text({
        "error": {"status_code": 400, "message": 'Invalid value "secret-host"'},
        "list_id": "a", "item_id": "i",
    }) == "a/i: 400 invalid"
    assert import_error_text({"error": {}}) == "?: ? error"
