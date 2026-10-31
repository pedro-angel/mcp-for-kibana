#!/usr/bin/env python3
"""The four tasks of studies 4 and 5, shared by both runners: prompts, the
seeded starting state, and the success checks.

    uv run python scripts/experiment/study_tasks.py prompt <task> <arm>
    uv run python scripts/experiment/study_tasks.py seed
    uv run python scripts/experiment/study_tasks.py replay [--runs DIR ...]

Every task value lives in ``tasks.json``. A check compares against values
Kibana or Elasticsearch hold, never against the model's wording; an answer
line counts only when it matches Kibana's actual value.

Scoring has two halves. ``collect_state`` reads Kibana and Elasticsearch
once, after the run: the run's dashboards and rules, the seeded objects, the
data views, the indices' time-field mapping, and an evaluation of every
rule's filter. ``score`` is pure: it takes that state and the model's final
text and returns one criterion per check, each with its expected and actual
value. ``span_attributes`` flattens the criteria for the run's root span, so
a failed run can be explained from Honeycomb alone.

A rule's filter is evaluated by running the rule's own query (Elasticsearch
DSL, KQL with filters, the index-threshold KQL filter, or ES|QL) twice: on
the task's real index, and on a probe index holding one document per
interesting value (500, 502, 503, 504, 599 against 200, 301, 404, 450 for
web logs). It passes when it selects exactly the matching documents on both:
the real data alone cannot tell a 5xx filter from a 503 filter, because 503
is the only 5xx code in the sample. The probe index exists only while a run
is scored.

The ``replay`` command scores the rules of stored runs (runs/efficiency,
runs/local) against the live stack and reports which filters and time
fields pass, so the checks are tested on real model output before a pilot.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import efficiency_run as er  # noqa: E402  (sibling script)

TASKS = HERE / "tasks.json"
ARMS = ("server", "api")
SNIPPET = 200

# Probe documents per data set: the field values that separate a correct
# filter from a near miss.
PROBES = {
    "logs": [
        {"response": v, "host": "probe.example", "@timestamp": "2026-01-01T00:00:00Z"}
        for v in ("500", "502", "503", "504", "599", "200", "301", "404", "450")
    ],
    "flights": [
        {"Cancelled": c, "Carrier": "Probe Air", "timestamp": "2026-01-01T00:00:00Z"}
        for c in (True, True, True, False, False, False)
    ],
}


def load(path: Path = TASKS) -> dict:
    return json.loads(path.read_text())


def task(cfg: dict, task_id: str) -> dict:
    for t in cfg["tasks"]:
        if t["id"] == task_id:
            return t
    raise SystemExit(f"FAIL: no task {task_id!r} in tasks.json (have: {task_ids(cfg)})")


def task_ids(cfg: dict) -> list[str]:
    return [t["id"] for t in cfg["tasks"]]


def context(cfg: dict, t: dict, marker: str) -> dict:
    d = cfg["data"][t["data"]]
    return {"marker": marker, "data_view": d["data_view"], "index": d["index"]}


def build_prompt(cfg: dict, task_id: str, arm: str, marker: str) -> str:
    """The arm's preamble, the task's intro, its numbered steps, its closing."""
    if arm not in cfg["arms"]:
        raise SystemExit(f"FAIL: no arm {arm!r} in tasks.json (have: {sorted(cfg['arms'])})")
    t = task(cfg, task_id)
    ctx = context(cfg, t, marker)
    lines = [cfg["arms"][arm], "", t["intro"].format(**ctx)]
    for i, step in enumerate(t["steps"], 1):
        lines.append(f"{i}. {step['text'].format(**ctx)}")
    lines += ["", t["closing"].format(**ctx)]
    return "\n".join(lines)


# ---------------------------------------------------------------- Elasticsearch


class Elastic:
    """The stack's Elasticsearch as the elastic user. Used only by the
    checker; it reaches no model, server or telemetry."""

    def __init__(self, url: str, password: str):
        self.url = url.rstrip("/")
        token = base64.b64encode(f"elastic:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    @classmethod
    def from_stack(cls) -> Elastic:
        return cls(*er.load_es_admin())

    def call(self, method: str, path: str, body=None, ndjson: str | None = None):
        headers = dict(self.headers)
        data = None
        if ndjson is not None:
            headers["Content-Type"] = "application/x-ndjson"
            data = ndjson.encode()
        elif body is not None:
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode(errors="replace")


def _properties(es: Elastic, index: str) -> dict:
    status, body = es.call("GET", f"/{index}/_mapping")
    if status != 200 or not body:
        return {}
    return next(iter(body.values()))["mappings"].get("properties", {})


def resolve_field(props: dict, field: str | None) -> str | None:
    """The concrete field a name points at: an alias resolves to its path."""
    if not field:
        return None
    node = props.get(field)
    if node is None and "." in field:
        head, _, rest = field.partition(".")
        sub = props.get(head) or {}
        node = (sub.get("properties") or {}).get(rest) or (sub.get("fields") or {}).get(rest)
    if node and node.get("type") == "alias":
        return node.get("path")
    return field if node else None


def truth_query(match: dict) -> dict:
    if "prefix" in match:
        return {"prefix": {match["field"]: match["prefix"]}}
    return {"term": {match["field"]: match["equals"]}}


def probe_name(data_key: str) -> str:
    return f"study-probe-{data_key}"


@contextlib.contextmanager
def probe_index(es: Elastic, cfg: dict, data_key: str):
    """A throwaway index with the real index's mapping and the probe
    documents; deleted on exit."""
    name = probe_name(data_key)
    props = _properties(es, cfg["data"][data_key]["index"])
    es.call("DELETE", f"/{name}")
    status, body = es.call("PUT", f"/{name}", {"mappings": {"properties": props}})
    if status != 200:
        raise SystemExit(f"FAIL: could not create the probe index {name} ({status}): {body}")
    lines = []
    for doc in PROBES[data_key]:
        lines += [json.dumps({"index": {}}), json.dumps(doc)]
    es.call("POST", f"/{name}/_bulk?refresh=true", ndjson="\n".join(lines) + "\n")
    try:
        yield name
    finally:
        es.call("DELETE", f"/{name}")


# ---------------------------------------------------------------- rule filters


def _rule_index(rule: dict, data_views: list[dict]) -> list[str]:
    p = rule.get("params") or {}
    if rule_form(rule) == "esqlQuery":
        m = re.match(r"(?i)\s*from\s+([^\s|]+)", (p.get("esqlQuery") or {}).get("esql") or "")
        return m.group(1).split(",") if m else []
    if p.get("index"):
        idx = p["index"]
        return idx if isinstance(idx, list) else [idx]
    dv_id = (p.get("searchConfiguration") or {}).get("index")
    if isinstance(dv_id, dict):
        return [dv_id.get("title")] if dv_id.get("title") else []
    for dv in data_views:
        if dv.get("id") == dv_id:
            return [dv["title"]]
    return []


def date_fields(props: dict) -> set[str]:
    """Names of the mapping's date fields, aliases to them included."""
    dates = {k for k, v in props.items() if v.get("type") in ("date", "date_nanos")}
    return dates | {
        k for k, v in props.items() if v.get("type") == "alias" and v.get("path") in dates
    }


def strip_time_ranges(node, dates: set[str]):
    """The DSL without range clauses on date fields: the rule's time window
    is checked on its own, and the filter is counted over the whole index."""
    if isinstance(node, list):
        kept = [strip_time_ranges(n, dates) for n in node]
        return [n for n in kept if n is not None]
    if not isinstance(node, dict):
        return node
    if set(node) == {"range"} and isinstance(node["range"], dict) and set(node["range"]) <= dates:
        return None
    out = {}
    for k, v in node.items():
        v2 = strip_time_ranges(v, dates)
        if v2 is not None:
            out[k] = v2
    return out


def _kql_esql(kql: str) -> str:
    return kql.replace("\\", "\\\\").replace('"', '\\"')


def _search_source_filters(conf: dict) -> list[dict]:
    out = []
    for f in conf.get("filter") or []:
        meta = f.get("meta") or {}
        if meta.get("disabled"):
            continue
        q = f.get("query") or {k: v for k, v in f.items() if k not in ("meta", "$state")}
        if not q:
            continue
        out.append({"bool": {"must_not": [q]}} if meta.get("negate") else q)
    return out


def rule_form(rule: dict) -> str:
    p = rule.get("params") or {}
    if rule.get("rule_type_id") == ".es-query":
        return p.get("searchType") or "esQuery"
    if rule.get("rule_type_id") == ".index-threshold":
        return "indexThreshold"
    return rule.get("rule_type_id") or "unknown"


def _esql_body(esql: str, index: str) -> str:
    """The rule's ES|QL up to its first STATS, on `index`, counted."""
    head = re.split(r"\|\s*(?i:stats)\b", esql, maxsplit=1)[0]
    head = re.sub(r"(?i)^\s*from\s+[^\s|]+", f"FROM {index}", head, count=1)
    return head.rstrip() + " | STATS c = COUNT(*)"


def count_rule(
    es: Elastic, rule: dict, index: str, extra: dict | None, dates: set[str] = frozenset()
) -> int | None:
    """How many documents of `index` the rule's own filter selects, narrowed
    by `extra` when given. None when the query cannot run."""
    p = rule.get("params") or {}
    form = rule_form(rule)
    if form == "esQuery":
        try:
            q = json.loads(p.get("esQuery") or "{}").get("query") or {"match_all": {}}
        except ValueError:
            return None
        q = strip_time_ranges(q, set(dates)) or {"match_all": {}}
        body = {"query": {"bool": {"filter": [q] + ([extra] if extra else [])}}}
        status, res = es.call("POST", f"/{index}/_count", body)
        return res.get("count") if status == 200 else None
    if form in ("searchSource", "indexThreshold"):
        if form == "searchSource":
            conf = p.get("searchConfiguration") or {}
            query = conf.get("query") or {}
            text, lang = (query.get("query") or "").strip(), query.get("language", "kuery")
            filters = _search_source_filters(conf)
        else:
            text, lang, filters = (p.get("filterKuery") or "").strip(), "kuery", []
        if text and lang == "lucene":
            filters.append({"query_string": {"query": text}})
            text = ""
        esql = f"FROM {index}"
        if text:
            esql += f' | WHERE KQL("{_kql_esql(text)}")'
        esql += " | STATS c = COUNT(*)"
        dsl = filters + ([extra] if extra else [])
        body = {"query": esql}
        if dsl:
            body["filter"] = {"bool": {"filter": dsl}}
        status, res = es.call("POST", "/_query", body)
        return res["values"][0][0] if status == 200 and res.get("values") else None
    if form == "esqlQuery":
        esql = (p.get("esqlQuery") or {}).get("esql") or ""
        body = {
            "query": _esql_body(esql, index),
            "params": [{"_tstart": "1970-01-01T00:00:00Z"}, {"_tend": "2100-01-01T00:00:00Z"}],
        }
        if extra:
            body["filter"] = extra
        status, res = es.call("POST", "/_query", body)
        return res["values"][0][0] if status == 200 and res.get("values") else None
    return None


def evaluate_filter(
    es: Elastic, cfg: dict, rule: dict, data_key: str, probe: str, data_views: list[dict]
) -> dict:
    """The rule's filter on the real index and the probe index. It passes
    when, on both, it selects exactly the documents that match the task."""
    d = cfg["data"][data_key]
    truth = truth_query(d["match"])
    targets = _rule_index(rule, data_views)
    out = {"form": rule_form(rule), "index": targets}
    if not targets or not any(d["index"] in t for t in targets):
        out.update(ok=False, detail=f"rule reads {targets or 'no index'}, not {d['index']}")
        return out
    dates = date_fields(_properties(es, d["index"]))
    for label, index in (("real", d["index"]), ("probe", probe)):
        _, tc = es.call("POST", f"/{index}/_count", {"query": truth})
        want = (tc or {}).get("count")
        got = count_rule(es, rule, index, None, dates)
        inside = count_rule(es, rule, index, truth, dates)
        out[label] = {"truth": want, "selected": got, "selected_matching": inside}
        if got is None:
            out.update(ok=False, detail=f"rule query does not run on the {label} index")
            return out
        if not (got == inside == want):
            out.update(
                ok=False,
                detail=f"{label} index: filter selects {got} documents, {inside} of them "
                f"matching; {want} match",
            )
            return out
    out.update(ok=True, detail="ok")
    return out


# ---------------------------------------------------------------- seed


def _data_view_id(kb, title: str) -> str | None:
    status, body = kb.call("GET", "/api/data_views")
    for dv in (body or {}).get("data_view", []) if status == 200 else []:
        if dv.get("title") == title:
            return dv["id"]
    return None


def ensure_samples(kb, cfg: dict) -> None:
    """Install the sample data sets the tasks use (only those missing)."""
    status, body = kb.call("GET", "/api/sample_data", internal=True)
    have = {d["id"] for d in body if d.get("status") == "installed"} if status == 200 else set()
    for d in cfg["data"].values():
        if d["sample"] in have:
            continue
        status, res = kb.call("POST", f"/api/sample_data/{d['sample']}", {}, internal=True)
        if status not in (200, 201):
            raise SystemExit(
                f"FAIL: could not load the {d['sample']} sample data ({status}): {res}"
            )


def seed_dashboard_body(cfg: dict, data_view_id: str) -> dict:
    s = cfg["seed"]["dashboard"]
    src = {"type": "data_view_reference", "ref_id": data_view_id}
    panels = []
    for i, p in enumerate(s["panels"]):
        if p["breakdown"]:
            x = {"operation": "terms", "fields": [p["breakdown"]], "limit": 10}
        else:
            x = {"operation": "date_histogram", "field": "timestamp"}
        layer = {"type": p["chart"], "data_source": src, "x": x, "y": [{"operation": "count"}]}
        panels.append(
            {
                "grid": {"x": 24 * (i % 2), "y": 12 * (i // 2), "w": 24, "h": 12},
                "type": "vis",
                "config": {"title": p["title"], "type": "xy", "layers": [layer]},
            }
        )
    return {"title": s["title"], "panels": panels}


def seed_rule_body(cfg: dict) -> dict:
    s = cfg["seed"]["rule"]
    d = cfg["data"][s["data"]]
    return {
        "name": s["name"],
        "rule_type_id": ".es-query",
        "consumer": "stackAlerts",
        "enabled": s["enabled"],
        "schedule": {"interval": s["interval"]},
        "params": {
            "searchType": "esQuery",
            "index": [d["index"]],
            "timeField": "timestamp",
            "esQuery": json.dumps({"query": truth_query(d["match"])}),
            "size": 0,
            "thresholdComparator": ">",
            "threshold": [s["threshold"]],
            "timeWindowSize": s["window_minutes"],
            "timeWindowUnit": "m",
        },
    }


def restore_seed(kb, cfg: dict) -> dict:
    """Recreate the seeded dashboard and rule at their fixed ids, exactly as
    tasks.json defines them, whatever a previous run did to them. Returns
    the restored objects as Kibana holds them."""
    ensure_samples(kb, cfg)
    s = cfg["seed"]
    dv_id = _data_view_id(kb, cfg["data"][s["dashboard"]["data"]]["index"])
    if not dv_id:
        raise SystemExit("FAIL: no data view on the web-logs index; run install-sample first")
    dash_id, rule_id = s["dashboard"]["id"], s["rule"]["id"]
    kb.call("DELETE", f"/api/dashboards/{dash_id}")
    kb.call("DELETE", f"/api/alerting/rule/{rule_id}")
    status, body = kb.call("PUT", f"/api/dashboards/{dash_id}", seed_dashboard_body(cfg, dv_id))
    if status not in (200, 201):
        raise SystemExit(f"FAIL: could not create the seed dashboard ({status}): {body}")
    status, body = kb.call("POST", f"/api/alerting/rule/{rule_id}", seed_rule_body(cfg))
    if status not in (200, 201):
        raise SystemExit(f"FAIL: could not create the seed rule ({status}): {body}")
    return seed_snapshot(kb, cfg)


def seed_snapshot(kb, cfg: dict) -> dict:
    s = cfg["seed"]
    _, dash = kb.call("GET", f"/api/dashboards/{s['dashboard']['id']}")
    status, rule = kb.call("GET", f"/api/alerting/rule/{s['rule']['id']}")
    return {
        "dashboard": dash if isinstance(dash, dict) and dash.get("id") else None,
        "rule": rule if status == 200 else None,
    }


# ---------------------------------------------------------------- state


def collect_state(
    kb,
    es: Elastic,
    cfg: dict,
    task_id: str,
    marker: str,
    before: dict,
    seed_before: dict | None = None,
) -> dict:
    """Everything the checks read, gathered once after the run."""
    t = task(cfg, task_id)
    d = cfg["data"][t["data"]]
    existing = {tuple(o) for o in before["objects"]}
    snap = kb.snapshot()
    new_dash_ids = [i for ty, i in snap["objects"] if ty == "dashboard" and (ty, i) not in existing]
    new_rule_ids = [i for i in snap["rules"] if i not in set(before["rules"])]
    dashboards = []
    for dash_id in new_dash_ids:
        status, full = kb.call("GET", f"/api/dashboards/{dash_id}")
        if status == 200:
            dashboards.append(full)
    rules = []
    for rule_id in new_rule_ids:
        status, rule = kb.call("GET", f"/api/alerting/rule/{rule_id}")
        if status == 200:
            rules.append(rule)
    _, dvs = kb.call("GET", "/api/data_views")
    data_views = (dvs or {}).get("data_view", [])
    seeded = seed_snapshot(kb, cfg) if t["seeded"] else {"dashboard": None, "rule": None}
    props = _properties(es, d["index"])
    state = {
        "task": task_id,
        "marker": marker,
        "dashboards": dashboards,
        "rules": rules,
        "seeded": seeded,
        "seed_before": seed_before,
        "data_views": data_views,
        "time_fields": {
            f: resolve_field(props, f)
            for f in {
                "timestamp",
                "@timestamp",
                "utc_time",
                *[rule_time_field(r, data_views) for r in rules + [seeded["rule"] or {}]],
            }
            if f
        },
        "filters": {},
    }
    to_eval = rules + ([seeded["rule"]] if seeded["rule"] else [])
    if to_eval:
        with probe_index(es, cfg, t["data"]) as probe:
            for rule in to_eval:
                state["filters"][rule["id"]] = evaluate_filter(
                    es, cfg, rule, t["data"], probe, data_views
                )
    return state


# ---------------------------------------------------------------- scoring


def _panels(dash: dict | None) -> list:
    return er._panels(dash) if dash else []


def _title(dash: dict | None) -> str:
    return ((dash or {}).get("data") or dash or {}).get("title") or ""


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def panel_titles(dash: dict | None) -> list[str]:
    return [
        (p.get("config") or {}).get("title")
        or p.get("title")
        or (p.get("library") or {}).get("title")
        or ""
        for p in _panels(dash)
    ]


def uses_data(dash: dict, d: dict, data_views: list[dict]) -> bool:
    """Every chart panel reads the task's index, by name or by a data view on it."""
    ids = {dv["id"] for dv in data_views if d["index"] in (dv.get("title") or "")}
    charts = [p for p in _panels(dash) if p.get("type") != "markdown"]
    if not charts:
        return False
    return all(d["index"] in json.dumps(p) or any(i in json.dumps(p) for i in ids) for p in charts)


_DUR = re.compile(
    r"(?i)^\s*(?:every\s+)?(\d+)?\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hours?)\s*$"
)


def seconds(value) -> int | None:
    m = _DUR.match(str(value or ""))
    if not m:
        return None
    n = int(m.group(1) or 1)
    unit = m.group(2).lower()[0]
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


def threshold_ok(rule: dict, want: int) -> tuple[bool, str]:
    """Fires above `want` documents: `> want` or `>= want+1`; an ES|QL rule
    may carry the comparison in its query instead."""
    p = rule.get("params") or {}
    comp, th = p.get("thresholdComparator"), (p.get("threshold") or [None])[0]
    actual = f"{comp} {th}"
    if (comp, th) in ((">", want), (">=", want + 1)):
        return True, actual
    if rule_form(rule) == "esqlQuery":
        esql = (p.get("esqlQuery") or {}).get("esql") or ""
        tail = re.split(r"\|\s*(?i:stats)\b", esql, maxsplit=1)
        if len(tail) == 2 and re.search(rf"(>\s*{want}\b|>=\s*{want + 1}\b)", tail[1]):
            return True, f"in query: {tail[1].strip()[:80]}"
    return False, actual


def window_minutes(rule: dict) -> float | None:
    p = rule.get("params") or {}
    size, unit = p.get("timeWindowSize"), p.get("timeWindowUnit")
    if size is None or unit not in ("s", "m", "h"):
        return None
    return size * {"s": 1 / 60, "m": 1, "h": 60}[unit]


def rule_checks(spec: dict, rule: dict, state: dict, time_field: str | None) -> list:
    """(name, ok, expected, actual) for each key the spec names."""
    out = []
    p = rule.get("params") or {}
    if "enabled" in spec:
        out.append(
            (
                "enabled",
                bool(rule.get("enabled")) == spec["enabled"],
                spec["enabled"],
                bool(rule.get("enabled")),
            )
        )
    if "interval" in spec:
        got = (rule.get("schedule") or {}).get("interval")
        out.append(("interval", seconds(got) == seconds(spec["interval"]), spec["interval"], got))
    if "threshold" in spec:
        ok, got = threshold_ok(rule, spec["threshold"])
        out.append(("threshold", ok, f"> {spec['threshold']}", got))
    if "window_minutes" in spec:
        got = window_minutes(rule)
        out.append(
            (
                "window",
                got == spec["window_minutes"],
                f"{spec['window_minutes']}m",
                f"{p.get('timeWindowSize')}{p.get('timeWindowUnit')}",
            )
        )
    if spec.get("kind") == "new_rule":
        got = rule_time_field(rule, state["data_views"])
        resolved = state["time_fields"].get(got)
        want = state["time_fields"].get(time_field)
        out.append(("time_field", bool(resolved) and resolved == want, time_field, got))
        f = state["filters"].get(rule.get("id")) or {"ok": False, "detail": "not evaluated"}
        out.append(("filter", f["ok"], "selects exactly the matching documents", f["detail"]))
    return out


def rule_time_field(rule: dict, data_views: list[dict]) -> str | None:
    """The field the rule's time window runs on: its own timeField, or for a
    KQL search without one, its data view's time field."""
    p = rule.get("params") or {}
    if p.get("timeField"):
        return p["timeField"]
    dv_id = (p.get("searchConfiguration") or {}).get("index")
    if isinstance(dv_id, dict):
        return dv_id.get("timeFieldName")
    for dv in data_views:
        if dv.get("id") == dv_id:
            return dv.get("timeFieldName")
    return None


def _dv_time_field(state: dict, d: dict) -> str | None:
    for dv in state["data_views"]:
        if dv.get("title") == d["index"]:
            return dv.get("timeFieldName")
    return None


def answers(text: str, line: str) -> str | None:
    """The value of the last `LINE: value` line, markdown emphasis stripped."""
    pat = re.compile(rf"(?im)^[\s>*_`#-]*{re.escape(line)}[*_`\s]*:[\s*_`'\"]*(.+?)[\s*_`'\".]*$")
    found = pat.findall(text or "")
    return found[-1].strip() if found else None


def _yes(value) -> str:
    return "yes" if value else "no"


def _answer_ok(expect: str, want, got: str | None) -> bool:
    if got is None or want is None:
        return False
    if expect in ("seeded_panels", "seeded_threshold"):
        m = re.search(r"\d+", got)
        return bool(m) and int(m.group()) == int(want)
    if expect == "seeded_interval":
        return seconds(got) is not None and seconds(got) == seconds(want)
    if expect in ("rule_enabled", "seeded_enabled"):
        g = _norm(got).split()[0] if _norm(got) else ""
        return g in ("yes", "no", "true", "false") and (g in ("yes", "true")) == (want == "yes")
    return _norm(got).strip("'\"`") == _norm(want)


def score(cfg: dict, task_id: str, marker: str, state: dict, final_text: str) -> dict:
    """One criterion per check of every step. A step passes when all its
    checks pass; ``steps_passed`` counts the leading passed steps."""
    t = task(cfg, task_id)
    d = cfg["data"][t["data"]]
    ctx = context(cfg, t, marker)
    criteria = []

    def add(step, check, ok, expected, actual, detail=None):
        criteria.append(
            {
                "step": step,
                "check": check,
                "ok": bool(ok),
                "expected": expected,
                "actual": actual,
                "detail": "ok"
                if ok
                else f"{check}: " + (detail or f"expected {expected!r}, got {actual!r}"),
            }
        )

    def named(items, key, name):
        return [
            o for o in items if _norm(_title(o) if key == "title" else o.get("name")) == _norm(name)
        ]

    seeded_dash, seeded_rule = state["seeded"]["dashboard"], state["seeded"]["rule"]
    time_field = _dv_time_field(state, d)

    for step in t["steps"]:
        sid = step["id"]
        for c in step["checks"]:
            kind = c["kind"]
            if kind == "answer":
                got = answers(final_text, c["line"])
                expect = c["expect"]
                if expect == "time_field":
                    want = time_field
                elif expect == "rule_enabled":
                    rs = named(state["rules"], "name", c["rule"].format(**ctx))
                    want = _yes(rs[0].get("enabled")) if rs else None
                elif expect == "seeded_panels":
                    want = len(_panels(seeded_dash)) if seeded_dash else None
                elif expect == "seeded_rule_name":
                    want = (seeded_rule or {}).get("name")
                elif expect == "seeded_threshold":
                    want = ((seeded_rule or {}).get("params") or {}).get("threshold", [None])[0]
                elif expect == "seeded_interval":
                    want = ((seeded_rule or {}).get("schedule") or {}).get("interval")
                elif expect == "seeded_enabled":
                    want = _yes((seeded_rule or {}).get("enabled")) if seeded_rule else None
                else:
                    raise SystemExit(f"FAIL: unknown answer expectation {expect!r}")
                add(
                    sid,
                    f"answer:{c['line']}",
                    _answer_ok(expect, want, got),
                    want,
                    got,
                    None if got is not None else f"no {c['line']}: line in the reply",
                )
            elif kind == "new_dashboard":
                title = c["title"].format(**ctx)
                cands = named(state["dashboards"], "title", title)
                if not cands:
                    add(
                        sid,
                        "dashboard",
                        False,
                        title,
                        [_title(x) for x in state["dashboards"]],
                        f"no new dashboard titled {title!r}",
                    )
                    continue
                best, why = None, []
                for dash in cands:
                    reasons = []
                    n = len(_panels(dash))
                    if n != c["panels"]:
                        reasons.append(f"{n} panels, expected {c['panels']}")
                    if not uses_data(dash, d, state["data_views"]):
                        reasons.append(f"a panel does not read {d['index']}")
                    for word in c.get("panel_mentions", []):
                        if word.casefold() not in json.dumps(_panels(dash)).casefold():
                            reasons.append(f"no panel mentions {word!r}")
                    if not reasons:
                        best = dash
                        break
                    why = why or reasons
                add(
                    sid,
                    "dashboard",
                    best is not None,
                    f"{c['panels']} panels on {d['index']}",
                    f"{len(_panels(cands[0]))} panels",
                    "; ".join(why),
                )
            elif kind == "panel_title":
                dash_title = c["dashboard"].format(**ctx)
                titles = [
                    pt
                    for dash in named(state["dashboards"], "title", dash_title)
                    for pt in panel_titles(dash)
                ]
                add(
                    sid,
                    "panel_title",
                    any(_norm(x) == _norm(c["title"]) for x in titles),
                    c["title"],
                    titles,
                )
            elif kind in ("new_rule", "seeded_rule"):
                if kind == "new_rule":
                    name = c["name"].format(**ctx)
                    cands = named(state["rules"], "name", name)
                    if not cands:
                        add(
                            sid,
                            "rule",
                            False,
                            name,
                            [r.get("name") for r in state["rules"]],
                            f"no new rule named {name!r}",
                        )
                        continue
                else:
                    if not seeded_rule:
                        add(
                            sid,
                            "rule",
                            False,
                            cfg["seed"]["rule"]["id"],
                            None,
                            "the seeded rule no longer exists",
                        )
                        continue
                    cands = [seeded_rule]
                results = [rule_checks(c, r, state, time_field) for r in cands]
                best = max(results, key=lambda rs: sum(ok for _, ok, _, _ in rs))
                for name, ok, want, got in best:
                    add(sid, f"rule.{name}", ok, want, got)
            elif kind == "seeded_dashboard":
                if not seeded_dash:
                    add(
                        sid,
                        "dashboard",
                        False,
                        cfg["seed"]["dashboard"]["id"],
                        None,
                        "the seeded dashboard no longer exists",
                    )
                    continue
                if "panels" in c:
                    n = len(_panels(seeded_dash))
                    add(sid, "dashboard.panels", n == c["panels"], c["panels"], n)
                for word in c.get("panel_mentions", []):
                    ok = word.casefold() in json.dumps(_panels(seeded_dash)).casefold()
                    add(
                        sid,
                        f"dashboard.mentions:{word}",
                        ok,
                        word,
                        panel_titles(seeded_dash),
                        f"no panel mentions {word!r}",
                    )
                if "title" in c:
                    got = _title(seeded_dash)
                    add(sid, "dashboard.title", _norm(got) == _norm(c["title"]), c["title"], got)
            elif kind == "nothing_new":
                n_d, n_r = len(state["dashboards"]), len(state["rules"])
                add(
                    sid,
                    "nothing_new",
                    n_d == 0 and n_r == 0,
                    "0 new dashboards, 0 new rules",
                    f"{n_d} new dashboards, {n_r} new rules",
                )
            elif kind == "nothing_changed":
                changed = _seed_changes(state.get("seed_before") or {}, state["seeded"])
                n_d, n_r = len(state["dashboards"]), len(state["rules"])
                if n_d or n_r:
                    changed.append(f"{n_d} new dashboards, {n_r} new rules")
                add(
                    sid,
                    "nothing_changed",
                    not changed,
                    "no change",
                    changed or "no change",
                    "; ".join(changed),
                )
            else:
                raise SystemExit(f"FAIL: unknown check kind {kind!r} in tasks.json")

    passed_steps = 0
    for step in t["steps"]:
        if all(c["ok"] for c in criteria if c["step"] == step["id"]):
            passed_steps += 1
        else:
            break
    failed = [f"{c['step']}: {c['detail']}" for c in criteria if not c["ok"]]
    return {
        "task": task_id,
        "success": not failed,
        "failure_reason": "; ".join(failed) or None,
        "first_failed_step": next((c["step"] for c in criteria if not c["ok"]), None),
        "steps_passed": passed_steps,
        "steps_total": len(t["steps"]),
        "criteria": criteria,
        "n_dashboards": len(state["dashboards"]),
        "n_rules": len(state["rules"]),
    }


def _seed_view(seeded: dict) -> dict:
    dash, rule = seeded.get("dashboard"), seeded.get("rule")
    return {
        "dashboard": None if not dash else {"title": _title(dash), "panels": panel_titles(dash)},
        "rule": None
        if not rule
        else {
            "name": rule.get("name"),
            "enabled": rule.get("enabled"),
            "schedule": rule.get("schedule"),
            "params": rule.get("params"),
        },
    }


def _seed_changes(before: dict, after: dict) -> list[str]:
    if not before:
        return ["no seed snapshot from before the run"]
    b, a = _seed_view(before), _seed_view(after)
    return [f"seeded {k} changed" for k in ("dashboard", "rule") if b[k] != a[k]]


def _attr(value) -> str | int | float | bool:
    if isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value)
    return text[:SNIPPET]


def span_attributes(outcome: dict) -> dict:
    """Flat study.* attributes for the run's root span: the task, the step
    tally, and every check's result with its expected and actual value."""
    attrs = {
        "study.task": outcome["task"],
        "study.steps_total": outcome["steps_total"],
        "study.checks_total": len(outcome["criteria"]),
        "study.checks_passed": sum(c["ok"] for c in outcome["criteria"]),
    }
    for c in outcome["criteria"]:
        key = re.sub(r"[^A-Za-z0-9_.]", "_", f"study.check.{c['step']}.{c['check']}")
        attrs[f"{key}.ok"] = c["ok"]
        attrs[f"{key}.expected"] = _attr(c["expected"])
        attrs[f"{key}.actual"] = _attr(c["actual"])
    return attrs


# ---------------------------------------------------------------- replay


def replay(cfg: dict, roots: list[Path]) -> dict:
    """Score the filter and time field of every stored rule against the live
    stack. Returns tallies and the failing rules, for review by hand."""
    kb = er.Kibana(*er.load_seed())
    es = Elastic.from_stack()
    _, dvs = kb.call("GET", "/api/data_views")
    data_views = (dvs or {}).get("data_view", [])
    props = _properties(es, cfg["data"]["logs"]["index"])
    want_tf = resolve_field(props, "timestamp")
    tally, failures = {}, []
    with probe_index(es, cfg, "logs") as probe:
        for root in roots:
            for path in sorted(root.glob("**/artifacts.json")):
                for rule in json.loads(path.read_text()).get("rules", []):
                    f = evaluate_filter(es, cfg, rule, "logs", probe, data_views)
                    tf = rule_time_field(rule, data_views)
                    tf_ok = resolve_field(props, tf) == want_tf
                    key = (f["form"], f["ok"], tf_ok)
                    tally[key] = tally.get(key, 0) + 1
                    if not (f["ok"] and tf_ok):
                        failures.append(
                            {
                                "run": str(path.parent.relative_to(HERE)),
                                "form": f["form"],
                                "filter": f["detail"],
                                "time_field": tf,
                                "query": _query_text(rule),
                            }
                        )
    return {
        "tally": [
            {"form": k[0], "filter_ok": k[1], "time_field_ok": k[2], "rules": n}
            for k, n in sorted(tally.items())
        ],
        "failures": failures,
    }


def _query_text(rule: dict) -> str:
    p = rule.get("params") or {}
    q = (
        p.get("esQuery")
        or ((p.get("searchConfiguration") or {}).get("query") or {}).get("query")
        or (p.get("esqlQuery") or {}).get("esql")
        or p.get("filterKuery")
        or ""
    )
    return str(q)[:300]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prompt", help="print a task's prompt")
    p.add_argument("task")
    p.add_argument("arm", choices=ARMS)
    p.add_argument("--marker", default="RUN-0000")
    sub.add_parser("seed", help="install the sample data and restore the seeded objects")
    r = sub.add_parser("replay", help="score stored runs' rules against the live stack")
    r.add_argument(
        "--runs",
        type=Path,
        nargs="*",
        default=[HERE / "runs" / "efficiency", HERE / "runs" / "local"],
    )
    r.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    cfg = load()
    if args.cmd == "prompt":
        print(build_prompt(cfg, args.task, args.arm, args.marker))
        return 0
    if args.cmd == "seed":
        kb = er.Kibana(*er.load_seed())
        seeded = restore_seed(kb, cfg)
        print(json.dumps(_seed_view(seeded), indent=2))
        return 0
    result = replay(cfg, args.runs)
    for row in result["tally"]:
        print(
            f"{row['rules']:4d}  {row['form']:16s} filter_ok={row['filter_ok']!s:5s} "
            f"time_field_ok={row['time_field_ok']}"
        )
    print(f"{len(result['failures'])} failing rules")
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
