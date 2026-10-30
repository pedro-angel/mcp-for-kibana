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

Every figure must be one the registry gives *in that place*: inside a
toolbox's spec (docs/spec/toolboxes/<box>.md) or its section of docs/tools.md,
that toolbox's own figures and the cross-cutting ones (the total, the space
contract); anywhere else, any whole-surface figure or toolbox size. A figure
wrapped across a line break ("... on 21" / "tools") is still a figure. A figure that records a past state (a study
run, a shipped-then-grown toolbox) goes in HISTORICAL with the words around
it. docs/spec/decisions.md and CHANGELOG.md are not scanned: they record
figures as they were at the time.
"""

import pathlib
import re
from collections import Counter

import pytest

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
# figure (a substring of the line, or of it joined with the next when the
# figure wraps). An entry exempts only the figures inside its words; an entry
# nothing matches any more fails.
HISTORICAL: dict[tuple[str, str], str] = {
    # toolboxes as they first shipped (the roadmap's done list)
    ("docs/roadmap.md", "Read-first v1 (20 tools)"): "fleet read tier, 2026-07",
    ("docs/roadmap.md", "**: 15 tools (agent/package-policy"): "fleet write tier, 2026-07",
    ("docs/roadmap.md", "**shipped 2026-07-18**. 3 tools (9→12)"): "streams follow-up",
    ("docs/roadmap.md", "**shipped 2026-07-18**. 6 tools (19→25)"): "security follow-up",
    ("docs/roadmap.md", "10 read tools; whole surface Basic-GA"): "security v1 scope",
    ("docs/roadmap.md", "dashboards + data-management + platform-admin, 29 tools at write"): "D39",
    ("docs/roadmap.md", "space targeting shipped on 21 tools"): "space wave, 2026-08-15",
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


def _tier_figures(tools, whose):
    """{kind: {value: label}} for a set of tools' tiers and tier triple."""
    counts = tier_counts(tools)
    figs = {tier: {counts[tier]: f"{whose} {tier} tier"} for tier in TIERS}
    figs["triple"] = {tuple(counts[t] for t in TIERS): f"{whose} read / write / destructive"}
    return counts, figs


def _add(figs, kind, value, label):
    figs.setdefault(kind, {}).setdefault(value, label)


def figures():
    """The figures the registry gives: (whole surface, cross-cutting, per toolbox,
    toolbox sizes), each {kind: {value: label}}.

    Kinds are "tools", "read", "write", "destructive", "triple" and "edges";
    the label says what the value is, so a failure names the right figure.
    Cross-cutting figures (the total, the space contract) may appear anywhere,
    a toolbox's spec included; the rest of the whole-surface figures (tier
    totals, built-in list and profile sizes) only outside a toolbox's scope. A
    toolbox's own figures are its size, each tier, and read + write (what the
    default tier shows)."""
    tools = list(registry().values())
    by_box: dict[str, list] = {}
    for t in tools:
        by_box.setdefault(t.toolbox, []).append(t)
    counts, whole = _tier_figures(tools, "the whole surface's")
    space = [t for t in tools if t.space_aware]
    space_counts = tier_counts(space)
    cross = {"tools": {len(tools): "the whole surface", len(space): "space-aware tools"}}
    for tier in TIERS:  # "including the 17 destructive-tier ones" of the space contract
        _add(cross, tier, space_counts[tier], f"space-aware {tier}-tier tools")
    for tier in TIERS:
        _add(whole, "tools", counts[tier], f"the whole surface's {tier} tier")
    for name, members in BUILTIN_LISTS.items():
        _add(whole, "tools", len(members), f"built-in list {name}")
    for name, size in profile_sizes().items():
        _add(whole, "tools", size, f"profile {name}")
    whole["edges"] = {
        (sum(len(v) for v in DEPENDENCIES.values()), len(DEPENDENCIES)): "the dependency map"
    }
    per_box = {}
    for box, members in by_box.items():
        box_counts, own = _tier_figures(members, f"{box}'s")
        own["tools"] = {len(members): f"the {box} toolbox"}
        for tier in TIERS:
            _add(own, "tools", box_counts[tier], f"{box}'s {tier} tier")
        _add(own, "tools", box_counts["read"] + box_counts["write"], f"{box}'s read + write")
        per_box[box] = own
    sizes = {len(v): f"the {box} toolbox" for box, v in sorted(by_box.items())}
    return whole, cross, per_box, sizes


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


def _problem(rel, i, text, allowed, box):
    where = f"the {box} toolbox's scope" if box else "the whole surface"
    listed = "; ".join(f"{v} ({label})" for v, label in sorted(allowed.items()))
    return (
        f"{rel}:{i}: '{text}' — not a figure the registry gives in {where}, which are: "
        f"{listed}; set the one this sentence means, or if it records a past state add "
        f"('{rel}', '<words around it>') to HISTORICAL in tests/unit/test_docs_counts.py"
    )


def _matches(line):
    """(kind, value, text, span) for every figure on a line."""
    found, edge_spans = [], []
    for m in EDGES_RE.finditer(line):
        edge_spans.append(m.span())
        found.append(("edges", (int(m.group(1)), int(m.group(2))), m.group(0), m.span()))
    for m in TOOLS_RE.finditer(line):
        if not any(a <= m.start() < b for a, b in edge_spans):
            found.append(("tools", int(m.group(1)), m.group(0), m.span()))
    for m in TRIPLE_RE.finditer(line):
        found.append(("triple", tuple(int(g) for g in m.groups()), m.group(0), m.span()))
    for m in TIER_RE.finditer(line):
        found.append((m.group(2), int(m.group(1)), m.group(0), m.span()))
    return found


def _line_figures(line, following):
    """The figures on a line, including one that wraps onto the next line
    ("... on 21" / "tools ..."): matched in `line + " " + following`, kept
    when it starts on this line, so each figure is reported once. The next
    line's indent and blockquote markers are dropped first, so a figure that
    wraps onto a list item's continuation ("  tools") or a quote ("> tools")
    is still one figure; so is this line's Markdown hard line break (trailing
    spaces, "21  ", or a backslash, "21\\")."""
    if following is None:
        return line, _matches(line)
    head = line.rstrip(" \t").removesuffix("\\").rstrip(" \t")
    joined = head + " " + following.lstrip(" \t>")
    return joined, [f for f in _matches(joined) if f[3][0] < len(head)]


def _spans(text, needle):
    start = text.find(needle)
    while start != -1:
        yield start, start + len(needle)
        start = text.find(needle, start + 1)


def scan(paths=SCANNED, root=ROOT):
    """(messages for every figure that is neither the registry's nor
    HISTORICAL, the HISTORICAL entries that matched a figure). A HISTORICAL
    entry exempts only the figures inside its words, not the rest of the line."""
    whole, cross, per_box, sizes = figures()
    problems, used = [], set()
    for path in paths:
        rel = path.relative_to(root).as_posix()
        lines = path.read_text().splitlines()
        scope = _scope(rel, lines, per_box)
        for i, line in enumerate(lines, 1):
            joined, found = _line_figures(line, lines[i] if i < len(lines) else None)
            history = {
                key: list(_spans(joined, key[1])) for key in HISTORICAL if key[0] == rel
            }
            box = scope[i]
            # a toolbox's lines may state its own figures and the cross-cutting ones;
            # other lines any whole-surface figure and any toolbox's size
            pools = (cross, per_box[box]) if box else (cross, whole, {"tools": sizes})
            for kind, value, text, (a, b) in found:
                allowed = {}
                for pool in reversed(pools):
                    allowed.update(pool.get(kind, {}))
                exempt = [k for k, spans in history.items() if any(s <= a and b <= e for s, e in spans)]
                used.update(exempt)
                if value not in allowed and not exempt:
                    problems.append(_problem(rel, i, text, allowed, box))
    return problems, used


def test_registry_figures_are_sane():
    _, _, per_box, _ = figures()
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
    assert f"{space} (space-aware tools)" in problems[0]
    assert "set the one this sentence means" in problems[0]


def test_a_toolbox_figure_counts_only_inside_its_own_scope(tmp_path):
    alerting = sum(t.toolbox == "alerting" for t in registry().values())
    ok = _scan_one(tmp_path, "docs/spec/toolboxes/alerting.md", f"{alerting} tools\n")
    assert ok == []
    # the same figure in the cases spec is not a cases figure ...
    (cases,) = _scan_one(tmp_path, "docs/spec/toolboxes/cases.md", f"exactly {alerting} tools\n")
    assert "in the cases toolbox's scope" in cases
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


def test_a_count_wrapped_across_a_line_break_is_still_a_count(tmp_path):
    (problem,) = _scan_one(tmp_path, "docs/index.md", "space targeting shipped on 21\ntools\n")
    assert problem.startswith("docs/index.md:1: '21 tools'")


@pytest.mark.parametrize(
    "text",
    [
        "- shipped on 21\n  tools accept\n",
        "> shipped on 21\n> tools\n",
        "all 999\n\t  tools\n",
        "shipped on 999  \ntools accept\n",  # a Markdown hard line break
        "shipped on 999 \n  tools\n",
        "shipped on 999\\\ntools\n",  # a backslash hard break
    ],
)
def test_a_count_wrapped_onto_an_indented_or_quoted_line_is_still_a_count(tmp_path, text):
    (problem,) = _scan_one(tmp_path, "docs/index.md", text)
    assert problem.startswith("docs/index.md:1: ")


def test_a_toolbox_spec_does_not_accept_whole_surface_tier_or_list_figures(tmp_path):
    tools = registry().values()
    health = sum(t.toolbox == "platform-health" for t in tools)
    destructive = sum(t.tier == "destructive" for t in tools)
    explorer = profile_sizes()["read-only-explorer"]
    assert health not in (destructive, explorer)
    text = f"MUST register exactly {destructive} tools\nall {explorer} tools\n"
    problems = _scan_one(tmp_path, "docs/spec/toolboxes/platform-health.md", text)
    assert [p.split(":")[1] for p in problems] == ["1", "2"]
    # the cross-cutting figures stay allowed: the total and the space contract
    total, space = len(registry()), sum(t.space_aware for t in tools)
    ok = f"one of {total} tools\nthe shared {space}-tool contract\n"
    assert _scan_one(tmp_path, "docs/spec/toolboxes/platform-health.md", ok) == []


def test_the_failure_labels_each_allowed_figure(tmp_path):
    box = [t for t in registry().values() if t.toolbox == "data-management"]
    (problem,) = _scan_one(tmp_path, "docs/spec/toolboxes/data-management.md", "the 11-tool surface\n")
    assert f"{len(box)} (the data-management toolbox)" in problem
    rw = sum(t.tier != "destructive" for t in box)
    assert f"{rw} (data-management's read + write)" in problem


def test_a_historical_entry_exempts_only_its_own_figure(tmp_path):
    rel = "docs/roadmap.md"
    line = "**shipped 2026-07-18**. 3 tools (9→12)"
    assert _scan_one(tmp_path, rel, line + "\n") == []
    (problem,) = _scan_one(tmp_path, rel, line + ", now 999 tools\n")
    assert "'999 tools'" in problem
