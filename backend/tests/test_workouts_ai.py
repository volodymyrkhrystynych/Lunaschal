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
