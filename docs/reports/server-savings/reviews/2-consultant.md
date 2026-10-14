# Review 2: client-facing consultant lens

Reviewer stance: a consultant who would put this page in front of a client running AI agents, to sell a Honeycomb engagement.

**Verdict:** The page is honest and well sourced, and Figure 14 plus the slow-run BubbleUp would land with a client. But it is framed as a product study ("what does our MCP server save?") rather than the client's question ("what are my agents costing me, and why?"). The how-to-reproduce recipe stops short of what you need to scope a project. Reframe and extend it before showing it to a client; it does not need a rewrite.

---

## 1. BLOCKER: The page answers our product question, not the client's
**Where:** H1 ("What does an MCP server save an AI agent? Honeycomb answered from the telemetry."), hero paragraph, "The question" section, footer ("Study 4 of mcp-for-kibana").
**Problem:** A client does not care whether mcp-for-kibana beats curl. They want to know three things: what each agent run costs, where the money and time go, and which design choice (model, tool, prompt) changes that. The page tells the story the other way round. It sells the MCP server, with Honeycomb as the witness. If a consultant shows it as is, it reads as a vendor's own benchmark of its own product. Also, the title's "Honeycomb answered" overstates Honeycomb's role. The study script produced the answer first, and Honeycomb reproduced it (the body says this honestly: "read the same answer").
**Fix:** Add a client framing on top, or make a client edition, without changing the study. Suggested H1: "Where does an AI agent's money go? One trace per run answers it." Then: "Here, the choice being compared was a purpose-built tool server versus raw API access. For you, it might be model tier, prompt version or tool set." Change "Honeycomb answered" to "Honeycomb reproduced the study's answer in a few queries, and showed why." Move "Study 4 of mcp-for-kibana" into the Sources section.

## 2. MAJOR: The reproduction recipe is not yet something you can scope a project from
**Where:** "How to reproduce this for a client", steps 1–3.
**Problem:** It says what was instrumented here, but not what a client would have to build. Four things are missing:
- **Effort and roles:** no estimate at all. How many days to add a root span, how many to wire the trace context, and how many to build the boards?
- **Cost source:** `study.total_cost_usd` came from Claude Code's own result output. Most client agents (LangGraph, OpenAI Agents SDK, in-house loops) do not report a cost, so the client needs a price table times token counts.
- **"Did it work":** `checks_passed` depended on a scripted verifier reading Kibana back. This is the hardest and most valuable part for a client, and the recipe mentions it in one line.
- **Agent coverage:** the only wiring shown is Claude Code env vars. That includes `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`, a beta flag, which I would not put on a client slide without caveat.

**Fix:** Add a short "Scoping a client project" block with three columns:
- **What you instrument:** root span per agent run; model and tool child spans; an outcome check.
- **What it takes:** for example, "root span + attributes: ~1 day; trace-context propagation into tool servers: 1–3 days per server; cost from price table: ~0.5 day; outcome checks: depends on the task, the biggest item; boards: ~1 day". Give ranges and label them as estimates.
- **Prerequisites:** OTel SDK, a collector or relay, a Honeycomb environment.

Add one line for non-Claude-Code agents: any OTel-instrumented agent framework works the same way, as long as the run's trace context reaches the tool servers.

## 3. MAJOR: Custom `study.*` attributes are the recipe, and the GenAI semantic conventions the study already emits are never mentioned
**Where:** Step 1's attribute list (`study.run_id, .arm, …`, `agent.tool.failures*`). The page never mentions gen_ai.
**Problem:** Clients will ask "is this standard?" In fact the relay in `scripts/experiment/live_telemetry.py` (`enrich`, lines ~278–310) adds the OTel GenAI attributes: `gen_ai.conversation.id`, `gen_ai.operation.name`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and `gen_ai.tool.name`. It does this specifically so that Honeycomb's agent/AI-conversation timeline view keys on each run. That is both the portability story and a Honeycomb feature a client would want to see, and the page leaves both out. "arm" is also study jargon a client will not know.
**Fix:** In step 1, split the attributes into two lists:
- **Standard (OTel GenAI):** model, token usage, tool name and conversation id, using the `gen_ai.*` names.
- **Your own:** run outcome, cost, check results, tool-result bytes, with a client-neutral prefix such as `app.agent.*`.

Rename "arm" to "variant" or "setup" everywhere in client-facing text, including the panel query column. If the agent timeline view renders these runs, add one screenshot or link. If it does not, say so rather than leaving it implied.

## 4. MAJOR: The two wow moments are not where a client will look first, and the stronger one is undersold
**Where:** The BubbleUp tables come after Figures 1, 2, 5, 6 and 7. Figure 14 is near the end. The hero card "Ask why" leads with the cost BubbleUp.
**Problem:** The cost BubbleUp ("all 24 expensive runs were Opus without the server") is honest, but a client may shrug: the most expensive model on the costlier setup is the most expensive. A plain group-by shows the same thing. The real wow is the **time** BubbleUp. Slow runs were driven by failed tool calls: 84% had one, mostly HTTP 4xx from the API, against 18% of other runs, and 68% had a failure the agent never retried. That is a "you didn't know this, and it is costing you" insight that transfers to any client. The second wow is **Figure 14**: the same task, $0.098 and 7 steps against $0.422 and 12 steps, with the first 20 s spent reading docs. Meanwhile, Figures 5–7 (spend, pass rate, sample count) are housekeeping and sit between the reader and the insight.
**Fix:** Reorder to headline result (one figure), then Figure 14, then the time-BubbleUp finding, then the mechanism (Figures 8–12). Move Figures 5–7 and the median-tie-break paragraph to an appendix. Rewrite the "Ask why" hero card around failures: "Select the slow runs: BubbleUp shows 84% hit a failed API call, against 18% of the rest." Consider making "Errors cost turns, and turns cost money" a pull quote.

## 5. MAJOR: The board links point into a personal "test" environment a client cannot open
**Where:** Every caption, plus the "Board:" links (`ui.eu1.honeycomb.io/pedro-angel/environments/test/...`).
**Problem:** In a client meeting, clicking a link will hit a Honeycomb login or a 404. The person's team slug and an environment called "test" also look unpolished in front of a client. The two hero links ("Board: Study 4 …") are offered as the deliverable, but no client can use them.
**Fix:** Make the boards client-viewable, or label the links "requires access to the study's Honeycomb team" and add one screenshot of each board as it actually looks in Honeycomb. Today only Figures 3–4 are Honeycomb's own rendering. For a Honeycomb pitch, at least one real board screenshot matters more than redrawn bars, because clients need to see the product, not our charts. Consider a demo environment with a neutral name.

## 6. MINOR: This is a controlled benchmark, and the page does not say how it maps to production monitoring
**Where:** "How to reproduce this for a client" intro ("Any team running AI agents can ask the same question…").
**Problem:** Here every run had a scripted outcome check and ran in a lab with 12 repeats per cell. A client's production agents have neither, and the client will ask, "does this work on my live traffic?" Without the caveat, a sharp client will spot the gap and doubt the rest.
**Fix:** Add two sentences:
- **Benchmark mode** (this study): A/B a design choice before shipping, with outcome checks.
- **Production mode:** the same root span and attributes on live runs, plus a cheaper success signal (user accepted, task completed, error-free) and triggers on cost per run or failure rate.

Mention event volume and sampling in one line, since clients will ask what the Honeycomb bill looks like.

## 7. MINOR: Two causal sentences say more than the data shows
**Where:** Fig 10 caption ("Cache writes are billed above normal input, so this tracks the cost gap closely"). BubbleUp cost table rows "No Kibana tool calls" and "Never a repeated identical call".
**Problem:**
- **Fig 10 caption:** the cache ratios are 5.8×, 3.0× and 4.7×, while the cost ratios are about 3.4×, 2.0× and 2.5×. The cache gap goes the same direction as the cost gap but does not "track it closely". A client's analyst will check.
- **"No Kibana tool calls":** this is the same fact as "without the server", restated.
- **"Never a repeated identical call":** 100% vs 76%. Shown without comment, it reads as if repeated calls make runs cheaper, which invites a question you cannot answer well.

**Fix:**
- Change "tracks the cost gap closely" to "moves with the cost gap".
- Drop the duplicate-calls row, or add a note that it is a side effect: these runs were Opus builds, which rarely repeated calls.
- Mark "No Kibana tool calls" as "same as setup = without".

## 8. MINOR: Jargon density on the client path
**Where:** Throughout the page: "P50", "root span", "arm", "rounds", "list-price cost", "TRACEPARENT", "BubbleUp's measure view", `efficiency.score, .reset`, "geometric mean of the four per-task ratios".
**Problem:** Each of these is fine on its own, but together they turn a client page into an engineering appendix. The hero is clean. The jargon builds up from "The question" onward. Tone is otherwise good: it is sober and not brochure-like, apart from the card label "Group by anything".
**Fix:** For a client edition, add a short glossary sidebar (span, trace, P50 = median, BubbleUp). Replace "P50" with "median" in captions. Keep the query syntax only in the reproduce table. Change "Group by anything" to "One row per run".

---
Counts: 1 blocker, 4 majors, 3 minors.
