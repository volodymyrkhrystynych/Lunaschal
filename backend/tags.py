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


def tag_counts(rows, column: str = 'tags') -> list[dict]:
    """Count tag occurrences across rows with a JSON-array tags column into
    the {name, count} shape used by tag-filter pill endpoints (recipes, food
    log, etc), sorted by count descending then name."""
    counts: dict[str, int] = {}
    for r in rows:
        try:
            for tag in json.loads(r[column]):
                if isinstance(tag, str) and tag.strip():
                    counts[tag] = counts.get(tag, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue
    return [
        {'name': name, 'count': count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
