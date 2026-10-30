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

# The `error` of every requested object in a target space Kibana refused as a
# whole (no per-object error there: one bare error for the space).
SPACE_ERROR = "space_error"


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


def unreadable_space_fix(spaces: Sequence[str]) -> str:
    """The fix when this API key cannot read a space. Kibana answers 403 for
    an id the key has no privilege in whether or not a space has that id
    (probed on 9.5.4), so the text never says the space is missing and
    never offers create_space."""
    names = ", ".join(f"'{s}'" for s in spaces)
    return (
        f"this API key has no privilege in {names}; Kibana answers that whether or not "
        "a space with that id exists. list_spaces shows the spaces this key can view; to "
        "work in another space, use an API key created by a user whose role grants a "
        "privilege there (a key keeps the privileges its owner had when it was created) "
        "— send it as 'Authorization: ApiKey <key>' or set KIBANA_API_KEY"
    )


def unreadable_spaces_message(hidden: Sequence[str], absent: Sequence[str] = ()) -> str:
    """Target spaces the key cannot read (`hidden`), plus any that are
    genuinely missing (`absent`), refused before anything is written."""
    noun = "space" if len(hidden) == 1 else "spaces"
    names = ", ".join(f"'{s}'" for s in hidden)
    text = f"cannot write into {noun} {names}: {unreadable_space_fix(hidden)}"
    if absent:
        text += f"; also {missing_spaces_message(absent)}"
    return text + "; nothing was changed"


def source_fixed_reason(kibana_url_pinned: bool, public_url_pinned: bool) -> str | None:
    """Why this deployment refuses source_space (D8), naming the URL that
    does it: a '/s/<id>' KIBANA_URL fixes the source to that space (it wins
    when both are pinned); a pinned KIBANA_PUBLIC_URL alone fixes it to the
    default space. None when source_space is accepted."""
    if kibana_url_pinned:
        return "KIBANA_URL pins it with a '/s/<id>' base path"
    if public_url_pinned:
        return (
            "KIBANA_PUBLIC_URL has a '/s/<id>' base path, which fixes the source to the "
            "default space"
        )
    return None


def self_target_message(space: str, source_fixed: str | None = None) -> str:
    """`source_fixed`: why this deployment refuses source_space
    (source_fixed_reason), so the text names that instead of offering it."""
    if source_fixed:
        return (
            f"the objects are already in space '{space}', the source space "
            f"({source_fixed}) — name other target spaces"
        )
    return (
        f"the objects are already in space '{space}', the source space — name other "
        "target spaces, or pass source_space to copy from another space"
    )


def objects_not_found_message(refs: Iterable[str], source_fixed: str | None = None) -> str:
    head = (
        f"saved object(s) not found in the source space: {', '.join(refs)} — `type` and "
        "`id` are what find_saved_objects returns for the source space"
    )
    if source_fixed:
        return (
            f"{head}; the source space is fixed on this deployment ({source_fixed}), so "
            "objects in another space cannot be named here"
        )
    return f"{head}; pass source_space when the objects are in another space"


def space_failure_fix(space: str, status: object, error: str, message: str) -> str:
    """What to do when Kibana refused the import into one target space as a
    whole (its copy route answers that space with a bare error, no object):
    nothing was written there, the other targets are reported as usual."""
    what = " ".join(str(x) for x in (status, error) if x not in (None, ""))
    said = f"{what}: {message}" if message else what
    if str(status) == "403":
        cause = (
            "the API key cannot write saved objects there. A key keeps the privileges "
            "its owner had when it was created, so changing a role does not change this "
            "one: use a new API key, created by a user whose role grants the saved "
            "objects' features in that space (create_or_update_role) — send it as "
            "'Authorization: ApiKey <key>' or set KIBANA_API_KEY — or leave the space out "
            "of target_spaces"
        )
    else:
        cause = "fix what Kibana names, then copy into that space again"
    return (
        f"Kibana refused the whole copy into space '{space}' ({said or 'no detail'}), so "
        f"nothing was written there: {cause}"
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


def sharing_refusal(
    states: Sequence[ObjectSpaces], source_fixed: str | None = None
) -> str | None:
    """Refuse objects the source space does not hold and types that cannot be
    shared, before any write; None when every object can be shared."""
    missing = [_ref(s.type, s.id) for s in states if s.missing]
    if missing:
        return objects_not_found_message(missing, source_fixed)
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
