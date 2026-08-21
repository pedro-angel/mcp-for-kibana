"""Replay RECORDED turns through a real MCP client. No LLM.

Why this tier exists
--------------------
`tests/e2e/test_lmstudio.py` is the honest end-to-end gate: a real local model
decides for itself which tools to call. It cannot run in CI, because CI has no
GPU, no LM Studio runtime and no loaded model — so the entire MCP surface a
model actually touches was, until this tier, certified only on a maintainer's
laptop.

This tier removes the model and keeps everything else real:

    recorded turn ──> ScriptedModel ──> fastmcp.Client   (a REAL MCP client)
    (fixed JSON)                          │ real stdio transport, real
                                          │ initialize + tools/list handshake
                                          ▼
                                   mcp-for-kibana (REAL server subprocess)
                                          │
                                          ▼
                             KibanaPyGateway ─> real Kibana ─> real Elasticsearch

Only token sampling is replaced. MCP framing, tool discovery, JSON-Schema
argument validation, the server, the gateway, kibana-py, HTTP, Kibana and
Elasticsearch are all the production paths.

What it certifies, precisely
----------------------------
1. Every tool the recorded turn reached still exists, under the same name.
2. The arguments in the transcript still VALIDATE against the server's live
   inputSchema — i.e. we did not tighten a schema in a way that breaks prompts
   and transcripts already in the wild.
3. The arguments the recording runtime rejected are still rejected, so the
   guard that caught them has not silently loosened.
4. The server's ERROR GUIDANCE still says what let the model self-correct. The
   flights run is not a happy path: the model first sent a bad `time_range`,
   then an unknown data view, then two non-existent field names, and recovered
   each time only because the error text named the fix. That guidance is a
   product feature for every LLM using this server, and this is what regression-
   protects it.
5. The corrected call still produces a real dashboard in real Kibana, asserted
   with `assert_model_built_dashboard` — the SAME helper the live-model test
   uses, so the two tiers cannot drift in what "built it correctly" means.

What it does NOT certify
------------------------
That a model can CHOOSE these calls from the prompt. Nothing here exercises
reasoning; the calls are fixed. That claim belongs to `e2e_green` alone, and no
passing run of this tier may be reported as evidence for it.

The transcripts
---------------
`transcripts/flights-dashboard.json` was recorded from a real MODEL run — the
model, runtime, date and token stats are in its `recorded` block, and each step
keeps the verbatim `observed_output` alongside the invariants this test gates.
It was generated from the capture, never hand-written, so the arguments are
exactly the bytes the model emitted.

`transcripts/space-dashboard.json` covers the space-targeted surface
(`create_space` then seven `space=`-scoped calls). **No model chose that
sequence** — it is an authored call order, recorded by
`record_space_transcript.py` driving the same real server over stdio against
the live stack, so the arguments and the `observed_output` bodies are real
bytes off the wire but the ORDER carries no evidence about model behaviour.
Its server needs the platform-admin toolbox for `create_space`, which is what
each transcript's `extra_env` supplies.

A transcript that carries a `space_token` is replayed into a FRESH space per
run: the recorded space id is substituted (as the marker already was), the
`assert_model_built_dashboard` gateway is space-scoped, the default space is
asserted free of the run's marker (contamination check), and teardown deletes
the space.

`transcripts/alerting-space-recovery-gemma-4-12b-qat.json` is a REAL model
turn on the alerting-space surface — and deliberately NOT a happy path: the
model's first `.es-query` create was rejected ("params invalid: [size]…"),
and the retry succeeded only because the error text named the missing field.
Its error step is replayed with `expect.is_error: true` and needles on that
guidance, so the rejection message the model recovered from is regression-
protected exactly like the flights transcript's dashboard guidance. Its
`end_state: "alerting_rule"` selects the final assertion: the same
`score_alerting_space_run` scorer the live alerting gate uses, so the two
tiers cannot drift in what "built it correctly" means.
"""

import contextlib
import json
import os
import uuid
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from jsonschema import Draft7Validator

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from kibana_mcp.core.dashboards.identity import derive_dashboard_id
from kibana_mcp.core.errors import KibanaNotFound
from tests._alerting_space_ladder import (
    score_alerting_space_run,
    sweep_default_space_marker_rules,
)
from tests._dashboard_assertion import assert_model_built_dashboard

pytestmark = pytest.mark.e2e_replay

_TRANSCRIPT_DIR = Path(__file__).parent / "transcripts"
_REPO_ROOT = Path(__file__).resolve().parents[2]

# (transcript file, extra server env that transcript needs). The space
# transcript's server must expose `create_space`, which lives in the
# platform-admin toolbox — off in the default set, so without this env the
# transcript's first step would fail on a tool that is not even registered.
_TRANSCRIPTS = [
    ("flights-dashboard.json", {}),
    (
        "space-dashboard.json",
        {"KIBANA_MCP_TOOLBOXES": "dashboards,data-management,platform-admin"},
    ),
    (
        # A REAL model turn (call order chosen by the model in a live run) —
        # see the transcript's `recorded` block for provenance.
        "space-dashboard-gemma-4-12b-qat.json",
        {"KIBANA_MCP_TOOLBOXES": "dashboards,data-management,platform-admin"},
    ),
    (
        # A REAL model turn on the alerting-space surface, including the
        # REJECTED first create (error guidance certified) — see docstring.
        "alerting-space-recovery-gemma-4-12b-qat.json",
        {"KIBANA_MCP_TOOLBOXES": "alerting,platform-admin"},
    ),
]


@pytest.fixture(scope="module", params=_TRANSCRIPTS, ids=lambda case: case[0])
def transcript(request) -> dict:
    """One recorded turn, plus the extra server env its replay needs.

    The env travels inside this fixture's dict (under `extra_env`, a key no
    transcript file carries) so `mcp_client` can depend on this one
    parametrized fixture and still launch the server the case requires.
    """
    name, extra_env = request.param
    return {**json.loads((_TRANSCRIPT_DIR / name).read_text()), "extra_env": extra_env}


@pytest.fixture
def marker() -> str:
    """A fresh run marker, substituted for the recorded one.

    Without it every run would reuse the recorded title/description, so two
    concurrent runs (a CI matrix, a rerun racing its predecessor's teardown)
    would collide and each sweep the other's dashboards. A fresh UUID also
    bounds the teardown below: it can only ever match dashboards this run
    created.
    """
    return f"REPLAY {uuid.uuid4().hex[:8]}"


@pytest.fixture
def run_space(transcript) -> str | None:
    """A fresh space id for a space-scoped transcript; None for the others.

    Same reasoning as `marker`, one level up: a fixed shared space id would
    make two runs fight over one space, and would let teardown delete a space
    this run did not create. Fits the `^[a-z0-9_-]+$` id grammar.
    """
    return f"replay-{uuid.uuid4().hex[:8]}" if transcript.get("space_token") else None


@pytest.fixture
def gateway():
    # The autouse require_stack_env() gate in conftest guarantees both vars are
    # set (or the session already skipped/failed) before this fixture runs.
    with KibanaPyGateway.connect(
        os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"]
    ) as gw:
        yield gw


@pytest.fixture
def mcp_client(transcript):
    """A real MCP client speaking real stdio to a real server subprocess.

    Launched exactly the way an operator's mcp.json launches it (see
    docs/examples/mcp.json): `uv run mcp-for-kibana`, credentials via
    KIBANA_MCP_ENV_FILE. The transcript's `extra_env` is merged in ahead of the
    two pinned keys, so a transcript can select toolboxes but can never move
    the tier: it stays `write`, and leaving `destructive` off means a replay can
    never delete anything even if a transcript is later edited carelessly.
    """
    return Client(
        StdioTransport(
            command="uv",
            args=["--directory", str(_REPO_ROOT), "run", "mcp-for-kibana"],
            env={
                **os.environ,
                **transcript["extra_env"],
                "KIBANA_MCP_ENV_FILE": str(_REPO_ROOT / "elastic-start-local" / ".env.seed"),
                "KIBANA_MCP_TIER": "write",
            },
        )
    )


def _substitute(value, tokens: dict[str, str]):
    """Deep-replace every recorded token with this run's value.

    Runs over the recorded `arguments` AND the `expect.contains` needles: a
    space id shows up in echoed results and in `/s/<id>/app/…` links, so an
    unsubstituted needle would look for the RECORDING's space in THIS run's
    output.
    """
    if isinstance(value, str):
        for recorded, fresh in tokens.items():
            value = value.replace(recorded, fresh)
        return value
    if isinstance(value, list):
        return [_substitute(v, tokens) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, tokens) for k, v in value.items()}
    return value


async def test_recorded_turn_replays_end_to_end(
    transcript, marker, run_space, gateway, mcp_client
):
    tokens = {transcript["marker_token"]: marker}
    recorded_space = transcript.get("space_token")
    if recorded_space:
        tokens[recorded_space] = run_space
    if recorded_id := transcript.get("dashboard_id_token"):
        # ids are globally unique across spaces (P9): the fresh marker in the
        # title yields a fresh id, so the recorded one is re-derived per run
        fresh_title = _substitute(transcript["dashboard_title"], tokens)
        tokens[recorded_id] = derive_dashboard_id(fresh_title)
    try:
        async with mcp_client as client:
            tools = {t.name: t for t in await client.list_tools()}

            # (1) every tool the recorded turn reached still exists, same name
            reached = {s["tool"] for s in transcript["steps"]} | {
                transcript["schema_rejected"]["tool"]
            }
            missing = sorted(reached - tools.keys())
            assert not missing, (
                f"tools the recorded turn used no longer exist: {missing}. "
                "Renaming a tool breaks every prompt and transcript in the wild."
            )

            # (3) the arguments the recording runtime rejected must STILL be
            # rejected by the live schema. Asserted before the happy path so a
            # loosened schema fails loudly rather than passing quietly.
            rejected = transcript["schema_rejected"]
            validator = Draft7Validator(tools[rejected["tool"]].inputSchema)
            assert list(validator.iter_errors(rejected["arguments"])), (
                f"arguments the recording runtime rejected now VALIDATE against "
                f"{rejected['tool']}'s schema — the guard loosened. "
                f"Runtime's reason at record time: {rejected['runtime_reason']}"
            )

            for index, step in enumerate(transcript["steps"]):
                tool_name = step["tool"]
                args = _substitute(step["arguments"], tokens)
                expect = step["expect"]

                # (2) a schema tightened since recording would break real
                # callers; catch it here with a precise message rather than as
                # an opaque validation error from the call below.
                errors = sorted(
                    Draft7Validator(tools[tool_name].inputSchema).iter_errors(args),
                    key=lambda e: list(e.path),
                )
                assert not errors, (
                    f"step {index} ({tool_name}): recorded arguments no "
                    f"longer satisfy the live inputSchema — "
                    f"{[f'{list(e.path)}: {e.message}' for e in errors]}"
                )

                result = await client.call_tool(tool_name, args, raise_on_error=False)
                body = "".join(
                    block.text for block in result.content if hasattr(block, "text")
                )

                assert result.is_error == expect["is_error"], (
                    f"step {index} ({tool_name}): expected is_error="
                    f"{expect['is_error']} because {expect['gates']}; got "
                    f"is_error={result.is_error} with body: {body[:500]}"
                )
                # (4) the guidance that let the model recover must survive.
                for needle in _substitute(expect["contains"], tokens):
                    assert needle in body, (
                        f"step {index} ({tool_name}): guidance regression — "
                        f"{needle!r} missing from the response. This text is what "
                        f"gates: {expect['gates']}. Full body: {body[:800]}"
                    )

                # Rule ids are server-generated UUIDs — unlike dashboard ids
                # they cannot be re-derived, so the recorded id is bound to
                # this run's id from the successful create's live response,
                # BEFORE any later step (enable_alert_rule) substitutes it.
                recorded_rule = transcript.get("rule_id_token")
                if (
                    recorded_rule
                    and recorded_rule not in tokens
                    and tool_name == "create_alert_rule"
                    and not expect["is_error"]
                ):
                    tokens[recorded_rule] = json.loads(body)["id"]

        # (5) same assertion helper as the live-model tier. A scoped transcript
        # is verified through a SPACE-SCOPED gateway, built here and not as a
        # fixture: connect(space=…) validates the space fail-closed, so a
        # fixture-time connect would raise before the create_space step ran.
        if transcript.get("end_state") == "alerting_rule":
            # Same scorer as the live alerting gate (test_lmstudio_alerting_space)
            # — the two tiers cannot drift in what "built it correctly" means.
            with KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], run_space
            ) as scoped:
                ladder = score_alerting_space_run(gateway, scoped, run_space, marker)
            assert ladder["passed"], (
                f"replayed alerting turn did not leave an enabled .es-query "
                f"marker rule in space '{run_space}': {ladder}"
            )
            assert not ladder["default_space_contamination"], (
                f"space targeting leaked: the default space holds a marker rule "
                f"for a turn that passed space='{run_space}' on every rule call"
            )
        elif run_space is not None:
            with KibanaPyGateway.connect(
                os.environ["KIBANA_URL"], os.environ["KIBANA_TEST_API_KEY"], run_space
            ) as scoped:
                assert_model_built_dashboard(scoped, marker)
            leaked = gateway.search_dashboards(marker)
            assert not leaked, (
                f"space targeting leaked: the default space holds "
                f"{[d.id for d in leaked]} for a turn that passed space="
                f"'{run_space}' on every call"
            )
        else:
            assert_model_built_dashboard(gateway, marker)
    finally:
        # Unconditional sweep on every path: tools execute server-side, so a
        # dashboard can exist even when a later assertion fails. The marker is a
        # fresh UUID, so this can only touch what this run created.
        for dashboard in gateway.search_dashboards(marker):
            gateway.delete_dashboard(dashboard.id)
        if transcript.get("end_state") == "alerting_rule":
            # Contamination path: a marker rule in the DEFAULT space (the
            # in-space rules die with the space below). Marker-scoped, so this
            # can only touch what this run created.
            sweep_default_space_marker_rules(gateway, marker)
        if run_space is not None:
            # force=True: the space holds the objects the turn created. The
            # suppression covers a failure BEFORE create_space landed — the
            # space then does not exist, and a teardown crash would mask the
            # error that actually stopped the run.
            with contextlib.suppress(KibanaNotFound):
                gateway.delete_space(run_space, force=True)
