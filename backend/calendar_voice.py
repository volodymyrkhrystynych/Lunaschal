"""Turning one spoken sentence at a calendar event into a bounded edit.

The day view's per-event mic button used to do exactly one thing: whatever was
said became the event's `description`, and the category classifier ran over it.
That is right for "walked the dog around the park" and useless for "actually
this was the dentist, move it to three". This module is the schema and the
pure validation that let one button do both — the model decides which fields
the sentence is asking to change, and everything below decides whether what it
came back with is allowed to touch the row.

**`date` is not a field here, deliberately.** The button lives on an event
already drawn on a specific day of a 4am-to-4am timeline, and a spoken "move
this to tomorrow" would make the event disappear from the screen it was spoken
at, with nothing on that screen to undo it with. Rescheduling across days is
what dragging past the midnight rule and the edit form are for. It is left out
of the JSON schema entirely rather than dropped afterwards, so llama-server's
compiled grammar cannot emit it in the first place — the same closed-vocabulary
trick backend/ai/calendar.py's category enum uses.

Nothing here talks to the database or the model; backend/ai/calendar.py's
parse_event_voice_edit makes the call and hands the raw JSON to
`normalize_voice_edit`, and backend/routes/calendar.py writes the result.
"""

from backend.tags import normalize_tags

#: Longest a spoken title is allowed to be. A title is a line on a timeline;
#: a runaway generation that put a paragraph there would be invisible in the
#: UI (it truncates) and wrong everywhere else.
MAX_TITLE_LEN = 200

MINUTES_PER_DAY = 24 * 60

VOICE_EDIT_SYSTEM = (
    "You are editing ONE entry in a personal calendar from a single spoken "
    "sentence. You are given the entry as it stands and a transcript of what "
    "the user just said about it.\n"
    "Return ONLY valid JSON with these five fields, and use null for every "
    "field the sentence does not ask to change:\n"
    '- "title": the entry\'s new name, or null.\n'
    '- "description": the entry\'s complete new description, or null. When the '
    "user is describing what happened rather than issuing an instruction, put "
    "what they said here. This REPLACES the existing description, so if you "
    "are adding to it, include the existing text too.\n"
    '- "time": the new start time as 24-hour "HH:MM", or null.\n'
    '- "endTime": the new end time as 24-hour "HH:MM", or null. Leave it null '
    "when the user moved the start without saying how long the entry runs — "
    "the existing length is kept automatically.\n"
    '- "tags": the complete new list of the user\'s own short labels, or null. '
    "Include the existing tags you are keeping; an empty list is treated as no "
    "change.\n"
    "You cannot change the entry's date. If the user asks to move it to "
    "another day, leave every field null.\n"
    "A sentence that is plain narration of what happened is a description, not "
    "a rename. Only set \"title\" when the user is actually naming the entry.\n"
    'Example — "that was the dentist, it actually started at quarter past two": '
    '{"title": "Dentist", "description": null, "time": "14:15", "endTime": '
    'null, "tags": null}'
)

#: No `pattern` on the time fields on purpose: llama.cpp compiles this schema
#: to a GBNF grammar and supports only a subset of regex, so the times come
#: back as free strings and `_hhmm` below is what actually enforces the shape.
VOICE_EDIT_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': ['string', 'null']},
        'description': {'type': ['string', 'null']},
        'time': {'type': ['string', 'null']},
        'endTime': {'type': ['string', 'null']},
        'tags': {
            'anyOf': [
                {'type': 'array', 'items': {'type': 'string'}},
                {'type': 'null'},
            ]
        },
    },
    'required': ['title', 'description', 'time', 'endTime', 'tags'],
}


def _hhmm(value) -> str | None:
    """Coerce whatever the model said into a stored 'HH:MM', or None.

    Deliberately looser than routes/calendar.py's `_valid_time`, which answers
    yes/no about a string a form or a drag produced. This one is reading a
    generation, so it accepts the shapes one plausibly emits — '9:05',
    '09:05:00' — and returns the single normalized form the column stores.
    """
    if not isinstance(value, str):
        return None
    parts = value.strip().split(':')
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return f'{hours:02d}:{minutes:02d}'


def _minutes(time: str | None) -> int | None:
    parsed = _hhmm(time)
    if parsed is None:
        return None
    hours, minutes = parsed.split(':')
    return int(hours) * 60 + int(minutes)


def shift_end_time(
    old_time: str | None, old_end: str | None, new_time: str
) -> str | None:
    """The end time an event keeps when only its start was moved.

    Duration is preserved rather than the end being left where it was: "move
    it to three" means the whole event, and an event whose start ran past its
    end is a range the day view cannot draw. Computed modulo the day so an
    event that already crossed midnight still comes out with the length it had.
    """
    start, end, moved = _minutes(old_time), _minutes(old_end), _minutes(new_time)
    if start is None or end is None or moved is None:
        return None
    duration = (end - start) % MINUTES_PER_DAY
    landed = (moved + duration) % MINUTES_PER_DAY
    return f'{landed // 60:02d}:{landed % 60:02d}'


def normalize_voice_edit(raw, current: dict) -> dict:
    """Validate a parsed voice edit against the event as it stands.

    `current` carries the event's stored `title`, `description`, `time`,
    `endTime` and `tags` (already a list). Returns camelCase keys for the
    fields that both survived validation *and* actually differ from what is
    stored — an edit that only restates the current title is not an edit, and
    reporting it as one would tell the user something changed when nothing did.
    """
    if not isinstance(raw, dict):
        return {}
    edit: dict = {}

    title = raw.get('title')
    if isinstance(title, str):
        title = title.strip()[:MAX_TITLE_LEN].strip()
        if title and title != (current.get('title') or ''):
            edit['title'] = title

    description = raw.get('description')
    if isinstance(description, str):
        description = description.strip()
        if description and description != (current.get('description') or ''):
            edit['description'] = description

    time = _hhmm(raw.get('time'))
    if time and time != (current.get('time') or '')[:5]:
        edit['time'] = time

    end_time = _hhmm(raw.get('endTime'))
    if end_time and end_time != (current.get('endTime') or '')[:5]:
        edit['endTime'] = end_time
    elif 'time' in edit:
        # Start moved, end not spoken: carry the length across.
        carried = shift_end_time(current.get('time'), current.get('endTime'), edit['time'])
        if carried:
            edit['endTime'] = carried

    tags = raw.get('tags')
    if isinstance(tags, list) and tags:
        # An empty list is read as "nothing to say about tags", not "clear
        # them". The model reaches for [] when a sentence mentions no labels at
        # all, and silently wiping a hand-typed set on an unrelated remark is
        # not a trade worth making for a command ("untag this") nobody speaks.
        # The edit form clears tags.
        normalized = normalize_tags(tags)
        # Compared as sets: a model handed the current tags back in a different
        # order is agreeing with them, and rewriting the column to say so would
        # be reported to the user as a change.
        if normalized and set(normalized) != set(normalize_tags(current.get('tags') or [])):
            edit['tags'] = normalized

    return edit
