"""Adapter translation for the platform-health gateway methods: raw live-shaped
Kibana status/task_manager bodies -> concise domain models. Raw shapes are the
ones a live stack actually returns, captured rather than paraphrased from docs."""

from types import SimpleNamespace

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway

from tests.unit.adapters.test_kibana_gateway import FakeResponse, make_fake_client

_STATUS_BODY = {
    "name": "kibana",
    "uuid": "abc",
    "version": {"number": "9.4.3", "build_hash": "x"},
    "status": {
        "overall": {"level": "available", "summary": "All services and plugins are available"},
        "core": {
            "elasticsearch": {"level": "available", "summary": "Elasticsearch is available"},
            "savedObjects": {"level": "degraded", "summary": "SO migrations pending"},
        },
        "plugins": {
            "fleet": {"level": "available", "summary": "Fleet is available"},
            "reporting": {"level": "unavailable", "summary": "Reporting is down"},
        },
    },
    "metrics": {"large": "blob ignored"},
}

_STATS_BODY = {
    "process": {
        "memory": {"heap": {"used_bytes": 645926408, "total_bytes": 708210688, "size_limit": 4496293888}},
        "event_loop_delay": 13.7,
    },
    "concurrent_connections": 4,
    "os": {"load": {}},
}

_TM_BODY = {
    "id": "abc",
    "timestamp": "2026-07-12T08:09:54.102Z",
    "status": "OK",
    "last_update": "2026-07-12T08:09:52.755Z",
    "stats": {"large": "tree ignored"},
}


def _gw(**ns):
    return KibanaPyGateway(make_fake_client(**ns))


def test_get_kibana_status_summarizes_and_filters_healthy():
    gw = _gw(status=SimpleNamespace(get_status=lambda: FakeResponse(_STATUS_BODY)))
    s = gw.get_kibana_status()
    assert s.overall_level == "available"
    assert s.overall_summary == "All services and plugins are available"
    assert s.version == "9.4.3"
    # only non-available core+plugin services are surfaced; healthy ones dropped
    names = {svc.name for svc in s.unhealthy}
    assert names == {"savedObjects", "reporting"}
    levels = {svc.name: svc.level for svc in s.unhealthy}
    assert levels == {"savedObjects": "degraded", "reporting": "unavailable"}


def test_get_kibana_status_all_healthy_gives_empty_unhealthy():
    body = {"version": {"number": "9.4.3"},
            "status": {"overall": {"level": "available", "summary": "ok"},
                       "core": {"elasticsearch": {"level": "available", "summary": "ok"}},
                       "plugins": {}}}
    gw = _gw(status=SimpleNamespace(get_status=lambda: FakeResponse(body)))
    assert gw.get_kibana_status().unhealthy == ()


def test_get_kibana_stats_picks_runtime_fields():
    gw = _gw(status=SimpleNamespace(get_stats=lambda: FakeResponse(_STATS_BODY)))
    st = gw.get_kibana_stats()
    assert st.heap_used_bytes == 645926408
    assert st.heap_total_bytes == 708210688
    assert st.heap_size_limit_bytes == 4496293888
    assert st.event_loop_delay_ms == 13.7
    assert st.concurrent_connections == 4


def test_get_task_manager_health_summary():
    gw = _gw(task_manager=SimpleNamespace(health=lambda: FakeResponse(_TM_BODY)))
    h = gw.get_task_manager_health()
    assert h.status == "OK"
    assert h.timestamp == "2026-07-12T08:09:54.102Z"
    assert h.last_update == "2026-07-12T08:09:52.755Z"
