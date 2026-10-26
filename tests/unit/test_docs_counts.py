"""Drift-guard: the figures the prose states about the tool surface (D57).

Tool counts were typed by hand into a dozen files and never checked: the space
contract read "86 tools" in four specs through two feature commits after the
registry reached 92, and commit d1a09c6 found counts still at 162/80/21 after
5da1b18 had passed the gate. This test computes the figures from the live
registry (tests/_registry.py) and scans the prose for the shapes a count takes:

- "N tools" / "N-tool" / "N tool(s)" (not "N tool calls");
- a tier triple "(R / W / D)";
- a tier count "N read", "N `write`s", "N destructive";
- "N edges from M tools" (the dependency map).

Every figure must be one the registry gives *in that place*: the whole surface
anywhere; a toolbox's own figures inside its spec (docs/spec/toolboxes/<box>.md)
or its section of docs/tools.md. A figure that records a past state (a study
run, a shipped-then-grown toolbox) goes in HISTORICAL with the words around
it. docs/spec/decisions.md and CHANGELOG.md are not scanned: they record
figures as they were at the time.
"""

import pathlib
import re
from collections import Counter

from kibana_mcp.tool_lists import BUILTIN_LISTS, DEPENDENCIES
from tests._registry import TIERS, profile_sizes, registry, tier_counts

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCANNED = sorted(
    [
        ROOT / "README.md",
        ROOT / "profiles" / "README.md",
        *(ROOT / "docs").glob("*.md"),
        *(p for p in (ROOT / "docs" / "spec").glob("*.md") if p.name != "decisions.md"),
        *(ROOT / "docs" / "spec" / "toolboxes").glob("*.md"),
    ]
)

# A count, not a version (9.5.4), a range (6–11), an id (D48), a step (9→12)
# or an approximation (~20: the selection-accuracy heuristic, brief.md).
_NUM = r"(?<![\w.\-/→–~])(\d+)"
TOOLS_RE = re.compile(_NUM + r"[- ]tool(?:s\b|\(s\)|\b)(?![ -]calls?\b)")
TRIPLE_RE = re.compile(r"\((\d+) ?/ ?(\d+) ?/ ?(\d+)\)")
TIER_RE = re.compile(_NUM + r" `?(read|write|destructive)`?(?:s\b|\b)")
EDGES_RE = re.compile(r"(\d+) edges? from (\d+) tools?\b")

# Figures that record a past state, keyed by file and the words around the
# figure (a substring of the line). An entry nothing matches any more fails.
HISTORICAL: dict[tuple[str, str], str] = {
    # toolboxes as they first shipped (the roadmap's done list)
    ("docs/roadmap.md", "Read-first v1 (20 tools)"): "fleet read tier, 2026-07",
    ("docs/roadmap.md", "**: 15 tools (agent/package-policy"): "fleet write tier, 2026-07",
    ("docs/roadmap.md", "**shipped 2026-07-18**. 3 tools (9→12)"): "streams follow-up",
    ("docs/roadmap.md", "**shipped 2026-07-18**. 6 tools (19→25)"): "security follow-up",
    ("docs/roadmap.md", "10 read tools; whole surface Basic-GA"): "security v1 scope",
    ("docs/roadmap.md", "dashboards + data-management + platform-admin, 29 tools at write"): "D39",
    # study runs, on the surface they ran against
    ("docs/e2e-setup.md", "designed on 24 tools for the dashboards gate"): "study design",
    ("docs/server-usability.md", "**24 tools**: `dashboards`, `data-management`"): "study run",
    # an example list and a sample log line, not the surface
    ("docs/configuration.md", "publishes exactly those 8 tools"): "example list",
    ("docs/configuration.md", "publishes only its 4 read tools"): "example list's reads",
    ("docs/configuration.md", "leaves out 2 tool(s) that published tools"): "sample log line",
    # a duration format, not a tier count
    ("docs/tools.md", "milliseconds: 1500 reads as `1.5`"): "not a count",
}


def _tier_figures(tools):
    counts = tier_counts(tools)
    return counts, {tier: {counts[tier]} for tier in TIERS} | {
        "triple": {tuple(counts[t] for t in TIERS)},
    }


def figures():
    """The figures the registry gives: (whole surface, per toolbox, toolbox totals).

    Each maps a kind ("tools", "read", "write", "destructive", "triple",
    "edges") to the allowed values. A toolbox's own figures are its size, each
    tier, and read + write (what the default tier shows)."""
    tools = list(registry().values())
    by_box: dict[str, list] = {}
    for t in tools:
        by_box.setdefault(t.toolbox, []).append(t)
    counts, whole = _tier_figures(tools)
    space_counts = tier_counts([t for t in tools if t.space_aware])
    for tier in TIERS:  # "including the 17 destructive-tier ones" of the space contract
        whole[tier].add(space_counts[tier])
    whole["tools"] = {
        len(tools), *counts.values(), sum(space_counts.values()),
        *(len(v) for v in BUILTIN_LISTS.values()), *profile_sizes().values(),
    }
    whole["edges"] = {(sum(len(v) for v in DEPENDENCIES.values()), len(DEPENDENCIES))}
    per_box = {}
    for box, members in by_box.items():
        box_counts, own = _tier_figures(members)
        own["tools"] = {len(members), *box_counts.values(), box_counts["read"] + box_counts["write"]}
        per_box[box] = own
    return whole, per_box, {len(v) for v in by_box.values()}


def _scope(rel, lines, boxes):
    """The toolbox each line speaks for: line number -> box name or None. A
    toolbox spec speaks for its toolbox; docs/tools.md by `## <box> toolbox`
    or `## ... (<box>)` section; everything else for the whole surface."""
    if rel.startswith("docs/spec/toolboxes/"):
        box = rel.rsplit("/", 1)[1].removesuffix(".md")
        return {i: box for i in range(1, len(lines) + 1)}
    scope, box = {}, None
    for i, line in enumerate(lines, 1):
        if rel == "docs/tools.md" and line.startswith("## "):
            heading = line[3:].strip()
            box = next(
                (b for b in boxes if heading == f"{b} toolbox" or heading.endswith(f"({b})")), None
            )
        scope[i] = box
    return scope


def _problem(rel, i, text, kind, value, allowed, box):
    where = f"the {box} toolbox" if box else "the whole surface"
    if kind in ("triple", "edges"):
        hint = f"the registry gives {' or '.join(map(str, sorted(allowed)))}"
    else:
        closest = min(sorted(allowed), key=lambda a: abs(a - value))
        hint = f"the registry gives {closest} (figures for {where}: {sorted(allowed)})"
    return (
        f"{rel}:{i}: '{text}' — {hint}; fix the figure, or if it records a past state "
        f"add ('{rel}', '<words around it>') to HISTORICAL in tests/unit/test_docs_counts.py"
    )


def _matches(line):
    """(kind, value, text) for every figure on a line."""
    found, edge_spans = [], []
    for m in EDGES_RE.finditer(line):
        edge_spans.append(m.span())
        found.append(("edges", (int(m.group(1)), int(m.group(2))), m.group(0)))
    for m in TOOLS_RE.finditer(line):
        if not any(a <= m.start() < b for a, b in edge_spans):
            found.append(("tools", int(m.group(1)), m.group(0)))
    for m in TRIPLE_RE.finditer(line):
        found.append(("triple", tuple(int(g) for g in m.groups()), m.group(0)))
    for m in TIER_RE.finditer(line):
        found.append((m.group(2), int(m.group(1)), m.group(0)))
    return found


def scan(paths=SCANNED, root=ROOT):
    """(messages for every figure that is neither the registry's nor
    HISTORICAL, the HISTORICAL entries that matched a line)."""
    whole, per_box, box_totals = figures()
    problems, used = [], set()
    for path in paths:
        rel = path.relative_to(root).as_posix()
        lines = path.read_text().splitlines()
        scope = _scope(rel, lines, per_box)
        for i, line in enumerate(lines, 1):
            history = [key for key in HISTORICAL if key[0] == rel and key[1] in line]
            used.update(history)
            box = scope[i]
            # a toolbox's lines may state its own figures; other lines any toolbox's size
            own = per_box[box] if box else {"tools": box_totals}
            for kind, value, text in _matches(line):
                allowed = whole.get(kind, set()) | own.get(kind, set())
                if value not in allowed and not history:
                    problems.append(_problem(rel, i, text, kind, value, allowed, box))
    return problems, used


def test_registry_figures_are_sane():
    _, per_box, _ = figures()
    tools = registry()
    assert len(tools) > 100 and len(per_box) >= 10
    assert Counter(t.tier for t in tools.values()).keys() == set(TIERS)


def test_every_figure_in_the_prose_matches_the_registry():
    problems, _ = scan()
    assert not problems, (
        f"{len(problems)} figure(s) in the docs disagree with the tool registry:\n"
        + "\n".join(problems)
    )


def test_every_historical_entry_still_matches_a_line():
    _, used = scan()
    stale = sorted(set(HISTORICAL) - used)
    assert not stale, (
        f"HISTORICAL entries in tests/unit/test_docs_counts.py match no line any more "
        f"(remove them): {stale}"
    )


def test_scanned_files_include_specs_and_skip_the_records():
    names = {p.relative_to(ROOT).as_posix() for p in SCANNED}
    assert {"README.md", "docs/tools.md", "docs/spec/design.md",
            "docs/spec/toolboxes/alerting.md", "profiles/README.md"} <= names
    assert "docs/spec/decisions.md" not in names
    assert "CHANGELOG.md" not in names


# --- the scanner bites: doctored prose fails, naming the file, line and fix ---


def _scan_one(tmp_path, rel, text):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return scan([path], root=tmp_path)[0]


def test_a_stale_space_count_in_a_spec_fails_naming_the_value(tmp_path):
    space = sum(t.space_aware for t in registry().values())
    problems = _scan_one(
        tmp_path, "docs/spec/toolboxes/alerting.md",
        "intro\n- **Space targeting follows the shared 86-tool contract**\n",
    )
    assert len(problems) == 1
    assert problems[0].startswith("docs/spec/toolboxes/alerting.md:2: '86-tool'")
    assert f"the registry gives {space} " in problems[0]
    assert "fix the figure" in problems[0]


def test_a_toolbox_figure_counts_only_inside_its_own_scope(tmp_path):
    alerting = sum(t.toolbox == "alerting" for t in registry().values())
    ok = _scan_one(tmp_path, "docs/spec/toolboxes/alerting.md", f"{alerting} tools\n")
    assert ok == []
    # the same figure in the cases spec is not a cases figure ...
    (cases,) = _scan_one(tmp_path, "docs/spec/toolboxes/cases.md", f"exactly {alerting} tools\n")
    assert "figures for the cases toolbox" in cases
    # ... while a page about the whole surface may name any toolbox's size
    assert _scan_one(tmp_path, "docs/index.md", f"alerting ({alerting} tools)\n") == []


def test_tools_md_sections_scope_tier_counts(tmp_path):
    counts = tier_counts([t for t in registry().values() if t.toolbox == "alerting"])
    good = f"## alerting toolbox\n\nshows the {counts['read']} reads + {counts['write']} writes\n"
    assert _scan_one(tmp_path, "docs/tools.md", good) == []
    bad = f"## alerting toolbox\n\nshows the {counts['read'] + 1} reads + {counts['write']} writes\n"
    (problem,) = _scan_one(tmp_path, "docs/tools.md", bad)
    assert problem.startswith(f"docs/tools.md:3: '{counts['read'] + 1} reads'")


def test_a_wrong_tier_triple_and_edge_count_fail(tmp_path):
    problems = _scan_one(
        tmp_path, "README.md", "destructive (1 / 2 / 3).\nthe map: 5 edges from 4 tools\n"
    )
    assert [p.split(":")[1] for p in problems] == ["1", "2"]
    assert "'(1 / 2 / 3)'" in problems[0] and "'5 edges from 4 tools'" in problems[1]


def test_tool_calls_and_versions_are_not_counts(tmp_path):
    text = "at 6–11 tool calls\nthe version-1 tool-list file\nKibana 9.5.4 read\n"
    assert _scan_one(tmp_path, "docs/index.md", text) == []
