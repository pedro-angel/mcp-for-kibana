"""Exception-list export files (D50): reading the NDJSON that
`POST /api/exception_lists/_export` returns and rewriting it as a new list.

An export holds one line for the list container, one per exception item and a
final details line (probed on 9.5.4). It stays on the server under an `el-`
handle (core.saved_objects) and only a summary reaches the model.

Why the copy is rewritten here instead of Kibana's `as_new_list=true` (probed):
that mode keeps every `item_id` of the source, so two lists then hold items
with the same `item_id` (a GET by `item_id` answers one of them, and a delete
by `item_id` cannot say which), and its result does not name the new
`list_id`. A plain import of this rewrite (a chosen or fresh `list_id`, a
fresh `item_id` per item) creates exactly one new list whose ids are known.

stdlib only (no fastmcp/mcp/kibana) so this stays in `core`."""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from kibana_mcp.core.saved_objects import to_ndjson

# The details line is the only one carrying this key.
DETAILS_KEY = "exported_exception_list_count"
# Server-generated fields of an exported line, dropped from a new copy so the
# import can never match them to the source (9.5.4 ignores them: an import
# minted a new saved-object id with the source still present, probed).
_GENERATED = ("id", "_version", "tie_breaker_id")


@dataclass(frozen=True, slots=True)
class ExceptionExportFile:
    container: dict[str, Any]
    items: tuple[dict[str, Any], ...]

    @property
    def list_id(self) -> str:
        return str(self.container.get("list_id", ""))

    @property
    def namespace_type(self) -> str:
        return str(self.container.get("namespace_type") or "single")

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(str(i.get("item_id", "")) for i in self.items)


def parse_export(content: bytes) -> ExceptionExportFile:
    """Split an export into its list line and item lines. Raises ValueError
    unless it holds exactly one list (export_exception_list writes one)."""
    containers: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for raw in content.decode("utf-8").splitlines():
        if not raw.strip():
            continue
        line = json.loads(raw)
        if not isinstance(line, dict) or DETAILS_KEY in line:
            continue
        (items if "item_id" in line else containers).append(line)
    if len(containers) != 1:
        raise ValueError(
            f"the export holds {len(containers)} exception lists, not one — pass a "
            "handle that export_exception_list returned"
        )
    return ExceptionExportFile(container=containers[0], items=tuple(items))


def as_new_copy(export: ExceptionExportFile, list_id: str | None) -> tuple[bytes, str]:
    """The export rewritten as a new list: `list_id` (a fresh uuid when None)
    on the list and every item, a fresh `item_id` per item, and the
    server-generated fields dropped. Returns (content, the new list_id)."""
    new_list_id = list_id or str(uuid.uuid4())

    def fresh(line: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in line.items() if k not in _GENERATED}
        out["list_id"] = new_list_id
        return out

    lines = [fresh(export.container)]
    for item in export.items:
        copy = fresh(item)
        copy["item_id"] = str(uuid.uuid4())
        lines.append(copy)
    return to_ndjson(lines), new_list_id


def item_count(body: list[dict[str, Any]]) -> int:
    """Exported items of an export body (the details line's count, else the
    item lines)."""
    details = next((o for o in body if DETAILS_KEY in o), {})
    counted = details.get("exported_exception_list_item_count")
    if isinstance(counted, int):
        return counted
    return len([o for o in body if "item_id" in o])


# Kibana answers 405 "action not allowed" to a duplicate of an `endpoint`
# list (probed); the endpoint artifact types share the prefix.
def duplicable(list_type: str) -> bool:
    return not list_type.startswith("endpoint")


_STATUS_TEXT = {400: "invalid", 404: "not found", 409: "already exists"}


def import_error_text(error: dict[str, Any]) -> str:
    """One per-object import failure as "list_id[/item_id]: <status> <kind>" —
    identity and status only: a 400's message echoes the values it refused."""
    ident = str(error.get("list_id") or "?")
    if error.get("item_id"):
        ident += f"/{error['item_id']}"
    status = (error.get("error") or {}).get("status_code")
    kind = _STATUS_TEXT.get(status, "error") if isinstance(status, int) else "error"
    return f"{ident}: {status if isinstance(status, int) else '?'} {kind}"
