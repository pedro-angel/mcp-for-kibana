"""In-band MCP docs:// resources: server self-serves selected docs/*.md
pages so any connected agent can read the manual without a repo checkout.
The pages are force-included into the wheel (see the [tool.hatch.build] section
of pyproject.toml), which is what makes them readable from an installed
package rather than only from a source tree."""

from fastmcp import Client

from kibana_mcp.adapters.mcp import docs_resources
from kibana_mcp.config import Settings, Tier
from kibana_mcp.server import build_server
from tests.fakes import FakeGateway


def _server(**settings_kwargs) -> object:
    return build_server(Settings(**settings_kwargs), lambda space=None: FakeGateway())


async def test_lists_three_docs_resources():
    async with Client(_server()) as client:
        resources = await client.list_resources()
    uris = {str(r.uri) for r in resources}
    assert uris == {"docs://user-guide", "docs://tools", "docs://troubleshooting"}


async def test_docs_resources_are_not_tier_gated():
    # Resources are read-only by nature; tier gating (which hides write/
    # destructive tools) must not touch them.
    async with Client(_server(tier=Tier.READ)) as client:
        resources = await client.list_resources()
    uris = {str(r.uri) for r in resources}
    assert uris == {"docs://user-guide", "docs://tools", "docs://troubleshooting"}


async def test_user_guide_resource_returns_full_guide():
    async with Client(_server()) as client:
        contents = await client.read_resource("docs://user-guide")
    assert len(contents) == 1
    assert contents[0].mimeType == "text/markdown"
    assert "your first dashboards" in contents[0].text


async def test_tools_resource_mentions_create_dashboard():
    async with Client(_server()) as client:
        contents = await client.read_resource("docs://tools")
    assert contents[0].mimeType == "text/markdown"
    assert "create_dashboard" in contents[0].text


async def test_troubleshooting_resource_is_extracted_and_shorter_than_guide():
    async with Client(_server()) as client:
        guide = (await client.read_resource("docs://user-guide"))[0].text
        troubleshooting = (await client.read_resource("docs://troubleshooting"))[0].text
    assert "tool_format_generation_error" in troubleshooting
    assert len(troubleshooting) < len(guide)


def test_extract_section_stops_at_next_heading():
    doc = (
        "# Title\n\n"
        "## Troubleshooting\n\n"
        "| Symptom | Cause |\n|---|---|\n| x | y |\n\n"
        "## Cleaning up\n\n"
        "more stuff that should not appear\n"
    )
    section = docs_resources.extract_section(doc, "## Troubleshooting")
    assert "| x | y |" in section
    assert "Cleaning up" not in section
    assert "more stuff" not in section


def test_extract_section_falls_back_to_whole_doc_when_heading_missing():
    doc = "# Title\n\nSome content with no matching heading at all.\n"
    assert docs_resources.extract_section(doc, "## Troubleshooting") == doc


def test_load_doc_dev_fallback_reads_repo_relative_file(monkeypatch):
    """When the wheel-bundled copy under kibana_mcp/_docs/ isn't present
    (editable/dev installs, where hatch force-include never ran), load_doc
    falls back to a repo-relative docs/ read."""
    monkeypatch.setattr(docs_resources, "_read_from_package", lambda filename: None)
    content = docs_resources.load_doc("tools.md")
    assert "create_dashboard" in content


def test_load_doc_prefers_package_copy_when_present(monkeypatch):
    monkeypatch.setattr(docs_resources, "_read_from_package", lambda filename: "PACKAGED")
    assert docs_resources.load_doc("tools.md") == "PACKAGED"


def test_tools_doc_documents_idempotent_status():
    text = docs_resources.load_doc("tools.md")
    # Assert the NEW behavior specifically. Avoid "idempotent" alone — the
    # idempotentHint column header already makes that trivially true.
    assert "status" in text and "replaced" in text
    assert "overwrite" in text.lower()
