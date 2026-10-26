"""Environment hygiene shared by the test tiers."""

import os


def drop_kibana_names_in_other_cases(monkeypatch):
    """Settings reads env names in any letter case, so a shell `kibana_url`
    would reach every unit test; the tests clear and set only the uppercase
    names."""
    for var in list(os.environ):
        if var != var.upper() and var.upper().startswith("KIBANA"):
            monkeypatch.delenv(var)
