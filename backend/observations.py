"""Short standing facts the assistant noticed mid-conversation.

The chat delegate's `remember` tool writes here. It deliberately does **not**
write `backend/memory.py`'s document: that one is the user's own, and an
assistant editing it unasked is precisely what got the original
`remember`/`revise_memory` pair removed — every passing correction became a
permanent fact, plus a step in the trace and a "noted" in the reply, for a write
nobody had requested.

An observation is a weaker claim than a memory. It is staged, capped, listed in
Settings and deletable in one click, and (once the synthesis pass exists) folded
into the life wiki and marked folded rather than living here forever. That is
what makes an instant write with no confirmation card a fair trade — the same
reasoning `create_note_to_self` and `add_todos` already run on: not that the
write is small, but that it is reversible.

Two caps, and they are the point rather than housekeeping:

- **`MAX_CHARS` per observation.** A standing fact is one line. Anything longer
  is a journal entry wearing a disguise.
- **`MAX_PENDING` for the queue.** These ride in every chat system prompt, twice
  per turn, so an unbounded queue is a tax on every message. Past the cap
  `add_observation` refuses instead of trimming silently, which is the same
  stance `set_memory` takes at its own ceiling: a full store should force a
  decision, not quietly drop the oldest thing in it.
"""
import time

from ulid import ULID

from backend.db.connection import get_db, row_to_dict

MAX_CHARS = 300

# ~40 one-line facts is comparable to what the user's own 4,000-char document
# holds, which is the right order of magnitude for something sharing the same
# prompt with it.
MAX_PENDING = 40

# How many reach the system prompt. Below MAX_PENDING on purpose: past this the
# block is long enough to start crowding out the journal and schedule blocks
# underneath it, and the newest facts are the ones a conversation is likeliest
# to need.
PROMPT_LIMIT = 25


class ObservationsFull(Exception):
    """The pending queue is at its cap and needs pruning before it grows."""


def pending(limit: int | None = None) -> list[dict]:
    """Unfolded observations, newest first."""
    sql = ('SELECT * FROM assistant_observations WHERE folded_at IS NULL'
           ' ORDER BY created_at DESC, id DESC')
    params: list = []
    if limit is not None:
        sql += ' LIMIT ?'
        params.append(limit)
    return [row_to_dict(r) for r in get_db().execute(sql, params).fetchall()]


def pending_count() -> int:
    row = get_db().execute(
        'SELECT COUNT(*) FROM assistant_observations WHERE folded_at IS NULL'
    ).fetchone()
    return int(row[0]) if row else 0


def add_observation(content: str, *, source: str = 'chat',
                    now: int | None = None) -> dict | None:
    """Record one observation. Returns the stored row, or None if it was a
    duplicate of something already pending.

    Duplicates are dropped rather than refused because the model re-stating a
    fact it already saved is a normal thing for it to do across a long
    conversation — it can see the pending block in its own prompt, but only the
    first PROMPT_LIMIT of it. Returning None lets the caller say so plainly
    instead of writing a second identical row.
    """
    content = ' '.join((content or '').split())
    if not content:
        return None
    if len(content) > MAX_CHARS:
        raise ValueError(f'an observation is limited to {MAX_CHARS} characters')

    db = get_db()
    existing = db.execute(
        'SELECT * FROM assistant_observations'
        ' WHERE folded_at IS NULL AND content=? COLLATE NOCASE',
        (content,),
    ).fetchone()
    if existing is not None:
        return None

    if pending_count() >= MAX_PENDING:
        raise ObservationsFull(
            f'there are already {MAX_PENDING} notes waiting to be filed'
        )

    now = now if now is not None else int(time.time())
    obs_id = str(ULID())
    db.execute(
        'INSERT INTO assistant_observations(id, content, source, created_at)'
        ' VALUES (?,?,?,?)',
        (obs_id, content, source, now),
    )
    db.commit()
    return row_to_dict(
        db.execute('SELECT * FROM assistant_observations WHERE id=?', (obs_id,)).fetchone()
    )


def delete_observation(observation_id: str) -> bool:
    db = get_db()
    cur = db.execute('DELETE FROM assistant_observations WHERE id=?', (observation_id,))
    db.commit()
    return cur.rowcount > 0


def format_observations_context(limit: int = PROMPT_LIMIT) -> str:
    """The system-prompt block, or '' when nothing is pending.

    Says plainly that these are the assistant's own notes rather than the
    user's, because the two blocks sit next to each other and they do not carry
    the same authority: the user wrote one of them.
    """
    rows = pending(limit)
    if not rows:
        return ''
    lines = '\n'.join(f'- {r["content"]}' for r in rows)
    return (
        "Things you have noticed about the user yourself, in earlier "
        "conversations. Unlike the document above, these are your own notes and "
        "may be wrong or out of date — treat them as leads rather than fact, and "
        "let the user correct them. Don't recite them back:\n\n" + lines
    )
