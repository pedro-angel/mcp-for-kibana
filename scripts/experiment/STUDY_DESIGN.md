# Efficiency study: design and findings

How the mcp-for-kibana efficiency study was designed, what was decided and why, and what the runs showed. The working board for the study holds only the current state; this file holds the reasoning. Numbers in the findings come from `scripts/experiment/runs/efficiency/full/analysis.json`; signal definitions are in [SIGNALS.md](SIGNALS.md); the runner is `efficiency_run.py`.

## Decisions

Each decision was settled by the user and Claude together, one topic at a time.

### 1. What the study must answer

**Question.** Which questions should the results answer, and for whom?

**Decision.** Claude part: does mcp-for-kibana make Claude's Kibana work cheaper or shorter? Measured with and without the server on the same task: tokens and list-price cost, tool calls, iterations, time. Audience: public write-up for people weighing adoption. The local-model part is designed separately and run later on the owner's machine.

Considered:

- The study has two parts with different goals: Claude and local models. We design the Claude part first and run it in the cloud session; the local-model part waits until the owner is at the machine with LM Studio.
- Claude part: the question is efficiency, not capability. We take it as given that Claude can do the work. The study measures what the work costs with the mcp-for-kibana server versus with only a shell and Kibana's API: tokens and list-price cost, tool calls, model iterations (turns), and wall-clock time. Same task, same Kibana, same data, same success check.
- Local-model part (parked for now): can local models drive the server through a stated, multi-step task, and do smaller models lose track over a longer connected chain?
- Audience: public. A write-up to publish (e.g. LinkedIn) for people deciding whether mcp-for-kibana is worth adopting, so the results must stand on their own: a clear task, a fair comparison, numbers readable without knowing the owner's setup.
- With one workflow, the results describe that workflow only. A claim about Kibana work in general needs several different tasks (Miller, 2024, «Adding Error Bars to Evals»).

### 2. Sample data

**Question.** Which data do the agents work on?

**Decision.** Elastic's built-in web-logs sample data. Expansion to e-commerce or flights possibly later.

Considered:

- Elastic ships three sample data sets: web logs, e-commerce orders and flights. All public, one-click to load, so anyone can reproduce the study.
- Web logs fits the dashboard-and-alert chain: timestamps, response codes, geo, bytes, and a natural alert (a spike in 5xx errors).
- E-commerce and flights stay open as a later expansion.

### 3. The workflow

**Question.** Which connected chain of Kibana work does each run perform?

**Decision.** Claude part: one end-to-end prompt (explore the data view's fields → dashboard → alert rule), identical in both arms. Server arm loads dashboards, data-management and alerting at the default write tier: 24 tools.

Considered:

- Claude runs the whole chain end-to-end from one prompt: explore the web-logs data, build a dashboard of the key metrics, set an alert rule.
- Same prompt in both arms: with the server's tools, and with only a shell plus Kibana's API.
- Load only the toolboxes the chain needs: data-management, dashboards, alerting. That is 32 of the 133 tools, or 24 at the default write tier (destructive tools hidden).
- 'Explore' means understanding the data's structure (data views, fields, types), not reading log records. The server covers it with list_data_views and describe_data_view, so both arms can do it and the comparison stays fair.
- Local models get the chain step by step instead; that difference is itself a finding.

### 4. Models

**Question.** Which local models and which Claude models take part?

**Decision.** Claude part: Haiku, Sonnet, Opus at their current versions, exact model string recorded per run. Local models decided separately.

**Clarified after the runs.** The runs drive the Claude Code agent headless (`claude -p`) with each model, so the results describe Claude Code and the model together (its system prompt, built-in tools, tool loop and prompt caching), not the bare models.

Considered:

- Claude part: Haiku, Sonnet and Opus, all three tiers.
- Current versions at run time (5.5 for all three today: claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5); each run records the exact model string it reports, so the write-up is precise.
- No predictions about which model gains most from the server: we measure.
- Local models: chosen later, for the local-model part.

### 5. Arms

**Question.** Which conditions do we compare for each model?

**Decision.** Two arms per model: server vs shell + Kibana API with the official OpenAPI spec available. Both on the same Kibana version's current API.

Considered:

- Two arms per model: with the mcp-for-kibana server, and with only a shell plus Kibana's API.
- The no-server arm gets Kibana's official OpenAPI spec to read, so it isn't hobbled: the same reference a developer would use. Bundled spec: https://raw.githubusercontent.com/elastic/kibana/main/oas_docs/output/kibana.yaml (browsable: https://www.elastic.co/docs/api/doc/kibana).
- To check before the pilot: the bundled spec says the Dashboards API reference is hosted separately, so the shell arm may need a second URL for it. Pin both to the Kibana version we run.
- Both arms target the same Kibana version's current API, including the typed Dashboards API (9.4+) the server itself is built on, not the deprecated saved-objects route.
- To watch, not an arm: whether a model given the server still goes to the raw API instead of the tools.

### 6. Repetitions, time and the pilot

**Question.** How many runs per condition, and how do they fit in 12 hours of machine time?

**Decision.** 30 runs per condition, 180 runs, after a one-run-per-condition pilot. All in the cloud; needs a reachable Kibana with web-logs loaded.

Considered:

- Statistics set the count, not time or money: 30 runs per condition.
- 3 models × 2 arms = 6 conditions × 30 = 180 runs.
- Time and cost are not constraints, so no rationing.
- A pilot first, one run per condition, only to confirm the task completes and the harness captures tokens, time and steps cleanly.
- All Claude runs go in the cloud session, unattended. Its one dependency: a Kibana the session can reach, with the web-logs sample data loaded.

### 7. Budget for the Claude runs

**Question.** How much may the Claude runs cost?

**Decision.** No dollar ceiling. Each run reports its list-price cost and tokens.

Considered:

- With the owner's Max 20x subscription, the Claude runs count against the plan's usage allowance instead of a bill, so money is not a constraint; usage limits are not a concern either (the owner's note).
- The study can still answer what the work costs: the CLI computes each run's list-price dollars from its token counts, which is what someone paying for the API would pay. That number does not depend on how we pay.
- The runs share the owner's account with their own use of Claude during those 12 hours.
- Candidate to settle: no dollar ceiling; report list-price cost and tokens per run.

### 8. Snapshots

**Question.** What do we keep from each run as evidence?

**Decision.** Two layers. Per-run files (transcript, metrics, outcome, final artifacts) are the archive. Honeycomb holds one OTel trace per run, sent by the harness for both arms, with outcome, failure reason, model, arm, tokens, cost, iterations and time as attributes.

Considered:

- Layer 1, per-run files (the archive): full transcript (every prompt, tool call, response); metrics (tokens, list-price cost, tool calls, iterations, wall-clock time); outcome from the success check; final artifacts (the dashboard and alert-rule definitions produced).
- Layer 2, Honeycomb (the lens): the same run data sent as OpenTelemetry traces, one trace per run, for querying across runs: by arm, by model, tool-call sequences.
- The outcome is a first-class attribute on each run's trace: success true/false, failure reason, model, arm, tokens, cost, iterations, time. So 'success rate by arm' is a direct Honeycomb query.
- The server has built-in OTel export (KIBANA_MCP_OTEL_ENABLED: a span per tool call), but only the server arm emits it. The harness must send its own traces for both arms so they are comparable.

### 9. Controls

**Question.** What do we hold fixed so the comparisons are sound?

**Decision.** Latest Kibana 9.5 pinned and recorded, clean reset between runs, identical prompt, data, success check and settings. Only the server varies.

Considered:

- Kibana pinned to the latest 9.5 release, stood up by the cloud session itself from the repo's bundled elastic-start-local stack (Docker). Set ES_LOCAL_VERSION to the latest 9.5.x (the repo's default is 9.5.4; check whether a newer 9.5 patch exists) and record the exact version.
- Both arms target that same Kibana version and its current API.
- Same web-logs sample data, loaded by the session; Kibana reset to a clean state between runs.
- Same prompt wording in both arms; same success check.
- Sampling settings fixed and recorded; same machine and network for all runs.
- The only variable changed is the server: on or off.

### 10. The results report

**Question.** What do we do with the results, and what goes in the report?

**Decision.** Private report built from the committed run records; the owner decides when it goes public. A few example snapshots of the dashboard and alert. Statistics chosen with the data in hand, to answer the questions faithfully.

Considered:

- Every run's files are committed as the archive; every report number traces to them, never hand-typed.
- Report is a separate page, private until the owner decides to publish it.
- Includes a few (not all) snapshots of a resulting dashboard and alert rule, as examples of what was achieved.
- Statistics are not fixed in advance: with the data in hand we add or remove them so the report faithfully answers the questions that come up.

### 11. A third arm for the live rerun (R8)

**Question.** In 49 of 90 with-server runs the model curled Kibana's console proxy, `/api/esql/query` or `/internal/search/es|esql` to look at the data before choosing panels and a 5xx threshold. Should the rerun measure the server with a proper data-exploration tool next to it?

**Decision.** Settled: yes, as a third arm, `with-mcp-es`: mcp-for-kibana plus Elastic's Elasticsearch MCP server (elastic/mcp-server-elasticsearch v0.4.6, pinned by image digest), composed in the run's MCP config. Additive: `with-mcp` and `no-mcp` are unchanged, and a block runs all three arms in an order rotated by block. Setup and caveats: [LIVE_RERUN.md](LIVE_RERUN.md#the-third-arm-with-mcp-es-r8).

Considered:

- mcp-for-kibana stays Kibana-only (brief: "Not an Elasticsearch client"); the arm composes a separate server rather than adding Elasticsearch tools to this one.
- The Elasticsearch server gets a per-run API key that can only read the web-logs data stream (index `read`, `view_index_metadata`, `monitor`; cluster `monitor`), minted by the runner and invalidated after the run. Writes with it answer 403. The key reaches only that server's config, never the model's environment.
- The standalone server is deprecated in favour of Kibana's Agent Builder MCP endpoint, which needs an Enterprise licence; the local stack is basic. Its `get_mappings` cannot decode the web-logs mapping (object fields); `esql`, `search`, `list_indices` and `get_shards` work. It emits no OpenTelemetry; Claude Code's tool spans and the hook cover its calls.

### 12. The four tasks of studies 4 and 5, and their checks

_2026-10-12_

**Question.** Study 4 (Claude Code, with and without the server) and study 5 (local models, with the server) run the same four tasks. What are the tasks, and what counts as a success?

**Decision.** One definition for both studies: [tasks.json](tasks.json), read by [study_tasks.py](study_tasks.py), which both runners use for the prompt, the starting state and the success check.

- **The web-logs task.** Name the data view's time field; build a dashboard with two panels (requests over time, requests by response code); create an enabled rule that fires on more than 10 5xx responses in 5 minutes, checked every minute; retitle the response-code panel and read the rule back. Both answer lines (TIME_FIELD, RULE_ENABLED) are asked for in one closing instruction.
- **The web-logs task on flights data.** The same four steps on the flights sample data: flights over time, flights by carrier, more than 10 cancelled flights in 5 minutes.
- **Change existing objects.** The seed holds the dashboard 'Site health' (two panels on web logs) and the disabled rule 'Server errors' (more than 10 5xx in 5 minutes, every minute). The model adds a requests-by-host panel and enables the rule, in place: the seeded ids must survive and nothing new may appear.
- **Look up existing objects.** The same seed. The model reports the dashboard's panel count and the 5xx rule's name, interval and enabled state, and changes nothing.
- Every step can be done with the server's tools at the write tier. The draft also asked to rename the dashboard, raise the rule's threshold and report the threshold; the server has no tool that renames a dashboard or changes a rule's params, and `get_alert_rule` returns no params, so those steps were removed rather than measure a missing tool.
- The prompt is the arm's preamble ("Use only the kibana tools", or the API-spec instructions of decision 5), then the task. Nothing else differs between arms.

The checks:

- Every check compares against a value Kibana or Elasticsearch holds. An answer line counts only when it matches Kibana's actual value (the data view's time field, the rule's enabled state, the seeded objects' values). Checks on which tools the model called are dropped: the arm without the server calls none.
- A rule's filter is evaluated, not pattern-matched: its own query (Elasticsearch DSL, KQL with filters, the index-threshold KQL filter, or ES|QL) runs on the real index and on a probe index holding 500, 502, 503, 504 and 599 against 200, 301, 404 and 450. It passes when it selects exactly the matching documents on both. The probe index is needed because 503 is the only 5xx code in the web-logs sample: on the real data a filter for 503 alone looks correct. Range clauses on date fields are left out of the count; the time window is checked on its own.
- The rule's time field must resolve to the data view's time field (on web logs, `timestamp` is an alias of `@timestamp`, so both pass; `utc_time` fails).
- A step passes when all its checks pass; `steps_passed` counts the leading passed steps.
- Every check's result, with its expected and actual value, goes on the run's root span as `study.check.<step>.<check>.ok|expected|actual`, beside `study.task`, `study.checks_total` and `study.checks_passed`, so a failed run can be explained from Honeycomb alone.

Considered:

- The lookup task first asked which dashboard is built on web logs, with a decoy on flights. Installing the web-logs sample also installs its own '[Logs] Web Traffic' dashboard, so that question has two right answers; the task names the dashboard instead and asks the model to find the rule by what it watches.
- Tested before any pilot (`study_tasks.py replay`, live stack, Kibana 9.5.5): of the 469 rules stored from the earlier runs, 465 pass the filter and time-field checks. The four rejected: a term filter on 503 only; a terms list of 500 to 509, which misses 510 to 599; a regexp with `^…$` anchors on the analysed `response` text field, which matches nothing; a `match` query on `_source.response.keyword` with an operator Elasticsearch does not have. Every correct form passes, including filters with their own `now-5m` range and ES|QL rules that carry the threshold in the query.
- On the live stack, correct runs of all four tasks pass, and each of these fails at its step: a 503-only filter, `utc_time` as the time field, a disabled rule, a wrong TIME_FIELD answer, a panel not retitled, a change run that left the rule disabled, a wrong ENABLED answer, and a lookup run that enabled the rule.

### 14. Study 4: the runner, the plan and the stall alert

_2026-10-12_

**Question.** How does the efficiency runner run the four tasks, in what order, and how do we notice a run that hangs?

**Decision.** `efficiency_run.py --task <id>` (off by default; the first study's single task runs unchanged without it) takes the prompt, the seed and the checks from `study_tasks.py`; the runner defines none of them. `--blocks FIRST LAST` runs every (task, model, arm) cell once per block: 4 tasks × 3 models × 2 arms = 24 runs a block, 12 blocks for the study, 1 block for the pilot.

- Arms: `with-mcp` gets the tasks.json `server` preamble, `no-mcp` the `api` one. Both keep the same built-in tools and working directory with the pinned API reference; only the preamble, the server and the key differ (decision 12). The Kibana key never reaches the with-mcp model's environment (the board's running rule); the no-mcp model needs it to reach Kibana at all.
- A run is cut at 30 minutes and counts as a failure, recorded as `timed_out`.
- Every task run starts from the seed: the seeded dashboard and rule are recreated at their fixed ids before the run, and what the run created is deleted after it.
- Order: tasks and models each rotate by one place per block and the arm order flips every other block, so over 12 blocks each task leads 3 times, each model 4 times, each arm 6 times. `--skip-done` resumes an interrupted plan.
- Evidence per run: transcript (written as it streams), record, `outcome.json` with every check's expected and actual value, `artifacts.json`, and with `--live-telemetry` the scrubbed telemetry copy. The first result event is read (the fault found after the first study). `study_tasks.span_attributes(outcome)` goes on the root span, with `study.steps_passed`, `study.first_failed_step` and `study.tasks_sha` (the hash of tasks.json the run used); every component's resource carries `study.task`.
- Stall alert: when neither a transcript line nor a finished run has appeared for 40 minutes (`--stall-minutes`), the plan writes a STALL line to stderr and an entry to `<out>/stalls.jsonl`, and a RESUMED entry when progress returns. It never stops the plan. A single run is cut at 30 minutes, so 40 minutes without progress means the harness, not the model, is stuck.
- The agent-signals hook judged outside-work-directory paths against the first hook payload's `cwd`, which a leading `cd sub` moves; the runner now writes the work directory into the hook's config.

Considered:

- Smoke runs, Haiku, local telemetry only (`runs/efficiency/study4/`): on the draft tasks, the web-logs task passed and the change task with the server stopped at the rename step, the model naming the missing tools (`smoke/`); that led to the removal recorded in decision 12. On the revised tasks, change with and without the server and lookup with the server passed (`smoke2/`).

## Background research

Looked up before or during the design.

### The brief

_2026-10-10_

- Do the study properly: more repetitions, perhaps fewer local models, a sounder scientific method.
- Local models of bigger and smaller sizes; the latest Kibana version.
- Snapshots of the elements created in Kibana.
- A usual, logical chain of related work, for example: a dashboard with two panels, then an alert rule on one panel's signal, then more. Concern: the smaller models and their context.
- Tests designed to show usefulness and controlled so the results are sound. How many repetitions give valid values? What quality is required (not for arXiv), and is it feasible in a day?
- Time: once the machinery to run the tests exists, 12 hours of running time on this machine, Claude models included.
- The final report: an HTML artifact, kept locally and on claude.ai.

Sources:

- The owner's message in the design session, 2026-10-10

### Statistics for repeated agent runs

_2026-10-10_

- One pass rate, Wilson 95% interval half-width at a 50% rate: ±0.26 for 10 runs, ±0.20 for 20, ±0.17 for 30, ±0.15 for 40, ±0.12 for 60. At 100% success the lower bound is n/(n+3.84): 0.84 for 20 runs, 0.89 for 30, 0.91 for 40.
- Clopper–Pearson intervals are wider; Bowyer et al. (ICML 2025) recommend Wilson or Bayesian intervals for small samples.
- Two pass rates, Fisher's exact test, runs per arm for 80% power (approximate): about 27 for 95% vs 60%, about 72 for 90% vs 70%, about 91 for 80% vs 60%. A very large gap (90% vs 10%) is detected with 10 runs per arm (power 0.96).
- Cost and time are skewed: report mean and median with interquartile range; compare arms with a ratio of medians or of geometric means. Precision within ×/÷1.5 needs about 12–19 runs per arm at a log-scale spread of 0.5; within ×/÷1.25 needs 39–61. The pilot gives the real spread.
- With one fixed task, results describe each model on that task only; generalising needs several tasks, analysed with the task as the cluster (Miller, 2024).
- Keep each model's default sampling; lowering temperature to cut variance changes what is measured (Miller, 2024). Temperature 0 is not deterministic and shifts pass rates (Kapoor et al., 2024; Bjarnason et al., 2026). Check that local runs are not identical replicates.
- Randomise run order within blocks; start every run from a clean state (Anthropic, 2026). Fix in advance: metrics, runs per cell, stopping rule, interval methods, and a small family of planned comparisons with Holm correction.
- Stopping a cell early biases its estimate toward 0 or 1. Stopping only for futility at 0 of 10 (upper bound 0.28) is acceptable if fixed in advance.
- Report reliability as pass^k, the chance that all k runs succeed (Yao et al., 2024, tau-bench), and report cost next to accuracy (Kapoor et al., 2024).

Sources:

- https://arxiv.org/html/2411.00640v1
- Anthropic, "A statistical approach to model evaluations" (anthropic.com, 2024)
- https://arxiv.org/html/2503.01747
- https://arxiv.org/html/2406.12045v1
- https://arxiv.org/html/2407.01502v1
- https://arxiv.org/html/2602.07150v2
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://arxiv.org/html/2507.02825
- https://pmc.ncbi.nlm.nih.gov/articles/PMC9896306

### Kibana release check

_2026-10-10_

- Newest release: 9.5.5, published 2026-10-06, together with 9.4.8 and 8.19.23. No 10.x release.
- No documented breaking changes between 9.5.4 and 9.5.5.
- The Dashboards and Visualizations APIs became generally available in 9.5.0, with schema changes: short duration units (ms, min, auto) and a search response envelope of data/meta.
- Elastic's start-local script starts any version with -v (for example -v 9.5.5); without it, it takes the latest.

Sources:

- https://api.github.com/repos/elastic/kibana/releases
- https://www.elastic.co/docs/release-notes/kibana
- https://www.elastic.co/docs/release-notes/kibana/breaking-changes
- https://github.com/elastic/start-local

## Findings from the runs

### Agent signals: what the catalogue shows

_2026-10-11_

- Catalogue: scripts/experiment/SIGNALS.md (commit cc4e0b2). Three tiers: OpenTelemetry GenAI names (22 attributes), agent.* signals for any agent (about 75: outcome, effort and cost, time, reliability, safety, harness anomalies), study.* dimensions (11). Computed in analysis.json; sent to Honeycomb dataset mcp-for-kibana-study.
- Hidden failures: the harness's error flag misses most shell failures. Without the server, 236 of 295 failed calls exited 0 with an HTTP error in the output; real failure rate 0.194 against a flagged 0.031.
- First attempts: create_dashboard's first call failed validation in 90 of 90 server runs; all recovered on retry.
- Model calls drop to 0.44x with the server (95% CI 0.38-0.50). Tokens per call: 1.13x pooled (1.00-1.25), 1.45x for Sonnet; so the savings come from fewer calls, partly offset by larger ones.
- Secrets: the key leaked in 2 runs (both without the server). Environment dumps, the leading indicator, appear in 45 runs; most tried to mask the output with sed.
- Shell around the server: 49 of 90 server runs also used curl (149 calls), mostly for queries the loaded toolboxes don't offer.
- Tool busy time is about 3-4 s per run in five of six cells (Haiku without the server: 6.2 s); long tool times without the server are sleep-polling and 120 s timeouts, now separate signals.
- Gaps: output tokens, stop reason and cost only per run; model-call spans approximate; HTTP failures in shell output found heuristically; nothing visible inside the MCP server; thinking encrypted.

Sources:

- https://github.com/pedro-angel/mcp-for-kibana/blob/claude/brave-knuth-9ho76v/scripts/experiment/SIGNALS.md
- https://ui.eu1.honeycomb.io/pedro-angel/environments/test/board/nG-roJe26eDj
- scripts/experiment/runs/efficiency/full/analysis.json

### The Kibana key reached the model without the server, never with it

_2026-10-11_

- Both arms got the key the same way: environment variable KIBANA_TEST_API_KEY, set by the runner for every run (efficiency_run.py:423).
- Without the server, 2 of 90 runs put the real key into the model's context, both Haiku, both by dumping the environment; it came back in a shell command's output. In one, the model tried to mask it with sed 's/=.*KEY.*/=<redacted>/', but the pattern didn't match and the key went through.
- With the server, 0 of 90 runs exposed it, although Haiku used the shell in all 30 of its with-server runs. The server holds the key and makes the Kibana calls, so the model never handles it.
- Two in 90 is small: report it as observed, not as a rate.
- The two run logs with the key are committed in the public repo. The key belonged to a throwaway local Kibana in the cloud container, now gone; the owner chose to leave the logs as they are.

Sources:

- scripts/experiment/runs/efficiency/full/b14/20261010T210654Z-claude-haiku-5-5-no-mcp-5ec909/transcript.jsonl
- scripts/experiment/runs/efficiency/full/b25/20261010T232039Z-claude-haiku-5-5-no-mcp-870141/transcript.jsonl

### Rules left disabled: the server's only failures

_2026-10-11_

- The server creates alert rules disabled unless asked otherwise, a deliberate safety default in its spec (docs/spec/toolboxes/alerting.md).
- Twice, Sonnet with the server saw the rule come back disabled and left it: the prompt's finish condition says the rule must 'exist', while the success check requires it enabled. These are the server arm's only two failures (88 of 90 pass; 90 of 90 if a disabled rule counts).
- How rules got enabled with the server: Opus asks for an enabled rule on create in 29 of 30 runs; Sonnet creates then enables with a second call in 26 of 30; Haiku 22 on create, 8 by a second call. The default costs an extra call in about a third of the server runs, most of Sonnet's.
- Without the server no rule was left disabled.
- Prompt and check stayed fixed for all 180 runs so they remain comparable; the report shows both readings.

Sources:

- scripts/experiment/runs/efficiency/full/analysis.json
- scripts/experiment/efficiency_run.py (score)

### The b20 failure, and a metrics fault in eight runs without the server

_2026-10-11_

- b20, Sonnet without the server: the model ran a stray `cat > $TMPDIR/d.json` with TMPDIR empty, so cat waited on input. The CLI moved it to the background after 120 s; the next command (`rm /d.json`, outside the work dir) was refused in dontAsk mode; the model concluded all Bash was refused and stopped. A fair failure: Bash stayed allowed.
- Metrics fault: the runner keeps only the last result event. When a hung background task is killed at the CLI's 10-minute limit, the CLI emits a second, one-turn result. That happened in 8 runs, all Sonnet without the server; their record.json files carry wrong turns, time and tokens. The b20 record also hides its one refused command.
- Handled: analysis.json re-derives every run from its transcript at the model's own end of turn, so the report's numbers are unaffected. The raw Honeycomb dataset still carries the wrong values.
- For the next study: fix the runner to read the first result event (parked).

Sources:

- scripts/experiment/runs/efficiency/full/b20/20261010T222039Z-claude-sonnet-5-5-no-mcp-24b399/transcript.jsonl
- scripts/experiment/efficiency_run.py (parse_stream reads the last result)
- scripts/experiment/runs/efficiency/full/analysis.json

### Honeycomb for this study

_2026-10-11_

- Environment 'test' (EU). Events can't be edited; a correction means a new dataset. Deleting a dataset is permanent and needs the UI (the API key can create datasets and manage boards, not delete datasets).
- Datasets: mcp-for-kibana-study (the study's dataset: 180 agent conversations with messages, timed tool calls and the agent.* signals); kibana-efficiency (raw harness records as sent during the runs, with the known skew; kept for now); kibana-efficiency-corrected (superseded, to delete).
- Board: 'mcp-for-kibana-study' https://ui.eu1.honeycomb.io/pedro-angel/environments/test/board/nG-roJe26eDj , organised by question (outcome, effort and cost, time, reliability, safety), with Model and Arm filters.
- Agent Timeline: AI > Agent Timeline, search a run id. It groups by gen_ai.conversation.id across the environment, so only one dataset may carry the run ids.
- Exported charts are time series without legends: not usable as report figures. Tables, BubbleUp and run timelines are good for exploration. Links work only for members of the Honeycomb team.

Sources:

- https://ui.eu1.honeycomb.io/pedro-angel/environments/test/board/nG-roJe26eDj

## Facts checked during the design

| Fact | Source and date |
|---|---|
| Machine: Apple M5 Pro with 64 GB of unified memory; 19 local models on disk in LM Studio. | Checked 2026-10-10 (sysctl, lms ls) |
| mcp-for-kibana has 133 tools in 10 toolboxes. Our chain's three: dashboards 11, data-management 10, alerting 11 = 32. At the default write tier (no destructive tools) that is 9 + 7 + 8 = 24. | Counted from the source of github.com/pedro-angel/mcp-for-kibana at commit 84e995d (2026-10-04), matches the README; checked 2026-10-10 |
| Kibana 9.5.5, released 2026-10-06, is the newest release. The dev stack is pinned to 9.5.4. | github.com/elastic/kibana releases, checked 2026-10-10 |
| The dev stack's seed loads only the flights sample data. | scripts/stack.sh, checked 2026-10-10 |
| The tool definitions an agent loads take about 7,600 tokens with the dashboards, data-management and platform-admin toolboxes (24 tools), and about 10,000 tokens with alerting and cases added (37 tools). | Measured from the server's tool list, 2026-10-10; characters ÷ 3.5, so approximate |
| Every local model on disk accepts at least 131,072 tokens of context. | lms ls --json, 2026-10-10 |
| Claude list prices per million tokens, input / output: Haiku 5.5 $0.10 / $0.50, Sonnet 5.5 $2 / $10, Opus 5.5 $4 / $20, Fable 5.1 $10 / $50. | Claude API reference, cached 2026-10-06 |
| Claude Code 2.1.296 can fix the effort level (--effort) and cap the spend of each run (--max-budget-usd). | claude --help, 2026-10-10 |
