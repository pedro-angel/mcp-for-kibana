"""Environment hygiene shared by the test tiers."""

import os
import re

_SHELL_NAME = re.compile(r"[A-Z0-9_]+")


def drop_kibana_names_in_other_cases(monkeypatch):
    """Settings matches env names case-insensitively (it lowercases them), so a
    shell `kibana_url` would reach every unit test; the tests clear and set
    only the ASCII-uppercase names, so drop every other spelling."""
    for var in list(os.environ):
        if var.lower().startswith("kibana") and not _SHELL_NAME.fullmatch(var):
            monkeypatch.delenv(var)
