"""Adapter translation tests for the platform-admin toolbox. Raw bodies mirror
shapes captured from a live stack: spaces
and roles come back as bare arrays / single objects; the upgrade-assistant status
carries non-deterministic per-call message text the converter must drop."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

# Real GET /api/spaces/space element captured live (the default space).
_SPACE = {
    "id": "default",
    "name": "Default",
    "description": "This is your default space!",
    "color": "#00bfb3",
    "disabledFeatures": ["apm", "uptime", "slo"],
    "_reserved": True,
    "solution": "es",
}

# Real GET /api/security/role/{name} shape (trimmed), a reserved system role.
_ROLE = {
    "name": "kibana_system",
    "description": "Grants access necessary for the Kibana system user...",
    "metadata": {"_reserved": True},
    "transient_metadata": {"enabled": True},
    "elasticsearch": {
        "cluster": ["monitor", "manage_index_templates"],
        "indices": [
            {"names": [".kibana*"], "privileges": ["all"], "allow_restricted_indices": True},
        ],
        "run_as": ["other_user"],
    },
    "kibana": [
        {"base": ["all"], "feature": {"dashboard": ["read"], "discover": ["all"]}, "spaces": ["*"]},
    ],
}


def _gw(**namespaces):
    return KibanaPyGateway(make_fake_client(**namespaces))


def test_list_spaces_bare_array():
    gw = _gw(spaces=SimpleNamespace(get_all=lambda: FakeResponse([_SPACE])))
    [s] = gw.list_spaces()
    assert s.id == "default"
    assert s.name == "Default"
    assert s.description == "This is your default space!"
    assert s.solution == "es"
    assert s.disabled_features == ("apm", "uptime", "slo")
    assert s.reserved is True  # _reserved -> reserved


def test_get_space_single_object():
    gw = _gw(spaces=SimpleNamespace(get=lambda *, id: FakeResponse(_SPACE)))
    s = gw.get_space("default")
    assert s.id == "default"
    assert s.reserved is True


def test_get_space_missing_maps_to_domain_error():
    def raise_nf(*, id):
        raise NotFoundError("Not Found", meta=SimpleNamespace(status=404), body=None)

    gw = _gw(spaces=SimpleNamespace(get=raise_nf))
    with pytest.raises(KibanaNotFound):
        gw.get_space("no-such-space")


def test_space_optional_fields_default_to_none():
    # A non-default space may omit description/solution and _reserved.
    body = {"id": "marketing", "name": "Marketing", "disabledFeatures": []}
    gw = _gw(spaces=SimpleNamespace(get=lambda *, id: FakeResponse(body)))
    s = gw.get_space("marketing")
    assert s.description is None
    assert s.solution is None
    assert s.disabled_features == ()
    assert s.reserved is False


def test_list_roles_summarizes_privileges():
    gw = _gw(security=SimpleNamespace(get_all_roles=lambda: FakeResponse([_ROLE])))
    [r] = gw.list_roles()
    assert r.name == "kibana_system"
    assert r.reserved is True  # metadata._reserved -> reserved
    assert r.cluster_privileges == ("monitor", "manage_index_templates")
    assert r.run_as == ("other_user",)
    # index privileges keep names + privileges only (field_security/etc. dropped).
    assert len(r.index_privileges) == 1
    assert r.index_privileges[0].names == (".kibana*",)
    assert r.index_privileges[0].privileges == ("all",)
    # kibana privileges: base kept; feature dict summarized to SORTED feature names.
    assert len(r.kibana_privileges) == 1
    assert r.kibana_privileges[0].base == ("all",)
    assert r.kibana_privileges[0].features == ("dashboard", "discover")
    assert r.kibana_privileges[0].spaces == ("*",)


def test_get_role_missing_maps_to_domain_error():
    def raise_nf(*, name):
        raise NotFoundError("Not Found", meta=SimpleNamespace(status=404), body=None)

    gw = _gw(security=SimpleNamespace(get_role=raise_nf))
    with pytest.raises(KibanaNotFound):
        gw.get_role("no-such-role")


def test_role_non_dict_elasticsearch_degrades_to_empty():
    # A shape drift where elasticsearch/metadata come back the WRONG TYPE (a list /
    # a string, not just absent) must degrade to empty via the isinstance guards,
    # never raise. kibana as a non-list is also filtered to empty.
    body = {"name": "custom", "description": None, "elasticsearch": [], "metadata": "x", "kibana": {}}
    gw = _gw(security=SimpleNamespace(get_role=lambda *, name: FakeResponse(body)))
    r = gw.get_role("custom")
    assert r.reserved is False  # metadata "x" -> not a dict -> reserved False
    assert r.cluster_privileges == ()
    assert r.index_privileges == ()
    assert r.kibana_privileges == ()


def test_get_upgrade_status_drops_nondeterministic_message():
    # message[] embeds a live call-count + "last call was on <timestamp>"; the
    # converter must keep title/level/type only so contract tests never flake.
    body = {
        "readyForUpgrade": True,
        "details": "All deprecation warnings have been resolved.",
        "recentEsDeprecationLogs": {"count": 3, "logs": [{"message": "x"}]},
        "kibanaApiDeprecations": [
            {
                "apiId": "unversioned|delete|/api/saved_objects/{type}/{id}",
                "title": "The DELETE route is deprecated",
                "level": "warning",
                "message": ["called 1 times. last call was on Monday..."],
                "correctiveActions": {"manualSteps": ["..."]},
                "deprecationType": "api",
            }
        ],
    }
    gw = _gw(upgrade_assistant=SimpleNamespace(status=lambda: FakeResponse(body)))
    u = gw.get_upgrade_status()
    assert u.ready_for_upgrade is True
    assert u.details == "All deprecation warnings have been resolved."
    assert u.es_deprecation_count == 3  # the count, never the log bodies
    assert len(u.api_deprecations) == 1
    dep = u.api_deprecations[0]
    assert dep.title == "The DELETE route is deprecated"
    assert dep.level == "warning"
    assert dep.type == "api"
    # No message / correctiveActions leaked onto the DTO.
    assert not hasattr(dep, "message")


def test_get_upgrade_status_non_dict_es_logs_degrades():
    # recentEsDeprecationLogs the WRONG TYPE (a list, not just null) still degrades
    # to count 0 via the isinstance guard; a non-list kibanaApiDeprecations yields ().
    body = {"readyForUpgrade": False, "recentEsDeprecationLogs": [], "kibanaApiDeprecations": None}
    gw = _gw(upgrade_assistant=SimpleNamespace(status=lambda: FakeResponse(body)))
    u = gw.get_upgrade_status()
    assert u.ready_for_upgrade is False
    assert u.details is None
    assert u.es_deprecation_count == 0
    assert u.api_deprecations == ()


# --- platform-admin write/destructive tier (#57): spaces + roles CRUD ---


def test_create_space_maps_body():
    gw = _gw(spaces=SimpleNamespace(create=lambda **kw: FakeResponse(
        {"id": kw["id"], "name": kw["name"], "description": kw.get("description"),
         "solution": kw.get("solution"), "disabledFeatures": kw.get("disabled_features") or []})))
    out = gw.create_space("mk", "Marketing", "d", None, None, ["uptime"], "es")
    assert out.id == "mk" and out.disabled_features == ("uptime",)


def test_update_space_rmw_resends_current_disabled_features_camelcase():
    # THE RMW AUTHORITY: assert the update() CALL re-sends the current disabledFeatures
    # (read from the camelCase GET key) when omitted, and provided fields override.
    cur = {"name": "orig", "description": "d", "color": "#111", "initials": "OG",
           "imageUrl": "data:x", "disabledFeatures": ["uptime"], "solution": "es", "_reserved": False}
    sent = {}
    gw = _gw(spaces=SimpleNamespace(
        get=lambda *, id: FakeResponse(dict(cur)),
        update=lambda **kw: sent.update(kw) or FakeResponse({})))
    gw.update_space("mk", None, "new", None, None, None, None)
    assert sent["name"] == "orig"                      # required, from current
    assert sent["description"] == "new"                # provided override
    assert sent["disabled_features"] == ["uptime"]     # re-sent from camelCase GET key
    assert sent["image_url"] == "data:x"               # camelCase imageUrl preserved


def test_update_space_missing_raises_not_found():
    def boom(*, id):
        raise NotFoundError("nope", meta=SimpleNamespace(status=404), body=None)
    gw = _gw(spaces=SimpleNamespace(get=boom))
    with pytest.raises(KibanaNotFound):
        gw.update_space("gone", "n", None, None, None, None, None)


def test_create_or_update_role_builds_es_kibana_and_rereads():
    sent = {}
    role_body = {"name": "r", "elasticsearch": {"cluster": ["monitor"], "indices": []},
                 "kibana": [{"base": ["read"], "spaces": ["*"]}], "metadata": {}}
    gw = _gw(security=SimpleNamespace(
        create_or_update_role=lambda **kw: sent.update(kw) or FakeResponse(None),
        get_role=lambda *, name: FakeResponse(role_body)))
    out = gw.create_or_update_role("r", ["monitor"], [{"names": ["logs-*"], "privileges": ["read"]}],
                                   ["read"], ["*"], None, True)
    assert sent["elasticsearch"] == {"cluster": ["monitor"], "indices": [{"names": ["logs-*"], "privileges": ["read"]}]}
    assert sent["kibana"] == [{"base": ["read"], "spaces": ["*"]}]
    assert sent["create_only"] is True
    assert out.name == "r"  # re-read via get_role (PUT is 204)


def test_create_or_update_role_no_kibana_when_base_empty():
    sent = {}
    gw = _gw(security=SimpleNamespace(
        create_or_update_role=lambda **kw: sent.update(kw) or FakeResponse(None),
        get_role=lambda *, name: FakeResponse({"name": "r", "elasticsearch": {}, "metadata": {}})))
    gw.create_or_update_role("r", ["monitor"], [], [], None, None, False)
    assert sent["kibana"] is None  # no kibana block when kibana_base empty


def test_create_or_update_role_refuses_reserved():
    # A full-replace targeting a reserved role must be refused client-side BEFORE the
    # PUT (no create_or_update_role stub -> would AttributeError if the guard missed).
    gw = _gw(security=SimpleNamespace(
        get_role=lambda *, name: FakeResponse({"name": name, "metadata": {"_reserved": True}})))
    with pytest.raises(KibanaRejected):
        gw.create_or_update_role("kibana_system", ["monitor"], [], None, None, None, False)


def test_delete_space_refuses_default():
    # The "default" fast-path refuses BEFORE any GET (no spaces.get stub needed —
    # supplying one would be dead, since the guard short-circuits first).
    gw = _gw(spaces=SimpleNamespace())
    with pytest.raises(KibanaRejected):
        gw.delete_space("default", force=False)


def test_delete_space_refuses_reserved():
    gw = _gw(spaces=SimpleNamespace(get=lambda *, id: FakeResponse({"id": "sys", "_reserved": True})))
    with pytest.raises(KibanaRejected):
        gw.delete_space("sys", force=False)


def test_delete_space_requires_force():
    gw = _gw(spaces=SimpleNamespace(get=lambda *, id: FakeResponse({"id": "mk", "_reserved": False})))
    with pytest.raises(KibanaRejected):
        gw.delete_space("mk", force=False)  # non-reserved but no force -> refuse the whole-space wipe


def test_delete_space_force_deletes():
    deleted = {}
    gw = _gw(spaces=SimpleNamespace(
        get=lambda *, id: FakeResponse({"id": "mk", "_reserved": False}),
        delete=lambda *, id: deleted.update(id=id) or FakeResponse({})))
    gw.delete_space("mk", force=True)
    assert deleted == {"id": "mk"}


def test_delete_role_refuses_reserved():
    gw = _gw(security=SimpleNamespace(
        get_role=lambda *, name: FakeResponse({"name": name, "metadata": {"_reserved": True}})))
    with pytest.raises(KibanaRejected):
        gw.delete_role("kibana_system")


def test_delete_role_deletes_custom():
    deleted = {}
    gw = _gw(security=SimpleNamespace(
        get_role=lambda *, name: FakeResponse({"name": name, "metadata": {}}),
        delete_role=lambda *, name: deleted.update(name=name) or FakeResponse({})))
    gw.delete_role("custom")
    assert deleted == {"name": "custom"}
