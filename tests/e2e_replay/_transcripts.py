"""The replay tier's transcripts and the server selection each one needs.

One list, read by the replay test (tests/e2e_replay/test_replay.py) and by
the coverage test (tests/unit/test_replay_coverage.py), which holds it to the
registry: every toolbox has a transcript that calls one of its tools, and
every file under transcripts/ is listed here. A loader-parsed helper, never a
test module.
"""

import json
from pathlib import Path

from kibana_mcp.config import Settings
from tests._toolbox_tasks import TOOLBOX_TASKS

TRANSCRIPT_DIR = Path(__file__).parent / "transcripts"

# (transcript file, extra server env that transcript needs). The space
# transcript's server must expose `create_space`, which lives in the
# platform-admin toolbox — off in the default set, so without this env the
# transcript's first step would fail on a tool that is not even registered.
_FIXED = [
    ("flights-dashboard.json", {}),
    (
        "space-dashboard.json",
        {"KIBANA_MCP_TOOLBOXES": "dashboards,data-management,platform-admin"},
    ),
    (
        # A REAL model turn (call order chosen by the model in a live run) —
        # see the transcript's `recorded` block for provenance.
        "space-dashboard-gemma-4-12b-qat.json",
        {"KIBANA_MCP_TOOLBOXES": "dashboards,data-management,platform-admin"},
    ),
    (
        # A REAL model turn on the alerting-space surface, including the
        # REJECTED first create (error guidance certified).
        "alerting-space-recovery-gemma-4-12b-qat.json",
        {"KIBANA_MCP_TOOLBOXES": "alerting,platform-admin"},
    ),
]


def toolbox_transcript_name(task):
    """The file record_claude_turn.py writes for a per-toolbox task."""
    return f"{task.name.replace('_', '-')}-{task.toolbox}.json"


def _per_toolbox():
    """One REAL Claude turn per per-toolbox task (D61): its server loads the
    toolbox the transcript names, and nothing else."""
    out = []
    for task in TOOLBOX_TASKS.values():
        if task.replayed:
            name = toolbox_transcript_name(task)
            path = TRANSCRIPT_DIR / name
            toolboxes = json.loads(path.read_text())["toolboxes"] if path.exists() else task.toolboxes
            out.append((name, {"KIBANA_MCP_TOOLBOXES": toolboxes}))
    return out


TRANSCRIPTS = _FIXED + _per_toolbox()


def server_toolboxes(extra_env):
    """The toolboxes a transcript's server loads (the server's default pair
    when the transcript selects none)."""
    raw = extra_env.get("KIBANA_MCP_TOOLBOXES")
    if raw is None:
        return list(Settings.model_fields["toolboxes"].default)
    return [b.strip() for b in raw.split(",") if b.strip()]
