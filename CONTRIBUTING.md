# Contributing to mcp-for-kibana

Thanks for your interest in contributing! This document is the practical guide —
it is self-contained, and every convention in it is backed by a gate you can run
locally, so nothing here depends on knowing how the project was originally
built.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Prerequisites](#prerequisites)
- [Development Setup](#development-setup)
- [The Local Stack](#the-local-stack)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Code Quality & Gates](#code-quality--gates)
- [Documentation](#documentation)
- [Submitting Changes](#submitting-changes)
- [Architecture Orientation](#architecture-orientation)

## Code of Conduct

This project adopts the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). By
participating, you are expected to uphold these standards.

## Prerequisites

- **Python 3.12+** (the true floor; CI runs 3.12–3.14)
- **[uv](https://docs.astral.sh/uv/)** for dependency and environment management
- **Docker** (with Compose) to run the local Elastic stack for contract/e2e tests
- **git**

## Development Setup

```bash
# Fork on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/mcp-for-kibana.git
cd mcp-for-kibana
git remote add upstream https://github.com/pedro-angel/mcp-for-kibana.git

# Install the dev environment: uv sync + git hooks
make setup
```

`make setup` runs `uv sync --group docs` and installs the pre-commit and
commit-msg hooks. Re-run it after any `uv sync` that replaces `.venv` (which also
removes the installed hooks). Run `make help` to see every target.

## The Local Stack

Contract and e2e tests run against a real Kibana. A one-command local stack
(Elasticsearch + Kibana + an APM/OTEL backend) is provided:

```bash
make stack-start     # start (idempotent; needs docker)
make stack-seed      # load sample data + mint a dev API key
make stack-env       # print the seed creds — PRINTS THE API KEY; redact before sharing
make stack-stop      # stop, keep volumes
make stack-destroy   # DESTRUCTIVE: stop + delete volumes and the seed key
```

Credentials are written to the git-ignored `elastic-start-local/.env.seed`;
your own settings go in `.env.local` (never committed — see `.env.local.example`).

## Development Workflow

1. Create a branch from `main`.
2. Make the smallest correct change; keep the diff surgical.
3. For non-trivial work, agree the design before writing code — open an issue
   describing the surface you intend to add and why, and let it be reviewed.
   For anything touching a Kibana payload, probe a live stack first: the
   OpenAPI spec has been wrong often enough that contract tests, not
   documentation, are the authority here.
4. Write [Conventional Commits](https://www.conventionalcommits.org/) (`feat`,
   `fix`, `docs`, `chore`, `refactor`, `test`, `build`, `ci`, `perf`, `style`,
   `revert`). Each commit body must carry a provenance trailer — the commit-msg
   hook enforces both.
5. Keep the gates green as you go.

## Testing

Tests are split into tiers by the infrastructure they need
(markers in `pyproject.toml`):

```bash
make test              # unit — fakes only, no network, with coverage (>=90%)
make test-contract     # contract — starts+seeds the stack, hits live Kibana
make test-e2e          # e2e — drives the server through LM Studio (see docs/e2e-setup.md)
make streams-ephemeral # destructive streams path on an isolated ephemeral stack
make fleet-ephemeral   # fleet agent-lifecycle on an isolated 2-agent stack
```

The destructive `*-ephemeral` tiers spin up an isolated stack and tear it down,
so they never touch your dev stack. Stop the dev stack first (RAM).

## Code Quality & Gates

```bash
make check   # local PR gate: hooks + lint + audit + sast + unit + docs
make dod     # the full Definition-of-Done gate (GO/NO-GO over dod.config)
```

- `make lint` — `ruff` + `lint-imports` (the hexagonal import contracts)
- `make audit` — `pip-audit` (dependency CVEs)
- `make sast` — `bandit` (static security scan of `src/`)

CI (`.github/workflows/checks.yml`) invokes the **same** make targets, so local
and CI cannot drift. Completion is certified by `make dod`, never self-asserted —
run it before claiming a change is done.

## Documentation

Docs are first-class. Pages live in `docs/` (MkDocs Material); `docs/tools.md`
is the tool reference and must match the registered tool set (a drift-guard test
enforces this). Build locally with:

```bash
make docs        # strict build — warnings fail
make docs-serve  # live-reload preview
```

Every change adds a `CHANGELOG.md` entry under `[Unreleased]` — the DoD gate
requires one unconditionally (`changelog_entry = required` in `dod.config`).
If your change alters the tool surface, also update `docs/tools.md` and the
relevant narrative page.

## Submitting Changes

1. Ensure `make check` (and `make dod` where infrastructure permits) is green.
2. Push your branch and open a PR against `main`; fill in the PR template.
3. A maintainer reviews; address feedback with follow-up commits.

## Licensing

By submitting a pull request you agree that your contribution is licensed
under the Apache License 2.0, the same license as this project.

## Architecture Orientation

- **Hexagonal core** — pure domain logic (`core/`, `ports/`, `config`) imports no
  framework and no `kibana-py`; enforced by import-linter.
- **Toolboxes** (`src/kibana_mcp/toolboxes/`) — vertical slices of tools that
  register onto the server; each is tier-tagged (`read`/`write`/`destructive`).
- **Adapters** (`src/kibana_mcp/adapters/`) — the MCP protocol side and the
  `kibana-py` gateway (the only module allowed to import `kibana`).

See [`docs/architecture.md`](docs/architecture.md) for the full picture.
