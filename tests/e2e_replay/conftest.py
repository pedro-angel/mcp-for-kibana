"""Fixtures for the replay tier.

The tier proves the model-facing MCP surface end to end with NO model in the
loop, so it is the only e2e-class tier that can run in CI. See
tests/e2e_replay/test_replay.py for what that does and does not certify.
"""

import pytest

from tests._stack_env import require_stack_env


@pytest.fixture(scope="session", autouse=True)
def _stack_env():
    """Load + gate the stack env before any replay fixture reads os.environ.

    Same gate as the contract and e2e tiers: a claimed-but-unusable stack fails
    loudly rather than skipping green.
    """
    require_stack_env()
