"""The efficiency runner's study-4 mode (--task, --blocks): the block plan and
its rotation, the stall alert, resuming a plan, the task arguments, scoring
through study_tasks onto the root span, the streamed transcript, and the
hook judging paths against the runner's work directory. No network, no
Docker, no claude."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "experiment"))

er = importlib.import_module("efficiency_run")
lt = importlib.import_module("live_telemetry")
st = importlib.import_module("study_tasks")
sink = importlib.import_module("otlp_sink")

TASKS = st.task_ids(st.load())
MODELS = list(er.STUDY4_MODELS)
ARMS = list(er.STUDY4_ARMS)


# ---------------------------------------------------------------- plan


def test_block_has_every_cell_once():
    cells = er.block_plan(1, TASKS, MODELS, ARMS)
    assert len(cells) == len(set(cells)) == 4 * 3 * 2
    assert {c[0] for c in cells} == set(TASKS)


def test_rotation_is_balanced_over_twelve_blocks():
    firsts = [er.block_plan(b, TASKS, MODELS, ARMS)[0] for b in range(1, 13)]
    assert Counter(t for t, _, _ in firsts) == {t: 3 for t in TASKS}
    assert Counter(m for _, m, _ in firsts) == {m: 4 for m in MODELS}
    assert Counter(a for _, _, a in firsts) == {a: 6 for a in ARMS}
    # Within a block, the two arms of a model are adjacent, in the block's order.
    b2 = er.block_plan(2, TASKS, MODELS, ARMS)
    assert [a for _, _, a in b2[:2]] == ARMS[::-1]
    assert b2[0][:2] == b2[1][:2]


def test_block_name():
    assert (er.block_name(1), er.block_name(12)) == ("b01", "b12")


def test_done_cells_reads_records(tmp_path):
    run = tmp_path / "b01" / "r1"
    run.mkdir(parents=True)
    rec = {"task": "change", "model_requested": "claude-opus-5-5", "arm": "no-mcp"}
    (run / "record.json").write_text(json.dumps(rec))
    (tmp_path / "b01" / "r2").mkdir()  # no record: the run did not finish
    (tmp_path / "b01" / "r3").mkdir()
    (tmp_path / "b01" / "r3" / "record.json").write_text("{not json")
    assert er.done_cells(tmp_path, "b01") == {("change", "claude-opus-5-5", "no-mcp")}
    assert er.done_cells(tmp_path, "b02") == set()


# ---------------------------------------------------------------- stall alert


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_stall_alerts_once_then_resumes(tmp_path, capsys):
    progress = er.Progress()
    progress.mark("b01 change claude-opus-5-5 no-mcp")
    clock = Clock(progress.t)
    watch = er.StallWatch(tmp_path, 40, progress, clock=clock)
    clock.t += 39 * 60
    assert watch.check() is None
    clock.t += 2 * 60
    assert watch.check() == "stall"
    assert watch.check() is None  # one alert per stall
    progress.mark("b01 done")
    clock.t = progress.t + 1
    assert watch.check() == "resumed"
    lines = [json.loads(x) for x in (tmp_path / "stalls.jsonl").read_text().splitlines()]
    assert [x["event"] for x in lines] == ["stall", "resumed"]
    assert lines[0]["idle_min"] == 41.0 and "change" in lines[0]["doing"]
    err = capsys.readouterr().err
    assert "STALL: no progress for 41.0 min" in err and "RESUMED" in err


def test_a_streaming_transcript_counts_as_progress(tmp_path):
    progress = er.Progress()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n")
    progress.mark("agent", transcript)
    later = progress.t + 600
    os.utime(transcript, (later, later))
    assert progress.last() == (later, "agent")
    transcript.unlink()  # a vanished file falls back to the mark
    assert progress.last()[0] < later


def test_run_streaming_writes_the_transcript_as_it_goes(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    code = "import sys; print('{\"type\": \"system\"}'); print('warn', file=sys.stderr)"
    out, err, timed_out = er.run_streaming(
        [sys.executable, "-c", code], tmp_path, dict(os.environ), transcript
    )
    assert (out, err.strip(), timed_out) == ('{"type": "system"}\n', "warn", False)
    assert transcript.read_text() == out and (tmp_path / "stderr.txt").exists()


def test_run_streaming_times_out_and_drops_empty_stderr(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    out, err, timed_out = er.run_streaming(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, dict(os.environ),
        transcript, timeout=0.5,
    )
    assert (out, err, timed_out) == ("", "", True)
    assert not (tmp_path / "stderr.txt").exists()


# ---------------------------------------------------------------- arguments


def _args(**kw):
    base = {"task": None, "blocks": None, "model": None, "arm": None,
            "arms": ",".join(ARMS)}
    return argparse.Namespace(**{**base, **kw})


def test_task_arguments():
    ap = argparse.ArgumentParser()
    assert er.check_task_args(ap, _args(model="m", arm="no-mcp")) == []
    assert er.check_task_args(ap, _args(task="all", blocks=[1, 1])) == TASKS
    assert er.check_task_args(ap, _args(task="lookup", model="m", arm="with-mcp")) == ["lookup"]


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"blocks": [1, 2]}, "--blocks needs --task"),
        ({"task": "lookup"}, "--model and --arm are required"),
        ({"task": "nope", "blocks": [1, 1]}, "not in tasks.json"),
        ({"task": "all", "model": "m", "arm": "no-mcp"}, "takes one task id"),
        ({"task": "all", "blocks": [3, 2]}, "1 <= FIRST <= LAST"),
        ({"task": "weblogs", "model": "m", "arm": "with-mcp-es"}, "with-mcp-es key"),
    ],
)
def test_task_argument_errors_name_the_fix(kw, message, capsys):
    with pytest.raises(SystemExit):
        er.check_task_args(argparse.ArgumentParser(), _args(**kw))
    assert message in capsys.readouterr().err


def test_task_arms_map_to_tasks_json_preambles():
    cfg = st.load()
    assert set(er.TASK_ARM.values()) <= set(cfg["arms"])
    assert er.TASK_ARM["with-mcp"] == "server" and er.TASK_ARM["no-mcp"] == "api"


# ---------------------------------------------------------------- scoring


def test_score_task_records_outcome_and_root_span_attributes(tmp_path, monkeypatch):
    cfg = st.load()
    state = {
        "task": "lookup", "marker": "S4-1", "dashboards": [], "rules": [],
        "seeded": {"dashboard": {"id": "d", "data": {"title": "Site health",
                                                    "panels": [{}, {}]}},
                   "rule": {"id": "r", "name": "Server errors", "enabled": False,
                            "schedule": {"interval": "1m"},
                            "params": {"threshold": [10]}}},
        "data_views": [], "time_fields": {}, "filters": {},
    }
    state["seed_before"] = state["seeded"]
    monkeypatch.setattr(st.Elastic, "from_stack", classmethod(lambda cls: None))
    monkeypatch.setattr(st, "collect_state", lambda *a, **k: state)
    record = {"final_text": "PANELS: 2\nRULE_NAME: Server errors\nTHRESHOLD: 10\n"
                            "INTERVAL: 1m\nENABLED: yes"}
    attrs = er.score_task(st, cfg, "lookup", None, "S4-1", {}, state["seeded"], record, tmp_path)
    assert record["success"] is False and record["first_failed_step"] == "rule_enabled"
    assert (record["steps_passed"], record["steps_total"]) == (3, 4)
    assert (record["checks_passed"], record["checks_total"]) == (5, 6)
    assert attrs["study.task"] == "lookup"
    assert attrs["study.check.rule_enabled.answer_ENABLED.ok"] is False
    assert attrs["study.check.rule_enabled.answer_ENABLED.expected"] == "no"
    outcome = json.loads((tmp_path / "outcome.json").read_text())
    assert len(outcome["criteria"]) == 6
    assert json.loads((tmp_path / "artifacts.json").read_text())["seeded"]["rule"]["name"] == (
        "Server errors"
    )


def test_root_span_carries_the_task_checks():
    record = {"run_id": "run-1", "success": False, "steps_passed": 2,
              "first_failed_step": "rule", "tasks_sha": "abc"}
    study = {"study.task": "weblogs", "study.check.rule.rule.enabled.ok": False}
    body = lt.runner_spans(record, "a" * 32, "b" * 16, {"agent": (1.0, 2.0)}, "svc",
                           lt.resource_attributes("run-1", "m", "with-mcp", "b01"),
                           extra={"agent.tool.calls": 3, **study})
    root = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: next(iter(a["value"].values())) for a in root["attributes"]}
    assert attrs["study.task"] == "weblogs"
    assert attrs["study.check.rule.rule.enabled.ok"] is False
    assert attrs["study.steps_passed"] == "2" and attrs["study.first_failed_step"] == "rule"
    assert attrs["agent.tool.calls"] == "3"


# ---------------------------------------------------------------- hook work dir


def _run_hook(cfg: Path, payload: dict):
    import subprocess

    return subprocess.run(
        [sys.executable, "-I", "-S", str(REPO / "scripts/experiment/agent_signals_hook.py"),
         str(cfg)],
        input=json.dumps(payload), capture_output=True, text=True,
        env={"PATH": "/usr/bin"}, timeout=30, check=False,
    )


def test_hook_judges_paths_against_the_runner_work_dir(tmp_path):
    workdir = tmp_path / "work"
    (workdir / "sub").mkdir(parents=True)
    tee = tmp_path / "tee.jsonl"
    relay = lt.Relay(tee, lt.Scrubber([]), agent_name="claude-code no-mcp").start()
    try:
        cfg = er.write_hook_config(tmp_path, relay.url, "svc", {"study.run_id": "r"}, "k", workdir)
        assert json.loads(cfg.read_text())["workdir"] == os.path.realpath(workdir)
        # The first call ran `cd sub`: Claude Code's cwd is now the subdirectory.
        payload = {
            "session_id": "s", "cwd": str(workdir / "sub"), "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "tool_use_id": "t1", "duration_ms": 3,
            "tool_input": {"command": f"cat {os.path.realpath(workdir)}/kibana-api/kibana.yaml"},
            "tool_response": {"stdout": "openapi: 3.0.3", "stderr": ""},
        }
        proc = _run_hook(cfg, payload)
        assert (proc.stdout, proc.stderr, proc.returncode) == ("", "", 0)
    finally:
        relay.stop()
    state = json.loads((tmp_path / "agent_hook_state.json").read_text())
    assert state["cwd"] == os.path.realpath(workdir)
    (span,) = [s for s in sink.spans(sink.load(tee)) if s["attrs"].get("gen_ai.tool.call.id")]
    assert span["attrs"]["agent.tool.outside_workdir"] is False


def test_hook_config_without_work_dir_is_unchanged(tmp_path):
    cfg = er.write_hook_config(tmp_path, "http://127.0.0.1:1", "svc", {}, "k")
    assert "workdir" not in json.loads(cfg.read_text())
