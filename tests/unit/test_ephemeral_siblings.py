"""The ephemeral harnesses refuse a sibling's leaked stack (D63).

They share elastic-start-local/.env.ephemeral.example's container names and
ports under three compose projects, so each sources
scripts/ephemeral-siblings.sh and calls refuse_leaked_siblings with its own
project before bring-up; every project the helper knows has a clean target.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HELPER = (_ROOT / "scripts" / "ephemeral-siblings.sh").read_text()
_SIBLINGS = dict(re.findall(r"(mcp-for-kibana-[a-z-]+):([a-z-]+-clean)", _HELPER))
_HARNESSES = ("scripts/ephemeral_stack.sh", "scripts/fleet_ephemeral.sh",
              "scripts/fleet_unaware_ephemeral.sh")


@pytest.mark.parametrize("script", _HARNESSES)
def test_each_harness_refuses_a_leaked_sibling_before_bring_up(script):
    text = (_ROOT / script).read_text()
    project = re.search(r"^PROJECT=(\S+)$", text, re.M).group(1)
    assert project in _SIBLINGS, f"{project} missing from scripts/ephemeral-siblings.sh"
    call = text.find('refuse_leaked_siblings "$PROJECT"')
    assert ". scripts/ephemeral-siblings.sh" in text and 0 <= call < text.find(" up --wait"), (
        f"{script} must source scripts/ephemeral-siblings.sh and call "
        'refuse_leaked_siblings "$PROJECT" before its compose up')


def test_every_sibling_has_a_make_clean_target_for_its_project():
    makefile = (_ROOT / "Makefile").read_text()
    assert len(_SIBLINGS) == len(_HARNESSES)
    for project, target in _SIBLINGS.items():
        recipe = re.search(rf"^{re.escape(target)}:.*\n\t(.*)$", makefile, re.M)
        assert recipe and f"-p {project} down" in recipe.group(1), (target, project)
