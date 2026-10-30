"""The DoD gate's stack-version check (tests/_stack_version.py, D53).

The seeded-stack criteria certify whatever Kibana the seed points at, so the
gate first reads that Kibana's version and refuses a stack that is not the line
it certifies: the `.env.example` pin locally (the newest supported version,
D30), the matrix's `ES_LOCAL_VERSION` in CI. Every refusal names the fix.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tests import _stack_version as sv

_SUPPORTED = ("9.5.4", "9.4.7")
_FIX = "scripts/stack.sh down && scripts/stack.sh up && scripts/stack.sh seed"


def _write_stack(root: Path, pin: str = "9.5.4", seed: str | None = None) -> Path:
    stack = root / "elastic-start-local"
    stack.mkdir(parents=True)
    (stack / ".env.example").write_text(f"ES_LOCAL_PASSWORD=x\nES_LOCAL_VERSION={pin}\n")
    if seed is not None:
        (stack / ".env.seed").write_text(seed)
    return root


# --- the pin -----------------------------------------------------------------


def test_pinned_version_reads_the_env_example_pin(tmp_path):
    assert sv.pinned_version(_write_stack(tmp_path, pin="9.5.4")) == "9.5.4"


def test_pinned_version_without_a_pin_names_the_file(tmp_path):
    stack = tmp_path / "elastic-start-local"
    stack.mkdir()
    (stack / ".env.example").write_text("ES_LOCAL_PASSWORD=x\n")
    with pytest.raises(sv.StackVersionError, match=r"ES_LOCAL_VERSION.*\.env\.example"):
        sv.pinned_version(tmp_path)


def test_the_real_pin_is_a_supported_version():
    root = Path(__file__).resolve().parents[2]
    assert sv.pinned_version(root) in sv.supported_versions()


# --- the expected version ----------------------------------------------------


def test_locally_the_gate_certifies_the_pin():
    version, source = sv.expected_version({}, "9.5.4", _SUPPORTED)
    assert version == "9.5.4"
    assert ".env.example" in source and "D30" in source


def test_locally_an_es_local_version_override_does_not_move_the_target():
    # D30: the local gate runs on the newest supported version, whatever the
    # shell exported for an earlier `ES_LOCAL_VERSION=9.4.7 make test-contract`.
    version, _ = sv.expected_version({"ES_LOCAL_VERSION": "9.4.7"}, "9.5.4", _SUPPORTED)
    assert version == "9.5.4"


def test_in_ci_the_gate_certifies_the_matrix_version():
    env = {"GITHUB_ACTIONS": "true", "ES_LOCAL_VERSION": "9.4.7"}
    version, source = sv.expected_version(env, "9.5.4", _SUPPORTED)
    assert version == "9.4.7"
    assert "matrix" in source


def test_in_ci_without_a_matrix_version_the_pin_stands():
    version, _ = sv.expected_version({"GITHUB_ACTIONS": "true"}, "9.5.4", _SUPPORTED)
    assert version == "9.5.4"


def test_in_ci_an_unsupported_matrix_version_is_refused_with_the_fix():
    env = {"GITHUB_ACTIONS": "true", "ES_LOCAL_VERSION": "9.5.5"}
    with pytest.raises(sv.StackVersionError) as exc:
        sv.expected_version(env, "9.5.4", _SUPPORTED)
    message = str(exc.value)
    assert "9.5.5" in message and "SUPPORTED_VERSIONS" in message


def test_a_pin_outside_the_supported_set_is_refused_with_the_fix():
    with pytest.raises(sv.StackVersionError) as exc:
        sv.expected_version({}, "9.5.5", _SUPPORTED)
    assert "elastic-start-local/.env.example" in str(exc.value)
    assert "9.5.4" in str(exc.value)


# --- the comparison ----------------------------------------------------------


def test_matching_versions_pass():
    assert sv.mismatch("9.5.4", "9.5.4", "the pin", {}) is None


def test_an_off_pin_stack_names_both_versions_and_the_reprovision_command():
    message = sv.mismatch("9.5.5", "9.5.4", "the pin", {})
    assert message is not None
    assert "9.5.5" in message and "9.5.4" in message
    assert _FIX in message


def test_a_local_override_in_the_shell_is_named_in_the_fix():
    message = sv.mismatch("9.4.7", "9.5.4", "the pin", {"ES_LOCAL_VERSION": "9.4.7"})
    assert message is not None
    assert "unset ES_LOCAL_VERSION" in message and _FIX in message


def test_a_local_override_equal_to_the_target_is_not_named():
    message = sv.mismatch("9.5.5", "9.5.4", "the pin", {"ES_LOCAL_VERSION": "9.5.4"})
    assert message is not None and "unset" not in message and _FIX in message


def test_in_ci_the_fix_does_not_tell_the_runner_to_unset_the_matrix_version():
    env = {"GITHUB_ACTIONS": "true", "ES_LOCAL_VERSION": "9.4.7"}
    message = sv.mismatch("9.5.4", "9.4.7", "the matrix", env)
    assert message is not None and "unset" not in message and _FIX in message


# --- the live read (a fake /api/status) ---------------------------------------


class _Status(BaseHTTPRequestHandler):
    version: str | None = "9.5.4"
    seen_auth: list[str] = []

    def do_GET(self):  # noqa: N802 — http.server's name
        type(self).seen_auth.append(self.headers.get("Authorization", ""))
        if self.path != "/api/status":
            self.send_response(404)
            self.end_headers()
            return
        version = type(self).version
        status = {"status": {"overall": {"level": "available"}}}
        body = json.dumps(status if version is None else {"version": {"number": version}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output quiet
        pass


@pytest.fixture
def kibana():
    _Status.seen_auth = []
    server = HTTPServer(("127.0.0.1", 0), _Status)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_running_version_reads_api_status_with_the_seed_key(kibana):
    _Status.version = "9.5.4"
    assert sv.running_version(kibana, "c2VlZA==") == "9.5.4"
    assert _Status.seen_auth == ["ApiKey c2VlZA=="]


def test_an_unreachable_kibana_names_the_start_command():
    with pytest.raises(sv.StackVersionError) as exc:
        sv.running_version("http://127.0.0.1:9", "k")
    assert "scripts/stack.sh up && scripts/stack.sh seed" in str(exc.value)


def test_an_unauthenticated_status_names_the_key_and_the_seed_command(kibana):
    _Status.version = None  # Kibana omits `version` for a caller it does not authenticate
    with pytest.raises(sv.StackVersionError) as exc:
        sv.running_version(kibana, "s3cr3t-key")
    assert "key is invalid or revoked" in str(exc.value)
    assert "scripts/stack.sh seed" in str(exc.value) and "s3cr3t-key" not in str(exc.value)


def test_check_passes_on_the_pinned_stack_and_returns_the_version(tmp_path, kibana):
    _Status.version = "9.5.4"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    assert sv.check(root, {}, _SUPPORTED) == "9.5.4"


def test_check_refuses_an_off_pin_stack(tmp_path, kibana):
    _Status.version = "9.5.5"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, {}, _SUPPORTED)
    assert "9.5.5" in str(exc.value) and _FIX in str(exc.value)


# --- shell credentials (D56) ---------------------------------------------------
# Shell env wins over .env.seed for the suites (tests/_stack_env.py), so a shell
# KIBANA_URL or KIBANA_TEST_API_KEY aimed the gate's mutating suites at another
# Kibana with only a warning in a log. The gate certifies the seeded local
# stack: a shell value that differs from the seed is refused before any call.

_UNSET = "unset KIBANA_URL KIBANA_TEST_API_KEY"


def test_check_refuses_a_shell_url_that_differs_from_the_seed(tmp_path, kibana):
    _Status.version = "9.5.4"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=seed\n")
    env = {"KIBANA_URL": "http://other-kibana:5601", "KIBANA_TEST_API_KEY": "seed"}
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, env, _SUPPORTED)
    message = str(exc.value)
    assert "KIBANA_URL" in message and ".env.seed" in message and _UNSET in message
    assert "seeded local stack" in message
    assert _Status.seen_auth == [], "the refusal comes before any call to a Kibana"


def test_check_refuses_a_shell_key_that_differs_and_never_prints_it(tmp_path, kibana):
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=seed-key\n")
    env = {"KIBANA_TEST_API_KEY": "s3cr3t-shell-key"}
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, env, _SUPPORTED)
    message = str(exc.value)
    assert "KIBANA_TEST_API_KEY" in message and _UNSET in message
    assert "s3cr3t-shell-key" not in message and "seed-key" not in message


def test_check_refuses_an_empty_shell_value_that_shadows_the_seed(tmp_path, kibana):
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    with pytest.raises(sv.StackVersionError, match=r"KIBANA_URL.*unset KIBANA_URL"):
        sv.check(root, {"KIBANA_URL": ""}, _SUPPORTED)


def test_check_accepts_shell_credentials_equal_to_the_seed(tmp_path, kibana):
    _Status.version = "9.5.4"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    env = {"KIBANA_URL": kibana, "KIBANA_TEST_API_KEY": "k"}
    assert sv.check(root, env, _SUPPORTED) == "9.5.4"
    assert _Status.seen_auth == ["ApiKey k"]


def test_main_prints_the_shell_refusal_on_one_line(tmp_path, kibana, capsys):
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    assert sv.main(root=root, environ={"KIBANA_URL": "http://x:1"}, supported=_SUPPORTED) == 1
    out = capsys.readouterr().out.strip()
    assert "\n" not in out and _UNSET in out


# The replay servers inherit the suite's environment (.env.local merged in) and
# read KIBANA_API_KEY ahead of the seed's bridged key, so an export of it
# re-credentials the server under test; the public-URL pair moves its links.


@pytest.mark.parametrize("var", sv._SERVER_SETTINGS)
def test_check_refuses_a_shell_server_setting_and_never_prints_it(tmp_path, kibana, var):
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, {var: "s3cr3t-other"}, _SUPPORTED)
    message = str(exc.value)
    assert f"the shell sets {var}" in message and f"{_UNSET} {var}" in message
    assert "s3cr3t-other" not in message
    assert _Status.seen_auth == [], "the refusal comes before any call to a Kibana"


def test_check_refuses_a_server_key_in_env_local(tmp_path, kibana):
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    (root / ".env.local").write_text("LMSTUDIO_MODEL=m\nKIBANA_API_KEY=s3cr3t-local\n")
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, {}, _SUPPORTED)
    message = str(exc.value)
    assert ".env.local sets KIBANA_API_KEY" in message
    assert "remove KIBANA_API_KEY from .env.local" in message
    assert "s3cr3t-local" not in message


@pytest.mark.parametrize("var", ["kibana_public_url", "Kibana_Api_Key", "kibana_mcp_public_kibana_url"])
def test_check_refuses_a_server_setting_in_any_case(tmp_path, kibana, var):
    # config.Settings reads env names case-insensitively, so a lowercase export
    # reaches the replayed server as surely as the uppercase one.
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, {var: "http://evil"}, _SUPPORTED)
    message = str(exc.value)
    assert f"the shell sets {var}" in message and f"{_UNSET} {var}" in message
    assert "http://evil" not in message


def test_check_refuses_a_lowercase_server_setting_in_env_local(tmp_path, kibana):
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    (root / ".env.local").write_text("kibana_public_url=http://evil\n")
    with pytest.raises(sv.StackVersionError, match=r"remove kibana_public_url from \.env\.local"):
        sv.check(root, {}, _SUPPORTED)


def test_check_ignores_server_aliases_the_seed_shadows(tmp_path, kibana):
    # Settings reads the first alias present; the seed fills KIBANA_URL and
    # KIBANA_API_KEY, so the KIBANA_MCP_ spellings never reach the server.
    _Status.version = "9.5.4"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    env = {"KIBANA_MCP_API_KEY": "x", "KIBANA_MCP_KIBANA_URL": "http://x:1"}
    assert sv.check(root, env, _SUPPORTED) == "9.5.4"


def test_check_without_a_seed_names_the_seed_command(tmp_path):
    root = _write_stack(tmp_path)
    with pytest.raises(sv.StackVersionError, match=r"scripts/stack.sh seed"):
        sv.check(root, {}, _SUPPORTED)


def test_check_with_an_empty_seed_key_names_the_seed_command(tmp_path):
    root = _write_stack(tmp_path, seed="KIBANA_URL=http://127.0.0.1:9\nKIBANA_TEST_API_KEY=\n")
    with pytest.raises(sv.StackVersionError, match=r"KIBANA_TEST_API_KEY.*scripts/stack.sh seed"):
        sv.check(root, {}, _SUPPORTED)


def test_main_prints_the_version_and_exits_zero(tmp_path, kibana, capsys):
    _Status.version = "9.5.4"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    assert sv.main(root=root, environ={}, supported=_SUPPORTED) == 0
    assert capsys.readouterr().out.strip() == "9.5.4"


def test_main_prints_one_line_naming_the_fix_and_exits_one(tmp_path, kibana, capsys):
    _Status.version = "9.4.7"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    assert sv.main(root=root, environ={}, supported=_SUPPORTED) == 1
    out = capsys.readouterr().out.strip()
    assert "\n" not in out and _FIX in out


# --- the gate's wiring -------------------------------------------------------

_GATE = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "definition-of-done.sh"
_SEEDED_CRITERIA = (
    "contract_green",
    "e2e_replay_green",
    "wheel_replay_green",
    "image_replay_green",
    "e2e_green",
)


def test_every_seeded_stack_criterion_goes_through_the_version_check():
    gate = _GATE.read_text()
    for criterion in _SEEDED_CRITERIA:
        block = gate.split(f"if req {criterion}; then", 1)[1].split("\nfi\n", 1)[0]
        assert "seeded_stack" in block and f"stack_nogo {criterion}" in block, (
            f"{criterion} must run behind seeded_stack (the stack-version check, D53) "
            "and report its refusal with stack_nogo"
        )
        assert "Kibana $stack_version" in block, (
            f"{criterion}'s GO line must name the Kibana version it certified (D53)"
        )


@pytest.mark.parametrize("criterion", ["streams_ephemeral", "fleet_ephemeral",
                                       "fleet_unaware_ephemeral"])
def test_every_ephemeral_criterion_goes_through_the_line_check(criterion):
    # D62: the ephemeral scripts honour an exported ES_LOCAL_VERSION, so the
    # gate refuses one off the pin locally and names the line on the GO line
    # (behaviour: tests/unit/test_dod_suite_env.py).
    gate = _GATE.read_text()
    block = gate.split(f"if req {criterion}; then", 1)[1].split("\nfi\n", 1)[0]
    assert "ephemeral_line" in block and f"NO-GO {criterion}  ($eph_reason)" in block
    assert "Kibana $eph_version" in block


def test_no_criterion_checks_the_seed_file_on_its_own():
    # A bare `-f .env.seed` test is the old precondition this check replaced:
    # only seeded_stack may look at the seed, so no criterion skips the version.
    gate = _GATE.read_text()
    assert "seeded_stack() {" in gate and "stack_nogo() {" in gate, (
        "the gate lost its seeded-stack precondition (seeded_stack/stack_nogo, D53)"
    )
    outside = gate.split("seeded_stack() {", 1)[0] + gate.split("stack_nogo() {", 1)[1]
    assert "-f elastic-start-local/.env.seed" not in outside


@pytest.mark.parametrize(
    "env",
    [
        {"kibana_url": "http://evil"},
        {"KIBANA_URL": "http://seed-equal", "kibana_url": "http://evil"},
        {"Kibana_Test_Api_Key": "s3cr3t-other"},
    ],
)
def test_check_refuses_a_credential_spelled_in_another_case(tmp_path, kibana, env):
    # Settings lowercases env names and the later key wins, so a lowercase
    # kibana_url points the replayed server at another Kibana with the seed key.
    seed_url = env.get("KIBANA_URL", kibana)
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={seed_url}\nKIBANA_TEST_API_KEY=k\n")
    odd = next(k for k in env if k not in sv._CREDS)
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, env, _SUPPORTED)
    message = str(exc.value)
    assert f"{odd} to a value that differs" in message and f"{_UNSET} {odd}" in message
    assert "http://evil" not in message and "s3cr3t-other" not in message
    assert _Status.seen_auth == [], "the refusal comes before any call to a Kibana"


def test_check_accepts_a_lowercase_credential_equal_to_the_seed(tmp_path, kibana):
    _Status.version = "9.5.4"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    assert sv.check(root, {"kibana_url": kibana}, _SUPPORTED) == "9.5.4"


@pytest.mark.parametrize("var", ["kibana_url", "KIBANA_TEST_API_KEY"])
def test_check_refuses_a_credential_in_env_local_in_any_case(tmp_path, kibana, var):
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    (root / ".env.local").write_text(f"{var}=http://evil\n")
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, {}, _SUPPORTED)
    message = str(exc.value)
    assert f"remove {var} from .env.local" in message and "http://evil" not in message


@pytest.mark.parametrize(
    "var",
    [
        "UV_ENV_FILE", "uv_env_file", "PYTHONPATH", "LD_PRELOAD", "KIBANA_MCP_SERVER_BIN",
        "kibana_mcp_server_bin", "KIBANA_MCP_SERVER_IMAGE", "kibana_mcp_server_image",
    ],
)
def test_check_refuses_a_runner_setting_in_env_local(tmp_path, kibana, var):
    # .env.local is merged in after the gate clears the shell: UV_ENV_FILE made
    # `uv run` load another KIBANA_API_KEY into the replayed server, and
    # KIBANA_MCP_SERVER_BIN replayed an installed server instead of the checkout.
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    (root / ".env.local").write_text(f"LMSTUDIO_MODEL=m\n{var}=/tmp/s3cr3t-path\n")
    with pytest.raises(sv.StackVersionError) as exc:
        sv.check(root, {}, _SUPPORTED)
    message = str(exc.value)
    assert f"remove {var} from .env.local" in message and "s3cr3t-path" not in message
    assert _Status.seen_auth == [], "the refusal comes before any call to a Kibana"


def test_check_accepts_the_lm_studio_settings_in_env_local(tmp_path, kibana):
    _Status.version = "9.5.4"
    root = _write_stack(tmp_path, seed=f"KIBANA_URL={kibana}\nKIBANA_TEST_API_KEY=k\n")
    (root / ".env.local").write_text("LMSTUDIO_MODEL=m\nLMSTUDIO_URL=http://localhost:1234\nLMSTUDIO_API_TOKEN=\n")
    assert sv.check(root, {}, _SUPPORTED) == "9.5.4"
