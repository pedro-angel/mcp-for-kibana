"""Study C runner for the server-usability study: drive `claude -p` through
one (model, arm) cell and score each run on the pre-registered ladder.

    uv run python scripts/experiment/claude_arm.py \
        --model claude-sonnet-5 --arm with-mcp --runs 5 --block b1

Arms (frozen in the pre-registered experiment design; git history archives it):
    with-mcp           mcp-for-kibana registered, neutral prompt
    no-mcp             no MCP server, same prompt
    with-mcp-directed  registered, prompt opens "Use your tools to ..."

Every run: a fresh empty working directory, a fresh marker and space id, the
frozen task prompt, `--output-format stream-json` for per-event tool counts,
and one JSONL record (metrics + ladder + contamination) appended to --out.
The zero-denial rule is mechanical: `permission_denials` comes from the CLI's
result event; a run with denials is recorded as invalid and must be re-run.

The runner never touches objects it did not create: cleanup deletes the run's
space (force, not-found tolerated) and sweeps only marker-bearing dashboards
from the default space.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway  # noqa: E402
from kibana_mcp.config import _parse_env_file  # noqa: E402
from kibana_mcp.core.errors import KibanaNotFound  # noqa: E402
from tests._space_ladder import (  # noqa: E402
    FLIGHTS_INDEX,
    append_jsonl,
    default_space_data_view_ids,
    score_space_run,
    sweep_new_default_space_data_views,
)

_SEED = _REPO_ROOT / "elastic-start-local" / ".env.seed"
_DEFAULT_OUT = Path(__file__).resolve().parent / "runs" / "study-c.jsonl"
_TIMEOUT_S = 900  # same ceiling the local-model REST arms used


def _task_prompt(marker: str, space_id: str, directed: bool) -> str:
    """The frozen task (redo plan, 'The task'), with Study C's one sentence of
    context. Identical across arms; the directed arm only prepends its
    opener."""
    opener = "Use your tools to do the following. " if directed else ""
    # Deliberately NEUTRAL: the prior study's "failure worth reading twice"
    # showed that spelling out the URL/key/header recipe makes an agent curl
    # past its registered tools. The frozen plan sentence names nothing.
    return (
        f"{opener}A local Kibana is available; its connection details are in "
        "the environment. "
        f"Create a new Kibana space with id '{space_id}'. Inside that space, "
        f"create a data view for the index pattern '{FLIGHTS_INDEX}', then "
        f"create a dashboard titled '{marker} flight prices' with a bar chart "
        "of the average ticket price by carrier over the last 7 days, using "
        "that data view."
    )


def _mcp_config(tmp: Path) -> Path:
    cfg = {
        "mcpServers": {
            "mcp-for-kibana": {
                "command": "uv",
                "args": ["--directory", str(_REPO_ROOT), "run", "mcp-for-kibana"],
                "env": {
                    "KIBANA_MCP_ENV_FILE": str(_SEED),
                    "KIBANA_MCP_TOOLBOXES": "dashboards,data-management,platform-admin",
                    "KIBANA_MCP_TIER": "write",
                },
            }
        }
    }
    path = tmp / "mcp-config.json"
    path.write_text(json.dumps(cfg))
    return path


def _run_once(model: str, arm: str, block: str, out: Path) -> dict:
    seed = _parse_env_file(str(_SEED))
    kibana_url, api_key = seed["KIBANA_URL"], seed["KIBANA_TEST_API_KEY"]
    marker = f"E2E {uuid.uuid4().hex[:8]}"
    space_id = f"e2e-{model.replace('.', '-')}-{uuid.uuid4().hex[:8]}"
    prompt = _task_prompt(marker, space_id, directed=arm == "with-mcp-directed")

    record = {
        "study": "C",
        "run_id": uuid.uuid4().hex,
        "ts_start": datetime.now(UTC).isoformat(),
        "model": model,
        "arm": arm,
        "block": block,
        "marker": marker,
        "space": space_id,
        "prompt": prompt,
        "git_sha": subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False,
        ).stdout.strip(),
    }
    started = time.monotonic()
    # Snapshot BEFORE the agent runs: the diff-based residue sweep must not
    # count the agent's own strays as pre-existing.
    with KibanaPyGateway.connect(kibana_url, api_key) as pre:
        dv_before = default_space_data_view_ids(pre)

    with tempfile.TemporaryDirectory(prefix="study-c-") as tmpdir:
        tmp = Path(tmpdir)
        workdir = tmp / "work"
        workdir.mkdir()
        cmd = [
            "claude", "-p", prompt,
            "--model", model,
            "--output-format", "stream-json",
            "--verbose",  # stream-json in print mode requires it
            "--dangerously-skip-permissions",  # the zero-denial design: no
            # permission policy may distort the arms (plan, Phase 3)
        ]
        if arm != "no-mcp":
            cmd += ["--mcp-config", str(_mcp_config(tmp)), "--strict-mcp-config"]
        env = {
            **os.environ,
            "KIBANA_URL": kibana_url,
            "KIBANA_TEST_API_KEY": api_key,
        }
        try:
            proc = subprocess.run(
                cmd, cwd=workdir, env=env, capture_output=True, text=True,
                timeout=_TIMEOUT_S, check=False,
            )
            events = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            tool_uses = [
                block_
                for e in events
                if e.get("type") == "assistant"
                for block_ in (e.get("message") or {}).get("content", [])
                if isinstance(block_, dict) and block_.get("type") == "tool_use"
            ]
            record["tool_calls"] = [t.get("name", "?") for t in tool_uses]
            record["mcp_calls"] = sum(
                1 for t in tool_uses if t.get("name", "").startswith("mcp__")
            )
            result = next((e for e in events if e.get("type") == "result"), {})
            record["num_turns"] = result.get("num_turns")
            record["total_cost_usd"] = result.get("total_cost_usd")
            record["duration_api_ms"] = result.get("duration_api_ms")
            record["usage"] = result.get("usage")
            record["permission_denials"] = len(result.get("permission_denials") or [])
            record["is_error"] = result.get("is_error", proc.returncode != 0)
            record["valid"] = record["permission_denials"] == 0
        except subprocess.TimeoutExpired:
            record["timeout"] = True
            record["valid"] = True  # a timeout is a real (failed) run, not harness noise

    try:
        with KibanaPyGateway.connect(kibana_url, api_key) as root:
            scoped = None
            try:
                scoped = KibanaPyGateway.connect(kibana_url, api_key, space_id)
            except KibanaNotFound:
                pass
            try:
                record.update(score_space_run(root, scoped, space_id, marker))
            finally:
                if scoped is not None:
                    scoped.__exit__(None, None, None)
            try:
                root.delete_space(space_id, force=True)
            except KibanaNotFound:
                pass
            for d in root.search_dashboards(marker):
                root.delete_dashboard(d.id)
            sweep_new_default_space_data_views(root, dv_before)
    except Exception as e:  # scoring/cleanup trouble is DATA, never a lost record
        record["harness_error"] = repr(e)
    finally:
        record["duration_s"] = round(time.monotonic() - started, 1)
        record["ts_end"] = datetime.now(UTC).isoformat()
        append_jsonl(out, record)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument(
        "--arm", required=True, choices=["with-mcp", "no-mcp", "with-mcp-directed"]
    )
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--block", default="adhoc")
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()

    failures = 0
    for i in range(args.runs):
        r = _run_once(args.model, args.arm, args.block, args.out)
        status = "PASS" if r.get("passed") else f"fail@{r.get('first_missing')}"
        print(
            f"[{i + 1}/{args.runs}] {args.model} {args.arm} {status} "
            f"turns={r.get('num_turns')} cost=${r.get('total_cost_usd')} "
            f"denials={r.get('permission_denials')} {r.get('duration_s')}s",
            flush=True,
        )
        if not r.get("passed"):
            failures += 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
