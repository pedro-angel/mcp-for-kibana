import pytest

from tests._env import drop_kibana_names_in_other_cases


@pytest.fixture(autouse=True)
def _no_kibana_names_in_other_cases(monkeypatch):
    drop_kibana_names_in_other_cases(monkeypatch)
