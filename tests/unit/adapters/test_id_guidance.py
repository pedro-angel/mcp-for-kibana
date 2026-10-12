"""Dashboard, alert-rule, connector and data-view ids: an empty id or one that matches nothing names
the fix (D17) — where a valid id comes from — instead of kibana-py's bare
"Parameter 'id' is required" (unmapped, so the caller saw only "Error calling
tool") or Kibana's "Saved object [alert/<name>] not found"."""

from types import SimpleNamespace

import pytest
from kibana.exceptions import NotFoundError, SpaceNotFoundError

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound, KibanaRejected, KibanaSpaceNotFound
from tests.unit.adapters.test_kibana_gateway import make_fake_client


def _missing(**_kw):
    raise NotFoundError("not found", meta=SimpleNamespace(status=404), body=None)


def _no_space(**_kw):
    raise SpaceNotFoundError("sales")


def _dash_gw(fn):
    return KibanaPyGateway(make_fake_client(dashboards=SimpleNamespace(get=fn, update=fn, delete=fn)))


def _rule_gw(fn):
    rule = SimpleNamespace(get=fn, enable=fn, disable=fn, delete=fn, update=fn)
    return KibanaPyGateway(make_fake_client(alerting=SimpleNamespace(rule=rule)))


def _conn_gw(fn):
    return KibanaPyGateway(make_fake_client(connectors=SimpleNamespace(get=fn, update=fn)))


_DASH_CALLS = [
    lambda gw, i: gw.get_dashboard(i),
    lambda gw, i: gw.get_dashboard_data(i),
    lambda gw, i: gw.update_dashboard(i, {}),
    lambda gw, i: gw.delete_dashboard(i),
]
_RULE_CALLS = [
    lambda gw, i: gw.get_alert_rule(i),
    lambda gw, i: gw.enable_alert_rule(i),
    lambda gw, i: gw.disable_alert_rule(i),
    lambda gw, i: gw.delete_alert_rule(i),
    lambda gw, i: gw.update_alert_rule(i, "renamed", None, None, None, None),
]
_CONN_CALLS = [
    lambda gw, i: gw.get_connector(i),
    lambda gw, i: gw.update_connector(i, "renamed", None, None),
]


@pytest.mark.parametrize("call", _DASH_CALLS)
@pytest.mark.parametrize("empty", ["", "  "])
def test_empty_dashboard_id_names_where_ids_come_from(call, empty):
    with pytest.raises(KibanaRejected, match=r"dashboard_id is empty — .*create_dashboard or search_dashboards"):
        call(_dash_gw(_missing), empty)


@pytest.mark.parametrize("call", _DASH_CALLS)
def test_unknown_dashboard_id_says_it_is_not_the_title(call):
    with pytest.raises(
        KibanaNotFound,
        match=r"dashboard 'LOC-1 Web logs' not found — dashboard_id is the id .*not the dashboard's title",
    ):
        call(_dash_gw(_missing), "LOC-1 Web logs")


@pytest.mark.parametrize("call", _RULE_CALLS)
@pytest.mark.parametrize("empty", ["", "  "])
def test_empty_rule_id_names_where_ids_come_from(call, empty):
    with pytest.raises(KibanaRejected, match=r"rule_id is empty — .*create_alert_rule or list_alert_rules"):
        call(_rule_gw(_missing), empty)


@pytest.mark.parametrize("call", _RULE_CALLS)
def test_unknown_rule_id_says_it_is_not_the_name(call):
    with pytest.raises(
        KibanaNotFound,
        match=r"alert rule 'LOC-1 5xx errors' not found — rule_id is the id .*not the rule's name",
    ):
        call(_rule_gw(_missing), "LOC-1 5xx errors")


@pytest.mark.parametrize("call", [_DASH_CALLS[1], _RULE_CALLS[1]])
def test_a_missing_space_is_still_reported_as_the_space(call):
    gw = _dash_gw(_no_space) if call is _DASH_CALLS[1] else _rule_gw(_no_space)
    with pytest.raises(KibanaSpaceNotFound):
        call(gw, "x")


@pytest.mark.parametrize("call", _CONN_CALLS)
@pytest.mark.parametrize("empty", ["", "  "])
def test_empty_connector_id_names_where_ids_come_from(call, empty):
    with pytest.raises(
        KibanaRejected, match=r"connector_id is empty — .*create_connector or list_connectors"
    ):
        call(_conn_gw(_missing), empty)


@pytest.mark.parametrize("call", _CONN_CALLS)
def test_unknown_connector_id_says_it_is_not_the_name(call):
    with pytest.raises(
        KibanaNotFound,
        match=r"connector 'Ops log' not found — connector_id is the id .*not the connector's "
              r"name; call list_connectors to find it",
    ):
        call(_conn_gw(_missing), "Ops log")


def test_unknown_connector_in_a_space_carries_the_suffix_once():
    gw = KibanaPyGateway(
        make_fake_client(connectors=SimpleNamespace(get=_missing)), space="sales"
    )
    with pytest.raises(KibanaNotFound) as exc:
        gw.get_connector("nope")
    assert exc.value.message.endswith(" (in space 'sales')")
    assert exc.value.message.count("(in space") == 1


def _dv_gw(fn, space=None):
    return KibanaPyGateway(
        make_fake_client(data_views=SimpleNamespace(get=fn, update=fn)), space=space
    )


@pytest.mark.parametrize("empty", ["", "  "])
def test_empty_view_id_names_where_ids_come_from(empty):
    with pytest.raises(
        KibanaRejected, match=r"view_id is empty — .*create_data_view or list_data_views"
    ):
        _dv_gw(_missing).update_data_view(empty, "renamed", None, None)


def test_unknown_view_id_says_it_is_not_the_name():
    # Kibana's own text is "Saved object [index-pattern/<id>] not found" (9.5.4).
    with pytest.raises(
        KibanaNotFound,
        match=r"data view 'Kibana Sample Data Flights' not found — view_id is the id .*"
              r"not the view's name or index pattern; call list_data_views to find it",
    ):
        _dv_gw(_missing).update_data_view("Kibana Sample Data Flights", "x", None, None)


def test_unknown_view_id_in_a_space_carries_the_suffix_once():
    with pytest.raises(KibanaNotFound) as exc:
        _dv_gw(_missing, space="sales").update_data_view("nope", "x", None, None)
    assert exc.value.message.endswith(" (in space 'sales')")
    assert exc.value.message.count("(in space") == 1


def test_update_data_view_missing_space_is_still_the_space():
    with pytest.raises(KibanaSpaceNotFound):
        _dv_gw(_no_space).update_data_view("x", "renamed", None, None)
