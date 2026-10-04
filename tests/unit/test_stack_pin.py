"""Drift guard: the stack pins track the newest Kibana version kibana-py supports.

The supported set is declared once, by kibana-py (`kibana.SUPPORTED_VERSIONS`),
and the CI contract matrix reads it from there. The two stack templates restate
one version each — the line every local tier runs on — and the rule (D30) is
that it is always the newest supported version. When a kibana-py release moves
its pins, this test fails on that bump until both templates follow.
"""

from pathlib import Path

import kibana
import pytest

_STACK_DIR = Path(__file__).resolve().parents[2] / "elastic-start-local"


def _newest_supported() -> str:
    versions = [v for _, v in kibana.SUPPORTED_VERSIONS]
    return max(versions, key=lambda v: tuple(int(part) for part in v.split(".")))


@pytest.mark.parametrize("template", [".env.example", ".env.ephemeral.example"])
def test_stack_pin_is_the_newest_supported_kibana(template):
    lines = (_STACK_DIR / template).read_text().splitlines()
    pins = [line.split("=", 1)[1] for line in lines if line.startswith("ES_LOCAL_VERSION=")]
    newest = _newest_supported()
    assert pins == [newest], (
        f"elastic-start-local/{template} pins ES_LOCAL_VERSION={pins}, but the newest "
        f"Kibana version kibana-py supports is {newest}: set the pin to {newest} (D30)"
    )
