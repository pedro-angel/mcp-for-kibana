# Development facade for mcp-for-kibana — thin, self-documenting delegation to the
# repo's scripts and gates. Logic lives in scripts/; recipes stay one-liners.
# Facade design tracked in issue #14.
# GNU Make 3.81 compatible. .NOTPARALLEL: facade targets mutate shared state
# (elastic-start-local/.env.seed, the smoke container) — do not run test-contract and test-e2e
# concurrently. Target names follow the shared cross-repo vocabulary, checked by
# the vocabulary-conformance hook pinned in .pre-commit-config.yaml (the name
# manifest ships with the hook; nothing is vendored here).

.DEFAULT_GOAL := help
.NOTPARALLEL:

# --- Setup --------------------------------------------------------------

.PHONY: setup
setup: ## Install dev env: uv sync + git hooks (re-run after uv sync replaces .venv)
	uv sync --group docs
	uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

# --- Local test stack (docker) -------------------------------------------

.PHONY: stack-start
stack-start: ## Start the local dev stack: ES + Kibana + the APM/OTEL telemetry backend (idempotent; needs docker). Tests keep the lean path via scripts/stack.sh up
	KIBANA_MCP_STACK_APM=1 scripts/stack.sh up

.PHONY: stack-seed
stack-seed: ## Load sample data; mint an API key only if the current one is invalid
	scripts/stack.sh seed

.PHONY: stack-status
stack-status: ## Show stack container status (docker compose ps)
	scripts/stack.sh status

.PHONY: stack-env
stack-env: ## Print the seed creds (INCLUDING the API key); .env.local's token stays hidden
	scripts/stack.sh env

.PHONY: stack-stop
stack-stop: ## Stop the stack but keep volumes (non-destructive; restart with stack-start)
	scripts/stack.sh stop

.PHONY: stack-destroy
stack-destroy: ## DESTRUCTIVE: stop stack, delete volumes and elastic-start-local/.env.seed (user config in .env.local survives)
	scripts/stack.sh down

# --- Tests ----------------------------------------------------------------

.PHONY: test
test: ## Run unit tests with coverage (no stack; fail_under lives in pyproject [tool.coverage.report])
	uv run pytest -q --cov=kibana_mcp --cov-report=term-missing

.PHONY: test-contract
test-contract: ## Run contract tests (starts and seeds the stack itself)
	scripts/stack.sh up && scripts/stack.sh seed && uv run pytest -m contract -q

.PHONY: test-contract-ci
test-contract-ci: ## Contract tests against an ALREADY-provisioned stack (no stack-start). CI's contract job now certifies via the DoD gate (.github/dod/ci-contract.config), which runs this same pytest selection.
	uv run pytest -m contract -q

.PHONY: test-e2e
test-e2e: ## Run E2E tests (stack + local LM Studio — see docs/e2e-setup.md)
	scripts/stack.sh up && scripts/stack.sh seed && uv run pytest -m e2e -q

.PHONY: test-e2e-replay
test-e2e-replay: ## Replay a RECORDED real-model turn through a real MCP client (stack only — no LLM, so this one runs in CI)
	scripts/stack.sh up && scripts/stack.sh seed && uv run pytest -m e2e_replay -q

.PHONY: streams-ephemeral
streams-ephemeral: ## Certify streams disable/enable on an isolated ephemeral stack (up -> pytest -m ephemeral -> down); part of the DoD gate
	scripts/ephemeral_stack.sh

.PHONY: streams-ephemeral-clean
streams-ephemeral-clean: ## Force-remove a leaked ephemeral stack (project mcp-for-kibana-ephemeral)
	cd elastic-start-local && docker compose -p mcp-for-kibana-ephemeral down -v --remove-orphans || true

.PHONY: fleet-ephemeral
fleet-ephemeral: ## Battle-test fleet agent-lifecycle tools on an isolated 2-agent ephemeral stack (up -> pytest -m fleet_ephemeral -> down); stop the dev stack first
	scripts/fleet_ephemeral.sh

.PHONY: fleet-ephemeral-clean
fleet-ephemeral-clean: ## Force-remove a leaked fleet-ephemeral stack (project mcp-for-kibana-fleet-ephemeral)
	cd elastic-start-local && docker compose -p mcp-for-kibana-fleet-ephemeral down -v --remove-orphans || true

# --- Code quality ----------------------------------------------------------

.PHONY: hooks
hooks: ## Run all configured commit hooks against all files
	uv run pre-commit run --all-files

.PHONY: types
types: ## Type-check the package with mypy (strict; config in pyproject [tool.mypy])
	uv run mypy

.PHONY: lint
lint: types ## Static analysis: ruff + mypy + import contracts (security scans live in audit/sast)
	uv run ruff check
	uv run lint-imports

.PHONY: audit
audit: ## Audit dependencies for known vulnerabilities (pip-audit)
	uv run pip-audit

.PHONY: sast
sast: ## Static security scan of src/ (bandit, medium+ severity)
	uv run bandit -r src/ -ll -q

.PHONY: check
check: hooks lint audit sast test docs ## Local PR gate: hooks+lint+audit+sast+unit+docs (the vocabulary floor)

# --- Docs -------------------------------------------------------------------

.PHONY: docs
docs: ## Build the docs site (strict — warnings fail)
	uv run --group docs mkdocs build --strict

.PHONY: docs-serve
docs-serve: ## Serve the docs site locally with live reload
	uv run --group docs mkdocs serve

# --- Gates & artifact ---------------------------------------------------------

.PHONY: dod
dod: ## Run the Definition-of-Done gate (GO/NO-GO over dod.config)
	scripts/checks/definition-of-done.sh

.PHONY: build
build: ## Build the Docker image and smoke-test it (SMOKE_PORT overrides 18300)
	docker build -f docker/Dockerfile -t mcp-for-kibana:dev .
	scripts/checks/image-smoke.sh mcp-for-kibana:dev

.PHONY: package
package: ## Build the wheel and smoke-test it from outside the repo
	scripts/checks/wheel-smoke.sh

# --- Cleanup ------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build/test caches (never .venv or elastic-start-local/.env.seed)
	rm -rf site .pytest_cache .ruff_cache .import_linter_cache .mypy_cache dist
	find src tests docs -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: clean-all
clean-all: clean ## DESTRUCTIVE: clean plus the .venv setup created (never the stack or elastic-start-local/.env.seed)
	rm -rf .venv

# --- Help ---------------------------------------------------------------------

.PHONY: help
help: ## Show this help message
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
