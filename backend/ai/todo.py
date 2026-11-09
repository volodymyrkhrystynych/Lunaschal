"""LLM extraction of to-do fields from a spoken transcript.

Called by ``POST /api/tasks/todos/transcribe`` (backend/routes/tasks.py) after
the client has already transcribed audio via ``/api/transcribe``.  The model
parses the transcript into ``title`` (required), ``notes``, ``priority`` (1-5),
and ``due`` (``YYYY-MM-DD``).  A failed or empty response falls back to using
the raw transcript as the title with default priority.
"""

from backend.ai.llm import chat_json

_TODO_SYSTEM = (
    "You extract a to-do item from a spoken sentence.  Return only valid JSON "
    "with these fields:\n"
    '- "title": the to-do title (max 100 chars, required).\n'
    '- "notes": additional details or context, or null if nothing extra.\n'
    '- "priority": an integer 1 (highest) to 5 (lowest), or null if not '
    "mentioned.\n"
    '- "due": a due date as "YYYY-MM-DD", or null if no date is mentioned.\n'
    "If the speaker says 'tomorrow' or 'next Monday', convert it to the actual "
    "date.  If no priority is mentioned, return null (not 3).  Keep the title "
    "concise — it will be the one-line label on the list."
)

_TODO_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {
            'type': 'string',
            'description': 'Concise to-do title (max 100 chars)',
        },
        'notes': {
            'type': ['string', 'null'],
            'description': 'Additional details, or null',
        },
        'priority': {
            'type': ['integer', 'null'],
            'description': 'Priority 1 (highest) to 5 (lowest), or null',
        },
        'due': {
            'type': ['string', 'null'],
            'description': 'Due date as YYYY-MM-DD, or null if not mentioned',
        },
    },
    'required': ['title'],
}


def extract_todo_fields(transcript: str) -> dict | None:
    """Run *transcript* through the LLM and return extracted fields, or ``None``
    if the call fails or returns an empty title.
    """
    try:
        data = chat_json(transcript, system=_TODO_SYSTEM, schema=_TODO_SCHEMA)
    except Exception:  # noqa: BLE001 — LLM down â caller falls back
        return None

    title = data.get('title')
    if not isinstance(title, str) or not title.strip():
        return None

    return {
        'title': title.strip()[:100].strip(),
        'notes': data.get('notes') if isinstance(data.get('notes'), str) else None,
        'priority': data.get('priority') if isinstance(data.get('priority'), int) else None,
        'due': data.get('due') if isinstance(data.get('due'), str) else None,
    }
