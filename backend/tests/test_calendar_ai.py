"""Unit tests for backend.ai.calendar.classify_event_categories — confirms it
calls the shared chat_json helper and writes the result back onto the
calendar_events row, following the same monkeypatch style as
test_email_ai.py."""
import time

import pytest
from ulid import ULID

from backend.ai import calendar as calendar_ai
from backend.db.connection import get_db


def _insert_event(db, **overrides) -> str:
    row_id = str(ULID())
    now = int(time.time())
    defaults = dict(
        id=row_id, title='Gym', description='Went for a run in the park with the dog.',
        date='2026-07-01', time='09:00', end_time='10:00', all_day=0, created_at=now,
    )
    defaults.update(overrides)
    db.execute(
        """
        INSERT INTO calendar_events (id, title, description, date, time, end_time, all_day, created_at)
        VALUES (:id, :title, :description, :date, :time, :end_time, :all_day, :created_at)
        """,
        defaults,
    )
    db.commit()
    return row_id


def test_classify_event_categories_writes_valid_categories(client, monkeypatch):
    db = get_db()
    event_id = _insert_event(db)

    calls = []

    def fake_chat_json(text, system=None, schema=None):
        calls.append(system)
        return {'categories': ['exercise', 'outside']}

    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(calendar_ai, 'chat_json', fake_chat_json)

    calendar_ai.classify_event_categories(event_id)

    assert calls == [calendar_ai._CATEGORY_SYSTEM]
    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
    assert row['category_tags'] == '["exercise", "outside"]'
    assert row['classified_at'] is not None
    assert row['classification_error'] is None


def test_classify_event_categories_dedupes_and_caps_at_three(client, monkeypatch):
    db = get_db()
    event_id = _insert_event(db)

    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        calendar_ai, 'chat_json',
        lambda text, system=None, schema=None: {
            'categories': ['work', 'work', 'family', 'leisure', 'indoors']
        },
    )

    calendar_ai.classify_event_categories(event_id)

    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
    import json
    assert json.loads(row['category_tags']) == ['work', 'family', 'leisure']


def test_classify_event_categories_out_of_vocabulary_values_are_dropped(client, monkeypatch):
    db = get_db()
    event_id = _insert_event(db)

    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        calendar_ai, 'chat_json',
        lambda text, system=None, schema=None: {'categories': ['nonsense', 'work']},
    )

    calendar_ai.classify_event_categories(event_id)

    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
    import json
    assert json.loads(row['category_tags']) == ['work']


def test_classify_event_categories_unconfigured_leaves_row_pending(client, monkeypatch):
    db = get_db()
    event_id = _insert_event(db)
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: False)

    calendar_ai.classify_event_categories(event_id)

    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
    assert row['category_tags'] is None
    assert row['classified_at'] is None


def test_classify_event_categories_missing_row_is_a_noop(client):
    calendar_ai.classify_event_categories('does-not-exist')  # must not raise


def test_classify_event_categories_llm_failure_records_error_and_stays_pending(client, monkeypatch):
    db = get_db()
    event_id = _insert_event(db)
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)

    def boom(text, system=None, schema=None):
        raise RuntimeError('llm unreachable')

    monkeypatch.setattr(calendar_ai, 'chat_json', boom)

    calendar_ai.classify_event_categories(event_id)

    row = db.execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
    assert row['classified_at'] is None
    assert row['classification_error'] == 'llm unreachable'


def test_prompt_text_includes_title_and_description():
    text = calendar_ai._prompt_text(
        {'title': 'Family walk', 'description': 'Walked around the block with the kids.'}
    )
    assert 'Family walk' in text
    assert 'Walked around the block with the kids.' in text


# --- parse_event_voice_edit: the day view's mic button ----------------------


def _current(**overrides) -> dict:
    current = {
        'title': 'Gym', 'description': None,
        'time': '09:00', 'endTime': '10:00', 'tags': ['fitness'],
    }
    current.update(overrides)
    return current


def test_parse_event_voice_edit_passes_the_bounded_schema(monkeypatch):
    seen = {}

    def fake_chat_json(text, system=None, schema=None):
        seen['text'] = text
        seen['schema'] = schema
        return {'title': 'Dentist', 'description': None, 'time': '14:15',
                'endTime': None, 'tags': None}

    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(calendar_ai, 'chat_json', fake_chat_json)

    edit = calendar_ai.parse_event_voice_edit(_current(), 'that was the dentist, quarter past two')

    # The event as it stands has to be in the prompt: almost everything this
    # call decides is relative to it.
    assert 'Gym' in seen['text'] and '09:00' in seen['text'] and 'fitness' in seen['text']
    assert 'date' not in seen['schema']['properties']
    # The end time was not spoken, so the hour-long length came with it.
    assert edit == {'title': 'Dentist', 'time': '14:15', 'endTime': '15:15'}


def test_parse_event_voice_edit_unconfigured_makes_no_call(monkeypatch):
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: False)
    monkeypatch.setattr(calendar_ai, 'chat_json', lambda *a, **k: pytest.fail('called'))
    assert calendar_ai.parse_event_voice_edit(_current(), 'move it to two') == {}


def test_parse_event_voice_edit_swallows_an_llm_failure(monkeypatch):
    # The recording is already in hand by the time this runs; the caller's
    # fallback is to keep it as the description, and it can only do that if
    # this returns rather than raises.
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)

    def boom(text, system=None, schema=None):
        raise RuntimeError('llm unreachable')

    monkeypatch.setattr(calendar_ai, 'chat_json', boom)
    assert calendar_ai.parse_event_voice_edit(_current(), 'move it to two') == {}


def test_parse_event_voice_edit_ignores_a_blank_transcript(monkeypatch):
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(calendar_ai, 'chat_json', lambda *a, **k: pytest.fail('called'))
    assert calendar_ai.parse_event_voice_edit(_current(), '   ') == {}
