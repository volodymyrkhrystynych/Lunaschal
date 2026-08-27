"""Which files a plan may name, and where that list comes from.

A plan is handed to a coding agent that will act on it without asking anyone.
A path in it that does not exist is not a small error: it sends that agent
looking for a file, failing, and then improvising — which is the failure the
whole evidence-by-index arrangement in backend/research/evidence.py exists to
prevent for "is this already built?". This is the same trick for "where does
this go?".

**The candidates are files somebody actually opened**, in three places, in this
order of trust:

1. Files read during this idea's discussions. The agent opened them *while
   thinking about this idea*, so they are the most likely to be relevant.
2. Files cited by the assessment, chosen by index from evidence.gather_candidates
   and therefore already known to exist.
3. Files behind the repo's code-wiki notes, which were read by the nightly pass.

Nothing here is generated. If no one has read anything, there are no candidates,
the schema drops the field entirely, and the plan simply has no file list —
which is honest, and better than a guessed one.
"""
import json

from backend.db.connection import get_db

MAX_CANDIDATES = 30


def _add(out: list[dict], seen: set[str], file: str, why: str) -> None:
    file = (file or '').strip()
    if not file or file in seen:
        return
    seen.add(file)
    out.append({'file': file, 'why': why})


def _files_from_sources(raw) -> list[str]:
    """The `{file}` entries of a stored sources/metadata JSON blob.

    Web sources live in the same list and carry `url` instead; they are not
    files and are skipped rather than coerced.
    """
    try:
        parsed = json.loads(raw or '[]')
    except (ValueError, TypeError):
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get('sources') or []
    if not isinstance(parsed, list):
        return []
    return [item['file'] for item in parsed
            if isinstance(item, dict) and item.get('file')]


def gather_file_candidates(idea_id: str, repo_id: str | None = None) -> list[dict]:
    """[{file, why}] — files that were read, most relevant first.

    Pure in the sense that matters: it reads rows, never a model, and never the
    filesystem. A file listed here was opened by a tool call that recorded it.
    """
    db = get_db()
    out: list[dict] = []
    seen: set[str] = set()

    # 1. Read while discussing this idea.
    rows = db.execute(
        'SELECT m.metadata FROM messages m JOIN conversations c'
        ' ON c.id = m.conversation_id'
        " WHERE c.idea_id = ? AND m.role = 'assistant' AND m.metadata IS NOT NULL"
        ' ORDER BY m.created_at DESC',
        (idea_id,),
    ).fetchall()
    for row in rows:
        for file in _files_from_sources(row['metadata']):
            _add(out, seen, file, 'read while discussing this idea')

    # 2. Cited by the assessment — already bounded to things that exist.
    row = db.execute(
        'SELECT a.evidence FROM idea_assessments a JOIN ideas i'
        ' ON i.assessment_id = a.id WHERE i.id = ?',
        (idea_id,),
    ).fetchone()
    if row:
        try:
            for item in json.loads(row['evidence'] or '[]'):
                if isinstance(item, dict) and item.get('file'):
                    _add(out, seen, item['file'], 'cited as existing machinery')
        except (ValueError, TypeError):
            pass

    # 3. Read by the nightly pass for this repo.
    if repo_id:
        for article in db.execute(
            "SELECT sources FROM wiki_articles WHERE repo_id=? AND kind='code'"
            ' AND sources IS NOT NULL ORDER BY updated_at DESC',
            (repo_id,),
        ).fetchall():
            for file in _files_from_sources(article['sources']):
                _add(out, seen, file, 'covered by a module note')

    return out[:MAX_CANDIDATES]


def render_candidates(candidates: list[dict]) -> str:
    """The numbered list the model picks from. 1-based, matching evidence.py."""
    return '\n'.join(
        f"{i}. {c['file']} ({c['why']})" for i, c in enumerate(candidates, start=1)
    )


def resolve_indexes(indexes, candidates: list[dict]) -> list[dict]:
    """Chosen indexes back to real paths, deterministically.

    The grammar already bounds these during decoding, so an out-of-range value
    should be impossible — this drops it anyway rather than raising, because
    the one thing worse than a plan with a missing file is no plan at all.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for index in indexes or []:
        try:
            position = int(index) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= position < len(candidates):
            continue
        candidate = candidates[position]
        if candidate['file'] in seen:
            continue
        seen.add(candidate['file'])
        out.append(candidate)
    return out
