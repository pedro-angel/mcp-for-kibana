# Review 4: busy decision-maker (owner)

Lens: 30 seconds, then maybe 3 minutes, tired, wants to show it to the team. Rendered with Playwright + Chromium at 1280 and 390 (light), full page, from the current report.html (1280: 15,975 px tall; 390: 19,241 px; no horizontal page scroll).

**Verdict:** The 30-second read works (the lede gives both the result and the Honeycomb "why"), but the page then becomes a 16,000 px audit trail: figures run out of order, jargon is never put away, and there are no collapsed sections, so it is not yet "great" to hand to a team.

## Findings (most important first)

### 1. MAJOR: Figures appear out of order (1, 2, 5, 6, 7, 3, 4, 8 to 14)
- **Where:** "The answer" section shows Figures 1, 2, then 5, 6, 7; "The outliers" section then shows 3 and 4. The hero cards also link "Figures 3 and 4" second, before the reader has seen 5 to 7.
- **Problem:** A reader scrolling sees Figure 2 followed by Figure 5 and assumes something is missing. This is the worst problem for swapping in your own Honeycomb snapshots: whoever replaces "Fig 5" on the board panel has to map board numbers to page positions that do not match.
- **Rewrite:** Number by position on the page, and rename the board panels to match: 1 Cost, 2 Time, 3 Total spend, 4 Checks passed, 5 Runs per task, 6 Cost heatmap, 7 Time heatmap, 8 to 14 unchanged. Easier still: drop numbers from captions and name each panel by its title ("Cost per run", "Cost of every run"), which survives reordering and snapshot swaps. Hero cards then read "Figures 1 and 2", "Figures 6 and 7".

### 2. MAJOR: Jargon is everywhere and nothing is collapsed
- **Where:** Hero cards ("One span per run", "BubbleUp explains them", "The trace tells the story"); "The question" span table (efficiency.run, claude_code.interaction, tools/call, study.* attributes); Figure 1/2 captions ("Honeycomb P50"); Figure 7 ("root spans"); Figure 10 title ("New context written to cache"); BubbleUp tables' "Attribute" column (study.arm = no-mcp, agent.tool.duplicate_calls = 0); Figure 13 caption ("tools/call <tool>"). The page has zero `<details>` elements.
- **Problem:** span, root span, trace, P50, BubbleUp, heatmap, cache-creation tokens are used before (or without) a plain explanation. The span table is the second thing after the hero and is pure plumbing.
- **Rewrite:** One plain glossary line under the lede: "Honeycomb stores each run as a trace: a timeline of every step. The summary of a run is one record (a 'span') with its cost, time and result. A heatmap plots every run as a dot; BubbleUp is Honeycomb's 'what do these dots have in common?' button." Then: P50 becomes "median" everywhere; Figure 10 title becomes "Text the model had to re-read (billed at a premium)"; move the span table, the BubbleUp "Attribute" column and the Figure 13 caption wording into a collapsed "How the data is shaped" section.

### 3. MAJOR: The answer section is buried under bookkeeping
- **Where:** Between Figure 2 and the heatmaps: the study-script vs Honeycomb ratio table and its paragraph on how P50 picks a middle run ($0.2908 vs $0.3546), Figure 6 (two full-width 100% bars), Figure 7 (runs per task, 36/36/35/36), and the "missing run" notes in Figures 5, 6 and 7.
- **Problem:** About 2,500 px of "Honeycomb agrees with the script within 3%" and "one run is missing" sits between the result and the interesting part (why). Figure 5 and Figure 6 are mostly whitespace at 1280 (a single bar pair centred in a tall card). An owner stops reading here.
- **Rewrite:** Keep Figures 1 and 2 and one line: "Honeycomb's numbers match the study's own within 3% (details below)." Move the ratio table, Figure 7 and all missing-run notes into a collapsed "Does Honeycomb agree with the study?" section. Fold Figure 6 into one sentence ("All 2,084 checks passed in both setups") rather than a chart of two identical full bars. Shrink Figure 5 to a compact bar pair or keep it only as the hero number.

### 4. MAJOR: The hero does not say in plain words why Honeycomb mattered
- **Where:** Title "What does an MCP server save an AI agent? Honeycomb answered from the telemetry." and the four hero cards.
- **Problem:** The lede's "10 to 19 times more text flows back into the model" is the best line in the report, but it is the last clause of a five-line paragraph, and "MCP server" / "telemetry" in the title mean nothing to a non-engineer on the team. The cards describe Honeycomb features (span, heatmap, BubbleUp, trace) rather than what they revealed.
- **Rewrite:** Title: "Our Kibana server makes the AI agent 50 to 71% cheaper. Honeycomb showed us why." Subtitle: "Without it, the agent reads 10 to 19 times more text, and every extra page is paid for." Cards by finding, feature second: "Cheaper on all 12 model-and-task pairs (Figure 1)"; "The 24 most expensive runs were all Opus building without the server (BubbleUp)"; "Slow runs were the agent guessing at Kibana's API and getting errors (BubbleUp)"; "Side by side: 7 quick steps vs 20 s of reading API docs (Figure 14)".

### 5. MAJOR: The BubbleUp table loses its key column on a phone
- **Where:** "What BubbleUp found" tables, 390 px.
- **Problem:** The table scrolls sideways inside its card and the visible part is "What BubbleUp found" plus the code-like "Attribute" column; the "Other runs" vs "Selected 100%" columns that carry the finding are off-screen.
- **Rewrite:** Drop the Attribute column (or move it into the collapsed data section) so the table is three columns: "What the selected runs share", "Other runs", "Selected". On phone, this fits at 390.

### 6. MINOR: Every figure ends with a heavy provenance footer
- **Where:** "REDRAWN / HONEYCOMB IMAGE ... Board: Study 4: agent cost with vs without the MCP server, panel: 'Fig N ...'. Honeycomb query: wD-hDas2GpHc." under all 14 figures.
- **Problem:** Repeated 14 times it is noise for an owner, and the board name is long. It will also go stale when you swap in your own snapshots.
- **Rewrite:** Collapse to one short link line per figure: "Open in Honeycomb" (query link). Put board names, the redrawn-vs-image distinction and the query IDs once, in Sources.

### 7. MINOR: The heatmaps are unreadable on a phone and unexplained at a glance
- **Where:** Figures 3 and 4 at 390 px; titles "HEATMAP(study.total_cost_usd)" inside the image.
- **Problem:** The Honeycomb images shrink to about 300 px wide with tiny axis text; the "isolated band above $0.15" is hard to see. The in-image title is a query string.
- **Rewrite:** Add a plain one-line takeaway above each image in bold ("The dots above $0.15 are 24 runs, all Opus building without the server") and, when you swap in your snapshot, crop to the band and annotate it with a box. Allow tap-to-enlarge on phone.

### 8. MINOR: Cut or move the "For a client" recipe and slim the Figure 12/13 detail
- **Where:** "How to reproduce this for a client" (env vars, attribute list, query table); Figure 13 tool table; Figure 12 caption "a few slow shell commands lift two of the averages".
- **Problem:** Useful for engineers, but it doubles the page's technical surface and a decision-maker's team will not act on env vars. Also small consistency nit for the team: the ratio table shows Opus "60% less" (Honeycomb) while the sibling Study 4 report's headline says 59%.
- **Rewrite:** Collapse the recipe under "How to set this up (for engineers)", keeping only the three step headings visible. Keep Figure 12's first sentence ("The saving is model time"). Move Figure 13 into the collapsed section. Use the study-script percentages (71 / 59 / 50) in any "% less" column so both reports quote the same numbers.

Counts: blockers 0, majors 5 (findings 1 to 5).
