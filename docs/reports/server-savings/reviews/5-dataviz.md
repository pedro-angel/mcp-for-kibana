# Review 5: data visualization and accessibility

Reviewer role: dataviz and accessibility designer. I loaded the `dataviz` skill before starting. The page was rendered with Playwright and Chromium (`/opt/pw-browsers/chromium`) at 1280 and 390 px wide, in light and dark mode, and at 1280 light with reduced motion. All screenshots are in `review-viz/`: `full-*.png` and per-figure crops named `fig-N-{1280,390}-{light,dark}.png`.

**What passes.** Series colours match the sister Study 4 report exactly: #2a78d6 / #eb6834 in light mode and #3987e5 / #d95926 in dark. Neither width scrolls horizontally (scrollWidth equals the viewport at 390 and 1280). Every bar starts at zero, and the axes are honest (Fig 5 runs from $0 to $15, Fig 12 from 0 to 60 s, Fig 14 puts both traces on one shared scale). Each of the 14 figures has a legend or a direct label, so identity never depends on colour alone. The real Honeycomb PNGs already sit on a white frame (`--img-bg:#fff`), which reads well in dark mode (`fig-3-1280-dark.png`), and both have alt text with numbers in it. With reduced motion the tooltip transition drops to 0s. The page has no `[hidden]` elements and no toggles: the only interactive controls are the 148 `[data-tip]` tooltips, which are tested in finding 4. Pointer hover works (`hover-fig-1-1280-light.png`).

## Findings

### 1. MAJOR: The two real Honeycomb PNGs (Figs 3–4) can't be read on a phone and look soft on desktop
- **Where:** `fig-3-390-*.png`, `fig-4-390-dark.png`, `fig-3-1280-dark.png`
- **Problem:**
  - The PNGs are 800×344 px, an aspect ratio of 2.33:1. At 1280 they are scaled up to 924×397, which blurs them. They also leave about 130 px of empty space inside the 942×530 (16:9) figure body.
  - At 390 they shrink to 282×121. Axis ticks and the 1/3/4/6 count legend come out at roughly 4–5 CSS px, which can't be read.
  - The teal heatmap colour means count of runs, not setup. A reader trained on blue/orange could misread it, and the caption doesn't say what the colour means.
- **Fix:**
  - Re-export both images from the query image link at 16:9 and 2× resolution, for example 1600×900. They will then fill the slot exactly, the same way the planned screenshots will.
  - Wrap each `<img>` in `<a href="…full-size…">` and add a visible "Open full size" link under it.
  - Below 760 px, let the frame scroll sideways (`overflow-x:auto`, `min-width:640px` on the img), or crop to the plot area.
  - Add to each caption: "Colour = number of runs in the cell (darker = more); setup is not encoded."
  - Keep the white frame. Dark mode needs it.

### 2. MAJOR: The figure slot isn't a clean swap target for a 16:9 screenshot
- **Where:** `fig-1-1280-light.png`, `fig-5-1280-light.png`, `fig-14-1280-light.png`, `fig-1-390-light.png`
- **Problem:**
  - `.fig-body` is the 16:9 box, but it holds the legend and the `.fig-note` takeaway sentence as well as the chart. If the owner replaces the chart with a 16:9 image (942×530 at 1280), the body overflows to about 600+ px. If they replace the whole `.fig-body`, the takeaway sentence is lost.
  - The 16:9 rule only applies at 760 px and wider.
  - Fig 14 is `.wide` (942×605) and holds two traces side by side, so one 16:9 screenshot can't replace it.
  - At 390, any pasted 16:9 image becomes about 300×169. That is unreadable for dense panels such as Fig 1/2 (12 bar pairs) and Fig 14 (36 span rows).
- **Fix:**
  - Restructure each figure as `figcaption` → `.fig-plot` (the only 16:9 element; it holds only the chart or `<img>`) → `.fig-legend`/`.fig-note` → `.src`. A swap then replaces one element.
  - Split Fig 14 into 14a and 14b, each with its own 16:9 slot, or plan for one stacked screenshot that is not 16:9.
  - Make every pasted image a link to its full-size file.
  - For Figs 1, 2 and 14, consider keeping the redraw as the phone fallback below 760 px, or as a `<details>` "Data" table.

### 3. MAJOR: Figures appear out of numeric order
- **Where:** `full-1280-light.png`. The reading order is 1, 2, 5, 6, 7, 3, 4, 8 … 14.
- **Problem:** The figure numbers are tied to the Honeycomb panel names ("Fig 5 · Total spend…"), not to where they sit in the report. A reader meets Figure 5 before Figure 3. Cross-references such as "Figures 3 and 4" in the hero cards send readers backward and forward.
- **Fix:** Number figures in reading order: 5→3, 6→4, 7→5, 3→6, 4→7. Keep the Honeycomb panel name only in the `.src` line ("panel: 'Fig 5 · …'"). If the board can be renamed instead, rename it to match.

### 4. MAJOR: Keyboard focus never shows the tooltip, and the page has 148 tab stops that say nothing
- **Where:** `focus-tab-1280-light.png` (focus ring on the first Fig 1 row; no tooltip)
- **Problem:**
  - When Tab moves focus to an element that is off screen, the browser scrolls it into view. That fires the `scroll` listener, which hides the tooltip. I confirmed it: after the 5th Tab the tip's class is `''` and its opacity is 0.
  - Keyboard users get 148 stops with a focus ring and nothing else.
  - At 390, 14 labels in Fig 14 are cut off with an ellipsis ("Shell: Read data view fields…"). The tooltip is the only way to recover the full text, and touch users have no hover.
- **Fix:**
  - In `hide` on scroll, skip hiding when `document.activeElement` has `data-tip`, and re-run `show` for it instead.
  - Or drop per-row `tabindex` and give each figure one tab stop plus a `<details><summary>Data</summary><table>` holding the rows.
  - In Fig 14 at 520 px or less, let `.wf-lab` wrap to two lines instead of `text-overflow:ellipsis`.

### 5. MINOR: Medians are shown with no spread or sample size, and Fig 7 has no reference line
- **Where:** `fig-1-1280-light.png`, `fig-2-*.png`, `fig-7-1280-light.png`
- **Problem:**
  - Figs 1 and 2 plot P50 of 12 runs per bar with no interval and no n. The claim "every one of the 12 pairs cheaper" deserves at least a stated n, because the heatmaps show a wide spread.
  - Fig 7's whole message is "36 per cell, one missing", but the chart has no x axis and no line at the design target of 36. The 35 bar is only about 15 px shorter.
- **Fix:**
  - Add "n = 12 runs per bar" to the Fig 1/2 subtitles.
  - Optionally draw a P25–P75 whisker. Honeycomb can return P25/P75 in the same query, and a screenshot of a P50 panel won't show spread either.
  - In Fig 7, draw a dashed reference line labelled "design: 36" and say "35 of 36" next to the short bar.

### 6. MINOR: The two kinds of ratio point in opposite directions
- **Where:** `fig-1-1280-light.png` ("0.19×") compared with `fig-8-*.png` through `fig-11-*.png` ("17.3× less")
- **Problem:** Figs 1, 2 and 5 and the hero tile use with÷without, where smaller is better. Figs 8–11 use without÷with with the word "less". Readers have to flip the comparison between sections.
- **Fix:** Use one convention everywhere. Either say "0.06× (17× less)" in Figs 8–11, or put a header such as "without ÷ with" above that column.

### 7. MINOR: The neutral greys are too faint, and the series orange is reused for a status
- **Where:** `fig-12-1280-light.png`, `fig-12-390-dark.png`, `fig-14-1280-light.png`
- **Problem:**
  - The "Everything else" grey (`--rest`) has a contrast of 1.82:1 against the surface in light mode and 2.24:1 in dark. Both are below the 3:1 minimum for non-text graphics.
  - The "Failed" label in Fig 14 is in `--without` orange. That reuses a series colour as a status colour, and orange already means "without".
- **Fix:**
  - Set `--rest` to about #8f99a7 in light mode (at least 3:1) and to #6b7584 in dark (3.7:1).
  - Render "Failed" in ink, with a ✕ icon and bold weight, not in the series colour.

### 8. MINOR: Sparse charts are stretched to fill 16:9, and the "Honeycomb image" badge uses the series blue
- **Where:** `fig-5-1280-light.png`, `fig-6-1280-dark.png`, `fig-3-1280-dark.png`
- **Problem:**
  - Fig 5 (two bars) and Fig 6 (two bars, both at 100%) float in about 530 px of empty space. In Fig 5 the "0.35×" label sits about 150 px from the bars.
  - The "Honeycomb image" badge is drawn in `--with` blue, so it reads as "with the server". The "Redrawn" badge is grey. The difference between the two badges is clear in wording but muddled in colour.
- **Fix:**
  - For redraws that stay, drop the forced aspect ratio (`.fig.compact .fig-plot{aspect-ratio:auto}`) and place the ratio next to the bars.
  - Make both badges neutral ink and tell them apart by an icon plus the word ("▣ Honeycomb image" / "↻ Redrawn from query").
  - Put the screenshot's capture time in the `.src` line.

**Verdict:** Not ready yet. The data, colours and scales are honest and consistent, but the planned screenshot swap needs a clean 16:9 slot per figure, the two PNGs need higher resolution and a full-size link, and the figure order and keyboard tooltips need fixing first.
