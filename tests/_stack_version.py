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
exits 0; otherwise it prints one line naming the fix and exits 1. The gate
certifies the seeded local stack (D56): the suites let shell env win over
.env.seed (tests/_stack_env.py), so a shell KIBANA_URL or KIBANA_TEST_API_KEY
that differs from the seed (an empty one included), in any letter case, or
either name in .env.local, is refused before any call, naming `unset
KIBANA_URL KIBANA_TEST_API_KEY`; so is a UV_*, PYTHON*, LD_*, KIBANA_MCP_SERVER_BIN or
KIBANA_MCP_SERVER_IMAGE line in .env.local (it would change what the replay runs), and a KIBANA_API_KEY,
KIBANA_PUBLIC_URL or KIBANA_MCP_PUBLIC_KIBANA_URL in the shell or .env.local,
which the replayed server would read ahead of the seed. A loader-parsed helper, never a
test module.
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


_CREDS = ("KIBANA_URL", "KIBANA_TEST_API_KEY")
# What the replay servers (e2e_replay_green, wheel_replay_green) read ahead of
# the seed: they inherit the suite's environment, .env.local merged in, and
# Settings.load bridges the seed's key to KIBANA_API_KEY only by setdefault, so
# any of these, set at all, replaces the seed's key or link base inside the
# server under test. The seed holds none of them. (KIBANA_MCP_API_KEY and
# KIBANA_MCP_KIBANA_URL lose to the seed-filled first alias: measured.)
# pydantic-settings reads names case-insensitively (config.Settings sets no
# case_sensitive), so any spelling counts: `kibana_public_url` moves the links
# as KIBANA_PUBLIC_URL does (measured with Settings.load).
_SERVER_SETTINGS = ("KIBANA_API_KEY", "KIBANA_PUBLIC_URL", "KIBANA_MCP_PUBLIC_KIBANA_URL")


# .env.local is merged into the suites' environment after the gate clears the
# shell, and the replay servers start through `uv run` (or the binary named in
# KIBANA_MCP_SERVER_BIN): a UV_ENV_FILE there loads another key into the server,
# a PYTHON* path or an LD_PRELOAD can load other code, and KIBANA_MCP_SERVER_BIN replays an
# installed server instead of the checkout (all measured by the H4 review);
# KIBANA_MCP_SERVER_IMAGE replays a Docker image instead (D62).
# .env.local holds the LM Studio settings; none of these belong there.
def _runner_settings_in(values: Mapping[str, str]) -> list[str]:
    """The .env.local names that change what the suites' subprocesses run or load."""
    return sorted(
        k for k in values
        if k.upper() in ("KIBANA_MCP_SERVER_BIN", "KIBANA_MCP_SERVER_IMAGE")
        or k.upper().startswith(("UV_", "PYTHON", "LD_"))
    )


def _server_settings_in(values: Mapping[str, str]) -> list[str]:
    """The names in values that config.Settings reads as a server setting, as spelled."""
    return sorted(k for k in values if k.upper() in _SERVER_SETTINGS)


def shell_divergence(
    environ: Mapping[str, str], seed: Mapping[str, str], local: Mapping[str, str] | None = None
) -> str | None:
    """None when nothing in the shell or .env.local moves the suites off .env.seed, else the refusal.

    The suites let shell env win (tests/_stack_env.py), so a differing value
    would aim the gate's mutating suites at another Kibana, and a server
    setting would re-credential the replayed server. Values are never
    printed: some of them are keys.
    """
    local = local or {}
    # Settings and the suites' loader read these in any letter case too, so a
    # lowercase `kibana_url` (shell or .env.local) aims the replayed server at
    # another Kibana with the seed's key (measured with Settings.load).
    differ = sorted(
        k for k in environ if k.upper() in _CREDS and environ[k] != seed.get(k.upper(), "")
    )
    shell_server = _server_settings_in(environ)
    local_server = sorted(
        {k for k in _server_settings_in(local) if k not in environ}
        | {k for k in local if k.upper() in _CREDS}
        | set(_runner_settings_in(local))
    )
    if not (differ or shell_server or local_server):
        return None
    parts = []
    if differ:
        parts.append(f"the shell sets {' and '.join(differ)} to a value that differs from {_SEED}")
    if shell_server:
        parts.append(f"the shell sets {' and '.join(shell_server)}")
    if local_server:
        parts.append(f".env.local sets {' and '.join(local_server)}")
    extra = [k for k in differ if k not in _CREDS]
    fix = f"unset {' '.join((*_CREDS, *extra, *shell_server))}"
    if local_server:
        fix += f", remove {' and '.join(local_server)} from .env.local"
    return (
        f"{'; '.join(parts)}, and the suites or the server under test would use it — "
        f"the gate certifies the seeded local stack: {fix}, then re-run make dod"
    )


def mismatch(actual: str, expected: str, source: str, environ: Mapping[str, str]) -> str | None:
    """None when the running stack is the certified line, else the refusal text."""
    if actual == expected:
        return None
    override = environ.get("ES_LOCAL_VERSION", "").strip()
    if override and override != expected and not _in_ci(environ):
        fix = f"unset ES_LOCAL_VERSION, then {REPROVISION}"
    else:
        fix = REPROVISION
    return (
        f"the seeded stack runs Kibana {actual}, but the gate certifies {expected} "
        f"({source}) — re-provision it: {fix}"
    )


def running_version(url: str, api_key: str) -> str:
    """The `version.number` Kibana's /api/status reports, read with this key."""
    import kibana

    try:
        body = kibana.Kibana(url, api_key=api_key).status.get_status().body
    except Exception as e:  # any transport or auth failure is a NO-GO naming the fix
        raise StackVersionError(
            f"cannot read Kibana's version from {url}/api/status ({type(e).__name__}: {e}) "
            f"— start and seed the stack: {START}"
        ) from e
    number = (body.get("version") or {}).get("number") if isinstance(body, dict) else None
    if not number:
        # Kibana answers /api/status without `version` to a caller it does not
        # authenticate, so a missing number almost always means a bad key.
        raise StackVersionError(
            f"{url}/api/status reported no version.number — the key is invalid or "
            f"revoked (Kibana hides the version from unauthenticated callers): "
            "scripts/stack.sh seed"
        )
    return str(number)


def check(root: Path, environ: Mapping[str, str], supported: Iterable[str]) -> str:
    """The running version when it is the certified line; StackVersionError otherwise."""
    supported = tuple(supported)
    seed = _stack_env._parse(root / _SEED)
    if seed is None:
        raise StackVersionError(f"no {_SEED} — run: {START}")
    for key in _CREDS:
        if not seed.get(key):
            raise StackVersionError(f"{key} is empty or missing in {_SEED} — re-run: scripts/stack.sh seed")
    refusal = shell_divergence(environ, seed, _stack_env._parse(root / ".env.local"))
    if refusal:
        raise StackVersionError(refusal)
    expected, source = expected_version(environ, pinned_version(root), supported)
    actual = running_version(seed["KIBANA_URL"].rstrip("/"), seed["KIBANA_TEST_API_KEY"])
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
