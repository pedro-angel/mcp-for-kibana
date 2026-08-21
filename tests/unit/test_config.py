import os

import pytest

from kibana_mcp.config import Settings, Tier

_CRED_VARS = ("KIBANA_URL", "KIBANA_API_KEY", "KIBANA_MCP_API_KEY", "KIBANA_TEST_API_KEY",
             "KIBANA_MCP_ENV_FILE")


def test_defaults(monkeypatch):
    for var in ("KIBANA_URL", "KIBANA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.kibana_url == "http://localhost:5601"
    assert s.api_key is None
    assert s.toolboxes == ["dashboards", "data-management"]
    assert s.tier is Tier.WRITE
    assert s.transport == "stdio"


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("KIBANA_URL", "https://kib.example.com")
    monkeypatch.setenv("KIBANA_API_KEY", "sekret")
    monkeypatch.setenv("KIBANA_MCP_TOOLBOXES", "dashboards, alerting")
    monkeypatch.setenv("KIBANA_MCP_TIER", "read")
    monkeypatch.setenv("KIBANA_MCP_TRANSPORT", "http")
    s = Settings()
    assert s.kibana_url == "https://kib.example.com"
    assert s.api_key == "sekret"
    assert s.toolboxes == ["dashboards", "alerting"]
    assert s.tier is Tier.READ
    assert s.transport == "http"


def test_aliased_fields_accept_field_name_kwarg(monkeypatch):
    # Regression for #6: fields with a validation_alias were silently unsettable
    # by their own name in the constructor (extra="ignore" swallowed the kwarg),
    # so Settings(api_key="x").api_key came back None. populate_by_name fixes it.
    for var in ("KIBANA_URL", "KIBANA_API_KEY", "KIBANA_PUBLIC_URL",
                "KIBANA_MCP_API_KEY", "KIBANA_MCP_KIBANA_URL", "KIBANA_MCP_PUBLIC_KIBANA_URL"):
        monkeypatch.delenv(var, raising=False)
    assert Settings(api_key="sekret").api_key == "sekret"
    assert Settings(kibana_url="https://kib.example.com").kibana_url == "https://kib.example.com"
    assert Settings(public_kibana_url="https://public.example.com").public_kibana_url == (
        "https://public.example.com"
    )


def test_tier_allowed():
    assert Tier.READ.allowed == {"read"}
    assert Tier.WRITE.allowed == {"read", "write"}
    assert Tier.DESTRUCTIVE.allowed == {"read", "write", "destructive"}


def test_public_url_falls_back_to_kibana_url(monkeypatch):
    monkeypatch.setenv("KIBANA_URL", "http://kibana:5601")
    s = Settings()
    assert s.effective_public_url == "http://kibana:5601"
    monkeypatch.setenv("KIBANA_PUBLIC_URL", "https://kibana.corp")
    assert Settings().effective_public_url == "https://kibana.corp"


@pytest.fixture
def isolated_env():
    """Fully snapshot/restore os.environ — Settings.load() mutates it via
    setdefault, which monkeypatch alone would not roll back."""
    saved = dict(os.environ)
    for var in _CRED_VARS:
        os.environ.pop(var, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_load_without_env_file_is_plain_settings(isolated_env):
    # Default-off: KIBANA_MCP_ENV_FILE unset -> identical to Settings().
    s = Settings.load()
    assert s.kibana_url == "http://localhost:5601"
    assert s.api_key is None


def test_load_hydrates_creds_from_env_file(isolated_env, tmp_path):
    # Mirrors .env.seed: KIBANA_URL + KIBANA_TEST_API_KEY (the alias bridge).
    seed = tmp_path / ".env.seed"
    seed.write_text("KIBANA_URL=http://localhost:15601\nKIBANA_TEST_API_KEY=seedkey123\n")
    os.environ["KIBANA_MCP_ENV_FILE"] = str(seed)
    s = Settings.load()
    assert s.kibana_url == "http://localhost:15601"
    assert s.api_key == "seedkey123"


def test_load_process_env_wins_over_file(isolated_env, tmp_path):
    seed = tmp_path / ".env.seed"
    seed.write_text("KIBANA_URL=http://from-file:15601\nKIBANA_TEST_API_KEY=filekey\n")
    os.environ["KIBANA_MCP_ENV_FILE"] = str(seed)
    os.environ["KIBANA_API_KEY"] = "envkey"  # explicit env must win
    os.environ["KIBANA_URL"] = "http://from-env:5601"
    s = Settings.load()
    assert s.api_key == "envkey"
    assert s.kibana_url == "http://from-env:5601"


def test_load_missing_env_file_raises(isolated_env, tmp_path):
    os.environ["KIBANA_MCP_ENV_FILE"] = str(tmp_path / "does-not-exist.env")
    with pytest.raises(RuntimeError, match="could not be read"):
        Settings.load()


def test_load_unreadable_binary_env_file_raises(isolated_env, tmp_path):
    # A non-UTF-8 file must surface as the clean RuntimeError, not a bare
    # UnicodeDecodeError traceback.
    bad = tmp_path / "binary.env"
    bad.write_bytes(b"\xff\xfe\x00\x01 not utf-8")
    os.environ["KIBANA_MCP_ENV_FILE"] = str(bad)
    with pytest.raises(RuntimeError, match="could not be read"):
        Settings.load()


def test_load_does_not_bridge_ambient_test_key(isolated_env):
    # Regression: KIBANA_TEST_API_KEY is bridged to api_key ONLY for file values.
    # A stray exported test key (no env-file) must NOT credential a server run.
    os.environ["KIBANA_TEST_API_KEY"] = "stray-test-key"
    assert Settings.load().api_key is None


def test_load_skips_empty_value_in_file(isolated_env, tmp_path):
    # A half-written seed (KIBANA_TEST_API_KEY=) is a MISSING value, not "".
    seed = tmp_path / ".env.seed"
    seed.write_text("KIBANA_URL=http://localhost:15601\nKIBANA_TEST_API_KEY=\n")
    os.environ["KIBANA_MCP_ENV_FILE"] = str(seed)
    s = Settings.load()
    assert s.kibana_url == "http://localhost:15601"
    assert s.api_key is None


def test_load_ignores_comments_and_blanks(isolated_env, tmp_path):
    seed = tmp_path / ".env.seed"
    seed.write_text("# a comment\n\nKIBANA_TEST_API_KEY=k\nnot a kv line\n")
    os.environ["KIBANA_MCP_ENV_FILE"] = str(seed)
    assert Settings.load().api_key == "k"
