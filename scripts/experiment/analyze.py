"""Generate the server-usability report tables from the run JSONL.

    uv run python scripts/experiment/analyze.py

Every table in docs/server-usability.md comes from this script over
scripts/experiment/runs/*.jsonl — no hand-transcribed
numbers (pre-registration: the redo plan, Phase 4). Prints Markdown to
stdout; the report author pastes verbatim.

Statistics, frozen in the plan:
- pass counts per cell with exact Clopper-Pearson 95% intervals (shown for
  width, not rank) — computed dependency-free by bisecting the binomial
  tail (math.comb), which IS the Beta quantile for integer parameters;
- medians per cell for turns / tool calls / output tokens / USD / seconds;
- failure taxonomy by first missing ladder rung (aborts — runs that died
  before scoring, e.g. tool_format_generation_error or a 600 s timeout —
  reported as their own class).
"""

import json
import statistics
from math import comb
from pathlib import Path

_RUNS = Path(__file__).resolve().parent / "runs"

RUNGS = ("s1_space", "s2_data_view", "s3_dashboard", "s4_panel")


def _binom_tail_ge(n: int, k: int, p: float) -> float:
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def _binom_tail_le(n: int, k: int, p: float) -> float:
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(0, k + 1))


def _bisect(fn, target, lo=0.0, hi=1.0, iters=60):
    for _ in range(iters):
        mid = (lo + hi) / 2
        if fn(mid) > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact two-sided CI for a binomial proportion."""
    lower = 0.0 if k == 0 else _bisect(lambda p: _binom_tail_ge(n, k, p), alpha / 2)
    upper = 1.0 if k == n else _bisect(lambda p: 1 - _binom_tail_le(n, k, p), 1 - alpha / 2)
    return lower, upper


def _load(name: str) -> list[dict]:
    path = _RUNS / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _fail_class(r: dict) -> str:
    if "passed" not in r:
        return "abort (timeout)" if r.get("duration_s", 0) >= 595 else "abort (runtime error)"
    if r.get("passed"):
        return "pass"
    rung = r.get("first_missing")
    if rung and r.get("default_space_contamination"):
        return f"{rung} (wrong space: built in default)"
    return rung or "unknown"


def _ci_str(k: int, n: int) -> str:
    lo, hi = clopper_pearson(k, n)
    return f"{k}/{n} [{lo:.2f}, {hi:.2f}]"


def study_l(records: list[dict]) -> None:
    runs = [r for r in records if r.get("study") == "L"]
    loads = {r["model"]: r for r in records if r.get("study") == "L-load"}
    # The dry-run record predates every L-load line; study runs are the ones
    # at or after their model's load timestamp.
    per: dict[str, list[dict]] = {}
    for r in runs:
        if r["model"] in loads and r["ts_start"] >= loads[r["model"]]["ts"]:
            per.setdefault(r["model"], []).append(r)
    print("## Study L — local models, 5 runs each\n")
    print("| Model | Passed (95% CI) | Load s | Run seconds | Failure classes |")
    print("|---|---|--:|---|---|")
    for model, rs in per.items():
        k = sum(1 for r in rs if r.get("passed"))
        secs = " / ".join(str(int(r["duration_s"])) for r in rs)
        fails = sorted({_fail_class(r) for r in rs if _fail_class(r) != "pass"})
        print(
            f"| `{model}` | {_ci_str(k, len(rs))} | {loads[model].get('load_s', '—')} "
            f"| {secs} | {'; '.join(fails) if fails else '—'} |"
        )
    print()


def study_c(records: list[dict]) -> None:
    runs = [r for r in records if r.get("study") == "C" and r.get("block", "").startswith("b")]
    cells: dict[tuple[str, str], list[dict]] = {}
    for r in runs:
        cells.setdefault((r["model"], r["arm"]), []).append(r)
    print("## Study C — Claude arms, 5 runs per cell (medians)\n")
    print("| Model | Arm | Passed (95% CI) | Turns | Tool calls | MCP calls | USD | Seconds | Denials |")
    print("|---|---|---|--:|--:|--:|--:|--:|--:|")
    _ARM_ORDER = {"with-mcp": 0, "no-mcp": 1, "with-mcp-directed": 2}
    for (model, arm), rs in sorted(cells.items(), key=lambda kv: (kv[0][0], _ARM_ORDER.get(kv[0][1], 9))):
        k = sum(1 for r in rs if r.get("passed"))

        def med(key, rs=rs):
            vals = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
            return round(statistics.median(vals), 3) if vals else "—"

        calls = [len(r.get("tool_calls") or []) for r in rs]
        print(
            f"| `{model}` | {arm} | {_ci_str(k, len(rs))} | {med('num_turns')} "
            f"| {statistics.median(calls) if calls else '—'} | {med('mcp_calls')} "
            f"| {med('total_cost_usd')} | {round(med('duration_s')) if isinstance(med('duration_s'), (int, float)) else '—'} "
            f"| {sum(r.get('permission_denials', 0) for r in rs)} |"
        )
    print()
    taxonomy: dict[str, int] = {}
    for r in runs:
        c = _fail_class(r)
        if c != "pass":
            taxonomy[c] = taxonomy.get(c, 0) + 1
    if taxonomy:
        print("### Study C failure taxonomy\n")
        for c, n in sorted(taxonomy.items(), key=lambda kv: -kv[1]):
            print(f"- {c}: {n}")
        print()


def main() -> None:
    study_l(_load("study-l.jsonl"))
    study_c(_load("study-c.jsonl"))


if __name__ == "__main__":
    main()
