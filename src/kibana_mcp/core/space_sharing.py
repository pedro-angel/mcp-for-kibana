"""Saved objects between spaces (D49): the checks and guidance the platform-admin
copy and share tools apply, kept pure so the adapter and the unit-test fake
say the same thing (D17: guidance text is product contract).

Observed on Kibana 9.5.4 (Basic), and what each shaped:
- the copy and share routes accept a space id that names no space and write
  into it without an error (the object shows up once a space with that id is
  created), so every space added to is checked first (D5);
- an object left in no space is deleted (spaces `[]`, its document gone), so
  removing an object's last space is refused unless forced;
- `_get_shareable_references` answers `spaces: []` for a type that lives in one
  space only (dashboard, visualization, search, map, tag, config) and
  `isMissing: true` for an object the source space does not hold.

stdlib only (no fastmcp/mcp/kibana) so this stays in `core`.
"""

from collections.abc import Iterable, Sequence

from kibana_mcp.core.models import ObjectSpaces

# Kibana's "every space, current and future" marker in an object's spaces.
ALL_SPACES = "*"


def _ref(type: str, id: str) -> str:
    return f"{type}/{id}"


def missing_spaces_message(missing: Sequence[str]) -> str:
    names = ", ".join(f"'{s}'" for s in missing)
    noun = "space" if len(missing) == 1 else "spaces"
    return (
        f"{noun} {names} not found — Kibana would write into a space that does not "
        "exist without an error, and the objects would only appear in a space created "
        "later with that id; check the ids with list_spaces, or create the space with "
        "create_space first"
    )


def self_target_message(space: str) -> str:
    return (
        f"the objects are already in space '{space}', the source space — name other "
        "target spaces, or pass source_space to copy from another space"
    )


def objects_not_found_message(refs: Iterable[str]) -> str:
    return (
        f"saved object(s) not found in the source space: {', '.join(refs)} — `type` and "
        "`id` are what find_saved_objects returns for the source space; pass "
        "source_space when the objects are in another space"
    )


def copy_fix(error: str, overwrite: bool) -> str:
    """What to do about one object Kibana did not copy; nothing was written for it."""
    if error == "missing_references":
        return (
            "it references objects that were not copied with it, so nothing was written "
            "for it: pass include_references=true to copy them along; if it was already "
            "true, those objects are missing from the source space too (find_saved_objects "
            "there), so fix or remove the references first"
        )
    if error == "unsupported_type":
        return (
            "Kibana cannot copy this type between spaces (it does not export it), so "
            "nothing was written for it"
        )
    if error == "ambiguous_conflict":
        return (
            "more than one object in the target space was copied from this one "
            "(`destinations`), so Kibana cannot tell which to overwrite and wrote nothing: "
            "remove the extra copies there, or make a new copy with "
            "copy_saved_objects_to_spaces"
        )
    if error == "conflict" and not overwrite:
        return (
            "an object copied from this one is already in the target space: "
            "overwrite_saved_objects_in_spaces replaces it (destructive tier), "
            "copy_saved_objects_to_spaces makes a new copy"
        )
    return f"Kibana did not copy it ({error}), so nothing was written for it"


def sharing_refusal(states: Sequence[ObjectSpaces]) -> str | None:
    """Refuse objects the source space does not hold and types that cannot be
    shared, before any write; None when every object can be shared."""
    missing = [_ref(s.type, s.id) for s in states if s.missing]
    if missing:
        return objects_not_found_message(missing)
    single = [s for s in states if not s.spaces]
    if single:
        types = ", ".join(sorted({f"'{s.type}'" for s in single}))
        refs = ", ".join(_ref(s.type, s.id) for s in single)
        return (
            f"{refs}: Kibana keeps {types} objects in one space and cannot share them to "
            "others — copy_saved_objects_to_spaces makes a copy in another space instead "
            "(data views, for one, can be shared)"
        )
    return None


def unshare_refusal(states: Sequence[ObjectSpaces], remove: Sequence[str], force: bool) -> str | None:
    """Refuse a removal that would remove nothing, or that would leave an object
    in no space (Kibana then deletes it) unless `force`; None to proceed."""
    gone = set(remove)
    if not any(gone & set(s.spaces) for s in states):
        where = "; ".join(f"{_ref(s.type, s.id)} is in {list(s.spaces)}" for s in states)
        return (
            f"none of these objects is shared to {list(remove)}, so there is nothing to "
            f"remove: {where}"
        )
    last = [s for s in states if not set(s.spaces) - gone]
    if last and not force:
        where = "; ".join(f"{_ref(s.type, s.id)} is only in {list(s.spaces)}" for s in last)
        return (
            f"removing these spaces would leave objects in no space, and Kibana deletes an "
            f"object left in no space: {where}. Keep one of its spaces (or share it to "
            "another space with share_saved_objects_to_spaces first), or pass force=True "
            "to delete it"
        )
    return None


def duplicates(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for item in items:
        if item in seen and item not in repeated:
            repeated.append(item)
        seen.add(item)
    return repeated
