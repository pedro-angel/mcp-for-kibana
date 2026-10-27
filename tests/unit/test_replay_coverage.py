"""Every toolbox is replayed (surface-1, D61).

The replay tier is the model-facing check every gate run and CI run makes:
recorded real-model turns driven through the real server. Before D61 its four
transcripts reached four toolboxes (dashboards, data-management,
platform-admin, alerting), so a renamed tool, a tightened schema or a changed
output in the other six reached users with GO. These tests hold the
transcript list (tests/e2e_replay/_transcripts.py) to the registry: every
toolbox has a transcript whose steps call one of its tools on a server that
loads it, and no transcript file sits outside the list.
"""

import json

from kibana_mcp.toolboxes import TOOLBOXES
from tests._registry import registry
from tests._toolbox_tasks import TOOLBOX_TASKS
from tests.e2e_replay._transcripts import (
    TRANSCRIPT_DIR,
    TRANSCRIPTS,
    server_toolboxes,
    toolbox_transcript_name,
)

_FIX = (
    "add a per-toolbox task to tests/_toolbox_tasks.py, pre-register it in "
    "scripts/experiment/CLAUDE_E2E.md, run it with `make test-e2e` into a committed "
    "block, then record its first passing run with "
    "`uv run python tests/e2e_replay/record_claude_turn.py <run dir>` (D61)"
)


def _load(name):
    return json.loads((TRANSCRIPT_DIR / name).read_text())


def test_every_toolbox_has_a_replayed_transcript():
    tools = registry()
    covered = set()
    for name, extra_env in TRANSCRIPTS:
        if not (TRANSCRIPT_DIR / name).exists():
            continue
        loaded = set(server_toolboxes(extra_env))
        for step in _load(name)["steps"]:
            tool = tools.get(step["tool"])
            if tool is not None and tool.toolbox in loaded:
                covered.add(tool.toolbox)
    missing = sorted(set(TOOLBOXES) - covered)
    assert not missing, (
        f"no replay transcript calls a tool of {missing}, so a renamed tool, a tightened "
        f"schema or a changed output there passes every gate run: {_FIX}"
    )


def test_every_transcript_file_is_listed_and_every_listed_one_exists():
    listed = [name for name, _ in TRANSCRIPTS]
    on_disk = sorted(p.name for p in TRANSCRIPT_DIR.glob("*.json"))
    unlisted = sorted(set(on_disk) - set(listed))
    absent = sorted(set(listed) - set(on_disk))
    assert not unlisted, (
        f"{unlisted} sit in tests/e2e_replay/transcripts/ but the replay never runs them: "
        "list them in tests/e2e_replay/_transcripts.py"
    )
    assert not absent, f"{absent} are listed but missing: {_FIX}"
    assert len(listed) == len(set(listed)), "a transcript is listed twice"


def test_every_step_calls_a_tool_its_server_loads():
    tools = registry()
    wrong = []
    for name, extra_env in TRANSCRIPTS:
        loaded = set(server_toolboxes(extra_env))
        t = _load(name)
        for step in [*t["steps"], t["schema_rejected"]]:
            tool = tools.get(step["tool"])
            if tool is None or tool.toolbox not in loaded:
                wrong.append((name, step["tool"], tool.toolbox if tool else None))
    assert not wrong, (
        f"steps call tools their server does not load (transcript, tool, its toolbox): "
        f"{wrong} — a renamed tool is re-recorded, a moved one needs the transcript's "
        "toolboxes updated"
    )


def test_every_replayed_task_has_its_own_transcript():
    for task in TOOLBOX_TASKS.values():
        name = toolbox_transcript_name(task)
        if not task.replayed:
            assert not (TRANSCRIPT_DIR / name).exists(), f"{name}: {task.name} is not replayed"
            continue
        assert (TRANSCRIPT_DIR / name).exists(), f"no {name} for {task.name}: {_FIX}"
        t = _load(name)
        assert t["task"] == task.name and t["toolboxes"] == task.toolboxes, name
        assert t["hex_token"] in t["marker_token"], name
        assert t["recorded"]["model"] != "none", f"{name} must be a real model turn"
        # The recorded run is a committed record (report numbers trace to one).
        source = t["recorded"]["source"].split(" ", 1)[0]
        assert (TRANSCRIPT_DIR.parents[2] / source / "record.json").exists(), (
            f"{name} cites {source}, which is not a committed run record"
        )


def test_a_task_with_no_state_rung_is_held_by_a_live_needle():
    """A read-only task's ladder is all run rungs, which a replay skips
    (run=None), so the ladder alone would pass any successful call: its
    transcript must pin a value Kibana reports this run (`live_tokens`)."""
    for task in TOOLBOX_TASKS.values():
        if not task.replayed or set(task.rungs) - set(task.run_rungs):
            continue
        t = _load(toolbox_transcript_name(task))
        live = t.get("live_tokens") or {}
        needles = [n for s in t["steps"] for n in s["expect"]["contains"]]
        assert live and any(tok in n for tok in live for n in needles), (
            f"{task.name} has no state rung, so its replay checks nothing beyond "
            "key names: add `live_tokens` to its transcript and a needle carrying "
            "that value (see kibana-status-platform-health.json)"
        )
