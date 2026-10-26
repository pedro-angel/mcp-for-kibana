"""The DoD gate binds its verdict to a tree (scripts/checks/definition-of-done.sh, D55).

`VERDICT: GO` used to name no commit and no tree: uncommitted and untracked
files were certified with the rest, an edit saved mid-run split the criteria
across two trees, and the logs of earlier runs stayed beside the new ones. The
gate now computes a content tree id (a temporary index, `git add -A`, `git
write-tree`) at start and at end, prints HEAD, the tree id and clean/dirty in
its header and on the VERDICT line, is NO-GO when the tree changed during the
run, clears its log dir at start and writes `verdict.json` there.

The gate runs here on a fixture repo whose config marks every criterion `n/a`
except `changelog_entry`, served by a stub script the tests control.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "definition-of-done.sh"
_FIX_TREE = "commit or stash"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _known() -> list[str]:
    text = _GATE.read_text()
    m = re.search(r'^known="([^"]*)"', text, re.M)
    assert m, "the gate's known= list moved"
    return m.group(1).replace("\\\n", " ").split()


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path):
    """A committed fixture repo holding the gate, a stub changelog check and t.config."""
    root = tmp_path / f"dodfix-{uuid.uuid4().hex[:12]}"
    (root / "scripts" / "checks").mkdir(parents=True)
    shutil.copy(_GATE, root / "scripts" / "checks" / "definition-of-done.sh")
    _stub(root, "echo 'stub ok'")
    cfg = "\n".join(
        f"{c} = {'required' if c == 'changelog_entry' else 'n/a'}" for c in _known()
    )
    (root / "t.config").write_text(cfg + "\n")
    (root / "app.py").write_text("x = 1\n")
    (root / ".gitignore").write_text("ignored/\n")
    _git(root, "init", "-q", "-b", "work")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    yield root
    shutil.rmtree(Path("/tmp") / f"dod-{root.name}", ignore_errors=True)


def _stub(root: Path, body: str) -> None:
    p = root / "scripts" / "checks" / "changelog-entry.sh"
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)


def _logdir(root: Path) -> Path:
    return Path("/tmp") / f"dod-{root.name}"


def _gate(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "scripts/checks/definition-of-done.sh", "t.config"],
        cwd=root,
        capture_output=True,
        text=True,
    )


def _verdict_json(root: Path) -> dict:
    return json.loads((_logdir(root) / "verdict.json").read_text())


def _verdict_line(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.startswith("VERDICT:")]
    assert len(lines) == 1, out
    return lines[0]


# --- a GO names the commit and the tree it certified --------------------------


def test_a_clean_go_names_head_and_the_tree_in_the_header_and_verdict(repo):
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    header = res.stdout.splitlines()[1]
    assert head in header and tree in header and "clean" in header
    line = _verdict_line(res.stdout)
    assert line.startswith("VERDICT: GO")
    assert head[:12] in line and tree in line and "clean" in line


def test_verdict_json_records_the_run(repo):
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    v = _verdict_json(repo)
    assert v["verdict"] == "GO"
    assert v["head"] == _git(repo, "rev-parse", "HEAD")
    assert v["tree"] == v["tree_at_end"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert v["dirty"] is False
    assert v["config"] == "t.config"
    assert v["config_sha256"] == hashlib.sha256((repo / "t.config").read_bytes()).hexdigest()
    assert v["criteria"]["changelog_entry"] == "GO"
    assert all(v["criteria"][c] == "n/a" for c in _known() if c != "changelog_entry")
    assert v["kibana_version"] is None and v["claude_model"] is None
    iso = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"
    assert re.match(iso, v["started_at"]) and re.match(iso, v["finished_at"])


def test_a_no_go_criterion_is_recorded_in_verdict_json(repo):
    _stub(repo, "echo 'stub refuses'; exit 1")
    _git(repo, "commit", "-qam", "refusing stub")
    res = _gate(repo)
    assert res.returncode == 1
    v = _verdict_json(repo)
    assert v["verdict"] == "NO-GO" and v["criteria"]["changelog_entry"] == "NO-GO"


# --- uncommitted work is visible, and certified as itself ---------------------


def test_an_untracked_file_makes_the_tree_dirty_and_changes_the_tree_id(repo):
    (repo / "helper.py").write_text("y = 1\n")
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    v = _verdict_json(repo)
    assert v["dirty"] is True
    assert v["tree"] != _git(repo, "rev-parse", "HEAD^{tree}")
    assert "dirty" in res.stdout.splitlines()[1] and "dirty" in _verdict_line(res.stdout)
    # Committing exactly the certified content reproduces the recorded tree id,
    # which is how a GO claim is checked (AGENTS.md).
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "helper")
    assert _git(repo, "rev-parse", "HEAD^{tree}") == v["tree"]


def test_the_real_index_is_left_untouched(repo):
    (repo / "helper.py").write_text("y = 1\n")
    (repo / "app.py").write_text("x = 2\n")
    before = _git(repo, "status", "--porcelain")
    assert _gate(repo).returncode == 0
    assert _git(repo, "status", "--porcelain") == before
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_an_ignored_file_does_not_change_the_tree(repo):
    (repo / "ignored").mkdir()
    (repo / "ignored" / "x.txt").write_text("scratch\n")
    assert _gate(repo).returncode == 0
    assert _verdict_json(repo)["dirty"] is False


def test_a_racily_clean_edit_is_in_the_tree(repo):
    # A same-size edit whose mtime equals the index file's own mtime is only
    # caught by git's racy-clean check, which compares against the index
    # file's mtime: a copy with a fresh mtime would hide it (review of H3).
    _git(repo, "config", "core.trustctime", "false")
    app, index = repo / "app.py", repo / ".git" / "index"
    t0 = (1_700_000_000, 1_700_000_000)
    os.utime(app, t0)
    _git(repo, "update-index", "--refresh")  # entry records t0, not smudged
    app.write_text("x = 2\n")  # same size as "x = 1\n"
    os.utime(app, t0)
    os.utime(index, t0)  # the index file is as old as the entry: racily clean
    # GIT_OPTIONAL_LOCKS=0: a plain status would rewrite the index and smudge the entry.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    assert _git(repo, "status", "--porcelain", env=env) == "M app.py"
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    v = _verdict_json(repo)
    assert v["dirty"] is True, res.stdout
    assert v["tree"] != _git(repo, "rev-parse", "HEAD^{tree}")


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_an_edit_to_a_file_git_is_told_to_ignore_is_in_the_tree(repo, flag):
    # `git add -A` never rehashes an entry with either bit, so the gate clears
    # them in its temporary index (review of H3); the real index keeps them.
    _git(repo, "update-index", flag, "app.py")
    assert _gate(repo).returncode == 0
    assert _verdict_json(repo)["dirty"] is False
    (repo / "app.py").write_text("x = 'edited'\n")
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    v = _verdict_json(repo)
    assert v["dirty"] is True, res.stdout
    tag = _git(repo, "ls-files", "-v", "app.py")[0]
    assert tag == ("S" if flag == "--skip-worktree" else "h")


def test_an_absent_skip_worktree_file_is_not_recorded_as_deleted(repo):
    # Sparse checkout: a skip-worktree entry with no file on disk is not a change.
    _git(repo, "update-index", "--skip-worktree", "app.py")
    (repo / "app.py").unlink()
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    assert _verdict_json(repo)["dirty"] is False


def test_an_absent_skip_worktree_file_with_a_quoted_name_is_not_recorded_as_deleted(repo):
    # git C-quotes a name holding a quote or backslash; the presence check
    # must look for the real name, not the quoted one (review of H3).
    name = 'a"b\\c.py'
    (repo / name).write_text("y = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "quoted name")
    _git(repo, "update-index", "--skip-worktree", name)
    (repo / name).unlink()
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    v = _verdict_json(repo)
    assert v["dirty"] is False, res.stdout
    assert v["tree"] == _git(repo, "rev-parse", "HEAD^{tree}")


@pytest.mark.parametrize("name", ["app.py", 'a"b\\c.py'])
def test_an_absent_assume_unchanged_file_is_recorded_as_deleted(repo, name):
    # Only skip-worktree has the sparse-checkout reason to keep its bit; an
    # assume-unchanged file gone from disk is a deletion (review of H3).
    if name != "app.py":
        (repo / name).write_text("y = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "quoted name")
    _git(repo, "update-index", "--assume-unchanged", name)
    (repo / name).unlink()
    res = _gate(repo)
    assert res.returncode == 0, res.stdout
    v = _verdict_json(repo)
    assert v["dirty"] is True, res.stdout
    assert v["tree"] != _git(repo, "rev-parse", "HEAD^{tree}")


# --- the defect class: the tree changes during the run ------------------------


def test_a_tree_changed_mid_run_is_no_go_naming_the_fix(repo):
    _stub(repo, "echo 'y = 2' > edited_mid_run.py; echo 'stub ok'")
    _git(repo, "commit", "-qam", "editing stub")
    res = _gate(repo)
    assert res.returncode == 1, res.stdout
    assert "GO    changelog_entry" in res.stdout
    nogo = [ln for ln in res.stdout.splitlines() if "NO-GO tree_stable" in ln]
    assert nogo and _FIX_TREE in nogo[0] and "make dod" in nogo[0]
    v = _verdict_json(repo)
    assert v["verdict"] == "NO-GO" and v["criteria"]["tree_stable"] == "NO-GO"
    assert v["tree"] != v["tree_at_end"]


# --- logs from an earlier run never sit beside this run's ---------------------


def test_the_log_dir_is_cleared_at_start(repo):
    logdir = _logdir(repo)
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / "contract_green.log").write_text("an old run's log\n")
    assert _gate(repo).returncode == 0
    assert not (logdir / "contract_green.log").exists()
    assert (logdir / "verdict.json").exists()
