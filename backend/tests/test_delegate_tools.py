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

    assert proposal == {'kind': 'task', 'data': {'title': 'Call the dentist', 'list': 'todo'}}
    assert event['ok'] is True
    assert 'Nothing has been saved yet' in text
    assert 'do not claim it is done' in text


def test_task_list_falls_back_when_the_model_invents_one():
    _, _, proposal = _proposal('propose_task', {'title': 'x', 'list': 'nonsense'})
    assert proposal['data']['list'] == 'todo'


def test_a_task_without_a_title_is_refused():
    text, event, proposal = _proposal('propose_task', {'title': '   '})
    assert proposal is None
    assert event['ok'] is False
    assert 'needs a title' in text


def test_calendar_event_defaults_to_today_rather_than_being_dropped():
    """A model omitting the date is far more common than one inventing a wrong
    date, and the card shows the date for the user to correct."""
    from datetime import date
    _, _, proposal = _proposal('propose_calendar_event', {'title': 'Dentist'})
    assert proposal['data']['date'] == date.today().isoformat()


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
