# mcp-for-kibana site

This branch is served by GitHub Pages at
<https://pedro-angel.github.io/mcp-for-kibana/>.

| Path | What it is |
|---|---|
| [`index.html`](index.html) | The landing page about the server, written by hand |
| [`check_facts.py`](check_facts.py) | Checks the landing page's version, Kibana versions, toolboxes and measured numbers against the server source at its release tag and the study data (`python3 check_facts.py`; `--self-test` shows it catches a wrong fact) |
| [`studies/mcp-savings/`](studies/mcp-savings/) | The MCP savings study: results page, data, frozen harness and analysis ([`studies/mcp-savings/study/README.md`](studies/mcp-savings/study/README.md)) |

Each study lives in its own folder under `studies/` and is linked from the
landing page's studies list.
