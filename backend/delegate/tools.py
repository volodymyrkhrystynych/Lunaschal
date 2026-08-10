"""The main chat model's toolbox: stage something, remember something, or ask.

**The `propose_*` tools only ever propose.** `propose_task`,
`propose_calendar_event`, `propose_calorie_log`, `propose_food_log`,
`propose_note_to_self` and `propose_flashcards` write nothing. They hand back a
staged payload the chat UI renders as a confirm card, and the row is inserted by
`resolve_proposal` in backend/routes/chat.py when the user clicks. So this
replaces the *classifier* — which guessed an intent after the reply and swallowed
its own failures — without also quietly taking the confirm click away.

**`remember` and `revise_memory` are the exception, and the exception is
narrow.** They edit the standing document in backend/memory.py without a card,
because the thing they exist for is the user correcting a misheard name
mid-sentence and a confirmation click there costs more than it protects. What
makes that safe is not the write being small but its being reversible: every
change snapshots the previous document, and Settings shows the whole history.
They are also the only tools here that touch the database at all.

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
    VALID_LISTS, VALID_UNITS, parse_due_date, parse_priority, parse_repeat,
)

logger = logging.getLogger(__name__)

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
            'name': 'remember',
            'description': (
                'Write one line into the standing document you keep about the '
                'user. This saves immediately — there is no confirmation card.\n'
                'Use it for things that stay true: a proper name and its exact '
                'spelling, who someone is, where they train or work, a standing '
                'preference. Above all use it when they correct you, since '
                'speech-to-text mangles names and a correction is the only '
                'chance to learn the right one.\n'
                'Do NOT use it for anything that stops being true — what they '
                'ate today, a plan for this afternoon, their mood. Those belong '
                'in the journal or a to-do, not here. One line at a time.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'note': {
                        'type': 'string',
                        'description': (
                            'The fact, in one self-contained sentence that will '
                            'still make sense months from now — "Their gym is '
                            'Movati (speech-to-text hears \'motivate\')", not '
                            '"it\'s Movati".'
                        ),
                    },
                },
                'required': ['note'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'revise_memory',
            'description': (
                'Change or remove something already in the standing document — a '
                'fact that is now wrong, out of date, or duplicated. Not for '
                'adding: use `remember` for that. Describe the change; do not '
                'write out the document.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'instruction': {
                        'type': 'string',
                        'description': (
                            'One sentence saying what to change — "their gym is '
                            'now GoodLife, not Movati" or "drop the line about '
                            'the old apartment".'
                        ),
                    },
                },
                'required': ['instruction'],
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
    todo_list = _text(args.get('list')) or 'todo'
    if todo_list not in VALID_LISTS:
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


def _remember(args: dict) -> tuple[str, dict]:
    """The one tool here that writes. Its event carries no `proposal`, the
    `ask_user` shape, so nothing about it can reach the confirm-card path.

    Writing without a card is a deliberate trade: a correction mid-sentence
    should not cost a click, and unlike a calendar event this is reversible from
    Settings, where every revision is kept.
    """
    from backend.memory import MemoryFull, append_note

    note = _text(args.get('note'))
    if not note:
        return _refused('remember', 'there is nothing to remember yet — say what the fact is')
    try:
        append_note(note)
    except MemoryFull as e:
        return _refused('remember', str(e))
    except Exception as e:
        logger.warning('remember failed: %s', e)
        return _refused('remember', 'the memory could not be written')
    return (
        f'Saved to your standing notes about the user: {note}\n'
        'It is written already — acknowledge it in a few words at most, and do '
        'not ask them to confirm it.',
        {'tool': 'remember', 'ok': True, 'arg': note},
    )


def _revise_memory(args: dict) -> tuple[str, dict]:
    """Queues the rewrite rather than doing it here.

    Rewriting the whole document is a full generation, and this runs on the
    blocking decision turn that sits in front of the user's reply. `run_bg` puts
    it behind the same single worker journal polish uses, so the reply starts
    streaming now and the document catches up.
    """
    from backend.ai.background import run_bg

    instruction = _text(args.get('instruction'))
    if not instruction:
        return _refused('revise_memory', 'say what about the memory should change')
    run_bg(lambda: _apply_revision(instruction))
    return (
        f'Queued a revision of your standing notes: {instruction}\n'
        'It happens in the background — say in a few words that you have updated '
        'them, and do not read the notes back.',
        {'tool': 'revise_memory', 'ok': True, 'arg': instruction},
    )


def _apply_revision(instruction: str) -> None:
    from backend.ai.memory import revise_memory_document
    from backend.memory import get_memory, set_memory

    try:
        revised = revise_memory_document(get_memory(), instruction)
        # None means the model was unavailable or answered unusably. Leaving the
        # document untouched is the whole point of that signal — a half-answer
        # overwriting a page of standing facts is the failure worth avoiding.
        if revised is not None:
            set_memory(revised, source='revise', note=instruction)
    except Exception as e:
        logger.warning('Applying memory revision failed: %s', e)


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
    'propose_note_to_self': _propose_note_to_self,
    'propose_flashcards': _propose_flashcards,
    'remember': _remember,
    'revise_memory': _revise_memory,
    'ask_user': _ask_user,
}


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    """Execute one proposal tool. Returns (text for the model, event for the UI)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f'Unknown tool: {name}', {'tool': name, 'ok': False, 'error': 'unknown tool'}
    return handler(args if isinstance(args, dict) else {})
