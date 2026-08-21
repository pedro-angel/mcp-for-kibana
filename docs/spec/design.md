# Design doctrine — how a correct implementation is shaped

Status: Draft v1.0 (2026-08-19) — regeneration corpus. Consumes
[brief.md](brief.md); refines [architecture.md](../architecture.md) (the
diagrams and layer map live there and are not duplicated here); feeds
[regeneration.md](regeneration.md).

This is the "how" that must survive a rewrite. Everything here is either
machine-enforced today (named where so) or pinned by a test tier. A future
implementation may change any mechanism this document does *not* name.

## The load-bearing shape

- **Hexagonal, machine-enforced.** A core (`core/`, `ports/`, `config/`)
  that may not import `fastmcp`, `mcp`, or `kibana` (pydantic is allowed —
  the ban is those three, not frameworks in general); toolboxes above it;
  adapters at the edges; a composition root (`server.py`) that wires but
  never talks to Kibana itself. The layering is not convention — it is
  import-linter contracts in `pyproject.toml`, red in CI when violated. A
  rewrite keeps the contracts, not necessarily the module names.
- **One port is the rewrite seam.** `src/kibana_mcp/ports/gateway.py`
  (`KibanaGateway`, a typed Protocol) is the only doorway between toolboxes
  and Kibana. The adapter package (`adapters/kibana/`) is the only place
  allowed to import the Kibana client library (a package-scoped contract;
  one module implements the port there today). Swapping that library — or
  regenerating everything behind the port — touches that package plus its
  tests. Treat the port file as part of this spec.
- **The regeneration unit is the toolbox.** Each toolbox is a vertical
  slice registered onto the server through one `register(mcp, deps)` entry,
  owning its tools, tiers, and guards. Rewrites happen toolbox-at-a-time
  against the behavior contracts under `docs/spec/toolboxes/`.
- **Every tool name has exactly one owning toolbox.** No two toolboxes
  register the same tool (133 names, zero duplicates — verified
  2026-08-20); when two toolboxes want the same capability, the resolution
  is extraction into a single owner or a distinguishing prefix, never
  sharing. Composition is defensively deterministic besides: duplicate
  registrations are ignored first-in-wins, so even a future overlap could
  not make one toolbox's behavior depend on another's presence. This rule
  is what makes the toolbox a safe regeneration unit — contracts never
  overlap, and cross-toolbox behavior flows only through the system
  invariants below and the shared port. Ownership constrains
  *registration*, not *availability*: personas whose jobs cross ownership
  boundaries compose toolboxes (building a dashboard in a fresh space
  takes three), and finer-grained per-tool selection is a filed roadmap
  feature, not a reason to share registrations.
- **Dependency injection via one factory.** Toolboxes receive a
  `gateway_factory(space=None)` callable and never construct clients. The
  factory is where per-request credentials and space scoping happen; a fake
  factory is how the unit tier tests every toolbox without a network.

## System invariants

Authentication and credentials:

- Per-request credential over HTTP: the caller's `ApiKey` header always
  beats any server-side env key; the `Bearer` scheme is rejected with no
  fallback; each request gets a fresh gateway — no cross-caller reuse.
- The env-fallback key is validated before use (stripped; a key containing
  CR/LF is rejected) and never interpolated into any error text.
- Untranslated exception text is masked at the MCP boundary; curated
  guidance errors pass through. No credential appears in logs, errors, or
  telemetry spans. No TLS-disable knob exists anywhere in configuration;
  verification is the client library's default and is currently unpinned
  by any test (an open gap a rewrite should close by pinning it).
- Connectivity failures are topology-free: the caller learns Kibana is
  unreachable, never the internal endpoint.

Tiering and destruction:

- Every tool is registered under exactly one of read / write / destructive.
  This exactly-one rule is currently unenforced by any test (an open gap a
  rewrite should close) — but it is load-bearing: the tier mechanism
  disables by tag, so an untagged tool would be visible at every tier.
- Write safety is a **registration-time** tier: tools above the deployment
  cap are disabled at registration and never appear in the advertised list
  — the point is that destructive tools never enter the model's context.
  A call naming a hidden tool fails as unknown (observed; the pinned
  assertions cover list-time visibility).
- Tier-gating is the destructive baseline; **additionally**, operations
  whose effect is invisible until the guidance is read carry an explicit
  `confirm` parameter (clearing all processing steps, activating a fork
  onto live traffic, cluster-wide disable, bulk agent actions), and
  irreversible whole-container deletes use `force`. Most destructive tools
  carry neither — the tier cap plus the key's RBAC is their gate. System
  objects (the default space, reserved roles, managed policies) are
  refused regardless of any force flag.

Space targeting:

- The `space` parameter is optional everywhere it exists; omitted means the
  default space and the call is byte-identical to the pre-space behavior.
- Space ids validate against `^[a-z0-9_-]+$` with **no maximum length** —
  Kibana accepts a 300-character id live, so the schema imposes no bound
  the server does not have (see [decisions.md](decisions.md), P8).
- Space existence is validated **fail-closed at gateway construction**,
  never on the error path: Kibana silently writes into orphan namespaces
  for nonexistent space ids, so there is no 404 to intercept after the
  fact (P7 — this is why validate-then-act is not optional).
- Dict-returning tools echo the effective space only when the caller chose
  one; list-returning tools carry no echo; scoped not-found errors carry
  the `(in space '<id>')` suffix exactly once; a deployment whose base URL
  is already `/s/<id>`-pinned refuses the parameter with guidance.
- Tool-level argument guards run **before** the factory call, so a guard
  failure performs no space validation.

Error guidance:

- Domain failures translate to typed errors and then to tool errors whose
  text names the fix. Payload rejections carry the stable prefix
  `Kibana rejected the payload:` plus Kibana's validation detail. These
  strings are product contract: the e2e-replay tier replays recorded model
  turns that recovered *because* of them, and fails if they regress.

Identity and state:

- Dashboard ids derive deterministically from the title, by this exact
  rule (byte-compatibility with existing deployments depends on it):
  NFC-normalize, casefold, collapse whitespace; slug = non-`[a-z0-9]` runs
  → `-`, trimmed; id = first 64 slug chars (fallback stem `dashboard`) +
  `-` + first 12 hex of SHA-256 over the slug (over the normalized title
  when the slug is empty, i.e. non-ASCII); blank titles are rejected, never
  hashed. Kibana holds ids globally unique across spaces — same-title
  creates in a second space conflict loudly rather than clobbering
  (P9/P9b).
- The HTTP transport is stateless; stdio mode is one process per user.
- Saved-object export/import moves NDJSON by server-side handle so bulk
  content never crosses the model's context window.

## Technology posture

FastMCP is the MCP framework today; kibana-py is the client today. Neither
is contractual — the port, the invariants above, and the behavior contracts
are. What *is* contractual about the boundary configuration: duplicate tool
registrations are ignored (composability), error masking is on, and HTTP
mode enables host/origin protection on loopback binds.

## Enforcement map

- Layering and purity: import-linter contracts (`pyproject.toml`), CI job.
- Invariants: `tests/unit/` (fakes; auth, tiers, space threading, guards),
  `tests/contract/` (live Kibana), `tests/e2e_replay/` (recorded turns,
  guidance strings, schema-rejection pins), `tests/e2e/` (live local model),
  `tests/ephemeral/` (destructive paths on throwaway stacks).
- The whole: `make dod` → `VERDICT: GO` (see [brief.md](brief.md)).
