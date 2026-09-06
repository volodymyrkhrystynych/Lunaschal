"""Unit tests for backend.calendar_voice — the pure half of the day view's mic
button, which decides what one spoken sentence is allowed to change about an
event. The LLM call itself is tested in test_calendar_ai.py; the route in
test_calendar_routes.py."""
import pytest

from backend.calendar_voice import (
    MAX_TITLE_LEN,
    VOICE_EDIT_SCHEMA,
    normalize_voice_edit,
    shift_end_time,
)


def _event(**overrides) -> dict:
    current = {
        'title': 'Gym',
        'description': None,
        'time': '09:00',
        'endTime': '10:00',
        'tags': [],
    }
    current.update(overrides)
    return current


def test_schema_cannot_express_a_date():
    # The guard that matters most: llama-server compiles this to a grammar, so
    # a `date` absent here is a `date` the model is incapable of emitting.
    assert 'date' not in VOICE_EDIT_SCHEMA['properties']
    assert set(VOICE_EDIT_SCHEMA['properties']) == {
        'title', 'description', 'time', 'endTime', 'tags'
    }


def test_a_date_the_model_emitted_anyway_is_dropped():
    edit = normalize_voice_edit({'date': '2026-07-09', 'title': 'Dentist'}, _event())
    assert edit == {'title': 'Dentist'}


def test_nulls_mean_no_change():
    edit = normalize_voice_edit(
        {'title': None, 'description': None, 'time': None, 'endTime': None, 'tags': None},
        _event(),
    )
    assert edit == {}


def test_restating_the_current_value_is_not_an_edit():
    # Otherwise the button reports "updated name" for a sentence that moved
    # nothing, which is worse than saying nothing at all.
    edit = normalize_voice_edit({'title': 'Gym', 'time': '09:00'}, _event())
    assert edit == {}


def test_moving_the_start_carries_the_length_across():
    edit = normalize_voice_edit({'time': '14:15'}, _event())
    assert edit == {'time': '14:15', 'endTime': '15:15'}


def test_a_spoken_end_time_wins_over_the_carried_one():
    edit = normalize_voice_edit({'time': '14:00', 'endTime': '14:30'}, _event())
    assert edit == {'time': '14:00', 'endTime': '14:30'}


def test_an_event_with_no_end_time_gains_none():
    edit = normalize_voice_edit({'time': '14:00'}, _event(endTime=None))
    assert edit == {'time': '14:00'}


@pytest.mark.parametrize(
    'spoken,expected',
    [('9:05', '09:05'), ('09:05:00', '09:05'), ('23:59', '23:59'), ('00:00', '00:00')],
)
def test_loose_time_shapes_are_normalized(spoken, expected):
    edit = normalize_voice_edit({'time': spoken}, _event(time='12:00', endTime=None))
    assert edit['time'] == expected


@pytest.mark.parametrize('spoken', ['25:00', '10:75', 'half past two', '', '1000', None, 9])
def test_unusable_times_are_dropped_rather_than_stored(spoken):
    edit = normalize_voice_edit({'time': spoken}, _event())
    assert 'time' not in edit
    # And nothing rides along on a dropped start.
    assert 'endTime' not in edit


def test_shift_end_time_preserves_a_length_that_crosses_midnight():
    # 23:00-00:30 is ninety minutes, not minus twenty-two and a half hours.
    assert shift_end_time('23:00', '00:30', '22:00') == '23:30'


def test_shift_end_time_wraps_the_landing_end_past_midnight():
    assert shift_end_time('09:00', '10:00', '23:30') == '00:30'


def test_a_runaway_title_is_capped():
    edit = normalize_voice_edit({'title': 'x' * 5000}, _event())
    assert len(edit['title']) == MAX_TITLE_LEN


def test_a_blank_title_is_not_an_edit():
    assert normalize_voice_edit({'title': '   '}, _event()) == {}


def test_tags_are_normalized_the_same_way_the_form_normalizes_them():
    edit = normalize_voice_edit({'tags': ['Work', 'work ', 'Errand']}, _event())
    assert edit == {'tags': ['work', 'errand']}


def test_an_empty_tag_list_is_read_as_no_change_not_as_clear_them():
    # The model reaches for [] whenever a sentence mentions no labels, and
    # wiping a hand-typed set on an unrelated remark is not a trade worth
    # making for a command nobody speaks.
    edit = normalize_voice_edit({'tags': []}, _event(tags=['work']))
    assert edit == {}


def test_the_same_tags_said_back_are_not_an_edit_whatever_the_case_or_order():
    assert normalize_voice_edit({'tags': ['Work']}, _event(tags=['work'])) == {}
    assert normalize_voice_edit(
        {'tags': ['errand', 'work']}, _event(tags=['work', 'errand'])
    ) == {}


def test_a_non_dict_generation_yields_nothing():
    assert normalize_voice_edit(None, _event()) == {}
    assert normalize_voice_edit('the dentist', _event()) == {}
