"""The DoD gate's stack-version check (D53): is the seeded stack the line the gate certifies?

The seeded-stack criteria (contract_green, e2e_replay_green, wheel_replay_green,
e2e_green) run on whatever Kibana elastic-start-local/.env.seed points at. Before
any of them runs, scripts/checks/definition-of-done.sh calls
`uv run python -m tests._stack_version`, which reads that Kibana's version from
`/api/status` with the seed key and compares it with the version the gate
certifies:

- locally, the `ES_LOCAL_VERSION` pin in elastic-start-local/.env.example — the
  newest version kibana-py supports (D30, tests/unit/test_stack_pin.py). A shell
  `ES_LOCAL_VERSION` override does not move it: the local gate certifies the
  newest line, and a stack left on another one is a NO-GO naming the fix;
- in CI (`GITHUB_ACTIONS=true`), the matrix's `ES_LOCAL_VERSION`, which must
  be in `kibana.SUPPORTED_VERSIONS`.

On a match it prints the version (one line, for the criteria's GO lines) and
exits 0; otherwise it prints one line naming the fix and exits 1. Credentials
resolve as the suites resolve them (tests/_stack_env.py: shell env wins over
.env.seed), so the check reads the Kibana the suites will hit. A loader-parsed
helper, never a test module.
"""

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from tests import _stack_env

_SEED = "elastic-start-local/.env.seed"
_PIN_FILE = "elastic-start-local/.env.example"
REPROVISION = "scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed"
START = "scripts/stack.sh up && scripts/stack.sh seed"


class StackVersionError(Exception):
    """A refusal whose text names the fix."""


def supported_versions() -> tuple[str, ...]:
    import kibana

    return tuple(v for _, v in kibana.SUPPORTED_VERSIONS)


def _newest(versions: Iterable[str]) -> str:
    return max(versions, key=lambda v: tuple(int(part) for part in v.split(".")))


def _in_ci(environ: Mapping[str, str]) -> bool:
    return environ.get("GITHUB_ACTIONS") == "true"


def pinned_version(root: Path) -> str:
    values = _stack_env._parse(root / _PIN_FILE) or {}
    pin = values.get("ES_LOCAL_VERSION", "").strip().strip('"')
    if not pin:
        raise StackVersionError(
            f"no ES_LOCAL_VERSION pin in {_PIN_FILE} — restore it "
            "(git checkout -- elastic-start-local/.env.example)"
        )
    return pin


def expected_version(
    environ: Mapping[str, str], pin: str, supported: Iterable[str]
) -> tuple[str, str]:
    """The version the gate certifies, and where it comes from."""
    supported = tuple(supported)
    override = environ.get("ES_LOCAL_VERSION", "").strip()
    if _in_ci(environ) and override:
        if override not in supported:
            raise StackVersionError(
                f"the CI matrix's ES_LOCAL_VERSION={override} is not a Kibana version "
                f"kibana-py supports ({', '.join(supported)}) — build the matrix from "
                "kibana.SUPPORTED_VERSIONS (integration.yml's versions job)"
            )
        return override, "the CI matrix's ES_LOCAL_VERSION"
    if pin not in supported:
        raise StackVersionError(
            f"{_PIN_FILE} pins ES_LOCAL_VERSION={pin}, which kibana-py does not support "
            f"({', '.join(supported)}) — set the pin to {_newest(supported)}, the newest (D30)"
        )
    return pin, f"the {_PIN_FILE} pin; D30: the newest supported version"


def _shell_overrides(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Credentials the shell sets: they win over .env.seed (tests/_stack_env.py)."""
    return tuple(key for key in ("KIBANA_URL", "KIBANA_TEST_API_KEY") if environ.get(key))


def _unset_first(names: Iterable[str], fix: str) -> str:
    names = tuple(names)
    return f"unset {' '.join(names)} (your shell overrides {_SEED}), then {fix}" if names else fix


def mismatch(actual: str, expected: str, source: str, environ: Mapping[str, str]) -> str | None:
    """None when the running stack is the certified line, else the refusal text."""
    if actual == expected:
        return None
    unset = list(_shell_overrides(environ))
    override = environ.get("ES_LOCAL_VERSION", "").strip()
    if override and override != expected and not _in_ci(environ):
        unset.append("ES_LOCAL_VERSION")
    fix = _unset_first(unset, REPROVISION)
    return (
        f"the seeded stack runs Kibana {actual}, but the gate certifies {expected} "
        f"({source}) — re-provision it: {fix}"
    )


def running_version(url: str, api_key: str, shell: Iterable[str] = ()) -> str:
    """The `version.number` Kibana's /api/status reports, read with this key.

    `shell` names the credentials taken from the shell, so a refusal says to
    unset them: re-provisioning the dev stack never fixes a shell override.
    """
    import kibana

    try:
        body = kibana.Kibana(url, api_key=api_key).status.get_status().body
    except Exception as e:  # any transport or auth failure is a NO-GO naming the fix
        raise StackVersionError(
            f"cannot read Kibana's version from {url}/api/status ({type(e).__name__}: {e}) "
            f"— start and seed the stack: {_unset_first(shell, START)}"
        ) from e
    number = (body.get("version") or {}).get("number") if isinstance(body, dict) else None
    if not number:
        # Kibana answers /api/status without `version` to a caller it does not
        # authenticate, so a missing number almost always means a bad key.
        raise StackVersionError(
            f"{url}/api/status reported no version.number — the key is invalid or "
            f"revoked (Kibana hides the version from unauthenticated callers): "
            f"{_unset_first(shell, 'scripts/stack.sh seed')}"
        )
    return str(number)


def check(root: Path, environ: Mapping[str, str], supported: Iterable[str]) -> str:
    """The running version when it is the certified line; StackVersionError otherwise."""
    supported = tuple(supported)
    seed = _stack_env._parse(root / _SEED)
    if seed is None:
        raise StackVersionError(f"no {_SEED} — run: {START}")
    creds = {}
    for key in ("KIBANA_URL", "KIBANA_TEST_API_KEY"):
        value = environ.get(key) or seed.get(key, "")
        if not value:
            raise StackVersionError(f"{key} is empty or missing in {_SEED} — re-run: scripts/stack.sh seed")
        creds[key] = value
    expected, source = expected_version(environ, pinned_version(root), supported)
    actual = running_version(
        creds["KIBANA_URL"].rstrip("/"), creds["KIBANA_TEST_API_KEY"], _shell_overrides(environ)
    )
    refusal = mismatch(actual, expected, source, environ)
    if refusal:
        raise StackVersionError(refusal)
    return actual


def main(
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    supported: Iterable[str] | None = None,
) -> int:
    try:
        version = check(
            root if root is not None else _stack_env._repo_root(),
            environ if environ is not None else os.environ,
            supported if supported is not None else supported_versions(),
        )
    except StackVersionError as e:
        print(" ".join(str(e).split()))
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
