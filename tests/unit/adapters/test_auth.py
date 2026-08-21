import pytest

from kibana_mcp.adapters.mcp.auth import parse_api_key
from kibana_mcp.core.errors import KibanaAuthError


def test_header_apikey_scheme():
    assert parse_api_key({"authorization": "ApiKey abc=="}, None) == "abc=="
    assert parse_api_key({"authorization": "apikey xyz"}, None) == "xyz"


def test_header_beats_fallback():
    assert parse_api_key({"authorization": "ApiKey fromheader"}, "fromenv") == "fromheader"


def test_no_header_uses_fallback():
    assert parse_api_key({}, "fromenv") == "fromenv"


def test_wrong_scheme_rejected_without_leaking_key():
    with pytest.raises(KibanaAuthError) as exc:
        parse_api_key({"authorization": "Bearer supersecret"}, None)
    assert "ApiKey" in str(exc.value)
    assert "supersecret" not in str(exc.value)


def test_nothing_at_all():
    with pytest.raises(KibanaAuthError, match="KIBANA_API_KEY"):
        parse_api_key({}, None)


def test_fallback_key_with_newline_is_rejected_without_echoing_it():
    with pytest.raises(KibanaAuthError) as exc:
        parse_api_key({}, "OPERATOR_SECRET_KEY\nX")
    assert "OPERATOR_SECRET_KEY" not in str(exc.value)


def test_fallback_key_is_stripped():
    assert parse_api_key({}, "  goodkey  ") == "goodkey"
