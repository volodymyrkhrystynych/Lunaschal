"""Merging a voice-only journal entry — nothing but a single recording, made
via the bottom bar's Record button — into another entry from the same day.

The behaviours worth pinning down: only a same-day, single-attachment,
no-body-text entry is ever eligible (anything else would silently drop text
or another attachment), the recording actually moves across as the target's
attachment rather than being copied, and the now-empty source entry is gone
afterward.
"""
import io
from datetime import datetime

import pytest

from backend.db.connection import get_db
from backend.routes import journal as journal_routes

# Fixed local noon/evening instants on two different calendar days, so the
# day-window math (backend/routes/journal.py's _local_day) is exercised the
# same way regardless of the machine's timezone.
DAY1_NOON = int(datetime(2026, 1, 15, 12, 0, 0).timestamp())
DAY1_EVENING = int(datetime(2026, 1, 15, 20, 0, 0).timestamp())
DAY2_NOON = int(datetime(2026, 1, 16, 12, 0, 0).timestamp())


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _no_background_work(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)


def _recording(client, *, filename='memo.m4a', mime='audio/mp4'):
    r = client.post(
        '/api/journal/recordings',
        data={'file': (io.BytesIO(b'\x00' * 1024), filename, mime)},
        content_type='multipart/form-data',
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()['id']


def _entry(client, content='Something written.'):
    r = client.post('/api/journal', json={'content': content})
    assert r.status_code == 201, r.get_json()
    return r.get_json()['id']


def _set_created_at(entry_id, timestamp):
    db = get_db()
    db.execute('UPDATE journal_entries SET created_at=? WHERE id=?', (timestamp, entry_id))
    db.commit()


# --- merge-candidates ---------------------------------------------------------

def test_merge_candidates_lists_other_entries_from_the_same_day(client):
    source = _recording(client)
    _set_created_at(source, DAY1_NOON)
    same_day = _entry(client, 'Written earlier today.')
    _set_created_at(same_day, DAY1_EVENING)
    other_day = _entry(client, 'Written the day before.')
    _set_created_at(other_day, DAY2_NOON)

    ids = [c['id'] for c in client.get(f'/api/journal/{source}/merge-candidates').get_json()]
    assert ids == [same_day]


def test_merge_candidates_404s_for_a_missing_entry(client):
    assert client.get('/api/journal/nope/merge-candidates').status_code == 404


# --- merge ---------------------------------------------------------------------

def test_merge_moves_the_attachment_and_deletes_the_source_entry(client):
    source = _recording(client)
    _set_created_at(source, DAY1_NOON)
    target = _entry(client, 'Notes from lunch.')
    _set_created_at(target, DAY1_EVENING)
    attachment_id = client.get(f'/api/journal/{source}/attachments').get_json()[0]['id']

    r = client.post(f'/api/journal/{source}/merge', json={'targetId': target})
    assert r.status_code == 200, r.get_json()
    merged = r.get_json()
    assert merged['id'] == target
    assert [a['id'] for a in merged['attachments']] == [attachment_id]

    assert client.get(f'/api/journal/{source}').status_code == 404
    # The recording itself survives, now hanging off the target entry.
    assert client.get(f'/api/journal/attachments/{attachment_id}/file').status_code == 200


def test_merge_rejects_a_source_entry_with_body_text(client):
    source = _recording(client)
    _set_created_at(source, DAY1_NOON)
    db = get_db()
    db.execute(
        "UPDATE journal_entries SET content='Actually I wrote something.' WHERE id=?",
        (source,),
    )
    db.commit()
    target = _entry(client)
    _set_created_at(target, DAY1_EVENING)

    r = client.post(f'/api/journal/{source}/merge', json={'targetId': target})
    assert r.status_code == 400


def test_merge_rejects_a_source_entry_with_more_than_one_attachment(client):
    source = _recording(client)
    _set_created_at(source, DAY1_NOON)
    client.post(
        f'/api/journal/{source}/attachments',
        data={'file': (io.BytesIO(b'\xff' * 64), 'photo.jpg', 'image/jpeg')},
        content_type='multipart/form-data',
    )
    target = _entry(client)
    _set_created_at(target, DAY1_EVENING)

    r = client.post(f'/api/journal/{source}/merge', json={'targetId': target})
    assert r.status_code == 400


def test_merge_rejects_a_text_entry_as_the_source(client):
    source = _entry(client, 'Just a written note, no recording.')
    _set_created_at(source, DAY1_NOON)
    target = _entry(client)
    _set_created_at(target, DAY1_EVENING)

    r = client.post(f'/api/journal/{source}/merge', json={'targetId': target})
    assert r.status_code == 400


def test_merge_rejects_entries_from_different_days(client):
    source = _recording(client)
    _set_created_at(source, DAY1_NOON)
    target = _entry(client)
    _set_created_at(target, DAY2_NOON)

    r = client.post(f'/api/journal/{source}/merge', json={'targetId': target})
    assert r.status_code == 400


def test_merge_rejects_merging_into_itself(client):
    source = _recording(client)
    r = client.post(f'/api/journal/{source}/merge', json={'targetId': source})
    assert r.status_code == 400


def test_merge_requires_a_target_id(client):
    source = _recording(client)
    assert client.post(f'/api/journal/{source}/merge', json={}).status_code == 400


def test_merge_404s_on_a_missing_source(client):
    target = _entry(client)
    r = client.post('/api/journal/nope/merge', json={'targetId': target})
    assert r.status_code == 404


def test_merge_404s_on_a_missing_target(client):
    source = _recording(client)
    r = client.post(f'/api/journal/{source}/merge', json={'targetId': 'nope'})
    assert r.status_code == 404
