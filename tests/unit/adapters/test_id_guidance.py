"""Dashboard and alert-rule ids: an empty id or one that matches nothing names
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
    rule = SimpleNamespace(get=fn, enable=fn, disable=fn, delete=fn)
    return KibanaPyGateway(make_fake_client(alerting=SimpleNamespace(rule=rule)))


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
