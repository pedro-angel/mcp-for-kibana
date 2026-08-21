from types import SimpleNamespace

import pytest
from kibana.exceptions import (
    AuthorizationException,
    BadRequestError,
    ConnectionTimeout,
    InvalidSpaceIdError,
    NotFoundError,
    SpaceNotFoundError,
)

import kibana_mcp.adapters.kibana.gateway as gateway_mod
from kibana_mcp.adapters.kibana.gateway import (
    KibanaPyGateway,
    _scoped,
    _translated,
    is_space_pinned,
)
from kibana_mcp.core.errors import (
    KibanaAuthError,
    KibanaNotFound,
    KibanaRejected,
    KibanaSpaceNotFound,
    KibanaUnavailable,
)


class FakeResponse:
    def __init__(self, body):
        self.body = body


def make_fake_client(**overrides):
    data_views = SimpleNamespace(
        get_all=lambda: FakeResponse(
            {"data_view": [{"id": "dv1", "name": "flights", "title": "kibana_sample_data_flights"}]}
        ),
        get=lambda view_id: FakeResponse(
            {
                "data_view": {
                    "id": "dv1",
                    "name": "flights",
                    "title": "kibana_sample_data_flights",
                    "timeFieldName": "timestamp",
                    "fields": {
                        "Carrier": {"name": "Carrier", "type": "string"},
                        "AvgTicketPrice": {"name": "AvgTicketPrice", "type": "number"},
                    },
                }
            }
        ),
    )
    dashboards = SimpleNamespace(
        get_all=lambda **kw: FakeResponse(
            {"dashboards": [{"id": "d1", "data": {"title": "Ops", "description": "x"}}], "total": 1}
        ),
        get=lambda id: FakeResponse(
            {
                "id": "d1",
                "data": {
                    "title": "Ops",
                    "description": "x",
                    "panels": [{"type": "vis", "grid": {"x": 0, "y": 0}, "config": {"title": "c"}}],
                },
                "meta": {},
            }
        ),
        create=lambda **kw: FakeResponse({"id": "new-dash"}),
        update=lambda **kw: FakeResponse({"id": kw["id"]}),
        delete=lambda id: FakeResponse({}),
    )
    visualizations = SimpleNamespace(
        create=lambda data: FakeResponse({"id": "new-viz"}),
        delete=lambda id: FakeResponse({}),
    )
    ns = dict(
        data_views=data_views, dashboards=dashboards, visualizations=visualizations,
        close=lambda: None,
    )
    ns.update(overrides)
    return SimpleNamespace(**ns)


def test_list_data_views():
    gw = KibanaPyGateway(make_fake_client())
    views = gw.list_data_views()
    assert views[0].id == "dv1"
    assert views[0].index_pattern == "kibana_sample_data_flights"


def test_get_data_view_resolves_by_name_and_normalizes_fields():
    gw = KibanaPyGateway(make_fake_client())
    dv = gw.get_data_view("flights")
    assert dv.time_field == "timestamp"
    assert dv.fields == {"Carrier": "string", "AvgTicketPrice": "number"}


def test_get_data_view_unknown_raises_domain_error():
    gw = KibanaPyGateway(make_fake_client())
    with pytest.raises(
        KibanaNotFound, match=r"data view 'nope' not found — call list_data_views to see what exists"
    ):
        gw.get_data_view("nope")


def test_get_data_view_ambiguous_name_raises_rejected():
    fake = make_fake_client()
    fake.data_views.get_all = lambda: FakeResponse(
        {
            "data_view": [
                {"id": "dv1", "name": "dup", "title": "index-a"},
                {"id": "dv2", "name": "dup", "title": "index-b"},
            ]
        }
    )
    gw = KibanaPyGateway(fake)
    with pytest.raises(KibanaRejected, match="matches 2 data views"):
        gw.get_data_view("dup")


def test_create_dashboard_spreads_flat_kwargs():
    captured = {}

    def create(**kw):
        captured.update(kw)
        return FakeResponse({"id": "new-dash"})

    fake = make_fake_client()
    fake.dashboards.create = create
    new_id = KibanaPyGateway(fake).create_dashboard(
        {"title": "t", "description": "", "panels": []}
    )
    assert new_id == "new-dash"
    assert captured == {"title": "t", "description": "", "panels": []}


def test_dashboard_detail_summarizes_panels():
    gw = KibanaPyGateway(make_fake_client())
    d = gw.get_dashboard("d1")
    assert d.panels[0].title == "c"
    assert d.panels[0].type == "vis"


def test_kibana_errors_map_to_domain_errors():
    def boom(id):
        raise NotFoundError("dashboard not found", meta=SimpleNamespace(status=404), body=None)

    gw = KibanaPyGateway(make_fake_client(dashboards=SimpleNamespace(get=boom)))
    with pytest.raises(KibanaNotFound):
        gw.get_dashboard("missing")


def test_bad_request_maps_to_rejected():
    def boom(**kw):
        raise BadRequestError("schema validation failed", meta=SimpleNamespace(status=400), body=None)

    fake = make_fake_client()
    fake.dashboards.create = boom
    gw = KibanaPyGateway(fake)
    with pytest.raises(KibanaRejected):
        gw.create_dashboard({"title": "t"})


def test_update_dashboard_filters_disallowed_keys_and_passes_id():
    captured = {}

    def update(**kw):
        captured.update(kw)
        return FakeResponse({"id": kw["id"]})

    fake = make_fake_client()
    fake.dashboards.update = update
    KibanaPyGateway(fake).update_dashboard(
        "d1", {"title": "t", "panels": [], "meta": {"x": 1}, "id": "evil"}
    )
    assert captured == {"id": "d1", "title": "t", "panels": []}


def test_upsert_dashboard_filters_disallowed_keys_and_returns_id():
    captured = {}

    def update(**kw):
        captured.update(kw)
        return FakeResponse({"id": kw["id"]})

    fake = make_fake_client()
    fake.dashboards.update = update
    returned = KibanaPyGateway(fake).upsert_dashboard(
        "d1", {"title": "t", "panels": [], "meta": {"x": 1}, "id": "evil"}
    )
    assert returned == "d1"
    assert captured == {"id": "d1", "title": "t", "panels": []}


def test_search_dashboards_query_kwarg():
    calls = []

    def get_all(**kw):
        calls.append(kw)
        return FakeResponse({"dashboards": [], "total": 0})

    fake = make_fake_client()
    fake.dashboards.get_all = get_all
    gw = KibanaPyGateway(fake)
    assert gw.search_dashboards("ops*") == []
    assert gw.search_dashboards(None) == []
    assert calls == [{"query": "ops*", "per_page": 100, "page": 1}, {"per_page": 100, "page": 1}]


def test_search_dashboards_walks_every_page():
    pages = [
        {
            "dashboards": [{"id": "d1", "data": {"title": "One"}}],
            "total": 2,
        },
        {
            "dashboards": [{"id": "d2", "data": {"title": "Two"}}],
            "total": 2,
        },
    ]

    def get_all(**kw):
        return FakeResponse(pages[kw["page"] - 1])

    fake = make_fake_client()
    fake.dashboards.get_all = get_all
    gw = KibanaPyGateway(fake)
    results = gw.search_dashboards(None)
    assert [r.id for r in results] == ["d1", "d2"]


def test_create_visualization_uses_data_kwarg():
    def create(*args, **kw):
        assert not args and set(kw) == {"data"}
        return FakeResponse({"id": "new-viz"})

    fake = make_fake_client()
    fake.visualizations.create = create
    assert KibanaPyGateway(fake).create_visualization({"type": "xy"}) == "new-viz"


def test_delete_dashboard_passes_id():
    deleted = []
    fake = make_fake_client()
    fake.dashboards.delete = lambda id: (deleted.append(id), FakeResponse({}))[1]
    KibanaPyGateway(fake).delete_dashboard("d9")
    assert deleted == ["d9"]


def test_delete_visualization_passes_id():
    deleted = []
    fake = make_fake_client()
    fake.visualizations.delete = lambda id: (deleted.append(id), FakeResponse({}))[1]
    KibanaPyGateway(fake).delete_visualization("viz9")
    assert deleted == ["viz9"]


def test_get_dashboard_data_warns_on_unexpected_fields():
    fake = make_fake_client()
    fake.dashboards.get = lambda id: FakeResponse(
        {
            "id": "d1",
            "data": {
                "title": "Ops",
                "panels": [],
                "sections": [{"title": "s1", "panels": []}],
            },
        }
    )
    gw = KibanaPyGateway(fake)
    data, warnings = gw.get_dashboard_data("d1")
    assert data["sections"] == [{"title": "s1", "panels": []}]
    assert warnings
    assert any("sections" in w for w in warnings)


def test_normalize_fields_list_shape():
    fake = make_fake_client()
    fake.data_views.get = lambda view_id: FakeResponse(
        {
            "data_view": {
                "id": "dv1",
                "name": "flights",
                "title": "kibana_sample_data_flights",
                "timeFieldName": "timestamp",
                "fields": [{"name": "Carrier", "type": "string"}],
            }
        }
    )
    dv = KibanaPyGateway(fake).get_data_view("flights")
    assert dv.fields == {"Carrier": "string"}


def test_space_not_found_is_a_not_found():
    e = KibanaSpaceNotFound("space 'x' not found")
    assert isinstance(e, KibanaNotFound)
    assert e.message == "space 'x' not found"


GUIDANCE = "space 'sales' not found — check what exists with list_spaces"


def test_translated_space_not_found_returns_tagged_guidance():
    out = _translated(SpaceNotFoundError("sales"), "sales")
    assert isinstance(out, KibanaSpaceNotFound)
    assert out.message.startswith(GUIDANCE)
    assert "omit `space`" in out.message


def test_translated_subclass_dispatch_connection_timeout():
    # ConnectionTimeout subclasses TransportError — ordered isinstance, not exact type
    out = _translated(ConnectionTimeout("boom"), None)
    assert isinstance(out, KibanaUnavailable)
    assert out.message == "cannot reach Kibana — check that the server's KIBANA_URL is reachable"


def test_translated_unknown_exception_passes_through_unchanged():
    e = ValueError("nope")
    assert _translated(e, None) is e


def test_scoped_appends_suffix_once_and_only_when_scoped():
    gw_scoped = KibanaPyGateway(object(), "sales")
    gw_plain = KibanaPyGateway(object())
    e = KibanaNotFound("dashboard 'x' not found")
    out = _scoped(gw_scoped, e)
    assert out is not e and out.message == "dashboard 'x' not found (in space 'sales')"
    assert str(out) == out.message  # args and message agree (new exception, no mutation)
    assert _scoped(gw_scoped, out) is out                    # idempotent second application
    assert _scoped(gw_plain, e) is e                         # space-is-None gate
    tagged = KibanaSpaceNotFound("space 'sales' not found — …")
    assert _scoped(gw_scoped, tagged) is tagged              # space-origin never suffixed


def test_decorator_suffixes_translated_vendor_404_when_scoped():
    """The OTHER suffix source: a kibana-py NotFoundError translated by the
    inner except must still pick up the scoped suffix in the outer try."""
    vendor = NotFoundError("dashboard not found", meta=SimpleNamespace(status=404), body=None)

    class FakeDashboards:
        def get(self, id):
            raise vendor

    class FakeClient:
        dashboards = FakeDashboards()

    gw = KibanaPyGateway(FakeClient(), "sales")
    with pytest.raises(KibanaNotFound) as exc:
        gw.get_dashboard("missing")
    assert exc.value.message.endswith(" (in space 'sales')")
    # the chain threads all the way back to the vendor error:
    # scoped -> translated -> vendor
    assert exc.value.__cause__ is not None
    assert exc.value.__cause__.__cause__ is vendor


def test_decorator_suffixes_adapter_raised_miss_when_scoped():
    class FakeDV:
        def get_all(self):
            class R:
                body = {"data_view": []}

            return R()

    class FakeClient:
        data_views = FakeDV()

    gw = KibanaPyGateway(FakeClient(), "sales")
    with pytest.raises(KibanaNotFound) as exc:
        gw.get_data_view("missing")
    assert exc.value.message.endswith(" (in space 'sales')")
    # and the unscoped twin carries no suffix:
    with pytest.raises(KibanaNotFound) as exc2:
        KibanaPyGateway(FakeClient()).get_data_view("missing")
    assert "(in space" not in exc2.value.message


def test_decorator_maps_midcall_space_not_found_to_guidance_untouched():
    class FakeDV:
        def get_all(self):
            raise SpaceNotFoundError("sales")

    class FakeClient:
        data_views = FakeDV()

    gw = KibanaPyGateway(FakeClient(), "sales")
    with pytest.raises(KibanaSpaceNotFound) as exc:
        gw.list_data_views()
    assert "space 'sales' not found" in exc.value.message
    assert "(in space" not in exc.value.message  # tag blocks the suffix


@pytest.mark.parametrize(
    "url,pinned",
    [
        ("http://localhost:5601", False),
        ("http://localhost:5601/", False),
        ("https://kb.example.com/s/team-a", True),
        ("https://kb.example.com/s/team-a/", True),
        ("https://gw.corp/apps/s/kibana", True),  # documented false positive
        ("https://kb.example.com/so/x", False),
        ("https://kb.example.com/s/UPPER", False),  # id grammar is lowercase
    ],
)
def test_is_space_pinned_truth_table(url, pinned):
    assert is_space_pinned(url) is pinned


class _StubSpaceClient:
    def __init__(self, exc):
        self.exc = exc
        self.closed = False

    def space(self, space_id, validate=True):
        raise self.exc

    def close(self):
        self.closed = True


def _connect_with(monkeypatch, exc, space="sales"):
    stub = _StubSpaceClient(exc)
    # patches the shared kibana module attribute (process-wide, restored by
    # monkeypatch; these cases must not run under in-process parallelism)
    monkeypatch.setattr(gateway_mod.kibana, "Kibana", lambda url, api_key: stub)
    with pytest.raises(Exception) as exc_info:
        KibanaPyGateway.connect("http://kb:5601", "key", space)
    return stub, exc_info.value


def test_connect_space_not_found_guidance(monkeypatch):
    stub, err = _connect_with(monkeypatch, SpaceNotFoundError("sales"))
    assert isinstance(err, KibanaSpaceNotFound)
    assert "space 'sales' not found" in err.message and stub.closed


def test_connect_invalid_id_rejected(monkeypatch):
    stub, err = _connect_with(monkeypatch, InvalidSpaceIdError("SALES"))
    assert isinstance(err, KibanaRejected) and stub.closed


def test_connect_auth_error_names_the_precheck(monkeypatch):
    # ApiError.__init__ reads meta.status — a None meta AttributeErrors at
    # construction (the file's existing convention uses SimpleNamespace)
    exc = AuthorizationException("forbidden", SimpleNamespace(status=403), None)
    stub, err = _connect_with(monkeypatch, exc)
    assert isinstance(err, KibanaAuthError) and stub.closed
    assert "raised while validating space 'sales'" in err.message
    assert "must be valid and able to read spaces" in err.message


def test_connect_transport_error_translated(monkeypatch):
    stub, err = _connect_with(monkeypatch, ConnectionTimeout("t/o"))
    assert isinstance(err, KibanaUnavailable) and stub.closed


def test_connect_unknown_error_passes_through(monkeypatch):
    stub, err = _connect_with(monkeypatch, ValueError("weird"))
    assert isinstance(err, ValueError) and stub.closed


def test_connect_refuses_pinned_url_with_space():
    with pytest.raises(KibanaRejected) as exc:
        KibanaPyGateway.connect("http://kb:5601/s/team-a", "key", "sales")
    assert "space-pinned" in exc.value.message


def test_connect_no_space_never_validates(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(gateway_mod.kibana, "Kibana", lambda url, api_key: sentinel)
    gw = KibanaPyGateway.connect("http://kb:5601/s/team-a", "key")  # pinned OK w/o space
    assert gw._client is sentinel and gw._space is None
