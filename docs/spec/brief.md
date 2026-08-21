# Brief — what this project is

Status: Draft v1.0 (2026-08-19) — regeneration corpus. Feeds the toolbox
behavior contracts (`docs/spec/toolboxes/`) → the design doctrine
(`docs/spec/design.md`) → the regeneration brief (`docs/spec/regeneration.md`).

This document is the top of the chain: if every line of code were deleted,
rebuilding starts here. It states what the project is, for whom, what it
values, and what it deliberately is not. The "how" lives in
[design.md](design.md); the per-toolbox "what" lives in
the contracts under `docs/spec/toolboxes/`; the proof-of-correctness lives in the test tiers
and the Definition-of-Done gate (`dod.config`).

## The product

An [MCP](https://modelcontextprotocol.io) server that lets an LLM do real work
in [Kibana](https://www.elastic.co/kibana) through a small, curated,
tier-gated tool surface — instead of ~610 raw HTTP endpoints. Tools are
grouped into composable **toolboxes** enabled per deployment; **profiles**
(`profiles/`) package toolbox + tier choices per persona. The flagship job:
a user describes a chart, the server translates a declarative spec into real
Kibana Lens visualizations and dashboards. Around it: data management,
alerting, cases, security detections, fleet, streams, observability reads,
and platform admin/health.

## Who it serves

- **A person driving an LLM** (Claude, a local model in LM Studio, any MCP
  client) who wants Kibana outcomes — dashboards built, rules managed, fleet
  inspected — without writing API calls.
- **The deployment operator** who decides which toolboxes and which tier a
  given agent gets, and supplies the Kibana API key that is the real
  authority boundary.
- **A future coding agent** rebuilding or extending this server: the
  regeneration corpus (this directory) is written for you.

## Goals, ranked

1. **Reliability for models.** With this server registered, every measured
   model passed every run of the reference task — 15/15 across three Claude
   models at five runs each, vs 5/15 without it — at 2.0–4.5× lower cost
   and 2.3–5.7× lower wall clock
   ([server-usability report](../server-usability.md)). Profiles narrow
   the tool surface per persona; the working heuristic (recorded, not a
   committed measurement) is that model tool-selection accuracy drops past
   ~20 tools, and small local models degrade earlier — a focused surface
   is more capable, not less.
2. **Safety proportional to blast radius.** Every tool is classified
   read / write / destructive; the tier cap hides everything above it;
   destructive-shaped operations carry explicit confirm gates. Curation is
   not a security boundary — the supplied API key's RBAC is.
3. **Honesty of scope.** What Basic license cannot do is recorded, not
   worked around; what infrastructure cannot support is deferred with the
   reason on record ([roadmap](../roadmap.md)). No faked surfaces.
4. **Composability.** Deployments enable only what their persona needs;
   adding a toolbox never changes the behavior of another.

## Values — how it should behave

- **Error guidance is a product feature.** When a call fails, the error text
  must name the fix well enough that a model self-corrects on the next turn.
  This is contract, not politeness: the e2e-replay tier regression-protects
  the exact guidance strings recorded from real model recoveries.
- **Additive means byte-identical without it.** New capability ships behind
  an optional parameter or toolbox that leaves every existing call unchanged
  (the space-targeting parameter is the reference example).
- **Evidence before claims.** Nothing is "done" because its author says so:
  the DoD gate (`make dod`, criteria in `dod.config`) certifies completion;
  report numbers trace to committed run records, never hand-transcribed.
- **Empirical design.** Dependency behavior is probed live before designs
  lean on it; observed behavior outranks documentation. Decisions and their
  evidence are recorded in [decisions.md](decisions.md).
- **Credentials are the caller's.** Per-request API key over HTTP, no
  credential ever echoed into errors, logs, or telemetry.

## Non-goals

- **Not an Elasticsearch client.** The server speaks to Kibana's public
  APIs; direct ES access is out of scope.
- **Not an endpoint mirror.** kibana-py exposes ~40 namespaces; this server
  deliberately wraps the subset that serves personas, at rewrite-sized
  granularity. Coverage gaps are recorded, not accidental.
- **Not a security boundary.** Toolbox and tier selection curate what a
  model sees; only the Kibana API key constrains what it can do.
- **Not license-agnostic.** Basic license is the baseline; Platinum- and
  Enterprise-gated surfaces are deferred with live-probed evidence, never
  stubbed.

## Constraints

- **Kibana 9.4+ only** — the dashboards toolbox is built on the 9.4 public
  Dashboards and Visualizations APIs (Elastic marks both Technical Preview;
  shapes may change without notice).
- **Trademark posture**: the name leads with the generic (MCP) and uses
  "for Kibana" referentially; the non-affiliation disclaimer stays in the
  README. Never register a domain containing the mark.
- **Apache-2.0**, with NOTICE covering the Elastic-licensed dev-stack images.

## What "correct" means

A build of this project is correct when the Definition-of-Done gate prints
`VERDICT: GO`: unit, lint, types, import contracts, hygiene, docs-strict,
dependency audit, SAST, vocabulary, image smoke, contract tier (live
Kibana), e2e-replay tier (recorded model turns through a real MCP client),
live-model e2e tier, both ephemeral destructive tiers, and a changelog
entry. A rewrite that reaches GO without reading the old implementation has
preserved everything this project promises.
