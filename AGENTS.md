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

## House rules

- **The DoD gate certifies completion, not you.** No completion or release
  claim without `VERDICT: GO` at that exact tree; every change carries a
  CHANGELOG entry — `changelog_entry` (and a `checks.yml` step on PRs) requires
  a line added under `## [Unreleased]` when anything outside
  `scripts/experiment/runs/`, `docs/plans/` and `uv.lock` changed since the
  merge-base with `origin/main` (run `git fetch origin main` first; D54).
- **Never bypass hooks** (`--no-verify` is prohibited); commits are
  Conventional Commits with a provenance trailer (hooks enforce both).
- **Additive is default-off**; error text must name the fix; specs are
  reconciled onto shipped code in the same change-set.
- **Report numbers trace to committed run records** — never hand-transcribe
  results into documents.
- `.env.local` and `~/.lmstudio/mcp.json` are user-owned: never written by
  tooling or agents.
