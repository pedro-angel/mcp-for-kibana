"""The DoD gate's changelog check (scripts/checks/changelog-entry.sh, D54).

`changelog_entry` used to grep for a `## [Unreleased]` heading, which every tree
has, so it read GO whether or not the change recorded anything. The check is
now diff-based: paths changed since `git merge-base HEAD origin/main` (falling
back to the latest `v*` tag), untracked files included, that fall outside a
short exemption list require a non-blank line added to CHANGELOG.md under
`## [Unreleased]` (or under a release section the same change adds, which is
how a release cut moves entries). Every refusal names the fix.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "changelog-entry.sh"
_FIX_ENTRY = "add an entry under ## [Unreleased] in CHANGELOG.md"
_FIX_FETCH = "git fetch origin main"

_CHANGELOG = """# Changelog

## [Unreleased]

### Added

- An earlier entry.

## [0.1.0] - 2026-01-01

### Added

- The first release.
"""

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, *, origin_main: bool = True, tag: str | None = None) -> Path:
    """A repo with one base commit; origin/main (and/or a tag) points at it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "work")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "CHANGELOG.md").write_text(_CHANGELOG)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("ignored/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    if origin_main:
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    if tag:
        _git(repo, "tag", tag)
    return repo


def _check(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", str(_SCRIPT)], cwd=repo, capture_output=True, text=True)


def _add_under_unreleased(repo: Path, line: str = "- A new entry.") -> None:
    text = (repo / "CHANGELOG.md").read_text()
    (repo / "CHANGELOG.md").write_text(
        text.replace("- An earlier entry.\n", f"- An earlier entry.\n{line}\n", 1)
    )


def _commit(repo: Path, msg: str = "change") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


# --- the defect class: a change with no entry --------------------------------


def test_a_committed_code_change_without_an_entry_is_refused_naming_the_fix(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    _commit(repo)
    res = _check(repo)
    assert res.returncode == 1
    assert _FIX_ENTRY in res.stdout
    assert "src/app.py" in res.stdout


def test_an_uncommitted_change_without_an_entry_is_refused(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    res = _check(repo)
    assert res.returncode == 1 and _FIX_ENTRY in res.stdout


def test_an_untracked_file_counts_as_a_change(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "new.py").write_text("y = 1\n")
    res = _check(repo)
    assert res.returncode == 1 and "src/new.py" in res.stdout


def test_an_ignored_file_is_not_a_change(tmp_path):
    repo = _repo(tmp_path)
    (repo / "ignored").mkdir()
    (repo / "ignored" / "x.txt").write_text("scratch\n")
    assert _check(repo).returncode == 0


# --- what counts as an entry -------------------------------------------------


def test_a_line_added_under_unreleased_passes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    _add_under_unreleased(repo)
    _commit(repo)
    res = _check(repo)
    assert res.returncode == 0, res.stdout
    assert "1 line" in res.stdout and "[Unreleased]" in res.stdout


def test_an_entry_still_uncommitted_passes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    _commit(repo)
    _add_under_unreleased(repo)
    assert _check(repo).returncode == 0


def test_a_blank_line_under_unreleased_is_not_an_entry(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    _add_under_unreleased(repo, line="")
    res = _check(repo)
    assert res.returncode == 1 and _FIX_ENTRY in res.stdout


def test_a_line_added_under_an_old_release_is_not_an_entry(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    text = (repo / "CHANGELOG.md").read_text()
    (repo / "CHANGELOG.md").write_text(text + "- Smuggled into 0.1.0.\n")
    res = _check(repo)
    assert res.returncode == 1 and _FIX_ENTRY in res.stdout


def test_editing_only_the_changelog_heading_area_is_not_an_entry(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    text = (repo / "CHANGELOG.md").read_text()
    (repo / "CHANGELOG.md").write_text(text.replace("# Changelog\n", "# Changelog\n\nIntro.\n", 1))
    assert _check(repo).returncode == 1


def test_a_release_cut_moving_entries_into_a_new_section_passes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "app.py").write_text("x = 2\n")
    text = (repo / "CHANGELOG.md").read_text()
    cut = text.replace(
        "## [Unreleased]\n\n### Added\n\n- An earlier entry.\n",
        "## [Unreleased]\n\n## [0.2.0] - 2026-02-01\n\n### Added\n\n- An earlier entry.\n",
        1,
    )
    (repo / "CHANGELOG.md").write_text(cut)
    res = _check(repo)
    assert res.returncode == 0, res.stdout


# --- exemptions --------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "scripts/experiment/runs/claude-e2e/x/runs.jsonl",
        "docs/plans/next.md",
        "uv.lock",
    ],
)
def test_exempt_paths_alone_need_no_entry(tmp_path, path):
    repo = _repo(tmp_path)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("data\n")
    _commit(repo)
    res = _check(repo)
    assert res.returncode == 0, res.stdout


def test_an_exempt_path_does_not_excuse_a_code_change(tmp_path):
    repo = _repo(tmp_path)
    (repo / "uv.lock").write_text("data\n")
    (repo / "src" / "app.py").write_text("x = 2\n")
    res = _check(repo)
    assert res.returncode == 1
    assert "src/app.py" in res.stdout and "uv.lock" not in res.stdout


def test_no_change_since_the_base_passes(tmp_path):
    res = _check(_repo(tmp_path))
    assert res.returncode == 0
    assert "no change" in res.stdout


# --- the base ----------------------------------------------------------------


def test_without_origin_main_or_a_tag_it_fails_closed_naming_the_fetch(tmp_path):
    repo = _repo(tmp_path, origin_main=False)
    res = _check(repo)
    assert res.returncode == 1
    assert _FIX_FETCH in res.stdout


def test_without_origin_main_it_falls_back_to_the_latest_v_tag(tmp_path):
    repo = _repo(tmp_path, origin_main=False, tag="v0.1.0")
    (repo / "src" / "app.py").write_text("x = 2\n")
    _commit(repo)
    res = _check(repo)
    assert res.returncode == 1 and "v0.1.0" in res.stdout and _FIX_ENTRY in res.stdout
    _add_under_unreleased(repo)
    assert _check(repo).returncode == 0


def test_the_base_is_the_merge_base_not_the_tip_of_origin_main(tmp_path):
    """Entries main gained after the branch point are not this change's entries."""
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "main-ahead")
    _add_under_unreleased(repo, "- Someone else's entry on main.")
    _commit(repo, "main moves on")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "work")
    (repo / "src" / "app.py").write_text("x = 2\n")
    _commit(repo)
    res = _check(repo)
    assert res.returncode == 1 and _FIX_ENTRY in res.stdout


def test_the_check_names_its_base_on_success(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "--short", "HEAD")
    (repo / "src" / "app.py").write_text("x = 2\n")
    _add_under_unreleased(repo)
    res = _check(repo)
    assert res.returncode == 0 and base in res.stdout and "origin/main" in res.stdout


def test_a_path_with_spaces_is_one_path(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "my notes.py").write_text("z = 1\n")
    res = _check(repo)
    assert res.returncode == 1 and "src/my notes.py" in res.stdout


# --- wiring: the gate and CI run this same script ------------------------------


def test_the_dod_gate_runs_this_script_for_changelog_entry():
    gate = (_SCRIPT.parent / "definition-of-done.sh").read_text()
    assert "scripts/checks/changelog-entry.sh" in gate


def test_checks_yml_runs_this_script_on_pull_requests():
    wf = (_SCRIPT.parents[2] / ".github" / "workflows" / "checks.yml").read_text()
    step = wf.split("- name: changelog entry", 1)[1].split("- name:", 1)[0]
    assert "github.event_name == 'pull_request'" in step
    assert "git fetch --no-tags origin main" in step
    assert "sh scripts/checks/changelog-entry.sh" in step
