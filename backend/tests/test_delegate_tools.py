"""The delegate's toolbox.

Every propose_* tool must stage rather than write, and must refuse a payload the
save routes would reject — the model can read a refusal and correct itself,
whereas a bad payload that reaches the card only fails when the user clicks it.
"""
from backend.delegate import tools


def _proposal(name, args):
    text, event = tools.run_tool(name, args)
    return text, event, event.get('proposal')


def test_a_staged_tool_never_claims_to_have_saved():
    """The model writes the reply the user reads off this text."""
    text, event, proposal = _proposal('propose_task', {'title': 'Call the dentist'})

    assert proposal == {
        'kind': 'task',
        'data': {'title': 'Call the dentist', 'list': 'todo', 'due': None,
                 'priority': 3, 'notes': None, 'repeatInterval': None,
                 'repeatUnit': None},
    }
    assert event['ok'] is True
    assert 'Nothing has been saved yet' in text
    assert 'do not claim it is done' in text


def test_a_task_carries_the_due_date_and_priority_it_was_given():
    """These were not parameters at all, so `due` and `priority` were dropped
    on the floor and every staged to-do arrived undated at neutral priority —
    the bug this whole toolbox change exists to fix."""
    _, _, proposal = _proposal('propose_task', {
        'title': 'Book the flights', 'due': '2026-08-14', 'priority': 5,
        'notes': 'window seat', 'repeatInterval': 2, 'repeatUnit': 'week',
    })
    data = proposal['data']
    # Staged as the model's own YYYY-MM-DD; the timestamp conversion happens at
    # accept time, so the card can show and edit a real date.
    assert data['due'] == '2026-08-14'
    assert data['priority'] == 5
    assert data['notes'] == 'window seat'
    assert (data['repeatInterval'], data['repeatUnit']) == (2, 'week')


def test_a_task_field_the_todos_api_would_reject_is_refused_here():
    """Refused where the model can read the reason and correct itself, rather
    than at the click, where the user just sees a card that fails."""
    for args in (
        {'title': 'x', 'priority': 9},
        {'title': 'x', 'priority': True},
        {'title': 'x', 'due': 'next friday'},
        {'title': 'x', 'due': '2026-02-30'},
        {'title': 'x', 'repeatInterval': 2},
    ):
        _, event, proposal = _proposal('propose_task', args)
        assert proposal is None, f'{args!r} should not stage a to-do'
        assert event['ok'] is False


def test_a_task_priority_defaults_to_neutral():
    _, _, proposal = _proposal('propose_task', {'title': 'Buy milk'})
    assert proposal['data']['priority'] == 3
    assert proposal['data']['due'] is None


def test_task_list_falls_back_when_the_model_invents_one():
    _, _, proposal = _proposal('propose_task', {'title': 'x', 'list': 'nonsense'})
    assert proposal['data']['list'] == 'todo'


def test_a_task_without_a_title_is_refused():
    text, event, proposal = _proposal('propose_task', {'title': '   '})
    assert proposal is None
    assert event['ok'] is False
    assert 'needs a title' in text


def test_a_calendar_event_without_a_date_asks_rather_than_assuming_today():
    """This used to stamp today's date on it. That was a guess wearing a fact's
    clothes: the card showed a real-looking date the user had never given, and
    confirming it was one click."""
    text, event, proposal = _proposal('propose_calendar_event', {'title': 'Dentist'})
    assert proposal is None
    assert event['ok'] is False
    assert 'ask_user' in text


def test_a_calendar_event_with_an_unreal_date_is_refused():
    _, _, proposal = _proposal('propose_calendar_event',
                               {'title': 'Dentist', 'date': 'next tuesday'})
    assert proposal is None


def test_an_all_day_event_clears_the_clock_it_was_also_given():
    """all_day means the whole day, not merely untimed — a time alongside it
    would be silently dropped at save, so it is dropped here where the model
    can see it in the staged summary."""
    _, _, proposal = _proposal('propose_calendar_event', {
        'title': 'Holiday', 'date': '2026-08-05', 'allDay': True, 'time': '09:00',
    })
    data = proposal['data']
    assert data['allDay'] is True
    assert data['time'] is None and data['endTime'] is None


def test_a_timed_event_is_not_marked_all_day():
    """Not knowing the time is not the same as meaning the whole day."""
    _, _, proposal = _proposal('propose_calendar_event',
                               {'title': 'Holiday', 'date': '2026-08-05'})
    assert proposal['data']['allDay'] is False


def test_calendar_event_keeps_time_and_tags():
    _, _, proposal = _proposal('propose_calendar_event', {
        'title': 'Standup', 'date': '2026-08-05', 'time': '09:30',
        'tags': ['work', '  ', 'daily'],
    })
    data = proposal['data']
    assert data['time'] == '09:30'
    # Blank tags are dropped rather than rendered as empty pills.
    assert data['tags'] == ['work', 'daily']


def test_an_untimed_calendar_event_carries_time_none():
    _, _, proposal = _proposal('propose_calendar_event',
                               {'title': 'Holiday', 'date': '2026-08-05'})
    assert proposal['data']['time'] is None


def test_calories_must_be_a_real_number_the_user_gave():
    for bad in (None, 'lots', 12.5):
        _, event, proposal = _proposal('propose_calorie_log',
                                       {'description': 'burger', 'calories': bad})
        assert proposal is None, f'{bad!r} should not stage a calorie entry'
        assert event['ok'] is False


def test_a_boolean_calorie_count_is_refused():
    """`bool` is an `int` in Python — without the explicit check a model
    answering `true` would stage a one-calorie meal."""
    _, event, proposal = _proposal('propose_calorie_log',
                                   {'description': 'burger', 'calories': True})
    assert proposal is None
    assert event['ok'] is False


def test_calories_out_of_range_are_refused():
    _, _, proposal = _proposal('propose_calorie_log',
                               {'description': 'burger', 'calories': 99999})
    assert proposal is None


def test_a_valid_calorie_log_stages():
    _, _, proposal = _proposal('propose_calorie_log',
                               {'description': 'burger', 'calories': 650})
    assert proposal == {'kind': 'calorie',
                        'data': {'description': 'burger', 'calories': 650}}


def test_an_empty_note_to_self_is_refused_with_the_reason():
    """"note to self" with no lesson yet has to send the model back to ask,
    not stage an empty card."""
    text, _, proposal = _proposal('propose_note_to_self', {'content': ''})
    assert proposal is None
    assert 'ask the user what it is' in text


def test_flashcards_stage_their_topic():
    _, _, proposal = _proposal('propose_flashcards', {'topic': 'React hooks'})
    assert proposal == {'kind': 'flashcards', 'data': {'topic': 'React hooks'}}


def test_ask_user_stages_nothing_at_all():
    """The whole reason to ask is that there is no honest payload to stage, so
    the event carries no `proposal` and can never reach the confirm-card path."""
    text, event, proposal = _proposal(
        'ask_user',
        {'question': 'Is that this Friday or next?', 'about': 'the flights to-do'},
    )
    assert proposal is None
    assert event['ok'] is True
    assert 'Nothing has been staged' in text
    assert 'Is that this Friday or next?' in text


def test_ask_user_leaves_anything_else_staged_this_turn_alone():
    """Exclusivity is per item, not per turn: "add buy milk, and remind me
    about the Dave thing" is one card and one question."""
    text, _, _ = _proposal('ask_user', {'question': 'When is the Dave thing?'})
    assert 'Anything else you staged this turn is unaffected' in text


def test_ask_user_labels_the_step_with_what_it_is_about():
    _, event, _ = _proposal('ask_user',
                            {'question': 'When?', 'about': 'the flights to-do'})
    assert event['arg'] == 'the flights to-do'


def test_ask_user_without_a_question_is_refused():
    _, event, _ = _proposal('ask_user', {'about': 'something'})
    assert event['ok'] is False


def test_an_unknown_tool_is_reported_not_raised():
    text, event, _ = _proposal('propose_something_else', {})
    assert 'Unknown tool' in text
    assert event['ok'] is False


def test_run_tool_survives_non_dict_arguments():
    """A malformed tool call is refused, not raised.

    The loop turns an exception here into an abandoned turn; a refusal is
    something the model can read and retry."""
    text, event = tools.run_tool('propose_task', None)
    assert event['ok'] is False
    assert 'needs a title' in text


def test_every_advertised_tool_has_a_handler():
    """A tool the model can see but nothing can run comes back as "Unknown
    tool", which reads to the model as broken rather than as off-limits."""
    advertised = {t['function']['name'] for t in tools.TOOLS}
    assert advertised == set(tools._HANDLERS)
