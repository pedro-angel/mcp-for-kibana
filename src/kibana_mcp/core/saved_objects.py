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

# A valid handle is a bare `so-<12 hex>` token — no path metacharacter is
# representable, so traversal is impossible. `\A..\Z` (not `^..$`) so a trailing
# newline can never slip through even if a future edit swaps fullmatch for match.
_HANDLE_RE = re.compile(r"\Aso-[0-9a-f]{12}\Z")
# O_NOFOLLOW may be absent on some platforms; 0 is a safe no-op there (Linux/macOS
# — the dev + CI targets — have it).
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
# Bound disk growth: export is a read-tier tool that writes a file per call.
_MAX_RETAINED = 20


def new_handle() -> str:
    return f"so-{secrets.token_hex(6)}"


def to_ndjson(export_body: list[dict[str, Any]]) -> bytes:
    """Serialize the FULL export body (objects + the trailing details line) to
    NDJSON — Kibana's import tolerates the details line (probed)."""
    return "\n".join(json.dumps(o, separators=(",", ":")) for o in export_body).encode("utf-8")


def resolve_handle_path(export_dir: Path, handle: str) -> Path:
    """THE security boundary. Raise ValueError unless `handle` is a bare
    `so-<hex>` token whose file resolves to a direct child of `export_dir`.
    The regex alone forbids every path character; the resolved-parent check is
    defense-in-depth (and rejects a final-component symlink out of the dir)."""
    if not _HANDLE_RE.fullmatch(handle):
        raise ValueError(f"invalid saved-objects export handle: {handle!r}")
    base = export_dir.resolve()
    path = base / f"{handle}.ndjson"
    if path.resolve().parent != base:
        raise ValueError(f"export handle escapes the export directory: {handle!r}")
    return path


def write_export(export_dir: Path, content: bytes) -> str:
    """Write `content` under a fresh handle; return the handle. Created with
    O_CREAT|O_EXCL|O_NOFOLLOW at mode 0600 — atomic, refuses a pre-planted
    symlink/file at the (random) name."""
    handle = new_handle()
    path = resolve_handle_path(export_dir, handle)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW, 0o600)
    try:
        view = memoryview(content)
        while view:  # write-all: a single os.write can be partial for a large export
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)
    _prune(export_dir)
    return handle


def read_export(export_dir: Path, handle: str) -> bytes:
    """Read a previously-written export by handle. Confined to `export_dir` and
    opened O_NOFOLLOW (a symlink at the name is refused, TOCTOU-proof). A missing
    file is a clean KibanaNotFound; any other open failure is a ValueError."""
    path = resolve_handle_path(export_dir, handle)
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except FileNotFoundError:
        raise KibanaNotFound(
            f"saved-objects export handle '{handle}' not found (it may have expired)"
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


def _prune(export_dir: Path) -> None:
    """Keep only the most recent _MAX_RETAINED export files (best-effort) so a
    read-tier tool can't grow the disk without bound."""
    try:
        files = sorted(
            (p for p in export_dir.iterdir() if p.name.startswith("so-") and p.name.endswith(".ndjson")),
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
