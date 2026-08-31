"""Overnight briefing: gather the user's recent life-data and turn it into a
warm morning check-in plus a concrete to-do plan.

Gathering and prompt-building are kept pure and LLM-free so they can be
unit-tested; only `generate_briefing` hits the model (via `chat_json`).
"""
import time
from datetime import datetime, date, timedelta

from backend.ai.chat import get_recent_journal_entries, format_journal_context
from backend.ai.llm import chat_json
from backend.day_boundary import day_key_for

# How far ahead the calendar lookahead reaches, in days (today + this many).
CALENDAR_LOOKAHEAD_DAYS = 3
# Hard cap on how many todos a single briefing may create.
MAX_BRIEFING_TODOS = 5
# The briefing runs overnight (or on a manual trigger), so latency is a
# non-issue — give the model plenty of room to finish the JSON rather than risk
# truncating a longer check-in. This is a ceiling, not a target.
BRIEFING_MAX_TOKENS = 16384


def pending_daily_tasks(db, today: str) -> list[dict]:
    """Daily tasks not yet completed today (mirrors tasks.list_tasks' join).

    Public, along with `open_todos` and `learning_due_count`, because the chat
    system prompt's "today" block reads the same three things. The briefing knew
    more about the user's day than the assistant they were talking to did, from
    the same tables.

    The id rides along so the briefing job can link a proposal to the real row;
    only the title is ever printed into the prompt.
    """
    rows = db.execute(
        '''SELECT t.id, t.title
           FROM daily_tasks t
           LEFT JOIN daily_task_completions c ON c.task_id = t.id AND c.date = ?
           WHERE c.id IS NULL
           ORDER BY t.position''',
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


def open_todos(db) -> list[dict]:
    # Exclude the 'archive' list — that's the "set aside" stash, deliberately
    # off the active plan, so it shouldn't resurface in the morning briefing.
    rows = db.execute(
        '''SELECT title, list, due, priority FROM todos
           WHERE done=0 AND list != 'archive' ORDER BY priority DESC, created_at''',
    ).fetchall()
    return [dict(r) for r in rows]


def _upcoming_calendar(db, today: str, horizon: str) -> list[dict]:
    # Via events_in_range, not a raw SELECT: a recurring series only sits in the
    # table on its anchor date, so a plain date-range query would hide the
    # user's standing commitments on every other day.
    from backend.calendar_query import events_in_range
    return [
        {k: e.get(k) for k in ('title', 'description', 'date', 'time', 'end_time')}
        for e in events_in_range(db, today, horizon)
    ]


def learning_due_count(db, now: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM learning_cards WHERE state='active' AND due <= ?",
        (now,),
    ).fetchone()
    return int(row[0]) if row else 0


def life_wiki_index(limit: int = 12) -> list[dict]:
    """The life wiki's slugs and summaries.

    The reason backend/briefing_scheduler.py runs the wiki pass before this one:
    the briefing had been re-deriving who the user is from raw rows every single
    morning, when a standing picture of them now exists and is current as of
    minutes ago.

    Summaries only, never bodies — the briefing is one short check-in, not a
    recitation of everything known about the reader.
    """
    from backend.research import wiki
    return wiki.list_articles(limit, repo_id=None, kind=wiki.LIFE_KIND)


def gather_briefing_context(now: int | None = None) -> dict:
    """Assemble everything the briefing draws on. Pure w.r.t. the model — only
    reads the DB."""
    from backend.db.connection import get_db
    from backend.ai.provider import get_settings
    now = now if now is not None else int(time.time())
    db = get_db()
    today = day_key_for(now)
    horizon = (date.fromisoformat(today) + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)).isoformat()
    s = get_settings()
    return {
        'now': now,
        'today': today,
        'goals': (s.get('briefing_goals') if s else None) or '',
        'journal': get_recent_journal_entries(now),
        'daily_tasks': pending_daily_tasks(db, today),
        'todos': open_todos(db),
        'calendar': _upcoming_calendar(db, today, horizon),
        'learning_due': learning_due_count(db, now),
        'life_wiki': life_wiki_index(),
    }


def _format_todo(t: dict) -> str:
    parts = [t['title']]
    if t.get('due'):
        parts.append(f"(due {date.fromtimestamp(t['due']).isoformat()})")
    if t.get('priority') and t['priority'] != 3:
        parts.append(f"[priority {t['priority']}/5]")
    if t.get('list') and t['list'] != 'todo':
        parts.append(f"[{t['list']}]")
    return ' '.join(parts)


def build_briefing_prompt(context: dict) -> str:
    """Render the gathered context into the user-role prompt (pure)."""
    weekday = datetime.fromtimestamp(context['now']).strftime('%A')
    lines = [f"Today is {weekday}, {context['today']}.", '']

    goals = (context.get('goals') or '').strip()
    if goals:
        lines += ["The user's stated goals and current focus:", goals, '']

    journal = format_journal_context(context['journal'], context['now'])
    if journal:
        lines += [journal, '']

    if context['daily_tasks']:
        lines.append('Daily tasks still pending today:')
        lines += [f'- {t["title"]}' for t in context['daily_tasks']]
        lines.append('')

    if context['todos']:
        lines.append('Open to-dos:')
        lines += [f'- {_format_todo(t)}' for t in context['todos']]
        lines.append('')

    if context['calendar']:
        lines.append('Calendar (today and the next few days):')
        for e in context['calendar']:
            when = e['date']
            if e.get('time'):
                # A standing block is defined by its span — "09:00 Work" loses
                # the part that says the rest of the day is spoken for.
                when += f" {e['time']}"
                if e.get('end_time'):
                    when += f"–{e['end_time']}"
            desc = f" — {e['description']}" if e.get('description') else ''
            lines.append(f"- {when}: {e['title']}{desc}")
        lines.append('')

    if context['learning_due']:
        lines.append(f"Spaced-repetition cards due for review: {context['learning_due']}.")
        lines.append('')

    # `.get`, not `[...]`: a context dict built before this key existed (an old
    # test fixture, a caller assembling one by hand) should render a briefing
    # without the block rather than raise on the way to the model.
    articles = context.get('life_wiki') or []
    if articles:
        lines.append('What you already know about them, from your own notes:')
        for article in articles:
            summary = (article.get('summary') or '').strip()
            lines.append(f'- {article["title"]}' + (f': {summary}' if summary else ''))
        lines.append('')

    if not any((context['journal'], context['daily_tasks'], context['todos'],
                context['calendar'], context['learning_due'], articles)):
        lines.append('(No recent journal, tasks, to-dos, calendar events, or reviews.)')

    lines.append(
        "Write the check-in and lay out today's plan as instructed."
    )
    return '\n'.join(lines)


SYSTEM_PROMPT = (
    "You are Lunaschal acting as the user's personal secretary. Overnight you've "
    "read through their journal, tasks, to-dos, calendar, and study reviews, and "
    "now you leave a short briefing waiting for them in the morning chat.\n\n"
    "Your answer has two parts and they must not duplicate each other.\n\n"
    "\"briefing\" is the check-in only: a warm, concise few sentences that reflect "
    "what they've been living and thinking about and how you're framing their day. "
    "Be encouraging, not naggy. Do NOT list the day's tasks here.\n\n"
    "\"todos\" IS the plan for the day. It is rendered as a checklist directly "
    "under your check-in and it is the only place the day's work appears, so it "
    "must carry the plan — a check-in with an empty list tells the user nothing "
    f"about their day. Give them at most {MAX_BRIEFING_TODOS} genuinely actionable "
    "items, prioritised ruthlessly rather than dumping everything back at them. "
    "Most of the plan will be work already sitting on their open to-dos or pending "
    "daily tasks — include those rather than skipping them as redundant. Return an "
    "empty array only when they have nothing pending at all; whenever the lists "
    "above have anything on them, the plan must not be empty.\n\n"
    "Whenever an item restates something already on those lists, copy that "
    "existing item's title into \"linkedTitle\" **verbatim** — that is what ties "
    "your item to theirs, so crossing it off here also crosses it off their list. "
    "Copy the title ONLY: the lists above annotate each entry with things like "
    "\"(due 2026-07-30)\", \"[priority 4/5]\" and \"[shopping]\", and those are "
    "not part of the title. For \"Buy milk (due 2026-07-30) [priority 4/5]\" the "
    "linkedTitle is \"Buy milk\". Use null when the item is genuinely new. Match "
    "on meaning, not wording: \"Get groceries\" and \"Buy groceries\" are the "
    "same task.\n\n"
    "Respond with a JSON object of exactly this shape:\n"
    '{"briefing": "<markdown check-in>", '
    '"todos": [{"title": "string", "priority": 1-5, "list": "todo", '
    '"due": <unix seconds or null>, '
    '"linkedTitle": <existing item title or null>}]}\n'
    "priority is 1 (low) to 5 (high); use 3 when unsure. list is usually \"todo\". "
    "Omit due (or use null) unless a date is clearly implied."
)

# Grammar-enforced shape of the briefing completion. llama-server compiles this
# into GBNF, so "prose instead of JSON" and "wrong key names" stop being possible
# failure modes — FALLBACK_BRIEFING below now only covers a genuinely empty or
# timed-out generation. maxItems enforces the plan cap in the grammar itself
# rather than trusting the prompt, though the caller still slices defensively.
BRIEFING_SCHEMA = {
    'type': 'object',
    'properties': {
        'briefing': {'type': 'string'},
        'todos': {
            'type': 'array',
            'maxItems': MAX_BRIEFING_TODOS,
            'items': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'priority': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                    'list': {'type': 'string'},
                    'due': {'type': ['integer', 'null']},
                    'linkedTitle': {'type': ['string', 'null']},
                },
                'required': ['title', 'priority', 'list', 'due', 'linkedTitle'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['briefing', 'todos'],
    'additionalProperties': False,
}

# Stands in for the check-in when the model's completion is unusable (empty,
# truncated mid-JSON, prose instead of JSON). The plan below it is still real.
FALLBACK_BRIEFING = (
    "**Good morning.** I couldn't write you a briefing this time — the model "
    "didn't come back with one. Here's what's on your plate today, straight from "
    "your own lists."
)


def fallback_plan(context: dict) -> list[dict]:
    """Today's plan built straight from the user's lists, in the same shape the
    model is asked for (so it goes through the usual validation and linking).

    Used when the model returns no plan at all. An empty plan is the one failure
    the user always notices — the check-in reads fine and they still have to go
    ask what today holds — so it's better to show them their own lists than
    nothing. Pending daily tasks come first — they're the standing commitments
    the user asks about by name — then their to-dos. Titles are copied verbatim
    so each item links back to the row it came from and crossing it off
    completes the real one; the caller's cap decides how many survive.
    """
    items = [
        {'title': t['title'], 'priority': 4, 'list': 'todo', 'due': None,
         'linkedTitle': t['title']}
        for t in context['daily_tasks']
    ]

    # Priority before due date — the opposite of the Tasks view, which sorts the
    # whole list rather than picking a handful out of it. Only a few items fit in
    # a day's plan, and a backlog of long-overdue low-priority to-dos would
    # otherwise crowd out the work the user themselves flagged as important. Due
    # date breaks ties (soonest first, undated last); full ties keep
    # `open_todos`' creation order, since sorted() is stable.
    todos = sorted(
        context['todos'],
        key=lambda t: (-(t.get('priority') or 3), t.get('due') or float('inf')),
    )
    items += [
        {'title': t['title'], 'priority': t.get('priority') or 3,
         'list': t.get('list') or 'todo', 'due': t.get('due'),
         'linkedTitle': t['title']}
        for t in todos
    ]
    return items


def _briefing_model() -> str | None:
    """Optional per-secretary model override; None falls back to the chat model."""
    from backend.ai.provider import get_settings
    s = get_settings()
    return (s.get('briefing_model') if s else None) or None


def _briefing_generation_opts() -> dict:
    """User-tunable generation knobs for the briefing, falling back to the module
    defaults when unset.

    Thinking and output ceiling only — the context window is not a per-request
    setting at all, since llama-server allocates the KV cache when it loads the
    model. Thinking is genuinely worth enabling here even though the chat path
    leaves it off: the briefing runs overnight, so latency is free.
    """
    from backend.ai.provider import get_settings
    s = get_settings() or {}
    return {
        'thinking': bool(s.get('briefing_thinking')),
        'max_tokens': s.get('briefing_max_tokens') or BRIEFING_MAX_TOKENS,
    }


def generate_briefing(context: dict) -> dict:
    """Call the model; returns {"briefing": str, "todos": [...]}. The only part
    of this module that touches the LLM."""
    result = chat_json(
        build_briefing_prompt(context), system=SYSTEM_PROMPT, model=_briefing_model(),
        schema=BRIEFING_SCHEMA, **_briefing_generation_opts(),
    )
    if not isinstance(result, dict):
        return {'briefing': '', 'todos': []}
    result.setdefault('briefing', '')
    todos = result.get('todos')
    result['todos'] = todos if isinstance(todos, list) else []
    return result
