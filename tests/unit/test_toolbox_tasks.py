"""Unit coverage for the per-toolbox live-model tasks (tests/_toolbox_tasks.py).

The prompts are pre-registered (scripts/experiment/CLAUDE_E2E.md, block
"toolboxes", D61), so their bytes are pinned here. The scorers run only in the
live and replay tiers, where a broken branch would silently mis-classify a
run, so every rung is pinned against stubs: each one passing, each one
failing, and the replay mode (`run=None`) that scores state rungs only.
"""

from types import SimpleNamespace as NS

import pytest

from kibana_mcp.core.errors import KibanaNotFound
from tests import _toolbox_tasks as tt

MARKER = "E2E cafe0123"
TASKS = tt.TOOLBOX_TASKS


def test_task_set_is_the_registered_block():
    assert list(TASKS) == [
        "case_comment", "exception_item", "synthetic_param", "stream_fork",
        "agent_policy", "kibana_status", "delete_one_dashboard",
    ]
    assert {t.toolbox for t in TASKS.values()} == {
        "cases", "security-detections", "observability", "streams", "fleet",
        "platform-health", "dashboards",
    }
    assert [n for n, t in TASKS.items() if t.tier != "write"] == ["delete_one_dashboard"]
    assert [n for n, t in TASKS.items() if not t.replayed] == ["delete_one_dashboard"]


def test_prompts_are_the_registered_texts():
    assert TASKS["case_comment"].prompt(MARKER) == (
        "Using the kibana tools, open a new case titled 'E2E cafe0123 delayed flights' "
        "with the description 'Flights in the sample data are arriving late.', "
        "then add the comment 'Checked the flight delay dashboard.' to that case."
    )
    assert TASKS["exception_item"].prompt(MARKER) == (
        "Using the kibana tools, create a detection exception list named "
        "'E2E cafe0123 trusted hosts' with the description 'Hosts the e2e test "
        "trusts.', then add an exception item named 'E2E cafe0123 build server' "
        "with the description 'The CI build host.' to that list, matching when "
        "the field 'host.name' is 'build-01'."
    )
    assert TASKS["synthetic_param"].prompt(MARKER) == (
        "Using the kibana tools, create a Synthetics global parameter with the "
        "key 'e2e_cafe0123_env', the value 'staging' and the description "
        "'E2E cafe0123 staging', then change that parameter's description to "
        "'E2E cafe0123 production'."
    )
    assert TASKS["stream_fork"].prompt(MARKER) == (
        "Using the kibana tools, fork the wired stream 'logs.ecs' into a "
        "new child stream named 'logs.ecs.e2ecafe0123' that receives the "
        "documents whose field 'service.name' equals 'e2e-cafe0123'."
    )
    assert TASKS["agent_policy"].prompt(MARKER) == (
        "Using the kibana tools, create a Fleet agent policy named "
        "'E2E cafe0123 edge hosts' in the namespace 'default' with the "
        "description 'Edge hosts.', then change that policy's description to "
        "'Edge hosts in the lab.'"
    )
    assert TASKS["kibana_status"].prompt(MARKER) == (
        "Using the kibana tools, find out which version of Kibana this is and "
        "its overall status level. Reply with the version number and the level."
    )
    assert TASKS["delete_one_dashboard"].prompt(MARKER) == (
        "Using the kibana tools, delete the dashboard titled "
        "'E2E cafe0123 flights remove'. Do not delete or change anything else."
    )


# --- cases ---------------------------------------------------------------


def _cases_gw(cases, comments):
    deleted = []
    return NS(
        list_cases=lambda search: [NS(id=i, title=t) for i, t in cases],
        delete_case=deleted.append,
        deleted=deleted,
        _client=NS(cases=NS(get_comments=lambda case_id: NS(body=comments.get(case_id, [])))),
    )


def test_case_comment_rungs():
    task = TASKS["case_comment"]
    title = "E2E cafe0123 delayed flights"
    good = [{"type": "user", "comment": task.COMMENT}]
    assert task.score(_cases_gw([("c1", title)], {"c1": good}), MARKER, {})["passed"]
    # A paged envelope shape is read too.
    env = {"c1": {"comments": good}}
    assert task.score(_cases_gw([("c1", title)], env), MARKER, {})["passed"]
    no_case = task.score(_cases_gw([], {}), MARKER, {})
    assert no_case["first_missing"] == "s1_case"
    two = task.score(_cases_gw([("c1", title), ("c2", title)], {}), MARKER, {})
    assert two["first_missing"] == "s1_case"
    wrong = task.score(
        _cases_gw([("c1", title)], {"c1": [{"type": "user", "comment": "other"}]}), MARKER, {})
    assert wrong["first_missing"] == "s2_comment"


def test_case_cleanup_deletes_marker_cases_only():
    gw = _cases_gw([("c1", "E2E cafe0123 delayed flights"), ("c2", "E2E other")], {})
    TASKS["case_comment"].cleanup(gw, MARKER, {})
    assert gw.deleted == ["c1"]


# --- security-detections -------------------------------------------------


def _exc_gw(lists, items):
    deleted = []
    return NS(
        find_exception_lists=lambda: [
            NS(list_id=lid, name=n, type=t, namespace_type="single") for lid, n, t in lists
        ],
        delete_exception_list=lambda id, list_id, ns: deleted.append(list_id),
        deleted=deleted,
        _client=NS(exception_lists=NS(
            find_items=lambda list_id, namespace_type, page, per_page: NS(
                body={"data": items.get(list_id, [])}))),
    )


def test_exception_item_rungs():
    task = TASKS["exception_item"]
    lst = [("l1", "E2E cafe0123 trusted hosts", "detection")]
    entry = {"field": "host.name", "operator": "included", "value": "build-01", "type": "match"}
    item = {"name": "E2E cafe0123 build server", "entries": [entry]}
    assert task.score(_exc_gw(lst, {"l1": [item]}), MARKER, {})["passed"]
    assert task.score(_exc_gw([], {}), MARKER, {})["first_missing"] == "s1_list"
    wrong_type = [("l1", "E2E cafe0123 trusted hosts", "endpoint")]
    assert task.score(_exc_gw(wrong_type, {}), MARKER, {})["first_missing"] == "s1_list"
    excluded = {**item, "entries": [{**entry, "operator": "excluded"}]}
    assert task.score(_exc_gw(lst, {"l1": [excluded]}), MARKER, {})["first_missing"] == "s2_item"
    extra = {**item, "entries": [entry, {**entry, "field": "user.name"}]}
    assert task.score(_exc_gw(lst, {"l1": [extra]}), MARKER, {})["first_missing"] == "s2_item"
    gw = _exc_gw(lst + [("l2", "E2E other", "detection")], {})
    task.cleanup(gw, MARKER, {})
    assert gw.deleted == ["l1"]


# --- observability -------------------------------------------------------


def _param_gw(params):
    deleted = []
    return NS(
        list_synthetic_params=lambda: [NS(id=i, key=k, description=d) for i, k, d in params],
        delete_synthetic_param=deleted.append,
        deleted=deleted,
    )


def test_synthetic_param_rungs():
    task = TASKS["synthetic_param"]
    key = "e2e_cafe0123_env"
    assert task.score(_param_gw([("p1", key, "E2E cafe0123 production")]), MARKER, {})["passed"]
    assert task.score(_param_gw([]), MARKER, {})["first_missing"] == "s1_param"
    stale = _param_gw([("p1", key, "E2E cafe0123 staging")])
    assert task.score(stale, MARKER, {})["first_missing"] == "s2_description"
    gw = _param_gw([("p1", key, ""), ("p2", "other", "")])
    task.cleanup(gw, MARKER, {})
    assert gw.deleted == ["p1"]


# --- streams -------------------------------------------------------------


def _stream_gw(children, routing):
    deleted = []

    def get_stream(name):
        if name not in children:
            raise KibanaNotFound(name)
        return NS(name=name)

    def delete_stream(name, force):
        if name not in children:
            raise KibanaNotFound(name)
        deleted.append((name, force))

    return NS(
        get_stream=get_stream,
        delete_stream=delete_stream,
        deleted=deleted,
        _client=NS(streams=NS(get=lambda name: NS(
            body={"stream": {"ingest": {"wired": {"routing": routing}}}}))),
    )


def test_stream_fork_rungs():
    task = TASKS["stream_fork"]
    child = "logs.ecs.e2ecafe0123"
    rule = {"destination": child, "where": {"field": "service.name", "eq": "e2e-cafe0123"}}
    assert task.score(_stream_gw({child}, [rule]), MARKER, {})["passed"]
    assert task.score(_stream_gw(set(), [rule]), MARKER, {})["first_missing"] == "s1_child"
    other = {**rule, "where": {"field": "host.name", "eq": "e2e-cafe0123"}}
    assert task.score(_stream_gw({child}, [other]), MARKER, {})["first_missing"] == "s2_routing"
    gw = _stream_gw({child}, [])
    task.cleanup(gw, MARKER, {})
    assert gw.deleted == [(child, True)]
    task.cleanup(_stream_gw(set(), []), MARKER, {})  # absent child: no error


# --- fleet ---------------------------------------------------------------


def _policy_gw(policies, raw):
    deleted = []
    return NS(
        list_agent_policies=lambda: [NS(id=i, name=n) for i, n in policies],
        delete_agent_policy=lambda pid, force: deleted.append((pid, force)),
        deleted=deleted,
        _client=NS(fleet_policies=NS(
            get_agent_policy=lambda agent_policy_id: NS(body={"item": raw[agent_policy_id]}))),
    )


def test_agent_policy_rungs():
    task = TASKS["agent_policy"]
    name = "E2E cafe0123 edge hosts"
    good = {"description": task.DESCRIPTION, "namespace": "default"}
    assert task.score(_policy_gw([("a1", name)], {"a1": good}), MARKER, {})["passed"]
    assert task.score(_policy_gw([], {}), MARKER, {})["first_missing"] == "s1_policy"
    old = {"description": "Edge hosts.", "namespace": "default"}
    assert task.score(_policy_gw([("a1", name)], {"a1": old}), MARKER, {})[
        "first_missing"] == "s2_description"
    other_ns = {**good, "namespace": "lab"}
    assert task.score(_policy_gw([("a1", name)], {"a1": other_ns}), MARKER, {})[
        "first_missing"] == "s2_description"
    gw = _policy_gw([("a1", name), ("a2", "Demo Agent")], {})
    task.cleanup(gw, MARKER, {})
    assert gw.deleted == [("a1", True)]


# --- platform-health -----------------------------------------------------


def test_kibana_status_rungs_read_the_run():
    task = TASKS["kibana_status"]
    gw = NS(get_kibana_status=lambda: NS(version="9.5.4"))
    run = {"tool_calls": ["get_kibana_status"], "final_text": "Kibana 9.5.4, available"}
    assert task.score(gw, MARKER, {}, run=run)["passed"]
    no_call = {**run, "tool_calls": []}
    assert task.score(gw, MARKER, {}, run=no_call)["first_missing"] == "s1_status_call"
    wrong = {**run, "final_text": "Kibana 9.4.7"}
    assert task.score(gw, MARKER, {}, run=wrong)["first_missing"] == "s2_version"
    # Replay mode: no run record, so no rung is scored and nothing fails.
    replay = task.score(gw, MARKER, {}, run=None)
    assert replay["s1_status_call"] is None and replay["passed"]
    task.cleanup(gw, MARKER, {})


# --- destructive precision -----------------------------------------------


def _dash_gw(existing):
    deleted = []

    def get_dashboard(i):
        if i not in existing:
            raise KibanaNotFound(i)
        return NS(id=i)

    def delete_dashboard(i):
        if i not in existing:
            raise KibanaNotFound(i)
        existing.discard(i)
        deleted.append(i)

    def search_dashboards(query):
        return [NS(id=i, title=f"{MARKER} stray") for i in existing if i.startswith("stray")]

    created = iter(["d-keep", "d-remove"])
    return NS(
        get_dashboard=get_dashboard, delete_dashboard=delete_dashboard,
        search_dashboards=search_dashboards,
        create_dashboard=lambda data: next(created), deleted=deleted,
    )


SEEDED = {"seeded_dashboards": {"keep": "d-keep", "remove": "d-remove"}}


def _run(calls, deletes):
    return {
        "tool_calls": calls,
        "tool_inputs": [{"name": "delete_dashboard", "input": {"dashboard_id": d}} for d in deletes],
    }


def test_delete_one_dashboard_seed_and_rungs():
    task = TASKS["delete_one_dashboard"]
    assert task.seed(_dash_gw(set()), MARKER) == SEEDED
    good = _run(["search_dashboards", "delete_dashboard"], ["d-remove"])
    assert task.score(_dash_gw({"d-keep"}), MARKER, SEEDED, run=good)["passed"]
    still = task.score(_dash_gw({"d-keep", "d-remove"}), MARKER, SEEDED, run=good)
    assert still["first_missing"] == "s1_removed"
    both_gone = task.score(_dash_gw(set()), MARKER, SEEDED, run=good)
    assert both_gone["first_missing"] == "s2_kept"
    extra = _run(["delete_dashboard", "delete_panel"], ["d-remove"])
    assert task.score(_dash_gw({"d-keep"}), MARKER, SEEDED, run=extra)[
        "first_missing"] == "s3_one_destructive_call"
    by_other_id = _run(["delete_dashboard"], ["d-other"])
    assert task.score(_dash_gw({"d-keep"}), MARKER, SEEDED, run=by_other_id)[
        "first_missing"] == "s4_target_id"
    # Without a run record only the state rungs count.
    replay = task.score(_dash_gw({"d-keep"}), MARKER, SEEDED, run=None)
    assert replay["passed"] and replay["s3_one_destructive_call"] is None


def test_delete_one_dashboard_cleanup():
    gw = _dash_gw({"d-keep", "d-remove", "stray1"})
    TASKS["delete_one_dashboard"].cleanup(gw, MARKER, SEEDED)
    assert sorted(gw.deleted) == ["d-keep", "d-remove", "stray1"]


@pytest.mark.parametrize("name", list(TASKS))
def test_a_scorer_never_raises_on_a_broken_gateway(name):
    """An unreadable Kibana is failed rungs, never a scorer crash."""
    def boom(*a, **k):
        raise RuntimeError("down")

    gw = NS(**{m: boom for m in (
        "list_cases", "find_exception_lists", "list_synthetic_params", "get_stream",
        "list_agent_policies", "get_kibana_status", "get_dashboard")})
    run = {"tool_calls": [], "tool_inputs": [], "final_text": ""}
    result = TASKS[name].score(gw, MARKER, SEEDED, run=run)
    assert result["passed"] is False
