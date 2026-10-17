"""The live-model tasks: one definition shared by both live-model tiers.

`make test-e2e` (Claude via `claude -p`, tests/e2e/test_claude_live.py) and
`make test-e2e-local` (LM Studio, tests/e2e/test_lmstudio*.py) send the same
prompts and score them with the same state-based ladders (tests/_space_ladder.py,
tests/_alerting_space_ladder.py, tests/_dashboard_assertion.py and the
rule-fix ladder below). The first three prompts are the frozen texts the LM
Studio gates have always sent, moved here unchanged; the fourth,
`fix_rule_threshold`, is new and was pre-registered with the Claude tier
(scripts/experiment/CLAUDE_E2E.md) before any counted run.
tests/unit/test_live_tasks.py pins every prompt byte for byte: a pre-registered
task does not change after its counted runs start.

Scoring reads Kibana's state after the run, never the model's text. A
loader-parsed helper module, never a test module.
"""

import json

from tests._dashboard_assertion import assert_model_built_dashboard
from tests._space_ladder import FLIGHTS_INDEX

# The server entries the tasks run against, by the LM Studio entry names
# (docs/e2e-setup.md step 5). The Claude tier writes the same toolbox sets
# into its own MCP config.
DASHBOARDS_TOOLBOXES = "dashboards,data-management,platform-admin"
ALERTING_TOOLBOXES = "alerting,platform-admin"


def flights_dashboard_prompt(marker):
    return (
        f"Using the kibana tools, create a dashboard titled '{marker} flight prices' "
        "with a bar chart of the average ticket price by carrier over the last 7 days, "
        "using the flights sample data. Make exactly one create-dashboard call, then stop."
    )


FLIGHTS_RUNGS = ("s1_dashboard", "s2_panel")


def score_flights_dashboard_run(gateway, marker):
    """The flights task's pass criterion as a ladder: a marker dashboard exists
    (S1) and assert_model_built_dashboard accepts it (S2) — the same assertion
    test_lmstudio.py makes."""
    rungs = dict.fromkeys(FLIGHTS_RUNGS, False)
    try:
        rungs["s1_dashboard"] = bool(gateway.search_dashboards(marker))
    except Exception:
        pass
    if rungs["s1_dashboard"]:
        try:
            assert_model_built_dashboard(gateway, marker)
            rungs["s2_panel"] = True
        except Exception:
            pass  # an unreadable or wrong panel is a failed rung, never a scorer crash
    first_missing = next((r for r in FLIGHTS_RUNGS if not rungs[r]), None)
    return {**rungs, "first_missing": first_missing, "passed": first_missing is None}


def space_dashboard_prompt(space_id, marker):
    # The pre-registered Study L prompt (frozen before any run) with the local
    # arm's "Using the kibana tools" opener. The chain and the index pattern are
    # stated deliberately: the study measures execution, not derivation.
    return (
        f"Using the kibana tools, create a new Kibana space with id '{space_id}'. "
        f"Inside that space, create a data view for the index pattern "
        f"'{FLIGHTS_INDEX}', then create a dashboard titled '{marker} flight "
        f"prices' with a bar chart of the average ticket price by carrier over "
        f"the last 7 days, using that data view."
    )


def alerting_space_prompt(space_id, marker):
    # Frozen in the runbook before any run. The chain, the rule type, the
    # consumer, the index, and the time field are stated deliberately: the gate
    # measures execution, not derivation.
    return (
        f"Using the kibana tools, create a new Kibana space with id '{space_id}'. "
        f"Inside that space, create an '.es-query' alert rule named "
        f"'{marker} flight watch' with consumer 'stackAlerts' that fires when "
        f"more than 0 documents match in the index '{FLIGHTS_INDEX}' (time "
        f"field 'timestamp') over the last 5 minutes, then enable that rule."
    )


# --- fix_rule_threshold: a maintenance task for the in-place update tools ---
#
# The harness seeds a disabled .es-query rule whose threshold is wrong
# (10000 where 100 is meant); the model must correct it in place. Pass means
# the SAME rule (same id) now has threshold [100] with comparator '>', and
# nothing else about it changed: name, tags, schedule, enabled state and every
# other param match the seeded rule as Kibana stored it, and no second
# marker-named rule exists.

FIX_RULE_WRONG_THRESHOLD = 10000
FIX_RULE_THRESHOLD = 100
FIX_RULE_RUNGS = ("s1_rule_kept", "s2_threshold", "s3_untouched", "s4_no_duplicate")


def fix_rule_name(marker):
    return f"{marker} late flights"


def fix_rule_threshold_prompt(marker):
    return (
        f"Using the kibana tools, fix the existing alert rule named "
        f"'{fix_rule_name(marker)}'. It should fire when more than "
        f"{FIX_RULE_THRESHOLD} documents match, but its threshold was set to "
        f"{FIX_RULE_WRONG_THRESHOLD} by mistake. Correct the threshold of that "
        "rule in place and change nothing else: do not create, delete, enable "
        "or disable any rule."
    )


def fix_rule_seed_args(marker):
    """create_alert_rule arguments for the broken rule (disabled, so it never
    executes on the shared stack)."""
    return {
        "name": fix_rule_name(marker),
        "rule_type_id": ".es-query",
        "consumer": "stackAlerts",
        "schedule_interval": "1m",
        "params": {
            "searchType": "esQuery",
            "index": [FLIGHTS_INDEX],
            "timeField": "timestamp",
            "esQuery": json.dumps({"query": {"match": {"FlightDelay": True}}}),
            "size": 100,
            "threshold": [FIX_RULE_WRONG_THRESHOLD],
            "thresholdComparator": ">",
            "timeWindowSize": 5,
            "timeWindowUnit": "m",
        },
        "tags": ["e2e"],
        "enabled": False,
    }


def rule_snapshot(body):
    """The parts of a rule (Kibana's GET body) the task must leave alone, plus
    the threshold pair it must fix."""
    params = dict(body.get("params") or {})
    return {
        "id": body.get("id"),
        "name": body.get("name"),
        "tags": sorted(body.get("tags") or []),
        "enabled": bool(body.get("enabled")),
        "schedule": body.get("schedule") or {},
        "threshold": params.pop("threshold", None),
        "thresholdComparator": params.pop("thresholdComparator", None),
        "other_params": params,
    }


def score_fix_rule_run(before, after, marker_rule_ids):
    """Score one fix_rule_threshold run.

    `before` is rule_snapshot() of the seeded rule as Kibana stored it, `after`
    the snapshot of the same id after the run (None when it is gone), and
    `marker_rule_ids` the ids of every marker-named rule in the space after the
    run. Returns one bool per rung, `first_missing`, and `passed`."""
    rungs = dict.fromkeys(FIX_RULE_RUNGS, False)
    if after is not None and after.get("id") == before["id"]:
        rungs["s1_rule_kept"] = True
        rungs["s2_threshold"] = (
            after["threshold"] == [FIX_RULE_THRESHOLD]
            and after["thresholdComparator"] == ">"
        )
        rungs["s3_untouched"] = all(
            after[k] == before[k]
            for k in ("name", "tags", "enabled", "schedule", "other_params")
        )
    rungs["s4_no_duplicate"] = set(marker_rule_ids) == {before["id"]}
    first_missing = next((r for r in FIX_RULE_RUNGS if not rungs[r]), None)
    return {**rungs, "first_missing": first_missing, "passed": first_missing is None}
