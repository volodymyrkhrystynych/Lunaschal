"""Hear the to-dos in a journal entry.

The user says things in the journal — "need to call the landlord", "should
book the dentist before the 20th" — and used to have to say them again in the
chat for them to reach the day plan. This reads one settled entry and returns
the few things in it the user said they need, want or plan to do. Orchestration
(which entries, where the cards go) is backend/journal/todos.py; this is only
the model call, so it can be tested with the model mocked.
"""
from backend.ai.llm import chat_json

MAX_ITEMS = 3
TITLE_LIMIT = 120

SYSTEM_PROMPT = (
    "You read one entry from the user's journal and pick out the things they "
    "said they need, want or plan to DO — concrete actions they could put on a "
    "to-do list (\"call the landlord\", \"book the dentist\", \"return the "
    "library books\"). Not feelings, not things already done, not things "
    "someone else will do, not vague wishes (\"I should be happier\"), not "
    "habits they already track. Most entries contain none: an empty array is "
    "the normal answer.\n\n"
    "For each item give:\n"
    "- \"title\": the action, short, imperative, in the user's own terms.\n"
    "- \"reason\": the phrase from the entry it came from, quoted briefly.\n"
    "- \"linkedTitle\": if it is the same task as one on their existing lists "
    "(given below), that item's title verbatim; otherwise null.\n"
    f"At most {MAX_ITEMS} items."
)

SCHEMA = {
    'type': 'object',
    'properties': {
        'todos': {
            'type': 'array',
            'maxItems': MAX_ITEMS,
            'items': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'reason': {'type': 'string'},
                    'linkedTitle': {'type': ['string', 'null']},
                },
                'required': ['title', 'reason', 'linkedTitle'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['todos'],
    'additionalProperties': False,
}


def build_prompt(entry_text: str, *, existing: list[str], goals: str = '') -> str:
    lines = ['Journal entry:', entry_text.strip(), '']
    if existing:
        lines.append('Already on their lists or today\'s plan:')
        lines += [f'- {t}' for t in existing]
        lines.append('')
    if goals:
        lines += ['Their goals (for context only — do not invent steps toward them):', goals, '']
    lines.append('What, if anything, did they say they need to do?')
    return '\n'.join(lines)


def extract_journal_todos(entry_text: str, *, existing: list[str], goals: str = '') -> list[dict]:
    """`[{title, reason, linkedTitle}]`, possibly empty. Model failures raise:
    the caller is a background job, and a paused model must leave it pending
    rather than quietly record "nothing to do"."""
    data = chat_json(build_prompt(entry_text, existing=existing, goals=goals),
                     system=SYSTEM_PROMPT, schema=SCHEMA)
    items = data.get('todos') if isinstance(data, dict) else None
    out = []
    for item in (items or [])[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        title = item.get('title')
        if not isinstance(title, str) or not title.strip():
            continue
        reason = item.get('reason') if isinstance(item.get('reason'), str) else ''
        linked = item.get('linkedTitle') if isinstance(item.get('linkedTitle'), str) else None
        out.append({'title': title.strip()[:TITLE_LIMIT], 'reason': reason.strip()[:300],
                    'linkedTitle': linked})
    return out
