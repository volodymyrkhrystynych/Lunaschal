"""Overnight briefing: gather the user's recent life-data and turn it into a
warm morning check-in plus a concrete to-do plan.

Gathering and prompt-building are kept pure and LLM-free so they can be
unit-tested; only `generate_briefing` hits the model (via `chat_json`).
"""
import time
from datetime import datetime, date, timedelta

from backend.ai.chat import get_recent_journal_entries, format_journal_context
from backend.ai.llm import chat_json

# How far ahead the calendar lookahead reaches, in days (today + this many).
CALENDAR_LOOKAHEAD_DAYS = 3
# Hard cap on how many todos a single briefing may create.
MAX_BRIEFING_TODOS = 5
# The briefing runs overnight (or on a manual trigger), so latency is a
# non-issue — give the model plenty of room to finish the JSON rather than risk
# truncating a longer check-in. This is a ceiling, not a target.
BRIEFING_MAX_TOKENS = 16384


def _pending_daily_tasks(db, today: str) -> list[dict]:
    """Daily tasks not yet completed today (mirrors tasks.list_tasks' join)."""
    rows = db.execute(
        '''SELECT t.title
           FROM daily_tasks t
           LEFT JOIN daily_task_completions c ON c.task_id = t.id AND c.date = ?
           WHERE c.id IS NULL
           ORDER BY t.position''',
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


def _open_todos(db) -> list[dict]:
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


def _learning_due_count(db, now: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM learning_cards WHERE state='active' AND due <= ?",
        (now,),
    ).fetchone()
    return int(row[0]) if row else 0


def gather_briefing_context(now: int | None = None) -> dict:
    """Assemble everything the briefing draws on. Pure w.r.t. the model — only
    reads the DB."""
    from backend.db.connection import get_db
    from backend.ai.provider import get_settings
    now = now if now is not None else int(time.time())
    db = get_db()
    today = date.fromtimestamp(now).isoformat()
    horizon = (date.fromtimestamp(now) + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)).isoformat()
    s = get_settings()
    return {
        'now': now,
        'today': today,
        'goals': (s.get('briefing_goals') if s else None) or '',
        'journal': get_recent_journal_entries(now),
        'daily_tasks': _pending_daily_tasks(db, today),
        'todos': _open_todos(db),
        'calendar': _upcoming_calendar(db, today, horizon),
        'learning_due': _learning_due_count(db, now),
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

    if not any((context['journal'], context['daily_tasks'], context['todos'],
                context['calendar'], context['learning_due'])):
        lines.append('(No recent journal, tasks, to-dos, calendar events, or reviews.)')

    lines.append(
        'Write the morning briefing and propose the to-dos as instructed.'
    )
    return '\n'.join(lines)


SYSTEM_PROMPT = (
    "You are Lunaschal acting as the user's personal secretary. Overnight you've "
    "read through their journal, tasks, to-dos, calendar, and study reviews, and "
    "now you leave a short briefing waiting for them in the morning chat.\n\n"
    "Write a warm, concise check-in (a few sentences) that reflects what they've "
    "been living and thinking about, then lay out a focused plan for the day as a "
    "short markdown list. Be encouraging, not naggy; prioritise ruthlessly rather "
    "than dumping everything back at them.\n\n"
    "Also propose the day's actionable to-dos. Only propose genuinely actionable "
    f"items, at most {MAX_BRIEFING_TODOS}, and never duplicate a to-do that already "
    "exists in the open to-dos above.\n\n"
    "Respond with a JSON object of exactly this shape:\n"
    '{"briefing": "<markdown check-in and plan>", '
    '"todos": [{"title": "string", "priority": 1-5, "list": "todo", '
    '"due": <unix seconds or null>}]}\n'
    "priority is 1 (low) to 5 (high); use 3 when unsure. list is usually \"todo\". "
    "Omit due (or use null) unless a date is clearly implied. If there is nothing "
    "worth doing, return an empty todos array."
)


def _briefing_model() -> str | None:
    """Optional per-secretary model override; None falls back to the chat model."""
    from backend.ai.provider import get_settings
    s = get_settings()
    return (s.get('briefing_model') if s else None) or None


def _briefing_generation_opts() -> dict:
    """User-tunable generation knobs for the briefing (reasoning level, output
    ceiling, context window), falling back to the module defaults when unset."""
    from backend.ai.provider import get_settings
    from backend.ai.llm import LLM_NUM_CTX
    s = get_settings() or {}
    return {
        'reasoning_effort': s.get('briefing_reasoning_effort') or 'none',
        'max_tokens': s.get('briefing_max_tokens') or BRIEFING_MAX_TOKENS,
        'num_ctx': s.get('briefing_num_ctx') or (LLM_NUM_CTX * 2),
    }


def generate_briefing(context: dict) -> dict:
    """Call the model; returns {"briefing": str, "todos": [...]}. The only part
    of this module that touches the LLM."""
    result = chat_json(
        build_briefing_prompt(context), system=SYSTEM_PROMPT, model=_briefing_model(),
        **_briefing_generation_opts(),
    )
    if not isinstance(result, dict):
        return {'briefing': '', 'todos': []}
    result.setdefault('briefing', '')
    todos = result.get('todos')
    result['todos'] = todos if isinstance(todos, list) else []
    return result
