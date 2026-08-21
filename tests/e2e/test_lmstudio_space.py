"""Study L gate for the server-usability study: a local LLM in LM Studio
receives the pre-registered space task and must create a Kibana space, a data
view inside it, and a dashboard with a valid chart — through mcp-for-kibana.

The prompt, the pass criterion, and the progress ladder this gate implements
were frozen before any run (the pre-registration document is archived in git
history). The LM Studio server entry must expose
KIBANA_MCP_TOOLBOXES=dashboards,data-management,platform-admin (24 tools at
tier=write) — probe P4b / the plan's dry run.

Every run appends one JSONL record (ladder, tool calls, timings, versions) to
KIBANA_MCP_EXPERIMENT_LOG or scripts/experiment/runs/. Tables in the final
report are generated from these records, never hand-transcribed.
"""

import json
import os
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound
from tests._space_ladder import (
    FLIGHTS_INDEX,
    append_jsonl,
    default_space_data_view_ids,
    score_space_run,
    sweep_new_default_space_data_views,
)

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Ad-hoc by default: dry runs, DoD sweeps, and transcript-recording sessions
# must never mix into study data. The Study L driver pins the study file via
# KIBANA_MCP_EXPERIMENT_LOG explicitly.
_DEFAULT_LOG = _REPO_ROOT / "scripts" / "experiment" / "runs" / "adhoc.jsonl"


@pytest.fixture(scope="session")
def lmstudio():
    return {
        "url": os.environ.get("LMSTUDIO_URL", "http://localhost:1234"),
        # OPTIONAL by design elsewhere; this machine's LM Studio requires it
        # (probe record 2026-08-15) — send it whenever present.
        "token": os.environ.get("LMSTUDIO_API_TOKEN"),
        "model": os.environ.get("LMSTUDIO_MODEL", "openai/gpt-oss-20b"),
    }


@pytest.fixture(scope="session")
def root_gateway():
    url = os.environ["KIBANA_URL"]
    key = os.environ["KIBANA_TEST_API_KEY"]
    with KibanaPyGateway.connect(url, key) as gw:
        yield gw


def _model_slug(model_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:40]


def _git_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()


def test_model_builds_dashboard_in_its_own_space(lmstudio, root_gateway):
    marker = f"E2E {uuid.uuid4().hex[:8]}"
    space_id = f"e2e-{_model_slug(lmstudio['model'])}-{uuid.uuid4().hex[:8]}"
    # The pre-registered prompt (frozen before any run) with the local arm's
    # "Using the kibana tools" opener. The chain and the index pattern are
    # stated deliberately: the study measures execution, not derivation.
    prompt = (
        f"Using the kibana tools, create a new Kibana space with id '{space_id}'. "
        f"Inside that space, create a data view for the index pattern "
        f"'{FLIGHTS_INDEX}', then create a dashboard titled '{marker} flight "
        f"prices' with a bar chart of the average ticket price by carrier over "
        f"the last 7 days, using that data view."
    )
    started = time.monotonic()
    dv_before = default_space_data_view_ids(root_gateway)
    ts_start = datetime.now(UTC).isoformat()
    record = {
        "study": "L",
        "run_id": uuid.uuid4().hex,
        "ts_start": ts_start,
        "model": lmstudio["model"],
        "marker": marker,
        "space": space_id,
        "prompt": prompt,
        "git_sha": _git_sha(),
    }
    try:
        headers = {}
        if lmstudio["token"]:
            headers["Authorization"] = f"Bearer {lmstudio['token']}"
        response = httpx.post(
            f"{lmstudio['url']}/api/v1/chat",
            headers=headers,
            # No context_length: a differing value would spawn a second model
            # instance (measured 2026-08-12; see test_lmstudio.py).
            json={
                "model": lmstudio["model"],
                "input": prompt,
                "integrations": ["mcp/mcp-for-kibana"],
            },
            timeout=600,
        )
        if response.is_error:
            body = response.text
            print(f"LM Studio /api/v1/chat error body: {body}")
            if "insufficient system resources" in body or "Failed to load model" in body:
                pytest.fail(
                    "ENVIRONMENT failure, not a model or mcp-for-kibana defect: "
                    f"LM Studio could not load {lmstudio['model']} for lack of "
                    "memory. Check `lms ps`. Body: " + body
                )
        response.raise_for_status()
        output = response.json()["output"]

        if dump_dir := os.environ.get("KIBANA_MCP_E2E_DUMP"):
            # Full raw output (arguments + bodies per call) — the committed
            # run JSONL keeps tool names only; a replay transcript needs the
            # bytes, so recording is opt-in via this env.
            dump = Path(dump_dir)
            dump.mkdir(parents=True, exist_ok=True)
            (dump / f"{record['run_id']}.json").write_text(json.dumps({
                "model": lmstudio["model"], "marker": marker, "space": space_id,
                "prompt": prompt, "output": output,
            }, indent=1))

        tool_calls = [item for item in output if item.get("type") == "tool_call"]
        invalid = [item for item in output if item.get("type") == "invalid_tool_call"]
        record["tool_calls"] = [c.get("tool", "?") for c in tool_calls]
        record["invalid_tool_calls"] = len(invalid)

        scoped = None
        try:
            scoped = KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], space_id
            )
        except KibanaNotFound:
            pass  # S1 will score False from the root gateway's own check
        try:
            ladder = score_space_run(root_gateway, scoped, space_id, marker)
        finally:
            if scoped is not None:
                scoped.__exit__(None, None, None)
        record.update(ladder)

        assert tool_calls, f"model never called a tool; output: {output}"
        assert ladder["passed"], (
            f"run failed at rung {ladder['first_missing']} "
            f"(contamination={ladder['default_space_contamination']}); "
            f"tool calls: {record['tool_calls']}"
        )
    finally:
        record["duration_s"] = round(time.monotonic() - started, 1)
        record["ts_end"] = datetime.now(UTC).isoformat()
        append_jsonl(os.environ.get("KIBANA_MCP_EXPERIMENT_LOG", _DEFAULT_LOG), record)
        # Unconditional sweep — this run created the space id and the marker,
        # so both deletes bind only to this run's artifacts.
        try:
            root_gateway.delete_space(space_id, force=True)
        except KibanaNotFound:
            pass
        for d in root_gateway.search_dashboards(marker):
            root_gateway.delete_dashboard(d.id)
        sweep_new_default_space_data_views(root_gateway, dv_before)
