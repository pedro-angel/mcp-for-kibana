"""Drift-guard: every test the docs cite exists (D57).

The specs' Enforcement sections name the tests that pin each guarantee, and
docs/spec/regeneration.md tells a rewrite to port exactly those. A renamed test
left its old name in platform-health.md (`..._is_nine_read_tools`, renamed in
afe9b17) through every GO since. This collects every `test_<name>` and every
`tests/....py` path cited in the docs corpus and requires each to resolve: a
path to a file, a name to a test module, function or class under tests/.
"""

import ast
import difflib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CITING = sorted(
    [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "profiles" / "README.md",
        *(ROOT / "docs").glob("*.md"),
        *(ROOT / "docs" / "spec").rglob("*.md"),
    ]
)

PATH_RE = re.compile(r"(?<![\w/.-])(tests/[\w/]+\.py)\b")
NAME_RE = re.compile(r"(?<![\w/.-])(test_\w+)")

# `test_<word>` identifiers that are not tests: a Synthetics field name.
NOT_TESTS = {"test_run_id"}


def defined(root=ROOT):
    """(test file paths, names defined under tests/: modules, functions, classes)."""
    paths, names = set(), set()
    for path in (root / "tests").rglob("*.py"):
        paths.add(path.relative_to(root).as_posix())
        names.add(path.stem)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
    return paths, names


def unresolved(files=CITING, root=ROOT):
    paths, names = defined(root)
    problems = []
    for doc in files:
        rel = doc.relative_to(root).as_posix()
        for i, line in enumerate(doc.read_text().splitlines(), 1):
            for m in PATH_RE.finditer(line):
                if m.group(1) not in paths:
                    near = difflib.get_close_matches(m.group(1), sorted(paths), n=1)
                    problems.append(_message(rel, i, m.group(1), near))
            for m in NAME_RE.finditer(line):
                if m.group(1) not in names and m.group(1) not in NOT_TESTS:
                    near = difflib.get_close_matches(m.group(1), sorted(names), n=1, cutoff=0.5)
                    problems.append(_message(rel, i, m.group(1), near))
    return problems


def _message(rel, i, cited, near):
    fix = f"did you mean `{near[0]}`?" if near else "cite the test that pins it now"
    return f"{rel}:{i}: cites `{cited}`, which is not under tests/ — {fix}"


def test_every_cited_test_exists():
    problems = unresolved()
    assert not problems, (
        f"{len(problems)} test citation(s) in the docs do not resolve:\n" + "\n".join(problems)
    )


def test_the_corpus_is_scanned():
    names = {p.relative_to(ROOT).as_posix() for p in CITING}
    assert {"docs/spec/toolboxes/platform-health.md", "docs/spec/decisions.md",
            "docs/tools.md", "AGENTS.md"} <= names


def test_a_renamed_test_fails_naming_the_closest(tmp_path):
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_profiles.py").write_text(
        "def test_read_only_explorer_profile_is_three_toolboxes_at_read():\n    pass\n"
    )
    doc = tmp_path / "spec.md"
    doc.write_text(
        "- `tests/unit/test_profiles.py` pins it\n"
        "- `test_read_only_explorer_profile_is_nine_read_tools` pins additive composition\n"
        "- `tests/unit/test_profile.py` too\n"
    )
    assert unresolved([doc], tmp_path) == [
        "spec.md:2: cites `test_read_only_explorer_profile_is_nine_read_tools`, which is not "
        "under tests/ — did you mean `test_read_only_explorer_profile_is_three_toolboxes_at_read`?",
        "spec.md:3: cites `tests/unit/test_profile.py`, which is not under tests/ — "
        "did you mean `tests/unit/test_profiles.py`?",
    ]
