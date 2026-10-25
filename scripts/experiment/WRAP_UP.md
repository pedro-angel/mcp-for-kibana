# Study wrap-up (2026-10-12)

Where the mcp-for-kibana studies stand after the 10–12 October sessions, for
designing the next ones from a clean start. Detail lives in the files linked
here; this page is the single overview.

## Status

All runs are stopped. Nothing is running and nothing is scheduled.

| Study | What ran | State | Data |
|---|---|---|---|
| 1. Claude Code efficiency | 180 runs: Claude Code (`claude -p`) with Haiku, Sonnet and Opus 5.5 × with / without the server × 30 | Complete, analysed, reported | `runs/efficiency/full/` (`analysis.json`, `honeycomb_crosscheck.json`) |
| 2. Claude Code rerun, live telemetry | 266 of 270 runs (3 models × 3 arms × 30; blocks 1–29 complete, 5 of block 30), all 266 passing | Stopped early; not analysed (tools ready: `efficiency_analyze.py` 3-arm, `live_crosscheck.py`, `live_report.py`) | `runs/efficiency/live/`; Honeycomb dataset `mcp-for-kibana-live` |
| 3. Local models (LM Studio, Mac) | Pilot (16 runs) plus 45 of 192 runs (4 models × 4 chain levels × 12) | Stopped early; not analysed | branch `claude/local-study`, `runs/local/`; Honeycomb dataset `mcp-for-kibana-local` |

Stop commits: 444253b (study 2), 015682b on `claude/local-study` (study 3).

## What study 1 showed

Published reports (private until the owner shares them):

- Results: https://claude.ai/artifact/7oadByxVLjG3YHW8FmNbZa — with the server,
  Claude Code's median cost per run was 0.37× (Haiku), 0.47× (Sonnet) and 0.39×
  (Opus); every 95% interval below 1. Pass rates 88/90 with, 89/90 without.
  The saving comes from fewer model calls (0.44×), not cheaper calls.
- Honeycomb as analyst: https://claude.ai/artifact/PSAF4iTPhcKFaEsZj461ip —
  Honeycomb reproduced 309 of 320 numbers (every count exactly; medians within
  its percentile method); it cannot compute bootstrap intervals.

## What we learned about designing and running these studies

Design
- Name the agent, not just the model: these runs measure Claude Code with a
  model (its prompt, tools, loop and caching), not the bare model.
- Write the success check and the prompt together; study 1's prompt said the
  rule must "exist" while the check required it enabled (2 Sonnet failures).
- With an explicit prompt every run passes, so pass rate stops separating the
  arms; decide up front which measures carry the comparison (cost, calls,
  time, errors).
- If the server arms keep the key out of the model's environment, the prompt
  must not tell those arms the key is in an environment variable.
- Text-matching checks are brittle (bold Markdown, narrow regexes rejected
  correct local answers); prefer checking Kibana's state over the model's text.
- Decide the per-run timeout and how timed-out runs are reported before the
  pilot.

Running
- Pilot first, then freeze the harness; study 3 changed scoring and the runner
  mid-run, and a telemetry bug stalled it for 4 hours. Add a stall watcher
  from the start.
- One runner per Kibana stack: each run's reset deletes other runs' objects.
- Read the first final result event from `claude -p`; a killed background task
  can add a second one (8 runs in study 1 had wrong metrics until re-derived).
- Keep secrets out of the model's environment entirely: the Kibana key reached
  the model in 2 of 90 shell-arm runs, and Bash inherited the Honeycomb key.
- The studies ran on Kibana 9.5.5, which kibana-py does not support (its
  `SUPPORTED_VERSIONS` are 9.5.4 and 9.4.7): their run records are study
  evidence, not evidence for a supported version. A study stack is started
  with `ES_LOCAL_VERSION=9.5.5 scripts/stack.sh up && scripts/stack.sh seed`,
  and the pinned 9.5.4 cannot open its volume. Before `make dod`, re-provision
  at the pin: `scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh
  seed` — the DoD gate refuses any other version on the seeded-stack criteria
  (D53).

Telemetry and Honeycomb
- Organic telemetry works: Claude Code's own traces, events and metrics (beta
  traces), the server's spans (joined via TRACEPARENT), a silent hook for
  derived agent signals, and a runner span per run. See `LIVE_RERUN.md`.
- Route study data to its own dataset (`OTEL_SERVICE_NAME`), set
  `gen_ai.conversation.id` per run (Claude Code's session id is shared), and
  drop identity attributes.
- Claude Code does not emit MCP tool outputs; Honeycomb's agent timeline needs
  GenAI names; its charts are time series, so report figures come from our own
  analysis. Signal catalogue: `SIGNALS.md`.
- Every number twice (our scripts and Honeycomb) found no data errors and is
  worth keeping.

Product findings (mcp-for-kibana)
- `create_dashboard` failed its first call in 90/90 server runs (a validation
  bug); fixed in 48b865b, 0/12 live afterwards.
- Models went around the server with curl to explore data (49/90 runs).
  The server stays Kibana-only (docs/spec/brief.md); the third arm pairs it
  with Elastic's Elasticsearch MCP server 0.4.6, which is deprecated and whose
  `get_mappings` fails on the web-logs data stream.

## Open items for the owner

- `make dod`: GO on every criterion except `image_smoke` (the cloud sandbox's
  image build can't verify the proxy CA). Choose: run the gate on the Mac or CI,
  mark it n/a in `dod.config`, or pass the proxy CA to the build (default-off).
- The partial data of studies 2 and 3 stays as is until a new study is
  complete; it may still help fine-tune the next design.

## Files

- Design record and decisions: `STUDY_DESIGN.md`
- Signal catalogue: `SIGNALS.md`
- Runners: `efficiency_run.py` (+ `--live-telemetry`, arm `with-mcp-es`),
  `local_run.py`; runbooks `LIVE_RERUN.md`, `LOCAL_STUDY.md`
- Analysis: `efficiency_analyze.py`, `efficiency_crosscheck.py`,
  `local_analyze.py`; reports `efficiency_report.py`, `honeycomb_report.py`
