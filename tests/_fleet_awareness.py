"""Which Fleet space-awareness mode a live tier certifies (D63).

uninstall_package takes a different path with Fleet space awareness on (refused
before any policy is read) than off (the package-policy listing, the Fleet Server
and monitoring guards, the forced delete). Each mode has a tier: the contract tier
runs on the seeded dev stack, where a fresh 9.5.4 has awareness on; the
fleet_unaware tier runs on scripts/fleet_unaware_ephemeral.sh, which boots Kibana
with it off and sets KIBANA_MCP_FLEET_UNAWARE=1. A test that branches on the mode
asks `mismatch()` first, so a stack in the other mode fails the test naming the
fix instead of quietly certifying the branch its tier is not there for.
"""

import os

UNAWARE_ENV = "KIBANA_MCP_FLEET_UNAWARE"


def expected_on() -> bool:
    """True where the tier certifies the awareness-on branches."""
    return os.environ.get(UNAWARE_ENV) != "1"


def mismatch(actual_on: bool) -> str | None:
    """None when the stack is in the mode this tier certifies, else the fix."""
    if actual_on == expected_on():
        return None
    if actual_on:
        return ("Fleet space awareness is on, but this run certifies the awareness-off "
                f"branches ({UNAWARE_ENV}=1): run it through scripts/fleet_unaware_ephemeral.sh, "
                "which boots Kibana with elastic-start-local/docker-compose.fleet-unaware.yml "
                f"(for the contract tier: unset {UNAWARE_ENV})")
    return ("Fleet space awareness is off on this stack, but the contract tier certifies the "
            "awareness-on branches (a fresh 9.5.4 stack has it on): re-provision with "
            "scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed; the "
            "awareness-off branches run in scripts/fleet_unaware_ephemeral.sh")
