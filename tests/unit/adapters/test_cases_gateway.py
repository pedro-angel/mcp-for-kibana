"""Adapter unit tests for the cases methods: raw body -> DTO, the read-modify-
write version handling for update, and list pagination. Shapes from probe C1."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import ConflictError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaRejected, KibanaUnavailable
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

_CASE = {
    "id": "c1", "title": "Inc", "status": "open", "severity": "high",
    "owner": "cases", "tags": ["a"], "totalComment": 2, "version": "v1",
}


def _gw(**client_overrides):
    return KibanaPyGateway(make_fake_client(**client_overrides))


def test_to_case_maps_fields():
    c = _gw(cases=SimpleNamespace(get=lambda case_id: FakeResponse(_CASE))).get_case("c1")
    assert (c.id, c.title, c.status, c.severity, c.owner) == ("c1", "Inc", "open", "high", "cases")
    assert c.tags == ("a",) and c.total_comments == 2


def test_update_case_reads_version_and_unwraps_list_and_omits_none():
    captured = {}

    def update(**kw):
        captured.update(kw)
        return FakeResponse([{**_CASE, "status": "closed", "version": "v2"}])

    cases = SimpleNamespace(get=lambda case_id: FakeResponse(_CASE), update=update)
    c = _gw(cases=cases).update_case("c1", "closed", None, None, None)
    assert captured["version"] == "v1"  # read from the prior get
    assert captured["status"] == "closed"
    assert "severity" not in captured and "tags" not in captured  # None fields omitted
    assert c.status == "closed"  # unwrapped from the LIST response


def test_list_cases_paginates_to_exhaustion():
    pages = {1: {"cases": [{"id": "c1"}], "total": 2}, 2: {"cases": [{"id": "c2"}], "total": 2}}
    seen = []

    def find(**kw):
        seen.append(kw["page"])
        return FakeResponse(pages[kw["page"]])

    cs = _gw(cases=SimpleNamespace(find=find)).list_cases(None)
    assert [c.id for c in cs] == ["c1", "c2"] and seen == [1, 2]


def test_add_comment_returns_updated_case():
    cases = SimpleNamespace(add_comment=lambda **kw: FakeResponse({**_CASE, "totalComment": 3}))
    assert _gw(cases=cases).add_case_comment("c1", "x").total_comments == 3


def test_delete_case_calls_delete_with_ids_list():
    captured = {}

    def delete(**kw):
        captured.update(kw)
        return FakeResponse({})

    assert _gw(cases=SimpleNamespace(delete=delete)).delete_case("c1") is None
    assert captured["ids"] == ["c1"]


def test_update_case_version_conflict_maps_to_rejected_not_unavailable():
    def update(**kw):
        raise ConflictError("version conflict", meta=SimpleNamespace(status=409), body=None)

    cases = SimpleNamespace(get=lambda case_id: FakeResponse(_CASE), update=update)
    with pytest.raises(KibanaRejected):  # a 409 is a rejection, not an outage
        _gw(cases=cases).update_case("c1", "closed", None, None, None)


def test_update_case_empty_response_raises_domain_error():
    cases = SimpleNamespace(get=lambda case_id: FakeResponse(_CASE), update=lambda **kw: FakeResponse([]))
    with pytest.raises(KibanaUnavailable):  # clean domain error, not an IndexError
        _gw(cases=cases).update_case("c1", "closed", None, None, None)
