"""Deployment configuration — the single source of truth for server assembly."""

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_TIER_ORDER = ["read", "write", "destructive"]

# ASCII env-var key grammar (same as the test env-loader's, so both accept the
# identical key set from the shared elastic-start-local/.env.seed / .env.local format).
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# elastic-start-local/.env.seed names the API key KIBANA_TEST_API_KEY. Bridge it to the real setting
# name — but ONLY for values that come from the env-file (see Settings.load),
# never as an ambient-environment alias, which would silently authenticate a real
# server run with a stray exported test key.
_ENV_FILE_KEY_ALIASES = {"KIBANA_TEST_API_KEY": "KIBANA_API_KEY"}


def _parse_env_file(path: str) -> dict[str, str]:
    """Minimal `KEY=value` parser (verbatim value after the first `=`; no quote
    handling, never shell-sourced). Blank / `#` / no-`=` lines and non-identifier
    keys are skipped; the whole line is stripped first, so trailing whitespace on
    a value is trimmed. A named-but-unreadable file is an error — a launcher that
    points KIBANA_MCP_ENV_FILE at a path meant it to exist."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"KIBANA_MCP_ENV_FILE={path!r} could not be read: {e}") from e
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if _ENV_KEY_RE.match(key.strip()):
            values[key.strip()] = value
    return values


class Tier(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    @property
    def allowed(self) -> set[str]:
        return set(_TIER_ORDER[: _TIER_ORDER.index(self.value) + 1])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KIBANA_MCP_", extra="ignore", populate_by_name=True
    )

    kibana_url: str = Field(
        "http://localhost:5601",
        validation_alias=AliasChoices("KIBANA_URL", "KIBANA_MCP_KIBANA_URL"),
    )
    public_kibana_url: str | None = Field(
        None, validation_alias=AliasChoices("KIBANA_PUBLIC_URL", "KIBANA_MCP_PUBLIC_KIBANA_URL")
    )
    api_key: str | None = Field(
        None, validation_alias=AliasChoices("KIBANA_API_KEY", "KIBANA_MCP_API_KEY")
    )
    # Default pairs dashboards with data-management: building a visualization
    # requires inspecting the data view's fields first (describe_data_view), which
    # lives in data-management since the #28 extraction.
    toolboxes: Annotated[list[str], NoDecode] = ["dashboards", "data-management"]
    tier: Tier = Tier.WRITE
    # Where saved-objects export writes NDJSON handles (import reads them back);
    # None -> <tempdir>/mcp-for-kibana-exports at server assembly. The bytes live on
    # disk in this dir, never in the model context (#37).
    export_dir: str | None = None
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    allow_env_key_http: bool = False

    # OpenTelemetry span export (Phase C; additive, default-off). When
    # otel_enabled is false the server behaves exactly as before and never
    # imports the OTEL SDK. Defaults target the local APM from the stack
    # (KIBANA_MCP_STACK_APM=1); the secret token is required by that endpoint.
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:18200"
    otel_secret_token: str | None = None
    otel_service_name: str = "mcp-for-kibana"

    @classmethod
    def load(cls) -> "Settings":
        """Build Settings, first hydrating os.environ from the file named by
        KIBANA_MCP_ENV_FILE (if set) with setdefault — so an **explicit process
        env var of the same name always wins** over the file. Additive and
        default-off: with the var unset this is exactly ``Settings()``.

        This lets a launcher (e.g. LM Studio's mcp.json) point KIBANA_MCP_ENV_FILE
        at the machine-written elastic-start-local/.env.seed instead of hard-copying the ephemeral API
        key: the key then refreshes on every ``stack.sh seed`` with no launcher
        edit. elastic-start-local/.env.seed names the key KIBANA_TEST_API_KEY; it is bridged to
        KIBANA_API_KEY **only for file values** (never for an ambient env var, so a
        stray exported test key can't silently credential a real server run).
        Empty values are ignored (a ``KEY=`` line is a missing value)."""
        env_file = os.environ.get("KIBANA_MCP_ENV_FILE")
        if env_file:
            for key, value in _parse_env_file(env_file).items():
                if value:  # skip empty: a KEY= line is a missing value, not ""
                    os.environ.setdefault(_ENV_FILE_KEY_ALIASES.get(key, key), value)
        return cls()

    @field_validator("toolboxes", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def effective_public_url(self) -> str:
        """URL used to build human-clickable dashboard links."""
        return (self.public_kibana_url or self.kibana_url).rstrip("/")
