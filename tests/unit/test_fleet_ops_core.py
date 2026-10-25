"""core/fleet_ops.py (D51): the pure checks and guidance text the fleet agent-tag,
diagnostics and package tools share between the adapter and the fake."""

from kibana_mcp.core.fleet_ops import (
    guard_reason,
    guarded_policy_refusal,
    missing_agents_message,
    monitoring_refusal,
    package_policies_refusal,
    pinned_space_refusal,
    tags_not_applied,
    unattached_guard_reason,
)
from kibana_mcp.core.models import FleetAgentTagChange


def _c(agent_id, tags):
    return FleetAgentTagChange(agent_id=agent_id, previous_tags=(), tags=tuple(tags))


def test_tags_not_applied_flags_a_missing_add_or_a_kept_remove():
    changes = [_c("ok", ["a"]), _c("lacks-add", []), _c("kept-remove", ["a", "b"])]
    assert tags_not_applied(changes, ["a"], ["b"]) == ("lacks-add", "kept-remove")


def test_tags_not_applied_is_empty_when_every_agent_matches():
    assert tags_not_applied([_c("x", ["a", "other"])], ["a"], ["gone"]) == ()


def test_missing_agents_message_singular():
    assert missing_agents_message(["nope"]) == (
        "no agent has agent id 'nope' — Kibana would skip it without an error; "
        "list_agents shows the enrolled agents' ids. Nothing was changed."
    )


def test_package_policies_refusal_counts_policies_not_pairs_and_names_no_agent_policy():
    text = package_policies_refusal("tcp", [
        ("pp-1", "a", "p1", 2, ""), ("pp-1", "a", "p2", 1, ""), ("pp-2", "b", "", 0, "")])
    assert text.startswith(
        "refusing to uninstall integration package 'tcp': it has 2 package policies — "
        "'pp-1' ('a') on agent policy 'p1' (2 agents); 'pp-1' ('a') on agent policy 'p2' "
        "(1 agent); 'pp-2' ('b') on no agent policy — ")
    one = package_policies_refusal("tcp", [("pp-1", "a", "p1", 0, "")])
    assert "it has 1 package policy — " in one


def test_guard_reason_guards_every_fleet_server_package_policy_and_only_lifecycles_otherwise():
    assert guard_reason("tcp", is_managed=True, is_default_fleet_server=False) == "managed"
    assert guard_reason("system", is_managed=False, is_default_fleet_server=True) == (
        "the default Fleet Server policy")
    assert guard_reason("fleet_server", is_managed=False, is_default_fleet_server=False).startswith(
        "a Fleet Server policy")
    assert guard_reason("tcp", is_managed=False, is_default_fleet_server=False) is None


def test_guarded_policy_refusal_names_delete_package_policy_only_where_it_applies():
    managed = guarded_policy_refusal("fleet_server", [("pp", "ap", "managed", "")])
    assert managed.endswith("This is refused regardless of force.")
    why = guard_reason("fleet_server", is_managed=False, is_default_fleet_server=False)
    plain = guarded_policy_refusal("fleet_server", [("pp", "ap", why or "", "")])
    assert plain.endswith("delete_package_policy once no Fleet Server is enrolled on that agent policy.")


def test_monitoring_refusal_counts_agent_policies():
    assert "in agent policy 'p1', " in monitoring_refusal("elastic_agent", ["p1"])
    assert "in agent policies 'p1', 'p2', " in monitoring_refusal("elastic_agent", ["p1", "p2"])


def test_unattached_guard_reason_guards_only_fleet_server():
    assert unattached_guard_reason("tcp") is None
    why = unattached_guard_reason("fleet_server")
    text = guarded_policy_refusal("fleet_server", [("pp", "", why or "", "")])
    assert "its package policy 'pp' is a fleet_server package policy on no agent policy, " in text
    assert text.endswith("delete_package_policy once no Fleet Server is enrolled on that agent policy.")


def test_guarded_policy_refusal_names_another_space():
    text = guarded_policy_refusal("tcp", [("pp", "ap", "managed", "ops")])
    assert "its package policy 'pp' in space 'ops' is on agent policy 'ap', which is managed" in text


def test_monitoring_refusal_names_another_space_and_where_to_change_it():
    here = monitoring_refusal("elastic_agent", ["p1"])
    assert "in space" not in here and here.endswith("(a managed agent policy cannot be changed).")
    there = monitoring_refusal("elastic_agent", ["p1", "p9"], {"p9": "ops"})
    assert "in agent policies 'p1', 'p9' in space 'ops', " in there
    assert "KIBANA_URL ending in '/s/<space>')." in there


def test_package_policies_refusal_names_another_space_and_where_to_delete_there():
    here = package_policies_refusal("tcp", [("pp-1", "a", "p1", 0, "")])
    assert "in space" not in here and "delete_package_policy, or pass force=True" in here
    there = package_policies_refusal("tcp", [("pp-1", "a", "p1", 0, ""), ("pp-o", "b", "po", 2, "ops")])
    assert "'pp-o' ('b') in space 'ops' on agent policy 'po' (2 agents) — " in there
    assert ("delete_package_policy (an agent or package policy in another space is changed in "
            "that space: Kibana's Fleet UI there, or this server with KIBANA_URL ending in "
            "'/s/<space>'), or pass force=True") in there


def test_pinned_space_refusal_names_the_unreadable_spaces_and_the_fix():
    text = pinned_space_refusal("tcp", "ops", ["default", "qa"])
    assert "KIBANA_URL pins space 'ops', so this server cannot read spaces 'default', 'qa'" in text
    assert "a KIBANA_URL without '/s/<space>'" in text
