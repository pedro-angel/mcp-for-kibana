"""The local-model study harness end to end, against HTTP fakes of LM Studio,
Kibana and Honeycomb (tests/_local_study_fakes.py): records written, scoring,
resume, retries of environment failures, redaction, preflight, analysis.

What only the owner's Mac can verify (real LM Studio stream shape, real model
behaviour, real Kibana objects) is listed in scripts/experiment/LOCAL_STUDY.md.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from tests._local_study_fakes import FakeHoneycomb, FakeKibana, FakeLMStudio

REPO = Path(__file__).resolve().parents[2]
EXP = REPO / "scripts" / "experiment"
sys.path.insert(0, str(EXP))

lr = importlib.import_module("local_run")
la = importlib.import_module("local_analyze")

KIBANA_KEY = "VnVhQ2ZHY0JDZGJrUXctMjBpZGQ6dWkybHAyYXhUTm1zeXFhd3FRclFsQQ=="
LM_TOKEN = "sk-lm-Zq81Kd02LpQ7mXv3Rt9Ws4Yb"
HC_KEY = "hcaik_01jx7Qp4Lm2Nn8Rr6Tt0Vv9Ww3Yy5Zz1Aa"
ENTRY = "mcp-for-kibana-study"


@pytest.fixture
def env(tmp_path, monkeypatch):
    kb = FakeKibana(KIBANA_KEY)
    lm = FakeLMStudio(kb, LM_TOKEN, ENTRY)
    hc = FakeHoneycomb(HC_KEY)
    seed = tmp_path / ".env.seed"
    seed.write_text(f"KIBANA_URL={kb.url}\nKIBANA_TEST_API_KEY={KIBANA_KEY}\n")
    mcp_json = tmp_path / "mcp.json"
    mcp_json.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "true"},
                    ENTRY: {
                        "command": sys.executable,
                        "args": ["-c", "from kibana_mcp.server import main; main()"],
                        "env": {
                            "KIBANA_URL": kb.url,
                            "KIBANA_API_KEY": KIBANA_KEY,
                            "KIBANA_MCP_TOOLBOXES": "dashboards,data-management,alerting",
                            "KIBANA_MCP_TIER": "write",
                        },
                    },
                }
            }
        )
    )
    cfg = json.loads((EXP / "local_study.json").read_text())
    cfg["models"] = ["fake/pass", "fake/fail"]
    cfg["lmstudio"].update(url=lm.url, mcp_json=str(mcp_json), run_timeout_s=30, idle_timeout_s=10)
    cfg["kibana"]["seed_file"] = str(seed)
    cfg["runs"].update(out_dir=str(tmp_path / "runs"), per_cell=2, max_attempts=2)
    cfg["honeycomb"].update(endpoint=f"{hc.url}/v1/traces", required=True)
    path = tmp_path / "local_study.json"

    def write(c=None):
        path.write_text(json.dumps(c or cfg))
        return lr.load_config(path)

    monkeypatch.setenv("LMSTUDIO_API_TOKEN", LM_TOKEN)
    monkeypatch.setenv("HONEYCOMB_API_KEY", HC_KEY)
    ns = type("Env", (), {})()
    ns.kb, ns.lm, ns.hc, ns.cfg, ns.write, ns.out = kb, lm, hc, cfg, write, tmp_path / "runs"
    ns.mcp_json = mcp_json
    yield ns
    for s in (kb, lm, hc):
        s.close()


def _records(out: Path, phase: str) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((out / phase).glob("*/*/record.json"))]


def _all_text(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


def _attrs(span: dict) -> dict:
    out = {}
    for a in span["attributes"]:
        v = a["value"]
        out[a["key"]] = next(iter(v.values()))
    return out


# ---------------------------------------------------------------- end to end


def test_pilot_records_scores_resets_and_traces(env):
    mcp_before = env.mcp_json.read_bytes()
    cfg = env.write()
    assert lr.run_matrix(cfg, "pilot") == 0
    recs = _records(env.out, "pilot")
    assert len(recs) == 8  # 2 models x 4 levels x 1

    passing = {r["level"]: r for r in recs if r["model"] == "fake/pass"}
    failing = {r["level"]: r for r in recs if r["model"] == "fake/fail"}
    for lv, r in passing.items():
        assert r["valid"] and r["success"], (lv, r["failure_reason"])
        assert r["steps_passed"] == r["chain_length"]
        assert r["stream_final_event"] == "ok"
        # the first chat.end is the result; the fake sends a bogus second one
        assert r["end_events"] == 2 and r["output_tokens"] > 1
    assert passing["L2"]["n_failed_tool_calls"] == 1  # the recovered create_dashboard
    assert passing["L2"]["failure_classes"] == {"validation": 1}
    assert passing["L4"]["tool_names"][-2:] == ["update_panel", "get_alert_rule"]

    assert all(r["valid"] and not r["success"] for r in failing.values())
    assert failing["L1"]["steps_passed"] == 0
    assert "answer '@timestamp', expected 'timestamp'" in failing["L1"]["failure_reason"]
    assert failing["L1"]["n_invalid_tool_calls"] == 1
    assert "dashboard has 1 panels, expected 2" in failing["L2"]["failure_reason"]
    assert "rule disabled" in failing["L3"]["failure_reason"]
    l4 = failing["L4"]["failure_reason"]
    assert "no successful update_panel call" in l4 and "no panel titled 'Responses by code'" in l4
    # leading-steps-passed is the chain depth: L3 fails at step 1 (answer) already
    assert failing["L3"]["first_failed_step"] == "describe"

    # every run's evidence
    for r in recs:
        d = next(env.out.glob(f"pilot/pilot/{r['run_id']}"))
        for f in (
            "cell.json",
            "before.json",
            "prompt.txt",
            "request.json",
            "events.jsonl",
            "response.json",
            "artifacts.json",
            "spans.json",
            "record.json",
        ):
            assert (d / f).exists(), f
        assert r["otel"].startswith("sent: HTTP 200")

    # Kibana back to its pre-run state; pre-existing objects untouched
    assert set(env.kb.dashboards) == {"preexisting-flights"}
    assert set(env.kb.rules) == {"preexisting-rule"}

    # requests: no context_length, no sampling, only the study's integration
    for req in env.lm.requests:
        assert set(req) == {"model", "input", "integrations", "stream"}
        assert req["integrations"] == [f"mcp/{ENTRY}"]
        assert KIBANA_KEY not in req["input"]
    l3 = next(
        q
        for q in env.lm.requests
        if "alert rule named" in q["input"] and "Responses" not in q["input"]
    )
    assert "must be enabled" in l3["input"]

    # Honeycomb: one trace per run, conversation id = run id, span counts match
    spans = env.hc.spans()
    roots = [s for s in spans if s["name"].startswith("invoke_agent")]
    assert len(roots) == 8
    by_run = {}
    for s in spans:
        a = _attrs(s)
        by_run.setdefault(a["gen_ai.conversation.id"], []).append((s, a))
    assert set(by_run) == {r["run_id"] for r in recs}
    for r in recs:
        group = by_run[r["run_id"]]
        tools = [a for s, a in group if a["gen_ai.operation.name"] == "execute_tool"]
        assert len(tools) == r["n_tool_calls"] + r["n_invalid_tool_calls"]
        root = next(a for s, a in group if a["gen_ai.operation.name"] == "invoke_agent")
        assert root["study.level"] == r["level"] and root["agent.task.success"] == r["success"]
        assert int(root["gen_ai.usage.output_tokens"]) == r["output_tokens"]
    assert env.mcp_json.read_bytes() == mcp_before  # user-owned, never written


def test_resume_skips_done_cells_and_recovers_an_interrupted_run(env):
    env.cfg["models"] = ["fake/pass"]
    cfg = env.write()
    lr.run_matrix(cfg, "full", limit=3)
    assert len(_records(env.out, "full")) == 3

    # a run that crashed mid-way: dir with before.json, no record.json, and
    # what it created still in Kibana
    ctx = lr.Ctx(cfg)
    before = ctx.kb.snapshot()
    crashed = env.out / "full" / "b02" / "20990101T000000Z-crashed"
    crashed.mkdir(parents=True)
    (crashed / "cell.json").write_text(json.dumps({"key": "x"}))
    (crashed / "before.json").write_text(json.dumps(before))
    env.kb.create_dashboard("LOC-deadbeef Web logs", [])

    lr.run_matrix(cfg, "full")
    recs = _records(env.out, "full")
    assert len(recs) == len(lr.plan(cfg, "full")) == 8  # 1 model x 4 levels x 2 blocks
    assert len({r["cell"] for r in recs}) == 8  # nothing ran twice
    assert (crashed / "abandoned.json").exists()
    assert set(env.kb.dashboards) == {"preexisting-flights"}
    prog = json.loads((env.out / "progress-full.json").read_text())
    assert prog["cells_done"] == prog["cells_total"] == 8

    n_requests = len(env.lm.requests)
    lr.run_matrix(cfg, "full")  # complete: a rerun does nothing
    assert len(env.lm.requests) == n_requests


def test_harness_loading_keeps_one_study_model_at_full_context(env):
    """With JIT loading off in LM Studio, `lmstudio.load: harness` loads each
    model at its maximum context before its runs and unloads the previous
    study model; a model the study does not use is never unloaded."""
    env.lm.loaded = {"fake/leak"}  # not a study model: must stay loaded
    env.cfg["lmstudio"]["load"] = "harness"
    cfg = env.write()
    assert lr.run_matrix(cfg, "pilot") == 0
    recs = _records(env.out, "pilot")
    assert len(recs) == 8 and all(r["valid"] for r in recs)
    loads = [x for x in env.lm.load_log if x[0] == "load"]
    assert loads == [("load", "fake/pass", 131072), ("load", "fake/fail", 131072)]
    assert ("unload", "fake/pass", None) in env.lm.load_log
    assert "fake/leak" in env.lm.loaded
    loaded_by_harness = [r for r in recs if r.get("harness_load")]
    assert len(loaded_by_harness) == 2
    assert loaded_by_harness[0]["harness_load"]["status"] == 200


def test_jit_mode_never_calls_the_load_api(env):
    cfg = env.write()
    assert lr.run_matrix(cfg, "pilot") == 0
    assert env.lm.load_log == []


def test_send_pending_sends_the_spans_of_runs_without_a_key(env, monkeypatch):
    """Runs made while the Honeycomb key was missing keep their spans on disk;
    `send-pending` sends them once the key is there, and only them."""
    real = lr.env_value
    monkeypatch.setattr(lr, "env_value", lambda n: None if n == "HONEYCOMB_API_KEY" else real(n))
    env.cfg["honeycomb"]["required"] = False
    cfg = env.write()
    assert lr.run_matrix(cfg, "pilot", limit=2) == 0
    assert env.hc.posts == []
    assert all(r["otel"].startswith("skipped") for r in _records(env.out, "pilot"))
    monkeypatch.setattr(lr, "env_value", real)
    assert lr.cmd_send_pending(cfg) == 0
    recs = _records(env.out, "pilot")
    assert all(r["otel"].startswith("sent") and r["otel_sent_late"] for r in recs)
    roots = [s for s in env.hc.spans() if s["name"].startswith("invoke_agent")]
    assert len(roots) == 2
    assert lr.cmd_send_pending(cfg) == 0  # nothing left: no second send
    assert len(env.hc.posts) == 2
    assert KIBANA_KEY not in "\n".join(env.hc.raw)


def test_environment_failures_are_invalid_and_retried(env):
    env.cfg["models"] = ["fake/oom"]
    env.cfg["task"]["levels"] = env.cfg["task"]["levels"][:1]
    cfg = env.write()
    lr.run_matrix(cfg, "pilot")
    recs = _records(env.out, "pilot")
    assert len(recs) == 2  # max_attempts
    assert [r["attempt"] for r in recs] == [1, 2] or sorted(r["attempt"] for r in recs) == [1, 2]
    for r in recs:
        assert not r["valid"] and not r["success"]
        assert "insufficient system resources" in r["invalid_reason"]
    roots = [_attrs(s) for s in env.hc.spans() if s["name"].startswith("invoke_agent")]
    assert {a["error.type"] for a in roots} == {"invalid_run"}


def test_timeout_keeps_the_streamed_calls(env):
    env.cfg["models"] = ["fake/hang"]
    env.cfg["task"]["levels"] = env.cfg["task"]["levels"][:1]
    env.cfg["lmstudio"].update(run_timeout_s=1, idle_timeout_s=1)
    env.lm.hang_s = 3
    cfg = env.write()
    lr.run_matrix(cfg, "pilot")
    (r,) = _records(env.out, "pilot")
    assert r["timed_out"] and r["valid"] and r["end_reason"] == "timeout"
    assert r["output_rebuilt_from_stream"] and r["tool_names"] == ["describe_data_view"]
    assert not r["success"]


def test_non_streaming_mode(env):
    env.cfg["models"] = ["fake/pass"]
    env.cfg["lmstudio"]["stream"] = False
    cfg = env.write()
    lr.run_matrix(cfg, "pilot")
    recs = _records(env.out, "pilot")
    assert len(recs) == 4 and all(r["success"] for r in recs)
    assert all(r["stream_final_event"] == "n/a" for r in recs)


# ---------------------------------------------------------------- secrets


def test_redaction_keeps_the_key_out_of_files_and_traces(env):
    env.cfg["models"] = ["fake/leak"]
    env.cfg["task"]["levels"] = env.cfg["task"]["levels"][:1]
    cfg = env.write()
    lr.run_matrix(cfg, "pilot")
    (r,) = _records(env.out, "pilot")
    assert r["known_secret_exposures"] == 1
    assert r["otel"].startswith("sent")
    disk = _all_text(env.out)
    assert KIBANA_KEY not in disk and LM_TOKEN not in disk and HC_KEY not in disk
    assert "[REDACTED_KNOWN_SECRET]" in disk
    sent = "\n".join(env.hc.raw)
    assert KIBANA_KEY not in sent and "[REDACTED" in sent
    root = next(_attrs(s) for s in env.hc.spans() if s["name"].startswith("invoke_agent"))
    assert root["agent.secret.known_exposed"] is True


def test_send_is_refused_when_redaction_misses_the_key(env, monkeypatch):
    class NoRedaction:
        known = None

        def __init__(self, known):
            pass

        def __call__(self, text):
            return text

        def tree(self, x):
            return x

    monkeypatch.setattr(lr, "MultiRedactor", NoRedaction)
    env.cfg["models"] = ["fake/leak"]
    env.cfg["task"]["levels"] = env.cfg["task"]["levels"][:1]
    cfg = env.write()
    lr.run_matrix(cfg, "pilot")
    (r,) = _records(env.out, "pilot")
    assert r["otel"].startswith("refused: known secret #1 present")
    assert env.hc.posts == []


# ---------------------------------------------------------------- preflight


def test_preflight_passes_on_a_ready_setup(env, capsys):
    cfg = env.write()
    mcp_before = env.mcp_json.read_bytes()
    rc = lr.cmd_preflight(cfg, load=True)  # lists the real server's tools over stdio
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "PASS  server_tools: the entry starts and lists 24 tools" in out
    assert "PASS  load fake/pass" in out
    assert KIBANA_KEY not in out and LM_TOKEN not in out
    assert env.mcp_json.read_bytes() == mcp_before


def test_preflight_names_the_fix_for_each_failure(env, capsys, monkeypatch):
    env.cfg["models"] = ["REPLACE_WITH_lms_ls_KEY_1"]
    env.cfg["kibana"]["version"] = "9.5.6"
    env.kb.logs_installed = False
    cfg = env.write()
    data = json.loads(env.mcp_json.read_text())
    data["mcpServers"][ENTRY]["env"]["KIBANA_MCP_TOOLBOXES"] = "dashboards"
    env.mcp_json.write_text(json.dumps(data))
    monkeypatch.delenv("HONEYCOMB_API_KEY")
    monkeypatch.setattr(lr, "env_value", lambda n: None if n == "HONEYCOMB_API_KEY" else LM_TOKEN)
    rc = lr.cmd_preflight(cfg, load=False, list_tools=lambda e: ["describe_data_view"])
    out = capsys.readouterr().out
    assert rc == 1
    for needle in (
        "FAIL  config: placeholder model entries",
        "FAIL  kibana_version: Kibana is 9.5.5, the study pins 9.5.6",
        "FAIL  sample_data",
        "local_run.py install-sample",
        "FAIL  mcp_json_entry: KIBANA_MCP_TOOLBOXES is 'dashboards'",
        "FAIL  server_tools: 1 tools (want 24)",
        "FAIL  honeycomb_key",
    ):
        assert needle in out, needle
    assert out.count("fix:") >= 6
    assert KIBANA_KEY not in out


def test_install_sample_installs_once(env, capsys):
    env.kb.logs_installed = False
    cfg = env.write()
    assert lr.cmd_install_sample(cfg) == 0
    assert env.kb.logs_installed
    assert lr.cmd_install_sample(cfg) == 0
    assert "already installed" in capsys.readouterr().out


def test_run_refuses_without_the_honeycomb_key_when_required(env, monkeypatch):
    monkeypatch.setattr(lr, "env_value", lambda n: None if n == "HONEYCOMB_API_KEY" else LM_TOKEN)
    cfg = env.write()
    with pytest.raises(SystemExit, match="HONEYCOMB_API_KEY is not set"):
        lr.run_matrix(cfg, "pilot")


# ---------------------------------------------------------------- plan, status, analysis


def test_plan_rotates_models_and_levels_per_block(env):
    env.cfg["models"] = ["a/x", "b/y", "c/z"]
    env.cfg["runs"]["per_cell"] = 3
    cfg = env.write()
    cells = lr.plan(cfg, "full")
    assert len(cells) == 3 * 4 * 3 and len({c["key"] for c in cells}) == len(cells)
    blocks = {}
    for c in cells:
        blocks.setdefault(c["block"], []).append(c)
    assert [b[0]["model"] for b in blocks.values()] == ["a/x", "b/y", "c/z"]
    assert [b[0]["level"] for b in blocks.values()] == ["L1", "L2", "L3"]
    for b in blocks.values():
        assert len({(c["model"], c["level"]) for c in b}) == 12
    assert len(lr.plan(cfg, "pilot")) == 12


def test_prompts_grow_by_level_and_state_the_checks(env):
    cfg = env.write()
    levels = cfg["task"]["levels"]
    prompts = [lr.build_prompt(cfg, lv, "LOC-00000000") for lv in levels]
    for i, p in enumerate(prompts, 1):
        assert f"{i}. " in p and f"{i + 1}. " not in p
    assert "TIME_FIELD:" in prompts[0] and "exactly two panels" in prompts[1]
    assert "must be enabled" in prompts[2] and "RULE_ENABLED:" in prompts[3]


def test_status_estimates_the_full_run_from_the_pilot(env, capsys):
    cfg = env.write()
    lr.run_matrix(cfg, "pilot")
    est = lr.estimate(cfg, _records(env.out, "pilot"))
    assert est["runs_per_cell"] == 2 and est["cells_without_pilot"] == []
    assert est["max_runs_per_cell_in_budget"] > 1000  # fake runs take well under a second
    assert lr.cmd_status(cfg) == 0
    out = capsys.readouterr().out
    assert "== pilot: 8/8 cells done" in out and "estimate from the pilot" in out


def test_analysis_rates_intervals_and_chain(env, tmp_path):
    env.cfg["runs"]["per_cell"] = 3
    cfg = env.write()
    lr.run_matrix(cfg, "full")
    a = la.analyze(cfg, "full")
    cell = a["cells"]["fake/pass"]["L4"]
    assert cell["n_valid"] == 3 and cell["passes"] == 3
    assert cell["wilson95"][0] == pytest.approx(3 / (3 + 1.959964**2), abs=1e-3)
    assert cell["pass_hat_k"]["3"] == 1.0
    fail = a["cells"]["fake/fail"]["L2"]
    assert fail["passes"] == 0 and fail["first_failed_step"] == {"describe": 3}
    assert a["chain"]["fake/pass"]["lose_track_at"] is None
    assert a["chain"]["fake/fail"]["lose_track_at"] == "L1"
    assert a["chain"]["fake/pass"]["rates"] == [1.0, 1.0, 1.0, 1.0]
    m = cell["metrics"]
    assert m["output_tokens"]["median"] > 0 and m["s_per_step"]["n"] == 3
    # re-derived from the stored responses: same as the records
    assert a["consistency"]["mismatches"] == []
    queries = la.honeycomb_queries(cfg)
    assert queries["q01_runs"]["breakdowns"] == ["study.model", "study.level"]


def test_wilson_interval_matches_the_design_record():
    lo, hi = la.wilson(5, 10)
    assert (hi - lo) / 2 == pytest.approx(0.26, abs=0.01)  # STUDY_DESIGN.md: ±0.26 at 10 runs
    assert la.wilson(30, 30)[0] == pytest.approx(30 / (30 + 1.959964**2), abs=1e-3)
    assert la.wilson(0, 0) == (None, None)


def test_compacted_events_parse_like_the_raw_stream():
    raw = [
        {"t_ms": 1.0, "type": "reasoning.delta", "data": {"content": "I will "}},
        {"t_ms": 2.0, "type": "reasoning.delta", "data": {"content": "look."}},
        {"t_ms": 3.0, "type": "tool_call.start", "data": {"tool": "describe_data_view"}},
        {
            "t_ms": 9.0,
            "type": "tool_call.success",
            "data": {
                "tool": "describe_data_view",
                "arguments": {},
                "output": '[{"type":"text","text":"{}"}]',
            },
        },
        {"t_ms": 10.0, "type": "message.delta", "data": {"content": "TIME_FIELD: "}},
        {"t_ms": 11.0, "type": "message.delta", "data": {"content": "timestamp"}},
    ]
    small = lr.compact_events(raw)
    assert [e["type"] for e in small] == [
        "reasoning.delta",
        "tool_call.start",
        "tool_call.success",
        "message.delta",
    ]
    assert (
        small[0]["data"]["content"] == "I will look."
        and small[0]["t_end_ms"] == 2.0
        and small[0]["n"] == 2
    )
    a, b = lr.parse_run({"events": raw}, []), lr.parse_run({"events": small}, [])
    assert a["output"] == b["output"] and a["tool_ms"] == b["tool_ms"] == 6.0
    assert b["message_text"] == "TIME_FIELD: timestamp"
    assert raw[0]["data"]["content"] == "I will "  # the raw events are not mutated
