"""The secret scan (D59, tests/_secret_scan.py) on fixtures: what it catches,
what it lets through, and that its Client hook fails a call that leaks."""

import base64
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from kibana_mcp.adapters.kibana.gateway import _to_fleet_output
from tests import _secret_scan as scan

_REPO = Path(__file__).resolve().parents[2]
_OUTPUT = json.loads((scan.CANARY_DIR / "fleet-output.json").read_text())
_PROXY = json.loads((scan.CANARY_DIR / "fleet-proxy.json").read_text())


def _result(payload=None, text=None, structured=None):
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)],
                           structured_content=structured)


def test_canaries_come_from_the_seed_files():
    values = scan.canaries()
    assert set(values) == {
        "mcp-for-kibana-canary-secret-service-token-5b2e8f61",
        "mcp-for-kibana-canary-secret-config-yaml-5b2e8f61",
        "mcp-for-kibana-canary-secret-certificate-key-5b2e8f61",
        "mcp-for-kibana-canary-secret-proxy-header-5b2e8f61",
    }
    whole, text = scan.seeded_secret_keys()
    assert {"service_token", "certificate_key", "proxy_headers", "proxy-authorization"} <= whole
    assert text == {"config_yaml"}


def test_stack_seed_creates_every_canary_file():
    """Every scripts/canaries file is one `stack.sh seed` posts: its name is
    <canary>.json and <canary>:<api path> is in the seed's loop."""
    seeded = scan.seeded_canaries()
    assert seeded, "scripts/stack.sh seed no longer loops over the canary files"
    assert all(path and not path.startswith("/") for path in seeded.values()), seeded
    files = {p.name for p in scan.CANARY_DIR.glob("*.json")}
    assert files == {f"{name}.json" for name in seeded}, (
        f"scripts/canaries holds {sorted(files)} but stack.sh seed creates "
        f"{sorted(seeded)} — add `<canary>:<Kibana API path>` to the seed's "
        "`for kind in` loop")


def test_every_canary_file_fits_the_seed_loop():
    """The loop POSTs the file to /api/<path> from its entry and DELETEs
    /api/<path>/<id>, the id read back from the file with sed: every file
    holds a top-level "id" on a line of its own, and the loop reads the path
    from the entry. Whether the API takes that id and returns the secret on
    GET is the contract test's job (test_canary_is_seeded_...)."""
    stack = (_REPO / "scripts" / "stack.sh").read_text()
    loop = stack[stack.index("for kind in"):stack.index("done", stack.index("for kind in"))]
    assert 'api="$KB/api/${kind#*:}"' in loop
    for name in scan.seeded_canaries():
        text = (scan.CANARY_DIR / f"{name}.json").read_text()
        ids = [ln for ln in text.splitlines() if ln.lstrip().startswith('"id":')]
        assert len(ids) == 1 and json.loads(text)["id"], (
            f"scripts/canaries/{name}.json needs exactly one top-level \"id\" line: "
            "the seed reads it back to DELETE the old canary (D59)")


def test_a_raw_fleet_output_is_caught_with_the_fix():
    hits = scan.scan_result(_result(_OUTPUT))
    joined = "\n".join(hits)
    assert "`service_token` has a value" in joined
    assert "`config_yaml` holds text naming a credential" in joined
    assert "canary from fleet-output.json" in joined
    assert "drop the field in the adapter's summary" in scan.message("list_outputs", hits)


def test_a_raw_fleet_proxy_is_caught():
    joined = "\n".join(scan.scan_result(_result({"items": [_PROXY]})))
    assert "`items.0.certificate_key` has a value" in joined
    assert "`items.0.proxy_headers` has a value" in joined


def test_the_allowlisted_output_summary_passes():
    """What list_outputs really returns for the canary output: nothing secret."""
    summary = asdict(_to_fleet_output(_OUTPUT))
    assert summary["id"] == _OUTPUT["id"]
    assert scan.scan_result(_result([summary], structured={"result": [summary]})) == []


def test_canary_in_error_text_or_structured_content_is_caught():
    value = next(iter(scan.canaries()))
    assert scan.scan_result(_result(text=f"Kibana said: bad value {value}"))
    assert scan.scan_result(SimpleNamespace(content=[], structured_content={"x": [value]}))


@pytest.mark.parametrize("payload", [
    {"is_missing_secrets": False, "has_password": True},
    {"password": None, "api_key": "", "secrets": {}, "service_token": False},
    {"ssl": {"certificate_authorities": ["ca"], "verification_mode": "full"}},
    {"config_yaml": "bulk_max_size: 50\n"},
    {"name": "tokens", "description": "rotate the password weekly"},
])
def test_harmless_shapes_pass(payload):
    assert scan.scan_payload(payload, "content[0]") == []


@pytest.mark.parametrize("payload, key", [
    ({"password": "hunter2"}, "password"),
    ({"output": {"secrets": {"ssl": {"key": {"id": "abc"}}}}}, "output.secrets"),
    ({"ssl": {"key": "dummy-pem-bytes"}}, "ssl.key"),
    ({"kibana_api_key": "a:b"}, "kibana_api_key"),
    ({"otel_exporter_config_yaml": "headers:\n  Authorization: Bearer x"},
     "otel_exporter_config_yaml"),
])
def test_secret_keys_with_values_are_caught(payload, key):
    assert any(f"`{key}`" in h for h in scan.scan_payload(payload, "content[0]"))


def test_the_test_key_is_caught_encoded_or_its_secret_half(monkeypatch):
    secret = "Zx9secretsecretsecret0"
    key = base64.b64encode(f"key-id-123:{secret}".encode()).decode()
    monkeypatch.setenv("KIBANA_TEST_API_KEY", key)
    assert scan.scan_text(f"ApiKey {key}", "content[0]")
    assert scan.scan_text(f"secret={secret}", "content[0]")
    assert scan.scan_text("created by key-id-123", "content[0]") == []


def _server(payload):
    server = FastMCP("leaky")

    @server.tool
    def leak() -> dict:
        return payload

    @server.tool
    def fail() -> dict:
        raise ValueError(f"bad output {payload['service_token']}")

    @server.tool
    def fine() -> dict:
        return {"id": "x", "name": "ok"}

    return server


async def test_the_client_hook_fails_a_leaking_call_and_records_it(monkeypatch):
    leaks = scan.install(monkeypatch)
    async with Client(_server(dict(_OUTPUT))) as client:
        assert (await client.call_tool("fine", {})).data == {"id": "x", "name": "ok"}
        with pytest.raises(scan.SecretInToolResult, match="tool 'leak' returned secret material"):
            await client.call_tool("leak", {})
        with pytest.raises(scan.SecretInToolResult, match="tool 'fail'.*canary"):
            await client.call_tool("fail", {}, raise_on_error=False)
    assert len(leaks) == 2 and all(scan.FIX in leak for leak in leaks)
