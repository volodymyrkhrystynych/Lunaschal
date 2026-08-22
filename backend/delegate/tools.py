"""The main chat model's toolbox: stage something, write something immediately,
or ask.

**The `propose_*` tools only ever propose.** `propose_task`,
`propose_calendar_event`, `propose_calorie_log`, `propose_food_log`,
`draft_flashcard` and `propose_flashcards` write nothing. They hand back a
staged payload the chat UI renders as a confirm card, and the row is inserted by
`resolve_proposal` in backend/routes/chat.py when the user clicks. So this
replaces the *classifier* — which guessed an intent after the reply and swallowed
its own failures — without also quietly taking the confirm click away.

**`create_note_to_self` is the exception, and the exception is narrow.** It
writes a backend/notes.py row with no card, because a note-to-self is meant to
be jotted down without a click, and what makes that safe is not the write being
small but its being reversible — it stays correctable afterward (editing tracks
a revision, backend/routes/notes.py) rather than needing to be gotten right the
first time. It is the only tool here that touches the database at all.

**The standing memory document is no longer written from chat.** `remember` and
`revise_memory` used to sit beside it, editing backend/memory.py mid-reply to
catch a misheard name. They are gone: an unbidden write on every correction put
a step in the trace and a "noted" in the reply for something the user had not
asked to be made permanent. The document itself stays, read into every system
prompt as before, and Settings → Memory is now the only thing that writes it.

**They run in the main chat, not in the delegate.** They used to live in the
delegate's loop, which is handed one `task` string and cannot see the
conversation — so "by Friday" and "it's urgent" were routinely lost in the
paraphrase and the row came out bare. These are cheap schemas returning one
short string, so the main chat can afford them, and it has the whole
conversation plus `format_now_context()` to resolve "Friday" against. The
delegate keeps the tools whose *output* is enormous (backend/delegate/agent.py).

**`ask_user` is the alternative to guessing.** Every silent default here was a
small lie — a calendar event with no date used to be stamped with today's. When
a field was clearly implied but named too loosely to resolve, the model asks
instead. It stages nothing, so it can never produce a card built on a guess.

A proposal is carried on the tool's own step event under `proposal`, so the
caller just filters events; nothing else needs to know. Every `run_tool` here
returns `(text for the model, event for the UI)` and never raises, matching
web.py's contract — a tool that raised would abandon a turn that is otherwise
fine.
"""
import logging

from backend.todo_recurrence import (
    VALID_LISTS, VALID_UNITS, normalize_list, parse_due_date, parse_priority,
    parse_repeat,
)

logger = logging.getLogger(__name__)

# Bounds mirrored from the routes that will eventually do the insert
# (backend/routes/chat.py's save_calories), so a bad number is rejected here —
# where the model can read the error and correct itself — rather than at the
# click, where the user just sees a card that fails.
MAX_CALORIES = 20000
MAX_TITLE_CHARS = 200
MAX_NOTE_CHARS = 4000

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'propose_task',
            'description': (
                'Stage a to-do for the user to confirm. Use when they ask to add '
                'a task, or to be reminded to do something later. Fill in every '
                'field the user actually gave — a to-do staged without the '
                'deadline or the urgency they just told you is one they have to '
                'go and fix by hand.'
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
                    'due': {
                        'type': 'string',
                        'description': (
                            'When it is due, as YYYY-MM-DD. Resolve relative '
                            'wording ("Friday", "in two weeks") against the '
                            'current date yourself. Omit if the user gave no '
                            'deadline; if they implied one too vaguely to '
                            'resolve ("soon", "before the trip"), call ask_user '
                            'instead of guessing.'
                        ),
                    },
                    'priority': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': 5,
                        'description': (
                            'How important: 1 very unimportant, 2 unimportant, '
                            '3 normal, 4 important, 5 very important. Defaults '
                            'to 3. Only move off 3 when the user said something '
                            'about urgency or importance.'
                        ),
                    },
                    'notes': {
                        'type': 'string',
                        'description': 'Extra detail that does not belong in the title.',
                    },
                    'repeatInterval': {
                        'type': 'integer',
                        'minimum': 1,
                        'description': 'For a repeating to-do: every N units. Needs repeatUnit.',
                    },
                    'repeatUnit': {
                        'type': 'string',
                        'enum': list(VALID_UNITS),
                        'description': 'Unit for repeatInterval. Needs repeatInterval.',
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
                'that happened or are going to happen at a particular time. An '
                'event needs a real date: if you cannot work one out from what '
                'the user said, call ask_user rather than staging one.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'date': {
                        'type': 'string',
                        'description': (
                            'YYYY-MM-DD. Resolve relative wording ("next '
                            'Tuesday") against the current date yourself.'
                        ),
                    },
                    'time': {'type': 'string', 'description': 'HH:MM, 24-hour. Omit if untimed.'},
                    'endTime': {'type': 'string', 'description': 'HH:MM, 24-hour. Omit if open-ended.'},
                    'allDay': {
                        'type': 'boolean',
                        'description': (
                            'True only when the user meant the whole day. This is '
                            'not the same as simply not knowing the time — leave '
                            'it false and omit time for that.'
                        ),
                    },
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
            'name': 'draft_flashcard',
            'description': (
                'Draft a Learning flashcard from a lesson the user just stated, '
                'for them to approve. Use when they ask for a specific fact or '
                'lesson to become a flashcard right away — "flashcard this", '
                '"turn that into a card" — AND have said what the lesson '
                'actually is. For a broader request to be quizzed on a topic '
                'rather than one stated fact, use propose_flashcards instead.'
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
    {
        'type': 'function',
        'function': {
            'name': 'propose_food_log',
            'description': (
                'Stage a meal for the user to confirm, for their food log. Use '
                'when they described eating or drinking something — a photo of '
                'it, what it was, where, how it was — not just a bare number. '
                'For "a coke, 140 calories" with nothing else to it, use '
                'propose_calorie_log instead.\n'
                'Their own photo and the exact words they said are attached '
                'automatically; do not repeat them into these fields, and never '
                'invent a calorie count they did not give.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'dish': {
                        'type': 'string',
                        'description': (
                            'What was eaten, named as specifically as the '
                            'conversation and the photo allow.'
                        ),
                    },
                    'place': {
                        'type': 'string',
                        'description': 'Where they ate it, if they said or the photo shows it.',
                    },
                    'notes': {
                        'type': 'string',
                        'description': (
                            'Their commentary on it, tidied but in their own '
                            'first-person voice. Leave empty if they said nothing about it.'
                        ),
                    },
                    'calories': {
                        'type': 'integer',
                        'minimum': 0,
                        'maximum': MAX_CALORIES,
                        'description': 'Only if they gave or clearly implied a number.',
                    },
                    'rating': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': 5,
                        'description': 'Only if they rated it or said plainly how good it was.',
                    },
                    'tags': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'A few short tags, or none.',
                    },
                },
                'required': ['dish'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'propose_recipe',
            'description': (
                'Stage a recipe for the user to confirm, for their recipe '
                'collection. Use when they ask you to write up or save a '
                'recipe — for a dish they described making, or one you are '
                'putting together for them. Write the actual recipe yourself: '
                'a short title and the full content as markdown with an '
                '"## Ingredients" bulleted list and an "## Instructions" '
                'numbered list, using quantities and steps from the '
                'conversation where they were given. Keep it complete but '
                'concise — this is not the place for a life story.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string', 'description': 'A short recipe name.'},
                    'content': {
                        'type': 'string',
                        'description': (
                            'The full recipe as markdown: "## Ingredients" '
                            '(bulleted) then "## Instructions" (numbered).'
                        ),
                    },
                    'tags': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': (
                            '1-5 lowercase tags (cuisine, meal type, main '
                            'ingredient), or none.'
                        ),
                    },
                },
                'required': ['title', 'content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'create_note_to_self',
            'description': (
                'Write a note to yourself immediately — no confirmation card. '
                'Use when they say "note to self" or a clear equivalent AND '
                'have said what the note actually is. This is for a stray '
                'thought, plan, or reminder they want resurfaced for review '
                'over the following days — not a fact to memorize (use '
                'draft_flashcard for that) and not a dated to-do (use '
                'propose_task).'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'content': {
                        'type': 'string',
                        'description': 'The note itself, not the phrase introducing it.',
                    },
                },
                'required': ['content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'ask_user',
            'description': (
                'Ask the user one question instead of staging something built on '
                'a guess. Nothing is saved and no card appears.\n'
                'Use it when they clearly meant a detail but named it too loosely '
                'to act on: a deadline given as "soon" or "before the trip", an '
                'urgency given as "when you get a chance", an event with no date '
                'you can work out, a meal with no calorie count.\n'
                'Do NOT use it when nothing was implied. "Add buy milk" is a '
                'complete request — stage it undated at normal priority and say '
                'nothing. Never ask which list something goes on, never ask for '
                'tags, and never ask the user to confirm what they just plainly '
                'said. One question at most per reply.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'question': {
                        'type': 'string',
                        'description': (
                            'The question, in one sentence, naming the specific '
                            'detail you are missing — "is that this Friday or '
                            'next?", not "can you give me more detail?".'
                        ),
                    },
                    'about': {
                        'type': 'string',
                        'description': 'What you were about to stage, in a few words.',
                    },
                },
                'required': ['question'],
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
    todo_list, err = normalize_list(_text(args.get('list')))
    if err:
        todo_list = 'todo'

    # Validated here, against the same rules /api/tasks/todos enforces, so a bad
    # value comes back as text the model can correct on its next turn rather
    # than as a card that fails at the click. `due` is staged as the YYYY-MM-DD
    # the model wrote and converted to a timestamp only at accept time — that
    # keeps the proposal payload readable, editable in the card, and one
    # representation the whole way through the confirm step.
    due = _text(args.get('due')) or None
    if due is not None:
        _, err = parse_due_date(due)
        if err:
            return _refused('propose_task', err)
    priority, err = parse_priority(args.get('priority'))
    if err:
        return _refused('propose_task', err)
    repeat, err = parse_repeat(args.get('repeatInterval'), args.get('repeatUnit'))
    if err:
        return _refused('propose_task', err)

    data = {
        'title': title,
        'list': todo_list,
        'due': due,
        'priority': priority,
        'notes': _text(args.get('notes')) or None,
        'repeatInterval': repeat[0],
        'repeatUnit': repeat[1],
    }
    detail = f' (due {due})' if due else ''
    return _staged('task', 'propose_task', data, f'to-do "{title}"{detail}')


def _propose_calendar_event(args: dict) -> tuple[str, dict]:
    title = _text(args.get('title'))[:MAX_TITLE_CHARS]
    if not title:
        return _refused('propose_calendar_event', 'an event needs a title')
    # No date used to mean today. That default was a guess wearing a fact's
    # clothes — the card showed a real-looking date the user had never given,
    # and confirming it was one click. Now the model is told to go and ask.
    when = _text(args.get('date'))
    if not when:
        return _refused(
            'propose_calendar_event',
            'an event needs a date — work one out from what the user said, or '
            'use ask_user to find out when they meant',
        )
    _, err = parse_due_date(when)
    if err:
        return _refused('propose_calendar_event', 'date must be a real date as YYYY-MM-DD')

    tags = args.get('tags')
    tags = [_text(t) for t in tags if _text(t)] if isinstance(tags, list) else []
    all_day = args.get('allDay') is True
    data = {
        'title': title,
        'date': when,
        # An all-day event is explicitly the whole day, not merely untimed, so
        # setting the flag clears any clock the model also volunteered.
        'time': None if all_day else (_text(args.get('time')) or None),
        'endTime': None if all_day else (_text(args.get('endTime')) or None),
        'allDay': all_day,
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


def _draft_flashcard(args: dict) -> tuple[str, dict]:
    content = _text(args.get('content'))
    if not content:
        return _refused('draft_flashcard',
                        'there is no lesson here yet — ask the user what it is')
    return _staged('flashcard_draft', 'draft_flashcard', {'content': content},
                   'a flashcard draft')


def _propose_flashcards(args: dict) -> tuple[str, dict]:
    topic = _text(args.get('topic'))[:MAX_TITLE_CHARS]
    if not topic:
        return _refused('propose_flashcards', 'flashcards need a topic')
    return _staged('flashcards', 'propose_flashcards', {'topic': topic},
                   f'flashcards on "{topic}"')


def _propose_food_log(args: dict) -> tuple[str, dict]:
    """Stage a meal. Note what it deliberately does *not* take: the photo and
    the verbatim transcript. Those are resolved from the message itself at accept
    time (`_accept_food` in backend/routes/chat.py) precisely because they must
    not be round-trippable through an editable card — what the user actually said
    is not something a later edit gets to rewrite."""
    dish = _text(args.get('dish'))[:MAX_TITLE_CHARS]
    if not dish:
        return _refused('propose_food_log', 'a food entry needs to say what was eaten')

    calories = args.get('calories')
    if calories is not None:
        if isinstance(calories, bool) or not isinstance(calories, int):
            return _refused('propose_food_log',
                            'calories must be a whole number the user actually gave')
        if not 0 <= calories <= MAX_CALORIES:
            return _refused('propose_food_log',
                            f'calories must be between 0 and {MAX_CALORIES}')

    rating = args.get('rating')
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int)
                               or not 1 <= rating <= 5):
        return _refused('propose_food_log', 'rating must be a whole number from 1 to 5')

    raw_tags = args.get('tags')
    tags = [t.strip() for t in raw_tags if isinstance(t, str) and t.strip()] \
        if isinstance(raw_tags, list) else []

    data = {
        'dish': dish,
        'place': _text(args.get('place')),
        'notes': _text(args.get('notes')),
        'calories': calories,
        'rating': rating,
        'tags': tags,
    }
    summary = f'"{dish}"' + (f' at {calories} cal' if calories is not None else '')
    return _staged('food', 'propose_food_log', data, f'food log for {summary}')


def _propose_recipe(args: dict) -> tuple[str, dict]:
    title = _text(args.get('title'))[:MAX_TITLE_CHARS]
    if not title:
        return _refused('propose_recipe', 'a recipe needs a title')
    content = _text(args.get('content'))
    if not content:
        return _refused('propose_recipe', 'a recipe needs ingredients and instructions')

    raw_tags = args.get('tags')
    tags = [t.strip() for t in raw_tags if isinstance(t, str) and t.strip()] \
        if isinstance(raw_tags, list) else []

    data = {'title': title, 'content': content, 'tags': tags}
    return _staged('recipe', 'propose_recipe', data, f'recipe "{title}"')


def _create_note_to_self(args: dict) -> tuple[str, dict]:
    """Writes a backend/notes.py row immediately, no confirm card: jotting a
    note down shouldn't cost a click, and it stays correctable afterward (the
    note view's edit tracks a revision) rather than needing to be right the
    first time. The only tool here that writes.
    """
    from backend.notes import create_note

    content = _text(args.get('content'))[:MAX_NOTE_CHARS]
    if not content:
        return _refused('create_note_to_self', 'there is nothing to note yet — ask what it is')
    create_note(content)
    return (
        f'Saved as a note to self: {content}\n'
        'It is written already — acknowledge it briefly and do not ask them '
        'to confirm it.',
        {'tool': 'create_note_to_self', 'ok': True, 'arg': content},
    )


def _ask_user(args: dict) -> tuple[str, dict]:
    """The one tool here that stages nothing.

    Its event carries no `proposal` key, so it can never reach the confirm-card
    path — which is the point: the whole reason to ask is that there is no
    honest payload to stage yet. The instruction back to the model is where the
    ask/stage exclusivity is enforced, per item rather than per turn: staging an
    unrelated to-do alongside a question is normal ("add buy milk, and remind me
    about the Dave thing"), so the proposals from this turn are deliberately not
    thrown away.
    """
    question = _text(args.get('question'))
    if not question:
        return _refused('ask_user', 'there is no question here — say what detail you need')
    about = _text(args.get('about'))
    return (
        f'Nothing has been staged. Put this question to the user in your reply, '
        f'in your own words: {question}\n'
        'Do not stage a guess at the thing you are asking about, and do not '
        'apologise for asking. Anything else you staged this turn is unaffected '
        'and still needs mentioning.',
        {'tool': 'ask_user', 'ok': True, 'arg': about or question},
    )


_HANDLERS = {
    'propose_task': _propose_task,
    'propose_calendar_event': _propose_calendar_event,
    'propose_calorie_log': _propose_calorie_log,
    'propose_food_log': _propose_food_log,
    'propose_recipe': _propose_recipe,
    'draft_flashcard': _draft_flashcard,
    'propose_flashcards': _propose_flashcards,
    'create_note_to_self': _create_note_to_self,
    'ask_user': _ask_user,
}


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    """Execute one proposal tool. Returns (text for the model, event for the UI)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f'Unknown tool: {name}', {'tool': name, 'ok': False, 'error': 'unknown tool'}
    return handler(args if isinstance(args, dict) else {})
