"""The Chat tab's day-plan bar (`chat_todos`) and what it stands for.

Two jobs, both about the bar being more than a list of strings:

**Links.** A bar row that restates an open to-do or a pending daily task
carries `source_type`/`source_id`, and ticking either one ticks the other —
in both directions, through the same completion helpers the Tasks view uses
(backend/routes/tasks.py), so a linked tick logs one Journal notification, not
two. The link was there once (resolved from the briefing's `linkedTitle`) and
was lost when the plan moved from message metadata into `chat_todos`; the
briefing prompt kept promising it the whole time. `goal` and `journal`
sources are only provenance: ticking a goal nudge does not mark the goal met —
goals are judged from evidence, not from a checkbox.

**What became of each suggestion.** `chat_todo_suggestions` remembers, per
item (`suggestion_key`), how many days in a row it was left undone. The tally
runs lazily when the next briefing is built (`tally_day`), so there is no
scheduler; `backend/day_plan_backoff.py` turns the streak into days out of the
plan, and at `ASK_AT_STREAK` the briefing asks whether to keep it at all
(`todo_review` card). Everything is filtered *before* the model sees the lists
(`held_back_keys`), so the rule holds whatever the model decides to write.
"""
import re
import time

from backend import day_plan_backoff as backoff

LIST_SOURCES = ('todo', 'daily')
SOURCE_TYPES = ('todo', 'daily', 'goal', 'journal')

# The briefing prompt annotates list items — "Buy milk (due 2026-07-30)
# [priority 4/5] [shopping]" — and a model copying one into linkedTitle
# sometimes keeps the annotations. Strip them before matching.
_ANNOTATION = re.compile(r'\s*(\(due \d{4}-\d{2}-\d{2}\)|\[[^\]]*\])\s*$')


def normalize_title(title: str | None) -> str:
    return ' '.join((title or '').split()).lower()


def clean_linked_title(title) -> str:
    if not isinstance(title, str):
        return ''
    title = title.strip()
    while True:
        stripped = _ANNOTATION.sub('', title)
        if stripped == title:
            return title
        title = stripped


def suggestion_key(source_type: str | None, source_id: str | None, title: str | None) -> str:
    """What a suggestion *is*, stable across days: the row it restates when it
    has one (so a renamed to-do keeps its history), else its title."""
    if source_type in LIST_SOURCES and source_id:
        return f'{source_type}:{source_id}'
    if source_type == 'goal' and source_id:
        return f'goal:{source_id}:{normalize_title(title)}'
    return f'title:{normalize_title(title)}'


# --- Links ---------------------------------------------------------------

def list_sources(db, today: str) -> dict[str, tuple[str, str]]:
    """`{normalized title: (type, id)}` for everything a bar item can restate:
    open to-dos (not the archive) and daily tasks still pending on `today`.
    To-dos win a title collision — most of the plan is drawn from them."""
    from backend.ai.briefing import pending_daily_tasks
    lookup: dict[str, tuple[str, str]] = {}
    for r in pending_daily_tasks(db, today):
        lookup[normalize_title(r['title'])] = ('daily', r['id'])
    for r in db.execute("SELECT id, title FROM todos WHERE done=0 AND list != 'archive'"):
        lookup[normalize_title(r['title'])] = ('todo', r['id'])
    return lookup


def resolve_list_source(db, today: str, title: str, linked_title=None,
                        sources: dict | None = None) -> tuple[str, str] | None:
    """The open to-do or pending daily task this item restates, if any.

    The model's declared link is tried first — it sees the same titles and can
    match on meaning — then the item's own title, so a verbatim restatement
    links even when the model forgot to say so."""
    sources = sources if sources is not None else list_sources(db, today)
    for candidate in (clean_linked_title(linked_title), title):
        hit = sources.get(normalize_title(candidate)) if candidate else None
        if hit:
            return hit
    return None


def source_exists(db, source_type: str | None, source_id: str | None) -> bool:
    if source_type == 'todo' and source_id:
        return db.execute('SELECT 1 FROM todos WHERE id=?', (source_id,)).fetchone() is not None
    if source_type == 'daily' and source_id:
        return db.execute('SELECT 1 FROM daily_tasks WHERE id=?', (source_id,)).fetchone() is not None
    return False


def source_open(db, source_type: str | None, source_id: str | None) -> bool:
    """Still something to do: a to-do that exists, isn't done and isn't set
    aside in the archive, or a habit that exists. `source_exists` is the
    weaker check — a finished to-do still exists."""
    if source_type == 'todo' and source_id:
        return db.execute(
            "SELECT 1 FROM todos WHERE id=? AND done=0 AND list != 'archive'", (source_id,),
        ).fetchone() is not None
    return source_exists(db, source_type, source_id)


def source_title(db, source_type: str | None, source_id: str | None) -> str | None:
    table = {'todo': 'todos', 'daily': 'daily_tasks'}.get(source_type or '')
    if not table or not source_id:
        return None
    row = db.execute(f'SELECT title FROM {table} WHERE id=?', (source_id,)).fetchone()
    return row['title'] if row else None


def mark_bar_for_source(db, source_type: str, source_id: str, day: str, done: bool, now: int) -> int:
    """The other direction: a to-do or habit ticked (or unticked) anywhere
    else sets the matching bar rows for `day`. No event of its own — the
    source's completion already logged one. Returns rows changed."""
    if done:
        cur = db.execute(
            'UPDATE chat_todos SET done=1, completed_at=COALESCE(completed_at, ?), updated_at=?'
            ' WHERE source_type=? AND source_id=? AND day_key=? AND done=0',
            (now, now, source_type, source_id, day),
        )
        reset_streak(db, suggestion_key(source_type, source_id, None), now)
    else:
        cur = db.execute(
            'UPDATE chat_todos SET done=0, completed_at=NULL, updated_at=?'
            ' WHERE source_type=? AND source_id=? AND day_key=? AND done=1',
            (now, source_type, source_id, day),
        )
    return cur.rowcount


def complete_source(db, source_type: str, source_id: str, day: str, now: int) -> None:
    """Tick the real row a bar item restates, exactly the way the Tasks view
    would (and so with its notification)."""
    from backend.routes.tasks import complete_daily_task, complete_todo_row
    if source_type == 'todo':
        complete_todo_row(db, source_id, now)
    elif source_type == 'daily':
        complete_daily_task(db, source_id, day, now)


def uncomplete_source(db, source_type: str, source_id: str, day: str, now: int,
                      bar_row_id: str | None = None) -> None:
    from backend.routes.tasks import uncomplete_daily_task, _uncomplete_todo_row
    if source_type == 'todo':
        todo = db.execute('SELECT repeat_interval, repeat_unit FROM todos WHERE id=?',
                          (source_id,)).fetchone()
        if todo and todo['repeat_interval'] and todo['repeat_unit']:
            _reopen_occurrence(db, source_id, bar_row_id, now)
        else:
            _uncomplete_todo_row(db, source_id, now)
    elif source_type == 'daily':
        uncomplete_daily_task(db, source_id, day, now)


def record_occurrence_due(db, todo_id: str, day: str, due) -> None:
    """Completing a repeating to-do moves its due date on, and nothing else
    remembers where it was. The bar rows ticked with it keep the old one, so
    unticking one can put this occurrence back rather than guess."""
    db.execute(
        "UPDATE chat_todos SET source_prev_due=? WHERE source_type='todo' AND source_id=?"
        ' AND day_key=?',
        (due, todo_id, day),
    )


def _reopen_occurrence(db, todo_id: str, bar_row_id: str | None, now: int) -> None:
    """Untick a bar row whose repeating to-do it completed: restore that
    occurrence's due date and retract that one completion. A repeating to-do
    is never done, so `_uncomplete_todo_row` (which retracts every completion
    it ever logged) is the wrong tool. Without a remembered due date — a row
    ticked before it was recorded — only the bar row is unticked."""
    row = db.execute('SELECT completed_at, source_prev_due FROM chat_todos WHERE id=?',
                     (bar_row_id,)).fetchone() if bar_row_id else None
    if row and row['source_prev_due'] is not None:
        db.execute('UPDATE todos SET due=?, updated_at=? WHERE id=?',
                   (row['source_prev_due'], now, todo_id))
        db.execute(
            "DELETE FROM task_events WHERE id = (SELECT id FROM task_events"
            " WHERE kind='todo_completed' AND ref_id=? AND created_at >= ?"
            ' ORDER BY created_at DESC LIMIT 1)',
            (todo_id, row['completed_at'] or 0),
        )
    if bar_row_id:
        db.execute('UPDATE chat_todos SET source_prev_due=NULL WHERE id=?', (bar_row_id,))


# --- What became of each suggestion -------------------------------------

def _upsert(db, key: str, title: str, now: int) -> dict:
    db.execute(
        'INSERT OR IGNORE INTO chat_todo_suggestions(item_key, title, updated_at) VALUES (?,?,?)',
        (key, title, now),
    )
    return dict(db.execute('SELECT * FROM chat_todo_suggestions WHERE item_key=?', (key,)).fetchone())


def reset_streak(db, key: str, now: int) -> None:
    """It got done: forget the skips, and let a later run of them ask again."""
    db.execute(
        'UPDATE chat_todo_suggestions SET ignored_streak=0, snoozed_until=NULL, asked_at=NULL,'
        ' updated_at=? WHERE item_key=?',
        (now, key),
    )


def _row_key(row) -> str:
    return suggestion_key(row['source_type'], row['source_id'], row['title'])


def _done_elsewhere(db, row, day: str) -> bool:
    """A linked row left unticked in the bar whose source was finished anyway
    (on another device before the sync existed, or deleted outright) was not
    ignored."""
    if row['source_type'] == 'todo':
        todo = db.execute('SELECT done FROM todos WHERE id=?', (row['source_id'],)).fetchone()
        return todo is None or bool(todo['done'])
    if row['source_type'] == 'daily':
        if not source_exists(db, 'daily', row['source_id']):
            return True
        return db.execute(
            'SELECT 1 FROM daily_task_completions WHERE task_id=? AND date=?',
            (row['source_id'], day),
        ).fetchone() is not None
    return False


def tally_day(db, day: str, now: int | None = None) -> None:
    """Judge `day`'s bar: each undone item lengthens its streak and is held
    back per backoff; each done one resets it. Idempotent per day
    (`last_judged_day`), so a forced briefing re-run cannot count a day twice."""
    now = now if now is not None else int(time.time())
    rows = db.execute(
        'SELECT title, done, source_type, source_id FROM chat_todos WHERE day_key=?', (day,),
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        key = _row_key(row)
        if key in seen:
            continue
        seen.add(key)
        s = _upsert(db, key, row['title'], now)
        if s['last_judged_day'] and s['last_judged_day'] >= day:
            continue
        if row['done'] or _done_elsewhere(db, row, day):
            db.execute(
                'UPDATE chat_todo_suggestions SET title=?, last_judged_day=?, ignored_streak=0,'
                ' snoozed_until=NULL, asked_at=NULL, updated_at=? WHERE item_key=?',
                (row['title'], day, now, key),
            )
            continue
        streak = s['ignored_streak'] + 1
        until = backoff.eligible_from(day, streak, daily=row['source_type'] == 'daily')
        # Never shorten a hold the user set (a dismissal can reach further).
        if s['snoozed_until'] and (until is None or s['snoozed_until'] > until):
            until = s['snoozed_until']
        db.execute(
            'UPDATE chat_todo_suggestions SET title=?, last_judged_day=?, ignored_streak=?,'
            ' total_ignored=total_ignored+1, snoozed_until=?, updated_at=? WHERE item_key=?',
            (row['title'], day, streak, until, now, key),
        )


def record_card_dismissal(db, data: dict, today: str, now: int) -> None:
    """A `todo` card (a carry-over, or something heard in the journal) turned
    down: it sits out like a ✕ in the bar, so neither the journal pass nor the
    briefing offers it again tomorrow. Not counted as a skip — a carry-over's
    day was already tallied, and a journal card was never in the plan."""
    title = (data.get('title') or '').strip()
    if not title:
        return
    source_type, source_id = data.get('sourceType'), data.get('sourceId')
    key = suggestion_key(source_type, source_id, title)
    s = _upsert(db, key, title, now)
    until = backoff.dismissed_until(today, daily=source_type == 'daily')
    if s['snoozed_until'] and s['snoozed_until'] > until:
        until = s['snoozed_until']
    db.execute(
        'UPDATE chat_todo_suggestions SET snoozed_until=?, dismissed_at=?, updated_at=? WHERE item_key=?',
        (until, now, now, key),
    )


def record_dismissal(db, row, today: str, now: int) -> None:
    """A ✕ in the bar: a clear "not now", so it sits out a few days and counts
    as a skip. Recorded before the row is deleted — the delete used to leave
    no trace at all, which is half of why dismissed items kept coming back."""
    key = _row_key(row)
    s = _upsert(db, key, row['title'], now)
    until = backoff.dismissed_until(today, daily=row['source_type'] == 'daily')
    if s['snoozed_until'] and s['snoozed_until'] > until:
        until = s['snoozed_until']
    db.execute(
        'UPDATE chat_todo_suggestions SET title=?, last_judged_day=?, ignored_streak=ignored_streak+1,'
        ' total_ignored=total_ignored+1, snoozed_until=?, dismissed_at=?, updated_at=? WHERE item_key=?',
        (row['title'], today, until, now, now, key),
    )


def held_back_keys(db, today: str) -> set[str]:
    """Items the plan must not suggest today: let go of, or still sitting out."""
    return {
        r['item_key'] for r in db.execute(
            'SELECT item_key FROM chat_todo_suggestions'
            ' WHERE dropped_at IS NOT NULL OR (snoozed_until IS NOT NULL AND snoozed_until > ?)',
            (today,),
        )
    }


def is_held_back(held: set[str], source_type: str | None, source_id: str | None, title: str) -> bool:
    return (suggestion_key(source_type, source_id, title) in held
            or suggestion_key(None, None, title) in held)


def skipped_lately(db, today: str, limit: int = 8) -> list[dict]:
    """Repeatedly skipped and not let go — for the prompt, so the check-in
    doesn't nag about what the plan is deliberately leaving out."""
    rows = db.execute(
        'SELECT title, ignored_streak FROM chat_todo_suggestions'
        ' WHERE dropped_at IS NULL AND ignored_streak >= 2 ORDER BY ignored_streak DESC, updated_at DESC'
        ' LIMIT ?',
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def review_candidates(db) -> list[dict]:
    """Skipped often enough to ask about, and not yet asked this run."""
    rows = db.execute(
        'SELECT * FROM chat_todo_suggestions WHERE dropped_at IS NULL AND asked_at IS NULL'
        ' AND ignored_streak >= ? ORDER BY ignored_streak DESC, item_key',
        (backoff.ASK_AT_STREAK,),
    ).fetchall()
    return [dict(r) for r in rows]


def parse_key(key: str) -> tuple[str | None, str | None]:
    """(source_type, source_id) for a list-linked or goal key, else (None, None)."""
    kind, _, rest = key.partition(':')
    if kind in LIST_SOURCES:
        return kind, rest
    if kind == 'goal':
        return 'goal', rest.partition(':')[0]
    return None, None


def mark_asked(db, key: str, now: int) -> None:
    db.execute('UPDATE chat_todo_suggestions SET asked_at=?, updated_at=? WHERE item_key=?',
               (now, now, key))


def drop(db, key: str, now: int) -> None:
    db.execute('UPDATE chat_todo_suggestions SET dropped_at=?, updated_at=? WHERE item_key=?',
               (now, now, key))


def snooze(db, key: str, until: str, now: int) -> None:
    db.execute('UPDATE chat_todo_suggestions SET snoozed_until=?, updated_at=? WHERE item_key=?',
               (until, now, key))


def find_day_conversation(db, day_key: str) -> str | None:
    """The day's chat — the one with no Writing project or idea behind it."""
    row = db.execute(
        'SELECT id FROM conversations WHERE day_key=? AND writing_project_id IS NULL AND idea_id IS NULL',
        (day_key,),
    ).fetchone()
    return row['id'] if row else None


def pending_cards(db, conv_id: str | None) -> list[str]:
    """Titles of `todo` cards still waiting in the day's chat, so the journal
    pass and the chat agent don't offer the same thing twice."""
    import json
    if not conv_id:
        return []
    titles: list[str] = []
    for r in db.execute(
        "SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant'"
        " AND metadata LIKE '%\"todo\"%' ORDER BY created_at",
        (conv_id,),
    ):
        try:
            meta = json.loads(r['metadata'] or '{}')
        except (ValueError, TypeError):
            continue
        for p in meta.get('proposals', []) if isinstance(meta, dict) else []:
            if isinstance(p, dict) and p.get('kind') == 'todo' and p.get('status') == 'pending':
                title = ((p.get('data') or {}).get('title') or '').strip()
                if title:
                    titles.append(title)
    return titles


def pending_card_titles(db, conv_id: str | None) -> set[str]:
    return {normalize_title(t) for t in pending_cards(db, conv_id)}


def insert_bar_row(db, day: str, title: str, now: int, *, notes=None, due=None, priority: int = 3,
                   source: tuple[str | None, str | None] | None = None, reason: str | None = None) -> str:
    """The one INSERT into the bar, so every writer (the briefing, the chat
    tool, the bulk route, an accepted card) carries a link the same way."""
    from ulid import ULID
    source_type, source_id = source if source else (None, None)
    row_id = str(ULID())
    db.execute(
        'INSERT INTO chat_todos(id, day_key, title, notes, due, priority, done, created_at, updated_at,'
        ' source_type, source_id, reason) VALUES (?,?,?,?,?,?,0,?,?,?,?,?)',
        (row_id, day, title, notes, due, priority, now, now, source_type, source_id,
         (reason or '').strip()[:300] or None),
    )
    return row_id
