"""Load the stack env files into os.environ for contract/e2e tests.

Ownership (specs: 2026-07-11-start-local-pivot-design.md,
2026-07-12-env-layout-reorg-design.md): elastic-start-local/.env.seed is
machine-written (scripts/stack.sh seed) and holds exactly KIBANA_URL and
KIBANA_TEST_API_KEY — foldered with the rest of the stack env, kibana-py-style.
.env.local is user-owned (LMSTUDIO_* etc.) and is never written by tooling; its
non-secret defaults are documented in the committed .env.local.example. Explicit
shell env always wins (os.environ.setdefault). Called at FIXTURE time, never at conftest
import — pytest imports deselected suites' conftests during unit-run
collection (measured, not assumed), so import-time work here would couple unit
runs to stack state.
"""

import os
import re
import warnings
from pathlib import Path

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MACHINE_KEYS = ("KIBANA_URL", "KIBANA_TEST_API_KEY")
# Seed creds live foldered with the stack env (elastic-start-local/), not at root.
_SEED_REL = Path("elastic-start-local") / ".env.seed"


def _repo_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "pyproject.toml").is_file():
        # A silently-wrong root loads nothing while everything still looks
        # plausible — the failure is invisible, so refuse loudly instead.
        raise RuntimeError(f"stack-env loader resolved a non-repo root: {root}")
    return root


def _parse(path: Path) -> dict[str, str] | None:
    """Parse KEY=value lines; None means the file does not exist.

    Whole-line strip only (CRLF-safe); blank and `#` lines skipped; lines
    without `=` or with a non-identifier key (e.g. an `export ` prefix or
    spaces around the key) are skipped — these files are loader-parsed,
    never shell-sourced, and values are taken verbatim after the first `=`
    (no quote handling — this list is the whole parsing policy).
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"stack-env loader cannot read {path}: {e}") from e
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not _KEY_RE.match(key):
            continue
        values[key] = value
    return values


def _load_pair(root: Path) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Parse .env.seed and .env.local exactly once each.

    Both public entry points below share this — re-parsing per call was
    harmless but meant the two files were read off disk twice per fixture.
    """
    return _parse(root / _SEED_REL), _parse(root / ".env.local")


def _merge_into_environ(machine: dict[str, str], local: dict[str, str]) -> None:
    merged = {**machine, **local}
    for key, value in merged.items():
        os.environ.setdefault(key, value)


def load_stack_env() -> bool:
    """Merge .env.seed then .env.local into os.environ (setdefault).

    Returns True iff the machine file existed. Shell env always wins.
    """
    root = _repo_root()
    machine, local = _load_pair(root)
    _merge_into_environ(machine or {}, local or {})
    return machine is not None


def require_stack_env() -> None:
    """Fixture-time gate for the contract, e2e and replay tiers.

    No machine file and no creds -> skip (no stack claimed). Machine file
    present but a machine key unusable -> hard fail with the cause: a present
    file claims a seeded stack, and silence here was the old skip-green hole.

    Shell-wins precedence (spec) still applies unconditionally, but when the
    shell value *differs* from a non-empty .env.seed value, that's the
    mutating suites silently aiming at whatever real Kibana the shell happens
    to point at instead of the seeded local stack — warn loudly so it's never
    silent, even though the shell still wins.
    """
    import pytest

    root = _repo_root()
    machine, local = _load_pair(root)
    machine_exists = machine is not None
    machine = machine or {}
    local = local or {}
    _merge_into_environ(machine, local)

    forbidden = [k for k in _MACHINE_KEYS if k in local]
    if forbidden:
        pytest.fail(
            "machine-owned keys belong to elastic-start-local/.env.seed — remove "
            f"them from .env.local ({', '.join(forbidden)})"
        )

    for key in _MACHINE_KEYS:
        machine_value = machine.get(key)
        shell_value = os.environ.get(key)
        if machine_value and shell_value and shell_value != machine_value:
            warnings.warn(
                f"shell {key} overrides elastic-start-local/.env.seed "
                f"({os.environ[key]!r} wins — shell env has precedence by design)",
                stacklevel=2,
            )

    missing = [k for k in _MACHINE_KEYS if not os.environ.get(k)]
    if not missing:
        return
    if not machine_exists:
        pytest.skip(
            "contract/e2e need a seeded stack — run: "
            "scripts/stack.sh up && scripts/stack.sh seed"
        )
    for key in missing:
        if machine.get(key) and os.environ.get(key) == "":
            pytest.fail(
                f"shell exports an empty {key} that shadows "
                "elastic-start-local/.env.seed — unset it"
            )
    for key in missing:
        if key in machine:
            # The line is present but its value is empty (e.g. `KEY=`) — a
            # missing *value*, not a missing *line*; say so precisely.
            pytest.fail(
                f"{key} is empty in elastic-start-local/.env.seed — re-run: "
                "scripts/stack.sh seed"
            )
    pytest.fail(
        f"{' and '.join(missing)} line missing from elastic-start-local/.env.seed "
        "— re-run: scripts/stack.sh seed"
    )
