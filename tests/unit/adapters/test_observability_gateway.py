"""Adapter translation tests for the observability toolbox. Raw bodies mirror
shapes captured from a live stack; the
monitor body is a real capture from a seeded-then-torn-down HTTP monitor, so the
SyntheticMonitor converter is exercised against a real shape, not docstring prose."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import BadRequestError, NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected
from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

# Real GET /api/synthetics/monitors/{id} body captured from a seeded monitor.
_MONITOR = {
    "id": "0f195910-33cf-48f7-99df-1a1645e66c23",
    "config_id": "0f195910-33cf-48f7-99df-1a1645e66c23",
    "type": "http",
    "name": "obs-probe-monitor",
    "enabled": True,
    "tags": ["probe"],
    "schedule": {"number": "10", "unit": "m"},
    "locations": [
        {
            "id": "1406e687-c175-467c-bd4c-c53f385bf84d",
            "label": "obs-probe-loc",
            "isServiceManaged": False,
            "agentPolicyId": "383ce526-6efd-4b24-a643-743add88fa8d",
        }
    ],
    "url": "https://example.com",
}


def _gw(**namespaces):
    return KibanaPyGateway(make_fake_client(**namespaces))


def test_list_synthetic_monitors_maps_real_body():
    gw = _gw(
        synthetics=SimpleNamespace(
            get_monitors=lambda **kw: FakeResponse(
                {"monitors": [_MONITOR], "total": 1, "absoluteTotal": 1, "page": 1, "perPage": 100}
            )
        )
    )
    [m] = gw.list_synthetic_monitors()
    assert m.id == "0f195910-33cf-48f7-99df-1a1645e66c23"  # from config_id
    assert m.name == "obs-probe-monitor"
    assert m.type == "http"
    assert m.enabled is True
    assert m.tags == ("probe",)
    assert m.locations == ("obs-probe-loc",)  # location object -> its label
    assert m.schedule == "10m"  # {"number":"10","unit":"m"} -> "10m"
    assert m.target == "https://example.com"  # http -> url


def test_get_synthetic_monitor_maps_real_body():
    gw = _gw(synthetics=SimpleNamespace(get_monitor=lambda **kw: FakeResponse(_MONITOR)))
    m = gw.get_synthetic_monitor("0f195910-33cf-48f7-99df-1a1645e66c23")
    assert m.id == "0f195910-33cf-48f7-99df-1a1645e66c23"
    assert m.schedule == "10m"
    assert m.locations == ("obs-probe-loc",)


def test_browser_monitor_has_no_target():
    browser = {k: v for k, v in _MONITOR.items() if k != "url"}
    browser["type"] = "browser"  # browser monitors carry neither url nor host
    gw = _gw(synthetics=SimpleNamespace(get_monitor=lambda **kw: FakeResponse(browser)))
    m = gw.get_synthetic_monitor("x")
    assert m.type == "browser"
    assert m.target is None


def test_list_synthetic_monitors_paginates_to_exhaustion():
    # Fabricated 2-page envelope (the live stack has too few monitors to
    # paginate). absoluteTotal (999) is deliberately != total (150) so that a
    # regression to terminating on `absoluteTotal` would over-collect and fail
    # this test — protecting the spec's "walk via `total`" decision.
    def paged(**kw):
        page = kw.get("page", 1)
        start = (page - 1) * 100
        count = 100 if page == 1 else 50
        items = [{**_MONITOR, "config_id": f"m{start + i}"} for i in range(count)]
        return FakeResponse({"monitors": items, "total": 150, "absoluteTotal": 999})

    gw = _gw(synthetics=SimpleNamespace(get_monitors=paged))
    monitors = gw.list_synthetic_monitors()
    assert len(monitors) == 150  # both pages walked, terminates on `total` (not absoluteTotal)
    assert monitors[0].id == "m0"
    assert monitors[-1].id == "m149"


def test_list_synthetic_params_bare_array():
    gw = _gw(
        synthetics=SimpleNamespace(
            get_params=lambda **kw: FakeResponse(
                [{"id": "p1", "key": "token", "description": "d", "tags": ["t"], "namespaces": ["default"]}]
            )
        )
    )
    [p] = gw.list_synthetic_params()
    assert (p.id, p.key, p.description, p.tags) == ("p1", "token", "d", ("t",))


def test_list_synthetic_private_locations_bare_array():
    gw = _gw(
        synthetics=SimpleNamespace(
            get_private_locations=lambda **kw: FakeResponse(
                [{"id": "l1", "label": "dc1", "agentPolicyId": "ap1", "isInvalid": True, "tags": []}]
            )
        )
    )
    [loc] = gw.list_synthetic_private_locations()
    assert loc.id == "l1"
    assert loc.label == "dc1"
    assert loc.agent_policy_id == "ap1"
    assert loc.is_invalid is True


def test_get_uptime_settings_maps_nested_email():
    gw = _gw(
        uptime=SimpleNamespace(
            get_settings=lambda **kw: FakeResponse(
                {
                    "heartbeatIndices": "heartbeat-*",
                    "certExpirationThreshold": 30,
                    "certAgeThreshold": 730,
                    "defaultConnectors": ["c1"],
                    "defaultEmail": {"to": ["a@b.com"], "cc": [], "bcc": []},
                }
            )
        )
    )
    s = gw.get_uptime_settings()
    assert s.heartbeat_indices == "heartbeat-*"
    assert s.cert_expiration_threshold == 30
    assert s.cert_age_threshold == 730
    assert s.default_connectors == ("c1",)
    assert s.default_email.to == ("a@b.com",)
    assert s.default_email.cc == ()


def test_list_apm_agent_configs_maps_service_and_settings():
    gw = _gw(
        apm=SimpleNamespace(
            get_agent_configurations=lambda **kw: FakeResponse(
                {
                    "configurations": [
                        {
                            "service": {"name": "checkout", "environment": "production"},
                            "settings": {"transaction_sample_rate": "0.5"},
                            "applied_by_agent": True,
                            "etag": "e1",
                        }
                    ]
                }
            )
        )
    )
    [c] = gw.list_apm_agent_configs()
    assert c.service_name == "checkout"
    assert c.service_environment == "production"
    assert c.settings == {"transaction_sample_rate": "0.5"}
    assert c.applied_by_agent is True
    assert c.etag == "e1"


def test_get_apm_agent_config_notfound_maps_to_domain_error():
    def raise_nf(**kw):
        raise NotFoundError("Not Found", meta=SimpleNamespace(status=404), body=None)

    gw = _gw(apm=SimpleNamespace(get_agent_configuration=raise_nf))
    with pytest.raises(KibanaNotFound):
        gw.get_apm_agent_config(None, None)


def test_list_apm_environments_surfaces_all_option_sentinel():
    gw = _gw(
        apm=SimpleNamespace(
            get_environments=lambda **kw: FakeResponse(
                {"environments": [{"name": "ALL_OPTION_VALUE", "alreadyConfigured": False}]}
            )
        )
    )
    [e] = gw.list_apm_environments("checkout")
    assert e.name == "ALL_OPTION_VALUE"
    assert e.already_configured is False


def test_list_apm_sourcemaps_maps_and_paginates():
    def paged(**kw):
        page = kw.get("page", 1)
        start = (page - 1) * 100
        count = 100 if page == 1 else 20
        arts = [{"identifier": f"s{start + i}", "created": "2026"} for i in range(count)]
        return FakeResponse({"artifacts": arts, "total": 120})

    gw = _gw(apm=SimpleNamespace(get_sourcemaps=paged))
    maps = gw.list_apm_sourcemaps()
    assert len(maps) == 120
    assert maps[0].identifier == "s0"


def test_apm_sourcemap_created_coerced_to_str():
    gw = _gw(
        apm=SimpleNamespace(
            get_sourcemaps=lambda **kw: FakeResponse(
                {"artifacts": [{"identifier": "x", "created": 1720000000000}], "total": 1}
            )
        )
    )
    [m] = gw.list_apm_sourcemaps()
    assert m.created == "1720000000000"  # coerced from a non-str (int) value


def test_search_apm_annotations_maps_timestamp_field():
    gw = _gw(
        apm=SimpleNamespace(
            search_annotations=lambda **kw: FakeResponse(
                {
                    "annotations": [
                        {"type": "deployment", "id": "a1", "@timestamp": "2026-07-12T00:00:00Z", "text": "v2"}
                    ]
                }
            )
        )
    )
    [a] = gw.search_apm_annotations("checkout", "s", "e", "ENVIRONMENT_ALL")
    assert a.id == "a1"
    assert a.timestamp == "2026-07-12T00:00:00Z"  # @timestamp -> timestamp
    assert a.text == "v2"
    assert a.type == "deployment"


# --- Synthetics write tier (D46): bodies captured live on 9.5.4 -------------


class _Recorder:
    """A synthetics namespace that records each call and answers from a map."""

    def __init__(self, **answers):
        self.calls = []
        self._answers = answers

    def __getattr__(self, name):
        def call(**kw):
            self.calls.append((name, kw))
            answer = self._answers[name]
            if isinstance(answer, BaseException):
                raise answer
            return FakeResponse(answer(kw) if callable(answer) else answer)
        return call


def _bad(message):
    return BadRequestError(message, meta=SimpleNamespace(status=400), body=None)


def _nf(message):
    return NotFoundError(message, meta=SimpleNamespace(status=404), body=None)


def _syn_gw(rec):
    return KibanaPyGateway(make_fake_client(synthetics=rec))


def test_create_synthetic_monitor_sends_kibanas_schedule_and_maps_the_response():
    rec = _Recorder(create_monitor={**_MONITOR, "schedule": {"number": "30", "unit": "s"}})
    m = _syn_gw(rec).create_synthetic_monitor(
        type="http", name="obs-probe-monitor", private_locations=["loc-1"],
        url="https://example.com", host=None, inline_script=None, schedule="30s",
        enabled=True, tags=["probe"],
    )
    assert rec.calls == [("create_monitor", {
        "type": "http", "name": "obs-probe-monitor", "private_locations": ["loc-1"],
        "url": "https://example.com", "schedule": {"number": "30", "unit": "s"},
        "enabled": True, "tags": ["probe"],
    })]
    assert m.id == _MONITOR["config_id"] and m.schedule == "30s"


def test_create_synthetic_monitor_omits_what_was_not_given():
    rec = _Recorder(create_monitor=_MONITOR)
    _syn_gw(rec).create_synthetic_monitor(
        type="tcp", name="db", private_locations=["dc1"], url=None, host="db:5432",
        inline_script=None, schedule=None, enabled=False, tags=None,
    )
    assert rec.calls[0][1] == {
        "type": "tcp", "name": "db", "private_locations": ["dc1"], "host": "db:5432",
        "enabled": False,
    }


def test_update_synthetic_monitor_sends_only_the_changes():
    rec = _Recorder(update_monitor={**_MONITOR, "enabled": False})
    m = _syn_gw(rec).update_synthetic_monitor("m-1", enabled=False, schedule="15m")
    assert rec.calls == [("update_monitor", {
        "id": "m-1", "enabled": False, "schedule": {"number": "15", "unit": "m"},
    })]
    assert m.enabled is False


def test_monitor_schedule_number_goes_out_canonical():
    # Kibana compares the number as text: {"number": "05"} is a 400 "Invalid
    # schedule 05 minutes" (probed on 9.5.4), so leading zeros never go out.
    rec = _Recorder(create_monitor=_MONITOR, update_monitor=_MONITOR)
    _syn_gw(rec).create_synthetic_monitor(
        type="http", name="m", private_locations=["loc-1"], url="https://example.com",
        host=None, inline_script=None, schedule="05m", enabled=True, tags=None,
    )
    _syn_gw(rec).update_synthetic_monitor("m-1", schedule="030s")
    assert rec.calls[0][1]["schedule"] == {"number": "5", "unit": "m"}
    assert rec.calls[1][1]["schedule"] == {"number": "30", "unit": "s"}


def test_test_synthetic_monitor_returns_the_run_id():
    rec = _Recorder(test_monitor={"testRunId": "2962b617-4d24-4d2b-8923-1a497e6d4898"})
    assert _syn_gw(rec).test_synthetic_monitor("m-1") == "2962b617-4d24-4d2b-8923-1a497e6d4898"
    assert rec.calls == [("test_monitor", {"monitor_id": "m-1"})]


def test_delete_synthetic_monitor_answers_200_on_a_miss():
    # Live: a second DELETE is a 200 [{"deleted": false, "error": "Monitor id ... not found!"}].
    ok = _Recorder(delete_monitor=[{"id": "m-1", "deleted": True}])
    _syn_gw(ok).delete_synthetic_monitor("m-1")
    miss = _Recorder(delete_monitor=[
        {"id": "m-1", "deleted": False, "error": "Monitor id m-1 not found!"}])
    with pytest.raises(KibanaNotFound, match=(
        r"^synthetic monitor 'm-1' not found — monitor_id is the id that create_synthetic_monitor "
        r"or list_synthetic_monitors returns, not the monitor's name; call "
        r"list_synthetic_monitors to find it$"
    )):
        _syn_gw(miss).delete_synthetic_monitor("m-1")


_MON_CALLS = [
    lambda gw, i: gw.get_synthetic_monitor(i),
    lambda gw, i: gw.update_synthetic_monitor(i, enabled=True),
    lambda gw, i: gw.test_synthetic_monitor(i),
    lambda gw, i: gw.delete_synthetic_monitor(i),
]


@pytest.mark.parametrize("call", _MON_CALLS)
def test_unknown_monitor_id_says_it_is_not_the_name(call):
    rec = _Recorder(**{n: _nf("Monitor id nope not found!") for n in (
        "get_monitor", "update_monitor", "test_monitor", "delete_monitor")})
    with pytest.raises(KibanaNotFound, match=r"^synthetic monitor 'nope' not found — monitor_id"):
        call(_syn_gw(rec), "nope")


@pytest.mark.parametrize("call", _MON_CALLS)
def test_empty_monitor_id_is_refused_before_kibana(call):
    rec = _Recorder()
    with pytest.raises(KibanaRejected, match=r"^monitor_id is empty — pass the id that "
                                             r"create_synthetic_monitor or list_synthetic_monitors"):
        call(_syn_gw(rec), " ")
    assert rec.calls == []


# The create response observed live: no value. The update response echoes the
# PREVIOUS value (Kibana 9.5.4, also kibana-py's note for 9.4.3) — never mapped.
_PARAM_CREATED = {"id": "7c2e", "description": "d", "key": "probe_key",
                  "namespaces": ["default"], "tags": ["probe"]}
_PARAM_UPDATED = {**_PARAM_CREATED, "value": "the-old-secret"}


def test_create_synthetic_param_maps_without_a_value():
    rec = _Recorder(create_param=_PARAM_CREATED)
    p = _syn_gw(rec).create_synthetic_param("probe_key", "s3cret", "d", ["probe"])
    assert rec.calls == [("create_param", {
        "key": "probe_key", "value": "s3cret", "description": "d", "tags": ["probe"]})]
    assert (p.id, p.key, p.description, p.tags) == ("7c2e", "probe_key", "d", ("probe",))
    assert not hasattr(p, "value")


def test_update_synthetic_param_drops_the_echoed_value():
    rec = _Recorder(update_param=_PARAM_UPDATED)
    p = _syn_gw(rec).update_synthetic_param("7c2e", value="new-secret")
    assert rec.calls == [("update_param", {"id": "7c2e", "value": "new-secret"})]
    assert "the-old-secret" not in repr(p) and "new-secret" not in repr(p)


@pytest.mark.parametrize("call", [
    lambda gw: gw.create_synthetic_param("k", "s3cret-xyz", None, None),
    lambda gw: gw.update_synthetic_param("7c2e", value="s3cret-xyz"),
])
def test_a_rejection_never_echoes_the_value(call):
    boom = _bad("[request body.value]: 's3cret-xyz' is not allowed")
    rec = _Recorder(create_param=boom, update_param=boom)
    with pytest.raises(KibanaRejected) as exc:
        call(_syn_gw(rec))
    assert "s3cret-xyz" not in exc.value.message + exc.value.detail
    assert "[redacted]" in exc.value.detail


def test_delete_synthetic_param_answers_200_on_a_miss():
    # Live: a missing id is a 200 [{"id": ..., "deleted": false}] with no error text.
    _syn_gw(_Recorder(delete_param=[{"id": "p1", "deleted": True}])).delete_synthetic_param("p1")
    with pytest.raises(KibanaNotFound, match=(
        r"^synthetic param 'p1' not found — param_id is the id that create_synthetic_param or "
        r"list_synthetic_params returns, not the param's key; call list_synthetic_params"
    )):
        _syn_gw(_Recorder(delete_param=[{"id": "p1", "deleted": False}])).delete_synthetic_param("p1")


def test_unknown_param_id_on_update_names_the_list_tool():
    rec = _Recorder(update_param=_nf("Param not found"))
    with pytest.raises(KibanaNotFound, match=r"^synthetic param 'nope' not found — param_id"):
        _syn_gw(rec).update_synthetic_param("nope", description="x")


_LOC = {"label": "probe-loc", "id": "bbce", "agentPolicyId": "9d19", "isServiceManaged": False,
        "isInvalid": False, "tags": ["probe"], "spaces": ["default"]}


def test_create_synthetic_private_location_maps_the_response():
    rec = _Recorder(create_private_location=_LOC)
    loc = _syn_gw(rec).create_synthetic_private_location("probe-loc", "9d19", ["probe"])
    assert rec.calls == [("create_private_location", {
        "label": "probe-loc", "agent_policy_id": "9d19", "tags": ["probe"]})]
    assert (loc.id, loc.label, loc.agent_policy_id) == ("bbce", "probe-loc", "9d19")


def test_create_synthetic_private_location_rejection_names_list_agent_policies():
    rec = _Recorder(create_private_location=_bad(
        "Agent policy with id nope not found in space default, please use an agent policy "
        "available in current space."))
    with pytest.raises(KibanaRejected) as exc:
        _syn_gw(rec).create_synthetic_private_location("x", "nope", None)
    assert "Agent policy with id nope not found" in exc.value.detail
    assert exc.value.detail.endswith(
        "— agent_policy_id is the id of a Fleet agent policy that no other private location "
        "uses: list_agent_policies shows them, create_agent_policy creates one (fleet toolbox)")


def test_update_synthetic_private_location_sends_the_label():
    rec = _Recorder(update_private_location={**_LOC, "label": "renamed"})
    loc = _syn_gw(rec).update_synthetic_private_location("bbce", "renamed")
    assert rec.calls == [("update_private_location", {"id": "bbce", "label": "renamed"})]
    assert loc.label == "renamed"


def test_delete_synthetic_private_location_maps_kibanas_400s():
    gone = _bad("Private location with id bbce does not exist.")
    with pytest.raises(KibanaNotFound, match=(
        r"^private location 'bbce' not found — location_id is the id that "
        r"create_synthetic_private_location or list_synthetic_private_locations returns, not the "
        r"location's label"
    )):
        _syn_gw(_Recorder(delete_private_location=gone)).delete_synthetic_private_location("bbce")
    used = _bad("Private location with id bbce cannot be deleted because it is used by 1 "
                "monitor(s).")
    with pytest.raises(KibanaRejected) as exc:
        _syn_gw(_Recorder(delete_private_location=used)).delete_synthetic_private_location("bbce")
    assert "is used by 1 monitor(s)" in exc.value.detail
    assert exc.value.detail.endswith(
        "— move those monitors to another private location with update_synthetic_monitor "
        "(private_locations) or delete them with delete_synthetic_monitor first; "
        "list_synthetic_monitors shows each monitor's locations")
    _syn_gw(_Recorder(delete_private_location={})).delete_synthetic_private_location("bbce")


def test_unknown_location_id_on_update_names_the_list_tool():
    rec = _Recorder(update_private_location=_nf("Private location with id nope does not exist."))
    with pytest.raises(KibanaNotFound, match=r"^private location 'nope' not found — location_id"):
        _syn_gw(rec).update_synthetic_private_location("nope", "x")


# --- APM agent configuration writes (D47): bodies captured live on 9.5.4 -----

# GET /api/apm/settings/agent-configuration/view?name=p-ag on a config made with
# agent_name "java" (the key is absent when none was sent).
_APM_VIEW = {"id": "m7PF", "agent_name": "java", "service": {"name": "p-ag"},
             "settings": {"capture_body": "all"}, "@timestamp": 1792893324964,
             "applied_by_agent": False, "etag": "47d8"}
_APM_DELETED = {"_index": ".apm-agent-configuration", "_id": "m7PF", "result": "deleted"}


def _apm_gw(rec):
    return KibanaPyGateway(make_fake_client(apm=rec))


def test_apm_agent_config_maps_agent_name():
    [c] = _gw(apm=SimpleNamespace(get_agent_configurations=lambda **kw: FakeResponse(
        {"configurations": [_APM_VIEW]}))).list_apm_agent_configs()
    assert c.agent_name == "java" and c.service_environment is None
    plain = {k: v for k, v in _APM_VIEW.items() if k != "agent_name"}
    got = _gw(apm=SimpleNamespace(
        get_agent_configuration=lambda **kw: FakeResponse(plain))).get_apm_agent_config("p-ag", None)
    assert got.agent_name is None


def test_upsert_apm_agent_config_puts_then_reads_back():
    # The PUT answers {} (probed), so the stored config is read back.
    rec = _Recorder(create_or_update_agent_configuration={}, get_agent_configuration=_APM_VIEW)
    c = _apm_gw(rec).upsert_apm_agent_config(
        "p-ag", None, {"capture_body": "all"}, agent_name="java", overwrite=True)
    assert rec.calls == [
        ("create_or_update_agent_configuration", {
            "service_name": "p-ag", "settings": {"capture_body": "all"}, "agent_name": "java",
            "overwrite": True}),
        ("get_agent_configuration", {"name": "p-ag"}),
    ]
    assert c.settings == {"capture_body": "all"} and c.etag == "47d8"


def test_upsert_apm_agent_config_omits_what_was_not_given():
    rec = _Recorder(create_or_update_agent_configuration={}, get_agent_configuration=_APM_VIEW)
    _apm_gw(rec).upsert_apm_agent_config(
        None, "prod", {"log_level": "debug"}, agent_name=None, overwrite=False)
    assert rec.calls[0] == ("create_or_update_agent_configuration", {
        "service_environment": "prod", "settings": {"log_level": "debug"}})
    assert rec.calls[1] == ("get_agent_configuration", {"environment": "prod"})


def test_upsert_apm_agent_config_names_the_settings_on_a_rejected_value():
    # Kibana's message can omit the key: "Failed to validate: \n  Must be a
    # number between 0.0000 and 1" for transaction_sample_rate "7" (probed).
    rec = _Recorder(create_or_update_agent_configuration=_bad(
        "Failed to validate: \n  Must be a number between 0.0000 and 1"))
    with pytest.raises(KibanaRejected) as exc:
        _apm_gw(rec).upsert_apm_agent_config(
            "cart", None, {"transaction_sample_rate": "7"}, agent_name=None, overwrite=False)
    assert "Must be a number between 0.0000 and 1" in exc.value.detail
    assert exc.value.detail.endswith(
        "— settings sent: {'transaction_sample_rate': '7'}; each value is a string in the form "
        "the setting takes (a number such as '0.5', 'true' or 'false', a duration such as "
        "'10s', or one of the setting's words)")


def test_upsert_apm_agent_config_race_names_the_retry():
    # A config created between the tool's read and its write: Kibana's 400 tells
    # the caller to use ?overwrite=true, which a tool caller cannot pass.
    rec = _Recorder(create_or_update_agent_configuration=_bad(
        'A configuration already exists for "cart/prod. Use ?overwrite=true to overwrite the '
        'existing configuration.'))
    with pytest.raises(KibanaRejected) as exc:
        _apm_gw(rec).upsert_apm_agent_config(
            "cart", "prod", {"recording": "true"}, agent_name=None, overwrite=False)
    assert exc.value.message == "Kibana refused to replace the APM agent configuration"
    assert exc.value.detail == (
        "a configuration for service 'cart', environment 'prod' was created after this call "
        "read none — call upsert_apm_agent_config again: it then replaces that one and returns "
        "its settings as previous_settings")


def test_delete_apm_agent_config_sends_the_service():
    rec = _Recorder(delete_agent_configuration=_APM_DELETED)
    _apm_gw(rec).delete_apm_agent_config("p-ag", None)
    assert rec.calls == [("delete_agent_configuration", {"service_name": "p-ag"})]


_APM_MISS = (
    "no APM agent configuration for all services, environment 'prod' — service_name and "
    "environment must match a configuration exactly (an omitted one means all); call "
    "list_apm_agent_configs to see them"
)


@pytest.mark.parametrize("call", [
    lambda gw: gw.get_apm_agent_config(None, "prod"),
    lambda gw: gw.delete_apm_agent_config(None, "prod"),
])
def test_apm_config_miss_names_the_list_tool(call):
    # Kibana's own 404 says only "Not Found" (probed on view and delete).
    rec = _Recorder(get_agent_configuration=_nf("Not Found"),
                    delete_agent_configuration=_nf("Not Found"))
    with pytest.raises(KibanaNotFound) as exc:
        call(_apm_gw(rec))
    assert exc.value.message == _APM_MISS
