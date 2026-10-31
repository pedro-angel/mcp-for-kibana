# Design doctrine — how a correct implementation is shaped

Status: Draft v1.0 (2026-08-19) — regeneration corpus. Consumes
[brief.md](brief.md); refines [architecture.md](../architecture.md) (the
diagrams and layer map live there and are not duplicated here); feeds
[regeneration.md](regeneration.md).

This is the "how" that must survive a rewrite. Everything here is either
machine-enforced today (named where so) or pinned by a test tier. A future
implementation may change any mechanism this document does *not* name.

## The load-bearing shape

- **Hexagonal, machine-enforced.** A core (`core/`, `ports/`, `config/`,
  and the tool-list data `tool_lists`)
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
  register the same tool (184 names, zero duplicates — verified
  2026-10-12); when two toolboxes want the same capability, the resolution
  is extraction into a single owner or a distinguishing prefix, never
  sharing. Composition is defensively deterministic besides: duplicate
  registrations are ignored first-in-wins, so even a future overlap could
  not make one toolbox's behavior depend on another's presence. This rule
  is what makes the toolbox a safe regeneration unit — contracts never
  overlap, and cross-toolbox behavior flows only through the system
  invariants below and the shared port. Ownership constrains
  *registration*, not *availability*: personas whose jobs cross ownership
  boundaries compose toolboxes (building a dashboard in a fresh space
  takes three), and finer-grained selection is the per-tool allow list
  (`KIBANA_MCP_TOOLS`, D34), which registers the owning toolboxes and hides
  every unlisted tool — selection, never a reason to share registrations.
  Named tool lists (`@name` in that list, D41) are names for such selections,
  not toolboxes: they own no tool and register nothing of their own.
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
  The rule is load-bearing: the tier mechanism disables by tag, so an
  untagged tool would be visible at every tier. `tests/_registry.py` enforces
  it — `registry()` unpacks each tool's tags against the three tiers and fails
  on none or two — and every test that reads the registry runs it (among them
  `test_every_figure_in_the_prose_matches_the_registry`).
- Write safety is a **registration-time** tier: tools above the deployment
  cap are disabled at registration and never appear in the advertised list
  — the point is that destructive tools never enter the model's tool list.
  The `docs://tools` reference resource is deliberately not tier-gated (it
  documents every tool; a hidden one still fails as unknown), so its tier
  cells must be right: `test_docs_tool_reference.py` pins them (D57).
  A call naming a hidden tool fails as unknown (observed; the pinned
  assertions cover list-time visibility).
- Surface selection only ever hides. The per-tool allow list
  (`KIBANA_MCP_TOOLS`) is applied as a second disable beside the tier's, so
  the published set is listed ∩ tier; assembly never calls an enable, which
  in FastMCP overrides an earlier disable and could re-publish a tool the
  tier hid (D34; pinned by `tests/unit/test_tool_allow_list.py`). Named
  lists (`@name`, D41) expand before any name is checked, so they change what
  is listed, never this rule; the coherence check over the published tools
  only ever logs — it neither adds a tool nor fails startup.
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
- A space the API key has no privilege in is a privilege problem, never a
  missing space: Kibana answers 403 for such an id whether or not a space has
  it, and its space listing leaves it out, so `space`/`source_space`
  validation and the copy/share target check name the key's privileges and
  `list_spaces` ("spaces this key can view"), never `create_space`; only a
  404 from a key that can read spaces means the space does not exist (D58).

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
  guidance strings, schema-rejection pins), `tests/e2e/` (live models, on
  demand since D61: Claude via `claude -p`, and a local model in LM Studio),
  `tests/ephemeral/` (destructive paths on throwaway stacks, on every
  supported Kibana line in CI, D62).
- Every toolbox's model-facing surface is replayed (D61): each has at least
  one recorded real-model turn in `tests/e2e_replay/transcripts/`
  (`tests/unit/test_replay_coverage.py` fails a toolbox without one), and
  the gate replays them from the checkout, the built wheel and the built
  Docker image over HTTP with the key in an `Authorization: ApiKey` header,
  where a call without the header is refused naming the fix (D62).
- The whole: `make dod` → `VERDICT: GO` (see [brief.md](brief.md)), on the
  newest supported Kibana version: the gate checks the running stack's version
  against the pin before the seeded-stack criteria and names it on their GO
  lines (D30, D53). The verdict is bound to a tree: header and VERDICT line
  name HEAD, the content tree id (uncommitted and untracked work included) and
  clean/dirty; a tree that changes during the run is NO-GO; the run is recorded
  in `/tmp/dod-<repo>/verdict.json`, and a "GO at `<sha>`" claim holds only when
  `git rev-parse <sha>^{tree}` equals the recorded tree id (D55). The suites
  run as declared: the gate clears the pytest and coverage variables that
  reshape them and `KIBANA_MCP_SERVER_BIN`/`KIBANA_MCP_SERVER_IMAGE` (which
  would replay another server), refuses `UV_*`, `PYTHON*`, `LD_*` and those
  two names in `.env.local`, prints `N passed / M collected` on every suite line, and
  refuses shell Kibana credentials that differ from `.env.seed`, and server
  credentials in the shell or `.env.local` that the replayed server would read
  ahead of the seed (D56).
- Privilege-dependent behaviour runs under restricted keys (D58): the
  contract tier's privilege matrix calls every tool that takes `space`,
  `source_space`, `target_spaces` or `spaces`, and every listing guard,
  through the MCP tool layer under three keys from the shared
  `restricted_key` fixture (default-space `all`, `read` everywhere, no Kibana
  privilege) and fails on a crash, masked text, a write tool succeeding under
  the read-only key or a hidden space reported as missing; a unit test holds
  its rows to the registry and to reaching Kibana, so a new space-taking tool
  has a working row by construction.
- No tool result carries a credential (D59): every result the contract and
  replay tiers receive through a fastmcp `Client` is scanned for the seeded
  canaries (a Fleet output and a Fleet proxy with dummy `service_token`,
  `config_yaml`, `certificate_key` and proxy-header values, created by
  `scripts/stack.sh seed` from `scripts/canaries/`), the test key, and
  secret-bearing keys with a value; a hit fails the test naming the fix.
- Every read tool runs through the MCP tool layer against real Kibana (D60):
  the contract tier's read-tool sweep calls each read-tier tool once through
  a fastmcp `Client` on the full server and requires no `isError`,
  structured content, and rows from the list tools the stack or the sweep
  fills; its rows equal the registry's read tools. The ephemeral suites drive their destructive calls
  through a `Client` too, each confirm gate refused first.
- The model-facing surface is budgeted (D60): `docs/spec/surface-budget.toml`
  caps the default selection, every profile, every toolbox and every tool by
  tool count, description and input-schema size; a cap is raised only by a
  new decision that states the new numbers and why the growth is worth it.
- Every tool that takes an id has a stated source (D60): a `DEPENDENCIES`
  edge to a tool that shows it, named in the description, or a reasoned
  exemption (checked per tool, not per input);
  every toolbox at every tier and every profile is coherent but for reviewed
  cross-toolbox gaps.
- Each Fleet space-awareness mode has a tier (D63): the contract tier runs
  `uninstall_package`'s awareness-on path (every space the key can access,
  Kibana's hidden-space 403) on the seeded stack, and
  `fleet_unaware_ephemeral` runs its awareness-off path (package-policy
  refusal, Fleet Server and monitoring guards, uninstall once none is left) on an isolated
  stack booted with Fleet's `useSpaceAwareness` feature off; a test that
  branches on the mode fails on a stack in the other one.
- Every change carries a CHANGELOG entry: `changelog_entry` and a `checks.yml`
  PR step run `scripts/checks/changelog-entry.sh`, which requires a new entry
  under `## [Unreleased]` when non-exempt paths changed since the merge-base
  with `origin/main` (D54).
- The docs state the registry's figures (D57): unit tests compare the tool
  counts in the prose (README, `docs/*.md`, the specs but `decisions.md`,
  `profiles/README.md`) with the registry, each `docs/tools.md` table row's
  section and tier with the tool's toolbox and tier, the annotation table with
  the real hints, and every test the docs cite with `tests/`.
