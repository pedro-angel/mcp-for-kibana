# Example snapshots — efficiency study (full)

One passing run (`record.json` `success: true`) per model × arm for
claude-opus-5-5 and claude-haiku-5-5, each chosen with a panel count typical
for its cell (the cell's mode or median across its 30 runs).

| Run id | Model | Arm | Panels | Dashboard | Rule |
|---|---|---|---|---|---|
| `20261010T190321Z-claude-opus-5-5-with-mcp-c577ba` (b01) | claude-opus-5-5 | with-mcp | 7 | [png](20261010T190321Z-claude-opus-5-5-with-mcp-c577ba-dashboard.png) | [png](20261010T190321Z-claude-opus-5-5-with-mcp-c577ba-rule.png) |
| `20261010T190417Z-claude-opus-5-5-no-mcp-963c83` (b01) | claude-opus-5-5 | no-mcp | 8 | [png](20261010T190417Z-claude-opus-5-5-no-mcp-963c83-dashboard.png) | [png](20261010T190417Z-claude-opus-5-5-no-mcp-963c83-rule.png) |
| `20261010T184612Z-claude-haiku-5-5-with-mcp-31c334` (b01) | claude-haiku-5-5 | with-mcp | 5 | [png](20261010T184612Z-claude-haiku-5-5-with-mcp-31c334-dashboard.png) | [png](20261010T184612Z-claude-haiku-5-5-with-mcp-31c334-rule.png) |
| `20261010T210036Z-claude-haiku-5-5-no-mcp-3f484e` (b13) | claude-haiku-5-5 | no-mcp | 5 | [png](20261010T210036Z-claude-haiku-5-5-no-mcp-3f484e-dashboard.png) | [png](20261010T210036Z-claude-haiku-5-5-no-mcp-3f484e-rule.png) |

## How they were made

Kibana was reset after every run, so the objects no longer existed. For each
run, on Kibana 9.5.5 (local dev stack, web-logs sample data freshly installed):

- **Dashboard:** re-created verbatim from the run's `artifacts.json` by
  POSTing its stored `data` block to `/api/dashboards` (no fields needed
  changing; Kibana assigned a new id). Screenshot with Playwright/Chromium at
  1440 px wide, viewport grown to the full dashboard height, time range
  "Last 7 days", taken after every panel reported `data-render-complete`.
- **Rule:** re-created from the stored rule object (`name`, `tags`,
  `rule_type_id`, `consumer`, `schedule`, `params`, `actions`) via
  `POST /api/alerting/rule`, **kept disabled** so it never ran; the snapshot is
  its details page in Stack Management > Rules (hence "Disabled", 0
  executions, no alerts — the original runs' rules were enabled).

The usage-collection banner was dismissed before capture. The re-created
dashboards and rules were deleted afterwards. Numbers in the panels reflect the
sample data at capture time, not at run time.
