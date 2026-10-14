# Server savings report (Honeycomb)

Source of the report "Does the mcp-for-kibana server make an AI agent's Kibana
work cheaper and faster?", published privately at
(private link removed). Every number comes from
Honeycomb queries on the study 4 runs (environment `test`, dataset
`mcp-for-kibana-study4`, 2026-10-12 17:10–20:10 UTC).

| File | What it is |
|---|---|
| `build.py` | Builds `report.html` from the templates and `data/`. Run `python3 build.py`. |
| `body.tmpl.html`, `page.tmpl.html` | Page text and styles. |
| `report.html` | The built page, identical to the published artifact. |
| `data/*.json` | Honeycomb query results the figures are drawn from. |
| `data/*.png` | Honeycomb's own chart images (the heatmaps use them). |
| `reviews/` | The five reviews of an earlier draft. |

Instructions for this report (from the owner):

- The subject is the usefulness of the mcp-for-kibana server; Claude Code is only the agent doing the work.
- Every number comes from Honeycomb's calculations; Honeycomb is named only as the tool used.
- No study names or references to other studies or reports.
- Every numbered figure links to its Honeycomb chart, on one of two boards:
  "mcp-for-kibana server: cost and time saved"
  (https://ui.eu1.honeycomb.io/pedro-angel/environments/test/board/5q-7tCefHafG) and
  "mcp-for-kibana server: where the savings come from"
  (https://ui.eu1.honeycomb.io/pedro-angel/environments/test/board/25-8QuCXHevA).
  Panels are named "Fig N · title", matching the report.

Open on 2026-10-14: the first board's panels are not in figure order, and its
Fig 3 is now two stat panels (total spend with and without the server) that
nobody has checked in the Honeycomb UI.
