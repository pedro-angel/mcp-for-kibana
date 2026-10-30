"""Convert a passing Claude live-model run into a replay transcript.

    uv run python tests/e2e_replay/record_claude_turn.py \
        scripts/experiment/runs/claude-e2e/<block>/<run_id>

The run directory is one `make test-e2e` run (tests/e2e/test_claude_live.py):
`record.json` and the key-redacted stream-json `transcript.jsonl`. Only runs of
the per-toolbox tasks (tests/_toolbox_tasks.py, D61) are accepted, and only
passing ones: the replay tier scores the replayed turn with the same task
ladder, so a failed run would record a turn the replay must fail.

Provenance: the step ORDER and every argument are the model's own (the
`tool_use` blocks of the transcript), and each `observed_output` is the
server's verbatim answer (the matching `tool_result`). Three elements are
authored, by construction, and the transcript says so:

- the needles: each is a string the model passed that the recorded body
  echoes, or a top-level key of the recorded JSON body (`"id":`), each
  verified present before it is written; an error step adds the body's first
  clause, the guidance the model read;
- the bindings: an argument that equals a value an earlier step returned
  (a server-generated id: a case, an agent policy, a parameter) is bound at
  replay time to the value the same step returns live, by its JSON path;
- the `schema_rejected` entry: the first step whose tool has a required
  input, with that input removed (or, when no step's tool has one, with an
  unknown property added — every input schema forbids extra properties).

The run's 8 hex marker digits are the one fresh-per-run token
(`hex_token`): the replay substitutes them everywhere, so the marker, the
stream name and the parameter key are fresh on every replay.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from fastmcp import Client  # noqa: E402

from kibana_mcp.config import Settings, Tier  # noqa: E402
from kibana_mcp.server import build_server  # noqa: E402
from tests._toolbox_tasks import TOOLBOX_TASKS, marker_hex  # noqa: E402
from tests.fakes import FakeGateway  # noqa: E402

PREFIX = "mcp__mcp-for-kibana__"
_TRANSCRIPTS = Path(__file__).parent / "transcripts"


def _events(path):
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict))


def calls(events):
    """(tool, arguments, body, is_error) per server call, in call order."""
    uses, results = [], {}
    for e in events:
        for b in (e.get("message") or {}).get("content", []) or []:
            if not isinstance(b, dict):
                continue
            if e.get("type") == "assistant" and b.get("type") == "tool_use":
                uses.append(b)
            elif e.get("type") == "user" and b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = b
    out = []
    for u in uses:
        name = u.get("name", "")
        if not name.startswith(PREFIX):
            raise SystemExit(f"call to a non-server tool {name!r} — not a valid run")
        r = results.get(u.get("id"))
        if r is None:
            raise SystemExit(f"no tool_result for {name} ({u.get('id')})")
        out.append((name.removeprefix(PREFIX), u.get("input") or {}, _text(r.get("content")),
                    bool(r.get("is_error"))))
    return out


def _leaves(value, path=()):
    """Every (path, string) leaf of a JSON value."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, (*path, k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, (*path, i))


def _json(body):
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


def bindings(steps, prompt, hex_token):
    """Arguments equal to a value an earlier step returned, not given by the
    prompt and not carrying the fresh hex: bound by (step, JSON path)."""
    found = {}
    for j, step in enumerate(steps):
        for _, value in _leaves(step["arguments"]):
            if value in found or len(value) < 6 or hex_token in value or value in prompt:
                continue
            for i in range(j - 1, -1, -1):
                hit = next(
                    (p for p, v in _leaves(_json(steps[i]["observed_output"])) if v == value),
                    None,
                )
                if hit is not None:
                    found[value] = {"value": value, "step": i, "path": list(hit)}
                    break
    return list(found.values())


def needles(args, body, is_error):
    out = []
    for _, value in _leaves(args):
        if len(value) >= 4 and value in body and value not in out:
            out.append(value)
    parsed = _json(body)
    if isinstance(parsed, dict):
        out += [f'"{k}":' for k in parsed if f'"{k}":' in body]
    if is_error:
        clause = body.split(":", 1)[0].strip()
        if clause and clause not in out:
            out.append(clause)
    return out


async def _schemas(toolboxes):
    settings = Settings(toolboxes=toolboxes.split(","), tier=Tier("write"))
    async with Client(build_server(settings, lambda space=None: FakeGateway())) as client:
        return {t.name: t.inputSchema for t in await client.list_tools()}


def schema_rejected(steps, schemas):
    for i, step in enumerate(steps):
        required = schemas[step["tool"]].get("required") or []
        if required:
            args = {k: v for k, v in step["arguments"].items() if k != required[0]}
            return {
                "tool": step["tool"],
                "arguments": args,
                "runtime_reason": (
                    f"authored: step {i}'s recorded arguments with the required "
                    f"'{required[0]}' removed"
                ),
            }
    step = steps[0]
    return {
        "tool": step["tool"],
        "arguments": {**step["arguments"], "unexpected_property": True},
        "runtime_reason": (
            "authored: step 0's recorded arguments plus a property the schema "
            "does not declare (additionalProperties is false)"
        ),
    }


def convert(run_dir):
    run_dir = Path(run_dir)
    record = json.loads((run_dir / "record.json").read_text())
    task = TOOLBOX_TASKS.get(record.get("task"))
    if task is None or not task.replayed:
        raise SystemExit(f"task {record.get('task')!r} is not a replayed per-toolbox task")
    if not (record.get("passed") and record.get("valid")):
        raise SystemExit("the run did not pass (or is invalid) — pick a passing run")
    marker, prompt = record["marker"], record["prompt"]
    hex_token = marker_hex(marker)
    schemas = asyncio.run(_schemas(task.toolboxes))
    steps = []
    for i, (tool, args, body, is_error) in enumerate(calls(_events(run_dir / "transcript.jsonl"))):
        steps.append({
            "tool": tool,
            "arguments": args,
            "observed_output": body,
            "expect": {
                "is_error": is_error,
                "contains": needles(args, body, is_error),
                "gates": (
                    f"step {i} of a passing {task.name} run: "
                    + ("it still fails with the guidance the model read" if is_error
                       else "it still succeeds and echoes what the model passed")
                ),
            },
        })
    if not steps:
        raise SystemExit("the run made no server call")
    try:
        rel = run_dir.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        raise SystemExit(
            f"{run_dir} is not under the repository: record from a committed counted "
            "block (scripts/experiment/runs/claude-e2e/<block>/<run_id>)"
        ) from None
    transcript = {
        "recorded": {
            "at": datetime.now(UTC).date().isoformat(),
            "by": "tests/e2e_replay/record_claude_turn.py",
            "model": ", ".join(record.get("models_reported") or [record.get("model_requested")]),
            "runtime": (
                f"claude -p (Claude Code {record.get('claude_cli')}) -> uv run mcp-for-kibana "
                f"(KIBANA_MCP_TIER=write, KIBANA_MCP_TOOLBOXES={task.toolboxes})"
            ),
            "stack": f"elastic-start-local, Kibana {record.get('kibana_version')}",
            "source": f"{rel} (committed run record, git {record.get('git_sha')})",
            "note": (
                "REAL model turn from a committed run record: call order and arguments are the "
                "model's own, observed_output the server's verbatim answer. Authored "
                "by construction: the needles (verified present in the recorded "
                "body), the bindings and the schema_rejected entry."
            ),
        },
        "task": task.name,
        "toolboxes": task.toolboxes,
        "prompt": prompt,
        "marker_token": marker,
        "hex_token": hex_token,
        "bindings": bindings(steps, prompt, hex_token),
        "schema_rejected": schema_rejected(steps, schemas),
        "steps": steps,
    }
    out = _TRANSCRIPTS / f"{task.name.replace('_', '-')}-{task.toolbox}.json"
    out.write_text(json.dumps(transcript, indent=1) + "\n")
    return out


if __name__ == "__main__":
    print(f"wrote {convert(sys.argv[1])}")
