"""The secret scan (D59): no tool result may carry a credential.

Every tool result the contract and replay tiers receive through a fastmcp
`Client` passes through `scan_result` (the autouse `secret_scan` fixtures in
tests/contract/conftest.py and tests/e2e_replay/conftest.py patch
`Client.call_tool_mcp`). Under the superuser-snapshot test key Kibana returns
plain credentials from many APIs, so a tool that passes one on is seen by the
live tiers; this is what looks.

A hit is any of:

- a seeded canary value (scripts/canaries/*.json, created by
  `scripts/stack.sh seed`): every string there that starts with
  `CANARY_PREFIX`, anywhere in the result's text or structured content;
- the test key itself (`KIBANA_TEST_API_KEY`, encoded or its secret half);
- a secret-bearing key with a value: the keys the canaries are seeded under
  (`service_token`, `certificate_key`, `proxy_headers`, …) plus the ones
  Kibana and Fleet keep credentials under (`password`, `secrets`, `api_key`,
  `kibana_api_key`, `ssl.key`, …); `config_yaml`-like keys only when their
  text names a credential.

The canary files are the one source: the keys and values are read from them.
"""

import base64
import binascii
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

CANARY_DIR = Path(__file__).resolve().parents[1] / "scripts" / "canaries"
CANARY_PREFIX = "mcp-for-kibana-canary-secret-"
_CANARY_RE = re.compile(re.escape(CANARY_PREFIX) + r"[a-z0-9-]+")

# Keys Kibana and Fleet keep credentials under, beyond the seeded ones.
_KNOWN_SECRET_KEYS = frozenset({
    "password", "passphrase", "key_passphrase", "secrets", "api_key", "kibana_api_key",
    "service_token", "certificate_key", "proxy_headers", "token", "access_token",
    "client_secret", "private_key",
})
# Keys whose value is free text that may hold a credential (Fleet's advanced
# YAML): a hit only when the text names one.
_KNOWN_TEXT_KEYS = frozenset({"config_yaml", "otel_exporter_config_yaml"})
_CREDENTIAL_WORDS = re.compile(
    r"password|passphrase|api_key|apikey|service_token|secret|token|authorization",
    re.IGNORECASE)

FIX = ("never return credentials from a tool: drop the field in the adapter's "
       "summary (keep an allowlist of fields, as list_outputs does) or refuse the "
       "object type (as get_saved_object does), D59")


_SEED_LOOP = re.compile(r"for kind in ([^;]+); do")


def seeded_canaries() -> dict[str, str]:
    """{canary name: Kibana API path} from `scripts/stack.sh seed`'s loop
    (entries `<canary>:<path>`; the seed POSTs scripts/canaries/<canary>.json
    to /api/<path>). Empty when the loop is gone."""
    loop = _SEED_LOOP.search((CANARY_DIR.parents[0] / "stack.sh").read_text())
    if not loop:
        return {}
    return dict(item.split(":", 1) for item in loop.group(1).split())


def _canary_bodies() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(CANARY_DIR.glob("*.json"))]


def _walk(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, (*path, str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, (*path, str(i)))


def canaries() -> dict[str, str]:
    """{canary value: where it is seeded}, from scripts/canaries/*.json."""
    found = {}
    for path in sorted(CANARY_DIR.glob("*.json")):
        for value in _CANARY_RE.findall(path.read_text()):
            found[value] = f"{path.name}"
    return found


def seeded_secret_keys() -> tuple[frozenset[str], frozenset[str]]:
    """(keys whose value IS a canary, keys whose text CONTAINS one), lowercased;
    a key that holds a dict of canaries (proxy_headers) is in the first set
    with the inner key."""
    whole, text = set(), set()
    for body in _canary_bodies():
        for path, value in _walk(body):
            if not path or not isinstance(value, str) or CANARY_PREFIX not in value:
                continue
            if _CANARY_RE.fullmatch(value):
                whole.update(p.lower() for p in path if not p.isdigit())
            else:
                text.add(path[-1].lower())
    return frozenset(whole), frozenset(text)


def _test_key_literals() -> list[str]:
    key = os.environ.get("KIBANA_TEST_API_KEY", "")
    if len(key) < 16:
        return []
    literals = [key]
    try:
        decoded = base64.b64decode(key, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return literals
    _, _, secret = decoded.partition(":")
    if len(secret) >= 16:
        literals.append(secret)
    return literals


def _has_value(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (str, list, dict)):
        return bool(value)
    return True


def scan_payload(payload: Any, where: str) -> list[str]:
    """Hits in one JSON value (a parsed text block or structured content)."""
    whole, text = seeded_secret_keys()
    secret_keys, text_keys = _KNOWN_SECRET_KEYS | whole, _KNOWN_TEXT_KEYS | text
    hits = []
    for path, value in _walk(payload):
        if not path:
            continue
        key, parent = path[-1].lower(), (path[-2].lower() if len(path) > 1 else "")
        dotted = ".".join(path)
        if key in secret_keys and _has_value(value):
            hits.append(f"{where}: secret-bearing key `{dotted}` has a value")
        elif key == "key" and parent == "ssl" and _has_value(value):
            hits.append(f"{where}: secret-bearing key `{dotted}` has a value")
        elif key in text_keys and isinstance(value, str) and _CREDENTIAL_WORDS.search(value):
            hits.append(f"{where}: `{dotted}` holds text naming a credential")
    return hits


def scan_text(text: str, where: str) -> list[str]:
    """Hits for the literal secrets (canaries, the test key) in any text."""
    hits = [f"{where}: the seeded canary from {origin} ({value[:len(CANARY_PREFIX) + 8]}…)"
            for value, origin in canaries().items() if value in text]
    hits += [f"{where}: the test key KIBANA_TEST_API_KEY (or its secret half)"
             for literal in _test_key_literals() if literal in text]
    return hits


def scan_result(result: Any) -> list[str]:
    """Every hit in an MCP CallToolResult: its text blocks (as text, and as
    JSON when they parse) and its structured content."""
    hits = []
    for i, block in enumerate(getattr(result, "content", None) or ()):
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        where = f"content[{i}]"
        hits += scan_text(text, where)
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        hits += scan_payload(parsed, where)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        hits += scan_text(json.dumps(structured), "structured_content")
        hits += scan_payload(structured, "structured_content")
    return hits


class SecretInToolResult(AssertionError):
    pass


def message(tool: str, hits: list[str]) -> str:
    return (f"tool {tool!r} returned secret material: {'; '.join(dict.fromkeys(hits))} — "
            f"{FIX}")


def install(monkeypatch) -> list[str]:
    """Patch `fastmcp.Client.call_tool_mcp` (which `call_tool` uses too) to
    scan every result; a hit raises at the call and is also returned in the
    list, so the fixture fails the test even if the test swallowed the raise."""
    from fastmcp import Client

    original = Client.call_tool_mcp
    leaks: list[str] = []

    async def scanned(self, name, arguments, *args, **kwargs):
        result = await original(self, name, arguments, *args, **kwargs)
        hits = scan_result(result)
        if hits:
            leaks.append(message(name, hits))
            raise SecretInToolResult(leaks[-1])
        return result

    monkeypatch.setattr(Client, "call_tool_mcp", scanned)
    return leaks
