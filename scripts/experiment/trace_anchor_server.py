"""A tool-less stdio MCP server: the no-mcp arm's trace anchor in live mode.

Claude Code 2.1.296 starts its `claude_code.interaction` span (and joins the
inbound TRACEPARENT) only when its tracer is ready before the first query.
With no MCP server to connect, the first query starts too early: there is no
interaction span, every `llm_request`/`tool` span is the root of its own
trace and the hook falls back to the runner's root (LIVE_RERUN.md, "The
no-mcp trace anchor"). Connecting any MCP server closes the race. This one
offers no tools, prompts or resources and no instructions, so the model's
tool list and prompt are unchanged (first request 5,047 tokens with and
without it in the probe).

Stdlib only, newline-delimited JSON-RPC on stdin/stdout.
"""

from __future__ import annotations

import json
import sys

PROTOCOL = "2025-06-18"


def answer(msg: dict) -> dict | None:
    """The response to one JSON-RPC message; None for notifications."""
    if "id" not in msg:
        return None
    method = msg.get("method")
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result: dict = {
            "protocolVersion": version,
            "capabilities": {},
            "serverInfo": {"name": "trace-anchor", "version": "1"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    elif method == "resources/list":
        result = {"resources": []}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        out = answer(msg) if isinstance(msg, dict) else None
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
