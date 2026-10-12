"""The shared study tasks (scripts/experiment/study_tasks.py, tasks.json):
prompts per arm, the pure scoring over a collected state, answer parsing,
threshold and time-window equivalences, the time-range strip used by the
filter evaluation, and the span attributes. No network, no Docker; the live
behaviour (seed, collect_state, filter evaluation, replay of stored runs)
is exercised on the dev stack, as STUDY_DESIGN.md records."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "experiment"))

st = importlib.import_module("study_tasks")

CFG = st.load()
M = "RUN-1"
LOGS_DV = {"id": "logs-dv", "title": "kibana_sample_data_logs", "timeFieldName": "timestamp"}
FLIGHTS_DV = {"id": "fl-dv", "title": "kibana_sample_data_flights", "timeFieldName": "timestamp"}
TIME_FIELDS = {"timestamp": "@timestamp", "@timestamp": "@timestamp", "utc_time": "utc_time"}


def _panel(title, dv="logs-dv", field=None):
    x = {"operation": "terms", "fields": [field]} if field else {"operation": "date_histogram"}
    src = {"type": "data_view_reference", "ref_id": dv}
    return {"type": "vis", "config": {"title": title, "type": "xy",
                                      "layers": [{"data_source": src, "x": x}]}}


def _dash(title, panels, dash_id="d1"):
    return {"id": dash_id, "data": {"title": title, "panels": panels}}


def _rule(name, rule_id="r1", enabled=True, threshold=10, time_field="timestamp", interval="1m"):
    return {
        "id": rule_id, "name": name, "rule_type_id": ".es-query", "enabled": enabled,
        "schedule": {"interval": interval},
        "params": {"searchType": "esQuery", "index": ["kibana_sample_data_logs"],
                   "timeField": time_field, "thresholdComparator": ">",
                   "threshold": [threshold], "timeWindowSize": 5, "timeWindowUnit": "m"},
    }


def _weblogs_state(**over):
    state = {
        "task": "weblogs", "marker": M,
        "dashboards": [_dash(f"{M} Web logs", [_panel("Requests over time"),
                                                _panel("Responses by code", field="response.keyword")])],
        "rules": [_rule(f"{M} 5xx errors")],
        "seeded": {"dashboard": None, "rule": None}, "seed_before": None,
        "data_views": [LOGS_DV, FLIGHTS_DV], "time_fields": dict(TIME_FIELDS),
        "filters": {"r1": {"ok": True, "detail": "ok"}},
    }
    state.update(over)
    return state


OK_TEXT = "All done.\nTIME_FIELD: timestamp\nRULE_ENABLED: yes"


def _seeded_state(dash_title="Site health", n_panels=2, rule=None):
    seeded_rule = rule or _rule("Server errors", "study-seed-server-errors", enabled=False)
    panels = [_panel("Requests over time"), _panel("Requests by response code", field="response.keyword")]
    panels += [_panel("Requests by host", field="host.keyword")] * (n_panels - 2)
    seeded = {"dashboard": _dash(dash_title, panels, "study-seed-site-health"), "rule": seeded_rule}
    return seeded


# ---------------------------------------------------------------- tasks.json


def test_four_tasks_with_known_check_kinds():
    assert st.task_ids(CFG) == ["weblogs", "flights", "change", "lookup"]
    kinds = {"answer", "new_dashboard", "panel_title", "new_rule", "seeded_rule",
             "seeded_dashboard", "nothing_new", "nothing_changed"}
    for t in CFG["tasks"]:
        assert t["data"] in CFG["data"]
        for step in t["steps"]:
            assert step["checks"], f"{t['id']}.{step['id']} has no check"
            assert {c["kind"] for c in step["checks"]} <= kinds


def test_every_answer_line_is_asked_for_in_the_closing():
    for t in CFG["tasks"]:
        for step in t["steps"]:
            for c in step["checks"]:
                if c["kind"] == "answer":
                    assert f"{c['line']}:" in t["closing"], (t["id"], c["line"])


@pytest.mark.parametrize("task_id", ["weblogs", "flights", "change", "lookup"])
def test_prompt_differs_only_in_the_arm_preamble(task_id):
    server = st.build_prompt(CFG, task_id, "server", M)
    api = st.build_prompt(CFG, task_id, "api", M)
    assert server.startswith(CFG["arms"]["server"])
    assert api.startswith(CFG["arms"]["api"])
    assert server.split("\n", 1)[1] == api.split("\n", 1)[1]
    assert "{" not in server


def test_weblogs_prompt_states_the_four_steps_and_both_lines():
    p = st.build_prompt(CFG, "weblogs", "server", M)
    for text in ("1. Describe", "2. Build a dashboard", "3. Create an alert rule", "4. Edit",
                 f"'{M} Web logs'", "TIME_FIELD:", "RULE_ENABLED:"):
        assert text in p


def test_unknown_task_and_arm_name_the_choices():
    with pytest.raises(SystemExit, match="have:"):
        st.build_prompt(CFG, "nope", "server", M)
    with pytest.raises(SystemExit, match="have:"):
        st.build_prompt(CFG, "weblogs", "shell", M)


# ---------------------------------------------------------------- weblogs scoring


def test_weblogs_correct_run_passes_every_check():
    out = st.score(CFG, "weblogs", M, _weblogs_state(), OK_TEXT)
    assert out["success"], out["failure_reason"]
    assert out["steps_passed"] == out["steps_total"] == 4


def test_wrong_time_field_is_rejected():
    state = _weblogs_state(rules=[_rule(f"{M} 5xx errors", time_field="utc_time")])
    out = st.score(CFG, "weblogs", M, state, OK_TEXT)
    assert not out["success"]
    assert out["first_failed_step"] == "rule"
    bad = [c for c in out["criteria"] if not c["ok"]]
    assert [c["check"] for c in bad] == ["rule.time_field"]
    assert bad[0]["actual"] == "utc_time"


def test_alias_of_the_time_field_is_accepted():
    state = _weblogs_state(rules=[_rule(f"{M} 5xx errors", time_field="@timestamp")])
    assert st.score(CFG, "weblogs", M, state, OK_TEXT)["success"]


def test_filter_failure_is_reported_with_its_detail():
    state = _weblogs_state(filters={"r1": {"ok": False, "detail": "probe index: 1 of 5"}})
    out = st.score(CFG, "weblogs", M, state, OK_TEXT)
    assert "rule.filter: expected 'selects exactly the matching documents', " \
        "got 'probe index: 1 of 5'" in out["failure_reason"]


def test_answer_must_match_kibana_not_the_text_alone():
    text = "TIME_FIELD: @timestamp\nRULE_ENABLED: yes"
    out = st.score(CFG, "weblogs", M, _weblogs_state(), text)
    assert out["first_failed_step"] == "describe" and out["steps_passed"] == 0


def test_rule_enabled_answer_compares_with_the_rule_state():
    state = _weblogs_state(rules=[_rule(f"{M} 5xx errors", enabled=False)])
    out = st.score(CFG, "weblogs", M, state, "TIME_FIELD: timestamp\nRULE_ENABLED: no")
    checks = {c["check"]: c for c in out["criteria"]}
    assert checks["answer:RULE_ENABLED"]["ok"]
    assert not checks["rule.enabled"]["ok"]


def test_missing_answer_line_is_named():
    out = st.score(CFG, "weblogs", M, _weblogs_state(), "TIME_FIELD: timestamp")
    assert "no RULE_ENABLED: line in the reply" in out["failure_reason"]


def test_dashboard_with_a_third_panel_or_other_data_fails():
    three = _weblogs_state(dashboards=[_dash(f"{M} Web logs", [
        _panel("a"), _panel("Responses by code", field="response.keyword"), _panel("c")])])
    assert "3 panels, expected 2" in st.score(CFG, "weblogs", M, three, OK_TEXT)["failure_reason"]
    other = _weblogs_state(dashboards=[_dash(f"{M} Web logs", [
        _panel("a", dv="fl-dv"), _panel("Responses by code", field="response.keyword")])])
    assert "does not read" in st.score(CFG, "weblogs", M, other, OK_TEXT)["failure_reason"]


def test_steps_passed_counts_leading_steps_only():
    state = _weblogs_state(dashboards=[])
    out = st.score(CFG, "weblogs", M, state, OK_TEXT)
    assert out["steps_passed"] == 1
    assert out["first_failed_step"] == "dashboard"


# ---------------------------------------------------------------- seeded tasks


def test_change_correct_run_passes():
    seeded = _seeded_state("Site health", 3, _rule("Server errors", "study-seed-server-errors"))
    state = _weblogs_state(task="change", dashboards=[], rules=[], seeded=seeded, filters={})
    out = st.score(CFG, "change", M, state, "Done.")
    assert out["success"], out["failure_reason"]


def test_change_with_the_rule_still_disabled_fails_at_enable():
    seeded = _seeded_state("Site health", 3)
    state = _weblogs_state(task="change", dashboards=[], rules=[], seeded=seeded, filters={})
    out = st.score(CFG, "change", M, state, "Done.")
    assert out["steps_passed"] == 1 and out["first_failed_step"] == "enable"


def test_change_that_recreates_objects_fails():
    state = _weblogs_state(task="change", dashboards=[], rules=[_rule("Server errors", "new")],
                           seeded={"dashboard": None, "rule": None}, filters={})
    out = st.score(CFG, "change", M, state, "Done.")
    assert "the seeded dashboard no longer exists" in out["failure_reason"]
    assert "nothing_new" in out["failure_reason"]


LOOKUP = "PANELS: 2\nRULE_NAME: Server errors\nINTERVAL: 1m\nENABLED: no"


def test_lookup_correct_answers_pass_and_a_change_fails():
    seeded = _seeded_state()
    state = _weblogs_state(task="lookup", dashboards=[], rules=[], seeded=seeded,
                           seed_before=copy.deepcopy(seeded), filters={})
    assert st.score(CFG, "lookup", M, state, LOOKUP)["success"]
    changed = copy.deepcopy(seeded)
    changed["rule"]["enabled"] = True
    state["seeded"] = changed
    out = st.score(CFG, "lookup", M, state, LOOKUP)
    assert "seeded rule changed" in out["failure_reason"]


@pytest.mark.parametrize("interval", ["1m", "1 minute", "60s", "every minute", "1 min"])
def test_lookup_interval_accepts_equivalent_forms(interval):
    seeded = _seeded_state()
    state = _weblogs_state(task="lookup", dashboards=[], rules=[], seeded=seeded,
                           seed_before=copy.deepcopy(seeded), filters={})
    assert st.score(CFG, "lookup", M, state, LOOKUP.replace("1m", interval))["success"]


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize(("text", "want"), [
    ("TIME_FIELD: timestamp", "timestamp"),
    ("**TIME_FIELD:** `timestamp`", "timestamp"),
    ("- TIME_FIELD: 'timestamp'.", "timestamp"),
    ("TIME_FIELD: a\nmore\nTIME_FIELD: b", "b"),
    ("no line", None),
])
def test_answers_reads_the_last_line(text, want):
    assert st.answers(text, "TIME_FIELD") == want


def test_threshold_equivalences():
    assert st.threshold_ok(_rule("x", threshold=10), 10)[0]
    r = _rule("x")
    r["params"].update(thresholdComparator=">=", threshold=[11])
    assert st.threshold_ok(r, 10)[0]
    assert not st.threshold_ok(_rule("x", threshold=11), 10)[0]
    esql = {"rule_type_id": ".es-query", "params": {
        "searchType": "esqlQuery", "threshold": [0], "thresholdComparator": ">",
        "esqlQuery": {"esql": "FROM l | WHERE x | STATS n = COUNT(*) | WHERE n > 10"}}}
    assert st.threshold_ok(esql, 10)[0]


def test_strip_time_ranges_keeps_the_filter():
    q = {"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-5m"}}},
                             {"range": {"response.keyword": {"gte": "500"}}}]}}
    out = st.strip_time_ranges(q, {"@timestamp", "timestamp"})
    assert out == {"bool": {"filter": [{"range": {"response.keyword": {"gte": "500"}}}]}}


def test_esql_body_counts_before_stats_on_the_target_index():
    esql = "FROM kibana_sample_data_logs | WHERE response LIKE \"5*\" | STATS n = COUNT(*) | WHERE n > 10"
    assert st._esql_body(esql, "probe") == 'FROM probe | WHERE response LIKE "5*" | STATS c = COUNT(*)'


def test_resolve_field_follows_aliases():
    props = {"@timestamp": {"type": "date"}, "timestamp": {"type": "alias", "path": "@timestamp"},
             "response": {"type": "text", "fields": {"keyword": {"type": "keyword"}}}}
    assert st.resolve_field(props, "timestamp") == "@timestamp"
    assert st.resolve_field(props, "response.keyword") == "response.keyword"
    assert st.resolve_field(props, "nope") is None
    assert st.date_fields(props) == {"@timestamp", "timestamp"}


def test_span_attributes_carry_every_check():
    state = _weblogs_state(rules=[_rule(f"{M} 5xx errors", time_field="utc_time")])
    out = st.score(CFG, "weblogs", M, state, OK_TEXT)
    attrs = st.span_attributes(out)
    assert attrs["study.task"] == "weblogs"
    assert attrs["study.checks_total"] == len(out["criteria"])
    assert attrs["study.checks_passed"] == len(out["criteria"]) - 1
    assert attrs["study.check.rule.rule.time_field.ok"] is False
    assert attrs["study.check.rule.rule.time_field.actual"] == "utc_time"
    assert attrs["study.check.describe.answer_TIME_FIELD.expected"] == "timestamp"
    assert all(isinstance(v, (str, int, float, bool)) for v in attrs.values())
    assert all(len(v) <= st.SNIPPET for v in attrs.values() if isinstance(v, str))
    json.dumps(attrs)
