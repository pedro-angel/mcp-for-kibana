"""Fixtures for the ephemeral-stack streams disable/enable certification.

These tests run ONLY under scripts/ephemeral_stack.sh, which boots an isolated
stack and passes its creds via the process env (KIBANA_URL/KIBANA_TEST_API_KEY) —
never the machine-owned elastic-start-local/.env.seed. The default pytest run
excludes them (pyproject addopts `-m 'not ... and not ephemeral'`)."""

import os

import pytest
from fastmcp import Client

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES


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


@pytest.fixture()
def call_tool():
    """`await call_tool(name, args)`: one call through the full server (every
    toolbox at `destructive`) and a fastmcp Client, against the same stack as
    the fixtures above, so the toolbox layer (confirm gates, argument schemas,
    the ToolError text of a Kibana refusal) runs live (D60). Returns the
    structured content; a refusal raises fastmcp's ToolError."""
    url, key = os.environ.get("KIBANA_URL"), os.environ.get("KIBANA_TEST_API_KEY")
    if not url or not key:
        pytest.skip("ephemeral stack env not set (run via scripts/ephemeral_stack.sh "
                    "or scripts/fleet_ephemeral.sh)")
    server = build_server(
        Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive")),
        lambda space=None: KibanaPyGateway.connect(url, key, space),
    )

    async def call(name, args=None):
        async with Client(server) as client:
            return (await client.call_tool(name, args or {})).structured_content

    return call
