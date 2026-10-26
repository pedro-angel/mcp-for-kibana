"""Drift-guard: every registered tool must be documented in docs/tools.md.

The public storefront once advertised "10 tools / dashboards only" while the
server actually shipped 133 tools across 10 toolboxes. This test ties the tool
reference to the live registry so that gap cannot silently reopen: it builds the
server with every toolbox at the `destructive` tier (so all tools are visible),
then asserts each registered tool name appears on the reference page. Add a tool
without documenting it and this fails. The page's tables are checked too (D57):
one row per tool in its own toolbox's section with the registry's tier, the
annotation table against the real hints, and each section's tier sentence.
"""

import pathlib
import re

from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests._registry import registry, tier_counts
from tests.fakes import FakeGateway

TOOLS_MD = pathlib.Path(__file__).resolve().parents[2] / "docs" / "tools.md"


async def _all_registered_tool_names() -> set[str]:
    settings = Settings(toolboxes=list(TOOLBOXES), tier=Tier("destructive"))
    mcp = build_server(settings, lambda space=None: FakeGateway())
    async with Client(mcp) as client:
        return {t.name for t in await client.list_tools()}


async def test_every_registered_tool_is_documented():
    text = TOOLS_MD.read_text()
    names = await _all_registered_tool_names()
    assert names, "no tools registered — build_server / registry regression"
    undocumented = sorted(n for n in names if f"`{n}`" not in text)
    assert not undocumented, (
        f"{len(undocumented)} registered tool(s) have no `name` entry in "
        f"docs/tools.md (document them there): {undocumented}"
    )


# --- the tables, not just the names (D57) ------------------------------------
#
# docs/tools.md is served to models as docs://tools at every tier, so a row's
# tier and the section it sits in are claims a model acts on. A name anywhere on
# the page once passed while the dashboards and annotation tables left out three
# add_esql_* tools (094e102). These pin each tool's row: exactly one, in its own
# toolbox's section, with the registry's tier; the dashboards annotation table
# against the real hints; and each section's "N reads + M writes" sentence.
# Rows a section shows on purpose for another toolbox's tool: the dashboards
# table lists the two data-view reads every dashboard build starts from.
BORROWED = {("dashboards", "list_data_views"), ("dashboards", "describe_data_view")}

_ROW_NAME = re.compile(r"\[?`(\w+)`")
_HINT_COLUMNS = ("read_only", "destructive", "idempotent")


def _sections(text):
    """[(heading, [(line_no, line)])] for every `## ` section of the page."""
    sections, current = [], ("", [])
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            sections.append(current)
            current = (line[3:].strip(), [])
        else:
            current[1].append((i, line))
    sections.append(current)
    return sections


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _tables(lines):
    """Each markdown table in a section: (header cells, [(line_no, cells)])."""
    tables, rows = [], None
    for i, line in lines:
        if line.startswith("|"):
            if rows is None:
                rows = (_cells(line), [])
                tables.append(rows)
            elif not set(line) <= set("|-: "):
                rows[1].append((i, _cells(line)))
        else:
            rows = None
    return tables


def _section_box(heading):
    boxes = {t.toolbox for t in registry().values()}
    return next((b for b in boxes if heading == f"{b} toolbox" or heading.endswith(f"({b})")), None)


def tool_rows(text):
    """(line_no, section box or None, tool name, tier cell) for every row of a
    `| Tool | Tier | ... |` table."""
    rows = []
    for heading, lines in _sections(text):
        for header, body in _tables(lines):
            if header[:2] != ["Tool", "Tier"]:
                continue
            for i, cells in body:
                m = _ROW_NAME.match(cells[0])
                rows.append((i, _section_box(heading), m.group(1) if m else cells[0], cells[1]))
    return rows


def row_problems(text):
    tools = registry()
    problems, seen = [], {}
    for i, box, name, tier_cell in tool_rows(text):
        tool = tools.get(name)
        if tool is None:
            problems.append(f"docs/tools.md:{i}: `{name}` is not a registered tool; remove the row")
            continue
        if box != tool.toolbox and (box, name) not in BORROWED:
            problems.append(
                f"docs/tools.md:{i}: `{name}` belongs to the {tool.toolbox} toolbox but its row "
                f"is in the {box or 'no'} toolbox section; move it to '## {tool.toolbox} toolbox'"
            )
            continue
        if (box, name) not in BORROWED:
            seen.setdefault(name, []).append(i)
        tier = tier_cell.split()[0] if tier_cell else ""
        if tier != tool.tier:
            problems.append(
                f"docs/tools.md:{i}: `{name}` tier cell says '{tier_cell}', the registry says "
                f"'{tool.tier}'; set the cell to {tool.tier}"
            )
        if "(upsert)" in tier_cell and not tool.idempotent:
            problems.append(
                f"docs/tools.md:{i}: `{name}` is marked (upsert) but its idempotentHint is false; "
                f"drop '(upsert)' or set the hint"
            )
    for name, lines in sorted(seen.items()):
        if len(lines) > 1:
            problems.append(f"docs/tools.md: `{name}` has {len(lines)} rows (lines {lines}); keep one")
    for name in sorted(set(tools) - set(seen)):
        problems.append(
            f"docs/tools.md: `{name}` has no row in the '## {tools[name].toolbox} toolbox' table; "
            f"add `| `{name}` | {tools[name].tier} | <description> |`"
        )
    return problems


def annotation_problems(text):
    """The dashboards '## Tiers and annotations' table against the real hints."""
    tools = registry()
    (lines,) = [lines for heading, lines in _sections(text) if heading == "Tiers and annotations"]
    (table,) = [rows for header, rows in _tables(lines) if header[:2] == ["Tier", "Tools"]]
    problems, listed = [], set()
    for i, cells in table:
        tier = cells[0].split()[0]
        for name in re.findall(r"`(\w+)`", cells[1]):
            listed.add(name)
            tool = tools.get(name)
            if tool is None:
                problems.append(f"docs/tools.md:{i}: `{name}` is not a registered tool")
                continue
            if tool.tier != tier:
                problems.append(
                    f"docs/tools.md:{i}: `{name}` is in the '{cells[0]}' row, its tier is {tool.tier}"
                )
            for attr, cell in zip(_HINT_COLUMNS, cells[2:5], strict=True):
                claimed = {"`true`": True, "`false`": False}.get(cell.split()[0])
                if claimed is not None and getattr(tool, attr) is not claimed:
                    problems.append(
                        f"docs/tools.md:{i}: `{name}` is in the '{cells[0]}' row, which says "
                        f"{attr}Hint {cell.split()[0]}; the tool's hint is "
                        f"{str(getattr(tool, attr)).lower()} — move it to the matching row"
                    )
    shown = {name for _, box, name, _ in tool_rows(text) if box == "dashboards"}
    if listed != shown:
        problems.append(
            "docs/tools.md: the annotation table and the dashboards table list different tools — "
            f"missing from the annotation table: {sorted(shown - listed)}; "
            f"not in the dashboards table: {sorted(listed - shown)}"
        )
    return problems


_TIER_SENTENCE = re.compile(
    r"shows the (\d+) reads \+\s+(\d+) writes; `destructive` adds the (\d+) deletes and "
    r"`(\w+)`;\s+`read`\s+shows only the (\d+) reads"
)


def sentence_problems(text):
    """Each section's 'A tier includes everything below it' sentence: (problems, boxes seen)."""
    tools = registry()
    problems, boxes = [], set()
    for heading, lines in _sections(text):
        box = _section_box(heading)
        body = " ".join(line for _, line in lines)
        for m in _TIER_SENTENCE.finditer(body):
            boxes.add(box)
            members = [t for t in tools.values() if t.toolbox == box]
            counts = tier_counts(members)
            reads, writes, deletes, extra, reads_again = m.groups()
            extra_tool = tools.get(extra)
            said = (int(reads), int(writes), int(deletes) + 1, int(reads_again))
            real = (counts["read"], counts["write"], counts["destructive"], counts["read"])
            if said != real or not (extra_tool and extra_tool.toolbox == box
                                    and extra_tool.tier == "destructive"):
                problems.append(
                    f"docs/tools.md '## {heading}': '{m.group(0)}' — the {box} toolbox has "
                    f"{counts['read']} reads, {counts['write']} writes and "
                    f"{counts['destructive']} destructive tools; fix the sentence"
                )
    return problems, boxes


def test_every_tool_has_one_row_in_its_own_section_with_its_tier():
    problems = row_problems(TOOLS_MD.read_text())
    assert not problems, "\n".join(problems)


def test_the_annotation_table_matches_the_hints():
    problems = annotation_problems(TOOLS_MD.read_text())
    assert not problems, "\n".join(problems)


def test_section_tier_sentences_match_the_registry():
    problems, boxes = sentence_problems(TOOLS_MD.read_text())
    assert not problems, "\n".join(problems)
    # the sentences are found at all (a reworded one would silently pass)
    assert {"alerting", "security-detections"} <= boxes


# --- the checks bite: doctored copies of the real page fail, naming the fix ---


def _doctor(old, new, count=1):
    text = TOOLS_MD.read_text()
    assert old in text, old
    return text.replace(old, new, count)


def test_a_re_tiered_row_fails():
    text = _doctor("| `delete_alert_rule` | destructive |", "| `delete_alert_rule` | write |")
    (problem,) = row_problems(text)
    assert "`delete_alert_rule` tier cell says 'write', the registry says 'destructive'" in problem
    assert "set the cell to destructive" in problem


def test_a_missing_row_fails():
    text = _doctor("| `delete_connector` | destructive |", "| `not_a_tool` | destructive |")
    problems = row_problems(text)
    assert any("`not_a_tool` is not a registered tool" in p for p in problems)
    assert any("`delete_connector` has no row in the '## alerting toolbox' table" in p
               for p in problems)


def _row(name):
    return next(line for line in TOOLS_MD.read_text().splitlines()
                if line.startswith(f"| `{name}` |"))


def test_a_row_in_another_toolbox_section_fails():
    anchor = _row("delete_alert_rule")
    problems = row_problems(_doctor(anchor, _row("list_cases") + "\n" + anchor))
    assert problems == [
        f"docs/tools.md:{problems[0].split(':')[1]}: `list_cases` belongs to the cases toolbox "
        "but its row is in the alerting toolbox section; move it to '## cases toolbox'"
    ]


def test_a_tool_with_two_rows_fails():
    anchor = _row("delete_alert_rule")
    (problem,) = row_problems(_doctor(anchor, anchor + "\n" + anchor))
    assert "`delete_alert_rule` has 2 rows" in problem and "keep one" in problem


def test_an_annotation_table_missing_a_tool_fails():
    # the 094e102 escape: an add_esql_* tool left out of the annotation table
    text = _doctor("`add_esql_metric_panel`, `add_esql_table_panel`", "`add_esql_table_panel`")
    (problem,) = annotation_problems(text)
    assert "missing from the annotation table: ['add_esql_metric_panel']" in problem


def test_an_annotation_row_with_the_wrong_hint_fails():
    text = _doctor("| write | `create_visualization`,", "| write | `update_panel`, `create_visualization`,")
    problems = annotation_problems(text)
    assert any("`update_panel` is in the 'write' row, which says idempotentHint `false`" in p
               for p in problems)


def test_a_stale_tier_sentence_fails():
    text = _doctor("shows the 6 reads +\n13 writes", "shows the 6 reads +\n12 writes")
    problems, _ = sentence_problems(text)
    assert len(problems) == 1 and "the alerting toolbox has 6 reads, 13 writes" in problems[0]
