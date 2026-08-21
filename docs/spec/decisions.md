# Decisions ledger

Status: Draft v1.0 (2026-08-19) — regeneration corpus. Consumes
[brief.md](brief.md); read alongside [design.md](design.md).

Each entry is a decision a rewrite must not silently re-litigate: what was
decided, when, and the evidence. Entries marked **probe** are live
observations against a real stack (Kibana 9.4.3 unless noted) — observed
behavior, not documentation. Raw probe records and the full process history
leave the public tree at release and live on in the private development
repository; every finding needed to rebuild is restated here in full, so
this ledger stands without them.

## Architecture and packaging

- **D1 (2026-07)** — Hexagonal with machine-enforced contracts. Layering
  and core purity are import-linter contracts in `pyproject.toml`, not
  convention. Why: the port seam is what makes toolbox-sized rewrites safe.
- **D2 (2026-07)** — Persona-first packaging. Toolboxes are chosen per
  deployment; profiles bundle toolbox+tier per persona. The stated
  rationale — tool-selection accuracy drops past ~20 tools, small local
  models earlier — is a recorded working heuristic (`profiles/README.md`),
  not a committed measurement; no controlled tool-count experiment exists
  in the run records. Study L's 11-model roster is the closest evidence.
- **D3 (2026-07)** — Three tiers, destructive hidden by default. Write
  safety is a **registration-time** tier: hidden tools never enter the
  model's context (list-time visibility is what the assembly tests pin;
  calling a hidden name fails as unknown — observed, unpinned). Curation
  is not a security boundary — the Kibana API key's RBAC is; the docs must
  always say so.
- **D4 (2026-07)** — Handle-based saved-object export/import: NDJSON stays
  server-side; a whole-space export never enters the model's context.

## Space targeting

- **D5 (2026-08-15, probe P7)** — Validate space existence **before**
  acting, never on the error path. Observed: calls under a nonexistent
  space id silently succeed — Kibana writes into an orphan namespace, no
  404 exists to intercept. This kills any lazy-validation design.
- **D6 (2026-08-15, probe P8)** — Space ids: pattern `^[a-z0-9_-]+$`, **no
  max length**. Observed: Kibana accepted a 300-character id live; the
  schema mirrors reality, not taste. Validation happens fail-closed at
  gateway construction; `default` is handled natively.
- **D7 (2026-08-15)** — Additive default-off: the `space` parameter absent
  means byte-identical pre-space behavior; dict results echo the effective
  space only when the caller chose one; list results carry no echo.
- **D8 (2026-08-15)** — A base URL already pinned to `/s/<id>` refuses the
  `space` parameter with guidance (two space authorities would lie).
- **D9 (2026-08-16, probes P9/P9b)** — Dashboard ids are title-derived and
  Kibana holds them globally unique across spaces: cross-space same-title
  creates conflict loudly (409); cross-space overwrite mints a new
  destination id on first restore. The feared twin-clobber hazard is
  impossible; deterministic cross-space title collisions are the real limit.
- **D10 (2026-08-18)** — Space uniformity rule: a toolbox gains the
  parameter on **all** its tools or none — partial coverage silently drops
  a model into the default space mid-flow. Consequences: `get_alerting_health`
  keeps a redundant-but-valid parameter; the five non-space toolboxes are
  excluded with reasons — `observability` (mixed axis), `fleet` (opt-in
  migration makes meaning deployment-dependent), `streams` (cluster-global
  objects; cosmetic space implies false containment), and
  `platform-admin`/`platform-health` (their objects and namespaces are
  global by nature; a space parameter would validate and then lie).
- **D11 (2026-08-18)** — Semantic caveats are contract text: preconfigured
  connectors are instance-global; `namespace_type="agnostic"` exception
  lists are shared across spaces; value-list backing indices are per-space,
  auto-created on first write.

## Security posture

- **D12 (2026-08-15)** — Mask untranslated exception text at the MCP
  boundary AND validate the env-fallback key (strip; reject CR/LF): a
  malformed env key was proven to echo verbatim into caller-visible errors
  and logs. Curated guidance errors pass the mask.
- **D13 (2026-08-15)** — KQL filter values escape backslashes before
  quotes; empty identifiers are rejected at the schema (`min_length=1`);
  connectivity errors are topology-free; HTTP mode enables host/origin
  protection on loopback binds; container base images are digest-pinned.
- **D14 (2026-07-18)** — Enrollment-key mint/revoke is deferred: under
  api-key auth an inline secret leaks into model context and an on-disk
  secret breaks the HTTP transport. Revisit only with a credential path
  that avoids both.
- **D15 (2026-08-19)** — Cloud-agent access uses a least-privilege
  fine-grained PAT scoped to this repository; branch protection (no
  deletions, no force pushes, required checks) is the enforcement locus,
  not agent instructions.

## Reliability and models (measured)

- **D16 (2026-08-16)** — The server's value is measured, not asserted:
  with-MCP vs no-MCP on a frozen task = every model passing every run
  (15/15 across three Claude models × five runs) vs 5/15; 2.0–4.5× cost
  and 2.3–5.7× wall-clock reduction; every number generated from committed
  run records (`docs/server-usability.md`, `scripts/experiment/`).
- **D17 (2026-08-16)** — Error-guidance strings are product contract: real
  models recovered mid-turn *because* the error text named the fix; the
  e2e-replay tier replays those recorded turns (including a rejected call
  and its recovery) and fails on guidance regression.
- **D18 (2026-08-19)** — The e2e reference model is `gemma-4-12b-qat`:
  5/5 on the dashboards space chain (Study L) and 2/2 on the alerting-space
  gate, while `gpt-oss-20b` characteristically drops the `space` parameter
  mid-chain (3/5 wrong-space on dashboards; on alerting 0/3 recorded — one
  of the three was a harness misconfiguration offering no alerting tools,
  0/2 with tools offered). Caveat: the gpt-oss and gemma alerting runs sit
  on different builds (the harness's server-entry fix landed between them),
  so that comparison is indicative, not controlled; the Study L dashboards
  numbers are the controlled ones. Space threading, tool-call discipline,
  and params authoring are three distinct model capabilities; gates must
  exercise all three.
- **D19 (2026-08-19)** — Replay transcripts carry recorded bytes, never
  hand-written arguments; error steps are recorded with their guidance
  needles; server-generated ids (alert rules) bind at replay time from the
  live create response since they cannot be re-derived. Provenance is
  per-transcript: most are real model turns, one (the authored
  space-dashboard sequence) has a hand-chosen call ORDER over real
  recorded wire bytes — its order carries no evidence about model behavior
  and each transcript's `recorded` block says which kind it is.

## Scope and licensing (probed on Basic)

- **D20 (2026-07-16 / 2026-08-19)** — License gates are probed live, never
  assumed: `workflows`/`agent_builder` 403 "requires an Enterprise
  license"; SLOs 403 "Platinum license or higher"; `maintenance_windows`
  and `logstash` 403 "your basic license does not support";
  `entity_analytics` 403 "license does not support" (Platinum-class
  inferred from the message, not a tier Kibana named); osquery answers 200
  on Basic but is infra-gated. The Generative-AI connector type is
  hard-gated to Enterprise — a local LLM cannot substitute, the connector
  type is the gate.
- **D21 (2026-08-19)** — Method-level Basic coverage is recorded honestly:
  ~126 of ~407 Basic-tier client methods wrapped, 26 of 27 namespaces
  touched — a metric in the current client library's vocabulary; a rebuild
  on a different client re-derives it against that client's surface. The
  gap list and its build order live in the [roadmap](../roadmap.md).
  Coverage claims are audited at method level, not toolbox level.
- **D22 (2026-07-18)** — Kibana's detection-rule bulk-action and patch
  endpoints fail 403/500 under api-key auth; rule enable/disable ships via
  full-object update instead — the api-key path constrains which Kibana
  endpoints are usable, whatever client wraps them.

## Release and identity

- **D23 (2026-08-15)** — Name: `mcp-for-kibana`. "for Kibana" is the
  referential construction Elastic's trademark policy permits; the mark
  never appears in a domain. PyPI/npm/GitHub availability verified; the
  README non-affiliation disclaimer is permanent.
- **D24 (2026-08-19)** — Claims come last: nothing is named, tagged,
  published, or reserved before every verification preceding it has
  passed; PyPI upload is rehearsed because versions are immutable.
- **D25 (2026-08-19)** — The public repository starts with exactly one
  commit on `main`, its only branch. History (including closed PRs, which
  remain reachable after a visibility flip) stays in the private
  development repository.

- **D26 (2026-08-19)** — Root `AGENTS.md` reintroduced as the regeneration
  corpus's agent entry point (the convention's standard name is the value).
  The earlier same-named methodology file stays removed; the dev-artifact
  reference guard dropped its ban on this name and keeps banning the other
  removed methodology filename (the guard's own pattern list names it —
  writing it here would trip that very guard).

- **D27 (2026-08-20)** — Single tool ownership: every tool name belongs to
  exactly one toolbox. Precedents: the data-view read tools were extracted
  from dashboards into data-management so the namespace is owned once; the
  planned AI toolbox prefixes its tools because fleet owns
  `list_agents`/`get_agent`. The server's duplicate-registration policy
  (ignore, first-in-wins) is a backstop for composition, not a sharing
  mechanism. Surfaced by owner review of this corpus — the toolbox is only
  a safe regeneration unit because contracts cannot overlap.
## Superseded

- ~~Space scoping via base-URL suffixing (probe P1)~~ — worked but
  validated nothing; superseded by D5/D6 (validate-at-construction) once
  P7 exposed the silent-orphan hazard.
