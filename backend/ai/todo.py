"""LLM extraction of to-do fields from a spoken transcript.

Called for every to-do that was said rather than typed: ``POST
/api/tasks/todos/transcribe``, the phone's recorded to-dos, and the Watch's
To-do button (a journal recording that also becomes a to-do). The model parses
the transcript into ``title`` (required), ``notes``, ``priority`` (1-5) and
``due`` (``YYYY-MM-DD``).

**Nothing that was said may be lost, and the title is never the sentence cut
off.** Both used to happen. The prompt asked only for a "concise" title, so a
local model often copied the whole run-on sentence into it, and the code then
cut it at 100 characters mid-word, dropping the rest when the model had left
``notes`` empty. The fallback did the same thing to the raw transcript. Now an
overlong title is split at a sentence, clause or word boundary rather than cut
(`split_title_notes`), and the transcript itself is kept as the notes whenever
the model's fields do not obviously cover it.

There is deliberately no ``maxLength`` in the schema: llama-server compiles it
to a grammar, and a grammar that runs out of characters closes the string
wherever it is, which is a mid-word cut again.
"""

import re
from datetime import date

from backend.ai.llm import chat_json
from backend.ai.service import InferencePaused, Preempted

# Past this a title is no longer a label on a list, whatever the model thought.
TITLE_LIMIT = 80
# What a split aims for: a checklist line that reads at a glance.
TITLE_TARGET = 60

_TODO_SYSTEM = (
    "You turn one spoken to-do into a checklist item. The words come from "
    "speech-to-text, so expect filler (\"um\", \"I need to\", \"remind me to\"), "
    "run-on sentences and no punctuation.\n"
    "Return only valid JSON with these fields:\n"
    '- "title": the action itself, as a short imperative phrase of 2 to 8 '
    f"words (at most {TITLE_TARGET} characters) that starts with a verb, like a "
    "label on a checklist. Leave out filler, reasons, background and dates. "
    "Never copy the whole sentence into the title.\n"
    '- "notes": every other detail that was said: who, where, why, which one, '
    "steps, numbers, names. Use the speaker's own words, tidied into sentences "
    "but not summarised away. null only when the title already says everything.\n"
    '- "priority": an integer 1 (highest) to 5 (lowest), only when urgency or '
    "importance was said; otherwise null (not 3).\n"
    '- "due": a date as "YYYY-MM-DD" when one was said; otherwise null. The '
    "message starts with today's date: resolve 'tomorrow', 'Friday' or 'next "
    "Monday' against it.\n"
    "\n"
    "Examples, where today is Monday, 2026-03-02:\n"
    "Spoken: um remind me to call the dentist tomorrow about moving my cleaning "
    "to next week because I have the trip\n"
    '{"title": "Call the dentist", "notes": "Move my cleaning to next week, '
    'because of the trip.", "priority": null, "due": "2026-03-03"}\n'
    "Spoken: buy milk\n"
    '{"title": "Buy milk", "notes": null, "priority": null, "due": null}\n'
    "Spoken: I need to sort out the VPN port forwarding thing the port changed "
    "again and all the downloads are stuck it's pretty urgent\n"
    '{"title": "Fix the VPN port forwarding", "notes": "The port changed again '
    'and all the downloads are stuck.", "priority": 2, "due": null}'
)

_TODO_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {
            'type': 'string',
            'description': 'Short imperative action, 2 to 8 words',
        },
        'notes': {
            'type': ['string', 'null'],
            'description': 'Every other detail that was said, or null',
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
    'required': ['title', 'notes', 'priority', 'due'],
}

_SENTENCE_END = re.compile(r'(?<=[.!?])\s+|\n+')
_CLAUSE_BREAK = re.compile(r'\s*(?:[,;:]|\s[—–-]\s)\s*')
_TRAILING = ' \t.,;:!?-—–'


def _words(text: str) -> list[str]:
    return re.findall(r'\w+', text.lower())


def split_title_notes(text: str, limit: int = TITLE_TARGET) -> tuple[str, str | None]:
    """A title for *text* and the notes that keep the rest of it, without a model.

    The title is the first sentence, else its first clause, else as many whole
    words as fit, marked with an ellipsis so it reads as cut rather than as the
    whole thought. Whenever the title is not all of *text*, the notes are all
    of it, so nothing that was said is lost to the split.
    """
    whole = ' '.join((text or '').split())
    if not whole:
        return '', None
    first = _SENTENCE_END.split((text or '').strip(), maxsplit=1)[0]
    first = ' '.join(first.split()).rstrip(_TRAILING)
    if len(first) > limit:
        clause = _CLAUSE_BREAK.split(first, maxsplit=1)[0].rstrip(_TRAILING)
        if len(clause) <= limit and len(clause.split()) >= 2:
            first = clause
    if len(first) > limit:
        cut = first[:limit + 1]
        space = cut.rfind(' ')
        first = (cut[:space] if space > 0 else first[:limit]).rstrip(_TRAILING) + '…'
    title = first or whole[:limit]
    if title.rstrip('…').rstrip(_TRAILING) == whole.rstrip(_TRAILING):
        return title, None
    return title, whole


def todo_title_and_notes(title: str, notes: str | None) -> tuple[str, str | None]:
    """A title and notes some other model wrote (the chat's `add_todos`, the
    briefing), held to the same rule: a title too long to be a label is split,
    and the sentence it came from goes ahead of the notes rather than away."""
    title = ' '.join((title or '').split())
    notes = (notes or '').strip() or None
    if len(title) <= TITLE_LIMIT:
        return title, notes
    short, whole = split_title_notes(title)
    if whole and whole not in (notes or ''):
        notes = f'{whole}\n\n{notes}' if notes else whole
    return short, notes


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


def _covers(transcript: str, title: str, notes: str | None) -> bool:
    """Whether the title and notes plausibly hold what was said. Filler and a
    date the model turned into `due` account for a few words, so this is a
    floor against an empty `notes` beside a long transcript, not a diff."""
    said = _words(transcript)
    kept = set(_words(title)) | set(_words(notes or ''))
    missing = [w for w in said if w not in kept]
    return len(missing) <= 6


def extract_todo_fields(transcript: str, today: str) -> dict | None:
    """Run *transcript* through the LLM and return extracted fields, or ``None``
    if the call fails or returns an empty title. *today* is the 4am day key
    (``YYYY-MM-DD``) that relative dates are resolved against.

    ``InferencePaused`` is raised rather than swallowed, so a caller can make
    the to-do from the words now and sort it properly once the GPU is back
    (see `_todo_from_speech`). ``Preempted`` is raised for a background job to
    be retried.
    """
    try:
        data = chat_json(_prompt(transcript, today), system=_TODO_SYSTEM, schema=_TODO_SCHEMA)
    except (InferencePaused, Preempted):
        raise
    except Exception:  # noqa: BLE001 — LLM down or malformed; the caller falls back
        return None
    if not isinstance(data, dict):
        return None

    title = data.get('title')
    if not isinstance(title, str) or not ' '.join(title.split()):
        return None
    title = ' '.join(title.split()).rstrip(' .')
    notes = data.get('notes')
    notes = ' '.join(notes.split()) or None if isinstance(notes, str) else None

    if len(title) > TITLE_LIMIT:
        # The model wrote a sentence. Split it rather than cut it, and keep
        # what the split moved out of the title.
        title, overflow = split_title_notes(title)
        if overflow and overflow not in (notes or ''):
            notes = f'{overflow}\n\n{notes}' if notes else overflow
    if not _covers(transcript, title, notes):
        spoken = ' '.join(transcript.split())
        notes = f'{notes}\n\nAs said: {spoken}' if notes else spoken

    due = data.get('due')
    return {
        'title': title,
        'notes': notes,
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
