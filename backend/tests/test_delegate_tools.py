"""The delegate's toolbox.

Every propose_* tool must stage rather than write, and must refuse a payload the
save routes would reject — the model can read a refusal and correct itself,
whereas a bad payload that reaches the card only fails when the user clicks it.
`add_todos` and `create_note_to_self` are the exceptions: they write straight
into the DB, so their tests need the isolated per-test database (`client`,
even though they never call it directly — it's what points get_db() at a
throwaway file instead of the developer's real one).
"""
from backend.db import connection
from backend.delegate import tools


def _proposal(name, args):
    text, event = tools.run_tool(name, args)
    return text, event, event.get('proposal')


def _chat_todos():
    return connection.get_db().execute(
        'SELECT title, notes, priority, due FROM chat_todos ORDER BY created_at'
    ).fetchall()


def test_add_todos_writes_immediately_with_no_card(client):
    """Unlike the propose_* tools, nothing here is staged — the model is told
    it's already saved, and the event carries no `proposal` key."""
    text, event, proposal = _proposal('add_todos', {
        'items': [{'title': 'Call the dentist'}],
    })

    assert proposal is None
    assert event['ok'] is True
    assert 'already saved' in text
    rows = _chat_todos()
    assert [(r['title'], r['priority'], r['due']) for r in rows] == [
        ('Call the dentist', 3, None)
    ]


def test_add_todos_adds_several_in_one_call(client):
    """The whole reason this replaced propose_task: 'today I want to do X, Y,
    and Z' is one call, not three round trips through a confirm card."""
    text, event, _ = _proposal('add_todos', {'items': [
        {'title': 'Buy milk'},
        {'title': 'Call the dentist', 'notes': 'ask about the filling'},
        {'title': 'Water the plants'},
    ]})

    assert event['ok'] is True
    rows = _chat_todos()
    assert [(r['title'], r['notes']) for r in rows] == [
        ('Buy milk', None),
        ('Call the dentist', 'ask about the filling'),
        ('Water the plants', None),
    ]


def test_add_todos_skips_a_title_already_tracked_elsewhere(client):
    """A to-do added mid-conversation shouldn't duplicate something already on
    the permanent list or already sitting in today's bar."""
    db = connection.get_db()
    db.execute(
        "INSERT INTO todos(id, title, done, list, priority, created_at, updated_at)"
        " VALUES ('t1', 'Buy milk', 0, 'todo', 3, 0, 0)"
    )
    db.commit()

    _, event, _ = _proposal('add_todos', {'items': [
        {'title': 'buy MILK'}, {'title': 'Call the dentist'},
    ]})

    assert event['ok'] is True
    assert [r['title'] for r in _chat_todos()] == ['Call the dentist']


def test_add_todos_with_only_duplicates_is_refused(client):
    db = connection.get_db()
    db.execute(
        "INSERT INTO todos(id, title, done, list, priority, created_at, updated_at)"
        " VALUES ('t1', 'Buy milk', 0, 'todo', 3, 0, 0)"
    )
    db.commit()

    text, event, _ = _proposal('add_todos', {'items': [{'title': 'Buy milk'}]})
    assert event['ok'] is False
    assert 'already on the list' in text
    assert _chat_todos() == []


def test_add_todos_with_no_items_is_refused(client):
    text, event, _ = _proposal('add_todos', {'items': []})
    assert event['ok'] is False
    assert 'no items given' in text
    assert _chat_todos() == []


def test_add_todos_skips_a_blank_title_but_keeps_the_rest(client):
    _, event, _ = _proposal('add_todos', {'items': [
        {'title': '   '}, {'title': 'Buy milk'},
    ]})
    assert event['ok'] is True
    assert [r['title'] for r in _chat_todos()] == ['Buy milk']


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


def test_an_empty_flashcard_draft_is_refused_with_the_reason():
    """"flashcard this" with no lesson yet has to send the model back to
    ask, not stage an empty card."""
    text, _, proposal = _proposal('draft_flashcard', {'content': ''})
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


def test_run_tool_survives_non_dict_arguments(client):
    """A malformed tool call is refused, not raised.

    The loop turns an exception here into an abandoned turn; a refusal is
    something the model can read and retry."""
    text, event = tools.run_tool('add_todos', None)
    assert event['ok'] is False
    assert 'no items given' in text


def test_every_advertised_tool_has_a_handler():
    """A tool the model can see but nothing can run comes back as "Unknown
    tool", which reads to the model as broken rather than as off-limits."""
    advertised = {t['function']['name'] for t in tools.TOOLS}
    assert advertised == set(tools._HANDLERS)
