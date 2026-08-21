"""Fixtures for the ephemeral-stack streams disable/enable certification.

These tests run ONLY under scripts/ephemeral_stack.sh, which boots an isolated
stack and passes its creds via the process env (KIBANA_URL/KIBANA_TEST_API_KEY) —
never the machine-owned elastic-start-local/.env.seed. The default pytest run
excludes them (pyproject addopts `-m 'not ... and not ephemeral'`)."""

import os

import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway


@pytest.fixture()
def eph_gateway():
    url, key = os.environ.get("KIBANA_URL"), os.environ.get("KIBANA_TEST_API_KEY")
    if not url or not key:
        pytest.skip("ephemeral stack env not set (run via scripts/ephemeral_stack.sh)")
    with KibanaPyGateway.connect(url, key) as gw:
        yield gw


@pytest.fixture()
def gateway():
    # Same env contract as eph_gateway, for the fleet 2-agent ephemeral stack
    # (scripts/fleet_ephemeral.sh passes KIBANA_URL/KIBANA_TEST_API_KEY).
    url, key = os.environ.get("KIBANA_URL"), os.environ.get("KIBANA_TEST_API_KEY")
    if not url or not key:
        pytest.skip("fleet-ephemeral stack env not set (run via scripts/fleet_ephemeral.sh)")
    with KibanaPyGateway.connect(url, key) as gw:
        yield gw
