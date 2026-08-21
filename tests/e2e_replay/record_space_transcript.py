"""Record `transcripts/space-dashboard.json` against a live stack.

    uv run python tests/e2e_replay/record_space_transcript.py

Why a script and not a hand-written JSON file
---------------------------------------------
The replay tier's value rests on its transcripts being REAL bytes: arguments
that were actually accepted, and response text that was actually returned. A
hand-written transcript proves only that someone typed the strings the test
then looks for. `transcripts/flights-dashboard.json` came out of a real model
run; this one has no model to record, so the recording is done here — by
driving the same real server over the same stdio transport the replay test
uses, capturing each step's verbatim body, and refusing to write anything if a
step did not do what the transcript would claim.

What this recording is, and is not
----------------------------------
The CALL SEQUENCE is authored (create a space, then target it from seven tools
across both toolboxes). No model chose it, so a green replay of it says nothing
about model behaviour — it certifies the space surface, not reasoning. The
arguments, the ids, the links and every `observed_output` are real.

Replay-safety rules this recorder enforces, because a transcript is replayed
with only two substitutions (`marker_token`, `space_token`):

* the dashboard TITLE carries the run marker: saved-object ids are globally
  unique ACROSS spaces (P9), so a fixed title would pin one global id and any
  leaked space or concurrent run would 409. The transcript therefore records
  `dashboard_title` and a `dashboard_id_token` (the recorded derived id); the
  replay substitutes the marker into the title, re-derives the id, and swaps
  the recorded id everywhere it appears;
* URL needles keep the substitutable prefix only (`/s/<space>/app/dashboards
  #/view/`) — the id after it is a hash of the title and would be a false
  invariant if a title ever changed;
* every needle is checked against the body at record time, so a transcript can
  never ship an expectation that did not hold when it was recorded.

The space this recording creates is deleted at the end: the recorder owns it.
"""

import asyncio
import contextlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.dashboards.identity import derive_dashboard_id
from kibana_mcp.core.errors import KibanaNotFound

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT = Path(__file__).parent / "transcripts" / "space-dashboard.json"
# Must match tests/e2e_replay/test_replay.py's `_TRANSCRIPTS` entry for this
# file: the recording and the replay have to boot the same server.
_EXTRA_ENV = {"KIBANA_MCP_TOOLBOXES": "dashboards,data-management,platform-admin"}

_INDEX_PATTERN = "kibana_sample_data_flights"
_DATA_VIEW = "Replay Flights"


def _title(marker: str) -> str:
    # The marker goes INTO the title: ids are globally unique across spaces
    # (P9), so a fixed title would pin one global id and any leaked space or
    # concurrent run would 409 — see the module docstring.
    return f"Replay space flights {marker}"

_PANEL = {
    "title": "Avg Ticket Price by Carrier",
    "chart_type": "bar",
    "data_view": _DATA_VIEW,
    "metrics": [{"agg": "average", "field": "AvgTicketPrice"}],
    "group_by": [{"field": "Carrier", "kind": "terms", "limit": 10}],
}


def _steps(space: str, marker: str) -> list[dict]:
    """The authored call sequence, with the needles each step must prove."""
    link = f"/s/{space}/app/dashboards#/view/"
    echo = f'"space":"{space}"'
    title = _title(marker)
    dashboard_id = derive_dashboard_id(title)
    return [
        {
            "tool": "create_space",
            "arguments": {
                "id": space,
                "name": f"Replay {space}",
                "description": "Ephemeral space for the MCP replay tier",
            },
            "contains": [f'"id":"{space}"'],
            "gates": "the space every later step targets is created under the id given",
        },
        {
            "tool": "create_data_view",
            "arguments": {
                "index_pattern": _INDEX_PATTERN,
                "name": _DATA_VIEW,
                "time_field": "timestamp",
                "space": space,
            },
            "contains": [_INDEX_PATTERN, echo],
            "gates": "a scoped write lands in the chosen space and echoes it back",
        },
        {
            "tool": "create_dashboard",
            "arguments": {
                "title": title,
                "description": f"{marker} replay run",
                "panels": [_PANEL],
                "space": space,
            },
            "contains": [link, '"panel_count":1', '"status":"created"', echo],
            "gates": (
                "the scoped create builds the dashboard, echoes the space, and "
                "returns a space-prefixed deep link a user can open"
            ),
        },
        {
            "tool": "add_esql_xy_panel",
            "arguments": {
                "dashboard_id": dashboard_id,
                "title": "Flights per day",
                "esql": (
                    f"FROM {_INDEX_PATTERN} | STATS flights = COUNT(*) "
                    "BY day = BUCKET(timestamp, 1 day) | SORT day"
                ),
                "x_column": "day",
                "y_columns": ["flights"],
                "chart_type": "line",
                "space": space,
            },
            "contains": [link, echo],
            "gates": (
                "a follow-up write finds the dashboard inside the space and "
                "returns the same space-prefixed link"
            ),
        },
        {
            "tool": "get_dashboard",
            "arguments": {"dashboard_id": dashboard_id, "space": space},
            "contains": [title, marker, echo],
            "gates": "a scoped read sees both panels the scoped writes created",
        },
        {
            "tool": "list_data_views",
            "arguments": {"space": space},
            "contains": [_DATA_VIEW],
            "gates": (
                "a scoped list returns the space's own data views (and, being a "
                "list, carries no space echo)"
            ),
        },
        {
            "tool": "describe_data_view",
            "arguments": {"data_view": _DATA_VIEW, "space": space},
            "contains": ["AvgTicketPrice", "Carrier", echo],
            "gates": "field discovery works inside the space, so a model can chart there",
        },
        {
            "tool": "search_dashboards",
            "arguments": {"query": marker, "space": space},
            "contains": [title, marker],
            "gates": "a scoped search finds what the scoped writes created",
        },
    ]


def _rejected(space_like: str, marker: str) -> dict:
    """A call the live schema must reject: `space` outside `^[a-z0-9_-]+$`.

    Uppercase, deliberately — Python's `re` and JSON-Schema Draft 7 agree on it,
    unlike a trailing-newline case, where `$` in Python matches before a final
    newline and the replay's Draft7Validator would disagree with the server.
    """
    return {
        "tool": "create_dashboard",
        "arguments": {"title": _title(marker), "panels": [_PANEL], "space": space_like},
    }


def _body(result) -> str:
    return "".join(block.text for block in result.content if hasattr(block, "text"))


def _client() -> Client:
    return Client(
        StdioTransport(
            command="uv",
            args=["--directory", str(_REPO_ROOT), "run", "mcp-for-kibana"],
            env={
                **os.environ,
                **_EXTRA_ENV,
                "KIBANA_MCP_ENV_FILE": str(_REPO_ROOT / "elastic-start-local" / ".env.seed"),
                "KIBANA_MCP_TIER": "write",
            },
        )
    )


async def record(space: str, marker: str) -> dict:
    recorded_steps = []
    dashboard_id = derive_dashboard_id(_title(marker))
    async with _client() as client:
        tools = {t.name for t in await client.list_tools()}
        for step in _steps(space, marker):
            name = step["tool"]
            if name not in tools:
                raise SystemExit(
                    f"{name} is not registered — check KIBANA_MCP_TOOLBOXES/TIER "
                    f"({_EXTRA_ENV}); registered: {sorted(tools)}"
                )
            result = await client.call_tool(name, step["arguments"], raise_on_error=False)
            body = _body(result)
            if result.is_error:
                raise SystemExit(f"step {name} failed against the live stack: {body}")
            missing = [needle for needle in step["contains"] if needle not in body]
            if missing:
                raise SystemExit(
                    f"step {name}: needles absent from the real response {missing} — "
                    f"the transcript would encode an expectation that never held. "
                    f"Body: {body}"
                )
            if name == "create_dashboard" and f'"id":"{dashboard_id}"' not in body:
                raise SystemExit(
                    f"the server returned a dashboard id other than {dashboard_id}; "
                    "the replay re-derives this id from the substituted title, so "
                    "a divergent id could never be replayed. Body: " + body
                )
            recorded_steps.append(
                {
                    "tool": name,
                    "arguments": step["arguments"],
                    "observed_output": body,
                    "expect": {
                        "is_error": False,
                        "contains": step["contains"],
                        "gates": step["gates"],
                    },
                }
            )

        rejected = _rejected("REPLAY-UPPER", marker)
        try:
            result = await client.call_tool(
                rejected["tool"], rejected["arguments"], raise_on_error=False
            )
            reason = _body(result)
            accepted = not result.is_error
        except Exception as e:  # a client that raises instead of returning is_error
            reason, accepted = f"{type(e).__name__}: {e}", False
        if accepted:
            raise SystemExit(
                f"{rejected['tool']} ACCEPTED space={rejected['arguments']['space']!r} — "
                "the pattern guard is not live; nothing to record as rejected."
            )
        rejected["runtime_reason"] = reason

    return {
        "recorded": {
            "at": datetime.now(UTC).date().isoformat(),
            "by": "tests/e2e_replay/record_space_transcript.py",
            "model": "none — authored call sequence, no model in the loop",
            "runtime": (
                "fastmcp.Client over stdio -> uv run mcp-for-kibana "
                "(KIBANA_MCP_TIER=write, "
                f"KIBANA_MCP_TOOLBOXES={_EXTRA_ENV['KIBANA_MCP_TOOLBOXES']})"
            ),
            "stack": "elastic-start-local + Kibana Sample Data Flights",
            "note": (
                "Every argument and observed_output is real traffic; the ORDER of the "
                "calls was authored, so a green replay certifies the space-targeted "
                "surface and NOT that a model can choose these calls."
            ),
        },
        "marker_token": marker,
        "space_token": space,
        # P9: the replay re-derives the dashboard id from the substituted
        # title and swaps this recorded id everywhere it appears.
        "dashboard_title": _title(marker),
        "dashboard_id_token": derive_dashboard_id(_title(marker)),
        "schema_rejected": rejected,
        "steps": recorded_steps,
    }


def main() -> None:
    # Imported here, not at module scope: run as a plain script, sys.path[0] is
    # this directory, so the repo root has to go on the path first — and a
    # module-level import after that assignment is exactly what E402 forbids.
    # (The package itself is installed in the venv and needs no such help.)
    sys.path.insert(0, str(_REPO_ROOT))
    from tests._stack_env import load_stack_env

    load_stack_env()
    for key in ("KIBANA_URL", "KIBANA_TEST_API_KEY"):
        if not os.environ.get(key):
            raise SystemExit(
                f"{key} is unset — run: scripts/stack.sh up && scripts/stack.sh seed"
            )
    space = f"replay-{uuid.uuid4().hex[:8]}"
    marker = f"REPLAY {uuid.uuid4().hex[:8]}"
    try:
        transcript = asyncio.run(record(space, marker))
    finally:
        # The recorder created this space, so the recorder removes it: force is
        # required (it is not empty), and KibanaNotFound covers a run that died
        # before create_space landed.
        with (
            contextlib.suppress(KibanaNotFound),
            KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"]
            ) as gw,
        ):
            gw.delete_space(space, force=True)
    _OUT.write_text(json.dumps(transcript, indent=2) + "\n")
    print(f"wrote {_OUT.relative_to(_REPO_ROOT)}: {len(transcript['steps'])} steps, "
          f"space_token={space!r}, marker_token={marker!r}")


if __name__ == "__main__":
    main()
