"""Unit tests for the stack-env loader: parsing policy and the fixture-time gate."""

import os

import pytest

from tests import _stack_env


_TEST_VARS = ("KIBANA_URL", "KIBANA_TEST_API_KEY", "LMSTUDIO_URL", "PROBE_X")


@pytest.fixture()
def env_root(tmp_path, monkeypatch):
    monkeypatch.setattr(_stack_env, "_repo_root", lambda: tmp_path)
    (tmp_path / "elastic-start-local").mkdir()  # seed creds are foldered here now
    for var in _TEST_VARS:
        monkeypatch.delenv(var, raising=False)
    yield tmp_path
    # load_stack_env writes to os.environ directly; monkeypatch can't restore
    # what it didn't set — scrub so no test-order pollution leaks downstream.
    for var in _TEST_VARS:
        os.environ.pop(var, None)


def test_parse_tolerates_crlf_comments_and_junk(env_root, monkeypatch):
    (env_root / "elastic-start-local" / ".env.seed").write_bytes(
        b"KIBANA_URL=http://a:1\r\n"
        b"# comment\n"
        b"\n"
        b"not a kv line\n"
        b"export KIBANA_TEST_API_KEY=nope\n"
        b"KIBANA_TEST_API_KEY=k\n"
    )
    assert _stack_env.load_stack_env() is True
    assert os.environ["KIBANA_URL"] == "http://a:1"  # CRLF-safe (whole-line strip)
    assert os.environ["KIBANA_TEST_API_KEY"] == "k"  # export-prefixed line skipped


def test_shell_env_wins_and_local_fills_missing(env_root, monkeypatch):
    (env_root / "elastic-start-local" / ".env.seed").write_text("KIBANA_URL=http://file:1\n")
    (env_root / ".env.local").write_text("LMSTUDIO_URL=http://local:2\n")
    monkeypatch.setenv("KIBANA_URL", "http://shell:9")
    _stack_env.load_stack_env()
    assert os.environ["KIBANA_URL"] == "http://shell:9"
    assert os.environ["LMSTUDIO_URL"] == "http://local:2"


def test_local_wins_over_machine_in_merge(env_root):
    (env_root / "elastic-start-local" / ".env.seed").write_text("PROBE_X=machine\n")
    (env_root / ".env.local").write_text("PROBE_X=local\n")
    _stack_env.load_stack_env()
    assert os.environ["PROBE_X"] == "local"


def test_returns_false_when_machine_file_absent(env_root):
    (env_root / ".env.local").write_text("LMSTUDIO_URL=http://local:2\n")
    assert _stack_env.load_stack_env() is False
    assert os.environ["LMSTUDIO_URL"] == "http://local:2"  # .local read regardless


def test_require_skips_when_no_stack_claimed(env_root):
    with pytest.raises(pytest.skip.Exception, match="scripts/stack.sh up"):
        _stack_env.require_stack_env()


def test_require_fails_on_keyless_machine_file(env_root):
    (env_root / "elastic-start-local" / ".env.seed").write_text("KIBANA_URL=http://a:1\n")
    with pytest.raises(pytest.fail.Exception, match="re-run: scripts/stack.sh seed"):
        _stack_env.require_stack_env()


def test_require_fails_on_empty_shell_shadow(env_root, monkeypatch):
    (env_root / "elastic-start-local" / ".env.seed").write_text(
        "KIBANA_URL=http://a:1\nKIBANA_TEST_API_KEY=k\n"
    )
    monkeypatch.setenv("KIBANA_TEST_API_KEY", "")
    with pytest.raises(pytest.fail.Exception, match="shadows elastic-start-local/.env.seed"):
        _stack_env.require_stack_env()


def test_require_fails_on_forbidden_key_in_local(env_root):
    (env_root / "elastic-start-local" / ".env.seed").write_text(
        "KIBANA_URL=http://a:1\nKIBANA_TEST_API_KEY=k\n"
    )
    (env_root / ".env.local").write_text("KIBANA_URL=http://stale:1\n")
    with pytest.raises(pytest.fail.Exception, match="machine-owned"):
        _stack_env.require_stack_env()


def test_require_passes_on_valid_state(env_root):
    (env_root / "elastic-start-local" / ".env.seed").write_text(
        "KIBANA_URL=http://a:1\nKIBANA_TEST_API_KEY=k\n"
    )
    _stack_env.require_stack_env()  # no raise


def test_parse_strips_bom(env_root):
    (env_root / "elastic-start-local" / ".env.seed").write_bytes(
        b"\xef\xbb\xbfKIBANA_URL=http://a:1\nKIBANA_TEST_API_KEY=k\n"
    )
    assert _stack_env.load_stack_env() is True
    assert os.environ["KIBANA_URL"] == "http://a:1"  # BOM didn't eat the key
    assert os.environ["KIBANA_TEST_API_KEY"] == "k"


def test_require_fails_on_present_but_empty_value(env_root):
    (env_root / "elastic-start-local" / ".env.seed").write_text(
        "KIBANA_URL=http://a:1\nKIBANA_TEST_API_KEY=\n"
    )
    with pytest.raises(pytest.fail.Exception, match="is empty in elastic-start-local/.env.seed"):
        _stack_env.require_stack_env()


def test_require_warns_when_shell_shadows_a_differing_value(env_root, monkeypatch):
    (env_root / "elastic-start-local" / ".env.seed").write_text(
        "KIBANA_URL=http://a:1\nKIBANA_TEST_API_KEY=k\n"
    )
    monkeypatch.setenv("KIBANA_URL", "http://other:9")
    with pytest.warns(UserWarning, match="overrides elastic-start-local/.env.seed"):
        _stack_env.require_stack_env()  # no raise — shell still wins


def test_parse_raises_runtime_error_on_unreadable_file(env_root):
    (env_root / "elastic-start-local" / ".env.seed").mkdir()
    with pytest.raises(RuntimeError, match="cannot read"):
        _stack_env.load_stack_env()


def test_repo_root_asserts_marker(tmp_path, monkeypatch):
    fake_file = tmp_path / "tests" / "_stack_env.py"
    fake_file.parent.mkdir()
    fake_file.touch()
    monkeypatch.setattr(_stack_env, "__file__", str(fake_file))
    with pytest.raises(RuntimeError, match="non-repo root"):
        _stack_env._repo_root()
