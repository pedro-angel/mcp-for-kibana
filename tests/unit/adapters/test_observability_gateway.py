"""Adapter translation tests for the observability toolbox. Raw bodies mirror
shapes captured from a live stack; the
monitor body is a real capture from a seeded-then-torn-down HTTP monitor, so the
SyntheticMonitor converter is exercised against a real shape, not docstring prose."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import NotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound
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
