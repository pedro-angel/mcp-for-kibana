"""Pure checks and guidance for saved objects between spaces (D49)."""

import pytest

from kibana_mcp.core.models import ObjectSpaces
from kibana_mcp.core.space_sharing import (
    copy_fix,
    duplicates,
    missing_spaces_message,
    objects_not_found_message,
    self_target_message,
    sharing_refusal,
    source_fixed_reason,
    space_failure_fix,
    unreadable_space_fix,
    unreadable_spaces_message,
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


def test_unreadable_spaces_never_say_missing_or_create():
    # Kibana answers 403 for an id the key has no privilege in, existing or not.
    one = unreadable_spaces_message(["ops"])
    assert one.startswith("cannot write into space 'ops': this API key has no privilege in 'ops'")
    assert "create_space" not in one and "not found" not in one
    assert "list_spaces shows the spaces this key can view" in one
    assert unreadable_spaces_message(["a", "b"]).startswith("cannot write into spaces 'a', 'b'")
    both = unreadable_spaces_message(["ops"], ["ghost"])
    assert "also space 'ghost' not found" in both and both.endswith("nothing was changed")
    assert "KIBANA_API_KEY" in unreadable_space_fix(["ops"])


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


@pytest.mark.parametrize(("status", "error", "message", "needle"), [
    (403, "Forbidden", "Unable to bulk_create index-pattern", "use a new API key"),
    (500, "Internal Server Error", "", "then copy into that space again"),
    (None, "", "", "(no detail)"),
])
def test_space_failure_fix_names_the_space_kibanas_error_and_the_fix(
    status, error, message, needle
):
    fix = space_failure_fix("team-b", status, error, message)
    assert fix.startswith("Kibana refused the whole copy into space 'team-b'")
    assert "nothing was written there" in fix and needle in fix


def test_space_failure_fix_on_a_403_names_both_ways_to_send_a_key():
    # HTTP mode reads the key from each request's Authorization header and
    # ignores KIBANA_API_KEY by default, so the fix names both (auth _GUIDANCE).
    fix = space_failure_fix("team-b", 403, "Forbidden", "Unable to bulk_create index-pattern")
    assert "'Authorization: ApiKey <key>'" in fix and "KIBANA_API_KEY" in fix


def test_guidance_offers_source_space_only_where_it_is_accepted():
    assert source_fixed_reason(False, False) is None
    assert "pass source_space" in self_target_message("default")
    assert "pass source_space" in objects_not_found_message(["index-pattern/x"])
    # Each refusing deployment names the URL that fixes the source, never
    # KIBANA_URL when only the public URL is pinned.
    for reason, url, other in (
        (source_fixed_reason(True, False), "KIBANA_URL pins", "KIBANA_PUBLIC_URL"),
        (source_fixed_reason(True, True), "KIBANA_URL pins", "KIBANA_PUBLIC_URL"),
        (source_fixed_reason(False, True), "KIBANA_PUBLIC_URL", "KIBANA_URL"),
    ):
        target = self_target_message("default", source_fixed=reason)
        fixed = objects_not_found_message(["index-pattern/x"], source_fixed=reason)
        missing = [_state([], id="nope", missing=True)]
        shared = sharing_refusal(missing, source_fixed=reason) or ""
        for text in (target, fixed, shared):
            assert "source_space" not in text and url in text and other not in text
        assert "find_saved_objects" in fixed
    assert "default space" in self_target_message("default", source_fixed_reason(False, True))


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
