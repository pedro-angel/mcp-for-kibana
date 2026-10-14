# Review 3: skeptical client (engineering lead, being sold Honeycomb)

Rendered report.html at 1280 px with Playwright + Chromium. I read the whole page, then the heatmap, BubbleUp and Figure 14 up close. Where a finding says the sources can answer something, I checked `scripts/experiment/live_telemetry.py`, `scripts/experiment/LIVE_RERUN.md` and the 288 `record.json` files under `scripts/experiment/runs/efficiency/study4/full/`.

**Verdict:** The page is honest and traceable about the numbers. As a pitch it fails: it never says which findings needed Honeycomb, it hides that the study shipped prompts and tool output to a vendor, and its "small recipe" leaves out most of the work.

## Findings

### 1. BLOCKER: The page never says that prompts, responses and tool output went to Honeycomb
- **Where:** "How to reproduce this for a client", step 2 (env block), and the line "scrub secrets on the way".
- **Problem:** My first question is what leaves my network. The study did not just send timings. Per `LIVE_RERUN.md` and `live_telemetry.py`, it also set `OTEL_LOG_USER_PROMPTS=1`, `OTEL_LOG_ASSISTANT_RESPONSES=1`, `OTEL_LOG_TOOL_DETAILS=1` and `OTEL_LOG_TOOL_CONTENT=1`. That exported prompt text, model responses, tool inputs, and Bash/Read output (`tool.output` span events). Figure 14's step labels ("Read 475 lines of API reference") come from that content. The recipe lists none of these flags. So it doesn't match what was run, and it hides the real privacy trade-off. "Scrub secrets" covers keys, not my customers' data in tool results.
- **Fix:** Add a short "What the telemetry contains" box. Split it into (a) metadata only (spans, tokens, cost, tool names) and (b) content, which needs the four `OTEL_LOG_*` flags. Say which figures need (b): Figure 14's labels do, Figures 1–13 do not. Also state what the relay removed, using the records: known secrets were replaced (0 occurrences across the 288 runs) and identity attributes (`user.email`, account and org IDs) were dropped. Say that `OTEL_LOG_RAW_API_BODIES` was never set.

### 2. MAJOR: The page doesn't separate what Honeycomb found from what a spreadsheet could do
- **Where:** Hero ("Honeycomb answered from the telemetry"), the "Group by anything" card, Figures 1, 2 and 5–12.
- **Problem:** Figures 1, 2 and 5–12 are pivot tables over 287 one-row-per-run summaries. The study script had already produced them, and the page says so ("Honeycomb read the same answer", "agree within 3%"). A skeptic reads that as "a spreadsheet did this first". The real Honeycomb-only material gets equal billing with the reproductions:
  - joined agent and MCP-server spans in one trace (Figures 13, 14)
  - server-side per-tool latency
  - interactive drill from a heatmap region to BubbleUp to a single trace
- **Fix:** Retitle the hero to "reproduced", not "answered". Add a two-column panel near the top: "A run log + spreadsheet gives you" (Figures 1, 2, 5–12) versus "Needs distributed traces" (Figures 13, 14; the slow-tail link to HTTP 4xx failures, if the failure counts come from spans rather than the runner). Lead with the second column.

### 3. MAJOR: The "small recipe" hides custom engineering and a ground-truth checker
- **Where:** "How to reproduce", step 1 (attribute list) and step 3 (questions table).
- **Problem:** Step 1 lists about 20 `study.*` and `agent.*` attributes and says only "the runner did that". Per `LIVE_RERUN.md`, `agent.usage.tool_result_bytes`, `agent.tool.failures*` and the tool-call rollups come from hook spans and runner rollups. Claude Code's telemetry doesn't emit them. `checks_passed/total` comes from a per-task scorer that reads Kibana back. Most production agents have no such oracle. "Did it work?" is the question I care most about, and the page treats it as a free attribute.
- **Fix:** Mark each attribute as native (Claude Code / MCP server emits it), derived (needs hooks or a runner; name the code), or needs a verifier (you must write one per task). Replace "The recipe is small" with an honest effort statement.

### 4. MAJOR: The evidence links go to a private Honeycomb team I can't open
- **Where:** Every caption's "Honeycomb query", both boards, "Open the BubbleUp result", and the trace links in Figure 14 (`ui.eu1.honeycomb.io/pedro-angel/environments/test/...`).
- **Problem:** The page asks for trust through links, but a client following any of them hits a login wall. The links look like proof, but I can't open them. That is a classic demo smell.
- **Fix:** Say plainly that these links need access to the consultant's Honeycomb team, and offer a read-only invite or a walkthrough. For the key claims (BubbleUp and the two traces), embed Honeycomb's own image as was done for Figures 3 and 4, or point to the committed run records the numbers trace to.

### 5. MAJOR: Data volume and instrumentation overhead aren't answered, though the records hold some of it
- **Where:** Missing. "Sources" mentions only "1 of 34 telemetry requests not forwarded".
- **Problem:** My next questions are how much telemetry this produces and what it costs to run. The records do answer part of the first one:
  - 288 runs sent 10,124 OTLP requests (10 to 113 per run) through the relay.
  - 12 requests failed to forward, which cost one root span.
  - The record I opened splits its requests between `/v1/logs` and `/v1/traces`.
  - Byte volume and Honeycomb event counts or pricing are not in the sources.
- **Fix:** Add a "Telemetry footprint" line with requests per run (median and range) and the forward-failure rate, from the records. Say explicitly that bytes, event counts and Honeycomb cost were not measured. Don't estimate them.

### 6. MINOR: The BubbleUp tables mostly restate the experiment's own design
- **Where:** Both "What BubbleUp found" tables.
- **Problem:** For the cost band, `checks_total = 10` is "it's a build" and `calls_mcp = 0` is "no server". That's the same fact as the arm and task rows. `duplicate_calls = 0` (76% vs 100%) explains nothing. Figure 1 already shows that Opus builds without the server are the expensive ones. Only the slow-tail table (tool failures, HTTP 4xx) says something the medians didn't.
- **Fix:** Collapse the redundant rows into one line ("= Opus, without the server, build tasks; already visible in Figure 1"). Make the slow-tail failure result the BubbleUp showcase.

### 7. MINOR: The sales copy undercuts the careful numbers
- **Where:** Card headings "Group by anything", "BubbleUp explains them", "The trace tells the story"; the hero "Honeycomb answered"; "FOR A CLIENT … The recipe is small."
- **Problem:** The body is measured and self-correcting (P50 tie-breaks, the missing run, 287 vs 288). The headings read like product marketing, and that makes me discount the body.
- **Fix:** Use descriptive headings ("One summary span per run", "Heatmap + BubbleUp on the cost band", "Two traces of the same build"). Drop the superlatives.

### 8. MINOR: Two products are pitched at once, and the agent result's scope isn't stated
- **Where:** Hero and "The question".
- **Problem:** Half the page argues for mcp-for-kibana (50–71% cheaper) and half for Honeycomb. The agent result comes from four seeded Kibana tasks, 12 rounds, list-price cost, and the server author's own study. Every run passed in both setups, so no run here shows observability catching a correctness failure. I'd ask whether I'm being sold the server or the tracing.
- **Fix:** State up front that the agent result is the case study and the Honeycomb workflow is the offer. Add one sentence on scope (tasks, n per cell, list price, 100% pass in both setups, so this shows cost and time only, not failure diagnosis).
