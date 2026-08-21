# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

New entries go under **[Unreleased]**; cutting a release moves them into a dated
version section.

## [Unreleased]

## [0.1.0] - 2026-08-21

### Added

- **Regeneration corpus** (`docs/spec/` + root `AGENTS.md`): the durable
  source of truth for rebuilding the server from documents — charter, design
  doctrine with system invariants, a decisions ledger distilling the live
  probes and recorded choices, per-toolbox behavior contracts, and a
  regeneration brief that ranks what is authoritative when documents and
  code disagree.

- **MCP server for Kibana 9.4+** over a hexagonal (ports-and-adapters) core,
  powered by [kibana-py](https://github.com/pedro-angel/kibana-py). Runs as a
  stdio server for a single local user or a stateless streamable-HTTP server in a
  container for multiple users; each request carries the caller's own Kibana API
  key so Kibana RBAC and audit logging stay per-user.
- **10 composable toolboxes / 133 tools**, enabled per deployment via
  `KIBANA_MCP_TOOLBOXES` (default `dashboards,data-management`):
  `dashboards` (11), `data-management` (10), `alerting` (11), `cases` (6),
  `platform-health` (3), `observability` (10), `security-detections` (25),
  `platform-admin` (10), `streams` (12), and `fleet` (35).
- **Tiered write safety.** Every tool is classified `read` / `write` /
  `destructive` (64 / 40 / 29); `KIBANA_MCP_TIER` caps which tiers are visible,
  and a call to a tool above the cap is rejected server-side, not merely hidden.
- **VizSpec → Lens translation.** A flat declarative chart spec is translated
  server-side into Kibana Lens payloads across 12 chart types, so small local
  models never have to emit deeply-nested Lens JSON.
- **In-band documentation resources** (`docs://user-guide`, `docs://tools`,
  `docs://troubleshooting`) served by the running server.
- **Optional OpenTelemetry** span export per tool call (`otel` extra,
  `KIBANA_MCP_OTEL_ENABLED`, off by default).
- **Container image** published to `ghcr.io/pedro-angel/mcp-for-kibana`.
- **Machine-enforced architecture**: import-linter contracts keep the hexagonal
  boundary; the domain core and ports import no framework or `kibana-py`.
- **Typed public API.** The package is checked with `mypy --strict` and ships a
  `py.typed` marker, so consumers type-check *through* it: a `dict` in a port
  signature is `dict[str, Any]`, not an implicit `Any`. Enforced by the
  `types_clean` gate and by `make lint` / `make check`.
- **Deterministic e2e replay tier** (`make test-e2e-replay`, marker
  `e2e_replay`). A real MCP client replays a *recorded* real-model turn over
  real stdio against a real server and a real Kibana, with no LLM in the loop —
  so the surface a model actually touches is certified on every PR, not only
  where a GPU happens to be. It asserts that the tools still exist under the
  same names, that arguments a real model produced still validate against the
  live input schemas, that arguments the runtime rejected are still rejected,
  and that the server's error guidance still says what lets a model
  self-correct. It does **not** certify that a model can choose those calls;
  that remains the live `e2e` tier's claim alone.
- **Container healthcheck.** The image declares liveness (the venv's own
  interpreter, so no new packages) asserting only that the server answers
  HTTP — deliberately not Kibana connectivity, which would restart-loop a
  healthy container whenever Kibana was unreachable.
- **One-shot Definition-of-Done runs.** `KIBANA_MCP_DOD_CYCLE_STACK=1` lets the
  gate stop the dev stack between the tiers that need it up and the ephemeral
  tiers that need it gone, so `make dod` can reach GO in a single pass.
- **Tooling**: a self-documenting `make` facade, a Definition-of-Done gate
  (`make dod`), and six test tiers (unit, contract, e2e-replay, e2e,
  streams-ephemeral, fleet-ephemeral) against live Kibana 9.4.3.
- **Server-usability report** ([`docs/server-usability.md`](./docs/server-usability.md)):
  twelve local models measured against the live e2e gate, three runs each. Seven
  drive the dashboards path 3/3, including a 3B model — the toolbox design, not one
  model's tool grammar, carries the path. Includes a control arm running the same
  task through the raw Kibana REST API plus its navigable OpenAPI specification:
  0 of 27 attempts produced a valid dashboard, against 8 of 9 through the curated
  tools, using triple the output tokens and triple the wall clock.
  Also measures what the tool surface *costs*: three Claude models driving a real
  coding agent against the same Kibana, with and without this server, 27 runs at
  zero permission denials. On `claude-opus-5` the median run without the server
  took 31 turns, 30,650 output tokens, $1.88 and 447 s; with it, 11 turns, 4,025
  tokens, $0.44 and 66 s. `claude-sonnet-5` shows the same shape. Reported with
  the failures: `claude-haiku-4-5` fails most runs in every arm, and one earlier
  configuration made the server *counter-productive* — a prompt that spelled out
  the curl recipe led the agent to ignore the registered tools entirely.
- **Space-targeted dashboards + data-management tools.** All 21 tools in the
  `dashboards` and `data-management` toolboxes — including the 5
  destructive-tier ones — gain an optional `space` parameter that
  fail-closed targets a Kibana space via kibana-py's
  `client.space(id, validate=True)`; a nonexistent space is rejected with
  guidance before any other work runs, rather than silently landing in an
  invisible orphan namespace. Additive and default-off: a call without
  `space` is byte-identical to before, in behavior and result shape. When
  `space` is set, dict-returning results echo `"space"`, not-found errors
  gain a `(in space '<id>')` suffix, dashboard links carry a `/s/<space>`
  prefix, `create_short_url` rejects a `/s/`-prefixed path, and saved-objects
  export/import can clone or overwrite across spaces. See
  [Tool reference](docs/tools.md#space-targeting)
  and [Configuration](docs/configuration.md#space-targeting).
- **Space targeting extended to alerting, cases, and security-detections.**
  All 42 tools in those three toolboxes gain the same optional `space`
  parameter — 63 space-aware tools in all — with the identical fail-closed
  validation, dict-result `"space"` echo, and `(in space '<id>')` not-found
  suffix, and no gateway or adapter changes (kibana-py scopes every
  namespace they use). Surfaces that stay instance-global under a space are
  documented rather than papered over: `get_alerting_health`'s report,
  preconfigured connectors, and `namespace_type="agnostic"` exception
  lists; value-list backing indices are per-space and auto-created on first
  write. The remaining toolboxes deliberately take no `space` —
  `platform-admin` manages global objects (spaces, roles),
  `platform-health` and `streams` wrap instance/cluster-global state,
  `fleet`'s space awareness is an opt-in migration, and `observability`
  (half space-scoped, half global APM config) is deferred as a follow-up.
  See [Tool reference](docs/tools.md#space-targeting).

### Changed

- **The distribution is now `mcp-for-kibana`** — the `kibana-mcp` name is owned by unrelated projects on both PyPI and npm, and the "MCP for Kibana" form is the referential construction Elastic's trademark guidelines permit. The import package remains `kibana_mcp`; the console script is `mcp-for-kibana`.
- **Supply chain**: the live-stack CI tiers run only for same-repo refs, matching the image job's fork posture; the container image carries OCI source/license labels and CI attests build provenance; a new wheel gate proves an installed package serves the in-band docs without the repo checkout; the weekly autoupdate workflow documents that its bot PR does not trigger CI.
- **Docs truth pass for the public flip**: corrected the default tool count, documented `KIBANA_MCP_EXPORT_DIR`, stated the export-handle statelessness exception and the endpoint-auth posture, replaced instructions that pointed at a PyPI name this project does not own, added Requirements, coordinated-disclosure and untrusted-content sections, and gave the Code of Conduct a genuinely private reporting channel.
- **docs/roadmap.md no longer cites issue numbers** — the public repo starts with an empty tracker.
- **The project no longer references its own build process.** Design specs,
  implementation plans, environment-research probes and retrospectives were
  being published as documentation pages and pointed at from code comments, the
  README, the Makefile and `pyproject.toml`. Every such pointer now states the
  fact it stood for, the artifacts are removed from the tree (git history is the
  archive), and `tests/unit/test_no_dev_artifact_references.py` keeps them out.
- **CI runs the live tiers per pull request**, not only on pushes to `main`, so
  the `dod-live` fan-in can gate merges.
- **The lint gate declares its own rule set.** `[tool.ruff.lint] select` now
  pins `E4, E7, E9, F` rather than inheriting ruff's implicit defaults, which
  change between releases — ruff 0.16 dropped `E402` and added `I`/`B`/`C4`/`S`/
  `RUF`, so a patch-level dependency bump reported 72 findings in unchanged
  code. The gate's meaning is now the project's to change, not the upgrade's.

### Fixed

- **Dashboards correctness**: data-view names/ids now resolve to the index pattern before Lens translation (was: silently empty charts); `search_dashboards` returns every page (was: first 20); `create_dashboard` preserves filters/query/tags/options on an existing dashboard (was: silently wiped); collapsible sections can no longer be deleted as if they were single panels, and appending panels works on dashboards that contain sections; Streams writes returning `acknowledged:false` now raise; `list_installed_packages` walks the full cursor; ambiguous data-view lookups are rejected with the candidate ids.
- **Security hardening before going public**: untranslated exception text is masked (a malformed env key could previously echo into the caller-visible error and the log); the env fallback key is validated; KQL filter values escape backslashes; empty tool identifiers are rejected up front; HTTP mode enables MCP host/origin protection on loopback binds; connectivity errors no longer disclose the internal Kibana endpoint; `set_stream_processing` requires `confirm=True` to clear all steps; the container healthcheck honours `KIBANA_MCP_PORT`; base images are digest-pinned.
- **LICENSE named the wrong copyright holder** (a copy-paste from the kibana-py sibling) and had a one-word deviation from the canonical Apache-2.0 text; NOTICE now also covers the Elastic-licensed images the dev stack pulls.
- **Package author email was a non-deliverable placeholder** (`noreply@users.noreply.github.com`); now the maintainer's allocated GitHub noreply address, so PyPI metadata carries a real (still private) contact.
- **`KibanaPyGateway` now satisfies its own `KibanaGateway` protocol.**
  `create_agent_policy` required three arguments the port declares optional, so
  a consumer coding against the published port would have hit a `TypeError`.
  Surfaced by the new type gate. No call site changed — every in-repo caller
  already passed them by keyword.
- **Dependency vulnerabilities**: `aiohttp` 3.14.1 → 3.14.3 (PYSEC-2026-3545,
  -3546, -3547) and `cryptography` 49.0.0 → 50.0.0 (PYSEC-2026-3552).
- **Base images are watched.** Dependabot covers the `docker` ecosystem, so the
  image's own OS layer receives security updates rather than only its Python
  dependencies.
- **The e2e harness no longer pins `context_length`.** It sent `8000`, which LM
  Studio treats as a *load* parameter: a value differing from the loaded instance
  spawns a second model instance at that context rather than reusing the
  operator's. Omitting it reuses the loaded model and hands it the operator's full
  context. `nvidia/nemotron-3-nano-omni` went from 0/3 to 3/3 on this change alone
  — at 8000 it spent its budget reasoning and was truncated mid-tool-call.
- **LM Studio load failures now report as environment failures.** A model the
  machine has no memory for was never exercised, yet surfaced as an opaque
  `HTTPStatusError: 400`. The gate now names the cause and points at `lms ps` and
  `lms unload <identifier>`. It fails rather than skips, so the skip-green hole
  guarded in `tests/_stack_env.py` is not reopened here.

[Unreleased]: https://github.com/pedro-angel/mcp-for-kibana/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pedro-angel/mcp-for-kibana/releases/tag/v0.1.0
