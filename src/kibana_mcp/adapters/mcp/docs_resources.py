"""Runtime loader for the project docs shipped as in-band MCP resources.

Single source of truth: the Markdown lives in git under `docs/*.md`. The
wheel duplicates the two pages these resources need under
`kibana_mcp/_docs/*.md` via hatch's `force-include` (see the
`[tool.hatch.build.targets.wheel.force-include]` table in pyproject.toml) so
an installed package can serve them with no repo checkout present. This
module is the runtime half: it tries the packaged copy first via
`importlib.resources`, and falls back to a repo-relative read for
editable/dev installs where force-include never ran (it only fires at wheel
build time).

Stdlib-only by design, matching the import-linter contract that forbids
`adapters.mcp` from importing `kibana` — nothing here needs it anyway.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

_TROUBLESHOOTING_HEADING = "## Troubleshooting"


def _read_from_package(filename: str) -> str | None:
    """The wheel-bundled copy under kibana_mcp/_docs/. None if missing."""
    resource = importlib.resources.files("kibana_mcp") / "_docs" / filename
    if resource.is_file():
        return resource.read_text(encoding="utf-8")
    return None


def _read_from_repo(filename: str) -> str:
    """Dev/editable fallback: walk up from this file to the repo root
    (src/kibana_mcp/adapters/mcp/docs_resources.py -> repo root is four
    parents up) and read docs/<filename> directly."""
    repo_root = Path(__file__).resolve().parents[4]
    return (repo_root / "docs" / filename).read_text(encoding="utf-8")


def load_doc(filename: str) -> str:
    """Load a docs/<filename>.md file's raw content: packaged copy first,
    repo-relative fallback second."""
    content = _read_from_package(filename)
    if content is not None:
        return content
    return _read_from_repo(filename)


def extract_section(markdown: str, heading: str) -> str:
    """Extract the section starting at an exact `## Heading` line through
    (not including) the next `## `-level heading, or end of document.

    Tolerant fallback: if `heading` isn't found verbatim, return the whole
    document rather than erroring — a stable-but-imperfect resource beats a
    broken one if the guide's heading text ever drifts.
    """
    lines = markdown.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return markdown
    end = next(
        (j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines)
    )
    return "".join(lines[start:end])


def user_guide() -> str:
    """The full getting-started guide."""
    return load_doc("user-guide.md")


def tools() -> str:
    """The tool reference page."""
    return load_doc("tools.md")


def troubleshooting() -> str:
    """The user guide's Troubleshooting section, extracted."""
    return extract_section(load_doc("user-guide.md"), _TROUBLESHOOTING_HEADING)
