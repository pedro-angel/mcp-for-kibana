import os

import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from tests._stack_env import require_stack_env

pytestmark = pytest.mark.contract


@pytest.fixture(scope="session")
def gateway():
    require_stack_env()
    with KibanaPyGateway.connect(
        os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"]
    ) as gw:
        yield gw
