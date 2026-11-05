"""Real upload routes, isolated storage, and no inference/network calls."""
import io
from unittest.mock import Mock

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.routes import journal
from backend.tests.test_screenshot_journal import _upload


@pytest.fixture(autouse=True)
def media(monkeypatch, tmp_path, queued_jobs):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    monkeypatch.setattr(journal, '_retitle_bg', Mock())
    monkeypatch.setattr('backend.ai.audio_description._probe_duration', lambda p: 600)


def shot(client, minute, hour=18):
    response = _upload(client, str(ULID()), f'2026-09-12T{hour:02}:{minute:02}:00-04:00')
    assert response.status_code == 201, response.json
    return response.json


def record(client, *, minute=0, hour=18, duration='600', **extra):
    fields = dict(id=str(ULID()), attachmentId=str(ULID()),
                  capturedAt=f'2026-09-12T{hour:02}:{minute:02}:00-04:00', **extra)
    if duration is not None:
        fields['durationSeconds'] = duration
    response = client.post('/api/journal/recordings', data={
        **fields, 'file': (io.BytesIO(b'audio'), 'recording.m4a', 'audio/mp4')})
    return response, fields


def attachments(entry):
    return get_db().execute('SELECT * FROM journal_attachments WHERE entry_id=?',
                            (entry,)).fetchall()


def test_delayed_phone_audio_merges_whole_overlapping_run_and_keeps_event(client):
    first = shot(client, 58, 17)
    shot(client, 5)
    event = get_db().execute('SELECT id FROM calendar_events').fetchone()[0]
    response, fields = record(client)
    assert response.status_code == 201
    target = response.json['id']
    assert target == fields['id']
    assert len(attachments(target)) == 3
    assert client.get(f"/api/journal/{first['id']}").status_code == 404
    assert get_db().execute('SELECT journal_id FROM calendar_events WHERE id=?',
                            (event,)).fetchone()[0] == target
    assert all(journal.storage.resolve_stored_path(a['path']).exists() for a in attachments(target))
    replay = client.post('/api/journal/recordings', data=fields)
    assert replay.status_code == 201
    assert replay.json['id'] == target
    assert len(attachments(target)) == 3


def test_audio_first_and_delayed_screenshots_converge_and_replay(client):
    response, _ = record(client)
    target = response.json['id']
    first = shot(client, 3)
    second = shot(client, 7)
    assert first['id'] == second['id'] == target
    replay = client.post('/api/journal/screenshots', data={
        'attachmentId': first['attachment']['id'], 'capturedAt': '2026-09-12T18:03:00-04:00'})
    assert replay.status_code == 201
    assert replay.json['id'] == target
    event = get_db().execute('SELECT * FROM calendar_events').fetchone()
    assert (event['time'], event['end_time']) == ('18:03', '18:07')
    assert len(attachments(target)) == 3
    assert shot(client, 20)['id'] != target


def test_nonoverlapping_recording_and_edited_screenshot_stay_separate(client):
    first = shot(client, 3)
    client.patch(f"/api/journal/{first['id']}", json={'content': 'Keep this context'})
    response, _ = record(client)
    assert response.status_code == 201
    assert len(attachments(response.json['id'])) == 1
    assert client.get(f"/api/journal/{first['id']}").json['content'] == 'Keep this context'
    other = shot(client, 40)
    assert other['id'] != response.json['id']


def test_native_client_without_duration_probes_saved_file(client):
    first = shot(client, 3)
    response, _ = record(client, duration=None)
    assert response.status_code == 201
    assert response.json['attachment']['durationSeconds'] == 600
    assert not attachments(first['id'])
    assert len(attachments(response.json['id'])) == 2


def test_missing_duration_and_probe_failure_keeps_captures_separate(client, monkeypatch):
    monkeypatch.setattr('backend.ai.audio_description._probe_duration', lambda p: None)
    first = shot(client, 3)
    response, _ = record(client, duration=None)
    assert response.status_code == 201
    assert len(attachments(first['id'])) == 1
    assert len(attachments(response.json['id'])) == 1


@pytest.mark.parametrize('duration', ['nan', 'inf', '-1', '0', 'bad'])
def test_invalid_duration_rejected_before_any_write(client, duration):
    response, _ = record(client, duration=duration)
    assert response.status_code == 400
    assert get_db().execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0] == 0


def test_ambiguous_recordings_do_not_claim_screenshot(client):
    first, _ = record(client)
    second, _ = record(client, minute=1)
    photo = shot(client, 3)
    assert photo['id'] not in (first.json['id'], second.json['id'])


def test_late_caption_uses_current_entry_after_merge(client, monkeypatch):
    photo = shot(client, 3)
    response, _ = record(client)
    aid = photo['attachment']['id']
    monkeypatch.setattr(journal, '_do_attachment_caption', lambda *a: 'Discord conversation')
    notify = Mock()
    monkeypatch.setattr(journal, '_notify_subscribers', notify)
    journal._transcribe_attachment_bg(aid, photo['id'], 'image', 'unused', 'Screenshot', now=True)
    saved = next(a for a in attachments(response.json['id']) if a['id'] == aid)
    assert saved['transcript'] == 'Discord conversation'
    notify.assert_called_with(response.json['id'])


def test_one_recording_combines_multiple_runs(client):
    first = shot(client, 1)
    shot(client, 2)
    # An unrelated text entry closes the first automatic run.
    text = client.post('/api/journal', json={'content': 'Separate note', 'title': 'Note'}).json
    second = shot(client, 6)
    shot(client, 8)
    response, _ = record(client)
    target = response.json['id']
    assert len(attachments(target)) == 5
    assert not attachments(first['id']) and not attachments(second['id'])
    assert client.get(f"/api/journal/{text['id']}").json['content'] == 'Separate note'
    events = get_db().execute('SELECT * FROM calendar_events WHERE journal_id=?', (target,)).fetchall()
    assert len(events) == 1
    assert (events[0]['time'], events[0]['end_time']) == ('18:01', '18:08')
    assert sorted(a['position'] for a in attachments(target)) == list(range(5))


def test_replay_finishes_merge_after_audio_was_saved(client, monkeypatch):
    photo = shot(client, 3)
    finish = journal._finish_recording_overlap
    monkeypatch.setattr(journal, '_finish_recording_overlap', lambda *a: None)
    response, fields = record(client)
    assert len(attachments(photo['id'])) == 1
    monkeypatch.setattr(journal, '_finish_recording_overlap', finish)
    replay = client.post('/api/journal/recordings', data=fields)
    assert replay.status_code == 201
    assert replay.json['id'] == response.json['id']
    assert len(attachments(response.json['id'])) == 2


def test_cross_midnight_and_time_zone_offsets_match_absolute_instants(client):
    photo = _upload(client, str(ULID()), '2026-09-13T04:02:00+00:00').json
    response, _ = record(client, hour=23, minute=58)
    assert response.status_code == 201
    assert not attachments(photo['id'])
    assert len(attachments(response.json['id'])) == 2


def test_no_capture_timestamp_never_uses_upload_time_for_matching(client):
    photo = shot(client, 3)
    response = client.post('/api/journal/recordings', data={
        'durationSeconds': '999999999',
        'file': (io.BytesIO(b'audio'), 'recording.m4a', 'audio/mp4')})
    assert response.status_code == 201
    assert response.json['attachment']['durationSeconds'] is None
    assert len(attachments(photo['id'])) == 1
