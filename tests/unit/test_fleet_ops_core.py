"""core/fleet_ops.py (D51): the pure checks and guidance text the fleet agent-tag,
diagnostics and package tools share between the adapter and the fake."""

from kibana_mcp.core.fleet_ops import (
    checks_hidden_spaces,
    guard_reason,
    guarded_policy_refusal,
    hidden_spaces_refusal,
    missing_agents_message,
    monitoring_refusal,
    package_policies_refusal,
    pinned_space_refusal,
    space_awareness_refusal,
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
        ("pp-1", "a", "", "p1", 2), ("pp-1", "a", "", "p2", 1), ("pp-2", "b", "", "", 0)],
        [], "default")
    assert text.startswith(
        "refusing to uninstall integration package 'tcp': it is still used by 2 package "
        "policies — 'pp-1' ('a') on agent policy 'p1' (2 agents); 'pp-1' ('a') on agent policy "
        "'p2' (1 agent); 'pp-2' ('b') on no agent policy — ")
    assert "Agent polic" not in text  # nothing would be left empty
    one = package_policies_refusal("tcp", [("pp-1", "a", "", "p1", 0)], [], "default")
    assert "it is still used by 1 package policy — " in one
    assert "force" not in text + one  # D64: there is no forced uninstall


def test_package_policies_refusal_names_the_space_and_the_emptied_agent_policies():
    text = package_policies_refusal("tcp", [
        ("pp-1", "a", "default", "p1", 0), ("pp-2", "b", "ops", "p2", 1)],
        [("default", "p1"), ("ops", "p2")], "default")
    assert "'pp-1' ('a') on agent policy 'p1' in space 'default' (0 agents)" in text
    assert ("remove those in 'ops' in Kibana's Fleet UI in that space") in text
    assert text.endswith(
        "Agent policies 'p1' (space 'default'), 'p2' (space 'ops') would each be left with no "
        "integration: delete_agent_policy removes it if it is no longer wanted.")


def test_checks_hidden_spaces_from_9_4_8_and_9_5_5_on():
    for version in ("9.4.8", "9.4.12", "9.5.5", "9.5.6", "9.6.0", "9.6.0-SNAPSHOT", "10.0.0"):
        assert checks_hidden_spaces(version), version
    for version in ("9.4.7", "9.5.4", "9.3.9", "8.19.5", "", "9.5", "x.y.z"):
        assert not checks_hidden_spaces(version), version


def test_hidden_space_and_pinned_refusals_name_the_fix():
    hidden = hidden_spaces_refusal("tcp")
    assert "Kibana answered 403" in hidden and "Nothing was changed." in hidden
    assert "call uninstall_package again" in hidden
    pinned = pinned_space_refusal("tcp", "ops", ["default"])
    assert "pinned to space 'ops'" in pinned and "also access space 'default'" in pinned
    assert "root URL (no '/s/<space>')" in pinned


def test_guard_reason_guards_every_fleet_server_package_policy_and_only_lifecycles_otherwise():
    assert guard_reason("tcp", is_managed=True, is_default_fleet_server=False) == "managed"
    assert guard_reason("system", is_managed=False, is_default_fleet_server=True) == (
        "the default Fleet Server policy")
    assert guard_reason("fleet_server", is_managed=False, is_default_fleet_server=False).startswith(
        "a Fleet Server policy")
    assert guard_reason("tcp", is_managed=False, is_default_fleet_server=False) is None


def test_guarded_policy_refusal_names_delete_package_policy_only_where_it_applies():
    managed = guarded_policy_refusal("fleet_server", [("pp", "ap", "managed")])
    assert managed.endswith("never removes an integration from such an agent policy. Nothing "
                            "was changed.")
    why = guard_reason("fleet_server", is_managed=False, is_default_fleet_server=False)
    plain = guarded_policy_refusal("fleet_server", [("pp", "ap", why or "")])
    assert plain.endswith("delete_package_policy once no Fleet Server is enrolled on that agent policy.")


def test_monitoring_refusal_counts_agent_policies():
    assert "in agent policy 'p1', " in monitoring_refusal("elastic_agent", ["p1"])
    assert "in agent policies 'p1', 'p2', " in monitoring_refusal("elastic_agent", ["p1", "p2"])


def test_unattached_guard_reason_guards_only_fleet_server():
    assert unattached_guard_reason("tcp") is None
    why = unattached_guard_reason("fleet_server")
    text = guarded_policy_refusal("fleet_server", [("pp", "", why or "")])
    assert "its package policy 'pp' is a fleet_server package policy on no agent policy, " in text
    assert text.endswith("delete_package_policy once no Fleet Server is enrolled on that agent policy.")


def test_space_awareness_refusal_says_why_and_names_a_fix_outside_this_server():
    text = space_awareness_refusal("tcp", "9.5.4")
    assert text.startswith(
        "refusing to uninstall integration package 'tcp': Fleet space awareness is on, and "
        "Kibana 9.5.4 uninstalls a package's package policies in every space, including spaces "
        "this API key cannot access")
    assert "Nothing was changed. Upgrade Kibana to 9.5.5 or later (9.4.8 or later on 9.4)" in text
    assert text.endswith(
        "uninstall it in Kibana's Integrations UI ('tcp' → Settings) as a user whose role "
        'grants Fleet and Integrations in all spaces (spaces ["*"]).')
    assert "force" not in text
    unread = space_awareness_refusal("tcp", "")
    assert "this API key cannot read Kibana's version" in unread
    assert "Add the `monitor` cluster privilege to the API key's role" in unread
