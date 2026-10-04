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
KIBANA_MCP_DOD_CYCLE_STACK=1 make dod          # full Definition-of-Done → VERDICT: GO
```

`make dod` needs no LLM: the live-model tier (`e2e_green`) is `n/a` in
`dod.config` as in CI's configs (D31), so the gate can reach GO on CI and in a
cloud session. The live-model tests run on demand with `make test-e2e` — a
local LM Studio runtime with the reference model loaded
([docs/e2e-setup.md](docs/e2e-setup.md)) — when a change touches tool names,
descriptions or schemas.

## House rules

- **The DoD gate certifies completion, not you.** No completion or release
  claim without `VERDICT: GO` at that exact tree; every change carries a
  CHANGELOG entry.
- **Never bypass hooks** (`--no-verify` is prohibited); commits are
  Conventional Commits with a provenance trailer (hooks enforce both).
- **Additive is default-off**; error text must name the fix; specs are
  reconciled onto shipped code in the same change-set.
- **Report numbers trace to committed run records** — never hand-transcribe
  results into documents.
- `.env.local` and `~/.lmstudio/mcp.json` are user-owned: never written by
  tooling or agents.
