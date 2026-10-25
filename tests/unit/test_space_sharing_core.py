"""Pure checks and guidance for saved objects between spaces (D49)."""

import pytest

from kibana_mcp.core.models import ObjectSpaces
from kibana_mcp.core.space_sharing import (
    copy_fix,
    duplicates,
    missing_spaces_message,
    sharing_refusal,
    unshare_refusal,
)


def _state(spaces, type="index-pattern", id="dv1", missing=False):
    return ObjectSpaces(type=type, id=id, spaces=tuple(spaces), missing=missing,
                        spaces_with_matching_origins=())


def test_missing_spaces_message_names_every_space_and_the_fix():
    one = missing_spaces_message(["ghost"])
    assert one.startswith("space 'ghost' not found")
    assert "without an error" in one and "list_spaces" in one and "create_space" in one
    assert missing_spaces_message(["a", "b"]).startswith("spaces 'a', 'b' not found")


@pytest.mark.parametrize(("error", "overwrite", "needle"), [
    ("missing_references", False, "include_references=true"),
    ("unsupported_type", True, "cannot copy this type"),
    ("ambiguous_conflict", True, "copy_saved_objects_to_spaces"),
    ("conflict", False, "overwrite_saved_objects_in_spaces"),
    ("conflict", True, "Kibana did not copy it (conflict)"),
    ("something_new", False, "Kibana did not copy it (something_new)"),
])
def test_copy_fix_per_error(error, overwrite, needle):
    assert needle in copy_fix(error, overwrite)


def test_sharing_refusal_missing_first_then_one_space_types():
    assert sharing_refusal([_state(["default"])]) is None
    missing = sharing_refusal([_state([], id="nope", missing=True), _state([], type="dashboard")])
    assert missing is not None
    assert "index-pattern/nope" in missing and "find_saved_objects" in missing
    single = sharing_refusal([_state([], type="dashboard", id="d1"), _state(["default"])])
    assert single is not None
    assert single.startswith("dashboard/d1: Kibana keeps 'dashboard' objects in one space")


def test_unshare_refusal_nothing_to_remove_then_last_space():
    states = [_state(["default", "team-a"]), _state(["team-a"], id="dv2")]
    assert unshare_refusal(states, ["team-b"], True) is not None
    assert "nothing to remove" in unshare_refusal(states, ["team-b"], True)
    last = unshare_refusal(states, ["team-a"], False)
    assert last is not None
    assert "index-pattern/dv2 is only in ['team-a']" in last and "dv1" not in last
    assert unshare_refusal(states, ["team-a"], True) is None  # forced
    assert unshare_refusal([_state(["default", "*"])], ["default"], False) is None
    assert unshare_refusal([_state(["*"])], ["*"], False) is not None


def test_duplicates_in_first_seen_order():
    assert duplicates(["a", "b", "a", "c", "b", "a"]) == ["a", "b"]
    assert duplicates([]) == []
