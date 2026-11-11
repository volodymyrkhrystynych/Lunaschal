"""Spoken to-dos: the title/notes split, the refine pass after a GPU pause, and
the Watch's To-do button (a journal recording that is also a to-do).

The failure these pin down is the one users saw: a title that was just the
transcript cut off, with the rest of what was said nowhere.
"""
import io
from unittest.mock import patch

import pytest
from ulid import ULID

from backend.ai import todo as todo_ai
from backend.ai.service import InferencePaused
from backend.db import connection
from backend.routes import journal as journal_routes


# --- split_title_notes ----------------------------------------------------------

def test_short_text_is_its_own_title_with_no_notes():
    assert todo_ai.split_title_notes('Buy milk') == ('Buy milk', None)
    assert todo_ai.split_title_notes('Buy milk.') == ('Buy milk', None)


def test_first_sentence_becomes_the_title_and_all_of_it_the_notes():
    title, notes = todo_ai.split_title_notes('Call the dentist. Ask about moving the cleaning.')
    assert title == 'Call the dentist'
    assert notes == 'Call the dentist. Ask about moving the cleaning.'


def test_a_long_sentence_splits_at_its_first_clause():
    said = ('pick up the dry cleaning, the ticket is in my wallet and they close '
            'at six so it has to be before then')
    title, notes = todo_ai.split_title_notes(said)
    assert title == 'pick up the dry cleaning'
    assert notes == said


def test_a_run_on_sentence_is_cut_at_a_word_and_marked():
    said = ('i need to remember to email the landlord about the leak under the '
            'kitchen sink because it is getting worse every day')
    title, notes = todo_ai.split_title_notes(said)
    assert title.endswith('…')
    head = title.rstrip('…')
    assert len(head) <= todo_ai.TITLE_TARGET
    assert said.startswith(head) and said[len(head)] == ' '
    assert notes == said


def test_one_enormous_word_is_cut_at_the_limit():
    title, notes = todo_ai.split_title_notes('x' * 200)
    assert title == 'x' * todo_ai.TITLE_TARGET + '…'
    assert notes == 'x' * 200


def test_empty_text():
    assert todo_ai.split_title_notes('   ') == ('', None)


# --- extract_todo_fields post-processing -------------------------------------------

def _fields(model_answer, said='anything'):
    with patch.object(todo_ai, 'chat_json', return_value=model_answer):
        return todo_ai.extract_todo_fields(said, '2026-11-09')


def test_a_good_answer_is_kept_as_given():
    said = 'remind me to call the dentist about the cleaning'
    f = _fields({'title': 'Call the dentist', 'notes': 'About the cleaning.',
                 'priority': None, 'due': None}, said)
    assert (f['title'], f['notes']) == ('Call the dentist', 'About the cleaning.')


def test_a_sentence_for_a_title_is_split_and_nothing_is_dropped():
    said = ('call the plumber about the leak under the kitchen sink because it is '
            'getting worse every single day and the cabinet is warping')
    f = _fields({'title': said, 'notes': None, 'priority': None, 'due': None}, said)
    assert len(f['title'].rstrip('…')) <= todo_ai.TITLE_LIMIT
    assert said in f['notes']


def test_notes_left_empty_beside_a_long_transcript_keep_the_transcript():
    said = ('buy a birthday present for Anna she wanted the blue ceramic teapot '
            'from the shop on Queen street near the bakery')
    f = _fields({'title': 'Buy a present for Anna', 'notes': None,
                 'priority': None, 'due': None}, said)
    assert f['title'] == 'Buy a present for Anna'
    assert f['notes'] == said


def test_a_paused_gpu_is_raised_not_swallowed():
    with patch.object(todo_ai, 'chat_json', side_effect=InferencePaused('paused')):
        with pytest.raises(InferencePaused):
            todo_ai.extract_todo_fields('buy milk', '2026-11-09')


def test_the_schema_has_no_length_cap_for_the_grammar_to_cut_at():
    assert 'maxLength' not in todo_ai._TODO_SCHEMA['properties']['title']


# --- todo_title_and_notes (chat add_todos, briefing) -------------------------------

def test_another_models_long_title_is_split_into_the_notes():
    long_title = ('Email the landlord about the leak under the kitchen sink and ask '
                  'when the plumber can come this week')
    title, notes = todo_ai.todo_title_and_notes(long_title, 'Mention the photos.')
    assert len(title.rstrip('…')) <= todo_ai.TITLE_TARGET
    assert notes.startswith(long_title)
    assert notes.endswith('Mention the photos.')


def test_a_short_title_is_untouched():
    assert todo_ai.todo_title_and_notes('Buy milk', '') == ('Buy milk', None)


def test_add_todos_splits_rather_than_cuts(client):
    from backend.delegate.tools import _add_todos

    long_title = ('Book the car in for its winter tyres before the first snow, the '
                  'garage on Dundas said Tuesdays are quietest')
    _add_todos({'items': [{'title': long_title}]})
    row = connection.get_db().execute('SELECT title, notes FROM chat_todos').fetchone()
    assert len(row['title'].rstrip('…')) <= todo_ai.TITLE_TARGET
    assert row['notes'] == long_title


# --- a pause, then the refine pass ---------------------------------------------------

def _paused_create(client, queued_jobs, said, todo_id):
    with patch('backend.routes.tasks.extract_todo_fields', side_effect=InferencePaused('paused')):
        r = client.post('/api/tasks/todos/transcribe', json={'text': said, 'id': todo_id})
    assert r.status_code == 201


def test_a_todo_made_during_a_pause_is_refined_once_the_gpu_is_back(client, queued_jobs):
    from backend.routes.tasks import refine_spoken_todo

    said = ('call the dentist tomorrow about moving my cleaning to next week '
            'because I have the trip')
    _paused_create(client, queued_jobs, said, 'paused-1')
    job = next(j for j in queued_jobs if j['kind'] == 'todo.refine_spoken')
    assert job['target_id'] == 'paused-1'

    good = {'title': 'Call the dentist', 'notes': 'Move the cleaning; the trip.',
            'priority': 2, 'due': '2026-11-10'}
    with patch('backend.routes.tasks.extract_todo_fields', return_value=good):
        refine_spoken_todo('paused-1', job['payload'])
    todo = next(t for t in client.get('/api/tasks/todos').get_json() if t['id'] == 'paused-1')
    assert (todo['title'], todo['notes'], todo['priority']) == (
        'Call the dentist', 'Move the cleaning; the trip.', 2)
    assert todo['due'] is not None


def test_the_refine_pass_leaves_a_todo_the_user_has_edited(client, queued_jobs):
    from backend.routes.tasks import refine_spoken_todo

    said = 'water the plants on the balcony and the ones in the bedroom too please'
    _paused_create(client, queued_jobs, said, 'paused-2')
    job = next(j for j in queued_jobs if j['kind'] == 'todo.refine_spoken')
    client.patch('/api/tasks/todos/paused-2', json={'title': 'Water plants'})

    with patch('backend.routes.tasks.extract_todo_fields',
               return_value={'title': 'Model title', 'notes': None, 'priority': None,
                             'due': None}) as extract:
        refine_spoken_todo('paused-2', job['payload'])
    extract.assert_not_called()
    todo = next(t for t in client.get('/api/tasks/todos').get_json() if t['id'] == 'paused-2')
    assert todo['title'] == 'Water plants'


def test_the_refine_pass_waits_out_a_pause(client, queued_jobs):
    from backend.routes.tasks import refine_spoken_todo

    _paused_create(client, queued_jobs, 'renew the passport before the trip in march', 'paused-3')
    job = next(j for j in queued_jobs if j['kind'] == 'todo.refine_spoken')
    with patch('backend.routes.tasks.extract_todo_fields', side_effect=InferencePaused('still')):
        with pytest.raises(InferencePaused):
            refine_spoken_todo('paused-3', job['payload'])


# --- the Watch's To-do button ----------------------------------------------------------

@pytest.fixture
def _journal_media(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))
    from backend.journal import storage

    monkeypatch.setattr(storage, '_root_override', None, raising=False)


def _record_todo(client, *, entry_id, attachment_id, todo_id):
    form = {
        'file': (io.BytesIO(b'\x00' * 2048), 'recording.m4a', 'audio/mp4'),
        'id': entry_id, 'attachmentId': attachment_id, 'todoId': todo_id,
        'transcribe': 'true',
    }
    return client.post('/api/journal/recordings', data=form, content_type='multipart/form-data')


def _transcribes(monkeypatch, text):
    monkeypatch.setattr(journal_routes, '_do_attachment_audio', lambda _p: text)
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)
    monkeypatch.setattr(journal_routes, '_generate_metadata_bg', lambda *a, **k: None)


def test_a_watch_todo_is_a_journal_entry_and_a_todo(client, monkeypatch, run_jobs_sync,
                                                    _journal_media):
    said = 'pick up the dry cleaning on the way home, the ticket is in my wallet'
    _transcribes(monkeypatch, said)
    entry_id, attachment_id, todo_id = str(ULID()), str(ULID()), str(ULID())
    fields = {'title': 'Pick up the dry cleaning',
              'notes': 'On the way home; the ticket is in my wallet.',
              'priority': None, 'due': None}
    with patch('backend.routes.tasks.extract_todo_fields', return_value=fields):
        r = _record_todo(client, entry_id=entry_id, attachment_id=attachment_id, todo_id=todo_id)
    assert r.status_code == 201
    assert r.get_json()['todoId'] == todo_id

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert said in (entry['rawContent'] or entry['content'])
    todo = next(t for t in client.get('/api/tasks/todos').get_json() if t['id'] == todo_id)
    assert (todo['title'], todo['notes']) == (fields['title'], fields['notes'])
    assert todo['list'] == 'todo'


def test_a_replayed_watch_todo_makes_one_todo(client, monkeypatch, run_jobs_sync, _journal_media):
    _transcribes(monkeypatch, 'buy stamps')
    ids = dict(entry_id=str(ULID()), attachment_id=str(ULID()), todo_id=str(ULID()))
    with patch('backend.routes.tasks.extract_todo_fields', return_value=None) as extract:
        assert _record_todo(client, **ids).status_code == 201
        assert _record_todo(client, **ids).status_code == 201
    assert extract.call_count == 1
    todos = [t for t in client.get('/api/tasks/todos').get_json() if t['id'] == ids['todo_id']]
    assert [t['title'] for t in todos] == ['buy stamps']


def test_a_silent_watch_todo_makes_no_todo(client, monkeypatch, run_jobs_sync, _journal_media):
    _transcribes(monkeypatch, '')
    todo_id = str(ULID())
    _record_todo(client, entry_id=str(ULID()), attachment_id=str(ULID()), todo_id=todo_id)
    assert not any(t['id'] == todo_id for t in client.get('/api/tasks/todos').get_json())


def test_a_plain_transcribe_clip_makes_no_todo(client, monkeypatch, run_jobs_sync, _journal_media):
    _transcribes(monkeypatch, 'just a thought about the day')
    before = len(client.get('/api/tasks/todos').get_json())
    form = {'file': (io.BytesIO(b'\x00' * 2048), 'recording.m4a', 'audio/mp4'),
            'transcribe': 'true'}
    client.post('/api/journal/recordings', data=form, content_type='multipart/form-data')
    assert len(client.get('/api/tasks/todos').get_json()) == before
