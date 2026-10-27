"""Drive one headless Claude Code run (`claude -p`) against mcp-for-kibana.

Used by the Claude live-model tier (tests/e2e/test_claude_live.py, `make
test-e2e`). The command line follows the efficiency study's runner
(scripts/experiment/efficiency_run.py) with the shell taken away:

- `--tools ""` removes every built-in tool (no Bash, no file tools, no web),
  `--strict-mcp-config` loads only the MCP config written here, `--restricted`
  ignores user/project/local settings, and `--permission-mode dontAsk` with
  `--allowedTools mcp__mcp-for-kibana` runs this server's tools without a
  prompt and denies anything else. The init event's tool list is recorded per
  run, and the test fails a run that was offered any other tool.
- The Kibana API key goes into the MCP server's `env` block only, in a
  mode-0600 config file inside a temporary directory the model cannot reach.
  The model's own environment drops every `KIBANA*` variable and any variable
  whose value holds the key, and the prompt never names it. After the run the
  transcript is searched for the key (`key_in_transcript`); a hit is redacted
  before anything is written and fails the run. The key is never printed.
- Telemetry (additive, default-off): with `KIBANA_MCP_OTEL_ENABLED` truthy in
  the runner's environment, every `KIBANA_MCP_OTEL_*` and `OTEL_*` variable is
  copied into the server's `env` block (the same private file), so the
  server's per-tool spans export where the operator points them; the model's
  environment then drops `OTEL_*` and `HONEYCOMB_*`, so the exporter's
  credentials live in the server entry only. Unset, nothing changes.

The model is a parameter: `CLAUDE_E2E_MODEL`, default `haiku` (the alias, so
the default follows the current Haiku; each record names the exact model the
CLI reports). Decision D38 in docs/spec/decisions.md. A loader-parsed helper,
never a test module.
"""

import json
import os
import subprocess
from pathlib import Path

MCP_SERVER = "mcp-for-kibana"
DEFAULT_MODEL = "haiku"
DEFAULT_RUNS = 5  # the pre-registered N per task (scripts/experiment/CLAUDE_E2E.md)
DEFAULT_BUDGET_USD = 5.0  # cumulative stop for one pytest session
RUN_BUDGET_USD = 0.75  # --max-budget-usd per run: a runaway turn ends there
TIMEOUT_S = 900
REDACTED = "[REDACTED:kibana-api-key]"

# Variables of a parent Claude Code session that would leak its identity or
# extra context into the measured run (as in efficiency_run.py).
_PARENT_SESSION_VARS = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_ADDITIONAL_DIRECTORIES",
    "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD",
)


def _fail(message):
    import pytest

    pytest.fail(message)


def claude_model(environ=os.environ):
    return environ.get("CLAUDE_E2E_MODEL", "").strip() or DEFAULT_MODEL


def runs_per_task(environ=os.environ):
    raw = environ.get("CLAUDE_E2E_RUNS", "").strip()
    if not raw:
        return DEFAULT_RUNS
    if not raw.isdigit() or int(raw) < 1:
        _fail(f"CLAUDE_E2E_RUNS={raw!r} is not a whole number of runs — set it to 1 or more")
    return int(raw)


def budget_usd(environ=os.environ):
    raw = environ.get("CLAUDE_E2E_BUDGET_USD", "").strip()
    if not raw:
        return DEFAULT_BUDGET_USD
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value <= 0:
        _fail(f"CLAUDE_E2E_BUDGET_USD={raw!r} is not a positive dollar amount — e.g. 5")
    return value


# The server's span export (src/kibana_mcp/telemetry.py) reads these; the
# truthy spellings are the ones pydantic accepts for Settings.otel_enabled.
_OTEL_SWITCH = "KIBANA_MCP_OTEL_ENABLED"
_TRUTHY = {"1", "true", "yes", "on", "t", "y"}
_SERVER_TELEMETRY_PREFIXES = ("KIBANA_MCP_OTEL_", "OTEL_")
# Held by the server entry, never by the model, while telemetry is passed through.
_MODEL_TELEMETRY_PREFIXES = ("OTEL_", "HONEYCOMB_")


def telemetry_enabled(environ=os.environ):
    return environ.get(_OTEL_SWITCH, "").strip().lower() in _TRUTHY


def server_telemetry_env(environ=os.environ):
    """The server `env` additions that make it export its spans: every
    KIBANA_MCP_OTEL_* and OTEL_* variable of the runner, when the switch is
    on; {} otherwise (the default: the server entry is unchanged)."""
    if not telemetry_enabled(environ):
        return {}
    return {
        k: v for k, v in environ.items() if k.upper().startswith(_SERVER_TELEMETRY_PREFIXES)
    }


def model_env(environ, api_key):
    """The environment `claude` (and so the model) runs with: no KIBANA*
    variable — the server's configuration comes from its MCP entry alone, so
    an exported KIBANA_MCP_TOOLS cannot change the surface either — no value
    holding the key, and no parent-session variables. While telemetry is
    passed through to the server, no OTEL_* or HONEYCOMB_* variable either."""
    tele = telemetry_enabled(environ)
    return {
        k: v
        for k, v in environ.items()
        if not k.upper().startswith("KIBANA")
        and k not in _PARENT_SESSION_VARS
        and not (api_key and api_key in v)
        and not (tele and k.upper().startswith(_MODEL_TELEMETRY_PREFIXES))
    }


def write_mcp_config(
    path, repo_root, kibana_url, api_key, toolboxes, telemetry_env=None, tier="write"
):
    """The private MCP config. `telemetry_env` (server_telemetry_env()) is
    merged first, so it can never override the URL, key, toolboxes or tier.
    `tier` is `write` for every task but the destructive precision task
    (tests/_toolbox_tasks.py, D61)."""
    cfg = {
        "mcpServers": {
            MCP_SERVER: {
                "command": "uv",
                "args": ["--directory", str(repo_root), "run", "mcp-for-kibana"],
                "env": {
                    **(telemetry_env or {}),
                    "KIBANA_URL": kibana_url,
                    "KIBANA_API_KEY": api_key,
                    "KIBANA_MCP_TOOLBOXES": toolboxes,
                    "KIBANA_MCP_TIER": tier,
                },
            }
        }
    }
    path = Path(path)
    path.touch(mode=0o600)
    path.chmod(0o600)
    path.write_text(json.dumps(cfg))
    return path


def command(prompt, model, mcp_config, run_budget_usd=RUN_BUDGET_USD):
    return [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",  # stream-json in print mode requires it
        "--restricted",
        "--disable-slash-commands",
        "--tools", "",
        "--strict-mcp-config",
        "--mcp-config", str(mcp_config),
        "--no-session-persistence",
        "--permission-mode", "dontAsk",
        "--allowedTools", f"mcp__{MCP_SERVER}",
        "--max-budget-usd", str(run_budget_usd),
    ]


def parse_stream(stdout):
    """Metrics of one stream-json transcript, from its init and first result
    event (the model's own end of turn)."""
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    init = next((e for e in events if e.get("subtype") == "init"), {})
    result = next((e for e in events if e.get("type") == "result"), {})
    tools = init.get("tools") or []
    uses = [
        b
        for e in events
        if e.get("type") == "assistant"
        for b in (e.get("message") or {}).get("content", [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    calls = [b.get("name", "?") for b in uses]
    denied = {
        e.get("tool_use_id")
        for e in events
        if e.get("type") == "system" and e.get("subtype") == "permission_denied"
    }
    denied.update(d.get("tool_use_id") for d in result.get("permission_denials") or [])
    denied.discard(None)
    usage = result.get("usage") or {}
    prefix = f"mcp__{MCP_SERVER}__"
    return {
        "init_model": init.get("model"),
        "permission_mode": init.get("permissionMode"),
        "mcp_servers": init.get("mcp_servers"),
        "offered_tools": sorted(t.removeprefix(prefix) for t in tools if t.startswith(prefix)),
        "non_server_tools": sorted(t for t in tools if not t.startswith(prefix)),
        "tool_calls": [c.removeprefix(prefix) for c in calls],
        # What each call was given, in call order: the precision task scores
        # the id its delete used (tests/_toolbox_tasks.py).
        "tool_inputs": [
            {"name": b.get("name", "?").removeprefix(prefix), "input": b.get("input")}
            for b in uses
        ],
        "n_tool_calls": len(calls),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "models_reported": sorted((result.get("modelUsage") or {}).keys()),
        "permission_denials": len(denied),
        "is_error": result.get("is_error"),
        "terminal_reason": result.get("subtype"),
        "final_text": result.get("result"),
        "has_result": bool(result),
    }


def redact(text, api_key):
    """(text with every occurrence of the key replaced, whether it held one)."""
    if not api_key or api_key not in text:
        return text, False
    return text.replace(api_key, REDACTED), True


def run_claude(prompt, model, workdir, mcp_config, api_key, environ=os.environ):
    """Run one turn. Returns (metrics, transcript, stderr, key_leaked, timed_out);
    transcript and stderr are already redacted."""
    try:
        proc = subprocess.run(
            command(prompt, model, mcp_config),
            cwd=workdir,
            env=model_env(environ, api_key),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        stdout, stderr, timed_out = proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        timed_out = True
    stdout, leaked_out = redact(stdout, api_key)
    stderr, leaked_err = redact(stderr, api_key)
    return parse_stream(stdout), stdout, stderr, leaked_out or leaked_err, timed_out
