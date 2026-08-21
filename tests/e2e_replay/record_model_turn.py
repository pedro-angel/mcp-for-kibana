"""Convert a REAL recorded model turn into a replay transcript.

    LMSTUDIO_MODEL=google/gemma-4-12b-qat \
        KIBANA_MCP_E2E_DUMP=/tmp/e2e-dumps \
        uv run pytest tests/e2e/test_lmstudio_space.py -m e2e -q
    uv run python tests/e2e_replay/record_model_turn.py /tmp/e2e-dumps/<run>.json

Provenance: unlike record_space_transcript.py (an authored call sequence),
the step ORDER and every argument here were chosen by a model in a live run —
the dump carries LM Studio's verbatim output items. Only two elements are
authored and say so in the transcript: the needle selection (each needle is
verified present in the recorded body before it is written) and the
schema_rejected entry (appended when the recorded turn contained no
client-rejected call; the live Draft-7 validation is the assertion, exactly
as in the authored transcript).

The dump's run must have PASSED overall, but individual calls may have
FAILED: a step whose body carries the gateway's error prefix ("Kibana
rejected the payload:") is recorded with expect.is_error True and needles
drawn from the guidance text — that is what regression-protects the error
message a model actually recovered from. The classification is textual
(the dump carries no error flag); the replay run asserts is_error live,
so a misclassified step fails loudly there.

Two tasks are recognized from the calls in the dump: the dashboards chain
(a marker-titled create_dashboard) and the alerting chain (a marker-named
.es-query create_alert_rule + enable_alert_rule); the alerting transcript
carries `end_state: "alerting_rule"` so the replay tier knows which final
assertion to run.
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from datetime import UTC, datetime  # noqa: E402

from kibana_mcp.core.dashboards.identity import derive_dashboard_id  # noqa: E402


# The gateway's KibanaRejected translation — the one stable prefix every
# payload rejection carries (adapters/kibana/gateway.py). Textual by
# necessity: the dump has no error flag. Replay asserts is_error live.
_ERROR_PREFIX = "Kibana rejected the payload:"


def _needles(body: str, space: str, marker: str) -> list[str]:
    candidates = [
        f'"id":"{space}"',
        f'"space":"{space}"',
        marker,
        f"/s/{space}/app/dashboards#/view/",
        "kibana_sample_data_flights",
    ]
    return [n for n in candidates if n in body]


def _alerting_needles(body: str, space: str, marker: str) -> list[str]:
    candidates = [
        _ERROR_PREFIX,  # lands only on rejected steps
        "params invalid",  # the guidance family the model recovered from
        f"[Space: {space}]",  # rejection carries the space suffix (scoped client)
        f'"id":"{space}"',
        f'"space":"{space}"',
        marker,
        ".es-query",
    ]
    return [n for n in candidates if n in body]


def convert(dump_path: Path) -> Path:
    dump = json.loads(dump_path.read_text())
    space, marker = dump["space"], dump["marker"]
    calls = [i for i in dump["output"] if i.get("type") == "tool_call"]
    if not calls:
        raise SystemExit("dump has no tool_call items")

    alerting = any(c["tool"] == "create_alert_rule" for c in calls)
    pick_needles = _alerting_needles if alerting else _needles

    steps, title, rule_ok, enable_ok, rule_id = [], None, False, False, None
    for c in calls:
        body = c.get("output") if isinstance(c.get("output"), str) else json.dumps(c.get("output"))
        args = c.get("arguments") or {}
        is_error = _ERROR_PREFIX in (body or "")
        if c["tool"] == "create_dashboard" and isinstance(args.get("title"), str):
            title = args["title"]
        if c["tool"] == "create_alert_rule" and not is_error:
            rule_ok = (
                marker in str(args.get("name", "")) and args.get("rule_type_id") == ".es-query"
            )
            # Rule ids are server-generated UUIDs (not derivable like dashboard
            # ids), so later steps referencing this id need a replay-time
            # binding — recorded here as rule_id_token.
            out = c.get("output")
            if isinstance(out, str):  # dumps serialize the content list
                out = json.loads(out)
            rule_id = json.loads(out[0]["text"])["id"]
        if c["tool"] == "enable_alert_rule" and not is_error:
            enable_ok = True
        steps.append({
            "tool": c["tool"],
            "arguments": args,
            "observed_output": body,
            "expect": {
                "is_error": is_error,
                "contains": pick_needles(body or "", space, marker),
                "gates": (
                    "recorded model turn — the error body carried the guidance the model recovered from"
                    if is_error
                    else "recorded model turn — every needle was verified present in the recorded body"
                ),
            },
        })
    if alerting:
        if not (rule_ok and enable_ok):
            raise SystemExit(
                "no successful marker-named .es-query create_alert_rule + "
                "enable_alert_rule — pick a passing run"
            )
    elif title is None or marker not in title:
        raise SystemExit("no create_dashboard call with a marker-bearing title — pick a passing run")

    integration = "mcp/mcp-for-kibana-alerting" if alerting else "mcp/mcp-for-kibana"
    transcript = {
        "recorded": {
            "at": datetime.now(UTC).date().isoformat(),
            "by": "tests/e2e_replay/record_model_turn.py",
            "model": dump["model"],
            "runtime": f"LM Studio /api/v1/chat with integrations: [{integration}]",
            "stack": "elastic-start-local + Kibana Sample Data Flights",
            "note": (
                "REAL model turn: call order and arguments are the model's own. "
                "Authored elements, by construction: needle selection (verified "
                "against recorded bodies), error-step classification (textual, "
                "on the gateway's rejection prefix; replay asserts is_error "
                "live) and the schema_rejected entry."
            ),
        },
        "marker_token": marker,
        "space_token": space,
        "steps": steps,
    }
    if alerting:
        transcript["end_state"] = "alerting_rule"
        transcript["rule_id_token"] = rule_id
        transcript["schema_rejected"] = {
            "tool": "create_alert_rule",
            "arguments": {
                "name": "x", "rule_type_id": ".es-query", "consumer": "stackAlerts",
                "params": {}, "space": "REPLAY-UPPER",
            },
            "runtime_reason": "authored addition: uppercase space id fails ^[a-z0-9_-]+$ under both Python re and Draft-7",
        }
        name = f"alerting-space-recovery-{dump['model'].split('/')[-1].replace('.', '-')}.json"
    else:
        transcript["dashboard_title"] = title
        transcript["dashboard_id_token"] = derive_dashboard_id(title)
        transcript["schema_rejected"] = {
            "tool": "create_dashboard",
            "arguments": {"title": title, "panels": [{"title": "x", "chart_type": "bar", "data_view": "x", "metrics": [{"agg": "average", "field": "AvgTicketPrice"}]}], "space": "REPLAY-UPPER"},
            "runtime_reason": "authored addition: uppercase space id fails ^[a-z0-9_-]+$ under both Python re and Draft-7",
        }
        name = f"space-dashboard-{dump['model'].split('/')[-1].replace('.', '-')}.json"
    out = Path(__file__).parent / "transcripts" / name
    out.write_text(json.dumps(transcript, indent=1) + "\n")
    return out


if __name__ == "__main__":
    print(f"wrote {convert(Path(sys.argv[1]))}")
