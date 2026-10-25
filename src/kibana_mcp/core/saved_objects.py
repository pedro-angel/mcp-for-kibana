"""Saved-objects export handles (#37). The exported NDJSON stays on disk in a
confined export directory and is referenced by an opaque handle — it never
crosses the model context. `resolve_handle_path` + the O_NOFOLLOW file ops are
the security boundary: `import` can only ever read a regular file it wrote inside
`export_dir`, never a symlink out of it.

stdlib only (no fastmcp/mcp/kibana) so this stays in `core` per the layering
contract. Directory *creation* (a secure 0700 dir) is the server's job
(server._resolve_export_dir), kept out of this module and out of build_server."""

import json
import os
import re
import secrets
from collections import Counter
from pathlib import Path
from typing import Any

from kibana_mcp.core.errors import KibanaNotFound
from kibana_mcp.core.models import ExportSummary, TypeCount

# A valid handle is a bare `<prefix>-<12 hex>` token — no path metacharacter is
# representable, so traversal is impossible. `\A..\Z` (not `^..$`) so a trailing
# newline can never slip through even if a future edit swaps fullmatch for match.
# Two kinds share the export directory (D50): `so-` saved-objects exports and
# `el-` exception-list exports. Each import reads only its own kind: the two
# NDJSON formats are not interchangeable (Kibana's exception-list import 400s
# a saved object, probed), so a handle of the other kind names its own tools.
SAVED_OBJECTS = "so"
EXCEPTION_LIST = "el"
_HANDLE_KINDS: dict[str, tuple[str, str, str]] = {
    # prefix: (label, the tools that take it, the tool that makes it)
    SAVED_OBJECTS: (
        "saved-objects", "import_saved_objects or overwrite_saved_objects",
        "export_saved_objects",
    ),
    EXCEPTION_LIST: (
        "exception-list", "import_exception_list or overwrite_exception_list",
        "export_exception_list",
    ),
}
_HANDLE_RES = {k: re.compile(rf"\A{k}-[0-9a-f]{{12}}\Z") for k in _HANDLE_KINDS}
# O_NOFOLLOW may be absent on some platforms; 0 is a safe no-op there (Linux/macOS
# — the dev + CI targets — have it).
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
# Bound disk growth: export is a read-tier tool that writes a file per call.
_MAX_RETAINED = 20


def new_handle(prefix: str = SAVED_OBJECTS) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def to_ndjson(export_body: list[dict[str, Any]]) -> bytes:
    """Serialize the FULL export body (objects + the trailing details line) to
    NDJSON — Kibana's import tolerates the details line (probed)."""
    return "\n".join(json.dumps(o, separators=(",", ":")) for o in export_body).encode("utf-8")


def resolve_handle_path(export_dir: Path, handle: str, prefix: str = SAVED_OBJECTS) -> Path:
    """THE security boundary. Raise ValueError unless `handle` is a bare
    `<prefix>-<hex>` token whose file resolves to a direct child of `export_dir`.
    The regex alone forbids every path character; the resolved-parent check is
    defense-in-depth (and rejects a final-component symlink out of the dir)."""
    if not _HANDLE_RES[prefix].fullmatch(handle):
        label, _, maker = _HANDLE_KINDS[prefix]
        for other, (other_label, takers, _) in _HANDLE_KINDS.items():
            if other != prefix and _HANDLE_RES[other].fullmatch(handle):
                raise ValueError(
                    f"{handle!r} is a {other_label} export handle: pass it to {takers}; "
                    f"this tool takes the {label} handle that {maker} returns ({prefix}-…)"
                )
        raise ValueError(f"invalid {label} export handle: {handle!r}")
    base = export_dir.resolve()
    path = base / f"{handle}.ndjson"
    if path.resolve().parent != base:
        raise ValueError(f"export handle escapes the export directory: {handle!r}")
    return path


def write_export(export_dir: Path, content: bytes, prefix: str = SAVED_OBJECTS) -> str:
    """Write `content` under a fresh handle; return the handle. Created with
    O_CREAT|O_EXCL|O_NOFOLLOW at mode 0600 — atomic, refuses a pre-planted
    symlink/file at the (random) name."""
    handle = new_handle(prefix)
    path = resolve_handle_path(export_dir, handle, prefix)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW, 0o600)
    try:
        view = memoryview(content)
        while view:  # write-all: a single os.write can be partial for a large export
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)
    _prune(export_dir, prefix)
    return handle


def read_export(export_dir: Path, handle: str, prefix: str = SAVED_OBJECTS) -> bytes:
    """Read a previously-written export by handle. Confined to `export_dir` and
    opened O_NOFOLLOW (a symlink at the name is refused, TOCTOU-proof). A missing
    file is a clean KibanaNotFound; any other open failure is a ValueError."""
    path = resolve_handle_path(export_dir, handle, prefix)
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except FileNotFoundError:
        label, _, maker = _HANDLE_KINDS[prefix]
        raise KibanaNotFound(
            f"{label} export handle '{handle}' not found (it may have expired: the "
            f"server keeps the last {_MAX_RETAINED}) — call {maker} again for a new one"
        ) from None
    except OSError as e:  # e.g. ELOOP from O_NOFOLLOW on a symlink
        raise ValueError(f"cannot read export handle '{handle}': {e.strerror}") from None
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1 << 16)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _prune(export_dir: Path, prefix: str = SAVED_OBJECTS) -> None:
    """Keep only the most recent _MAX_RETAINED export files of one kind
    (best-effort) so a read-tier tool can't grow the disk without bound."""
    try:
        files = sorted(
            (
                p for p in export_dir.iterdir()
                if p.name.startswith(f"{prefix}-") and p.name.endswith(".ndjson")
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in files[_MAX_RETAINED:]:
        try:
            old.unlink()
        except OSError:
            pass


def summarize_export(
    export_body: list[dict[str, Any]], handle: str, byte_size: int
) -> ExportSummary:
    """Split the trailing export-details dict from the objects and summarize —
    the summary (counts, missing refs) is what the model sees, not the bytes.
    `missingReferences` are `{type,id}` dicts → rendered as "type/id" strings."""
    # The trailing details line is the only entry without a saved-object `type`
    # (a more robust discriminator than "exportedCount", which a future object
    # shape could in principle carry).
    objects = [o for o in export_body if "type" in o]
    details = next((o for o in export_body if "type" not in o), {})
    counts = Counter(o.get("type", "unknown") for o in objects)
    missing = details.get("missingReferences") or []
    return ExportSummary(
        handle=handle,
        exported_count=int(details.get("exportedCount", len(objects)) or 0),
        types=tuple(TypeCount(type=t, count=c) for t, c in sorted(counts.items())),
        missing_ref_count=int(details.get("missingRefCount", len(missing)) or 0),
        missing_references=tuple(
            f"{m.get('type', '?')}/{m.get('id', '?')}" for m in missing if isinstance(m, dict)
        ),
        excluded_count=int(details.get("excludedObjectsCount", 0) or 0),
        byte_size=byte_size,
    )


# get_saved_object (D48) returns attributes only for these types: user-authored
# analytics content. Kibana strips only the attributes a plugin registers as
# encrypted, and some types it serves keep credentials in plain fields (9.5.4:
# `fleet-proxy` stores `certificate_key` and `proxy_headers`, `ingest-outputs`
# `config_yaml` and a `service_token`), so a fixed type allowlist, like Fleet's
# field allowlist, keeps them out instead of a denylist that new types outgrow.
READABLE_TYPES: tuple[str, ...] = (
    "canvas-element", "canvas-workpad", "canvas-workpad-template", "dashboard",
    "event-annotation-group", "graph-workspace", "index-pattern", "lens", "links", "map",
    "query", "search", "tag", "visualization",
)


# What the get_saved_object refusal names for a type it knows: only ways that
# work for that type on 9.5.4, and the toolbox the named tools live in (a
# profile or KIBANA_MCP_TOOLBOXES selection may leave it out). Kibana refuses
# to export Fleet's and Synthetics' types (400 "Trying to export non-exportable
# type(s): ...", probed for each below) and its find answers an empty page for
# hidden types (action, alert), so neither is offered where it fails.
_AGENT_POLICIES = ("list_agent_policies and get_agent_policy read agent policies", "fleet")
_PACKAGE_POLICIES = (
    "list_package_policies and get_package_policy read package policies", "fleet")
_MONITORS = (
    "list_synthetic_monitors and get_synthetic_monitor read Synthetics monitors",
    "observability",
)
_LOCATIONS = ("list_synthetic_private_locations reads private locations", "observability")
_UNREADABLE_WAYS: dict[str, tuple[str, str | None]] = {
    "action": ("list_connectors and get_connector read connectors", "alerting"),
    "alert": ("list_alert_rules and get_alert_rule read rules", "alerting"),
    "fleet-agent-policies": _AGENT_POLICIES,
    "ingest-agent-policies": _AGENT_POLICIES,
    "fleet-package-policies": _PACKAGE_POLICIES,
    "ingest-package-policies": _PACKAGE_POLICIES,
    "fleet-fleet-server-host": ("list_fleet_server_hosts reads Fleet Server hosts", "fleet"),
    "fleet-proxy": (
        "find_saved_objects lists Fleet proxies by name, but no tool reads their "
        "settings and Kibana does not export them",
        None,
    ),
    "ingest-outputs": ("list_outputs reads Fleet outputs with a fixed field set", "fleet"),
    "synthetics-monitor": _MONITORS,
    "synthetics-monitor-multi-space": _MONITORS,
    "synthetics-private-location": _LOCATIONS,
    "synthetics-privates-locations": _LOCATIONS,
    "synthetics-param": (
        "list_synthetic_params lists parameters without their values", "observability"),
}

# Any other type: no tool of this server reads it as an object, and each way
# holds only conditionally, so the text says when.
_UNREADABLE_FALLBACK = (
    "no tool of this server reads this type's attributes; find_saved_objects lists it "
    "by title and name unless the type is hidden (then it answers an empty page), and "
    "export_saved_objects moves it without its content reaching the model if Kibana "
    "exports the type (it refuses Fleet's and Synthetics' types)"
)


def unreadable_type_message(type: str) -> str:
    """The refusal for a get_saved_object `type` outside READABLE_TYPES,
    naming only the ways forward that work for that type, and the toolbox
    that serves the tools it names."""
    way, toolbox = _UNREADABLE_WAYS.get(type, (_UNREADABLE_FALLBACK, None))
    if toolbox is not None:
        way += (f" (in the {toolbox} toolbox: if they are not listed, add it to "
                "KIBANA_MCP_TOOLBOXES, or the tools to KIBANA_MCP_TOOLS)")
    return (
        f"get_saved_object does not read '{type}' objects: it returns attributes only for "
        f"{', '.join(READABLE_TYPES)}, because other types can keep credentials in plain "
        "attributes (Fleet outputs and proxies do) — " + way
    )


# get_saved_object (D48): an attribute longer than this, as JSON text, is left
# out of the result and only its size reported — a dashboard's panelsJSON (the
# flights sample's is 33,809 characters) or a data view's field list would
# otherwise fill the model's context with what the type's own tools summarize.
ATTRIBUTE_CHARS_SHOWN = 4_000


def cap_attributes(attributes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Split `attributes` into the ones shown and {name: size} for the ones
    left out. A string's size is its length; any other value's is the length
    of its compact JSON text."""
    shown: dict[str, Any] = {}
    omitted: dict[str, int] = {}
    for key, value in attributes.items():
        size = len(value) if isinstance(value, str) else len(
            json.dumps(value, separators=(",", ":"), default=str)
        )
        if size > ATTRIBUTE_CHARS_SHOWN:
            omitted[key] = size
        else:
            shown[key] = value
    return shown, omitted
