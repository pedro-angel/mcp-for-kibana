# Review 1 — skeptical methodologist

**Verdict:** The numbers hold up. Every figure I checked matches the committed records and Honeycomb. The framing claims more than the method supports in three places: what Honeycomb "answered", what BubbleUp "explains", and an unlabelled mean-based multiplier in the hero. Fix those before consultants reuse the page.

## What I checked (all matched unless noted)

Against `runs.jsonl.gz`, `analysis.json` and `STUDY4_RESULTS.md`:

- Fig 5: $5.04 / $14.49. The 288-run total is $5.09, and the missing run cost $0.0557.
- Fig 6: 1,040/1,040 and 1,044/1,044 checks. The missing run had 4 checks.
- Fig 7: 35 of 36. The missing run's record shows 34 telemetry requests with 1 failed (connection reset).
- Fig 8: all six averages (1,910 / 33,051; 1,729 / 18,124; 1,671 / 32,182 bytes).
- Figs 9–11: averages and their ratios.
- Fig 12: model and other seconds, all six.
- Cost BubbleUp: 24 runs = 12 Opus web-logs builds + 12 Opus flights builds, all without the server; baselines 27.0%, 45.6% and 76.4%.
- Time BubbleUp: 31 runs; 83.9 vs 18.4, 67.7 vs 4.7, 71.0 vs 17.6, 64.5 vs 29.7.
- Fig 14 runs: $0.0981 / $0.4219, 27.4 / 61.0 s.
- Per-model ratio table and the Opus flights middle pair ($0.2908 / $0.3546).

Against Honeycomb, environment `test`:

- Re-fetched query 7H-fV2rCk3UX: $14.49 / $5.04.
- Fresh query ub-Qaq4cpb3k (AVG and P50 of `agent.usage.tool_result_bytes` by model and arm, root spans): reproduces Fig 8 with n = 48 per cell and 47 for Opus with the server. It also confirms that pooled P50s are unstable (finding 3).

Fig 13 matches the saved `pH-JB1yihmCd` rows. Saved query specs all filter `name = efficiency.run`, except Fig 13, which correctly filters `tools/call*`, so no averages double-count across span types.

"Honeycomb image" vs "Redrawn" labels are accurate:
- Figs 3 and 4 embed the PNGs fetched from the `query_result_image` links of pW-124hpWVWU and ux-3jHLdSd4p.
- Everything else is drawn from the result rows.
- Side note: `fig3_heat.png` (grouped by arm, bd-cddi7Qoh3) is byte-identical to `fig3_heatall.png`. It is unused, but don't reuse it as a "by arm" image.

## Findings

### 1. MAJOR — Hero and the "Sources" section present Honeycomb as an independent check, but it only re-aggregates the runner's own numbers
**Where:**
- h1 "Honeycomb answered from the telemetry"
- lede "Honeycomb read the same answer … and showed why"
- Sources: "Honeycomb's sums and averages equal the study's committed run records"

**Problem:**
- `study.total_cost_usd`, `study.wall_s`, the token counts and the checks are written onto the root span by the runner, from the same Claude Code result JSON and scorer that produce `runs.jsonl`.
- So agreement is guaranteed by construction. It proves the telemetry pipeline lost nothing (except one run). It does not confirm the finding.
- The only differences are the P50 convention and the missing run.
- A consultant could tell a client that "Honeycomb verified" the savings.

**Fix:**
- Reword the h1 and lede, e.g. "…the same answer is a few queries away in Honeycomb, from one summary span per run."
- Add one sentence in "The question" or "Sources": the root-span attributes are the runner's own measurements, so agreement with the study records shows the telemetry is complete and faithful, not that the result was independently measured.
- What Honeycomb adds is the slicing (heatmaps, BubbleUp, traces). Say that is the value.

### 2. MAJOR — "BubbleUp explains them": the cost BubbleUp mostly recovers the design factors, and one row is a pure proxy
**Where:**
- Card "Ask why / BubbleUp explains them"
- The table under Fig 3 and its paragraph

**Problem:**
- Selecting on the outcome (cost > $0.15) and reading back attributes gives correlations within that selection.
- Three of the five rows are one design variable said three ways:
  - `study.arm = no-mcp`
  - `agent.tool.calls_mcp = 0`
  - (the third, `checks_total = 10`, is just "task is a build")
- Fig 1 already shows those cells at $0.36–0.40.
- "Never a repeated identical call" (`duplicate_calls = 0`, 100% vs 76.4%) is true of **every** no-server run (144/144). The baseline gap comes from the with-server runs re-reading objects (Haiku 33%, Opus 38% have zero duplicates). It is an arm proxy, not a property of expensive runs.
- As printed, the row implies that expensive runs are distinguished by never repeating a call.

**Fix:**
- Change the card to "BubbleUp shows who they are" (the section h2 already says this).
- Add a caption line: "BubbleUp lists attributes over-represented in the selection; several are the same setup variable, and none is shown to cause the cost."
- Drop the `duplicate_calls` row or annotate it: "true of every run without the server; a proxy for the setup."
- Mark `calls_mcp = 0` as "same as the setup."

### 3. MAJOR — The hero multiplier "10 to 19 times more text" is mean-based and unlabelled, and disagrees with the companion report
**Where:**
- Lede: "without the server, 10 to 19 times more text flows back into the model"
- Fig 8 ratios

**Problem:**
- The hero sits next to median-based cost and time claims, but this range comes from averages (17.3×, 10.5×, 19.3×).
- The linked Study 4 report gives pooled medians of 16.7×, 8.6× and 12.5× (Opus 12.5× vs 19.3× here).
- Readers comparing the two pages will see different headline multipliers for the same runs.
- The note's justification for averages is sound, and I verified it: the pooled no-server distribution is bimodal (Haiku 24,229 → 43,278 bytes at the middle; Opus 16,555 → 33,215). Honeycomb's P50 lands on the upper cluster (43,278 vs the true median 33,754 for Haiku).
- But that justification appears only below the hero, and the claim that the Study 4 report "tells the same story" hides a 9–19× vs 10–19× difference in range.

**Fix:**
- Say "on average 10 to 19 times" in the lede, or quote a range robust to the choice ("about 9 to 19 times, by median or average").
- In the Figs 8–12 note, give the median ratios in one line (16.7×, 8.6×, 12.5×) and name the bimodality as the reason P50 is unreliable here. Example: "Honeycomb's P50 for Haiku without the server is 43 kB though half the runs read under 25 kB." This is a useful lesson for consultants in its own right.

### 4. MAJOR — The slow-tail BubbleUp is narrated causally, and its baseline is confounded by setup
**Where:** The paragraph after the time BubbleUp table: "The strongest signal is failure … Without the server the agent learns the API by trial and error, and each error costs another model turn."

**Problem:**
- With-server runs have zero tool failures (0 of 143), so the "other runs 18.4%" baseline mixes in a setup that cannot fail. Comparing 84% with 18% partly re-measures the arm.
- The fair comparison is within builds without the server: 31 slow runs vs 41 not slow. There the failure rate is 83.9% vs 9.8%. The association survives, but it is still an association.
- Longer runs make more calls and so have more chances to fail.
- The 60 s cut is arbitrary on a continuous distribution: 58.6, 58.7, 59.0, 59.5, 60.2 s… Unlike the cost band, which has a real gap from $0.147 to $0.226, the tail is not a separate cluster, and "31 runs" depends on the cut.

**Fix:**
- State it as "slow runs were much more likely to contain a failed call: among builds without the server, 84% of slow runs vs 10% of the rest."
- Mark the trial-and-error sentence as an interpretation, supported by the Fig 14 trace, rather than something BubbleUp showed.
- Say the 60 s threshold was chosen by eye.

### 5. MINOR — Figs 1 and 2 label cells "Median", but at n = 12 Honeycomb's P50 is one of the two middle runs, and one shown ratio differs visibly from the study
**Where:** Fig 1 caption "Median cost of a run (Honeycomb P50…)", with Opus flights shown as $0.355 and 0.25×.

**Problem:**
- The study's median is $0.323, with ratio 0.28 (CI 0.24–0.36). That is a 9.9% cell difference.
- The explanation appears only after Fig 2, in the per-model table note. A reader checking Fig 1 against the Study 4 report sees 0.25× vs 0.28× with no flag.
- Every cell is n = 12 (11 for Opus change with the server), and the page shows no interval anywhere. Two Fig 2 ratios are near 1 (Opus look-up 0.93, CI 0.85–0.99; Sonnet 0.90, CI 0.81–0.97).

**Fix:**
- In the Fig 1/2 captions, write "Honeycomb P50 (with 12 runs per cell, one of the two middle runs)".
- Mark cells that differ from the study script by more than 1%.
- Add one sentence that the study's 95% intervals for every per-task cost ratio are below 1, with a link to them.

### 6. MINOR — The "% less" column mixes sources and contradicts the hero
**Where:**
- The per-model ratio table: Sonnet "51% less" cost, "34% less" time
- Hero: "50 to 71% less", "33 to 60% less"

**Problem:**
- The "% less" column is computed from the Honeycomb ratios (0.494, 0.657), but the hero quotes the study script (0.498, 0.666).
- The same model gets 50% in one place and 51% in another, and 33% vs 34%.

**Fix:** Derive "% less" from the study-script column, which the hero cites, or label the column "(from Honeycomb)".

### 7. MINOR — Fig 14 counts and wording do not line up with the run records
**Where:** Fig 14 headers "7 model calls" and "12 model calls"; the section text "produced the same dashboard and rule".

**Problem:**
- The records give `num_turns` 8 and 11, which is the metric Fig 11 averages. The trace span counts are 7 and 12. The footnote says the counts come from traces, but a reader comparing with Fig 11 will see a mismatch.
- "The same dashboard and rule" claims too much: the scorer checks properties, not identity.

**Fix:**
- Add "(trace spans; the run record counts 8 and 11 turns)".
- Change the wording to "both passed all 10 checks".

### 8. MINOR — Fig 3 description and alt text disagree on the band
**Where:** Fig 3 caption "isolated band sits above $0.15"; alt text "between $0.22 and $0.52"; selection "$0.15 to $0.60".

**Problem:**
- The nearest run outside the band is $0.147, just under the cut, and it is visible in the image near 19:41.
- The selection is robust because the real gap runs from $0.147 to $0.226, but the text and alt text describe it differently.

**Fix:**
- Caption: "24 runs from $0.23 to $0.52; no run between $0.15 and $0.22".
- Keep the $0.15 selection and note that any cut in that gap selects the same 24.
