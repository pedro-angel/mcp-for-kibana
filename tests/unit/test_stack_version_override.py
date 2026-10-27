"""Every stack honours ES_LOCAL_VERSION (environment-4, D62).

The dev stack always did (scripts/stack.sh, the CI contract matrix); the two
isolated stacks copied .env.ephemeral.example and sourced it, which reset an
exported ES_LOCAL_VERSION to the pin, so CI could run the destructive tiers on
the newest line only. One sourced helper (scripts/stack-version.sh) now writes
every stack's env file, and CI runs both ephemeral tiers over the supported
set. These tests run the helper as the scripts do and refuse a stack template
copied any other way.
"""

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HELPER = _ROOT / "scripts" / "stack-version.sh"
_TEMPLATES = ("elastic-start-local/.env.example", "elastic-start-local/.env.ephemeral.example")
_STACK_SCRIPTS = ("scripts/stack.sh", "scripts/ephemeral_stack.sh", "scripts/fleet_ephemeral.sh")


def _run(tmp_path, template, version):
    """Write the env file as the scripts do, source it, and report what a
    child process (docker compose) sees."""
    dst = tmp_path / "env"
    script = (
        f'. "{_HELPER}"; env_with_version "{_ROOT / template}" "{dst}" && . "{dst}" '
        '&& echo "shell=$ES_LOCAL_VERSION" && sh -c \'echo "child=$ES_LOCAL_VERSION"\''
    )
    env = {"PATH": "/usr/bin:/bin"}
    if version is not None:
        env["ES_LOCAL_VERSION"] = version
    proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)
    return proc, dst


def _pin(template):
    text = (_ROOT / template).read_text()
    return re.search(r"^ES_LOCAL_VERSION=(.*)$", text, re.M).group(1)


@pytest.mark.parametrize("template", _TEMPLATES)
def test_an_override_reaches_the_env_file_and_the_child(tmp_path, template):
    proc, dst = _run(tmp_path, template, "9.4.7")
    assert proc.returncode == 0, proc.stderr
    assert "ES_LOCAL_VERSION=9.4.7" in dst.read_text().splitlines()
    assert "child=9.4.7" in proc.stdout
    assert "environment override of the" in proc.stdout  # logged when it applies


@pytest.mark.parametrize("template", _TEMPLATES)
def test_unset_keeps_the_pin_silently(tmp_path, template):
    proc, dst = _run(tmp_path, template, None)
    assert proc.returncode == 0, proc.stderr
    assert dst.read_text() == (_ROOT / template).read_text()
    assert f"shell={_pin(template)}" in proc.stdout.splitlines()
    assert "override" not in proc.stdout


def test_a_malformed_version_fails_naming_the_shape(tmp_path):
    proc, _ = _run(tmp_path, _TEMPLATES[1], "9.4.7; rm -rf /")
    assert proc.returncode != 0
    assert "is not a version like 9.5.4" in proc.stderr


@pytest.mark.parametrize("script", _STACK_SCRIPTS)
def test_every_stack_script_writes_its_env_through_the_helper(script):
    text = (_ROOT / script).read_text()
    assert ". scripts/stack-version.sh" in text and "env_with_version " in text, (
        f"{script} does not write its stack env file through scripts/stack-version.sh: "
        "source it and call env_with_version <template> <env file>, so an "
        "ES_LOCAL_VERSION from the environment (the CI matrix) picks the Kibana line"
    )


def test_no_script_copies_a_stack_template_directly():
    offenders = [
        f"{path.relative_to(_ROOT)}:{n}"
        for path in sorted((_ROOT / "scripts").rglob("*.sh"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"\bcp\b.*\.env[\w.]*\.example", line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, (
        f"{offenders} copy a stack template with cp, which ignores ES_LOCAL_VERSION "
        "(sourcing the copy resets an exported override to the pin): use "
        "env_with_version from scripts/stack-version.sh instead"
    )
