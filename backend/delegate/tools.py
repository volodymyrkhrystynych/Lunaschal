"""The chat delegate's toolbox.

Two kinds of tool live here, and the split is the whole design:

**Read-only tools run.** `web_search` / `web_fetch` are executed for real,
straight through backend/research/web.py — the same SSRF-guarded implementation
the Ideas agent uses, not a second copy of it. The delegate retries a failed
fetch on its own budget, which is the thing a one-shot proposal could never do.

**Writing tools only ever propose.** `propose_task`, `propose_calendar_event`,
`propose_calorie_log`, `propose_note_to_self` and `propose_flashcards` write
nothing. They hand back a staged payload that the chat UI renders as the same
confirm card it always has, and the row is inserted by the existing
`/api/chat/save-*` routes when the user clicks. So the delegate replaces the
*classifier* — which guessed an intent after the reply and swallowed its own
failures — without also quietly taking the confirm click away.

A proposal is carried on the tool's own step event under `proposal`, so the loop
in backend/research/agent.py needs to know nothing about it: it collects events
already, and the caller filters. Every `run_tool` here returns
`(text for the model, event for the UI)` and never raises, matching web.py's
contract — a tool that raised would abandon a turn that is otherwise fine.
"""
from datetime import date

from backend.todo_recurrence import VALID_LISTS

# Bounds mirrored from the routes that will eventually do the insert
# (backend/routes/chat.py's save_calories), so a bad number is rejected here —
# where the model can read the error and correct itself — rather than at the
# click, where the user just sees a card that fails.
MAX_CALORIES = 20000
MAX_TITLE_CHARS = 200

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'propose_task',
            'description': (
                'Stage a to-do for the user to confirm. Use when they ask to add '
                'a task, or to be reminded to do something later.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {
                        'type': 'string',
                        'description': 'What needs doing, phrased as an action.',
                    },
                    'list': {
                        'type': 'string',
                        'enum': sorted(VALID_LISTS),
                        'description': 'Which list it belongs on. Defaults to todo.',
                    },
                },
                'required': ['title'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'propose_calendar_event',
            'description': (
                'Stage a calendar event for the user to confirm. Use for things '
                'that happened or are going to happen at a particular time.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'date': {'type': 'string', 'description': 'YYYY-MM-DD.'},
                    'time': {'type': 'string', 'description': 'HH:MM, 24-hour. Omit if untimed.'},
                    'description': {'type': 'string'},
                    'tags': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['title', 'date'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'propose_calorie_log',
            'description': (
                'Stage a calorie entry for the user to confirm. Only when they '
                'gave or clearly implied a number — never guess a calorie count.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'description': {'type': 'string', 'description': 'What was eaten or drunk.'},
                    'calories': {'type': 'integer', 'minimum': 0, 'maximum': MAX_CALORIES},
                },
                'required': ['description', 'calories'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'propose_note_to_self',
            'description': (
                'Stage a lesson worth remembering, to be drafted into a Learning '
                'card the user can approve. Use when they say "note to self" or a '
                'clear equivalent AND have said what the lesson actually is.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'content': {
                        'type': 'string',
                        'description': 'The lesson itself, not the phrase introducing it.',
                    },
                },
                'required': ['content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'propose_flashcards',
            'description': (
                'Stage a flashcard-generation request for the user to confirm. '
                'Use when they ask to be quizzed or to have cards made.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'topic': {'type': 'string', 'description': 'What the cards should cover.'},
                },
                'required': ['topic'],
            },
        },
    },
]


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _staged(kind: str, tool: str, data: dict, summary: str) -> tuple[str, dict]:
    """A successful proposal: what the model is told, and what the UI shows.

    The model is told plainly that nothing is saved yet, because it writes the
    reply the user reads — "I've added that" when a card is still sitting there
    unconfirmed is worse than not offering at all.
    """
    return (
        f'Staged for the user to confirm: {summary}. Nothing has been saved yet — '
        'a confirmation card is now showing in the chat. Mention it briefly and '
        'do not claim it is done.',
        {'tool': tool, 'ok': True, 'arg': summary,
         'proposal': {'kind': kind, 'data': data}},
    )


def _refused(tool: str, reason: str) -> tuple[str, dict]:
    return (
        f'Could not stage that: {reason}.',
        {'tool': tool, 'ok': False, 'error': reason},
    )


def _propose_task(args: dict) -> tuple[str, dict]:
    title = _text(args.get('title'))[:MAX_TITLE_CHARS]
    if not title:
        return _refused('propose_task', 'a task needs a title')
    todo_list = _text(args.get('list')) or 'todo'
    if todo_list not in VALID_LISTS:
        todo_list = 'todo'
    return _staged('task', 'propose_task', {'title': title, 'list': todo_list},
                   f'to-do "{title}"')


def _propose_calendar_event(args: dict) -> tuple[str, dict]:
    title = _text(args.get('title'))[:MAX_TITLE_CHARS]
    if not title:
        return _refused('propose_calendar_event', 'an event needs a title')
    # An event with no date can't be saved, and the model omitting one is far
    # more common than it inventing a wrong one — so today is the honest
    # default, and the card shows the date for the user to correct.
    when = _text(args.get('date')) or date.today().isoformat()
    tags = args.get('tags')
    tags = [_text(t) for t in tags if _text(t)] if isinstance(tags, list) else []
    data = {
        'title': title,
        'date': when,
        'time': _text(args.get('time')) or None,
        'description': _text(args.get('description')),
        'tags': tags,
    }
    return _staged('calendar', 'propose_calendar_event', data,
                   f'event "{title}" on {when}')


def _propose_calorie_log(args: dict) -> tuple[str, dict]:
    description = _text(args.get('description'))[:MAX_TITLE_CHARS]
    if not description:
        return _refused('propose_calorie_log', 'a calorie entry needs a description')
    calories = args.get('calories')
    # `bool` is an `int` in Python, and a model that answers `true` here would
    # otherwise stage a 1-calorie meal.
    if isinstance(calories, bool) or not isinstance(calories, int):
        return _refused('propose_calorie_log',
                        'calories must be a whole number the user actually gave')
    if not 0 <= calories <= MAX_CALORIES:
        return _refused('propose_calorie_log',
                        f'calories must be between 0 and {MAX_CALORIES}')
    return _staged('calorie', 'propose_calorie_log',
                   {'description': description, 'calories': calories},
                   f'{calories} cal for "{description}"')


def _propose_note_to_self(args: dict) -> tuple[str, dict]:
    content = _text(args.get('content'))
    if not content:
        return _refused('propose_note_to_self',
                        'there is no lesson here yet — ask the user what it is')
    return _staged('note', 'propose_note_to_self', {'content': content},
                   'a note to self')


def _propose_flashcards(args: dict) -> tuple[str, dict]:
    topic = _text(args.get('topic'))[:MAX_TITLE_CHARS]
    if not topic:
        return _refused('propose_flashcards', 'flashcards need a topic')
    return _staged('flashcards', 'propose_flashcards', {'topic': topic},
                   f'flashcards on "{topic}"')


_HANDLERS = {
    'propose_task': _propose_task,
    'propose_calendar_event': _propose_calendar_event,
    'propose_calorie_log': _propose_calorie_log,
    'propose_note_to_self': _propose_note_to_self,
    'propose_flashcards': _propose_flashcards,
}


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    """Execute one proposal tool. Returns (text for the model, event for the UI)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f'Unknown tool: {name}', {'tool': name, 'ok': False, 'error': 'unknown tool'}
    return handler(args if isinstance(args, dict) else {})
