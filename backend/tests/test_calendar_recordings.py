"""An event spoken into its form, and classification of a new event.

`POST /api/calendar/recordings` keeps the food log's contract: both ids are the
client's and a replay is a no-op. What is pinned here is what the clip then does
— the transcript lands in the description once, a blank title is filled from it
and a typed one never is, and the categories wait for those words.
"""
import io

import pytest

from backend.ai import calendar as calendar_ai
from backend.ai import job_handlers  # noqa: F401  (registers handlers)
from backend.ai import jobs as llm_jobs
from backend.ai.service import InferencePaused
from backend.db.connection import get_db
from backend.routes import stt as stt_routes

EVENT = '01J00000000000000000000EVT'
CLIP = '01J00000000000000000000CLP'


@pytest.fixture(autouse=True)
def _media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('CALENDAR_ROOT', str(tmp_path / 'calendar-media'))


@pytest.fixture
def transcript(monkeypatch):
    said = {'text': 'Walked along the lake with Sam after lunch today'}
    monkeypatch.setattr(stt_routes, 'transcribe_file', lambda path: said['text'])
    return said


def _queued(kind=None):
    rows = get_db().execute(
        "SELECT id, kind FROM llm_jobs WHERE status='pending'").fetchall()
    return [r['kind'] for r in rows if kind is None or r['kind'] == kind]


def _drain():
    while True:
        rows = get_db().execute(
            "SELECT id FROM llm_jobs WHERE status='pending' ORDER BY id").fetchall()
        if not rows:
            return
        for row in rows:
            llm_jobs.process_one(row['id'])


def _event(id=EVENT):
    return get_db().execute('SELECT * FROM calendar_events WHERE id=?', (id,)).fetchone()


def _create(client, **fields):
    return client.post('/api/calendar', json={'id': EVENT, 'date': '2026-11-04', **fields})


def _upload(client, event=EVENT, clip=CLIP, mime='audio/mp4', **fields):
    return client.post('/api/calendar/recordings', data={
        'id': event, 'mediaId': clip, **fields,
        'audio': (io.BytesIO(b'clip'), 'recording.m4a', mime),
    }, content_type='multipart/form-data')


def test_a_new_event_is_queued_for_classification(client):
    assert _create(client, title='Gym').status_code == 201
    assert _queued() == ['calendar.classify']


def test_categories_ticked_by_hand_are_never_classified(client):
    _create(client, title='Gym', categoryTags=['exercise'])
    assert _queued() == []


def test_replaying_a_create_does_not_classify_again(client):
    _create(client, title='Gym')
    _drain()
    _create(client, title='Gym')
    assert _queued() == []


def test_a_blank_title_needs_clips_on_the_way(client):
    assert _create(client, title='   ').status_code == 400
    assert _create(client, title='', pendingClips=0).status_code == 400
    assert _create(client, title='', pendingClips=1).status_code == 201
    assert _event()['title'] == ''


def test_a_spoken_event_waits_for_its_transcript_before_classifying(client):
    _create(client, title='', pendingClips=1)
    assert _queued() == []


def test_a_clip_fills_the_description_title_and_categories(client, transcript, monkeypatch):
    replies = {'title': {'title': 'Lake walk.'}, 'categories': {'categories': ['outside']}}
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        calendar_ai, 'chat_json',
        lambda text, system, schema: replies['title' if 'title' in schema['properties'] else 'categories'])
    _create(client, title='', description='Typed first.', pendingClips=1)
    assert _upload(client).status_code == 201
    _drain()
    event = _event()
    assert event['description'] == f"Typed first.\n\n{transcript['text']}"
    assert event['title'] == 'Lake walk'
    assert event['category_tags'] == '["outside"]'


def test_a_typed_title_is_kept(client, transcript, monkeypatch):
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(calendar_ai, 'chat_json', lambda *a, **k: {'categories': []})
    _create(client, title='Lunch', pendingClips=1)
    _upload(client)
    _drain()
    assert _event()['title'] == 'Lunch'


def test_a_title_typed_while_the_job_waited_wins(client, monkeypatch):
    _create(client, title='', description='Spoken words', pendingClips=1)

    def typed_meanwhile(*args, **kwargs):
        client.patch(f'/api/calendar/{EVENT}', json={'title': 'Mine'})
        return {'title': 'Generated'}

    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(calendar_ai, 'chat_json', typed_meanwhile)
    calendar_ai.fill_event_title(EVENT)
    assert _event()['title'] == 'Mine'


def test_title_falls_back_to_the_first_words_without_a_model(client, transcript, monkeypatch):
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: False)
    _create(client, title='', pendingClips=1)
    _upload(client)
    _drain()
    assert _event()['title'] == 'Walked along the lake with Sam'


def test_a_paused_model_leaves_the_title_for_later(client, monkeypatch):
    _create(client, title='', description='Spoken words', pendingClips=1)

    def paused(*args, **kwargs):
        raise InferencePaused('paused')

    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(calendar_ai, 'chat_json', paused)
    with pytest.raises(InferencePaused):
        calendar_ai.fill_event_title(EVENT)
    assert _event()['title'] == ''


def test_replaying_a_clip_is_a_no_op(client, transcript):
    _create(client, title='Lunch', pendingClips=1)
    _upload(client)
    _drain()
    assert _upload(client).status_code == 201
    assert _queued() == []
    assert _event()['description'] == transcript['text']
    assert get_db().execute('SELECT COUNT(*) FROM calendar_recordings').fetchone()[0] == 1


def test_two_clips_read_in_the_order_they_landed(client, transcript):
    _create(client, title='Lunch', pendingClips=2)
    transcript['text'] = 'First.'
    _upload(client)
    _drain()
    transcript['text'] = 'Second.'
    _upload(client, clip='01J00000000000000000000CL2')
    _drain()
    assert _event()['description'] == 'First.\n\nSecond.'


def test_a_failed_transcription_still_classifies_what_was_typed(client, monkeypatch):
    def fail(path):
        raise RuntimeError('no model')

    monkeypatch.setattr(stt_routes, 'transcribe_file', fail)
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: False)
    _create(client, title='Lunch', pendingClips=1)
    _upload(client)
    llm_jobs.process_one(get_db().execute('SELECT id FROM llm_jobs').fetchone()['id'])
    row = get_db().execute('SELECT * FROM calendar_recordings').fetchone()
    assert (row['transcript_status'], row['transcript_error']) == ('error', 'no model')
    assert _queued() == ['calendar.classify']


def test_a_clip_for_a_missing_event_is_refused_without_a_date(client):
    assert _upload(client).status_code == 404
    assert _event() is None


def test_a_rescued_clip_opens_a_blank_event_on_its_date(client, transcript, monkeypatch):
    monkeypatch.setattr(calendar_ai, 'is_ai_configured', lambda: False)
    assert _upload(client, date='2026-11-04').status_code == 201
    _drain()
    assert _event()['date'] == '2026-11-04'
    assert _event()['title'] == 'Walked along the lake with Sam'


def test_non_audio_is_refused(client):
    _create(client, title='Lunch')
    response = client.post('/api/calendar/recordings', data={
        'id': EVENT, 'mediaId': CLIP,
        'audio': (io.BytesIO(b'x'), 'photo.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert response.status_code == 400


def test_deleting_the_event_removes_its_clip(client, transcript):
    from backend import calendar_media

    _create(client, title='Lunch', pendingClips=1)
    _upload(client)
    stored = get_db().execute('SELECT path FROM calendar_recordings').fetchone()['path']
    assert client.delete(f'/api/calendar/{EVENT}').status_code == 200
    assert not calendar_media.resolve_stored_path(stored).exists()
    assert get_db().execute('SELECT COUNT(*) FROM calendar_recordings').fetchone()[0] == 0
