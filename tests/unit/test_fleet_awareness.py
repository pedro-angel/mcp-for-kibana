"""The live tiers' Fleet space-awareness mode check (D63, tests/_fleet_awareness.py)."""

import pytest

from tests import _fleet_awareness as fa


def test_the_contract_tier_certifies_awareness_on(monkeypatch):
    monkeypatch.delenv(fa.UNAWARE_ENV, raising=False)
    assert fa.mismatch(True) is None
    why = fa.mismatch(False)
    assert why and "scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed" in why
    assert "scripts/fleet_unaware_ephemeral.sh" in why


def test_the_unaware_tier_certifies_awareness_off(monkeypatch):
    monkeypatch.setenv(fa.UNAWARE_ENV, "1")
    assert fa.mismatch(False) is None
    why = fa.mismatch(True)
    assert why and "scripts/fleet_unaware_ephemeral.sh" in why
    assert "docker-compose.fleet-unaware.yml" in why and f"unset {fa.UNAWARE_ENV}" in why


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_exactly_1_selects_the_unaware_tier(monkeypatch, value):
    monkeypatch.setenv(fa.UNAWARE_ENV, value)
    assert fa.expected_on() is True
