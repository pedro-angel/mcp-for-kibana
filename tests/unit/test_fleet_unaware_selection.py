"""The awareness-off tier runs exactly the tests D63 names.

`fleet_unaware_ephemeral` reads GO on any non-empty, skip-free run, so a test
that lost its `fleet_unaware` mark would drop its off branch from the only live
tier that runs it with the gate still GO. The harness selects with
`pytest -m fleet_unaware`; this collects the same selection.
"""

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "tests/contract/test_fleet_contract.py::test_package_install_uninstall_live",
    "tests/contract/test_fleet_contract.py::test_uninstall_fleet_server_package_refused_live",
    "tests/contract/test_fleet_contract.py::test_uninstall_elastic_agent_refused_while_monitoring_live",
    "tests/contract/test_privilege_matrix.py::test_uninstall_package_under_each_key[default-only-all]",
    "tests/contract/test_privilege_matrix.py::test_uninstall_package_under_each_key[no-features]",
    "tests/contract/test_privilege_matrix.py::test_uninstall_package_under_each_key[read-everywhere]",
}


def test_the_fleet_unaware_selection_is_the_one_d63_names():
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", "fleet_unaware", "tests"],
        cwd=_ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    got = {line.strip() for line in proc.stdout.splitlines() if "::" in line}
    assert got == EXPECTED, (
        f"pytest -m fleet_unaware selects {sorted(got)}; D63 names {sorted(EXPECTED)} — "
        "put the fleet_unaware mark back on a test that lost it, or update D63, this "
        "list and the decision's case count together")
