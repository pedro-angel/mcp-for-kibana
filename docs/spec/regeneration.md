# Regeneration brief — rebuilding this server from documents

Status: Draft v1.0 (2026-08-19) — regeneration corpus. The reading order and
rules of engagement for a coding agent rebuilding, extending, or replacing
this implementation. The premise: code is disposable; intent is not. The old
implementation is **evidence, not authority** — read it last, if at all.

## Reading order

1. [brief.md](brief.md) — what this is, values, non-goals, what "correct" means.
2. [design.md](design.md) — the load-bearing shape and system invariants.
3. [architecture.md](../architecture.md) — layers and runtime flows, as diagrams.
4. [decisions.md](decisions.md) — settled choices with evidence. Do not
   re-litigate silently; supersede explicitly (strike through, point to the
   replacement).
5. the contracts under `docs/spec/toolboxes/` — one behavior contract per toolbox: the WHAT a
   rewrite must preserve, at rewrite size.
6. [tools.md](../tools.md) — the per-tool reference surface.
7. [configuration.md](../configuration.md) and
   [deployment.md](../deployment.md) — the environment-variable surface
   (`KIBANA_URL`, `KIBANA_API_KEY`, `KIBANA_MCP_TIER`,
   `KIBANA_MCP_TOOLBOXES`, and the rest): this is wire compatibility with
   every deployed `mcp.json`, as contractual as any tool schema.
8. `src/kibana_mcp/ports/gateway.py` — the one code file elevated to spec
   status: the seam everything regenerates behind.

## What is authoritative, in order

1. **The behavior contracts and invariants** (this corpus).
2. **The transcript-driven tiers**: `tests/e2e_replay/` and `tests/e2e/`
   exercise the server the way a deployment does — a real MCP client over
   stdio, the server as a subprocess — so their *driving* half is
   implementation-blind. Honesty note: their harnesses are not import-free
   — final-state assertions and fixtures import the gateway adapter — so
   they are portable with modest harness edits, not zero. The transcript
   *format* is specified by its producer and consumer
   (`tests/e2e_replay/record_model_turn.py` and
   `tests/e2e_replay/test_replay.py`, whose docstrings are the schema);
   treat those two files as spec the way the port file is.
3. **The implementation-coupled tiers**: `tests/unit/` and
   `tests/contract/` import the package's module layout — including a few
   private symbols (the env-key fallback, the dashboards pre-flight
   helper), so keeping the public layout alone is *nearly* sufficient, not
   fully. A rewrite that changes layout ports their *assertions* — the
   behaviors they pin are listed per contract under "Enforcement".
4. **The old code** — consult only to disambiguate a contract, then fix the
   contract so the next reader never needs to.

When a contract and observed Kibana behavior disagree, run a live probe,
trust the observation, and **reconcile the contract in the same change**
(bump its Status version, record the probe in [decisions.md](decisions.md)).

## Environment

- Live stack: `scripts/stack.sh up && scripts/stack.sh seed` (Elasticsearch
  + Kibana 9.4 + Fleet from `elastic-start-local/`); seed **after every**
  stack start — the API key dies with the stack.
- Fast gates: `make check`. Full certification: `KIBANA_MCP_DOD_CYCLE_STACK=1
  make dod` → must print `VERDICT: GO`. Criteria in `dod.config`; the gate,
  not the author, certifies completion.
- Live-model tier: LM Studio setup in [e2e-setup.md](../e2e-setup.md);
  reference model and the reasons behind it are in
  [decisions.md](decisions.md).

## Rules of engagement

- **Additive is default-off**: new capability must leave every existing
  call byte-identical when unused.
- **Probe before designing** on any dependency behavior nobody has
  observed; record the probe outcome in the ledger.
- **Error text is interface**: when you touch a failure path, the message
  must still name the fix; the replay tier will hold you to recorded
  guidance.
- **Specs are living**: every change that shifts behavior updates the
  matching contract in the same change-set — a spec describing what you
  hoped to ship is a defect.
- **Claims come last**: nothing is tagged, published, or announced before
  the gate is green at that exact tree.
