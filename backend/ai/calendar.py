"""LLM classification of a calendar event's transcribed description into 1-3
fixed categories (leisure/work/exercise/family/outside/indoors), driving the
border color(s) the mobile day view and the Journal event-group wrapper both
render. Same idiom as backend/ai/email.py's classify_email: a closed-vocab
tuple doubles as the JSON-schema enum, so an off-vocabulary category can't be
emitted at all.

Runs on the llm_jobs worker (backend/ai/jobs.py) when an event is created with
no categories ticked, and after a recording or spoken edit gives it new words
(backend/routes/calendar.py), so a slow LLM call never blocks the save.
classified_at IS NULL is the
"still pending" state, for both never-classified and previously-failed
events — a crash mid-classification needs no separate in-progress flag.
"""
import json
import time

from backend import authorship
from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.ai.service import InferencePaused, Preempted
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
    failed. Meant for the job queue (backend/ai/jobs.py); never raises."""
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


_TITLE_SYSTEM = (
    "You write the title of a personal calendar event from a spoken description "
    "of what happened during it.\n"
    "Return ONLY valid JSON with one field:\n"
    '- "title": 2-6 words naming the activity, in the language of the description, '
    "with no quotes, date or time and no ending punctuation.\n"
    'Example: {"title": "Lunch with Sam"}'
)
_TITLE_SCHEMA = {
    'type': 'object',
    'properties': {'title': {'type': 'string'}},
    'required': ['title'],
}
_TITLE_MAX = 80
_FALLBACK_TITLE_WORDS = 6


def _clean_title(raw) -> str:
    if not isinstance(raw, str):
        return ''
    return ' '.join(raw.split()).strip('"\'“”').rstrip('.!?,;:')[:_TITLE_MAX].strip()


def fill_event_title(event_id: str) -> None:
    """Title an event that was saved blank, from what its clips said.

    Only ever fills a blank, down to the UPDATE itself, so a title typed while
    the job waited wins. With no usable answer from the model the first words of
    the transcript do the job: an event left "Untitled" is one nobody can find.
    A pause or preemption is not that case and is left to requeue the job.
    """
    db = get_db()
    row = db.execute(
        'SELECT title, description FROM calendar_events WHERE id=?', (event_id,)
    ).fetchone()
    body = (row['description'] or '').strip() if row else ''
    if not body or (row['title'] or '').strip():
        return

    title = ''
    if is_ai_configured():
        try:
            title = _clean_title(
                chat_json(body, system=_TITLE_SYSTEM, schema=_TITLE_SCHEMA).get('title'))
        except (InferencePaused, Preempted):
            raise
        except Exception as e:
            print(f'Event title generation failed for {event_id}: {e}')
    title = title or _clean_title(' '.join(body.split()[:_FALLBACK_TITLE_WORDS]))
    cur = db.execute(
        "UPDATE calendar_events SET title=? WHERE id=? AND TRIM(title)=''",
        (title, event_id),
    )
    if cur.rowcount:
        # The model's words (or the transcript's first few, which the app chose).
        authorship.mark_ai(db, 'calendar_events', event_id, 'title', title)
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
