# The harness and the analysis

Copied from the study's working repository; nothing here is edited except
the project name in `pyproject.toml` and `uv.lock`.

| File | Commit it comes from |
|---|---|
| `mcp_savings.py`, `mcp_savings.json`, `tasks.json`, `study_tasks.py`, `efficiency_run.py`, `efficiency_analyze.py`, `live_telemetry.py`, `otlp_sink.py`, `agent_signals_hook.py`, `trace_anchor_server.py`, `pyproject.toml`, `uv.lock` | `9d4ec85`, the harness frozen for the pilot and the study: what ran |
| `mcp_savings_analyze.py` | `9d4ec85` (unchanged since; it produced `analysis.json`) |
| `mcp_savings_explore.py` | `82cf33a`, the version that produced `exploratory.json` (it adds the CLI-span and cold-start sections to the `9d4ec85` version) |

Comments in the code refer to files of the working repository that are not
published (a runbook, design notes). The scripts expect to sit side by
side: `mcp_savings_analyze.py` and `mcp_savings_explore.py` import
`mcp_savings.py`, which reads `mcp_savings.json`.
