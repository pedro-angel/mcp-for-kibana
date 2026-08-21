"""Drift guard: the product must not reference how it was built.

Design specs, implementation plans, environment-research probes and
retrospectives were development artifacts, not documentation. They have been
deleted from the working tree — git history is the archive, and every fact worth
keeping was moved into the code, the tests or the shipped docs first. This test
stops them, or references to them, coming back.

That property is easy to state and easy to erode: one convenient
`see docs/<somewhere>/<some-design>.md` in a docstring and the codebase quietly
depends on a file no consumer will ever have. This test is the enforcement.

When it fails, the fix is NOT to add an exemption. It is to write down the fact
the pointer was standing in for. A comment should say *what was established*
("live Kibana 9.4.3 rejects time_range inside Lens configs"), never *where
somebody recorded it*. The knowledge is the valuable part; the path is not.
"""

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Directory names that used to hold development artifacts. They no longer exist,
# so a reference to one is now a guaranteed-broken link rather than merely an
# unwanted coupling. Kept as patterns because the failure mode this guards
# against is someone recreating them.
_ARTIFACT_DIRS = ("superpowers", "experiments", "research", "retros", "evidence")

_BANNED = {
    "path into a development-artifact directory": re.compile(
        r"(?:docs/)?(?:" + "|".join(_ARTIFACT_DIRS) + r")/"
    ),
    "assistant working-state directory": re.compile(r"(?<![\w.-])\.superpowers\b"),
    # Labels that only resolve inside an artifact: "spec D2", "probe#7". A
    # reader without the artifact cannot act on either.
    "artifact-local section label": re.compile(r"\bspec D\d+\b|\bprobe#\d+\b"),
    "artifact filename": re.compile(r"\bdecisions\.jsonl\b|\bLessons\.md\b"),
    # CLAUDE.md was removed in e8ad9c8 — any surviving link is already broken.
    # AGENTS.md left this ban on 2026-08-19: it was reintroduced deliberately as
    # the regeneration-corpus entry point (docs/spec/), a different artifact that
    # happens to carry the convention's standard name.
    "removed methodology file": re.compile(r"\bCLAUDE\.md\b"),
}

# Structural exceptions — each is a rule ABOUT a path, not a dependency ON one.
# Keep this set tiny; "it was inconvenient to fix" is not a reason to grow it.
_EXEMPT = {
    # This test: the patterns above are its subject matter.
    "tests/unit/test_no_dev_artifact_references.py",
    # Ignoring a path requires naming it — there is no other way to express the
    # rule. A stale ignore entry for a directory that no longer exists is inert,
    # so this cannot break anything when the artifacts go.
    ".gitignore",
    # Vendored verbatim from upstream and deliberately never edited (the manifest
    # path is passed as an argument instead), so its header keeps upstream's
    # wording. Patching it here would defeat the point of vendoring it unforked.
    "scripts/checks/vocabulary-conformance.sh",
}


def _tracked_files() -> list[str]:
    """Every file git tracks — the exact set a `git clone` would deliver.

    Using git rather than a filesystem walk means the guard covers precisely
    what ships and cannot be fooled by an untracked scratch file, and it skips
    .venv/site/caches for free.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def _shipped_files() -> list[str]:
    """Tracked files minus the development artifacts themselves.

    The artifacts may of course cross-reference each other — that is their whole
    point. The guard is about what the PRODUCT references, not what the history
    references.
    """
    return [
        f
        for f in _tracked_files()
        if not any(f.startswith(f"docs/{d}/") for d in _ARTIFACT_DIRS)
        and not f.startswith(".superpowers/")
        and f not in _EXEMPT
    ]


@pytest.mark.parametrize("label,pattern", sorted(_BANNED.items()))
def test_no_shipped_file_references_a_development_artifact(label, pattern):
    hits = []
    for rel in _shipped_files():
        path = _REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # binary asset or a path git tracks but disk lacks
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"  {rel}:{number}: {line.strip()[:100]}")

    assert not hits, (
        f"{len(hits)} shipped file(s) contain a {label} — the project must stand "
        "on its own if every development artifact is deleted.\n"
        + "\n".join(hits)
        + "\n\nFix by stating the FACT the pointer stood for, inline. Do not add "
        "an exemption."
    )


def test_the_guard_can_actually_fail():
    """The guard is worthless if its patterns never match anything.

    Asserts each pattern against a string it must catch, so a future edit that
    accidentally neuters a regex (making the test above vacuously green) fails
    here instead of going unnoticed.
    """
    must_match = {
        "path into a development-artifact directory": "see experiments/foo/",
        "assistant working-state directory": "ledger at .superpowers/sdd/progress.md",
        "artifact-local section label": "parsing policy (spec D2) and probe#7",
        "artifact filename": "shapes from decisions.jsonl and Lessons.md",
        "removed methodology file": "described in CLAUDE.md",
    }
    assert must_match.keys() == _BANNED.keys(), "a pattern lost its canary"
    for label, sample in must_match.items():
        assert _BANNED[label].search(sample), f"pattern {label!r} no longer matches"
