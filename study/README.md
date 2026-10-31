# MCP savings study: data, harness and analysis

What is behind the results page ([`../index.html`](../index.html)). Every
number on that page is computed by [`build_site.py`](build_site.py) from the
files in [`data/`](data/), which were pulled from Honeycomb and analysed by
the scripts in [`harness/`](harness/).

## What was measured

Claude Code (CLI 2.1.302, effort medium) did one long Kibana job on the
web-logs and flights sample data: six steps in order (a space, two data
views, two dashboards with alert rules, an in-place dashboard change, a
read-back), then 27 checks against Kibana. Three models (Haiku 5.5,
Sonnet 5.5, Opus 5.5) × two setups × 100 blocks = 600 runs, each block one
run of every model in every setup in a seeded order, on six workers,
2026-10-17/18. Per run: list-price cost, time from the CLI's start to its
result, tool calls, and whether every check passed.

## The two setups

- **MCP**: Claude Code reaches Kibana through the tools of the
  mcp-for-kibana server, version 0.4.0, write tier; the prompt opens "Use
  only the kibana tools" and the model's environment holds no Kibana key.
- **Direct API**: Claude Code calls Kibana's REST API itself from the
  shell, with the pinned Kibana API reference, the Kibana URL and a key.

Everything else is the same: Kibana 9.5.5, the frozen harness (commit
`9d4ec85`), the built-in tools and the work directory. The data files and
scripts call the setups `with-mcp` and `no-mcp`.

## Files

| Path | What it is |
|---|---|
| `data/rows.jsonl` | One row per run: the 601 `efficiency.run` root spans (600 counted, 1 aborted and rerun) |
| `data/spans/spans.jsonl` | The CLI's own spans of those runs (`claude_code.llm_request`, `claude_code.tool.execution`, `claude_code.tool`): name, run id, duration, tool name |
| `data/cache/min-request-cache-read.json` | Honeycomb query result: least cache read per model request, per run |
| `data/cache/pilot-root-spans.json` | Honeycomb query result: the pilot's root spans (for the gap before the first study run) |
| `data/analysis.json`, `data/RESULTS.md` | The confirmatory analysis, planned before the runs |
| `data/exploratory.json`, `data/EXPLORATORY.md` | Exploratory analysis, not planned before the runs |
| `harness/` | The harness as frozen at `9d4ec85` and the analysis scripts that produced the published numbers ([which commit each came from](harness/README.md)) |
| `docs/PULL_ROWS.md` | How the rows and spans were pulled from Honeycomb |
| `build_site.py` | Writes `../index.html` from `data/` (Python standard library only) |

## Rerun the analysis

With [uv](https://docs.astral.sh/uv/), from `study/harness/`:

```
uv run python mcp_savings_analyze.py analyze --phase study \
  --rows ../data/rows.jsonl --harness-sha 9d4ec85 --out /tmp/mcp-savings
uv run python mcp_savings_explore.py --phase study \
  --rows ../data/rows.jsonl --harness-sha 9d4ec85 --out /tmp/mcp-savings \
  --spans ../data/spans/spans.jsonl \
  --min-reads ../data/cache/min-request-cache-read.json \
  --pilot-rows ../data/cache/pilot-root-spans.json
cmp /tmp/mcp-savings/analysis.json ../data/analysis.json
cmp /tmp/mcp-savings/exploratory.json ../data/exploratory.json
```

Both files come out byte for byte identical to the ones in `data/`. Then,
from the branch root, `python3 study/build_site.py` rebuilds the page.

## How the data was pulled

Each run sent one root span, `efficiency.run`, to the Honeycomb dataset
`mcp-savings`, with the run's cost, time, tokens, turns, tool calls,
checks, model, setup, block, worker and versions. The rows were pulled with
the Honeycomb MCP connector (`run_query` in raw-row mode), page by page; each
page's full-precision JSON was downloaded and merged into `rows.jsonl`. The
CLI spans and the two cache inputs were pulled the same way. The queries,
filters and columns are in [`docs/PULL_ROWS.md`](docs/PULL_ROWS.md). The
analysis reads only these files.

## Limits

- **Warm cache.** Caches were not controlled; 593 of 600 runs started with
  the prompt already cached, so the ratios describe repeated use. Cold
  against cold (one run per model and setup), MCP cost more for Sonnet 5.5
  and Opus 5.5.
- **Success does not separate the setups.** All 600 runs passed all 27
  checks; the study compares cost and time at equal success.
- **One job, one stack.** One task, one Kibana version, one Claude Code CLI
  version, one server version, one effort level.
- **List prices.** Cost is computed at Anthropic's published prices
  (fetched 2026-10-17), not what any account was billed.
- **Results describe Claude Code with each model**, not the model alone.

## License

Apache License 2.0, as for mcp-for-kibana.
