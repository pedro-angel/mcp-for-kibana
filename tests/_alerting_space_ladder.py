"""Scoring for the alerting-space e2e gate: the frozen progress ladder of the
space extension's model-capability claim — the alerting twin of the Study L
ladder (tests/_space_ladder.py), frozen before any run.

Every run is scored to the first missing rung so failures carry information:

    S1 space created -> S2 .es-query rule in the space -> S3 rule enabled

plus the default-space contamination check (a marker-named rule in the default
space separates "wrong space" from "no rule").

Used by the live gate (tests/e2e/test_lmstudio_alerting_space.py) — a
loader-parsed helper, never a test module.
"""

from kibana_mcp.core.errors import KibanaNotFound

RUNGS = ("s1_space", "s2_rule", "s3_enabled")

ES_QUERY_RULE_TYPE = ".es-query"


def _marker_rules(gateway, marker):
    """Alert rules whose name carries the run marker. The gateway-side search
    is a token query (it can match on the shared 'E2E' prefix token alone),
    so the exact-substring filter binds the result to THIS run's marker."""
    return [r for r in gateway.list_alert_rules(marker) if marker in r.name]


def score_alerting_space_run(root_gateway, scoped_gateway, space_id, marker):
    """Score one run. `root_gateway` is unscoped (default space);
    `scoped_gateway` targets `space_id` and may be None when the space was
    never created (S1 already failed at connect time).

    Returns a dict with one bool per rung, `first_missing` (rung name or
    None), `default_space_contamination`, and `passed`.
    """
    rungs = dict.fromkeys(RUNGS, False)

    try:
        root_gateway.get_space(space_id)
        rungs["s1_space"] = True
    except Exception:
        pass

    if rungs["s1_space"] and scoped_gateway is not None:
        try:
            rules = [
                r for r in _marker_rules(scoped_gateway, marker)
                if r.rule_type_id == ES_QUERY_RULE_TYPE
            ]
            rungs["s2_rule"] = bool(rules)
            # Enabled counts only on a rung-2 rule: a marker-named rule of the
            # wrong type being enabled is not the pre-registered success.
            rungs["s3_enabled"] = any(r.enabled for r in rules)
        except Exception:
            pass  # an unreadable space is failed rungs, never a scorer crash

    try:
        contamination = bool(_marker_rules(root_gateway, marker))
    except Exception:
        contamination = False

    first_missing = next((r for r in RUNGS if not rungs[r]), None)
    return {
        **rungs,
        "first_missing": first_missing,
        "default_space_contamination": contamination,
        "passed": first_missing is None,
    }


def sweep_default_space_marker_rules(root_gateway, marker):
    """Delete marker-named alert rules from the DEFAULT space — wrong-space
    residue (a model that drops the `space` parameter creates its rule in
    default, and an enabled stray keeps executing on the shared stack
    forever). Marker-bound, so the sweep deletes exactly what this run
    created. The run's own space needs no per-rule sweep: the gate's
    delete_space(force=True) removes the space with everything inside it."""
    for rule in _marker_rules(root_gateway, marker):
        try:
            root_gateway.delete_alert_rule(rule.id)
        except KibanaNotFound:
            pass  # already gone — the goal state holds
