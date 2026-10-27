"""The per-toolbox live-model tasks: one task for each toolbox no model had
been offered before 2026-10-27, and one destructive-tier precision task.

Pre-registered in scripts/experiment/CLAUDE_E2E.md (block "toolboxes",
2026-10-27, D61) before any counted run, beside the four tasks in
tests/_live_tasks.py. They run in `make test-e2e` only (the live-model tier is
on demand since D61, outside the Definition-of-Done gate). A passing run of
each write-tier task was converted into a replay transcript
(tests/e2e_replay/record_claude_turn.py), and the replay tier scores the
replayed turn with the SAME `score` below, so the two tiers cannot drift in
what "done" means.

Every task is fixed by: its toolboxes and tier (the server's whole surface for
the run), a prompt built from the run marker `E2E <8 hex>`, an optional seed
(state the harness creates first), a ladder scored from Kibana's state after
the run, and a cleanup that deletes only what carries the marker or was
seeded. Two rung kinds exist: state rungs, read from Kibana, and run rungs,
read from the run's own record (which tools were called, with what input,
and the final answer). The replay tier has no run record, so it scores state
rungs only (`run=None`); a task whose every rung is a run rung
(`kibana_status`) then has nothing more to check than the replayed calls.

tests/unit/test_toolbox_tasks.py pins every prompt byte for byte and every
scoring branch against fakes. A loader-parsed helper module, never a test
module.
"""

import contextlib

from kibana_mcp.core.dashboards.compose import build_dashboard_data
from kibana_mcp.core.errors import KibanaNotFound

STREAM_PARENT = "logs.ecs"


def marker_hex(marker):
    """The 8 hex digits of a run marker (`E2E <hex>`): the part of every
    derived name (stream, parameter key) that is fresh per run."""
    return marker.rsplit(" ", 1)[-1]


def _ladder(rungs, order):
    """`first_missing` and `passed` over the rungs that were scored; a rung
    set to None was not scored (a run rung during a replay)."""
    scored = [r for r in order if rungs[r] is not None]
    first_missing = next((r for r in scored if not rungs[r]), None)
    return {**rungs, "first_missing": first_missing, "passed": first_missing is None}


def _calls(run):
    return list((run or {}).get("tool_calls") or [])


class ToolboxTask:
    """One pre-registered task. Subclasses fill in the class attributes and
    the prompt, score and cleanup methods."""

    name = ""
    toolbox = ""  # the toolbox the task covers
    toolboxes = ""  # KIBANA_MCP_TOOLBOXES for the run
    tier = "write"
    rungs: tuple[str, ...] = ()
    run_rungs: frozenset[str] = frozenset()  # rungs read from the run record
    replayed = True  # a passing run was recorded as a replay transcript

    def prompt(self, marker):
        raise NotImplementedError

    def seed(self, gateway, marker):
        """Create the state the task starts from; returns what score and
        cleanup need (JSON-serialisable, written into the run record)."""
        return {}

    def score(self, gateway, marker, seeded, run=None):
        raise NotImplementedError

    def cleanup(self, gateway, marker, seeded):
        raise NotImplementedError

    def _finish(self, rungs, run):
        if run is None:
            rungs = {r: (None if r in self.run_rungs else v) for r, v in rungs.items()}
        return _ladder(rungs, self.rungs)


# --- cases ---------------------------------------------------------------


class CaseComment(ToolboxTask):
    name = "case_comment"
    toolbox = toolboxes = "cases"
    rungs = ("s1_case", "s2_comment")
    COMMENT = "Checked the flight delay dashboard."

    def title(self, marker):
        return f"{marker} delayed flights"

    def prompt(self, marker):
        return (
            f"Using the kibana tools, open a new case titled '{self.title(marker)}' "
            "with the description 'Flights in the sample data are arriving late.', "
            f"then add the comment '{self.COMMENT}' to that case."
        )

    def _cases(self, gateway, marker):
        return [c for c in gateway.list_cases(marker) if marker in c.title]

    def score(self, gateway, marker, seeded, run=None):
        rungs = dict.fromkeys(self.rungs, False)
        with contextlib.suppress(Exception):
            cases = [c for c in self._cases(gateway, marker) if c.title == self.title(marker)]
            rungs["s1_case"] = len(cases) == 1
            if rungs["s1_case"]:
                # Kibana's raw comments, not the gateway mapping the model reads.
                body = gateway._client.cases.get_comments(case_id=cases[0].id).body
                comments = body if isinstance(body, list) else body.get("comments", [])
                rungs["s2_comment"] = any(
                    c.get("type") == "user" and c.get("comment") == self.COMMENT
                    for c in comments
                )
        return self._finish(rungs, run)

    def cleanup(self, gateway, marker, seeded):
        for case in self._cases(gateway, marker):
            gateway.delete_case(case.id)


# --- security-detections -------------------------------------------------


class ExceptionItem(ToolboxTask):
    name = "exception_item"
    toolbox = toolboxes = "security-detections"
    rungs = ("s1_list", "s2_item")
    ENTRY = {"field": "host.name", "operator": "included", "value": "build-01"}

    def list_name(self, marker):
        return f"{marker} trusted hosts"

    def item_name(self, marker):
        return f"{marker} build server"

    def prompt(self, marker):
        return (
            f"Using the kibana tools, create a detection exception list named "
            f"'{self.list_name(marker)}' with the description 'Hosts the e2e test "
            f"trusts.', then add an exception item named '{self.item_name(marker)}' "
            "with the description 'The CI build host.' to that list, matching when "
            "the field 'host.name' is 'build-01'."
        )

    def _lists(self, gateway, marker):
        return [x for x in gateway.find_exception_lists() if marker in x.name]

    def score(self, gateway, marker, seeded, run=None):
        rungs = dict.fromkeys(self.rungs, False)
        with contextlib.suppress(Exception):
            lists = [
                x for x in self._lists(gateway, marker)
                if x.name == self.list_name(marker) and x.type == "detection"
            ]
            rungs["s1_list"] = len(lists) == 1
            if rungs["s1_list"]:
                body = gateway._client.exception_lists.find_items(
                    list_id=lists[0].list_id, namespace_type=lists[0].namespace_type,
                    page=1, per_page=100,
                ).body
                rungs["s2_item"] = any(
                    item.get("name") == self.item_name(marker)
                    and [
                        {k: e.get(k) for k in ("field", "operator", "value")}
                        for e in item.get("entries") or []
                    ] == [self.ENTRY]
                    for item in body.get("data", [])
                )
        return self._finish(rungs, run)

    def cleanup(self, gateway, marker, seeded):
        # Deleting a list deletes its items.
        for x in self._lists(gateway, marker):
            gateway.delete_exception_list(None, x.list_id, x.namespace_type)


# --- observability -------------------------------------------------------


class SyntheticParam(ToolboxTask):
    name = "synthetic_param"
    toolbox = toolboxes = "observability"
    rungs = ("s1_param", "s2_description")

    def key(self, marker):
        return f"e2e_{marker_hex(marker)}_env"

    def prompt(self, marker):
        return (
            f"Using the kibana tools, create a Synthetics global parameter with the "
            f"key '{self.key(marker)}', the value 'staging' and the description "
            f"'{marker} staging', then change that parameter's description to "
            f"'{marker} production'."
        )

    def _params(self, gateway, marker):
        return [p for p in gateway.list_synthetic_params() if p.key == self.key(marker)]

    def score(self, gateway, marker, seeded, run=None):
        rungs = dict.fromkeys(self.rungs, False)
        with contextlib.suppress(Exception):
            params = self._params(gateway, marker)
            rungs["s1_param"] = len(params) == 1
            rungs["s2_description"] = (
                rungs["s1_param"] and params[0].description == f"{marker} production"
            )
        return self._finish(rungs, run)

    def cleanup(self, gateway, marker, seeded):
        for p in self._params(gateway, marker):
            gateway.delete_synthetic_param(p.id)


# --- streams -------------------------------------------------------------


class StreamFork(ToolboxTask):
    name = "stream_fork"
    toolbox = toolboxes = "streams"
    rungs = ("s1_child", "s2_routing")

    def child(self, marker):
        return f"{STREAM_PARENT}.e2e{marker_hex(marker)}"

    def value(self, marker):
        return f"e2e-{marker_hex(marker)}"

    def prompt(self, marker):
        return (
            f"Using the kibana tools, fork the wired stream '{STREAM_PARENT}' into a "
            f"new child stream named '{self.child(marker)}' that receives the "
            f"documents whose field 'service.name' equals '{self.value(marker)}'."
        )

    def score(self, gateway, marker, seeded, run=None):
        rungs = dict.fromkeys(self.rungs, False)
        with contextlib.suppress(Exception):
            gateway.get_stream(self.child(marker))
            rungs["s1_child"] = True
            body = gateway._client.streams.get(name=STREAM_PARENT).body
            routing = body["stream"]["ingest"]["wired"]["routing"]
            rungs["s2_routing"] = any(
                r.get("destination") == self.child(marker)
                and r.get("where") == {"field": "service.name", "eq": self.value(marker)}
                for r in routing
            )
        return self._finish(rungs, run)

    def cleanup(self, gateway, marker, seeded):
        with contextlib.suppress(KibanaNotFound):
            gateway.delete_stream(self.child(marker), force=True)


# --- fleet ---------------------------------------------------------------


class AgentPolicy(ToolboxTask):
    name = "agent_policy"
    toolbox = toolboxes = "fleet"
    rungs = ("s1_policy", "s2_description")
    DESCRIPTION = "Edge hosts in the lab."

    def policy_name(self, marker):
        return f"{marker} edge hosts"

    def prompt(self, marker):
        return (
            f"Using the kibana tools, create a Fleet agent policy named "
            f"'{self.policy_name(marker)}' in the namespace 'default' with the "
            f"description 'Edge hosts.', then change that policy's description to "
            f"'{self.DESCRIPTION}'"
        )

    def _policies(self, gateway, marker):
        return [p for p in gateway.list_agent_policies() if marker in p.name]

    def score(self, gateway, marker, seeded, run=None):
        rungs = dict.fromkeys(self.rungs, False)
        with contextlib.suppress(Exception):
            policies = [
                p for p in self._policies(gateway, marker)
                if p.name == self.policy_name(marker)
            ]
            rungs["s1_policy"] = len(policies) == 1
            if rungs["s1_policy"]:
                raw = gateway._client.fleet_policies.get_agent_policy(
                    agent_policy_id=policies[0].id).body["item"]
                rungs["s2_description"] = (
                    raw.get("description") == self.DESCRIPTION
                    and raw.get("namespace") == "default"
                )
        return self._finish(rungs, run)

    def cleanup(self, gateway, marker, seeded):
        for p in self._policies(gateway, marker):
            gateway.delete_agent_policy(p.id, force=True)


# --- platform-health -----------------------------------------------------


class KibanaStatus(ToolboxTask):
    """A read-only toolbox leaves no state to score, so both rungs are read
    from the run: the status tool was called, and the answer names the
    version Kibana itself reports (read by the harness, not by the model)."""

    name = "kibana_status"
    toolbox = toolboxes = "platform-health"
    rungs = ("s1_status_call", "s2_version")
    run_rungs = frozenset(rungs)

    def prompt(self, marker):
        return (
            "Using the kibana tools, find out which version of Kibana this is and "
            "its overall status level. Reply with the version number and the level."
        )

    def score(self, gateway, marker, seeded, run=None):
        rungs = dict.fromkeys(self.rungs, False)
        if run is not None:
            rungs["s1_status_call"] = "get_kibana_status" in _calls(run)
            with contextlib.suppress(Exception):
                version = gateway.get_kibana_status().version
                rungs["s2_version"] = bool(version) and version in (run.get("final_text") or "")
        return self._finish(rungs, run)

    def cleanup(self, gateway, marker, seeded):
        pass  # read-only: nothing was created


# --- destructive precision (dashboards at the destructive tier) ----------


def restore_flights_sample(gateway):
    """Reinstall the flights sample (what `scripts/stack.sh seed` loads):
    Kibana overwrites its saved objects, so a deleted sample dashboard comes
    back under its own id. Same call and headers as the seed; the root
    gateway's key is the seed's superuser key."""
    gateway._client.perform_request(
        "POST", "/api/sample_data/flights",
        headers={"kbn-xsrf": "true", "x-elastic-internal-origin": "Kibana"},
    )


class DeleteOneDashboard(ToolboxTask):
    """The one destructive-tier task (surface-2): two marker dashboards are
    seeded, the model must delete exactly one of them by its id. Not replayed:
    the replay tier never runs above `write`.

    The run's server holds the superuser key at `destructive` in the default
    space of the shared dev stack, beside the flights sample the contract
    tier reads, so the seed records the dashboards that were there first and
    cleanup brings back any the run deleted: it reinstalls the flights sample,
    and if a dashboard is still missing it raises naming them and the fix
    (`scripts/stack.sh seed` restores only the sample; anything else must be
    recreated by hand)."""

    name = "delete_one_dashboard"
    toolbox = toolboxes = "dashboards"
    tier = "destructive"
    rungs = ("s1_removed", "s2_kept", "s3_one_destructive_call", "s4_target_id")
    run_rungs = frozenset({"s3_one_destructive_call", "s4_target_id"})
    replayed = False

    def keep_title(self, marker):
        return f"{marker} flights keep"

    def remove_title(self, marker):
        return f"{marker} flights remove"

    def prompt(self, marker):
        return (
            f"Using the kibana tools, delete the dashboard titled "
            f"'{self.remove_title(marker)}'. Do not delete or change anything else."
        )

    def seed(self, gateway, marker):
        before = sorted(d.id for d in gateway.search_dashboards(None))
        ids = {}
        for role, title in (("keep", self.keep_title(marker)), ("remove", self.remove_title(marker))):
            ids[role] = gateway.create_dashboard(
                build_dashboard_data(title, "seeded by the e2e precision task", [], None)
            )
        return {"seeded_dashboards": ids, "dashboards_before": before}

    def _exists(self, gateway, dashboard_id):
        try:
            gateway.get_dashboard(dashboard_id)
        except KibanaNotFound:
            return False
        return True

    def score(self, gateway, marker, seeded, run=None):
        from tests._registry import registry  # FakeGateway build: no I/O

        ids = seeded["seeded_dashboards"]
        rungs = dict.fromkeys(self.rungs, False)
        with contextlib.suppress(Exception):
            rungs["s1_removed"] = not self._exists(gateway, ids["remove"])
            rungs["s2_kept"] = self._exists(gateway, ids["keep"])
        if run is not None:
            tools = registry()
            destructive = [c for c in _calls(run) if c in tools and tools[c].tier == "destructive"]
            rungs["s3_one_destructive_call"] = destructive == ["delete_dashboard"]
            deletes = [
                i.get("input") or {} for i in run.get("tool_inputs") or []
                if i.get("name") == "delete_dashboard"
            ]
            rungs["s4_target_id"] = [d.get("dashboard_id") for d in deletes] == [ids["remove"]]
        return self._finish(rungs, run)

    def cleanup(self, gateway, marker, seeded):
        for dashboard_id in (seeded or {}).get("seeded_dashboards", {}).values():
            with contextlib.suppress(KibanaNotFound):
                gateway.delete_dashboard(dashboard_id)
        for d in gateway.search_dashboards(marker):
            if marker in d.title:
                gateway.delete_dashboard(d.id)
        before = set((seeded or {}).get("dashboards_before") or ())
        lost = before - {d.id for d in gateway.search_dashboards(None)}
        if lost:
            restore_flights_sample(gateway)
            lost -= {d.id for d in gateway.search_dashboards(None)}
        if lost:
            raise RuntimeError(
                f"the run deleted dashboards that were on the stack before it: "
                f"{', '.join(sorted(lost))}; reinstalling the flights sample did not "
                "bring them back — recreate them, or re-provision the dev stack: "
                "scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed"
            )


TOOLBOX_TASKS = {
    t.name: t
    for t in (
        CaseComment(), ExceptionItem(), SyntheticParam(), StreamFork(), AgentPolicy(),
        KibanaStatus(), DeleteOneDashboard(),
    )
}
