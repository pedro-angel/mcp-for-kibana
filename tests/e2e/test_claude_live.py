"""The live-model tier, driven by Claude: `make test-e2e`.

Each run sends one task prompt to a headless Claude Code (`claude -p`) whose
only tools are this server's MCP tools (tests/_claude_cli.py), then scores
Kibana's state — never the model's text — with the ladder the task's
definition names (tests/_live_tasks.py). Four tasks: the three the LM Studio
tier (`make test-e2e-local`) has always run, with the same prompts and the
same scoring, and `fix_rule_threshold`, a maintenance task for the in-place
update tools. Tasks, pass criteria and N=5 runs per task were pre-registered in
scripts/experiment/CLAUDE_E2E.md before any counted run.

Needs Claude sign-in for `claude -p` (no API key is required) and a seeded
stack. Off by default and outside the Definition-of-Done gate (D31, D38).

    CLAUDE_E2E_MODEL       model or alias passed to --model (default: haiku)
    CLAUDE_E2E_RUNS        runs per task (default: 5, the pre-registered N)
    CLAUDE_E2E_BUDGET_USD  stop for the session's cumulative cost (default: 5)
    CLAUDE_E2E_OUT         run-record directory
                           (default: scripts/experiment/runs/claude-e2e/adhoc)

Every run writes prompt.txt, transcript.jsonl (stream-json, key-redacted),
record.json and, when non-empty, stderr.txt to its own directory under
CLAUDE_E2E_OUT, and appends its record to CLAUDE_E2E_OUT/runs.jsonl. Reports
are generated from those records (scripts/experiment/claude_e2e_report.py),
never hand-transcribed.
"""

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.errors import KibanaNotFound
from tests import _claude_cli as cli
from tests import _live_tasks as tasks
from tests._alerting_space_ladder import (
    _marker_rules,
    score_alerting_space_run,
    sweep_default_space_marker_rules,
)
from tests._space_ladder import (
    append_jsonl,
    default_space_data_view_ids,
    score_space_run,
    sweep_new_default_space_data_views,
)

pytestmark = pytest.mark.e2e_claude

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUT = _REPO_ROOT / "scripts" / "experiment" / "runs" / "claude-e2e" / "adhoc"
TASKS = ("flights_dashboard", "space_dashboard", "alerting_space", "fix_rule_threshold")

_SPENT = {"usd": 0.0}


def pytest_generate_tests(metafunc):
    if {"task", "run_index"} <= set(metafunc.fixturenames):
        runs = range(1, cli.runs_per_task() + 1)
        metafunc.parametrize(
            ("task", "run_index"),
            [(t, i) for t in TASKS for i in runs],
            ids=[f"{t}-{i}" for t in TASKS for i in runs],
        )


@pytest.fixture(scope="session")
def creds():
    return os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"]


@pytest.fixture(scope="session")
def root_gateway(creds):
    with KibanaPyGateway.connect(*creds) as gw:
        yield gw


@pytest.fixture(scope="session")
def session_info(root_gateway):
    def run(*args):
        return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()

    try:
        kibana_version = root_gateway.get_kibana_status().version
    except Exception as e:  # metadata, never a reason to lose the run
        kibana_version = f"unknown ({type(e).__name__})"
    return {
        "claude_cli": run("claude", "--version"),
        "git_sha": run("git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"),
        "server_dirty": bool(run("git", "-C", str(_REPO_ROOT), "status", "--porcelain", "--", "src")),
        "kibana_version": kibana_version,
    }


@pytest.fixture(scope="session")
def out_dir():
    return Path(os.environ.get("CLAUDE_E2E_OUT") or _DEFAULT_OUT)


def _slug(model):
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")[:30]


class _Task:
    """One run's task: prompt, server toolboxes, scoring and cleanup."""

    def __init__(self, name, root, creds, model):
        self.name, self.root, self.creds = name, root, creds
        self.marker = f"E2E {uuid.uuid4().hex[:8]}"
        self.space_id = None
        self.seed_before = None
        self.dv_before = set()
        if name == "flights_dashboard":
            self.toolboxes = tasks.DASHBOARDS_TOOLBOXES
            self.prompt = tasks.flights_dashboard_prompt(self.marker)
        elif name == "space_dashboard":
            self.toolboxes = tasks.DASHBOARDS_TOOLBOXES
            self.space_id = f"e2e-{_slug(model)}-{uuid.uuid4().hex[:8]}"
            self.prompt = tasks.space_dashboard_prompt(self.space_id, self.marker)
            # Snapshot BEFORE the model runs: the diff-based residue sweep must
            # not count the model's own strays as pre-existing.
            self.dv_before = default_space_data_view_ids(root)
        elif name == "alerting_space":
            self.toolboxes = tasks.ALERTING_TOOLBOXES
            self.space_id = f"e2e-{_slug(model)}-{uuid.uuid4().hex[:8]}"
            self.prompt = tasks.alerting_space_prompt(self.space_id, self.marker)
        elif name == "fix_rule_threshold":
            self.toolboxes = tasks.ALERTING_TOOLBOXES
            self.prompt = tasks.fix_rule_threshold_prompt(self.marker)
        else:
            raise ValueError(f"unknown task {name!r}")

    def seed(self):
        """The broken state a maintenance task starts from, read back as Kibana
        stored it (Kibana adds defaults to a rule's params)."""
        if self.name == "fix_rule_threshold":
            rule = self.root.create_alert_rule(**tasks.fix_rule_seed_args(self.marker))
            self.seed_before = tasks.rule_snapshot(self._rule_body(rule.id))
            return {"seeded_rule_id": rule.id}
        return {}

    def _rule_body(self, rule_id):
        try:
            self.root.get_alert_rule(rule_id)
        except KibanaNotFound:
            return None
        # Scored from Kibana's raw body, not from the get_alert_rule mapping the
        # model reads: the scorer must not depend on the code under test.
        return self.root._client.alerting.rule.get(id=rule_id).body

    def score(self):
        if self.name == "flights_dashboard":
            return tasks.score_flights_dashboard_run(self.root, self.marker)
        if self.name == "fix_rule_threshold":
            body = self._rule_body(self.seed_before["id"])
            after = tasks.rule_snapshot(body) if body is not None else None
            ids = [r.id for r in _marker_rules(self.root, self.marker)]
            return {
                **tasks.score_fix_rule_run(self.seed_before, after, ids),
                "rule_after": after,
            }
        scoped = None
        try:
            scoped = KibanaPyGateway.connect(*self.creds, self.space_id)
        except KibanaNotFound:
            pass  # S1 will score False from the root gateway's own check
        try:
            scorer = (
                score_space_run if self.name == "space_dashboard" else score_alerting_space_run
            )
            return scorer(self.root, scoped, self.space_id, self.marker)
        finally:
            if scoped is not None:
                scoped.__exit__(None, None, None)

    def cleanup(self):
        """Delete only what this run created or seeded: its space, and
        marker-bound objects in the default space."""
        if self.space_id:
            try:
                self.root.delete_space(self.space_id, force=True)
            except KibanaNotFound:
                pass
        for d in self.root.search_dashboards(self.marker):
            self.root.delete_dashboard(d.id)
        sweep_default_space_marker_rules(self.root, self.marker)
        if self.name == "space_dashboard":
            sweep_new_default_space_data_views(self.root, self.dv_before)


def test_claude_completes_task(task, run_index, creds, root_gateway, session_info, out_dir):
    budget = cli.budget_usd()
    if _SPENT["usd"] >= budget:
        pytest.fail(
            f"cumulative cost ${_SPENT['usd']:.4f} reached CLAUDE_E2E_BUDGET_USD={budget:g}; "
            "this run was not started — raise the budget or lower CLAUDE_E2E_RUNS"
        )
    model = cli.claude_model()
    kibana_url, api_key = creds
    t = _Task(task, root_gateway, creds, model)
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{task}-{run_index}-{uuid.uuid4().hex[:6]}"
    run_dir = out_dir / run_id
    record = {
        "study": "claude-e2e",
        "block": out_dir.name,
        "run_id": run_id,
        "task": task,
        "run_index": run_index,
        "model_requested": model,
        "marker": t.marker,
        "space": t.space_id,
        "toolboxes": t.toolboxes,
        "tier": "write",
        "prompt": t.prompt,
        "ts_start": datetime.now(UTC).isoformat(),
        **session_info,
    }
    started = time.monotonic()
    transcript = stderr = ""
    try:
        record.update(t.seed())
        with tempfile.TemporaryDirectory(prefix="claude-e2e-") as tmpdir:
            tmp = Path(tmpdir)
            workdir = tmp / "work"
            workdir.mkdir()
            cfg = cli.write_mcp_config(
                tmp / "mcp.json", _REPO_ROOT, kibana_url, api_key, t.toolboxes
            )
            metrics, transcript, stderr, leaked, timed_out = cli.run_claude(
                t.prompt, model, workdir, cfg, api_key
            )
        record.update(metrics)
        record["timed_out"] = timed_out
        record["key_in_transcript"] = leaked
        _SPENT["usd"] += metrics.get("total_cost_usd") or 0.0
        record["session_cost_usd"] = round(_SPENT["usd"], 6)
        record.update(t.score())
    except Exception as e:  # a harness fault is data, never a lost record
        record["harness_error"] = repr(e)
        record.setdefault("passed", False)
    finally:
        try:
            t.cleanup()
        except Exception as e:
            record["cleanup_error"] = repr(e)
        record["valid"] = (
            not record.get("harness_error")
            and record.get("key_in_transcript") is False
            and not record.get("non_server_tools")
            and record.get("permission_denials") == 0
            and (record.get("has_result") or record.get("timed_out"))
        )
        record["wall_s"] = round(time.monotonic() - started, 1)
        record["ts_end"] = datetime.now(UTC).isoformat()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "prompt.txt").write_text(t.prompt + "\n")
        if transcript and not transcript.endswith("\n"):
            transcript += "\n"
        (run_dir / "transcript.jsonl").write_text(transcript)
        if stderr.strip():
            (run_dir / "stderr.txt").write_text(stderr if stderr.endswith("\n") else stderr + "\n")
        (run_dir / "record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        append_jsonl(out_dir / "runs.jsonl", record)

    assert record.get("key_in_transcript") is False, (
        "the Kibana API key appeared in the run's output (redacted on disk); "
        "find how it reached the model before trusting any result"
    )
    assert not record.get("harness_error"), f"harness error: {record['harness_error']}"
    assert not record.get("non_server_tools"), (
        f"the model was offered tools besides this server's: {record['non_server_tools']}"
    )
    assert record.get("permission_denials") == 0, (
        f"{record.get('permission_denials')} permission denial(s): see {run_dir}/transcript.jsonl"
    )
    assert record.get("passed"), (
        f"{task} failed at rung {record.get('first_missing')}; tool calls: "
        f"{record.get('tool_calls')}; transcript: {run_dir}/transcript.jsonl"
    )
