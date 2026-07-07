"""Shared normalization for JSON-array tag columns.

Currently used by flashcards; journal/calendar tagging should reuse these
helpers instead of growing their own normalization rules, so tag identity
stays consistent across entities.
"""
import json


def normalize_tags(raw) -> list[str]:
    """Trim, lowercase, and dedupe a raw tags payload; drop non-strings and blanks."""
    if not isinstance(raw, list):
        return []
    seen: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            continue
        name = t.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def tags_json(raw) -> str | None:
    """Serialize tags for a TEXT column; empty normalizes to NULL, not '[]'."""
    tags = normalize_tags(raw)
    return json.dumps(tags) if tags else None
