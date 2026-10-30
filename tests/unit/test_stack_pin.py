"""Drift guard: the stack pins track the newest Kibana version kibana-py supports.

The supported set is declared once, by kibana-py (`kibana.SUPPORTED_VERSIONS`),
and the CI contract matrix reads it from there. The two stack templates restate
one version each — the line every local tier runs on — and the rule (D30) is
that it is always the newest supported version. When a kibana-py release moves
its pins, this test fails on that bump until both templates follow, and
scripts/cloud-setup.sh's image pre-pull fallback with them. The DoD gate checks
the running stack against the pin (tests/_stack_version.py, D53).
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


def test_cloud_setup_fallback_is_the_newest_supported_kibana():
    # scripts/cloud-setup.sh pre-pulls the stack images; it reads the pin from
    # the repo and falls back to a restated version when it cannot.
    script = (_STACK_DIR.parent / "scripts" / "cloud-setup.sh").read_text().splitlines()
    fallbacks = [
        line.split("=", 1)[1].strip('"') for line in script if line.startswith("fallback_version=")
    ]
    newest = _newest_supported()
    assert fallbacks == [newest], (
        f"scripts/cloud-setup.sh sets fallback_version={fallbacks}, but the newest Kibana "
        f"version kibana-py supports is {newest}: set it to {newest} (D30)"
    )
