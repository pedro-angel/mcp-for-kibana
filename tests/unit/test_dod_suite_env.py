"""The DoD gate runs each suite as declared and shows how much of it ran (D56).

`run_suite` counted only `passed` and `skipped`, and the gate cleared only
MAKEFLAGS/MFLAGS, so a `PYTEST_ADDOPTS='-k ... --no-cov'` left in the shell
shrank every suite to a handful of tests, dropped the coverage floor, and still
read `GO`. The gate now clears the variables that change what pytest selects,
loads or enforces (PYTEST_ADDOPTS, PYTEST_PLUGINS, PYTEST_DEBUG,
PYTEST_DISABLE_PLUGIN_AUTOLOAD, COVERAGE_RCFILE) and the replay's
KIBANA_MCP_SERVER_BIN (which swaps the replayed server for an installed
executable), names the ones it cleared,
and prints `N passed / M collected` on every suite line.

The gate runs on a fixture repo whose config requires only `streams_ephemeral`,
served by a stub `scripts/ephemeral_stack.sh` the tests control (no docker:
KIBANA_MCP_DOD_CYCLE_STACK is unset).
"""

import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "definition-of-done.sh"
_CLEARED = (
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DEBUG",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "COVERAGE_RCFILE",
    "KIBANA_MCP_SERVER_BIN",  # swaps the server e2e_replay_green replays
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _known() -> list[str]:
    m = re.search(r'^known="([^"]*)"', _GATE.read_text(), re.M)
    assert m, "the gate's known= list moved"
    return m.group(1).replace("\\\n", " ").split()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _stub(root: Path, body: str) -> None:
    p = root / "scripts" / "ephemeral_stack.sh"
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)


@pytest.fixture
def repo(tmp_path: Path):
    root = tmp_path / f"dodenv-{uuid.uuid4().hex[:12]}"
    (root / "scripts" / "checks").mkdir(parents=True)
    shutil.copy(_GATE, root / "scripts" / "checks" / "definition-of-done.sh")
    _stub(root, "echo '1 passed in 0.01s'")
    cfg = "\n".join(
        f"{c} = {'required' if c == 'streams_ephemeral' else 'n/a'}" for c in _known()
    )
    (root / "t.config").write_text(cfg + "\n")
    # A four-test suite the stub can run with a real pytest.
    (root / "suite").mkdir()
    (root / "suite" / "test_four.py").write_text(
        "".join(f"def test_{n}():\n    pass\n\n\n" for n in ("one", "two", "three", "four"))
    )
    (root / ".gitignore").write_text("seen.env\n.pytest_cache/\n__pycache__/\n")
    _git(root, "init", "-q", "-b", "work")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    yield root
    shutil.rmtree(Path("/tmp") / f"dod-{root.name}", ignore_errors=True)


def _gate(root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    base.pop("KIBANA_MCP_DOD_CYCLE_STACK", None)
    return subprocess.run(
        ["sh", "scripts/checks/definition-of-done.sh", "t.config"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**base, **env},
    )


def _line(out: str, name: str) -> str:
    lines = [ln for ln in out.splitlines() if re.match(rf"^  (GO|NO-GO) +{name}\b", ln)]
    assert len(lines) == 1, out
    return lines[0]


def _commit_stub(root: Path, body: str) -> None:
    _stub(root, body)
    _git(root, "commit", "-qam", "stub")


# --- the environment cannot reshape the suites ---------------------------------


def test_the_gate_clears_the_pytest_and_coverage_variables_before_any_suite(repo):
    _commit_stub(repo, "env > seen.env; echo '1 passed in 0.01s'")
    res = _gate(repo, **{k: "-k nothing" for k in _CLEARED})
    assert res.returncode == 0, res.stdout
    seen = (repo / "seen.env").read_text().splitlines()
    leaked = [ln for ln in seen if ln.split("=", 1)[0] in _CLEARED]
    assert leaked == [], f"the suites saw {leaked}"


def test_the_header_names_the_variables_it_cleared(repo):
    res = _gate(repo, PYTEST_ADDOPTS="--no-cov", COVERAGE_RCFILE="/dev/null")
    assert res.returncode == 0, res.stdout
    note = [ln for ln in res.stdout.splitlines() if "cleared" in ln]
    assert len(note) == 1, res.stdout
    assert "PYTEST_ADDOPTS" in note[0] and "COVERAGE_RCFILE" in note[0]
    assert "PYTEST_PLUGINS" not in note[0]


def test_no_note_when_nothing_was_cleared(repo):
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    assert "cleared" not in res.stdout


def test_a_shell_pytest_addopts_no_longer_shrinks_a_real_suite(repo):
    # The defect itself: '-k one' made a four-test suite run one test, and the
    # gate read GO. Now the whole suite runs and the line says so.
    _commit_stub(repo, f'"{sys.executable}" -m pytest -q -p no:cacheprovider suite')
    res = _gate(repo, PYTEST_ADDOPTS="-k one")
    assert res.returncode == 0, res.stdout
    assert "(4 passed / 4 collected)" in _line(res.stdout, "streams_ephemeral")


# --- the GO line shows how much of the suite ran -------------------------------


def test_the_go_line_shows_passed_over_collected_deselected_included(repo):
    _commit_stub(repo, "echo '3 passed, 1517 deselected, 1 warning in 0.50s'")
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    assert "(3 passed / 1520 collected)" in _line(res.stdout, "streams_ephemeral")


def test_every_outcome_counts_towards_collected(repo):
    _commit_stub(repo, "echo '5 passed, 2 xfailed, 1 xpassed, 4 deselected in 1.00s'")
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    assert "(5 passed / 12 collected)" in _line(res.stdout, "streams_ephemeral")


def test_a_skip_is_still_no_go_and_names_the_counts(repo):
    _commit_stub(repo, "echo '2 passed, 1 skipped, 7 deselected in 0.10s'")
    res = _gate(repo)
    assert res.returncode == 1
    line = _line(res.stdout, "streams_ephemeral")
    assert line.startswith("  NO-GO")
    assert "passed=2 skipped=1" in line and "10 collected" in line


def test_an_all_deselected_suite_is_no_go(repo):
    _commit_stub(repo, "echo '1520 deselected in 0.10s'")
    res = _gate(repo)
    assert res.returncode == 1
    line = _line(res.stdout, "streams_ephemeral")
    assert "passed=0" in line and "1520 collected" in line


# --- the docs say what the cycle flag does -------------------------------------


def test_the_gate_header_says_the_cycle_flag_never_starts_the_stack():
    header = _GATE.read_text().split("set -u", 1)[0]
    assert "KIBANA_MCP_DOD_CYCLE_STACK" in header
    assert "never starts or restarts" in header and "stays stopped" in header


def test_agents_md_says_what_the_cycle_flag_does():
    agents = (Path(__file__).resolve().parents[2] / "AGENTS.md").read_text()
    assert "never starts or restarts" in agents and "stays stopped" in agents
