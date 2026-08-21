import pytest

from tests._stack_env import require_stack_env


@pytest.fixture(scope="session", autouse=True)
def _stack_env():
    """Load + gate the stack env before any e2e fixture reads os.environ."""
    require_stack_env()
