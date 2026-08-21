"""The MVP success criterion, automated: a local LLM in LM Studio receives a
natural-language request and must produce a real Kibana dashboard through
mcp-for-kibana. See docs/e2e-setup.md for the one-time setup.

This is the ONLY tier where a model chooses the calls itself, so it is the only
evidence for that claim. It needs a GPU, an LM Studio runtime and a loaded
model, so it cannot run in CI; the deterministic replay of a recorded run
(tests/e2e_replay/, `e2e_replay_green`) covers the same MCP surface per-PR but
proves nothing about model reasoning. Passing runs of the two are not
interchangeable.

First observed green 2026-08-09 with openai/gpt-oss-20b: the model recovered
from a rejected `time_range`, an unknown data view and two wrong field names
using only the server's error guidance, then built the dashboard.

LM Studio's native REST API (`POST /api/v1/chat`) does not require an API
token on every installation — this harness treats `LMSTUDIO_API_TOKEN` as
OPTIONAL: send it as a Bearer header when set, but never skip the test for
its absence. (Verified 2026-07-09: a local LM Studio instance answered
`/api/v1/chat` with 200 and no Authorization header at all.)
"""

import json
import os
import uuid

import httpx
import pytest

from kibana_mcp.adapters.kibana.gateway import KibanaPyGateway
from tests._dashboard_assertion import assert_model_built_dashboard

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def lmstudio():
    token = os.environ.get("LMSTUDIO_API_TOKEN")
    return {
        "url": os.environ.get("LMSTUDIO_URL", "http://localhost:1234"),
        "token": token,
        # gpt-oss-20b's native tool grammar is parser-enforced by LM Studio;
        # qwen2.5-coder-14b stochastically corrupts its tool-call markers
        # (~1 run in 3 fails with tool_format_generation_error).
        "model": os.environ.get("LMSTUDIO_MODEL", "openai/gpt-oss-20b"),
    }


@pytest.fixture(scope="session")
def gateway():
    # The autouse require_stack_env() gate in conftest guarantees both vars
    # are set (or the session already skipped/failed) before this fixture
    # runs — no fallback skip here to avoid resurrecting skip-green silently
    # if that gate is ever removed.
    url = os.environ["KIBANA_URL"]
    key = os.environ["KIBANA_TEST_API_KEY"]
    with KibanaPyGateway.connect(url, key) as gw:
        yield gw


def test_model_creates_dashboard_from_description(lmstudio, gateway):
    marker = f"E2E {uuid.uuid4().hex[:8]}"
    prompt = (
        f"Using the kibana tools, create a dashboard titled '{marker} flight prices' "
        "with a bar chart of the average ticket price by carrier over the last 7 days, "
        "using the flights sample data. Make exactly one create-dashboard call, then stop."
    )
    try:
        headers = {}
        if lmstudio["token"]:
            headers["Authorization"] = f"Bearer {lmstudio['token']}"
        response = httpx.post(
            f"{lmstudio['url']}/api/v1/chat",
            headers=headers,
            # `context_length` is deliberately NOT sent. MEASURED 2026-08-12
            # against LM Studio 0.4.12: the value is a *load* parameter, so a
            # request whose context_length differs from the loaded instance
            # spawns a SECOND instance at that context (`gpt-oss-20b:2` at
            # 8000) instead of reusing the operator's. Omitting it reuses the
            # loaded model — no spawn, nothing to clean up on any Auto-Evict
            # setting — and gives the model the operator's full context rather
            # than 8000, which the endpoint docs call too low for MCP use
            # ("Higher values recommended for MCP usage") against the tool
            # schema plus whatever the model spends on reasoning.
            # Pinning it also evicts unrelated JIT-loaded models (measured).
            # Trade-off: the effective context is now whatever the operator
            # loaded. See docs/e2e-setup.md.
            json={
                "model": lmstudio["model"],
                "input": prompt,
                "integrations": ["mcp/mcp-for-kibana"],
            },
            timeout=600,
        )
        # A non-2xx here is most often the LM Studio "Allow calling servers
        # from mcp.json" integration toggle being off (see docs/e2e-setup.md)
        # rather than an httpx/network problem — surface the body before
        # raising so the failure is actionable without a debugger.
        if response.is_error:
            body = response.text
            print(f"LM Studio /api/v1/chat error body: {body}")
            # A model that never loaded was never exercised, so an opaque 400
            # here reads as "the model failed the gate" when the truth is "this
            # machine had no room for it". Name the cause instead. FAIL, never
            # skip: a skip would re-open the skip-green hole that
            # tests/_stack_env.py guards against on the stack creds.
            if "insufficient system resources" in body or "Failed to load model" in body:
                pytest.fail(
                    "ENVIRONMENT failure, not a model or mcp-for-kibana defect: LM "
                    f"Studio could not load {lmstudio['model']} for lack of "
                    "memory. Check `lms ps` for models held by other work and "
                    "unload with `lms unload <identifier>`. Body: " + body
                )
        response.raise_for_status()
        output = response.json()["output"]

        # Response shape, OBSERVED live 2026-08-09 (openai/gpt-oss-20b, LM
        # Studio /api/v1/chat, integrations: ["mcp/mcp-for-kibana"]) — no longer
        # inferred from written research. `output` is a list of typed items:
        #   reasoning          content
        #   tool_call          tool, arguments, output, provider_info
        #   invalid_tool_call  reason, metadata{tool_name, arguments}
        #   message            content
        # Executed calls are `tool_call`; `invalid_tool_call` is a turn LM
        # Studio's own validator rejected BEFORE reaching the server, so it is
        # deliberately not counted here — it never touched mcp-for-kibana. A
        # verbatim recording of one such run, replayed against a real MCP
        # client in CI, lives in tests/e2e_replay/transcripts/.
        tool_calls = [item for item in output if item.get("type") == "tool_call"]
        if not tool_calls and output:
            print("LM Studio output contained no 'tool_call'-typed items:")
            print(json.dumps(output, indent=2))
        assert tool_calls, f"model never called a tool; output: {output}"

        # Tolerant of the model creating >1 dashboard for one request (issue #23):
        # all bear the run marker and are swept below; logic is unit-tested in
        # tests/unit/dashboards/test_model_built_assertion.py.
        assert_model_built_dashboard(gateway, marker)
    finally:
        # Unconditional sweep: the model may have created dashboards even if
        # the chat call errored mid-flight (tools execute server-side before
        # the HTTP response), or created more than one (failing the len == 1
        # assertion above before any targeted delete could run). Deleting
        # every marker match keeps the stack clean on every failure path —
        # the marker is a fresh UUID per run, so this can only touch
        # dashboards this test created.
        for d in gateway.search_dashboards(marker):
            gateway.delete_dashboard(d.id)
