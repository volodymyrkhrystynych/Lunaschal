"""LLM classification of a calendar event's transcribed description into 1-3
fixed categories (leisure/work/exercise/family/outside/indoors), driving the
border color(s) the mobile day view and the Journal event-group wrapper both
render. Same idiom as backend/ai/email.py's classify_email: a closed-vocab
tuple doubles as the JSON-schema enum, so an off-vocabulary category can't be
emitted at all.

Runs on backend.ai.background's single-worker executor right after a
transcription is saved (backend/routes/calendar.py's transcribe_event), so a
slow LLM call never blocks the recording itself. classified_at IS NULL is the
"still pending" state, for both never-classified and previously-failed
events — a crash mid-classification needs no separate in-progress flag.
"""
import json
import time

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.calendar_voice import (
    VOICE_EDIT_SCHEMA,
    VOICE_EDIT_SYSTEM,
    normalize_voice_edit,
)
from backend.db.connection import build_update, get_db

EVENT_CATEGORIES = ('leisure', 'work', 'exercise', 'family', 'outside', 'indoors')

_CATEGORY_SYSTEM = (
    "You classify a personal calendar event into 1-3 categories, based on a "
    "spoken description of what happened during it.\n"
    "Return ONLY valid JSON with one field:\n"
    '- "categories": an array of 1-3 values chosen ONLY from this exact list:\n'
    f"  {', '.join(EVENT_CATEGORIES)}\n"
    "'outside' means it happened outdoors; 'indoors' means it happened inside "
    "(the two are not mutually exclusive with the others — a family walk is "
    "both 'family' and 'outside').\n"
    'Example: {"categories": ["exercise", "outside"]}'
)
_CATEGORY_SCHEMA = {
    'type': 'object',
    'properties': {
        'categories': {
            'type': 'array',
            'items': {'type': 'string', 'enum': list(EVENT_CATEGORIES)},
            'maxItems': 3,
        }
    },
    'required': ['categories'],
}


def _prompt_text(row) -> str:
    title = row['title'] or ''
    description = row['description'] or ''
    return f'Title: {title}\n\n{description}'


def classify_event_categories(event_id: str) -> None:
    """Load the event, classify its (already-saved) description into 1-3
    categories, write the result back — or classification_error if something
    failed. Meant for run_bg(); never raises."""
    db = get_db()
    try:
        row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
        if not row or not is_ai_configured():
            return

        text = _prompt_text(row)
        data = chat_json(text, system=_CATEGORY_SYSTEM, schema=_CATEGORY_SCHEMA)
        raw = data.get('categories')
        categories: list[str] = []
        if isinstance(raw, list):
            for c in raw:
                if c in EVENT_CATEGORIES and c not in categories:
                    categories.append(c)
        categories = categories[:3]

        build_update(
            db, 'calendar_events',
            {
                'category_tags': json.dumps(categories) if categories else None,
                'classified_at': int(time.time()),
                'classification_error': None,
            },
            'id=?', (event_id,),
        )
        db.commit()
    except Exception as e:
        build_update(db, 'calendar_events', {'classification_error': str(e)}, 'id=?', (event_id,))
        db.commit()


def _voice_edit_prompt(current: dict, transcript: str) -> str:
    """What the model sees: the entry as it stands, then what was said.

    The current state is spelled out rather than left implicit because most of
    what this call decides is *relative* — "half an hour later", "add work to
    that" — and none of it is answerable without the values being moved from.
    """
    lines = [
        f"Title: {current.get('title') or '(untitled)'}",
        f"Start: {current.get('time') or '(none)'}",
        f"End: {current.get('endTime') or '(none)'}",
        f"Tags: {', '.join(current.get('tags') or []) or '(none)'}",
        f"Description: {current.get('description') or '(none)'}",
    ]
    return '\n'.join(lines) + f'\n\nWhat the user just said:\n{transcript}'


def parse_event_voice_edit(current: dict, transcript: str) -> dict:
    """One spoken sentence -> the fields of a calendar entry it asks to change.

    Returns {} for everything that isn't a usable edit — AI unconfigured, the
    model unreachable, a generation that validated down to nothing — because
    the caller's floor is the behaviour this button has always had: store the
    transcript as the description. Never raises, for the same reason
    ai/workouts.py's parse never does: the recording is the thing that must not
    be lost, and it is already in hand by the time this runs.
    """
    if not transcript.strip() or not is_ai_configured():
        return {}
    try:
        data = chat_json(
            _voice_edit_prompt(current, transcript),
            system=VOICE_EDIT_SYSTEM,
            schema=VOICE_EDIT_SCHEMA,
        )
    except Exception:
        return {}
    return normalize_voice_edit(data, current)
