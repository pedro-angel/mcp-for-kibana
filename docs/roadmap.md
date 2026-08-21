# Roadmap

The one page you need to pick this project up: what is built, what is
deliberately not, and the facts that cost something to learn. Open items are
tracked as GitHub issues.

## Where the project stands

The server ships **10 toolboxes / 133 tools** (dashboards, data-management,
alerting, cases, security-detections, fleet, streams, observability,
platform-admin, platform-health) — see the [Tool reference](tools.md) for every
one. Every tool is classified read / write / destructive and contract-tested
against a live Kibana 9.4.3.

**Toolbox-level Basic coverage is complete; method-level is not.** The 2026-07-16
catalog audit was toolbox-granular, and its "no Basic functionality left" claim
did not survive the **2026-08-19 method-level audit**: the server touches 26 of
the 27 kibana-py namespaces usable on Basic but wraps ~126 of their ~407
methods. The remaining Basic surface worth building is now filed in build order
under *Open work*; everything else still open is gated on a license this project
has not got, on infrastructure it does not run, or on a decision that has not
been taken.

Six test tiers back that claim: unit (fakes), contract (live stack), e2e-replay
(a recorded model turn through a real MCP client), e2e (a real local model),
plus two ephemeral tiers that stand up throwaway stacks for the destructive
paths. Everything except the live-model tier runs in CI.

The command that certifies completion is the Definition-of-Done gate — `make dod`,
criteria declared in `dod.config`. Nothing is "done" because its author says so.
A single pass needs the dev stack up for the live tiers and gone for the
ephemeral ones, so:

```bash
scripts/stack.sh up && scripts/stack.sh seed
KIBANA_MCP_DOD_CYCLE_STACK=1 make dod
```

Day to day: `make check` for the fast gates, `make dod` before any completion or
release claim, `make help` for the rest.

## Open work — tracked as GitHub issues

As of this page:

**Buildable now (Basic license, no LLM connector).** The *write-tier* campaign on the
existing read toolboxes is done, but a 2026-07-16 catalog audit found unbuilt
Basic-buildable **toolboxes** that had never been filed. Now filed, in build order:

- feat: `fleet` toolbox — **DONE**. Read-first v1 (20 tools) + **write/destructive tier shipped 2026-07-18**: 15 tools (agent/package-policy + output CRUD, single+bulk agent actions), `fleet-admin` profile now `destructive`, a `fleet_ephemeral` 2-agent battle-test harness + DoD criterion. Enrollment-key mint/revoke deferred (unsafe under api-key auth — inline leaks to context, on-disk breaks HTTP transport).
- feat: `ai-automation` toolbox — **Enterprise-gated (reclassified 2026-07-18)**. A live probe found `workflows` AND `agent_builder` reads both return 403 "requires an Enterprise license" on Basic — the "Basic half" premise was wrong. Blocked on Enterprise licensing (the same gate as the AI assistant tools below); a 30-day trial stack would unblock the ~12 GA read tools (`converse` additionally needs a `.gen-ai` connector). Read tools would use an `ai_*` prefix (fleet already owns `list_agents`/`get_agent`).
- feat: `streams` write follow-ups — **shipped 2026-07-18**. 3 tools (9→12): `set_stream_processing` (RMW the processing.steps facet), `deactivate_fork` (write), `activate_fork` (**destructive + confirm** — activation diverts live docs; already-routed docs stay in the child, per env-research). ILM-mode retention (no ILM policy on-stack) and raw full-config `update_ingest` (footgun) + `space_id` deferred.
- feat: `security-detections` write follow-ups — **shipped 2026-07-18**. 6 tools (19→25): value-list item CRUD, full-replace `replace_detection_rule` (RMW — omitting a field wipes it; also translates the `from_`↔`from` kwarg/body-key mismatch), and enable/disable. **Env-research corrected the "enable/disable is privilege-gated" premise**: `bulk_action`/`patch` do 403/500 under api-key auth, but `update_rule(enabled=…)` works (the path kibana-py's docstring recommends) — so it's buildable, and shipped.
- feat: `alerting` rule-lifecycle follow-ups — **next up (ordered 2026-08-19)**. The kibana-py `alerting.rule` surface is 7/16 wrapped: missing `update` (rule edit), mute/unmute (per-alert and rule-wide), snooze/unsnooze, `update_api_key`, and a `rule_types` read. All Basic; same toolbox, additive tools.
- feat: `synthetics` write tier — **second (ordered 2026-08-19)**. The observability toolbox reads monitors/params/private-locations (4/19 methods); the write tier adds monitor + param + private-location CRUD and `test_monitor`. The API is Basic (reads contract-tested); env-research must confirm the execution paths — private locations need a synthetics-capable Fleet agent, so run-side value may be infra-bounded like osquery below.
- **Deprioritized, with the reason on record (2026-08-19):** `cases` depth (comments management, files, configuration, tags/reporters, alert linkage — 6/22 methods wrapped). Core case CRUD is Basic and shipped; the depth that makes cases compelling is license-locked — external push needs Gold+ connector types, assignees need Platinum — so the remaining Basic-buildable slice is thin workflow plumbing. Revisit if a license lands (same trigger as the Platinum/Enterprise items below).
- **Parked as infra-gated (re-confirmed 2026-08-19):** `osquery` — the one whole Basic namespace with zero coverage (0/14 methods; live probe: saved-queries/packs answer 200 on Basic). Already classified under `security-response` above: useful only with the osquery_manager integration on an agent policy and hosts worth querying — the dockerized lab agents would return container-level trivia. Revisit when real endpoints enroll.
- feat: per-tool surface configuration — **filed 2026-08-20 (owner request,
  unordered)**. Today's selection axes are toolbox + tier only; a persona
  whose job crosses ownership boundaries must enable whole toolboxes
  (building a dashboard in a fresh space takes three: dashboards +
  data-management + platform-admin, 24 tools at write). An additive,
  default-off allow/deny of individual tool names (composing with the
  existing registration-time disable mechanism) would let operators trim
  the surface toward the ~20-tool reliability heuristic. Design tension to
  resolve in the spec: name-level selection can split coherent pairs
  (read-modify-write partners, fork lifecycles) — same
  curation-not-security posture as tiers, but the docs must say which
  splits are foot-guns.
- **Unordered Basic backlog from the 2026-08-19 audit** (file individually when picked up): `spaces.copy_saved_objects` + `update_objects_spaces` (natural extension of space targeting), `timeline` depth (1/19 wrapped), data-view runtime fields + default get/set, exception-list update/duplicate/export/import, granular `saved_objects` reads (find/get — raw CRUD stays out: Kibana marks it deprecated), `apm` writes (agent-config upsert, annotations), fleet advanced ops (package install/uninstall, diagnostics, tags, k8s manifest), streams queries/attachments/content (significant-events likely needs an AI connector — verify before filing).

The two formerly-unclassified catalog toolboxes are now **classified (2026-07-18) and
deferred**:
`security-entity-analytics` (license-gated: `entity_analytics` reads 403 "license does
not support", Platinum-class, trial unblocks) and `security-response` (infra-gated:
endpoint/osquery reads are Basic-GA but empty without an enrolled Elastic Defend endpoint +
osquery integration; `endpoint.get_actions_list` 403-privilege-gated; the response actions are
☠️ destructive on live hosts). `platform-admin`'s YAGNI non-goals stay unfiled (deliberate scope
cuts, not tracked work).

**Blocked on license (env-research done, can't build past it).** All 403 on Basic,
confirmed live. Note the two tiers — Platinum ≠ Enterprise:

*Platinum unblocks (a trial or Platinum license):*

- feat: observability SLO reads (deferred from the observability toolbox)
- feat: `platform-admin` logstash pipeline reads (split from the platform-admin write/destructive-tier work)

*Enterprise-only — the Generative-AI (`.gen-ai`) connector these all need is hard-gated to
Enterprise in Kibana (`minimumLicenseRequired: 'enterprise'`); Basic 403s connector creation
("does not support it"), so a local OpenAI-compatible LLM like LM Studio can't substitute —
the connector **type** is the gate, not the model (confirmed live 2026-07-16):*

- feat: observability AI assistant tool (Tech-Preview; deferred from the observability toolbox)
- feat: `security-ai` toolbox (`security_ai_assistant` + `attack_discovery`; deferred from security-detections)

An Elastic **trial** license (Enterprise-level, 30 days) unblocks all four at once.

**Deferred by decision until all functionality is done + battle-tested:**

- release: PyPI publishing + versioning/changelog
- chore: public-flip checklist (docs hosting, site_url, badges) — includes **fork-PR CI hardening**: the `checks.yml` `image` job runs the built container on every event (`make build` → `image-smoke.sh` does `docker run`), so once public a fork PR would execute its own code in the runner; gate the smoke `docker run` to same-repo refs (build-only on untrusted forks) before flipping public. Small blast radius today (no secrets on fork PRs, ghcr push gated to `main`), but it belongs on this checklist.

Closed since this page's 2026-07-12 baseline: `platform-admin`
write/destructive tier (logstash reads split out separately),
`security-detections` update-rule + value lists, `streams`
write/destructive tier, `saved_objects` export/import +
destructive overwrite, ES|QL panels — `metric` v1 +
`table`/`xy` chart types. Also closed in the 2026-07-12 campaign: several
smaller fixes, including the Settings kwarg and auth-docs corrections closed
earlier still.

**Tool packaging & the toolbox roadmap.** Which toolboxes to build next is
decided persona-first: a catalog of ~14 candidate toolboxes over all 40
kibana-py namespaces, a set of
shippable persona [`profiles/`](https://github.com/pedro-angel/mcp-for-kibana/tree/main/profiles)
(2 live, the rest planned), and a leverage-first build order. **Wave 1
(`platform-health` + `data-management`) and Wave 2 (`alerting` + `cases`) are
done** (2026-07-12 campaign). **Wave 3 `observability` v1 is done** (read-first:
synthetics + uptime + apm-config reads; env-research → spec → adversarial review
→ implement → adversarial review → live contract tests on Basic). Scoped
honestly: the SRE-facing telemetry an operator expects is *not* buildable on the
public/Basic surface — APM services/traces/service-maps are internal-only
(`/internal/apm/*`, `400` externally, unwrapped by kibana-py) and SLOs need
Platinum (`403` on Basic). SLOs and the obs-AI assistant are deferred as future
additive tiers of the same toolbox; `observability-sre`
stays `planned` until SLOs land. **Wave 3 `security-detections` v1 is done**
(read-first: detection rules + alerts + rule-tags/prepackaged-status + exception
lists/items + value lists + timelines; 10 read tools; whole surface Basic-GA,
seeded-and-captured rule/exception shapes; env-research → spec → adversarial
review → live contract tests). Its AI half, `security-ai` (security_ai_assistant
+ attack_discovery), is deferred — it needs an LLM connector, like obs-AI; the
`soc-analyst` profile stays `planned` until it lands. **Wave 3 core toolboxes are now
complete.** (Wave 4 — `platform-admin` + `streams` read-first, then their
write/destructive tiers plus the `security-detections` write tiers — has since landed;
see the dated updates above through **2026-07-16**, the newest of which is the current
state.)

Longer-horizon ideas deliberately not filed yet (file when real): a meta-tool
gateway toolbox for power users, OAuth 2.1 if the user base outgrows API keys.
(Kibana Spaces support graduated from this list: space targeting shipped on 21
tools in the 2026-08-15 wave and extended to 63 space-aware tools across
alerting, cases and security-detections on 2026-08-19.)

## Accepted nits (known, deliberately not fixed)

- `kibana_mcp.server` legitimately imports the kibana adapter (composition
  root); only *direct* `import kibana` is contract-forbidden
  (`allow_indirect_imports` contract in `pyproject.toml`).
- `DataViewDetail` is a frozen dataclass holding a dict — unhashable if
  ever hashed; nothing hashes it.
- `build_dashboard_data` drops an explicitly-empty `time_range={}`
  (truthiness check); unreachable from any tool.

## Hard-won facts a new session should not re-derive

- **Elastic AI features are Enterprise-gated, not just "needs a connector".** The
  Observability + Security AI Assistants, Attack Discovery, and the Generative-AI
  (`.gen-ai`) connector all require an **Enterprise** license (not Platinum). The
  `.gen-ai` connector is the binding constraint — Kibana sets `minimumLicenseRequired:
  'enterprise'`, so Basic 403s connector creation ("does not support it") and a local
  OpenAI-compatible LLM (LM Studio) can't substitute. So the AI assistant tools need
  Enterprise; a Platinum trial (which unblocks SLOs + logstash reads) does not. A full
  **trial** license is Enterprise-level and unblocks all four. Confirmed live 2026-07-16.
  Build note: `agent_builder.converse` (GA since 9.2,
  plain-JSON) is a cleaner surface than the Tech-Preview obs-AI SSE stream.
- **Contract tests are the payload authority.** Live Kibana 9.4.3 rejects
  `time_range` inside Lens visualization configs (the OpenAPI spec
  suggested otherwise); type names are `data_table`, terms buckets use
  `fields` (plural) + `limit`, metric charts need `{"type": "primary"}`.
- **fastmcp 3.4.x:** `get_http_headers()` strips `authorization` unless
  `include={"authorization"}`; tier gating uses `mcp.disable(tags=...)`.
- **kibana-py:** `ApiError.__init__` reads `meta.status` unconditionally —
  test fixtures need a `meta` with a `.status`.
- **Local test stack** runs on host ports **19200/15601** (the machine's
  own Kibana dev stack owns the defaults). `scripts/stack.sh up|seed|status|stop|down|env`;
  credentials regenerate into gitignored `elastic-start-local/.env.seed`.
- **LM Studio (0.4.12):** the mcp.json toggle is gated behind *Require
  Authentication* and API tokens need the *Use MCP Servers* permission —
  full path in [e2e-setup](e2e-setup.md). `google/gemma-4-12b-qat` is the
  reference E2E model (5/5 on the dashboards space chain, 2/2 on the
  alerting-space gate); `openai/gpt-oss-20b` characteristically drops the
  `space` parameter mid-chain (3/5 wrong-space) and was retired from this
  duty 2026-08-19; qwen2.5-coder-14b garbles tool syntax ~1 run in 3.
- **`/api/v1/chat` response shape (observed 2026-08-09, not inferred):**
  `output` is a list of typed items — `reasoning`, `tool_call` (`tool`,
  `arguments`, `output`, `provider_info`), `invalid_tool_call` (`reason`,
  `metadata.tool_name`, `metadata.arguments`) and `message`. Count `tool_call`
  only: `invalid_tool_call` was rejected by LM Studio's own schema validation
  and **never reached the server**, so counting it credits us for a call we
  never received.
- **Error guidance is a product feature, not a diagnostic.** On the first green
  e2e run the model failed three times and recovered from the error text alone:
  a bad `time_range` (rejected client-side), then `data view 'flights' not found
  — call list_data_views to see what exists`, then `field 'price' does not
  exist … did you mean 'Carrier'?`. A model has no other way to learn your data's
  schema. `tests/e2e_replay/` regression-protects those exact strings; degrading
  one to a bare "not found" fails the tier.
- **Ephemeral-stack headroom is measured, not assumed** (2026-08-09, 11.7GB VM):
  the dev stack alone is ~4.9GB (ES 3.2, Kibana 1.2, fleet-server 0.4). The guard
  in `fleet_ephemeral.sh` is sized by *headroom*, not by VM total — re-measure
  before relaxing it, and note the VM is shared with whatever else is running.
- **`uv sync` replaces `.venv`** and with it any bootstrap-installed git
  hooks — pre-commit is a dev dependency for exactly this reason; if hooks
  ever go quiet, `uv run pre-commit install --hook-type pre-commit
  --hook-type commit-msg`.

## Machine-local state (not in git, by design)

A session on a fresh machine recreates all of this from the docs:

- `elastic-start-local/.env.seed` — machine-written by `scripts/stack.sh seed` (exactly
  KIBANA_URL and KIBANA_TEST_API_KEY; deleted by `down`).
- `.env.local` — user-owned (LMSTUDIO_* etc.); no script writes or
  deletes it. Both are loader-parsed plain `VAR=value` files (no `export`
  prefix, not shell-sourced) read by the test suites at fixture time.
- `~/.lmstudio/mcp.json` — the mcp-for-kibana stdio entry
  ([example](examples/mcp.json)).
- The running docker test stack and loaded LM Studio model.
