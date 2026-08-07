import time
from datetime import date, datetime, timedelta

from backend.ai.llm import chat_stream_deltas

SYSTEM_PROMPT = """You are Lunaschal, the user's seneschal — their second-in-command, running
the day-to-day so nothing falls through the cracks.

Talk like a trusted chief of staff, not a hype-man or a yes-man: direct, organized, and
plainly on the user's side. React to what they actually said, ask the practical follow-up
question that moves things forward, and give a real opinion when asked rather than hedging.
If something they say doesn't add up against what you know of their day — a plan that
conflicts with their schedule, a task abandoned mid-stream, a habit slipping — say so plainly
("what happened to X?") rather than letting it pass unremarked; pushback in service of the
user's own goals is part of the job, not rudeness. Keep replies short and to the point — a
couple of sentences unless the user clearly wants depth. Don't list your capabilities or
turn every message into a task; it's fine for a chat to just be a chat.

If the user mentions something worth keeping — a memory, a plan, something they learned —
you may gently offer to save it, but never push.

If the user says "note to self" without saying what the lesson actually is,
ask them to spell it out before it can be saved.

If journal entries from the last 24 hours are included below, treat them as things the
user has recently been living and thinking about. Let them inform the conversation and
follow up on them naturally when relevant, but don't recite them back or announce that
you can see them.

If the user's schedule is included below, use it to know when they're busy or free —
so you can suggest things that actually fit their day and not pester them mid-commitment.
Same rule: let it shape what you say, don't read it back to them.

When extra context from the user's knowledge base is provided, weave it in naturally."""

JOURNAL_WINDOW_SECONDS = 86400
JOURNAL_MAX_ENTRIES = 10
JOURNAL_MAX_CHARS = 2000

# How far ahead the schedule block reaches (today plus this many days).
SCHEDULE_LOOKAHEAD_DAYS = 3
SCHEDULE_MAX_EVENTS = 20


def get_recent_journal_entries(now: int | None = None) -> list[dict]:
    """Journal entries from the last 24 hours, excluding fanfic-commentary
    entries (those linked via journal_entry_fic_refs), oldest first."""
    from backend.db.connection import get_db
    now = now if now is not None else int(time.time())
    rows = get_db().execute(
        '''SELECT title, content, created_at FROM journal_entries
           WHERE created_at >= ?
             AND id NOT IN (SELECT journal_entry_id FROM journal_entry_fic_refs)
           ORDER BY created_at DESC LIMIT ?''',
        (now - JOURNAL_WINDOW_SECONDS, JOURNAL_MAX_ENTRIES),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _format_entry_time(ts: int, now: int) -> str:
    dt = datetime.fromtimestamp(ts)
    days = (datetime.fromtimestamp(now).date() - dt.date()).days
    day = 'today' if days == 0 else 'yesterday' if days == 1 else dt.strftime('%b %d')
    return f"{day} {dt.strftime('%H:%M')}"


def format_journal_context(entries: list[dict], now: int | None = None) -> str:
    if not entries:
        return ''
    now = now if now is not None else int(time.time())
    parts = []
    for e in entries:
        content = e['content']
        if len(content) > JOURNAL_MAX_CHARS:
            content = content[:JOURNAL_MAX_CHARS] + '…'
        header = f"[{_format_entry_time(e['created_at'], now)}]"
        if e.get('title'):
            header += f" {e['title']}"
        parts.append(f"{header}\n{content}")
    return (
        "Here is what the user wrote in their journal over the last 24 hours "
        "(oldest first):\n\n" + '\n\n'.join(parts)
    )


def get_upcoming_schedule(now: int | None = None) -> list[dict]:
    """Calendar events from today through the lookahead horizon, with recurring
    series expanded into concrete occurrences."""
    from backend.calendar_query import events_in_range
    from backend.db.connection import get_db
    now = now if now is not None else int(time.time())
    today = datetime.fromtimestamp(now).date()
    horizon = today + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS)
    events = events_in_range(get_db(), today.isoformat(), horizon.isoformat())
    return events[:SCHEDULE_MAX_EVENTS]


def _format_event_day(iso: str, today) -> str:
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    days = (d - today).days
    if days == 0:
        return 'today'
    if days == 1:
        return 'tomorrow'
    return d.strftime('%a %b %d')


def format_schedule_context(events: list[dict], now: int | None = None) -> str:
    if not events:
        return ''
    now = now if now is not None else int(time.time())
    today = datetime.fromtimestamp(now).date()
    lines = []
    for e in events:
        when = _format_event_day(e['date'], today)
        span = ''
        if e.get('time'):
            span = f" {e['time']}"
            if e.get('end_time'):
                span += f"–{e['end_time']}"
        desc = f" — {e['description']}" if e.get('description') else ''
        lines.append(f"- {when}{span}: {e['title']}{desc}")
    return (
        "The user's schedule for today and the next few days:\n\n" + '\n'.join(lines)
    )


def build_chat_system_prompt(now: int | None = None) -> str:
    blocks = [
        format_journal_context(get_recent_journal_entries(now), now),
        format_schedule_context(get_upcoming_schedule(now), now),
    ]
    parts = [SYSTEM_PROMPT] + [b for b in blocks if b]
    return '\n\n'.join(parts)


TIME_PREFIX_NOTE = (
    "Each message in this conversation is prefixed with when it was sent, like "
    "[today 21:58]. Use them for anything time-sensitive — how long ago something "
    "was said, whether a plan has already come and gone, whether the user has been "
    "quiet for hours. The app adds those prefixes: never write one on your own replies."
)


def format_now_context(now: int | None = None) -> str:
    """Without this the model has no idea what time it is, which makes both the
    message prefixes and the relative day labels in the context blocks useless."""
    dt = datetime.fromtimestamp(now if now is not None else int(time.time()))
    return f"Right now it is {dt.strftime('%A, %d %B %Y, %H:%M')}."


def _parse_iso(value) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return None


def stamp_messages(messages: list[dict], now: int | None = None) -> list[dict]:
    """Prefix each message with when it was sent, as `[today 21:58] ...`.

    Callers that don't track timestamps (the voice listener keeps history in
    memory) simply pass none, and their messages go through untouched.
    """
    now = now if now is not None else int(time.time())
    out = []
    for m in messages:
        content = m.get('content', '')
        ts = _parse_iso(m.get('createdAt'))
        if ts is not None and m.get('role') != 'system':
            content = f"[{_format_entry_time(ts, now)}] {content}"
        out.append({'role': m.get('role'), 'content': content})
    return out


def chat_stream(
    messages: list[dict],
    system_prompt: str = '',
    with_time_context: bool = True,
):
    """`with_time_context=False` for one-shot utility calls (transcript cleanup)
    whose prompts demand an exact output shape and shouldn't carry a clock."""
    system = system_prompt or SYSTEM_PROMPT
    if with_time_context:
        system = f"{system}\n\n{format_now_context()}"
        if any(_parse_iso(m.get('createdAt')) is not None for m in messages):
            system = f"{system}\n\n{TIME_PREFIX_NOTE}"
        messages = stamp_messages(messages)
    else:
        messages = [{'role': m.get('role'), 'content': m.get('content', '')} for m in messages]

    all_messages = [{'role': 'system', 'content': system}] + messages
    yield from chat_stream_deltas(all_messages)
