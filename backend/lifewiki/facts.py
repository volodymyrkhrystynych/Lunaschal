"""The facts a life-wiki article is rendered from.

**The prose is derived; these are the memory.** That inversion is the whole
point of the design and it exists because of a specific documented failure: an
LLM asked to revise its own prose night after night accumulates distortions it
cannot itself detect, and each rewrite compounds the last one's loss. An earlier
draft of this feature did exactly that — article plus new data in, revised
article out, forever. Rendering from the fact list instead means the Nth render
reads N facts rather than N-1 renders, so there is no chain to compound along.

Four properties carry that:

- **Every fact cites the row it came from** (`source_kind`/`source_id`). A
  citation is what makes a wrong fact findable by a human rather than merely
  plausible, and it is what `rebuild_article` re-derives from.
- **Nothing is edited.** A fact that stops being true gets superseded, which
  writes a pointer and leaves both rows in place. A wrong supersession is then
  visible and reversible instead of a silent overwrite.
- **Nothing is deleted by the pass.** Only the user deletes, and only through
  the routes.
- **A locked fact is frozen.** The pass cannot supersede one the user corrected
  by hand — the "protect critical information from updates" mitigation, and the
  same stance `wiki_articles.locked` already takes for a whole article.
"""
import time

from ulid import ULID

from backend.db.connection import get_db, row_to_dict

# One fact is one sentence. Anything longer is a paragraph of prose sneaking
# back in through the store built to keep prose out.
MAX_STATEMENT_CHARS = 240

# What a fact can be drawn from. Not a CHECK constraint in the schema, because
# adding a source should not be a table rebuild -- but a closed set here, so a
# typo becomes a failed insert rather than a fact nothing can trace.
SOURCE_KINDS = ('journal', 'message', 'food', 'workout', 'calendar', 'observation')


def _normalise(statement: str) -> str:
    return ' '.join((statement or '').split())[:MAX_STATEMENT_CHARS]


def live_facts(article_id: str) -> list[dict]:
    """The facts currently believed about this article, newest evidence first."""
    rows = get_db().execute(
        'SELECT * FROM life_facts WHERE article_id=? AND superseded_by IS NULL'
        ' ORDER BY last_seen DESC, id DESC',
        (article_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def all_facts(article_id: str) -> list[dict]:
    """Everything ever recorded, superseded rows included — the audit view."""
    rows = get_db().execute(
        'SELECT * FROM life_facts WHERE article_id=? ORDER BY created_at, id',
        (article_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_fact(fact_id: str) -> dict | None:
    row = get_db().execute('SELECT * FROM life_facts WHERE id=?', (fact_id,)).fetchone()
    return row_to_dict(row) if row else None


def add_fact(article_id: str, statement: str, *, source_kind: str,
             source_id: str | None = None, now: int | None = None) -> dict | None:
    """Record one fact. Returns the row, or None if there was nothing to record.

    Restating a fact that is already live does **not** write a second row — it
    moves `last_seen` forward on the existing one. That is what "the user
    mentioned this again" should mean: more confidence in the same fact, not two
    facts. It is also what keeps a nightly pass over an overlapping window from
    doubling the article every time it runs.
    """
    statement = _normalise(statement)
    if not statement:
        return None
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f'unknown fact source: {source_kind}')

    db = get_db()
    now = now if now is not None else int(time.time())
    existing = db.execute(
        'SELECT * FROM life_facts WHERE article_id=? AND superseded_by IS NULL'
        ' AND statement=? COLLATE NOCASE',
        (article_id, statement),
    ).fetchone()
    if existing is not None:
        db.execute('UPDATE life_facts SET last_seen=? WHERE id=?', (now, existing['id']))
        db.commit()
        return row_to_dict(
            db.execute('SELECT * FROM life_facts WHERE id=?', (existing['id'],)).fetchone()
        )

    fact_id = str(ULID())
    db.execute(
        'INSERT INTO life_facts(id, article_id, statement, source_kind, source_id,'
        ' first_seen, last_seen, locked, superseded_by, created_at)'
        ' VALUES (?,?,?,?,?,?,?,0,NULL,?)',
        (fact_id, article_id, statement, source_kind, source_id, now, now, now),
    )
    db.commit()
    return row_to_dict(
        db.execute('SELECT * FROM life_facts WHERE id=?', (fact_id,)).fetchone()
    )


def supersede(old_fact_id: str, replacement: dict | None) -> bool:
    """Point a fact at the one that replaced it. False if it could not be done.

    A locked fact is never superseded — the user corrected it by hand, and a
    pass overruling that would make the correction pointless. `replacement` may
    be None for a fact that simply stopped being true with nothing taking its
    place; the row then points at itself, which reads as "retired" and keeps the
    NULL check meaning "live".
    """
    db = get_db()
    row = db.execute('SELECT * FROM life_facts WHERE id=?', (old_fact_id,)).fetchone()
    if row is None or row['locked']:
        return False
    target = replacement['id'] if replacement else old_fact_id
    db.execute('UPDATE life_facts SET superseded_by=? WHERE id=?', (target, old_fact_id))
    db.commit()
    return True


def set_locked(fact_id: str, locked: bool) -> bool:
    db = get_db()
    cur = db.execute(
        'UPDATE life_facts SET locked=? WHERE id=?', (1 if locked else 0, fact_id)
    )
    db.commit()
    return cur.rowcount > 0


def delete_fact(fact_id: str) -> bool:
    """The user's own delete. The pass never calls this."""
    db = get_db()
    cur = db.execute('DELETE FROM life_facts WHERE id=?', (fact_id,))
    db.commit()
    return cur.rowcount > 0


def clear_derived(article_id: str) -> int:
    """Drop every fact the pass derived, keeping the ones the user locked.

    What `rebuild_article` calls before re-extracting from source rows. Locked
    facts survive on purpose: a rebuild is meant to correct the machine's drift,
    not to throw away the corrections the user made to it.
    """
    db = get_db()
    cur = db.execute('DELETE FROM life_facts WHERE article_id=? AND locked=0', (article_id,))
    db.commit()
    return cur.rowcount


def format_facts(facts: list[dict], *, with_ids: bool = False) -> str:
    """Render facts for a prompt. `with_ids` for the turn that may supersede."""
    if not facts:
        return ''
    lines = []
    for f in facts:
        prefix = f'[{f["id"]}] ' if with_ids else '- '
        locked = ' (locked by the user — do not contradict)' if f['locked'] else ''
        lines.append(f'{prefix}{f["statement"]}{locked}')
    return '\n'.join(lines)
