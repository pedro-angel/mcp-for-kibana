"""MkDocs hook: emit llms.txt and llms-full.txt from the built nav.

Route chosen over the mkdocs-llmstxt PyPI plugin (evaluated first, per the
design doc): that plugin requires `site_url` to build its links, and this
project has no site_url yet — hosting (GitHub Pages vs ReadTheDocs) is
deliberately undecided until the repo goes public. llms.txt must not bake in
a placeholder domain that would go stale, so this hook emits site-root-
relative links instead, which resolve correctly under whatever domain the
site eventually gets. It also lets us dump raw Markdown source verbatim for
llms-full.txt rather than round-tripping through rendered HTML. No new
dependency: ~localized to this file, using only mkdocs' own hook API.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.nav import Navigation

log = logging.getLogger("mkdocs.hooks.llms_txt")

_SITE_TITLE = "mcp-for-kibana"
_SITE_BLURB = (
    "MCP server for Kibana: composable toolboxes over a hexagonal core, "
    "powered by kibana-py. Lets an LLM go from a plain-English request to a "
    "real Kibana dashboard through a small, reliable tool surface."
)

# One-liners for the curated llms.txt index, keyed by each page's source path
# relative to docs_dir. Hand-maintained by design (see spec section 3) rather
# than pulled from front matter, since the page set is small and stable.
_ONE_LINERS: dict[str, str] = {
    "index.md": "What mcp-for-kibana is, the four design pillars, and MVP status.",
    "user-guide.md": "Zero to your first talked-into-existence dashboard in ~10 minutes.",
    "tools.md": "The 10 MCP tools: tiers, VizSpec inputs, return shapes, error behavior.",
    "configuration.md": "Every environment variable, tier semantics, toolbox selection.",
    "deployment.md": "stdio vs streamable HTTP, docker run, the per-request auth model.",
    "architecture.md": "Hexagonal layers, toolbox concept, VizSpec-to-Lens translation.",
    "e2e-setup.md": "One-time LM Studio + docker Kibana stack setup for the E2E gate.",
}

# Module-level: mkdocs imports this file once per build and calls the event
# functions below in order, so state set in on_nav is still there for
# on_post_build within the same process.
_nav: Any = None


def on_nav(nav: "Navigation", config: "MkDocsConfig", files: Any) -> "Navigation":
    global _nav
    _nav = nav
    return nav


def _iter_pages(items: list) -> Any:
    for item in items:
        if getattr(item, "is_page", False):
            yield item
        elif getattr(item, "children", None):
            yield from _iter_pages(item.children)


def _page_url(page: Any) -> str:
    """Site-root-relative link (no scheme/host, so it doesn't depend on
    site_url — see module docstring). mkdocs represents the home page's URL
    as "./" (directory-style urls), which would otherwise turn into the
    slightly odd "/./" once root-prefixed."""
    url = page.file.url
    if url in ("", ".", "./"):
        return "/"
    return "/" + url


def on_post_build(config: "MkDocsConfig", **kwargs: Any) -> None:
    if _nav is None:
        log.warning("llms_txt hook: no nav captured, skipping llms.txt/llms-full.txt")
        return

    site_dir = Path(config["site_dir"])
    full_parts: list[str] = []
    index_sections: list[str] = []

    for item in _nav.items:
        pages = list(_iter_pages([item])) if not getattr(item, "is_page", False) else [item]
        header = getattr(item, "title", None) or "Docs"
        bullets: list[str] = []
        for page in pages:
            src = page.file.src_uri
            one_liner = _ONE_LINERS.get(src, "")
            title = page.title or src
            url = _page_url(page)
            suffix = f": {one_liner}" if one_liner else ""
            bullets.append(f"- [{title}]({url}){suffix}")

            source_path = Path(page.file.abs_src_path)
            text = source_path.read_text(encoding="utf-8").rstrip()
            full_parts.append(f"<!-- source: {src} -->\n\n{text}\n")

        index_sections.append(f"## {header}\n\n" + "\n".join(bullets))

    llms_txt = f"# {_SITE_TITLE}\n\n> {_SITE_BLURB}\n\n" + "\n\n".join(index_sections) + "\n"
    llms_full_txt = "\n\n---\n\n".join(full_parts) + "\n"

    (site_dir / "llms.txt").write_text(llms_txt, encoding="utf-8")
    (site_dir / "llms-full.txt").write_text(llms_full_txt, encoding="utf-8")
    log.info("llms_txt hook: wrote llms.txt and llms-full.txt to %s", site_dir)
