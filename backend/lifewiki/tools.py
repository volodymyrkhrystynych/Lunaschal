"""Read-only tools over the user's own record: past chats, journal, and a day.

The chat agent had none of these. Its whole picture of the user was whatever
`build_chat_system_prompt` could afford to rebuild on every turn — the memory
document, 24 hours of journal, three days of calendar — plus the current
conversation segment. Anything said last week, or written 25 hours ago, was
unreachable at any price.

Three rules shape what these return:

- **Snippets, never dumps.** The result of a tool call is pasted into the
  transcript and paid for on every later turn, which is the same reason
  `backend/delegate/agent.py` hands back only the delegate's closing summary. A
  hit here is a few lines and a date, enough for the model to decide whether it
  has its answer or needs to ask.
- **Hard character caps, applied to the whole result.** Not per hit — a caller
  asking for ten hits of two hundred characters each is the case that matters.
- **The current conversation is excluded from chat hits.** It is already in the
  transcript verbatim; returning it again would spend the budget re-reading what
  the model can already see.

Same duck type as `backend/research/code.py`'s CodeTools and
`backend/research/wiki.py`'s WikiTools — `run_tool(name, args) -> (text, event)`
— so the shared loop in `backend/research/agent.py` can dispatch to it unchanged
and the chat delegate can call it directly.
"""
import logging
from datetime import datetime

from backend.db.connection import fts_match_query, get_db, search_journal_fts
from backend.day_boundary import day_bounds, day_key_for

logger = logging.getLogger(__name__)

# Total characters one tool call may return. Sized so that two searches in one
# turn still leave the conversation itself the larger part of the prompt.
MAX_RESULT_CHARS = 2400

# Per-hit ceiling, applied before the total. A single rambling journal entry
# should not be able to spend the whole budget.
MAX_HIT_CHARS = 400

DEFAULT_LIMIT = 5
MAX_LIMIT = 10

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'search_conversations',
            'description': (
                'Search everything the user has said to you in past '
                'conversations, by keyword. Use it when they refer back to '
                'something discussed before that is not in the conversation you '
                'can already see. Keyword search, not semantic — use words they '
                'would actually have said.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Keywords to search for.'},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_journal',
            'description': (
                "Search the user's journal by keyword, over all of it. The "
                'system prompt already carries the last 24 hours, so use this '
                'for anything older than that.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Keywords to search for.'},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'read_day',
            'description': (
                'Read what the user recorded on one particular day: journal '
                'entries, calendar events, workouts and meals. Use it when they '
                'ask about a specific day rather than a topic.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'date': {
                        'type': 'string',
                        'description': 'The day as YYYY-MM-DD.',
                    },
                },
                'required': ['date'],
            },
        },
    },
]

TOOL_NAMES = {t['function']['name'] for t in TOOLS}


def _clip(text: str, limit: int = MAX_HIT_CHARS) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def _join(blocks: list[str]) -> str:
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


def _when(ts: int) -> str:
    """A date the model can act on, with the weekday it will be asked about."""
    return datetime.fromtimestamp(ts).strftime('%a %d %b %Y, %H:%M')


def _limit_of(args: dict) -> int:
    try:
        limit = int(args.get('limit') or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


class LifeTools:
    """Bound to one conversation, so chat hits can exclude it.

    Binding an object rather than threading an argument through the shared loop
    is the same choice WikiTools makes for its repo scope, and for the same
    reason: `agent._loop` calls `dispatch[name].run_tool(...)` and knows nothing
    about either.
    """

    def __init__(self, conversation_id: str | None = None):
        self.conversation_id = conversation_id

    # --- chat transcripts -------------------------------------------------

    def search_conversations(self, query: str, limit: int = DEFAULT_LIMIT) -> tuple[str, dict]:
        event = {'tool': 'search_conversations', 'arg': query, 'ok': True, 'count': 0}
        match = fts_match_query(query)
        if not match:
            return ('No search terms in that query.', event)

        sql = (
            'SELECT m.id, m.role, m.created_at, m.content, c.title, c.day_key'
            ' FROM messages_fts'
            ' JOIN messages m ON m.rowid = messages_fts.rowid'
            ' JOIN conversations c ON c.id = m.conversation_id'
            " WHERE messages_fts MATCH ? AND m.role IN ('user','assistant')"
            "   AND m.content != ''"
        )
        params: list = [match]
        if self.conversation_id:
            sql += ' AND m.conversation_id != ?'
            params.append(self.conversation_id)
        sql += ' ORDER BY rank LIMIT ?'
        params.append(limit)

        try:
            rows = get_db().execute(sql, params).fetchall()
        except Exception as e:
            logger.warning('search_conversations failed: %s', e)
            return ('That search could not be run.',
                    {**event, 'ok': False, 'error': 'search failed'})

        if not rows:
            return (f'Nothing in past conversations matches "{query}".', event)

        blocks = []
        for r in rows:
            who = 'The user said' if r['role'] == 'user' else 'You replied'
            title = f' — {r["title"]}' if r['title'] else ''
            blocks.append(
                f'[{_when(r["created_at"])}{title}]\n{who}: {_clip(r["content"])}'
            )
        return (
            'From past conversations (most relevant first):\n\n' + _join(blocks),
            {**event, 'count': len(rows)},
        )

    # --- journal ----------------------------------------------------------

    def search_journal(self, query: str, limit: int = DEFAULT_LIMIT) -> tuple[str, dict]:
        event = {'tool': 'search_journal', 'arg': query, 'ok': True, 'count': 0}
        hits = search_journal_fts(query, limit)
        if not hits:
            return (f'No journal entries match "{query}".', event)

        db = get_db()
        order = {h['id']: i for i, h in enumerate(hits)}
        placeholders = ','.join('?' * len(order))
        rows = db.execute(
            f'SELECT id, title, content, created_at FROM journal_entries'
            f' WHERE id IN ({placeholders})',
            list(order),
        ).fetchall()
        rows = sorted(rows, key=lambda r: order.get(r['id'], 0))

        blocks = []
        for r in rows:
            header = f'[{_when(r["created_at"])}]'
            if r['title']:
                header += f' {r["title"]}'
            blocks.append(f'{header}\n{_clip(r["content"])}')
        return (
            'From the journal (most relevant first):\n\n' + _join(blocks),
            {**event, 'count': len(rows)},
        )

    # --- one day ----------------------------------------------------------

    def read_day(self, date: str) -> tuple[str, dict]:
        """Everything recorded on one 4am-anchored day.

        The day is `backend/day_boundary.py`'s, not the calendar's — a journal
        entry written at 01:00 belongs to the day the user was still awake in,
        which is the day they will ask about.

        Chat transcripts are deliberately not included: a day's conversation can
        be longer than everything else here put together, and
        `search_conversations` already covers it.
        """
        event = {'tool': 'read_day', 'arg': date, 'ok': True, 'count': 0}
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except (TypeError, ValueError):
            return ('That is not a date I can read — use YYYY-MM-DD.',
                    {**event, 'ok': False, 'error': 'bad date'})

        db = get_db()
        start, end = day_bounds(date)
        sections: list[str] = []
        count = 0

        entries = db.execute(
            'SELECT title, content, created_at FROM journal_entries'
            ' WHERE created_at >= ? AND created_at < ? ORDER BY created_at',
            (start, end),
        ).fetchall()
        for e in entries:
            header = datetime.fromtimestamp(e['created_at']).strftime('%H:%M')
            if e['title']:
                header += f' {e["title"]}'
            sections.append(f'Journal [{header}]\n{_clip(e["content"])}')
        count += len(entries)

        from backend.calendar_query import events_in_range
        for ev in events_in_range(db, date, date):
            when = ev.get('time') or 'all day'
            if ev.get('end_time'):
                when += f'–{ev["end_time"]}'
            desc = f' — {ev["description"]}' if ev.get('description') else ''
            sections.append(f'Calendar [{when}]: {ev["title"]}{desc}')
            count += 1

        workouts = db.execute(
            'SELECT location_type, duration_minutes, intensity_rating, notes'
            ' FROM workout_sessions WHERE date=? ORDER BY created_at',
            (date,),
        ).fetchall()
        for w in workouts:
            from backend.lifestyle.activity import ACTIVITY_LABELS
            label = ACTIVITY_LABELS.get(w['location_type'], w['location_type'])
            bits = [label]
            if w['duration_minutes']:
                bits.append(f'{w["duration_minutes"]} min')
            if w['intensity_rating']:
                bits.append(f'intensity {w["intensity_rating"]}/5')
            line = f'Workout: {", ".join(bits)}'
            if w['notes']:
                line += f'\n{_clip(w["notes"])}'
            sections.append(line)
        count += len(workouts)

        meals = db.execute(
            'SELECT dish, place, notes, rating, created_at FROM food_entries'
            ' WHERE created_at >= ? AND created_at < ? ORDER BY created_at',
            (start, end),
        ).fetchall()
        for m in meals:
            when = datetime.fromtimestamp(m['created_at']).strftime('%H:%M')
            bits = [m['dish'] or 'a meal']
            if m['place']:
                bits.append(f'at {m["place"]}')
            if m['rating']:
                bits.append(f'{m["rating"]}/5')
            line = f'Food [{when}]: {", ".join(bits)}'
            if m['notes']:
                line += f'\n{_clip(m["notes"])}'
            sections.append(line)
        count += len(meals)

        calories = db.execute(
            'SELECT SUM(calories) AS total FROM calorie_logs WHERE date=?', (date,)
        ).fetchone()
        if calories and calories['total']:
            sections.append(f'Calories logged: {calories["total"]}')

        if not sections:
            return (f'Nothing was recorded on {date}.', event)
        return (f'What the user recorded on {date}:\n\n' + _join(sections),
                {**event, 'count': count})

    # --- dispatch ---------------------------------------------------------

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        args = args if isinstance(args, dict) else {}
        if name == 'search_conversations':
            return self.search_conversations(
                (args.get('query') or '').strip(), _limit_of(args)
            )
        if name == 'search_journal':
            return self.search_journal(
                (args.get('query') or '').strip(), _limit_of(args)
            )
        if name == 'read_day':
            return self.read_day((args.get('date') or '').strip() or day_key_for())
        return (f'Unknown tool: {name}', {'tool': name, 'ok': False, 'error': 'unknown tool'})
