"""The overnight-briefing sweep body.

A plain function (no Flask request, no thread) so it can be called directly by
the scheduler thread, by the manual-trigger route, and by tests. It find-or-
creates today's chat and drops in the AI briefing as the first assistant
message. The to-dos it suggests are *today's plan*, not backlog: they land
straight in `chat_todos` — the same day-scoped table the chat delegate's
`add_todos` tool writes to — with no confirm step, since the Chat tab's bar
above the input is where the user edits, completes, dismisses or promotes them
afterward. `_seed_chat_todos` skips anything already tracked (an open todo, a
pending daily task, or something already added to today's bar) so the same
item never shows up twice.

Yesterday's *unfinished* bar items are the one thing that does not go straight
in. They come back as `todo` suggestion cards on the briefing message, in the
same queue as yesterday's reconstructed calendar events (`stage_carryover_todos`),
so a carried-over item is something the user chooses to keep rather than one
more row mixed in with the day's own plan. Accepting one
(`backend/routes/chat.py`'s `_accept_todo`) writes it into today's bar.

The plan is the part the user actually needs, so it never comes back empty while
they have something pending: a model that returns no items (or no usable
completion at all) falls back to `fallback_plan`, built from their own lists.
"""
import json
import time
import threading
from datetime import date, timedelta

from ulid import ULID

from backend.db.connection import get_db
from backend.day_boundary import day_key_for
from backend.ai.provider import is_ai_configured
from backend.ai.briefing import (
    fallback_plan,
    gather_briefing_context,
    generate_briefing,
    FALLBACK_BRIEFING,
    MAX_BRIEFING_TODOS,
)
from backend.ai.llm import EmptyCompletion
from backend.briefing_events import stage_events
from backend.routes.tasks import (
    _parse_priority, _parse_due, _today_chat_todo_titles,
)


def find_or_create_day_conversation(db, day_key: str, now: int) -> str:
    row = db.execute(
        'SELECT id FROM conversations WHERE day_key=? AND writing_project_id IS NULL AND idea_id IS NULL',
        (day_key,),
    ).fetchone()
    if row:
        return row['id']
    conv_id = str(ULID())
    db.execute(
        'INSERT INTO conversations(id, title, day_key, created_at, updated_at) VALUES (?,?,?,?,?)',
        (conv_id, None, day_key, now, now),
    )
    return conv_id


def _has_briefing(db, conv_id: str) -> bool:
    rows = db.execute(
        "SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant'",
        (conv_id,),
    ).fetchall()
    for r in rows:
        if not r['metadata']:
            continue
        try:
            if json.loads(r['metadata']).get('briefing'):
                return True
        except (ValueError, TypeError):
            continue
    return False


def _seed_chat_todos(db, proposed: list, today: str, now: int) -> int:
    """Validate/clamp each briefing-suggested item and insert it straight into
    chat_todos — no confirm step. The plan is deliberately allowed to restate
    an open todo or a pending daily task (that's most of what it is); it only
    skips a title already sitting in today's bar, so a forced re-run or a
    same-batch repeat can't duplicate it. Caps at MAX_BRIEFING_TODOS. Returns
    the count actually inserted."""
    taken = _today_chat_todo_titles(db, today)
    seen: set[str] = set()
    inserted = 0
    for item in proposed:
        if inserted >= MAX_BRIEFING_TODOS:
            break
        if not isinstance(item, dict):
            continue
        title = (item.get('title') or '').strip()
        if not title or title.lower() in seen or title.lower() in taken:
            continue

        priority, err = _parse_priority(item.get('priority'))
        if err:
            priority = 3
        due, err = _parse_due(item.get('due'))
        if err:
            due = None

        db.execute(
            'INSERT INTO chat_todos(id, day_key, title, notes, due, priority, done, created_at, updated_at)'
            ' VALUES (?,?,?,NULL,?,?,0,?,?)',
            (str(ULID()), today, title, due, priority, now, now),
        )
        seen.add(title.lower())
        inserted += 1
    return inserted


def _carried_titles(db, conv_id: str, day: str) -> set[str]:
    """Lowercased titles already offered as carry-overs from `day` in this
    conversation, whatever became of them — a forced re-run must neither
    duplicate a pending card nor re-offer one the user already dropped."""
    taken: set[str] = set()
    rows = db.execute(
        "SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant'",
        (conv_id,),
    ).fetchall()
    for r in rows:
        try:
            meta = json.loads(r['metadata']) if r['metadata'] else {}
        except (ValueError, TypeError):
            continue
        for p in meta.get('proposals', []) if isinstance(meta, dict) else []:
            if isinstance(p, dict) and p.get('kind') == 'todo' and p.get('carriedFrom') == day:
                taken.add(((p.get('data') or {}).get('title') or '').strip().lower())
    return taken


def stage_carryover_todos(db, conv_id: str, today: str) -> list[dict]:
    """Yesterday's unfinished bar items as pending `todo` proposals.

    They are suggestions rather than rows: the bar is today's plan, and an item
    the user didn't get to yesterday is a decision (keep it, or let it go), not
    something to silently re-add beside the daily tasks. Skips anything already
    in today's bar — the plan may have restated it — and anything offered
    earlier today."""
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    taken = _today_chat_todo_titles(db, today) | _carried_titles(db, conv_id, yesterday)
    rows = db.execute(
        'SELECT id, title, notes, due, priority FROM chat_todos'
        ' WHERE day_key=? AND done=0 ORDER BY created_at, id',
        (yesterday,),
    ).fetchall()
    proposals = []
    for r in rows:
        title = (r['title'] or '').strip()
        if not title or title.lower() in taken:
            continue
        taken.add(title.lower())
        proposals.append({
            'id': str(ULID()), 'kind': 'todo', 'status': 'pending',
            'carriedFrom': yesterday,
            'data': {'title': title, 'notes': r['notes'], 'due': r['due'],
                     'priority': r['priority'] or 3},
        })
    return proposals


_briefing_lock = threading.Lock()


def run_briefing(now: int | None = None, force: bool = False) -> dict | None:
    # A manual trigger can race the nightly daemon. Serialize through staging
    # so its duplicate check sees the proposals the other invocation stored.
    with _briefing_lock:
        return _run_briefing(now, force)


def _run_briefing(now: int | None = None, force: bool = False) -> dict | None:
    """Generate and store today's briefing. Returns a summary dict, or None if it
    was skipped (AI unconfigured, or a briefing already exists and not forced)."""
    if not is_ai_configured():
        return None
    now = now if now is not None else int(time.time())
    db = get_db()
    day_key = day_key_for(now)
    conv_id = find_or_create_day_conversation(db, day_key, now)

    if _has_briefing(db, conv_id) and not force:
        db.commit()  # persist the conversation if we just created it
        return None

    context = gather_briefing_context(now)
    degraded = False
    try:
        result = generate_briefing(context)
    except EmptyCompletion:
        # A completion we can't parse (empty, truncated mid-JSON, prose instead of
        # JSON) used to mean the 4am run left nothing behind at all, and the user
        # woke up to no idea what their day held. Keep the plan and say the prose
        # is missing. A manual run still raises: the user is standing right there
        # and can retry or switch models.
        if force:
            raise
        result, degraded = {'briefing': FALLBACK_BRIEFING, 'todos': []}, True

    briefing = (result.get('briefing') or '').strip()
    if not briefing:
        db.commit()
        return None

    inserted = _seed_chat_todos(db, result.get('todos', []), context['today'], now)
    if not inserted:
        # The model dropped the plan — it sometimes writes one into the check-in
        # prose and then returns an empty array, which renders as nothing at all.
        # Their own lists are a better answer than silence.
        inserted = _seed_chat_todos(db, fallback_plan(context), context['today'], now)
    # After the seed, so a title the plan already put back in the bar isn't
    # also offered as a card.
    carried = stage_carryover_todos(db, conv_id, context['today'])
    if degraded and not inserted and not carried:
        # No prose *and* nothing added: there's no briefing to leave.
        db.commit()
        return None

    metadata = {'briefing': True}
    events = stage_events(db, result.get('events', []), context.get('yesterday'))
    proposals = events + carried
    if proposals:
        metadata['proposals'] = proposals
    if degraded:
        metadata['degraded'] = True
    message_id = str(ULID())
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (message_id, conv_id, 'assistant', briefing, json.dumps(metadata), now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conv_id))
    db.commit()
    return {
        'conversationId': conv_id,
        'messageId': message_id,
        'briefing': briefing,
        'todosAdded': inserted,
        'eventsSuggested': len(events),
        'todosCarried': len(carried),
        'degraded': degraded,
    }
