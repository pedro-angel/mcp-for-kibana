"""Smoke test of the live-telemetry path: one short `claude -p` run with every
producer on (Claude Code, the MCP server, the agent-signals hook, the runner's
root span), then checks on what the relay received.

    uv run python scripts/experiment/live_smoke.py --dataset mcp-for-kibana-smoke-live
    uv run python scripts/experiment/live_smoke.py --telemetry-endpoint none   # local only

The probe prompt makes three calls whose signals are known in advance: a Bash
curl that gets an HTTP 404 and exits 0 (agent.tool.hidden_failure), an MCP
create_dashboard call with an invalid panel (first_attempt_failed, class
validation, and the server's own ValidationError span) and one valid
create_dashboard retry (retry_of / recovers). Kibana is reset afterwards.
Prints a PASS/FAIL line per check and the trace id; the relay's copy is kept
under --out.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import efficiency_run as er  # noqa: E402
import live_telemetry as lt  # noqa: E402
import otlp_sink  # noqa: E402

PROBE = (
    "Do exactly these steps and nothing else.\n"
    '1) With Bash run: curl -s -H "Authorization: ApiKey $KIBANA_TEST_API_KEY" '
    '"$KIBANA_URL/api/saved_objects/dashboard/does-not-exist"; echo\n'
    "2) Call the mcp-for-kibana create_dashboard tool once with title "
    "'{marker} probe' and panels set to [{{\"type\": \"bogus\"}}].\n"
    "3) Call create_dashboard again with title '{marker} probe' and a single "
    "markdown panel whose text is 'probe' (read the tool's schema for the shape).\n"
    "Then reply: done"
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--dataset", default="mcp-for-kibana-smoke-live")
    ap.add_argument("--telemetry-endpoint", default="https://api.eu1.honeycomb.io")
    ap.add_argument("--out", type=Path, default=HERE / "runs" / "efficiency" / "live-smoke")
    args = ap.parse_args()

    endpoint = None if args.telemetry_endpoint == "none" else args.telemetry_endpoint
    hc_key = os.environ.get("HONEYCOMB_API_KEY") if endpoint else None
    if endpoint and not hc_key:
        raise SystemExit("FAIL: HONEYCOMB_API_KEY is not set — export it, or pass --telemetry-endpoint none")
    kibana_url, api_key = er.load_seed()
    kb = er.Kibana(kibana_url, api_key)
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-smoke-{os.urandom(3).hex()}"
    marker = f"EFF-{os.urandom(4).hex()}"
    run_dir = args.out / run_id
    run_dir.mkdir(parents=True)
    trace_id, root_id = lt.new_trace()
    attrs = lt.resource_attributes(run_id, args.model, "with-mcp", "smoke")
    relay = lt.Relay(
        run_dir / "telemetry.jsonl",
        lt.Scrubber([api_key, hc_key or ""]),
        upstream=endpoint,
        api_key=hc_key,
        dataset=args.dataset,
        agent_name="claude-code with-mcp",
        known=api_key,
    ).start()
    before = kb.snapshot()
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="eff-smoke-") as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "work").mkdir()
        cfg = er.write_hook_config(tmp, relay.url, args.dataset, attrs, api_key)
        mcp = er.mcp_config(
            tmp / "mcp.json", kibana_url, api_key, lt.server_env(relay.url, args.dataset, attrs)
        )
        cmd = [
            "claude", "-p", PROBE.format(marker=marker), "--model", args.model,
            "--output-format", "stream-json", "--verbose", "--restricted",
            "--disable-slash-commands", "--tools", er.BUILTIN_TOOLS, "--strict-mcp-config",
            "--no-session-persistence", "--permission-mode", "dontAsk",
            "--allowedTools", "mcp__mcp-for-kibana", *er.ALLOWED_TOOLS,
            "--mcp-config", str(mcp), "--settings", json.dumps(er.hook_settings(cfg)),
        ]  # fmt: skip
        env = {k: v for k, v in os.environ.items() if k not in er._STRIP_ENV}
        env.update({"KIBANA_URL": kibana_url, "KIBANA_TEST_API_KEY": api_key})
        env = lt.claude_env(env, relay.url, args.dataset, attrs, lt.traceparent(trace_id, root_id))
        proc = subprocess.run(
            cmd, cwd=tmp / "work", env=env, capture_output=True, text=True,
            timeout=600, check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        t_agent = time.time()
        rollups = er.read_hook_rollups(cfg, api_key, run_dir)
    (run_dir / "transcript.jsonl").write_text(proc.stdout)
    record = {"run_id": run_id, "arm": "with-mcp", "block": "smoke", "model_requested": args.model}
    record.update({k: v for k, v in er.parse_stream(proc.stdout.splitlines(), True).items() if k != "events"})
    removed = kb.reset_to(before)
    record["success"] = bool(removed["objects"])
    record["agent_live"] = rollups
    tel = {"trace_id": trace_id, "root_id": root_id, "attrs": attrs, "relay": relay}
    phases = {"agent": (t0, t_agent), "reset": (t_agent, time.time())}
    print(er.finish_live(record, tel, phases, args.dataset))
    (run_dir / "record.json").write_text(json.dumps(record, indent=2))

    recs = otlp_sink.load(run_dir / "telemetry.jsonl")
    spans = otlp_sink.spans(recs)
    hook = [s for s in spans if s["resource"].get("study.component") == "hook"]
    server = [s for s in spans if s["resource"].get("study.component") == "mcp-server"]
    tee = (run_dir / "telemetry.jsonl").read_text()
    ok = all(
        [
            check("every span in the run's trace", all(s["traceId"] == trace_id for s in spans),
                  f"{len(spans)} spans"),
            check("claude_code.interaction under the root",
                  any(s["name"] == "claude_code.interaction" and s.get("parentSpanId") == root_id
                      for s in spans)),
            check("server spans under the root", bool(server) and all(
                s.get("parentSpanId") == root_id for s in server), f"{len(server)} spans"),
            check("hook span per tool call", len([s for s in hook if s["name"].startswith("agent.tool ")])
                  == record.get("n_tool_calls"), f"{len(hook)} hook spans"),
            check("hidden failure on the curl 404", any(
                s["attrs"].get("agent.tool.hidden_failure") is True
                and s["attrs"].get("agent.tool.http_status") in ("404", 404) for s in hook)),
            check("first create_dashboard: first_attempt_failed, validation", any(
                s["attrs"].get("gen_ai.tool.name", "").endswith("create_dashboard")
                and s["attrs"].get("agent.tool.first_attempt_failed") is True
                and s["attrs"].get("agent.tool.error_class") == "validation" for s in hook)),
            check("hook output never reached the transcript",
                  '"hook_response"' not in proc.stdout or all(
                      json.loads(line).get("stdout", "") == "" for line in proc.stdout.splitlines()
                      if '"hook_response"' in line)),
            check("no hook errors", not (run_dir / "hook_errors.txt").exists()),
            check("Kibana key absent from the relay's copy", api_key not in tee),
            check("no raw API bodies", "api_request_body" not in tee and "api_response_body" not in tee),
            check("relay forwarded everything", relay.stats["failed"] == 0, json.dumps(relay.stats)[:200]),
        ]
    )  # fmt: skip
    print(f"trace {trace_id}  run {run_id}  hook p50 {rollups.get('agent.hook.p50_ms')} ms "
          f"max {rollups.get('agent.hook.max_ms')} ms")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
