#!/usr/bin/env python3
"""Check the facts on the landing page (index.html) against their sources.

Facts that go stale are marked in index.html with a data-fact attribute. This
script works out what each one must say and fails if the page disagrees:

- server facts (version, Python floor, supported Kibana versions, toolbox
  names, tools per toolbox and per tier, default toolboxes) from the
  mcp-for-kibana source at SERVER_TAG: pyproject.toml, README.md, the toolbox
  registry, each toolbox's @mcp.tool declarations and config.py;
- measured numbers (cost and time ratios with 95% intervals, runs, warm runs,
  cold-start result, study versions) from studies/mcp-savings/study/data/.

Standard library only (Python 3.11+). Usage, from this folder:

    python3 check_facts.py [--server-repo PATH]   # exit 1 on any mismatch
    python3 check_facts.py --self-test            # shows it catches wrong facts

The server source is read with `git show SERVER_TAG:<path>` from --server-repo
(or $MCP_FOR_KIBANA_REPO, or the git repository holding this file if it has
the tag), else fetched from GitHub at the tag.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

SERVER_TAG = "v0.4.0"  # the one place the pinned server release is set
SERVER_GITHUB = "pedro-angel/mcp-for-kibana"

HERE = Path(__file__).resolve().parent
PAGE = HERE / "index.html"
STUDY = HERE / "studies" / "mcp-savings" / "study"
WITH, WITHOUT = "with-mcp", "no-mcp"
TIERS = ("read", "write", "destructive")


# ---------------------------------------------------------------- server source


def server_reader(repo: str | None):
    """Return read(path) -> text of that file at SERVER_TAG."""
    candidates = [repo, os.environ.get("MCP_FOR_KIBANA_REPO"), str(HERE)]
    for c in candidates:
        if not c:
            continue
        ok = subprocess.run(
            ["git", "-C", c, "rev-parse", "-q", "--verify", f"refs/tags/{SERVER_TAG}"],
            capture_output=True,
        )
        if ok.returncode == 0:
            return lambda path, c=c: subprocess.run(
                ["git", "-C", c, "show", f"{SERVER_TAG}:{path}"],
                capture_output=True, text=True, check=True,
            ).stdout
        if c == repo:
            sys.exit(f"--server-repo {repo}: tag {SERVER_TAG} not found (git fetch origin tag {SERVER_TAG})")

    def fetch(path: str) -> str:
        url = f"https://raw.githubusercontent.com/{SERVER_GITHUB}/{SERVER_TAG}/{path}"
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode()

    return fetch


def one(pattern: str, text: str, what: str, flags: int = 0) -> re.Match:
    m = re.search(pattern, text, flags)
    if not m:
        raise SystemExit(f"source changed: cannot find {what} ({pattern!r})")
    return m


def server_facts(read) -> dict[str, object]:
    py = tomllib.loads(read("pyproject.toml"))["project"]
    version = py["version"]
    if f"v{version}" != SERVER_TAG:
        raise SystemExit(f"pyproject version {version} does not match tag {SERVER_TAG}")
    python = one(r"^>=(\d+\.\d+)$", py["requires-python"], "requires-python").group(1)

    readme = read("README.md")
    kibana = one(r"supports \*\*Kibana (\d+\.\d+ and \d+\.\d+)\*\*", readme, "Kibana versions").group(1)

    registry = read("src/kibana_mcp/toolboxes/__init__.py")
    module_of = {cls: mod for mod, cls in re.findall(r"^from kibana_mcp\.toolboxes\.(\w+)\.toolbox import (\w+)$", registry, re.M)}
    names = re.findall(r'^\s+"([a-z-]+)": (\w+)\(\),$', registry, re.M)
    if not names:
        raise SystemExit("source changed: no toolboxes found in the registry")

    # tools per toolbox and per tier, counted from the @mcp.tool declarations
    per_box: dict[str, int] = {}
    per_tier: dict[str, int] = defaultdict(int)
    for name, cls in names:
        src = read(f"src/kibana_mcp/toolboxes/{module_of[cls]}/toolbox.py")
        decls = re.findall(r"@mcp\.tool\(tags=\{([^}]*)\}", src)
        if len(decls) != src.count("@mcp.tool("):
            raise SystemExit(f"source changed: unrecognised @mcp.tool form in {name}")
        per_box[name] = len(decls)
        for d in decls:
            tier = [t for t in TIERS if f'"{t}"' in d]
            if len(tier) != 1:
                raise SystemExit(f"source changed: tool without one tier in {name}: {d}")
            per_tier[tier[0]] += 1
    total = sum(per_box.values())

    # the README must agree with the source (else the page would cite a README
    # that contradicts the code)
    rtotal, rboxes = map(int, one(r"\*\*(\d+) tools across (\d+) toolboxes\*\*", readme, "tool total").groups())
    rtiers = tuple(map(int, one(r"read / write /\s*destructive \((\d+) / (\d+) / (\d+)\)", readme, "tier split").groups()))
    rtable = {n: int(c) for n, c in re.findall(r"^\| `([a-z-]+)` \| (\d+) \|", readme, re.M)}
    problems = []
    if (rtotal, rboxes) != (total, len(per_box)):
        problems.append(f"README says {rtotal} tools in {rboxes} toolboxes; source has {total} in {len(per_box)}")
    if rtiers != tuple(per_tier[t] for t in TIERS):
        problems.append(f"README tier split {rtiers}; source {tuple(per_tier[t] for t in TIERS)}")
    if rtable != per_box:
        problems.append(f"README toolbox table {rtable}; source {per_box}")
    if problems:
        raise SystemExit("server source disagrees with its README at " + SERVER_TAG + ":\n  " + "\n  ".join(problems))

    config = read("src/kibana_mcp/config.py")
    default = one(r"^\s+toolboxes: .*= \[([^\]]*)\]", config, "default toolboxes", re.M).group(1)
    default_boxes = ",".join(re.findall(r'"([a-z-]+)"', default))

    facts: dict[str, object] = {
        "version": version,
        "python": python,
        "kibana": kibana,
        "package": py["name"],
        "tools-total": str(total),
        "toolbox-count": str(len(per_box)),
        "default-toolboxes": default_boxes,
        "toolbox": [n for n, _ in names],  # each toolbox exactly once
    }
    facts.update({f"tier-{t}": str(per_tier[t]) for t in TIERS})
    facts.update({f"toolbox-tools:{n}": str(c) for n, c in per_box.items()})
    return facts


# ---------------------------------------------------------------- study data


def model_name(m: str) -> str:
    """Display name, as the study page prints it (same rule as build_site.py)."""
    parts = m.split("-")
    return parts[1].capitalize() + " " + ".".join(parts[2:])


def names(ms: list[str]) -> str:
    n = [model_name(m) for m in ms]
    return n[0] if len(n) == 1 else ", ".join(n[:-1]) + " and " + n[-1]


def study_facts() -> dict[str, object]:
    a = json.loads((STUDY / "data" / "analysis.json").read_text())
    x = json.loads((STUDY / "data" / "exploratory.json").read_text())
    models = json.loads((STUDY / "harness" / "mcp_savings.json").read_text())["study"]["models"]
    facts: dict[str, object] = {}
    for m in models:
        for key, short in (("cost_usd", "cost"), ("time_s", "time")):
            r = a["ratios"][m][key]
            lo, hi = r["ratio_ci95"]
            facts[f"{short}:{model_name(m)}"] = f"{r['ratio']:.2f}"
            facts[f"{short}-ci:{model_name(m)}"] = f"{lo:.2f}–{hi:.2f}"
    # warm runs: counted minus the cold ones, as build_site.py counts them
    cold = x["cold_start"]
    n_cold = sum(bool(c["cold"]) + len(c["others_at_floor"]) for c in cold["cells"].values())
    facts["runs"] = str(a["counted"])
    facts["warm"] = str(a["counted"] - n_cold)
    facts["cold-costlier"] = names([m for m in models if cold["ratios"][m]["cold_cost_usd"] > 1])
    facts["study-kibana"] = a["kibana_versions"]["values"][0]
    facts["study-server"] = a["server_versions"][WITH]["values"][0]
    return facts


# ---------------------------------------------------------------- page


class FactParser(HTMLParser):
    """Collect (data-fact key, text content) for every marked element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.open: list[list] = []  # [key, text parts]
        self.facts: list[tuple[str, str]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        key = dict(attrs).get("data-fact")
        self.stack.append((tag, key))
        if key is not None:
            self.open.append([key, []])

    def handle_endtag(self, tag):
        while self.stack:
            t, key = self.stack.pop()
            if key is not None:
                k, parts = self.open.pop()
                self.facts.append((k, " ".join("".join(parts).split())))
            if t == tag:
                break

    def handle_data(self, data):
        self.text.append(data)
        for o in self.open:
            o[1].append(data)


def check(page: str, expected: dict[str, object]) -> list[str]:
    p = FactParser()
    p.feed(page)
    found: dict[str, list[str]] = defaultdict(list)
    for k, v in p.facts:
        found[k].append(v)
    errors = []
    for k in sorted(set(found) - set(expected)):
        errors.append(f"{k}: on the page but no source checks it")
    for k, want in expected.items():
        got = found.get(k, [])
        if isinstance(want, list):
            if sorted(got) != sorted(want):
                errors.append(f"{k}: page lists {sorted(got)}, source has {sorted(want)}")
        elif not got:
            errors.append(f"{k}: missing from the page (source says {want!r})")
        else:
            errors += [f"{k}: page says {g!r}, source says {want!r}" for g in got if g != want]
    # any x.y.z version anywhere in the text must be one the sources give
    allowed = {expected["version"], expected["study-kibana"], expected["study-server"]}
    for v in sorted(set(re.findall(r"\b\d+\.\d+\.\d+\b", " ".join(p.text))) - allowed):
        errors.append(f"version {v} appears on the page but is not a checked fact")
    return errors


def expected_facts(repo: str | None) -> dict[str, object]:
    return {**server_facts(server_reader(repo)), **study_facts()}


def self_test(expected: dict[str, object]) -> int:
    page = PAGE.read_text()
    assert check(page, expected) == [], "the real page must pass first"
    mutations = {
        "wrong version": ('data-fact="version">', '0.4.0', '0.3.0'),
        "wrong Kibana versions": ('data-fact="kibana">', '9.4 and 9.5', '9.3 and 9.4'),
        "wrong cost ratio": ('data-fact="cost:Opus 5.5">', '0.56', '0.50'),
        "wrong interval": ('data-fact="cost-ci:Haiku 5.5">', '0.29–0.32', '0.28–0.32'),
        "missing toolbox": ('<code data-fact="toolbox">', 'fleet', 'fleet-x'),
        "wrong tool count": ('data-fact="toolbox-tools:cases">', '6', '7'),
    }
    failed = 0
    for label, (anchor, old, new) in mutations.items():
        assert page.count(anchor + old) >= 1, (label, anchor + old)
        bad = page.replace(anchor + old, anchor + new, 1)
        errs = check(bad, expected)
        ok = bool(errs)
        failed += not ok
        print(f"{'caught' if ok else 'MISSED'}: {label}" + (f" -> {errs[0]}" if ok else ""))
    stray = page.replace("</footer>", "<p>Now on 0.9.9.</p></footer>", 1)
    errs = check(stray, expected)
    failed += not errs
    print(f"{'caught' if errs else 'MISSED'}: unchecked version in text" + (f" -> {errs[0]}" if errs else ""))
    print("self-test " + ("PASS" if not failed else f"FAIL ({failed} missed)"))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server-repo", help=f"a mcp-for-kibana clone that has tag {SERVER_TAG}")
    ap.add_argument("--self-test", action="store_true", help="show that wrong facts fail the check")
    args = ap.parse_args()
    expected = expected_facts(args.server_repo)
    if args.self_test:
        return self_test(expected)
    errors = check(PAGE.read_text(), expected)
    if errors:
        print(f"check_facts: FAIL against {SERVER_TAG} and the study data")
        for e in errors:
            print("  " + e)
        return 1
    n = sum(len(v) if isinstance(v, list) else 1 for v in expected.values())
    print(f"check_facts: PASS, {n} facts on index.html agree with mcp-for-kibana {SERVER_TAG} and the study data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
