"""The surface budget (D60): what a model is handed is capped, and a cap is
raised only by a decision that says why the growth is worth it.

docs/spec/surface-budget.toml caps each surface a model can be given — the
default selection, every shipped profile, every toolbox at `destructive` and
all of them together — by tool count, total description characters and total
input-schema characters (compact JSON), and every single tool by description
and input-schema size (named exceptions for the few above the per-tool caps).
Measured here from a FakeGateway build, as the tool listing reaches a client.

Each cap cites a decision in docs/spec/decisions.md whose entry states the
cap's numbers in the form the file's header gives and carries a `Why:`; a cap
above its founding value (FOUNDING, D60's numbers), or new, must cite a later
decision, and every decision's budget lines are pinned here (LEDGER), so a
decision edited in place, or one before D60 cited, fails unless LEDGER is
edited in the same change (a diff a reviewer sees).
So growth past the headroom is a diff to the budget plus a decision entry
saying why it is worth it — a new tool alone is not a reason (owner, H8).
"""

import asyncio
import contextlib
import functools
import json
import os
import pathlib
import re
import tomllib
from unittest import mock

import pytest
from fastmcp import Client

from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from kibana_mcp.toolboxes import TOOLBOXES
from tests.fakes import FakeGateway

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUDGET = ROOT / "docs" / "spec" / "surface-budget.toml"
DECISIONS = ROOT / "docs" / "spec" / "decisions.md"
PROFILES = ROOT / "profiles"
METRICS = ("tools", "description_chars", "input_schema_chars")
TOOL_METRICS = ("description_chars", "input_schema_chars")
FOUNDING_DECISION = "D60"
_FIX = (
    "trim it (shorter description, fewer or smaller parameters, or fewer tools on that "
    "surface), or raise the cap in docs/spec/surface-budget.toml citing a NEW decision in "
    "docs/spec/decisions.md that states the new cap and says why the growth is worth it — "
    "a new tool alone is not a reason (D60)")


@contextlib.contextmanager
def _no_selection_env():
    """Settings read KIBANA_MCP_* from the environment, and a shell
    KIBANA_MCP_TOOLS or KIBANA_MCP_TIER would reshape every surface measured
    here: each surface is built with none of them set."""
    kept = {k: v for k, v in os.environ.items() if not k.upper().startswith("KIBANA_MCP_")}
    with mock.patch.dict(os.environ, kept, clear=True):
        yield


def _settings(**selection):
    with _no_selection_env():
        return Settings(**selection)


def _profile_settings():
    out = {}
    for path in sorted(PROFILES.glob("*.mcp.json")):
        env = json.loads(path.read_text())["mcpServers"]["mcp-for-kibana"]["env"]
        selection = {"toolboxes": env["KIBANA_MCP_TOOLBOXES"]} if "KIBANA_MCP_TOOLBOXES" in env else {}
        if "KIBANA_MCP_TOOLS" in env:
            selection["tools"] = env["KIBANA_MCP_TOOLS"]
        out[f"profile:{path.name.removesuffix('.mcp.json')}"] = _settings(
            **selection, tier=Tier(env["KIBANA_MCP_TIER"]))
    return out


def surfaces() -> dict[str, Settings]:
    """Every budgeted surface, by the name the budget file uses."""
    every = {
        "default": _settings(),
        "all-toolboxes": _settings(toolboxes=list(TOOLBOXES), tier=Tier.DESTRUCTIVE),
        **_profile_settings(),
    }
    for box in TOOLBOXES:
        every[f"toolbox:{box}"] = _settings(toolboxes=[box], tier=Tier.DESTRUCTIVE)
    return every


async def _listed(settings):
    with _no_selection_env():
        server = build_server(settings, lambda space=None: FakeGateway())
    async with Client(server) as client:
        return await client.list_tools()


def _size(tool):
    return {
        "description_chars": len(tool.description or ""),
        "input_schema_chars": len(json.dumps(tool.input_schema, separators=(",", ":"))),
    }


@functools.cache
def measured() -> dict[str, dict[str, int]]:
    """Each surface's tool count and summed sizes."""
    out = {}
    for name, settings in surfaces().items():
        sizes = [_size(t) for t in asyncio.run(_listed(settings))]
        out[name] = {"tools": len(sizes), **{
            m: sum(s[m] for s in sizes) for m in TOOL_METRICS}}
    return out


@functools.cache
def measured_tools() -> dict[str, dict[str, int]]:
    tools = asyncio.run(_listed(_settings(toolboxes=list(TOOLBOXES), tier=Tier.DESTRUCTIVE)))
    return {t.name: _size(t) for t in tools}


@functools.cache
def budget():
    return tomllib.loads(BUDGET.read_text(encoding="utf-8"))


def _entries():
    """Decision id -> its entry's text (to the next entry)."""
    text = DECISIONS.read_text(encoding="utf-8")
    starts = [(m.group(1), m.start()) for m in re.finditer(r"^- \*\*(D\d+)\b", text, re.M)]
    return {d: text[s:(starts[i + 1][1] if i + 1 < len(starts) else len(text))]
            for i, (d, s) in enumerate(starts)}


def budget_line(key, caps):
    """The line a decision states a cap with, e.g.
    `budget default: 28 tools, 12700 description_chars, 23300 input_schema_chars`."""
    return f"`budget {key}: " + ", ".join(f"{caps[m]} {m}" for m in METRICS if m in caps) + "`"


def _rows():
    """(key, caps, decision) for every cap in the budget file."""
    b = budget()
    def caps(row):
        return {m: v for m, v in row.items() if m != "decision"}

    rows = [(name, caps(row), row["decision"]) for name, row in b["surface"].items()]
    per = b["per_tool"]
    rows.append(("per-tool", {m: per[m] for m in TOOL_METRICS}, per["decision"]))
    for tool, row in per.get("exceptions", {}).items():
        rows.append((f"tool:{tool}", caps(row), row["decision"]))
    return rows


# What D60 set; a cap above these needs a later decision.
FOUNDING = {
    "all-toolboxes": {"tools": 193, "description_chars": 69000, "input_schema_chars": 67100},
    "default": {"tools": 28, "description_chars": 13100, "input_schema_chars": 21500},
    "profile:dashboards-analyst": {"tools": 28, "description_chars": 13100, "input_schema_chars": 21500},
    "profile:fleet-admin": {"tools": 43, "description_chars": 11900, "input_schema_chars": 8600},
    "profile:read-only-explorer": {"tools": 15, "description_chars": 4600, "input_schema_chars": 3300},
    "toolbox:alerting": {"tools": 23, "description_chars": 10900, "input_schema_chars": 6900},
    "toolbox:cases": {"tools": 7, "description_chars": 1100, "input_schema_chars": 2100},
    "toolbox:dashboards": {"tools": 18, "description_chars": 5800, "input_schema_chars": 18000},
    "toolbox:data-management": {"tools": 18, "description_chars": 9600, "input_schema_chars": 5300},
    "toolbox:fleet": {"tools": 43, "description_chars": 11900, "input_schema_chars": 8600},
    "toolbox:observability": {"tools": 23, "description_chars": 7900, "input_schema_chars": 5600},
    "toolbox:platform-admin": {"tools": 15, "description_chars": 6300, "input_schema_chars": 6300},
    "toolbox:platform-health": {"tools": 4, "description_chars": 400, "input_schema_chars": 200},
    "toolbox:security-detections": {"tools": 31, "description_chars": 11800, "input_schema_chars": 12500},
    "toolbox:streams": {"tools": 13, "description_chars": 3700, "input_schema_chars": 2000},
    "per-tool": {"description_chars": 1300, "input_schema_chars": 3000},
}
# Every budget line any decision states, by decision. Pinned here so a
# decision's numbers edited in place (a silent supersede), or a cap citing a
# decision before D60, fails unless this table changes in the same diff: a
# decision that raises a cap adds its own entry here, beside its entry in
# docs/spec/decisions.md.
LEDGER = {
    FOUNDING_DECISION: FOUNDING,
}
_BUDGET_LINE = re.compile(r"`budget (\S+): ([^`]+)`")


# --- the budget holds -------------------------------------------------------------


def test_every_surface_is_budgeted():
    assert sorted(budget()["surface"]) == sorted(surfaces()), (
        "docs/spec/surface-budget.toml must have exactly one [surface.<name>] per budgeted "
        f"surface: {sorted(surfaces())}")


@pytest.mark.parametrize("name", sorted(surfaces()))
def test_surface_is_within_its_budget(name):
    caps, got = budget()["surface"][name], measured()[name]
    over = [f"{m} {got[m]} > {caps[m]}" for m in METRICS if got[m] > caps[m]]
    assert not over, f"surface '{name}' is over its budget ({'; '.join(over)}): {_FIX}"


def test_every_tool_is_within_its_budget():
    per = budget()["per_tool"]
    exceptions = per.get("exceptions", {})
    over = []
    for tool, got in sorted(measured_tools().items()):
        caps = {m: exceptions.get(tool, {}).get(m, per[m]) for m in TOOL_METRICS}
        over += [f"{tool} {m} {got[m]} > {caps[m]}" for m in TOOL_METRICS if got[m] > caps[m]]
    assert not over, f"tool(s) over the per-tool budget ({'; '.join(over)}): {_FIX}"


def test_exceptions_are_real_and_needed():
    per = budget()["per_tool"]
    tools = measured_tools()
    for tool, row in per.get("exceptions", {}).items():
        assert tool in tools, f"per-tool exception '{tool}' names no tool: remove it"
        needed = [m for m in TOOL_METRICS if m in row and tools[tool][m] > per[m]]
        assert needed == [m for m in TOOL_METRICS if m in row], (
            f"per-tool exception '{tool}' lifts {sorted(row.keys() - {'decision'})} but the tool "
            f"is within the per-tool cap on {sorted(set(row) - set(needed) - {'decision'})}: "
            "drop that part of the exception")


# --- a cap is raised only by a decision ---------------------------------------------


def _stated():
    """decision -> {cap key -> caps}: every budget line in docs/spec/decisions.md."""
    stated = {}
    for decision, text in _entries().items():
        for key, numbers in _BUDGET_LINE.findall(text):
            caps = {}
            for part in numbers.split(","):
                value, metric = part.split()
                caps[metric] = int(value)
            stated.setdefault(decision, {})[key] = caps
    return stated


def test_the_decisions_state_exactly_the_pinned_budget_lines():
    assert _stated() == LEDGER, (
        "the budget lines in docs/spec/decisions.md differ from LEDGER in "
        "tests/unit/test_surface_budget.py: a cap is raised by a NEW decision (its "
        "`budget ...` lines and a Why), whose lines are added to LEDGER too; a decision's "
        "numbers are never edited in place, and no decision before "
        f"{FOUNDING_DECISION} states one")
    assert all(int(d[1:]) >= int(FOUNDING_DECISION[1:]) for d in LEDGER), (
        f"LEDGER names a decision before {FOUNDING_DECISION}, which set the first budget")


@pytest.mark.parametrize("key, caps, decision", _rows(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_cap_is_stated_by_its_decision(key, caps, decision):
    entries = _entries()
    assert decision in entries, (
        f"cap '{key}' cites {decision}, which docs/spec/decisions.md does not have: {_FIX}")
    assert decision in LEDGER, (
        f"cap '{key}' cites {decision}, which LEDGER does not pin: {_FIX}; then add its "
        "budget lines to LEDGER in tests/unit/test_surface_budget.py")
    assert LEDGER[decision].get(key) == caps, (
        f"cap '{key}' cites {decision}, which states {LEDGER[decision].get(key)}, not "
        f"{caps}: write the new numbers as {budget_line(key, caps)} into the NEW decision "
        "that raises the cap, and cite it")
    assert "Why:" in entries[decision], (
        f"{decision} (cited by cap '{key}') has no `Why:`: say why the growth is worth it")
    newest = max((d for d in LEDGER if key in LEDGER[d]), key=lambda d: int(d[1:]))
    assert newest == decision, (
        f"cap '{key}' cites {decision} but {newest} states a later one: cite the newest "
        "decision (and its numbers)")


@pytest.mark.parametrize("key, caps, decision", _rows(), ids=lambda v: v if isinstance(v, str) else "")
def test_a_raised_or_new_cap_cites_a_later_decision(key, caps, decision):
    founding = FOUNDING.get(key, {})
    raised = [m for m in caps if caps[m] > founding.get(m, 0)]
    assert not raised or int(decision[1:]) > int(FOUNDING_DECISION[1:]), (
        f"cap '{key}' is above {FOUNDING_DECISION}'s value (or new) on {raised} without a "
        f"later decision: {_FIX}")
