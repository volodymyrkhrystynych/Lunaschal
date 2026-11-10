"""LLM extraction of to-do fields from a spoken transcript.

Called by ``POST /api/tasks/todos/transcribe`` (backend/routes/tasks.py) after
the client has already transcribed audio via ``/api/transcribe``.  The model
parses the transcript into ``title`` (required), ``notes``, ``priority`` (1-5),
and ``due`` (``YYYY-MM-DD``).  A failed or empty response returns ``None`` and
the route falls back to the transcript itself.
"""

from datetime import date

from backend.ai.llm import chat_json

TITLE_LIMIT = 100

_TODO_SYSTEM = (
    "You extract a to-do item from a spoken sentence.  Return only valid JSON "
    "with these fields:\n"
    f'- "title": the to-do title (max {TITLE_LIMIT} chars, required).\n'
    '- "notes": additional details or context, or null if nothing extra.\n'
    '- "priority": an integer 1 (highest) to 5 (lowest), or null if not '
    "mentioned.\n"
    '- "due": a due date as "YYYY-MM-DD", or null if no date is mentioned.\n'
    "The message starts with today's date.  Resolve 'tomorrow', 'Friday' or "
    "'next Monday' against it.  If no priority is mentioned, return null (not "
    "3).  Keep the title concise; it will be the one-line label on the list."
)

_TODO_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {
            'type': 'string',
            'description': f'Concise to-do title (max {TITLE_LIMIT} chars)',
        },
        'notes': {
            'type': ['string', 'null'],
            'description': 'Additional details, or null',
        },
        'priority': {
            # Bounded in the grammar too: an out-of-range number would make
            # parse_priority refuse the whole to-do.
            'type': ['integer', 'null'],
            'minimum': 1,
            'maximum': 5,
            'description': 'Priority 1 (highest) to 5 (lowest), or null',
        },
        'due': {
            'type': ['string', 'null'],
            'description': 'Due date as YYYY-MM-DD, or null if not mentioned',
        },
    },
    'required': ['title'],
}


def _prompt(transcript: str, today: str) -> str:
    """Today's date, with its weekday, ahead of the words: without it the model
    has nothing to resolve 'tomorrow' against and invents a date."""
    try:
        weekday = date.fromisoformat(today).strftime('%A')
        stamp = f'{weekday}, {today}'
    except ValueError:
        stamp = today
    return f'Today is {stamp}.\n\nSpoken: {transcript}'


def _priority(value) -> int | None:
    """An integer clamped into 1-5, or None. A grammar that ignored the bounds
    must still not cost the user the to-do."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return min(5, max(1, value))


def extract_todo_fields(transcript: str, today: str) -> dict | None:
    """Run *transcript* through the LLM and return extracted fields, or ``None``
    if the call fails or returns an empty title. *today* is the 4am day key
    (``YYYY-MM-DD``) that relative dates are resolved against.
    """
    try:
        data = chat_json(_prompt(transcript, today), system=_TODO_SYSTEM, schema=_TODO_SCHEMA)
    except Exception:  # noqa: BLE001 — LLM down; the caller falls back
        return None
    if not isinstance(data, dict):
        return None

    title = data.get('title')
    if not isinstance(title, str) or not title.strip():
        return None

    notes = data.get('notes')
    due = data.get('due')
    return {
        'title': title.strip()[:TITLE_LIMIT].strip(),
        'notes': notes.strip() or None if isinstance(notes, str) else None,
        'priority': _priority(data.get('priority')),
        'due': due if isinstance(due, str) else None,
    }


MAX_STEPS = 4

_BREAKDOWN_SYSTEM = (
    "The user keeps putting off one task. Split it into 2 to "
    f"{MAX_STEPS} small, concrete first steps, each doable in under half an "
    "hour, in the order they'd be done. The first step should be the easiest "
    "possible way to start. Phrase each as an action, short enough for a "
    'checklist. Return JSON: {"steps": ["..."]}.'
)

_BREAKDOWN_SCHEMA = {
    'type': 'object',
    'properties': {
        'steps': {
            'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS,
            'items': {'type': 'string'},
        },
    },
    'required': ['steps'],
    'additionalProperties': False,
}


def break_down_todo(title: str, notes: str | None = None) -> list[str]:
    """Smaller first steps for something the day plan kept offering and the
    user kept skipping (the `todo_review` card's "break it down"). Raises on a
    model failure — the user pressed the button and is waiting for an answer —
    and returns [] when the model had nothing to offer."""
    prompt = f'Task: {title}' + (f'\nDetails: {notes}' if notes else '')
    data = chat_json(prompt, system=_BREAKDOWN_SYSTEM, schema=_BREAKDOWN_SCHEMA)
    steps = data.get('steps') if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []
    out = []
    for step in steps[:MAX_STEPS]:
        if isinstance(step, str) and step.strip():
            out.append(step.strip()[:TITLE_LIMIT])
    return out
