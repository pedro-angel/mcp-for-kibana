"""Deterministic dashboard ids derived from titles — pure, framework-free.

A title maps to a stable id so re-creating a dashboard with the same title
overwrites it (idempotent) rather than duplicating. Determinism is pinned:
SHA-256 over the ASCII slug (never Python's per-process-salted hash()), so the
same title yields the same id across processes and interpreter restarts.
"""

import hashlib
import re
import unicodedata

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_CAP = 64
_HASH_LEN = 12
_FALLBACK_STEM = "dashboard"


def normalize(title: str) -> str:
    """Case-, whitespace- and accent-form-insensitive identity key ('' if blank)."""
    return " ".join(unicodedata.normalize("NFC", title).casefold().split())


def derive_dashboard_id(title: str) -> str:
    """Stable '<slug>-<12 hex>' id for a title. Punctuation/whitespace/case
    variants converge (hash over the slug); non-ASCII titles diverge (hash over
    the normalized title when the slug is empty)."""
    norm = normalize(title)
    if not norm:
        # Blank / whitespace-only: no stable identity to derive. The tool layer
        # already rejects these before reaching here; this makes a direct caller
        # fail closed too, rather than minting a shared "dashboard-<hash of ''>".
        raise ValueError("cannot derive a dashboard id from a blank or whitespace-only title")
    slug = _NON_SLUG.sub("-", norm).strip("-")
    hash_input = slug or norm
    prefix = slug[:_SLUG_CAP].strip("-") or _FALLBACK_STEM
    suffix = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:_HASH_LEN]
    return f"{prefix}-{suffix}"
