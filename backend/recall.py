"""Shared machinery for the recall tools — the caps, the clipping, the search.

Every chat-shaped surface has a tool for reaching its own world: the Chat tab
reads the user's own record (`backend/lifewiki/tools.py`), a Writing discussion
reads its project's chapters and notes (`backend/writing/tools.py`), an Ideas
discussion reads the other ideas for its repository
(`backend/research/idea_recall.py`). They answer different questions over
different tables, but the budget, the clipping and the honesty rules are the
same in all three, and were written for the first of them.

A top-level module rather than a home in any one of those packages: the other
two would then import a feature package they have nothing to do with, and a
later reader would take that for a real dependency. It is the shape
`backend/tags.py` and `backend/day_boundary.py` already have — and it
**imports nothing from `backend`**, so it can be reached from the delegate,
from research, from a route, with no possible edge back.

Free functions, not a base class. The three surfaces disagree about what a hit
is, about what an unset scope means, and about where search comes from; a base
class would be a bag of hooks each one overrides. The interface they actually
share is the duck type `run_tool(name, args) -> (text, event)`, which
`backend/research/agent.py`'s loop dispatches to.
"""
import re
from datetime import datetime

# Total characters one tool call may return. Sized so that two searches in one
# turn still leave the conversation itself the larger part of the prompt.
MAX_RESULT_CHARS = 2400

# Per-hit ceiling, applied before the total. A single rambling entry should not
# be able to spend the whole budget.
MAX_HIT_CHARS = 400

# What one *document* read in full may return. A journal entry at 400 chars is
# a hit; a chapter at 400 chars is noise, so a read needs its own budget. Sits
# just under `wiki.MAX_ARTICLE_CHARS` (8000), the comparable "read one thing"
# allowance already in the codebase.
MAX_DOC_CHARS = 6000

DEFAULT_LIMIT = 5
MAX_LIMIT = 10


def clip(text: str, limit: int = MAX_HIT_CHARS) -> str:
    """One hit, whitespace-collapsed and cut to `limit`."""
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def join(blocks: list[str]) -> str:
    """Concatenate hits until the budget runs out, whole hits only.

    A half-hit is worse than one fewer hit: the model reads a truncated journal
    entry as the whole of what was written that day.
    """
    out: list[str] = []
    used = 0
    for block in blocks:
        if used + len(block) > MAX_RESULT_CHARS and out:
            out.append(f'({len(blocks) - len(out)} more not shown)')
            break
        out.append(block)
        used += len(block)
    return '\n\n'.join(out)


def when(ts: int) -> str:
    """A date the model can act on, with the weekday it will be asked about."""
    return datetime.fromtimestamp(ts).strftime('%a %d %b %Y, %H:%M')


def limit_of(args: dict) -> int:
    """The caller's `limit`, clamped. Accepted but deliberately not in any tool
    schema — the model does not need one more thing to decide."""
    try:
        limit = int((args or {}).get('limit') or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def clip_doc(text: str, limit: int = MAX_DOC_CHARS) -> tuple[str, bool]:
    """(text, truncated) — a whole document cut at a readable boundary.

    Prefers a paragraph break in the last fifth of the window, then a word
    boundary, because a cut mid-sentence reads as the author's own writing.
    Unlike `join`, dropping the thing is not an option here, so the honesty has
    to be in the marker the caller appends: a model that reads two thirds of a
    chapter and is not told so will answer about an ending it never saw.
    """
    text = text or ''
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    para = window.rfind('\n\n')
    if para >= int(limit * 0.8):
        return window[:para].rstrip(), True
    space = window.rfind(' ')
    if space > 0:
        return window[:space].rstrip(), True
    return window.rstrip(), True


def find_ci(haystack: str, needle: str) -> int | None:
    """Index of the first case-insensitive occurrence, or None.

    `re.IGNORECASE` rather than SQL: SQLite's `LIKE` and `lower()` fold case
    for ASCII only, so a search for "мірена" would never match "Мірена" — which
    is why search here happens in Python over the scoped rows rather than in
    the query. It is also why this is not `casefold()`: folding can change a
    string's length (ß → ss), so an index into the folded text is not an index
    into the original, and every excerpt after such a character would be cut in
    the wrong place. `re` searches the original and returns a real offset.

    `re.escape` because the needle is the user's words, not a pattern.
    """
    if not haystack or not needle:
        return None
    match = re.search(re.escape(needle), haystack, re.IGNORECASE)
    return match.start() if match else None


def excerpt(text: str, needle: str, limit: int = MAX_HIT_CHARS) -> str:
    """The window around the first match, or a head clip when there is none.

    A hit found in the body of a 30,000-character chapter, clipped from the
    top, returns the chapter's opening — which does not contain the match. The
    model then reads a result that appears to contradict the search that found
    it. The no-match fallback is for a title-only hit, which is a real result.
    """
    text = text or ''
    at = find_ci(text, needle)
    if at is None:
        return clip(text, limit)
    start = max(0, at - limit // 3)
    window = ' '.join(text[start:start + limit].split())
    prefix = '…' if start > 0 else ''
    suffix = '…' if start + limit < len(text) else ''
    return f'{prefix}{window}{suffix}'


def scope_clause(column: str, value) -> tuple[str, list]:
    """('<column> IS NULL', []) or ('<column> = ?', [value]).

    The generalisation of `wiki._repo_clause`, and the trap is the same one:
    `column = NULL` is never true in SQL, so the unset case genuinely needs a
    different operator rather than a bound None. What an unset scope *means* is
    per-surface and each caller must say which it is — the wiki's unscoped
    notes are visible to every repo, a writing project's are visible to nobody.
    """
    if value is None:
        return f'{column} IS NULL', []
    return f'{column} = ?', [value]


def unknown_tool(name: str) -> tuple[str, dict]:
    """The refusal every toolbox returns for a name it does not have.

    Never raise: an exception out of a tool call is a broken run, where this is
    just a model reaching for something that is not there.
    """
    return f'Unknown tool: {name}', {
        'tool': name, 'ok': False, 'error': 'unknown tool',
    }


def pick_one(rows, needle: str, title_of) -> tuple[object | None, str]:
    """Resolve a title the model typed to exactly one row.

    (row, '') on success, (None, reason) otherwise. Exact case-insensitive
    match wins; failing that a substring match, but only when it is unique.

    Ambiguity is reported rather than resolved to the first row: two chapters
    called "Untitled" is a normal state of a draft, and picking one silently
    means the model reads one thing while believing it read another.
    """
    needle = (needle or '').strip()
    if not needle:
        return None, 'no title given'
    folded = needle.casefold()

    exact = [r for r in rows if (title_of(r) or '').strip().casefold() == folded]
    if len(exact) == 1:
        return exact[0], ''
    if len(exact) > 1:
        return None, 'ambiguous'

    partial = [r for r in rows if folded in (title_of(r) or '').casefold()]
    if len(partial) == 1:
        return partial[0], ''
    if len(partial) > 1:
        return None, 'ambiguous'
    return None, 'not found'
