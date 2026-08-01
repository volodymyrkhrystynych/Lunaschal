"""Unit tests for `backend.ai.workouts.parse_workout` — confirms it calls the
shared chat_json helper with the workout prompt and sanitizes the result."""
import pytest

from backend.ai import workouts


@pytest.fixture(autouse=True)
def ai_configured(monkeypatch):
    monkeypatch.setattr(workouts, 'is_ai_configured', lambda: True)


def _stub(monkeypatch, payload):
    def fake_chat_json(text, system=None, **kwargs):
        assert system == workouts._WORKOUT_SYSTEM
        assert kwargs['schema'] == workouts._WORKOUT_SCHEMA
        return payload
    monkeypatch.setattr(workouts, 'chat_json', fake_chat_json)


def test_parses_exercises_and_sets(monkeypatch):
    _stub(monkeypatch, {'exercises': [
        {'name': 'bicep curls', 'sets': [
            {'weight': 20, 'reps': 10}, {'weight': 20, 'reps': 10},
        ]},
        {'name': 'squats', 'sets': [{'weight': 60, 'reps': 8}]},
    ]})
    assert workouts.parse_workout('bicep curls 20,10 20,10\nsquats 60,8') == [
        {'name': 'bicep curls', 'sets': [
            {'weight': 20, 'reps': 10}, {'weight': 20, 'reps': 10},
        ]},
        {'name': 'squats', 'sets': [{'weight': 60, 'reps': 8}]},
    ]


def test_bodyweight_sets_keep_a_null_weight(monkeypatch):
    _stub(monkeypatch, {'exercises': [
        {'name': 'pull ups', 'sets': [{'weight': None, 'reps': 12}]},
    ]})
    result = workouts.parse_workout('pull ups 3x12')
    assert result == [{'name': 'pull ups', 'sets': [{'weight': None, 'reps': 12}]}]


def test_bare_rep_counts_are_four_bodyweight_sets(monkeypatch):
    """"squats 10 10 10 10" is how bodyweight work actually gets written: four
    sets of ten, no weight anywhere on the line."""
    _stub(monkeypatch, {'exercises': [
        {'name': 'squats', 'sets': [{'weight': None, 'reps': 10}] * 4},
    ]})
    assert workouts.parse_workout('squats 10 10 10 10') == [
        {'name': 'squats', 'sets': [
            {'weight': None, 'reps': 10}, {'weight': None, 'reps': 10},
            {'weight': None, 'reps': 10}, {'weight': None, 'reps': 10},
        ]},
    ]


def test_a_missing_weight_key_is_also_a_bodyweight_set(monkeypatch):
    """The schema requires the key, but a schema-less fallback parse can still
    hand back a set with no `weight` at all — it must not become 0 kg."""
    _stub(monkeypatch, {'exercises': [{'name': 'dips', 'sets': [{'reps': 8}]}]})
    assert workouts.parse_workout('dips 8') == [
        {'name': 'dips', 'sets': [{'weight': None, 'reps': 8}]},
    ]


def test_schema_demands_an_explicit_weight_that_may_be_null():
    """The grammar has to allow (and ask for) a null weight, or the model is
    pushed into inventing a number for bodyweight work."""
    set_schema = (
        workouts._WORKOUT_SCHEMA['properties']['exercises']['items']
        ['properties']['sets']['items']
    )
    assert set_schema['properties']['weight']['type'] == ['number', 'null']
    assert set_schema['required'] == ['weight', 'reps']


def test_prompt_shows_a_bodyweight_example():
    assert 'squats 10 10 10 10' in workouts._WORKOUT_SYSTEM
    assert 'bodyweight' in workouts._WORKOUT_SYSTEM


def test_drops_sets_carrying_neither_weight_nor_reps(monkeypatch):
    _stub(monkeypatch, {'exercises': [
        {'name': 'squats', 'sets': [
            {'weight': None, 'reps': None}, {'weight': 60, 'reps': 8},
        ]},
    ]})
    assert workouts.parse_workout('squats')[0]['sets'] == [{'weight': 60, 'reps': 8}]


def test_drops_nonsense_values(monkeypatch):
    _stub(monkeypatch, {'exercises': [
        {'name': '  squats  ', 'sets': [
            {'weight': -5, 'reps': 8},          # negative weight -> dropped
            {'weight': 'heavy', 'reps': 8},     # non-numeric weight -> dropped
            {'weight': True, 'reps': 8},        # bool is not a weight
            'not a set',
        ]},
        {'name': '', 'sets': []},               # nameless exercise -> dropped
        {'name': 42, 'sets': []},
    ]})
    assert workouts.parse_workout('squats') == [
        {'name': 'squats', 'sets': [
            {'weight': None, 'reps': 8},
            {'weight': None, 'reps': 8},
            {'weight': None, 'reps': 8},
        ]},
    ]


def test_empty_exercise_list_is_a_real_answer_not_a_failure(monkeypatch):
    _stub(monkeypatch, {'exercises': []})
    assert workouts.parse_workout('felt tired today, went for a walk') == []


@pytest.mark.parametrize('payload', [None, [], {'exercises': 'nope'}, {}])
def test_malformed_response_yields_none(monkeypatch, payload):
    _stub(monkeypatch, payload)
    assert workouts.parse_workout('squats 60,8') is None


def test_llm_error_yields_none(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('llama-server is down')
    monkeypatch.setattr(workouts, 'chat_json', boom)
    assert workouts.parse_workout('squats 60,8') is None


def test_none_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(workouts, 'is_ai_configured', lambda: False)
    assert workouts.parse_workout('squats 60,8') is None


def test_none_for_blank_text(monkeypatch):
    _stub(monkeypatch, {'exercises': []})
    assert workouts.parse_workout('   ') is None
