# Agent guide

You are working on **mcp-for-kibana**: an MCP server exposing Kibana to LLMs
through curated, tier-gated toolboxes over a hexagonal core. The durable
source of truth is the **regeneration corpus** — start there, not in the code:

1. [docs/spec/brief.md](docs/spec/brief.md) — what this is, values, non-goals.
2. [docs/spec/design.md](docs/spec/design.md) — load-bearing shape, system invariants.
3. [docs/spec/decisions.md](docs/spec/decisions.md) — settled decisions with
   evidence; supersede explicitly, never silently.
4. [docs/spec/toolboxes/](docs/spec/toolboxes/) — per-toolbox behavior contracts.
5. [docs/spec/regeneration.md](docs/spec/regeneration.md) — rebuilding rules;
   what is authoritative when documents and code disagree.

Reference material: [docs/tools.md](docs/tools.md) (tool surface),
[docs/architecture.md](docs/architecture.md) (diagrams),
[docs/roadmap.md](docs/roadmap.md) (open work, recorded deferrals).

## Commands

```bash
make setup        # uv sync + git hooks (run once)
make check        # content gates: hooks + lint + audit + sast + unit + docs
scripts/stack.sh up && scripts/stack.sh seed   # live dev stack (seed after EVERY start)
make test-contract / test-e2e-replay           # live tiers
make test-e2e                                  # live model: Claude via claude -p (needs sign-in)
make test-e2e-local                            # live model: LM Studio (small local models)
KIBANA_MCP_DOD_CYCLE_STACK=1 make dod          # full Definition-of-Done → VERDICT: GO
```

`make dod` runs the Claude live-model tier: the criterion `e2e_green` is
`required` in `dod.config` (D42) and runs what `make test-e2e` runs — headless
Claude Code (`claude -p`) with only this server's tools through the
pre-registered tasks (`scripts/experiment/CLAUDE_E2E.md`), about 5–6 minutes.
It needs a seeded stack and a signed-in `claude` CLI, no API key (check with
`claude auth status`; without a sign-in the criterion is NO-GO and names the
fix); the model is `CLAUDE_E2E_MODEL`, default `haiku` (D38). CI's configs keep
`e2e_green` `n/a` (no Claude sign-in there). Run `make test-e2e` on its own when
a change touches tool names, descriptions or schemas. `make test-e2e-local`
runs the LM Studio tests for small local models — a tool-use model loaded and
named in `LMSTUDIO_MODEL` ([docs/e2e-setup.md](docs/e2e-setup.md)).

The DoD certifies the newest Kibana version the server supports (D30, D53).
Before the seeded-stack criteria (`contract_green`, `e2e_replay_green`,
`wheel_replay_green`, `e2e_green`) the gate reads the running Kibana's version
from `/api/status` with the seed key and requires the `ES_LOCAL_VERSION` pin in
`elastic-start-local/.env.example` (in CI: the matrix's version). A stack left on
another line — a study's 9.5.5, or 9.4.7 after `ES_LOCAL_VERSION=9.4.7 make
test-contract` — is NO-GO on each of them, naming
`scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed`; every
GO line names the version it certified.

A GO is bound to a tree (D55). The gate's header and VERDICT line name HEAD,
the content tree id — uncommitted and untracked files included (also files
marked skip-worktree or assume-unchanged; a submodule by its commit only),
built in a temporary index — and
`clean` or `dirty`; a tree that changed during the run (an edit saved
mid-gate, a `hygiene_hooks` fixer) is NO-GO `tree_stable`. The
log dir `/tmp/dod-<repo>/` is cleared at start and ends with `verdict.json`
(HEAD, tree id, dirty, config and its sha256, every criterion's result, Kibana
version, Claude model, timestamps). To check a claim "GO at `<sha>`": `git
rev-parse <sha>^{tree}` must equal the recorded tree id; a dirty GO certifies
only the commit whose tree matches it.

The suites run as declared (D56). The gate clears `PYTEST_ADDOPTS`,
`PYTEST_PLUGINS`, `PYTEST_DEBUG`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD`,
`COVERAGE_RCFILE` and `KIBANA_MCP_SERVER_BIN` (its header names the ones it
cleared), every suite line shows `N passed / M collected` (deselected tests
included in M), and a shell `KIBANA_URL` or `KIBANA_TEST_API_KEY` that differs
from `elastic-start-local/.env.seed` is NO-GO on the seeded-stack criteria,
naming `unset KIBANA_URL KIBANA_TEST_API_KEY`: the gate certifies the seeded
local stack. Both names count in any letter case, and `.env.local` may not set
either. So is a `KIBANA_API_KEY`, `KIBANA_PUBLIC_URL` or
`KIBANA_MCP_PUBLIC_KIBANA_URL` (any letter case) in the shell or `.env.local`, which the replayed
server would read ahead of the seed, and a `UV_*`, `PYTHON*`, `LD_*` or
`KIBANA_MCP_SERVER_BIN` line in `.env.local`, which the suites merge back in
after the gate clears the shell.

`KIBANA_MCP_DOD_CYCLE_STACK=1` only lets the gate **stop** the dev stack between
the seeded-stack criteria and the ephemeral tiers (`scripts/stack.sh stop`:
volumes and `.env.seed` survive). It never starts or restarts the stack: start
and seed it before `make dod`, and after the run it stays stopped — bring it back
with `scripts/stack.sh up && scripts/stack.sh seed` before the next live tier.

## House rules

- **The DoD gate certifies completion, not you.** No completion or release
  claim without `VERDICT: GO` at that exact tree; every change carries a
  CHANGELOG entry — `changelog_entry` (and a `checks.yml` step on PRs) requires
  a new entry under `## [Unreleased]` (a reworded, re-spaced or moved line is
  not one) when anything outside `scripts/experiment/runs/` and
  `.claude/plans/` changed since the merge-base with `origin/main` (run `git fetch origin main` first; D54).
- **Never bypass hooks** (`--no-verify` is prohibited); commits are
  Conventional Commits with a provenance trailer (hooks enforce both).
- **Additive is default-off**; error text must name the fix; specs are
  reconciled onto shipped code in the same change-set.
- **Report numbers trace to committed run records** — never hand-transcribe
  results into documents. Tool-surface figures in the docs are checked against
  the registry (`tests/unit/test_docs_counts.py`; a figure that records a past
  state goes in its `HISTORICAL` table), `docs/tools.md` rows against each
  tool's toolbox and tier, and every cited test must exist (D57).
- **Restricted keys come from one fixture** (D58): a contract test that needs
  a credential other than the superuser test key uses `restricted_key`
  (`tests/contract/conftest.py`), never a key of its own; a new tool that
  takes a space gets a privilege-matrix row automatically, and one that acts
  on a space listing without taking a space goes in `LISTING_GUARDS`
  (`tests/_privilege_matrix.py`).
- **No tool result carries a credential** (D59): the contract and replay
  tiers scan every result a fastmcp `Client` receives (autouse `secret_scan`,
  `tests/_secret_scan.py`) for the seeded canaries (`scripts/canaries/*.json`,
  created by `scripts/stack.sh seed`), the test key and secret-bearing keys
  with a value; a hit fails the test naming the fix. A new secret-bearing
  Kibana object type gets a canary file `scripts/canaries/<canary>.json` and
  a `<canary>:<Kibana API path>` entry in the seed's loop when its API takes
  the `id` in the POST body and returns the secret on GET (else its own seed
  step: D59), and a tool that returns credentials Kibana hands out is called
  through a `Client` in a live test (`tests/contract/test_secret_canaries.py`).
- `.env.local` and `~/.lmstudio/mcp.json` are user-owned: never written by
  tooling or agents.
