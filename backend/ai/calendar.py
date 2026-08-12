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
