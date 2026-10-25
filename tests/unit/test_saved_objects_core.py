"""Core saved-objects handle mechanics (#37) — especially the security boundary:
a handle can only ever resolve to a regular file inside export_dir."""

import os
import re

import pytest

from kibana_mcp.core.errors import KibanaNotFound
from kibana_mcp.core.saved_objects import (
    _MAX_RETAINED,
    new_handle,
    read_export,
    resolve_handle_path,
    summarize_export,
    to_ndjson,
    write_export,
)

_VALID = "so-abcdef012345"


def test_new_handle_format():
    assert re.fullmatch(r"so-[0-9a-f]{12}", new_handle())


def test_to_ndjson_full_body():
    nd = to_ndjson([{"type": "index-pattern", "id": "a"}, {"exportedCount": 1}])
    lines = nd.decode().splitlines()
    assert len(lines) == 2 and '"index-pattern"' in lines[0]


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd", "/etc/passwd", "so-../../etc", "so-", "so-xyz",
        "SO-ABCDEF012345", "so-abcdef0123456", "so-abcdef01234",  # wrong case / 13 / 11
        "so-abcdef012345\n", "so-abcdef012345 ", " so-abcdef012345",  # newline / spaces
        "..", "", "so-abcde/012345", "so-abcdef01234z",  # slash / non-hex
    ],
)
def test_resolve_handle_path_rejects_bad(tmp_path, bad):
    with pytest.raises(ValueError):
        resolve_handle_path(tmp_path, bad)


def test_resolve_handle_path_accepts_valid_and_confines(tmp_path):
    p = resolve_handle_path(tmp_path, _VALID)
    assert p.parent == tmp_path.resolve()
    assert p.name == f"{_VALID}.ndjson"


def test_write_read_roundtrip(tmp_path):
    content = b'{"type":"x","id":"1"}\n{"exportedCount":1}'
    handle = write_export(tmp_path, content)
    assert re.fullmatch(r"so-[0-9a-f]{12}", handle)
    assert read_export(tmp_path, handle) == content
    assert (tmp_path / f"{handle}.ndjson").stat().st_mode & 0o777 == 0o600


def test_read_unknown_handle_raises_notfound(tmp_path):
    with pytest.raises(KibanaNotFound):
        read_export(tmp_path, "so-000000000000")


def test_read_refuses_symlink_via_nofollow(tmp_path):
    # A valid-token name that is a SYMLINK (even one pointing INSIDE export_dir,
    # so the resolved-parent check passes) must be refused by O_NOFOLLOW.
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"TOPSECRET")
    link = tmp_path / f"{_VALID}.ndjson"
    os.symlink(secret, link)
    with pytest.raises(ValueError):
        read_export(tmp_path, _VALID)


def test_prune_bounds_retention(tmp_path):
    for _ in range(_MAX_RETAINED + 5):
        write_export(tmp_path, b"x")
    assert len(list(tmp_path.glob("so-*.ndjson"))) == _MAX_RETAINED


def test_summarize_splits_details_and_maps_refs():
    body = [
        {"type": "dashboard", "id": "d1"},
        {"type": "index-pattern", "id": "i1"},
        {"type": "index-pattern", "id": "i2"},
        {
            "exportedCount": 3, "missingRefCount": 1,
            "missingReferences": [{"type": "index-pattern", "id": "gone"}],
            "excludedObjectsCount": 2,
        },
    ]
    s = summarize_export(body, _VALID, 123)
    assert s.handle == _VALID and s.exported_count == 3 and s.byte_size == 123
    assert {t.type: t.count for t in s.types} == {"dashboard": 1, "index-pattern": 2}
    assert s.missing_ref_count == 1
    assert s.missing_references == ("index-pattern/gone",)  # dict -> "type/id" string
    assert s.excluded_count == 2


# --- get_saved_object's attribute cap (D48) ---

from kibana_mcp.core.saved_objects import ATTRIBUTE_CHARS_SHOWN, cap_attributes  # noqa: E402


def test_cap_attributes_keeps_short_values_and_sizes_long_ones():
    limit = ATTRIBUTE_CHARS_SHOWN
    shown, omitted = cap_attributes({
        "title": "t", "panelsJSON": "x" * (limit + 1), "edge": "y" * limit,
        "nested": {"k": "v" * limit}, "count": 3,
    })
    assert shown == {"title": "t", "edge": "y" * limit, "count": 3}
    # A string's size is its length; another value's, its compact JSON text.
    assert omitted == {"panelsJSON": limit + 1, "nested": limit + 8}


def test_cap_attributes_limit_is_the_one_the_description_states():
    assert ATTRIBUTE_CHARS_SHOWN == 4_000


# --- two handle kinds share the export dir (D50) ---


def test_exception_list_handles_have_their_own_kind(tmp_path):
    from kibana_mcp.core.saved_objects import EXCEPTION_LIST

    handle = write_export(tmp_path, b"x", EXCEPTION_LIST)
    assert re.fullmatch(r"el-[0-9a-f]{12}", handle)
    assert read_export(tmp_path, handle, EXCEPTION_LIST) == b"x"
    # each import reads only its own kind, and names the tools of the other
    with pytest.raises(ValueError, match="pass it to import_exception_list or overwrite"):
        read_export(tmp_path, handle)
    so = write_export(tmp_path, b"y")
    with pytest.raises(ValueError, match="pass it to import_saved_objects or overwrite"):
        read_export(tmp_path, so, EXCEPTION_LIST)


def test_missing_exception_list_handle_names_the_export_tool(tmp_path):
    from kibana_mcp.core.saved_objects import EXCEPTION_LIST

    with pytest.raises(KibanaNotFound, match="call export_exception_list again"):
        read_export(tmp_path, "el-000000000000", EXCEPTION_LIST)


def test_prune_is_per_kind(tmp_path):
    from kibana_mcp.core.saved_objects import EXCEPTION_LIST

    for _ in range(_MAX_RETAINED + 3):
        write_export(tmp_path, b"x", EXCEPTION_LIST)
    write_export(tmp_path, b"y")
    assert len(list(tmp_path.glob("el-*.ndjson"))) == _MAX_RETAINED
    assert len(list(tmp_path.glob("so-*.ndjson"))) == 1
