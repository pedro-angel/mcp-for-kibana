"""The seeded secret canaries (D59) against the read tier.

`scripts/stack.sh seed` creates a Fleet output and a Fleet proxy whose secret
fields hold the dummy values in scripts/canaries/*.json. Kibana returns them
in plain text to the stack's superuser-snapshot key (asserted first, so the
scan has something real to catch); every tool result in this tier passes
through the autouse `secret_scan` hook (tests/_secret_scan.py). Here the read
tools that reach those objects run through the full server at the `read`
tier — the documented safe profile — and the hook fails any that passes a
canary on, as get_saved_object did before d1a09c6. The Fleet tools that read
real credentials Kibana hands out (enrollment keys' `api_key`, uninstall
tokens) and update_output's read-modify-write of the raw canary output run
here too: no other live test calls them through a Client.
"""

import contextlib
import json
import os

import kibana
import pytest
from fastmcp import Client, FastMCP

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests import _secret_scan
from tests._stack_env import require_stack_env

pytestmark = pytest.mark.contract

_SEED_FIX = "re-run: scripts/stack.sh seed (it creates the canaries from scripts/canaries/)"
_CANARIES = {
    path.stem: json.loads(path.read_text())
    for path in sorted(_secret_scan.CANARY_DIR.glob("*.json"))
}
_API = {name: f"/api/{path}" for name, path in _secret_scan.seeded_canaries().items()}
_SO_TYPES = {"fleet-output": "ingest-outputs", "fleet-proxy": "fleet-proxy"}


@pytest.fixture(autouse=True)
def _stack():
    require_stack_env()


def _server(tier="read"):
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier(tier))
    return build_server(
        settings,
        lambda space=None: KibanaPyGateway.connect(
            os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space),
    )


@pytest.mark.parametrize("name", sorted(_CANARIES))
def test_canary_is_seeded_and_readable_to_the_test_key(name):
    """The scan only proves something if Kibana does hand the secret out: the
    canary exists and its secret fields come back in plain text."""
    body = _CANARIES[name]
    with contextlib.closing(kibana.Kibana(
            os.environ["KIBANA_URL"], api_key=os.environ["KIBANA_TEST_API_KEY"])) as kb:
        try:
            got = kb.perform_request("GET", f"{_API[name]}/{body['id']}").body
            item = got.get("item", got)  # Fleet wraps the object in `item`
        except kibana.NotFoundError:
            pytest.fail(f"canary {name} '{body['id']}' is not on the stack — {_SEED_FIX}")
    seeded = {k: v for k, v in body.items() if _secret_scan.CANARY_PREFIX in json.dumps(v)}
    assert seeded and {k: item.get(k) for k in seeded} == seeded, (
        f"canary {name} '{body['id']}' does not hold the values in "
        f"scripts/canaries/{name}.json — {_SEED_FIX}")


async def test_the_scan_hook_is_active_in_this_tier(secret_scan):
    """A tool that returns a canary fails at the call: the autouse hook is on."""
    server = FastMCP("leaky")
    value = next(iter(_secret_scan.canaries()))

    @server.tool
    def leak() -> dict:
        return {"note": value}

    async with Client(server) as client:
        with pytest.raises(_secret_scan.SecretInToolResult, match="tool 'leak'"):
            await client.call_tool("leak", {})
    assert len(secret_scan) == 1
    secret_scan.clear()  # the deliberate leak; any other one still fails the test


async def test_read_tier_tools_that_reach_the_canaries_return_no_secret():
    """Every read tool that can reach a canary, at the read tier: the hook
    scans each result; list_outputs must still show the canary output (by its
    allowlisted fields), and get_saved_object must refuse both types."""
    output_id = _CANARIES["fleet-output"]["id"]
    async with Client(_server()) as client:
        listed = await client.call_tool("list_outputs", {})
        assert output_id in {o["id"] for o in listed.structured_content["result"]}, (
            f"list_outputs does not show the canary output '{output_id}' — {_SEED_FIX}")
        await client.call_tool("get_output_health", {"output_id": output_id},
                               raise_on_error=False)
        await client.call_tool("list_fleet_server_hosts", {})
        found = await client.call_tool(
            "find_saved_objects", {"types": sorted(_SO_TYPES.values()), "per_page": 100})
        hits = found.structured_content["saved_objects"]
        names = {_CANARIES[k]["name"] for k in _CANARIES}
        canary_hits = [h for h in hits if h.get("title") in names or h.get("name") in names]
        assert {h["type"] for h in canary_hits} == set(_SO_TYPES.values()), (
            f"find_saved_objects does not show both canaries ({hits}) — {_SEED_FIX}")
        for hit in canary_hits:
            got = await client.call_tool(
                "get_saved_object", {"type": hit["type"], "id": hit["id"]},
                raise_on_error=False)
            assert got.is_error, (
                f"get_saved_object read a {hit['type']} at the read tier; it must refuse "
                "types outside READABLE_TYPES (core/saved_objects.py)")


async def test_fleet_credential_tools_return_no_secret():
    """The SECRET-REDACTED Fleet reads (fleet/toolbox.py) through the server:
    Kibana returns each enrollment key's `api_key` and each uninstall token's
    `token` to the test key, so a tool that passed the raw item on is caught
    by the hook. The seeded stack holds enrollment keys (the agent policies')."""
    async with Client(_server()) as client:
        keys = (await client.call_tool("list_enrollment_keys", {})).structured_content["result"]
        assert keys, ("list_enrollment_keys is empty, so the scan sees no key — "
                      "scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed")
        for key in keys[:3]:
            await client.call_tool("get_enrollment_key", {"key_id": key["id"]})
        await client.call_tool("list_uninstall_tokens", {})


def _reseed(kb, name):
    """Put a canary back as its file holds it (what `stack.sh seed` does)."""
    body = _CANARIES[name]
    with contextlib.suppress(kibana.NotFoundError):
        kb.perform_request("DELETE", f"{_API[name]}/{body['id']}",
                           headers={"kbn-xsrf": "true"})
    kb.perform_request("POST", _API[name], body=body,
                       headers={"kbn-xsrf": "true", "content-type": "application/json"})


async def test_update_output_on_the_canary_returns_no_secret():
    """update_output reads the raw output (service_token, config_yaml) and
    writes it back: pointed at the canary output at the write tier, its result
    carries no secret. The write clears the canary's service_token (kibana-py's
    update takes none, so the read-modify-write drops it: docs/roadmap.md), so
    the canary is put back afterwards for the next run."""
    body = _CANARIES["fleet-output"]
    with contextlib.closing(kibana.Kibana(
            os.environ["KIBANA_URL"], api_key=os.environ["KIBANA_TEST_API_KEY"])) as kb:
        try:
            async with Client(_server("write")) as client:
                got = await client.call_tool(
                    "update_output", {"output_id": body["id"], "name": body["name"]})
            assert got.structured_content["id"] == body["id"]
        finally:
            _reseed(kb, "fleet-output")
