"""Adapter tests for saved objects between spaces (D49). Raw bodies are the
shapes captured live on Kibana 9.5.4 (Basic): `_copy_saved_objects` answers a
map of target space -> result, per-object failures inside a 200;
`_get_shareable_references` answers `spaces: []` for a type kept in one space
and `isMissing` for an object the source space does not hold;
`_update_objects_spaces` answers each object's spaces after the change."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway, is_space_pinned, pinned_space
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected, KibanaSpaceNotFound
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

_SPACES = [{"id": "default", "name": "Default", "_reserved": True},
           {"id": "team-a", "name": "Team A"}, {"id": "team-b", "name": "Team B"}]
_DV = {"type": "index-pattern", "id": "dv1"}
_DASH = {"type": "dashboard", "id": "dash-1"}


class _Spaces:
    """Records every call; answers with the bodies it was given."""

    def __init__(self, copy=None, refs=None, update=None, copy_error=None):
        self.calls: list[tuple[str, dict]] = []
        self._copy, self._refs, self._update, self._copy_error = copy, refs, update, copy_error

    def get_all(self, **kw):
        self.calls.append(("get_all", kw))
        return FakeResponse(_SPACES)

    def copy_saved_objects(self, **kw):
        self.calls.append(("copy", kw))
        if self._copy_error is not None:
            raise self._copy_error
        return FakeResponse(self._copy)

    def get_shareable_references(self, **kw):
        self.calls.append(("refs", kw))
        return FakeResponse(self._refs)

    def update_objects_spaces(self, **kw):
        self.calls.append(("update", kw))
        return FakeResponse(self._update)


def _gw(spaces: _Spaces, space=None, pinned=None):
    return KibanaPyGateway(make_fake_client(spaces=spaces), space, pinned_space=pinned)


def _names(spaces: _Spaces) -> list[str]:
    return [name for name, _ in spaces.calls]


# --- copy ---------------------------------------------------------------------

_COPIED = {
    "team-a": {"success": True, "successCount": 2, "successResults": [
        {"type": "index-pattern", "id": "dv1", "meta": {"title": "flights"},
         "managed": False, "destinationId": "9f8d"},
        {"type": "dashboard", "id": "dash-1", "meta": {"title": "[Flights] Global"},
         "managed": False, "destinationId": "5355"},
    ]},
}


def test_copy_makes_new_copies_with_references_after_checking_the_targets():
    spaces = _Spaces(copy=_COPIED)
    [result] = _gw(spaces).copy_saved_objects_to_spaces([_DASH], ["team-a"], True, False)
    assert _names(spaces) == ["get_all", "copy"]
    assert spaces.calls[1][1] == {
        "spaces": ["team-a"], "objects": [_DASH], "include_references": True,
        "create_new_copies": True,
    }
    assert result.space == "team-a" and result.success is True
    assert [(c.type, c.id, c.destination_id, c.overwritten) for c in result.copied] == [
        ("index-pattern", "dv1", "9f8d", False), ("dashboard", "dash-1", "5355", False)]
    assert result.copied[1].title == "[Flights] Global"
    assert result.failed == () and result.unchanged == ()


def test_overwrite_sends_overwrite_without_new_copies_and_flags_replacements():
    body = {"team-a": {"success": True, "successCount": 1, "successResults": [
        {"type": "index-pattern", "id": "dv1", "meta": {"title": "flights"},
         "overwrite": True, "destinationId": "0d05"}]}}
    spaces = _Spaces(copy=body)
    [result] = _gw(spaces).copy_saved_objects_to_spaces([_DV], ["team-a"], False, True)
    assert spaces.calls[1][1] == {
        "spaces": ["team-a"], "objects": [_DV], "include_references": False,
        "create_new_copies": False, "overwrite": True,
    }
    assert result.copied[0].overwritten is True and result.copied[0].destination_id == "0d05"


def test_copy_reports_an_object_already_in_the_target_as_unchanged():
    # Overwriting an object already shared to the target: 200, successCount 0,
    # no successResults, no errors (probed).
    spaces = _Spaces(copy={"team-a": {"success": True, "successCount": 0}})
    [result] = _gw(spaces).copy_saved_objects_to_spaces([_DV], ["team-a"], False, True)
    assert result.success is True and result.copied == () and result.failed == ()
    assert result.unchanged == (_DV,)


def test_copy_failures_carry_kibanas_error_and_the_fix():
    body = {
        "team-a": {"success": False, "successCount": 0, "errors": [
            {"id": "dash-1", "type": "dashboard", "meta": {"title": "[Flights] Global"},
             "error": {"type": "missing_references", "references": [
                 {"type": "search", "id": "5367"}, {"type": "index-pattern", "id": "9f8d"}]}},
        ]},
        "team-b": {"success": False, "successCount": 0, "errors": [
            {"type": "index-pattern", "id": "dv1", "meta": {"title": "flights"},
             "error": {"type": "ambiguous_conflict", "destinations": [
                 {"id": "c065", "title": "flights", "updatedAt": "2026-10-25T05:26:56.805Z"},
                 {"id": "cc12", "title": "flights", "updatedAt": "2026-10-25T05:26:49.372Z"}]}},
            {"id": "out-1", "type": "ingest-outputs", "meta": {},
             "error": {"type": "unsupported_type"}},
        ]},
    }
    spaces = _Spaces(copy=body)
    a, b = _gw(spaces).copy_saved_objects_to_spaces(
        [_DASH, _DV, {"type": "ingest-outputs", "id": "out-1"}], ["team-a", "team-b"], False, True)
    (missing,) = a.failed
    assert missing.error == "missing_references"
    assert missing.missing_references == (
        {"type": "search", "id": "5367"}, {"type": "index-pattern", "id": "9f8d"})
    assert "include_references=true" in missing.fix
    ambiguous, unsupported = b.failed
    assert ambiguous.destinations == ({"id": "c065", "title": "flights"},
                                      {"id": "cc12", "title": "flights"})
    assert "copy_saved_objects_to_spaces" in ambiguous.fix
    assert unsupported.error == "unsupported_type" and unsupported.title is None
    assert "cannot copy this type" in unsupported.fix
    # an object that failed is not "unchanged"
    assert a.unchanged == ({"type": "index-pattern", "id": "dv1"},
                           {"type": "ingest-outputs", "id": "out-1"})
    assert b.unchanged == (_DASH,)


def test_a_whole_target_space_failure_fails_its_objects_and_keeps_the_other_copies():
    # Captured live: a user with `all` in team-a and `read` in team-b. Kibana's
    # copy route catches the failed import into team-b and answers it with the
    # bare error (Boom payload, no object); the team-a copy was written, and
    # its destination id must survive.
    body = {
        "team-a": {"success": True, "successCount": 1, "successResults": [
            {"type": "index-pattern", "id": "dv1", "destinationId": "new-1"}]},
        "team-b": {"success": False, "successCount": 0, "errors": [
            {"statusCode": 403, "error": "Forbidden",
             "message": "Unable to bulk_create index-pattern"}]},
    }
    a, b = _gw(_Spaces(copy=body)).copy_saved_objects_to_spaces(
        [_DV], ["team-a", "team-b"], True, False)
    assert a.copied[0].destination_id == "new-1" and a.failed == ()
    assert b.success is False and b.copied == () and b.unchanged == ()
    (failure,) = b.failed
    assert (failure.type, failure.id, failure.error) == ("index-pattern", "dv1", "space_error")
    assert "403 Forbidden: Unable to bulk_create index-pattern" in failure.fix
    assert "configure a new KIBANA_API_KEY" in failure.fix
    assert "leave the space out of target_spaces" in failure.fix


def test_a_space_failure_beside_object_errors_fails_only_the_rest():
    body = {"team-a": {"success": False, "successCount": 0, "errors": [
        {"type": "dashboard", "id": "dash-1", "error": {"type": "missing_references"}},
        {"statusCode": 500, "error": "Internal Server Error", "message": "boom"}]}}
    [result] = _gw(_Spaces(copy=body)).copy_saved_objects_to_spaces(
        [_DASH, _DV], ["team-a"], False, False)
    assert [(f.id, f.error) for f in result.failed] == [
        ("dash-1", "missing_references"), ("dv1", "space_error")]
    assert "then copy into that space again" in result.failed[1].fix
    assert result.unchanged == ()


def test_copy_refuses_a_target_space_that_does_not_exist_before_copying():
    spaces = _Spaces(copy=_COPIED)
    with pytest.raises(KibanaSpaceNotFound) as exc:
        _gw(spaces).copy_saved_objects_to_spaces([_DV], ["team-a", "nope", "gone"], True, False)
    assert _names(spaces) == ["get_all"]  # nothing copied
    assert "spaces 'nope', 'gone' not found" in exc.value.message
    assert "list_spaces" in exc.value.message and "create_space" in exc.value.message


@pytest.mark.parametrize(("space", "pinned", "target"), [
    (None, None, "default"), ("team-a", None, "team-a"), (None, "team-b", "team-b"),
])
def test_copy_refuses_the_source_space_as_a_target(space, pinned, target):
    spaces = _Spaces(copy=_COPIED)
    with pytest.raises(KibanaRejected) as exc:
        _gw(spaces, space, pinned).copy_saved_objects_to_spaces([_DV], [target], True, False)
    assert f"already in space '{target}', the source space" in exc.value.message
    # source_space is refused on a pinned deployment, so it is never offered there
    assert ("source_space" in exc.value.message) is (pinned is None)
    assert "copy" not in _names(spaces)


def test_a_public_url_pin_also_keeps_source_space_out_of_the_guidance():
    spaces = _Spaces(copy=_COPIED)
    gw = KibanaPyGateway(make_fake_client(spaces=spaces), public_url_pinned=True)
    with pytest.raises(KibanaRejected) as exc:
        gw.copy_saved_objects_to_spaces([_DV], ["default"], True, False)
    assert "source_space" not in exc.value.message


@pytest.mark.parametrize("pinned", [None, "team-b"])
def test_object_not_found_offers_source_space_only_where_it_works(pinned):
    err = NotFoundError("Saved objects not found", meta=SimpleNamespace(status=404), body={
        "attributes": {"objects": [{"id": "nope", "type": "index-pattern"}]}})
    with pytest.raises(KibanaNotFound) as exc:
        _gw(_Spaces(copy_error=err), pinned=pinned).copy_saved_objects_to_spaces(
            [{"type": "index-pattern", "id": "nope"}], ["team-a"], True, False)
    assert ("pass source_space" in exc.value.message) is (pinned is None)
    missing = _Spaces(refs=_refs({"type": "index-pattern", "id": "nope", "isMissing": True}))
    with pytest.raises(KibanaRejected) as exc:
        _gw(missing, pinned=pinned).share_saved_objects(
            [{"type": "index-pattern", "id": "nope"}], ["team-a"])
    assert ("pass source_space" in exc.value.message) is (pinned is None)
    assert "find_saved_objects" in exc.value.message


def test_a_pinned_source_may_copy_into_the_default_space():
    spaces = _Spaces(copy={"default": _COPIED["team-a"]})
    [result] = _gw(spaces, pinned="team-b").copy_saved_objects_to_spaces(
        [_DV], ["default"], True, False)
    assert result.space == "default" and len(result.copied) == 2


def test_copy_of_a_missing_object_names_where_ids_come_from():
    # 404 {"message": "Saved objects not found", "attributes": {"objects": [...]}}
    err = NotFoundError("Saved objects not found", meta=SimpleNamespace(status=404), body={
        "statusCode": 404, "error": "Not Found", "message": "Saved objects not found",
        "attributes": {"objects": [{"id": "nope-xyz", "type": "index-pattern"}]}})
    spaces = _Spaces(copy_error=err)
    with pytest.raises(KibanaNotFound) as exc:
        _gw(spaces, "team-a").copy_saved_objects_to_spaces(
            [{"type": "index-pattern", "id": "nope-xyz"}], ["team-b"], True, False)
    message = exc.value.message
    assert "index-pattern/nope-xyz" in message and "find_saved_objects" in message
    assert message.endswith(" (in space 'team-a')")


# --- share --------------------------------------------------------------------

def _refs(*objects):
    return {"objects": list(objects)}


def test_share_reads_then_adds_and_reports_what_it_added():
    spaces = _Spaces(
        refs=_refs({"type": "index-pattern", "id": "dv1", "spaces": ["default"],
                    "inboundReferences": [], "spacesWithMatchingOrigins": ["team-b"]}),
        update={"objects": [{"id": "dv1", "type": "index-pattern",
                             "spaces": ["team-a", "team-b", "default"]}]},
    )
    [change] = _gw(spaces).share_saved_objects([_DV], ["team-a", "team-b"])
    assert _names(spaces) == ["get_all", "refs", "update"]
    assert spaces.calls[2][1] == {
        "objects": [_DV], "spaces_to_add": ["team-a", "team-b"], "spaces_to_remove": []}
    assert change.spaces == ("team-a", "team-b", "default")
    assert change.added == ("team-a", "team-b") and change.removed == ()
    assert change.deleted is False and change.error is None
    assert change.spaces_with_matching_origins == ("team-b",)


def test_share_to_every_space_skips_the_existence_check():
    spaces = _Spaces(
        refs=_refs({"type": "index-pattern", "id": "dv1", "spaces": ["default"]}),
        update={"objects": [{"id": "dv1", "type": "index-pattern", "spaces": ["default", "*"]}]},
    )
    [change] = _gw(spaces).share_saved_objects([_DV], ["*"])
    assert _names(spaces) == ["refs", "update"]
    assert change.added == ("*",)


def test_share_refuses_a_space_that_does_not_exist():
    spaces = _Spaces()
    with pytest.raises(KibanaSpaceNotFound, match="space 'ghost' not found"):
        _gw(spaces).share_saved_objects([_DV], ["ghost"])
    assert _names(spaces) == ["get_all"]


def test_share_refuses_a_missing_object_and_a_one_space_type_before_writing():
    missing = _Spaces(refs=_refs({"type": "index-pattern", "id": "nope", "spaces": [],
                                  "inboundReferences": [], "isMissing": True}))
    with pytest.raises(KibanaRejected) as exc:
        _gw(missing, "team-a").share_saved_objects(
            [{"type": "index-pattern", "id": "nope"}], ["team-b"])
    assert "index-pattern/nope" in exc.value.message
    assert "find_saved_objects" in exc.value.message
    single = _Spaces(refs=_refs({"type": "dashboard", "id": "dash-1", "spaces": [],
                                 "inboundReferences": []}))
    with pytest.raises(KibanaRejected) as exc:
        _gw(single).share_saved_objects([_DASH], ["team-a"])
    assert "keeps 'dashboard' objects in one space" in exc.value.message
    assert "copy_saved_objects_to_spaces" in exc.value.message
    assert "update" not in _names(missing) + _names(single)


def test_share_keeps_the_request_order_and_ignores_related_objects_in_the_read():
    # The read may list objects the request did not name (related ones).
    spaces = _Spaces(
        refs=_refs({"type": "index-pattern", "id": "other", "spaces": ["default"]},
                   {"type": "index-pattern", "id": "dv1", "spaces": ["default"]}),
        update={"objects": [{"id": "dv1", "type": "index-pattern",
                             "spaces": ["default", "team-a"]}]},
    )
    changes = _gw(spaces).share_saved_objects([_DV], ["team-a"])
    assert [(c.type, c.id) for c in changes] == [("index-pattern", "dv1")]


def test_a_per_object_error_after_the_write_is_returned_not_raised():
    spaces = _Spaces(
        refs=_refs({"type": "index-pattern", "id": "dv1", "spaces": ["default"]},
                   {"type": "index-pattern", "id": "dv2", "spaces": ["default"]}),
        update={"objects": [
            {"id": "dv1", "type": "index-pattern", "spaces": ["default", "team-a"]},
            {"id": "dv2", "type": "index-pattern", "spaces": [], "error": {
                "statusCode": 409, "error": "Conflict",
                "message": "Saved object [index-pattern/dv2] conflict"}}]},
    )
    first, second = _gw(spaces).share_saved_objects(
        [_DV, {"type": "index-pattern", "id": "dv2"}], ["team-a"])
    assert first.added == ("team-a",) and first.error is None
    assert second.error == "Saved object [index-pattern/dv2] conflict"
    assert second.spaces == ("default",) and second.added == () and second.deleted is False


# --- unshare ------------------------------------------------------------------

def test_unshare_removes_and_reports_what_it_removed():
    spaces = _Spaces(
        refs=_refs({"type": "index-pattern", "id": "dv1", "spaces": ["default", "team-a"]}),
        update={"objects": [{"id": "dv1", "type": "index-pattern", "spaces": ["default"]}]},
    )
    [change] = _gw(spaces).unshare_saved_objects([_DV], ["team-a", "team-b"], False)
    assert _names(spaces) == ["refs", "update"]  # removal needs no existence check
    assert spaces.calls[1][1] == {
        "objects": [_DV], "spaces_to_add": [], "spaces_to_remove": ["team-a", "team-b"]}
    assert change.removed == ("team-a",) and change.deleted is False


def test_unshare_refuses_removing_the_last_space_unless_forced():
    refs = _refs({"type": "index-pattern", "id": "dv1", "spaces": ["team-a"]})
    spaces = _Spaces(refs=refs)
    with pytest.raises(KibanaRejected) as exc:
        _gw(spaces, "team-a").unshare_saved_objects([_DV], ["team-a"], False)
    assert "Kibana deletes an object left in no space" in exc.value.message
    assert "force=True" in exc.value.message
    assert "update" not in _names(spaces)
    forced = _Spaces(refs=refs, update={"objects": [
        {"id": "dv1", "type": "index-pattern", "spaces": []}]})
    [change] = _gw(forced, "team-a").unshare_saved_objects([_DV], ["team-a"], True)
    assert change.deleted is True and change.spaces == () and change.removed == ("team-a",)


def test_unshare_refuses_a_removal_that_removes_nothing():
    spaces = _Spaces(refs=_refs({"type": "index-pattern", "id": "dv1", "spaces": ["default"]}))
    with pytest.raises(KibanaRejected, match="nothing to remove"):
        _gw(spaces).unshare_saved_objects([_DV], ["team-b"], True)
    assert "update" not in _names(spaces)


def test_unshare_keeps_an_object_shared_to_every_space():
    spaces = _Spaces(
        refs=_refs({"type": "index-pattern", "id": "dv1", "spaces": ["default", "*"]}),
        update={"objects": [{"id": "dv1", "type": "index-pattern", "spaces": ["*"]}]},
    )
    [change] = _gw(spaces).unshare_saved_objects([_DV], ["default"], False)
    assert change.spaces == ("*",) and change.deleted is False


# --- the source space a pinned KIBANA_URL names ------------------------------

@pytest.mark.parametrize(("url", "space"), [
    ("http://kb:5601", None),
    ("http://kb:5601/s/team-a", "team-a"),
    ("http://kb:5601/s/team-a/", "team-a"),
    ("http://kb:5601/proxy/s/ops_1", "ops_1"),
    ("http://kb:5601/s/", None),
])
def test_pinned_space_reads_the_base_path(url, space):
    assert pinned_space(url) == space
    assert is_space_pinned(url) is (space is not None)


# --- routing: the copy and share routes run on the source space ----------------

def _recording_root(answers):
    import kibana

    root = kibana.Kibana("http://kb:5601", api_key="x")
    paths: list[tuple[str, str]] = []

    def perform_request(method, path, *, params=None, headers=None, body=None):
        paths.append((method, path))
        return FakeResponse(answers[path.rsplit("/", 1)[1]])

    root.perform_request = perform_request  # type: ignore[method-assign]
    return root, paths


def test_a_scoped_gateway_sends_the_spaces_routes_under_its_space():
    # kibana-py serves `spaces` unscoped on a space-scoped client (probed: a
    # share from a throwaway space read its own data view as missing).
    root, paths = _recording_root({
        "space": _SPACES,
        "_get_shareable_references": {"objects": [
            {"type": "index-pattern", "id": "dv1", "spaces": ["team-a"]}]},
        "_update_objects_spaces": {"objects": [
            {"type": "index-pattern", "id": "dv1", "spaces": ["team-a", "team-b"]}]},
        "_copy_saved_objects": {"team-b": {"success": True, "successCount": 0}},
    })
    gw = KibanaPyGateway(root.space("team-a", validate=False), "team-a")
    gw.share_saved_objects([_DV], ["team-b"])
    gw.copy_saved_objects_to_spaces([_DV], ["team-b"], True, False)
    assert paths == [
        ("GET", "/api/spaces/space"),  # the existence check lists every space
        ("POST", "/s/team-a/api/spaces/_get_shareable_references"),
        ("POST", "/s/team-a/api/spaces/_update_objects_spaces"),
        ("GET", "/api/spaces/space"),
        ("POST", "/s/team-a/api/spaces/_copy_saved_objects"),
    ]


def test_an_unscoped_gateway_sends_them_on_its_base_path():
    root, paths = _recording_root({"_get_shareable_references": {"objects": [
        {"type": "index-pattern", "id": "dv1", "spaces": ["default"]}]}})
    with pytest.raises(KibanaRejected, match="nothing to remove"):
        KibanaPyGateway(root).unshare_saved_objects([_DV], ["team-b"], False)
    assert paths == [("POST", "/api/spaces/_get_shareable_references")]
