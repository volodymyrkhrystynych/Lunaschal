"""The overnight-briefing sweep body.

A plain function (no Flask request, no thread) so it can be called directly by
the scheduler thread, by the manual-trigger route, and by tests. It find-or-
creates today's chat and drops in the AI briefing as the first assistant
message. The to-dos it suggests are *today's plan*, not backlog: they land
straight in `chat_todos` — the same day-scoped table the chat delegate's
`add_todos` tool writes to — with no confirm step, since the Chat tab's bar
above the input is where the user edits, completes, dismisses or promotes them
afterward. Most of the plan restates the user's open to-dos and pending daily
tasks on purpose, and each such row is *linked* to the one it restates
(backend/day_plan.py), so ticking it in the bar ticks the real one and the
other way round. `_seed_chat_todos` only skips a title or a linked row already
in today's bar, so a forced re-run can't duplicate it.

Before planning, the previous days' bars are tallied (`day_plan.tally_day`):
an item left undone day after day sits out of the plan for longer each time
(backend/day_plan_backoff.py), and once it has been skipped often enough a
`todo_review` card asks whether to keep it, let it go or break it down.

Yesterday's *unfinished* bar items are the one thing that does not go straight
in. They come back as `todo` suggestion cards on the briefing message, in the
same queue as yesterday's reconstructed calendar events (`stage_carryover_todos`),
so a carried-over item is something the user chooses to keep rather than one
more row mixed in with the day's own plan. Accepting one
(`backend/routes/chat.py`'s `_accept_todo`) writes it into today's bar.

The plan is the part the user actually needs, so it never comes back empty while
they have something pending: a model that returns no items (or no usable
completion at all) falls back to `fallback_plan`, built from their own lists.

The message ends with the server-oddities section (backend/oddities_job.py),
appended to the stored content rather than asked of the model, so it is there
whatever the model wrote and the day's chat can be asked about it.
"""
import json
import time
import threading
from datetime import date, timedelta

from ulid import ULID

from backend.db.connection import get_db
from backend.day_boundary import day_key_for
from backend.ai.provider import is_ai_configured
from backend import day_plan
from backend.ai.briefing import (
    fallback_plan,
    gather_briefing_context,
    generate_briefing,
    FALLBACK_BRIEFING,
    MAX_BRIEFING_TODOS,
    MAX_NEW_TODOS,
)
from backend.ai.llm import EmptyCompletion
from backend.briefing_events import stage_events
from backend.coach import review as day_review
from backend.goals import rollover as goal_rollover
from backend.oddities_job import briefing_report, render_markdown as render_oddities
from backend.routes.tasks import (
    _parse_priority, _parse_due, _today_bar_sources, _today_chat_todo_titles,
)

# How many past days' bars a briefing tallies, so a server that was off for a
# few nights still counts the skips it missed (each day is judged once).
TALLY_DAYS = 7


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


def _seed_chat_todos(db, proposed: list, today: str, now: int, *,
                     held: set[str] | frozenset = frozenset(),
                     goal_ids: dict[str, str] | None = None) -> int:
    """Validate/clamp each briefing-suggested item and insert it straight into
    chat_todos — no confirm step. The plan is deliberately allowed to restate
    an open todo or a pending daily task (that's most of what it is), and such
    an item is linked to the row it restates. It skips a title or a linked row
    already in today's bar, so a forced re-run or a same-batch repeat can't
    duplicate it, and anything the user has been skipping (`held`) — the
    prompt already left those out, this holds even if the model didn't.
    Caps at MAX_BRIEFING_TODOS, of which at most MAX_NEW_TODOS may be new
    (from a goal or the journal rather than their lists). Returns the count
    actually inserted."""
    taken = _today_chat_todo_titles(db, today)
    linked = _today_bar_sources(db, today)
    sources = day_plan.list_sources(db, today)
    goal_ids = goal_ids or {}
    seen: set[str] = set()
    inserted = new = 0
    for item in proposed:
        if inserted >= MAX_BRIEFING_TODOS:
            break
        if not isinstance(item, dict):
            continue
        title = (item.get('title') or '').strip()
        norm = day_plan.normalize_title(title)
        if not title or norm in seen or norm in taken:
            continue

        source = None
        if item.get('sourceType') in day_plan.LIST_SOURCES and item.get('sourceId'):
            source = (item['sourceType'], item['sourceId'])
        else:
            source = day_plan.resolve_list_source(db, today, title, item.get('linkedTitle'), sources)
        if source and source in linked:
            continue
        basis = item.get('basis')
        is_new = source is None and basis in ('goal', 'journal')
        if is_new and new >= MAX_NEW_TODOS:
            continue
        if source is None:
            if basis == 'goal':
                goal_id = goal_ids.get(day_plan.normalize_title(item.get('basisRef')))
                source = ('goal', goal_id) if goal_id else None
            elif basis == 'journal':
                source = ('journal', None)
        if day_plan.is_held_back(held, *(source or (None, None)), title):
            continue

        priority, err = _parse_priority(item.get('priority'))
        if err:
            priority = 3
        due, err = _parse_due(item.get('due'))
        if err:
            due = None
        reason = item.get('reason') if isinstance(item.get('reason'), str) else None
        day_plan.insert_bar_row(db, today, title, now, due=due, priority=priority,
                                source=source, reason=reason)
        seen.add(norm)
        if source and source[0] in day_plan.LIST_SOURCES:
            linked.add(source)
        # An unmatched "list" item is a wording the matcher missed, not a new
        # idea; only goal and journal items count against the cap.
        new += is_new
        inserted += 1
    return inserted


def tally_previous_days(db, today: str, now: int) -> None:
    """Judge the bars of the days before `today` that haven't been yet."""
    start = date.fromisoformat(today)
    for back in range(TALLY_DAYS, 0, -1):
        day_plan.tally_day(db, (start - timedelta(days=back)).isoformat(), now)


def stage_todo_reviews(db, now: int) -> list[dict]:
    """A "keep it or let it go?" card for each item skipped often enough. The
    card defaults to letting it go — the honest reading of a week of skips —
    and dismissing it keeps suggesting the item, spaced out."""
    proposals = []
    for s in day_plan.review_candidates(db):
        source_type, source_id = day_plan.parse_key(s['item_key'])
        if source_type in day_plan.LIST_SOURCES and not day_plan.source_exists(db, source_type, source_id):
            # Finished or deleted since; nothing to ask about.
            day_plan.drop(db, s['item_key'], now)
            continue
        proposals.append({
            'id': str(ULID()), 'kind': 'todo_review', 'status': 'pending',
            'data': {'itemKey': s['item_key'], 'title': s['title'],
                     'streak': s['ignored_streak'], 'sourceType': source_type,
                     'choice': 'drop'},
        })
        day_plan.mark_asked(db, s['item_key'], now)
    return proposals


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
    taken = _today_chat_todo_titles(db, today) | {
        day_plan.normalize_title(t) for t in _carried_titles(db, conv_id, yesterday)}
    linked = _today_bar_sources(db, today)
    # Sitting out per the backoff, or about to be asked about on a review card:
    # neither should also come back as "keep this for today?".
    held = day_plan.held_back_keys(db, today) | {
        s['item_key'] for s in db.execute(
            'SELECT item_key FROM chat_todo_suggestions WHERE asked_at IS NOT NULL')}
    rows = db.execute(
        'SELECT id, title, notes, due, priority, source_type, source_id, reason FROM chat_todos'
        ' WHERE day_key=? AND done=0 ORDER BY created_at, id',
        (yesterday,),
    ).fetchall()
    proposals = []
    for r in rows:
        title = (r['title'] or '').strip()
        norm = day_plan.normalize_title(title)
        if not title or norm in taken:
            continue
        source = (r['source_type'], r['source_id']) if r['source_id'] else None
        if source and source in linked:
            continue
        if source and source[0] in day_plan.LIST_SOURCES and not day_plan.source_exists(db, *source):
            continue
        if day_plan.is_held_back(held, r['source_type'], r['source_id'], title):
            continue
        taken.add(norm)
        proposals.append({
            'id': str(ULID()), 'kind': 'todo', 'status': 'pending',
            'carriedFrom': yesterday,
            'data': {'title': title, 'notes': r['notes'], 'due': r['due'],
                     'priority': r['priority'] or 3, 'reason': r['reason'],
                     'sourceType': r['source_type'], 'sourceId': r['source_id']},
        })
    return proposals


HABIT_SUGGESTION_DAYS = 7


def _may_suggest_habit(db, habit_count: int, now: int) -> bool:
    """At most one suggested habit a week, and never into a full list."""
    from backend.routes.tasks import MAX_TASKS
    if habit_count >= MAX_TASKS:
        return False
    since = now - HABIT_SUGGESTION_DAYS * 86400
    for row in db.execute("SELECT metadata FROM messages WHERE role='assistant' AND created_at>=? "
                          "AND metadata LIKE '%\"habit\"%'", (since,)):
        try:
            if any(p.get('kind') == 'habit' for p in json.loads(row['metadata']).get('proposals', [])):
                return False
        except (TypeError, ValueError, AttributeError):
            continue
    return True


def _habit_suggestion(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    title = (raw.get('title') or '').strip()[:200]
    if not title:
        return None
    return {'id': str(ULID()), 'kind': 'habit', 'status': 'pending',
            'data': {'title': title, 'cue': (raw.get('cue') or '').strip()[:200],
                     'why': (raw.get('why') or '').strip()[:300]}}


def stage_habit_plans(db, conv_id: str, review: dict) -> list[dict]:
    """The review's if-then plans for recurring obstacles, as `habit_plan`
    cards. Accepting one sets that habit's cue. A plan whose habit title
    doesn't match a habit is dropped — a cue needs somewhere to go."""
    habits = {r['title'].strip().lower(): r for r in db.execute('SELECT id, title, cue FROM daily_tasks')}
    proposals = []
    for plan in review.get('replan') or []:
        habit = habits.get((plan.get('habitTitle') or '').strip().lower())
        if not habit or not (plan.get('ifThen') or '').strip():
            continue
        proposals.append({
            'id': str(ULID()), 'kind': 'habit_plan', 'status': 'pending',
            'data': {'taskId': habit['id'], 'habitTitle': habit['title'],
                     'obstacle': plan.get('obstacle') or '', 'cue': plan['ifThen'].strip()[:200]},
        })
    return proposals


def _oddities_report(now: int) -> dict:
    """The server-oddities section never costs the briefing: a broken read of
    its tables reports the check as not having run, which is what it amounts to."""
    try:
        return briefing_report(now)
    except Exception as e:
        print(f'Server oddities report failed: {e}')
        return {'ran': False, 'journalAvailable': False, 'items': [], 'more': 0}


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

    # Close finished goal periods and review yesterday before planning today:
    # the plan should know how yesterday went, and the review leads the message.
    # Neither may cost the briefing.
    try:
        goal_rollover.run(now)
    except Exception as e:
        print(f'Goal rollover before the briefing failed: {e}')
    try:
        review = day_review.finalize(day_review.yesterday(now), now)
    except Exception as e:
        print(f"Yesterday's review failed: {e}")
        review = None
    shown_review = review if review and review['items'] else None

    # Before gathering, so what the user kept skipping is already held back
    # from the lists the model reads.
    tally_previous_days(db, day_key, now)
    context = gather_briefing_context(now)
    context['review'] = shown_review
    context['habits'] = [dict(r) for r in db.execute('SELECT title FROM daily_tasks ORDER BY position')]
    context['suggest_habit'] = _may_suggest_habit(db, len(context['habits']), now)
    if date.fromisoformat(day_key).weekday() == 6:
        context['week'] = day_review.recent(7, day_key)
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

    seed = {'held': context.get('held', frozenset()), 'goal_ids': context.get('goal_ids')}
    inserted = _seed_chat_todos(db, result.get('todos', []), context['today'], now, **seed)
    if not inserted:
        # The model dropped the plan — it sometimes writes one into the check-in
        # prose and then returns an empty array, which renders as nothing at all.
        # Their own lists are a better answer than silence.
        inserted = _seed_chat_todos(db, fallback_plan(context), context['today'], now, **seed)
    # Review cards first, so an item about to be asked about isn't also
    # carried over; carry-overs after the seed, so a title the plan already
    # put back in the bar isn't also offered as a card.
    reviews = stage_todo_reviews(db, now)
    carried = stage_carryover_todos(db, conv_id, context['today'])
    oddities = _oddities_report(now)
    if (degraded and not inserted and not carried and not reviews and not oddities['items']
            and not shown_review):
        # No prose, nothing added, nothing carried over and nothing wrong with
        # the server: there's no briefing to leave. A server problem alone is
        # still worth waking to.
        db.commit()
        return None

    content = f"{briefing}\n\n{render_oddities(oddities)}"
    metadata = {'briefing': True}
    replans = []
    if shown_review:
        content = f"{day_review.render(shown_review)}\n\n{content}"
        metadata['review'] = day_review.card(shown_review)
        replans = stage_habit_plans(db, conv_id, shown_review)
    if oddities['items']:
        metadata['oddities'] = [i['fingerprint'] for i in oddities['items']]
    events = stage_events(db, result.get('events', []), context.get('yesterday'))
    suggestion = _habit_suggestion(result.get('habitSuggestion')) if context['suggest_habit'] else None
    proposals = events + carried + reviews + replans + ([suggestion] if suggestion else [])
    if proposals:
        metadata['proposals'] = proposals
    if degraded:
        metadata['degraded'] = True
    message_id = str(ULID())
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (message_id, conv_id, 'assistant', content, json.dumps(metadata), now),
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
        'oddities': len(oddities['items']),
        'degraded': degraded,
    }
