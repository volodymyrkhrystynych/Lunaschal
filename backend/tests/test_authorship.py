"""Who wrote which part of a field, from the model write to the phone's replica."""
import io
import json
from pathlib import Path

import pytest
from ulid import ULID

from backend import authorship
from backend.db.connection import get_db
from backend.routes import journal

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'text_authorship.json').read_text())


@pytest.mark.parametrize('case', FIXTURE['runs'], ids=lambda c: c['name'])
def test_runs_match_the_shared_fixture(case):
    assert authorship.runs(case['text'], case['spans'], case['fallback']) == case['runs']


@pytest.mark.parametrize('case', FIXTURE['merges'], ids=lambda c: c['name'])
def test_merge_edit_matches_the_shared_fixture(case):
    assert authorship.merge_edit(case['old'], case['oldSpans'], case['new'],
                                 author=case.get('author', 'u'),
                                 source=case.get('source')) == case['spans']


@pytest.fixture
def no_enrichment(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal, name, lambda *a, **k: None)


def _polished_entry(client, monkeypatch, raw, polished):
    id = client.post('/api/journal', json={'content': raw, 'rawContent': raw}).json['id']
    db = get_db()
    db.execute('UPDATE journal_entries SET raw_content=? WHERE id=?', (raw, id))
    db.commit()
    monkeypatch.setattr(journal, 'polish_journal_entry', lambda text, context=None: polished)
    assert client.post(f'/api/journal/{id}/polish').status_code == 200
    return id


def _entry(client, id):
    return client.get(f'/api/journal/{id}').json


def test_typed_entry_is_the_users_own(client, no_enrichment):
    id = client.post('/api/journal', json={'content': 'my own words'}).json['id']
    assert _entry(client, id)['authorship']['content'] == [['u', 12]]


def test_polish_is_the_models_and_an_edit_colours_only_what_was_typed(client, monkeypatch, no_enrichment):
    id = _polished_entry(client, monkeypatch, 'i went to the store today', 'I went to the store today.')
    assert _entry(client, id)['authorship']['content'] == [['a', 26]]
    client.patch(f'/api/journal/{id}', json={'content': 'I went to the market today.'})
    assert _entry(client, id)['authorship']['content'] == [['a', 14], ['u', 6], ['a', 7]]
    # Polishing again is a fresh model write over the whole field.
    assert client.post(f'/api/journal/{id}/polish').status_code == 200
    assert _entry(client, id)['authorship']['content'] == [['a', 26]]


def test_a_legacy_polished_entry_needs_no_backfill(client, no_enrichment):
    id = client.post('/api/journal', json={'content': 'x'}).json['id']
    db = get_db()
    db.execute("UPDATE journal_entries SET raw_content='um so yeah', content='So, yeah.' WHERE id=?", (id,))
    db.commit()
    assert _entry(client, id)['authorship']['content'] == [['a', 9]]


def test_a_write_that_skips_authorship_falls_back_rather_than_miscolouring(client, monkeypatch, no_enrichment):
    id = _polished_entry(client, monkeypatch, 'raw words', 'Polished words.')
    client.patch(f'/api/journal/{id}', json={'content': 'Polished words, edited.'})
    db = get_db()
    # Some future path rewrites the text without telling backend/authorship.py.
    db.execute("UPDATE journal_entries SET content='Something else entirely' WHERE id=?", (id,))
    db.commit()
    assert _entry(client, id)['authorship']['content'] == [['a', 23]]


def test_a_transcript_appended_to_typing_is_speech_naming_its_recording(client, no_enrichment):
    id = client.post('/api/journal', json={'content': 'Lunch.'}).json['id']
    db = get_db()
    journal._append_entry_text(db, id, 'ramen with extra egg', author='s', source='01JCLIP')
    db.commit()
    entry = _entry(client, id)
    expected = [['u', 6], ['s', 22, '01JCLIP']]
    assert entry['authorship']['rawContent'] == expected
    # Unpolished, the body is the raw text and carries the same spans.
    assert entry['authorship']['content'] == expected


def test_polish_then_a_second_clip_keeps_both_recordings_apart(client, monkeypatch, no_enrichment):
    id = client.post('/api/journal', json={'content': 'x'}).json['id']
    db = get_db()
    db.execute("UPDATE journal_entries SET content='', raw_content=NULL WHERE id=?", (id,))
    journal._append_entry_text(db, id, 'first clip', author='s', source='A1')
    authorship.mark_ai(db, 'journal_entries', id, 'content', 'First clip.')
    db.execute("UPDATE journal_entries SET content='First clip.' WHERE id=?", (id,))
    journal._append_entry_text(db, id, 'second clip', author='s', source='A2')
    db.commit()
    spans = _entry(client, id)['authorship']['rawContent']
    assert spans == [['s', 10, 'A1'], ['s', 13, 'A2']]


def test_backfill_finds_old_transcripts_in_raw_text(client, no_enrichment):
    id = client.post('/api/journal', json={'content': 'x'}).json['id']
    db = get_db()
    raw = 'Typed first.\n\nthen I said this out loud'
    db.execute('UPDATE journal_entries SET raw_content=?, content=? WHERE id=?', (raw, raw, id))
    db.execute("INSERT INTO journal_attachments(id, entry_id, kind, name, path, position, transcript,"
               " transcript_status, created_at) VALUES ('ATT', ?, 'audio', 'clip', '/x', 0,"
               " 'then I said this out loud', 'done', 0)", (id,))
    db.commit()
    assert authorship.backfill(db) >= 1
    db.commit()
    assert _entry(client, id)['authorship']['rawContent'] == [['u', 14], ['s', 25, 'ATT']]
    assert authorship.backfill(db) == 0, 'a row that has spans is not looked at again'


def test_empty_fields_have_no_spans(client, no_enrichment):
    id = client.post('/api/journal', json={'content': 'body'}).json['id']
    assert _entry(client, id)['authorship']['title'] == []


def test_generated_title_then_renamed(client, no_enrichment):
    id = client.post('/api/journal', json={'content': 'body'}).json['id']
    db = get_db()
    db.execute("UPDATE journal_entries SET title='A walk' WHERE id=?", (id,))
    authorship.mark_ai(db, 'journal_entries', id, 'title', 'A walk')
    db.commit()
    client.patch(f'/api/journal/{id}', json={'title': 'A long walk'})
    assert _entry(client, id)['authorship']['title'] == [['a', 2], ['u', 5], ['a', 4]]


def test_deleting_an_entry_deletes_its_spans(client, monkeypatch, no_enrichment):
    id = _polished_entry(client, monkeypatch, 'raw', 'Polished.')
    client.delete(f'/api/journal/{id}')
    assert get_db().execute('SELECT 1 FROM text_authorship WHERE row_id=?', (id,)).fetchone() is None


# --- the phone ---------------------------------------------------------------

def _sync(client, collections='journal_entries'):
    return client.get('/api/mobile/sync', query_string={'collections': collections}).json


def _record(page, collection, id):
    return next(r for r in page['changes'] if r['collection'] == collection and r['id'] == id)


def test_replica_carries_authorship_and_a_phone_edit_is_merged(client, monkeypatch, no_enrichment):
    id = _polished_entry(client, monkeypatch, 'raw', 'I went to the store today.')
    page = _sync(client)
    current = _record(page, 'journal_entries', id)
    assert current['data']['authorship']['content'] == [['a', 26]]
    body = {'id': str(ULID()), 'epoch': page['epoch'], 'collection': 'journal_entries',
            'recordId': id, 'baseRevision': current['revision'], 'action': 'update',
            'data': {'content': 'I went to the market today.'}}
    result = client.post('/api/mobile/operations', json=body)
    assert result.status_code == 200, result.json
    assert result.json['change']['data']['authorship']['content'] == [['a', 14], ['u', 6], ['a', 7]]
    delta = client.get('/api/mobile/sync', query_string={'cursor': page['cursor']}).json
    assert _record(delta, 'journal_entries', id)['data']['authorship']['content'] == \
        [['a', 14], ['u', 6], ['a', 7]]


def test_a_model_write_after_the_row_update_still_reaches_the_phone(client, no_enrichment):
    id = client.post('/api/journal', json={'content': 'Polished.'}).json['id']
    page = _sync(client)
    db = get_db()
    # Spans only, no projected column moves: the authorship trigger re-sends.
    authorship.mark_ai(db, 'journal_entries', id, 'content', 'Polished.')
    db.commit()
    delta = client.get('/api/mobile/sync', query_string={'cursor': page['cursor']}).json
    assert _record(delta, 'journal_entries', id)['data']['authorship']['content'] == [['a', 9]]


@pytest.fixture
def food_root(monkeypatch, tmp_path):
    monkeypatch.setenv('FOOD_ROOT', str(tmp_path / 'food'))


def test_food_notes_follow_generated_notes_and_hand_edits(client, food_root):
    id = str(ULID())
    data = {'id': id, 'text': '', 'dish': 'Ramen', 'notes': 'typed',
            'media': [(io.BytesIO(b'\xff\xd8\xffFAKE'), 'bowl.jpg')]}
    assert client.post('/api/food', data=data, content_type='multipart/form-data').status_code == 201
    page = _sync(client, 'food_entries')
    db = get_db()
    # As the structuring pass writes it.
    db.execute("UPDATE food_entries SET notes='Rich broth, thick noodles.',"
               " generated_notes='Rich broth, thick noodles.' WHERE id=?", (id,))
    authorship.mark_ai(db, 'food_entries', id, 'notes', 'Rich broth, thick noodles.')
    db.commit()
    assert client.get(f'/api/food/{id}').json['authorship']['notes'] == [['a', 26]]
    delta = client.get('/api/mobile/sync', query_string={'cursor': page['cursor']}).json
    assert _record(delta, 'food_entries', id)['data']['authorship']['notes'] == [['a', 26]]
    client.patch(f'/api/food/{id}', json={'notes': 'Rich broth, thin noodles.'})
    assert client.get(f'/api/food/{id}').json['authorship']['notes'] == [['a', 12], ['u', 4], ['a', 9]]


def test_idea_polish_and_edit(client):
    db = get_db()
    id = str(ULID())
    db.execute("INSERT INTO ideas(id, title, raw_content, content, status, created_at, updated_at)"
               " VALUES (?, '', 'add dark mode', 'Add a dark mode.', 'new', 0, 0)", (id,))
    db.commit()
    assert client.get(f'/api/ideas/{id}').json['authorship']['content'] == [['a', 16]]
    client.patch(f'/api/ideas/{id}', json={'content': 'Add a light mode.'})
    assert client.get(f'/api/ideas/{id}').json['authorship']['content'] == [['a', 6], ['u', 5], ['a', 6]]


# --- the rest of the app -------------------------------------------------------

def test_a_dictated_chat_message_names_its_clip(client):
    from backend.routes import chat
    db = get_db()
    db.execute("INSERT INTO conversations(id, title, created_at, updated_at) VALUES ('C', NULL, 0, 0)")
    db.execute("INSERT INTO messages(id, conversation_id, role, content, status, created_at)"
               " VALUES ('M', 'C', 'user', 'Look:', 'done', 0)")
    chat._append_message_text(db, 'M', 'is this ripe', source='CLIP')
    db.commit()
    spans = json.loads(db.execute(
        f"SELECT {authorship.sql_object('messages', 'p')} AS a FROM messages p WHERE id='M'"
    ).fetchone()['a'])['content']
    assert spans == [['u', 5], ['s', 14, 'CLIP']]


def test_writing_keeps_typed_accepted_and_restored_text_apart(client):
    from backend.writing import chapters
    project = client.post('/api/writing/projects', json={'title': 'Book'}).json['id']
    chapter = client.post(f'/api/writing/projects/{project}/chapters', json={'title': 'One'}).json['id']
    assert client.patch(f'/api/writing/chapters/{chapter}', json={'content': 'She ran.'}).status_code == 200
    db = get_db()
    chapters.apply_update(db, project, chapter_id=chapter, mode='append',
                          content='The door slammed behind her.')
    db.commit()
    body = client.get(f'/api/writing/chapters/{chapter}').json
    assert body['authorship']['content'] == [['u', 8], ['a', 30]]
    saved = client.patch(f'/api/writing/chapters/{chapter}',
                         json={'content': 'She ran.\n\nThe door slammed shut behind her.'}).json
    assert saved['authorship']['content'] == [['u', 8], ['a', 19], ['u', 5], ['a', 11]]
    # Restoring the revision taken before the accept brings its spans back too.
    revisions = client.get(f'/api/writing/chapters/{chapter}/revisions').json
    before_accept = next(r for r in revisions if r['source'] == 'accept')
    client.post(f'/api/writing/chapters/{chapter}/revisions/{before_accept["id"]}/restore')
    assert client.get(f'/api/writing/chapters/{chapter}').json['authorship']['content'] == [['u', 8]]


def test_a_spoken_todo_is_the_models_rewrite_and_a_retitle_is_the_users(client, monkeypatch):
    from backend.routes import tasks
    monkeypatch.setattr(tasks, 'extract_todo_fields', lambda text, day: {
        'title': 'Call the dentist', 'notes': None, 'priority': None, 'due': None})
    id = client.post('/api/tasks/todos/transcribe', json={'text': 'remind me to call the dentist'}).json['id']
    todo = next(t for t in client.get('/api/tasks/todos').json if t['id'] == id)
    assert todo['authorship']['title'] == [['a', 16]]
    client.patch(f'/api/tasks/todos/{id}', json={'title': 'Call the dentist today'})
    todo = next(t for t in client.get('/api/tasks/todos').json if t['id'] == id)
    assert todo['authorship']['title'] == [['a', 16], ['u', 6]]


def test_a_spoken_todo_the_model_could_not_read_is_speech(client, monkeypatch):
    from backend.routes import tasks
    monkeypatch.setattr(tasks, 'extract_todo_fields', lambda text, day: None)
    id = client.post('/api/tasks/todos/transcribe', json={'text': 'buy milk'}).json['id']
    todo = next(t for t in client.get('/api/tasks/todos').json if t['id'] == id)
    assert todo['authorship']['title'] == [['s', 8]]


def test_typed_todos_are_the_users(client):
    id = client.post('/api/tasks/todos', json={'title': 'Water plants'}).json['id']
    todo = next(t for t in client.get('/api/tasks/todos').json if t['id'] == id)
    assert todo['authorship']['title'] == [['u', 12]]


def test_a_calendar_sentence_nothing_could_parse_is_kept_as_speech(client, monkeypatch):
    import backend.ai.calendar as ai_calendar
    monkeypatch.setattr(ai_calendar, 'parse_event_voice_edit', lambda current, text: None)
    id = client.post('/api/calendar', json={'title': 'Gym', 'date': '2026-11-16'}).json['id']
    client.post(f'/api/calendar/{id}/transcribe', json={'text': 'bring the blue towel'})
    rows = client.get('/api/calendar', query_string={'start': '2026-11-16', 'end': '2026-11-16'}).json
    event = next(e for e in rows if e['id'] == id)
    assert event['authorship']['title'] == [['u', 3]]
    assert event['authorship']['description'] == [['s', 20]]


def test_a_note_to_self_is_the_assistants_until_edited(client):
    from backend import notes
    id = notes.create_note('Ask Mira about the lease')
    notes.edit_note(id, 'Ask Mira about the new lease')
    assert notes.get_note(id)['authorship']['content'] == [['a', 19], ['u', 4], ['a', 5]]


def test_idea_recordings_keep_each_clips_source(client):
    from backend.routes import ideas
    db = get_db()
    db.execute("INSERT INTO ideas(id, title, raw_content, content, status, created_at, updated_at)"
               " VALUES ('I', '', 'typed line', '', 'new', 0, 0)")
    db.commit()
    ideas.apply_recording_transcript('I', 'one\n\ntwo', spans=[['s', 3, 'A1'], ['s', 5, 'A2']])
    got = client.get('/api/ideas/I').json['authorship']['rawContent']
    # The blank line joining them was the delivery's, from no one recording.
    assert got == [['u', 10], ['s', 2], ['s', 3, 'A1'], ['s', 5, 'A2']]
