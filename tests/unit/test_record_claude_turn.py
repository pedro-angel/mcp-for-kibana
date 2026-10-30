"""Unit coverage for the Claude-turn recorder (tests/e2e_replay/record_claude_turn.py).

The recorder decides three authored parts of a per-toolbox transcript (D61):
the id bindings, the needles and the schema_rejected entry. A wrong binding
would replay a recorded id against a fresh stack; a needle the body never
held would fail every replay; a schema_rejected the schema accepts would pin
nothing. Each is pinned here on a small stream-json fixture.
"""

import json

import pytest

from tests.e2e_replay import record_claude_turn as rec

P = rec.PREFIX


def _stream(*pairs):
    """stream-json events for (name, input, result text, is_error) calls."""
    events = []
    for i, (name, args, text, err) in enumerate(pairs):
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": P + name, "input": args}]}})
        events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": err,
             "content": [{"type": "text", "text": text}]}]}})
    return events


def test_calls_pair_uses_with_results_in_order():
    events = _stream(("create_case", {"title": "a"}, '{"id":"c1"}', False),
                     ("add_case_comment", {"case_id": "c1"}, "boom: no", True))
    assert rec.calls(events) == [
        ("create_case", {"title": "a"}, '{"id":"c1"}', False),
        ("add_case_comment", {"case_id": "c1"}, "boom: no", True),
    ]


def test_calls_refuse_a_foreign_tool_or_a_missing_result():
    with pytest.raises(SystemExit, match="non-server tool"):
        rec.calls([{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "x", "name": "Bash", "input": {}}]}}])
    with pytest.raises(SystemExit, match="no tool_result"):
        rec.calls([{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "x", "name": P + "get_case", "input": {}}]}}])


def _steps(*pairs):
    return [{"tool": t, "arguments": a, "observed_output": o} for t, a, o in pairs]


def test_bindings_take_server_ids_only():
    steps = _steps(
        ("create_case", {"title": "E2E cafe0123 x"}, json.dumps(
            {"id": "6f1c0b9e-uuid", "title": "E2E cafe0123 x", "items": [{"ref": "r-123456"}]})),
        ("add_case_comment", {"case_id": "6f1c0b9e-uuid", "comment": "Checked it."}, "{}"),
        ("other", {"ref": "r-123456", "title": "E2E cafe0123 x", "ns": "default"}, "{}"),
    )
    prompt = "open a case titled 'E2E cafe0123 x' in 'default', comment 'Checked it.'"
    assert rec.bindings(steps, prompt, "cafe0123") == [
        {"value": "6f1c0b9e-uuid", "step": 0, "path": ["id"]},
        {"value": "r-123456", "step": 0, "path": ["items", 0, "ref"]},
    ]


def test_needles_are_echoed_arguments_and_top_level_keys():
    body = '{"id":"p1","key":"e2e_cafe0123_env","description":"E2E cafe0123 staging"}'
    args = {"key": "e2e_cafe0123_env", "value": "secret-value", "description": "E2E cafe0123 staging"}
    assert rec.needles(args, body, False) == [
        "e2e_cafe0123_env", "E2E cafe0123 staging", '"id":', '"key":', '"description":']
    # An error body adds its first clause, the guidance the model read.
    assert rec.needles({}, "Kibana rejected the payload: bad field", True) == [
        "Kibana rejected the payload"]


def test_schema_rejected_drops_a_required_input_or_adds_an_unknown_one():
    steps = _steps(("get_kibana_status", {}, "{}"), ("create_case", {"title": "t", "description": "d"}, "{}"))
    schemas = {"get_kibana_status": {"properties": {}}, "create_case": {"required": ["title"]}}
    out = rec.schema_rejected(steps, schemas)
    assert out["tool"] == "create_case" and out["arguments"] == {"description": "d"}
    only_status = rec.schema_rejected(steps[:1], schemas)
    assert only_status["arguments"] == {"unexpected_property": True}


def test_convert_refuses_a_failed_or_unreplayed_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "record.json").write_text(json.dumps({"task": "delete_one_dashboard", "passed": True}))
    with pytest.raises(SystemExit, match="not a replayed"):
        rec.convert(run)
    (run / "record.json").write_text(
        json.dumps({"task": "case_comment", "passed": False, "valid": True}))
    with pytest.raises(SystemExit, match="did not pass"):
        rec.convert(run)
