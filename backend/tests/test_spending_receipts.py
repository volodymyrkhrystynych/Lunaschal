"""Purchases captured like meals: receipts and clips that the model reads into an amount."""
import io
import json
from datetime import datetime, timezone

import pytest
from ulid import ULID

from backend.ai import images, jobs, job_handlers  # noqa: F401
from backend.ai import spending as ai_spending
from backend.db.connection import get_db, init_db
from backend.routes import spending, stt

RECEIPT = 'LOBLAWS\nTOTAL 42.86'


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('SPENDING_ROOT', str(tmp_path / 'spending'))
    monkeypatch.setattr(images, 'is_vision_configured', lambda: True)
    monkeypatch.setattr(images, 'describe_image', lambda path, **kw: RECEIPT)
    monkeypatch.setattr(stt, 'transcribe_file', lambda path: 'coffee with Sam, four fifty')
    calls = []

    def parse(text, *, memory='', descriptions=''):
        calls.append({'text': text, 'descriptions': descriptions})
        if 'TOTAL 42.86' in descriptions:
            return {'merchant': 'Loblaws', 'category': 'Groceries', 'amountCents': 4286,
                    'notes': text or None}
        return {'merchant': None, 'category': 'Coffee', 'amountCents': 450, 'notes': text or None}

    monkeypatch.setattr(spending, 'parse_spending_entry', parse)
    return calls


def drain():
    for _ in range(30):
        if jobs.drain_once() is None:
            return
    pytest.fail('Spending jobs did not finish')


def receipt(client, **form):
    data = {'media': (io.BytesIO(b'jpeg'), 'receipt.jpg'), **form}
    return client.post('/api/spending', data=data, content_type='multipart/form-data')


def get(client, entry_id):
    return client.get(f'/api/spending/{entry_id}').get_json()


def test_receipt_photo_alone_becomes_an_entry_whose_amount_is_read_from_it(client):
    created = receipt(client)
    assert created.status_code == 201
    entry = created.get_json()
    assert entry['amountCents'] is None and entry['reading']
    assert entry['media'][0]['descriptionStatus'] == 'running'
    drain()
    done = get(client, entry['id'])
    assert (done['merchant'], done['category'], done['amountCents']) == ('Loblaws', 'Groceries', 4286)
    assert done['media'][0]['description'] == RECEIPT
    assert not done['reading']


def test_pending_amount_is_left_out_of_totals_not_counted_as_zero(client, monkeypatch):
    monkeypatch.setattr('backend.routes.lifestyle._today', lambda: spending.day_key_for())
    client.post('/api/lifestyle/spending', json={'category': 'Coffee', 'amountCents': 450})
    receipt(client)  # not drained: still being read
    day = client.get('/api/lifestyle/spending').get_json()
    assert day['totalCents'] == 450
    # Shipped phones decode every listed entry as non-null: the unread receipt
    # is counted, not listed.
    assert [e['amountCents'] for e in day['entries']] == [450]
    assert day['pendingCount'] == 1
    today = client.get('/api/spending/trends?days=3').get_json()[-1]
    assert today['totalCents'] == 450 and today['pendingCount'] == 1
    assert today['byCategory'] == {'Coffee': 450}


def test_legacy_daily_list_names_a_read_receipt_by_its_merchant(client, monkeypatch):
    monkeypatch.setattr('backend.routes.lifestyle._today', lambda: spending.day_key_for())
    entry = receipt(client).get_json()
    client.patch(f"/api/spending/{entry['id']}", json={'amountCents': 1200, 'merchant': 'Loblaws'})
    day = client.get('/api/lifestyle/spending').get_json()
    assert [(e['category'], e['amountCents']) for e in day['entries']] == [('Loblaws', 1200)]
    assert day['pendingCount'] == 0


def test_trends_emit_every_day_oldest_first(client):
    rows = client.get('/api/spending/trends?days=7').get_json()
    assert len(rows) == 7
    assert [r['date'] for r in rows] == sorted(r['date'] for r in rows)
    assert rows[-1]['date'] == spending.day_key_for()
    assert all(r['totalCents'] == 0 and r['pendingCount'] == 0 for r in rows)


def test_hand_edit_wins_over_the_structuring_pass(client):
    entry = receipt(client).get_json()
    client.patch(f"/api/spending/{entry['id']}", json={'amountCents': 4000, 'category': 'Food'})
    drain()
    done = get(client, entry['id'])
    assert (done['amountCents'], done['category']) == (4000, 'Food')
    assert done['merchant'] == 'Loblaws'  # still empty, so still filled


def test_typed_amount_is_never_replaced_by_the_receipt(client):
    entry = receipt(client, amountCents='5000', category='Treats').get_json()
    drain()
    assert (get(client, entry['id'])['amountCents'], get(client, entry['id'])['category']) == (5000, 'Treats')


def test_a_clip_can_arrive_before_its_entry_and_is_appended_once(client, setup):
    entry_id, media_id = str(ULID()), str(ULID())
    body = {'id': entry_id, 'mediaId': media_id, 'audio': (io.BytesIO(b'm4a'), 'clip.m4a')}
    first = client.post('/api/spending/recordings', data=body, content_type='multipart/form-data')
    assert first.status_code == 201
    replay = client.post('/api/spending/recordings', content_type='multipart/form-data',
                         data={'id': entry_id, 'mediaId': media_id, 'audio': (io.BytesIO(b'm4a'), 'clip.m4a')})
    assert replay.get_json()['media']['id'] == media_id
    assert get_db().execute('SELECT COUNT(*) FROM spending_media').fetchone()[0] == 1
    # The create that the clip outran still lands, and keeps the clip.
    client.post('/api/spending', json={'id': entry_id, 'pendingClips': 1})
    drain()
    done = get(client, entry_id)
    assert done['rawContent'] == 'coffee with Sam, four fifty'
    assert (done['category'], done['amountCents']) == ('Coffee', 450)
    assert setup[-1]['text'] == 'coffee with Sam, four fifty'


def test_create_replay_with_media_ids_stores_the_photo_once(client):
    entry_id, media_id = str(ULID()), str(ULID())
    form = {'id': entry_id, 'mediaIds': f'["{media_id}"]'}
    assert receipt(client, **form).status_code == 201
    assert receipt(client, **form).status_code == 201
    assert get_db().execute('SELECT COUNT(*) FROM spending_media').fetchone()[0] == 1


def test_capture_time_files_the_entry_under_its_own_day(client):
    captured = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
    entry = receipt(client, capturedAt=captured.isoformat()).get_json()
    assert entry['date'] == spending.day_key_for(int(captured.timestamp()))


def test_exif_capture_time_moves_the_day(client, monkeypatch):
    taken = int(datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(spending, 'extract_photo_meta',
                        lambda p: {'taken_at': taken, 'latitude': 43.6, 'longitude': -79.4})
    entry = receipt(client).get_json()
    row = get_db().execute('SELECT * FROM spending_logs WHERE id=?', (entry['id'],)).fetchone()
    assert row['created_at'] == taken and row['date'] == spending.day_key_for(taken)
    assert (row['latitude'], row['longitude']) == (43.6, -79.4)


def test_video_and_empty_captures_are_refused(client):
    video = client.post('/api/spending', content_type='multipart/form-data',
                        data={'media': (io.BytesIO(b'mov'), 'till.mov')})
    assert video.get_json()['media'] == []
    assert client.post('/api/spending', json={}).status_code == 400
    assert client.post('/api/spending', json={'amountCents': 0}).status_code == 400
    assert client.post('/api/spending', json={'id': '../x', 'text': 'hi'}).status_code == 400


def test_unreadable_receipt_is_visible_and_leaves_the_amount_empty(client, monkeypatch):
    def fail(path, **kw):
        raise RuntimeError('vision model offline')
    monkeypatch.setattr(images, 'describe_image', fail)
    entry = receipt(client).get_json()
    drain()
    done = get(client, entry['id'])
    assert done['amountCents'] is None
    assert done['media'][0]['descriptionStatus'] == 'error'
    assert done['media'][0]['descriptionError'] == 'vision model offline'


def test_journal_lists_typed_and_captured_entries_newest_first(client):
    client.post('/api/lifestyle/spending', json={'category': 'Coffee', 'amountCents': 450,
                                                 'capturedAt': '2026-10-01T09:00:00+00:00'})
    entry = receipt(client).get_json()
    feed = client.get('/api/spending/journal').get_json()
    assert [e['id'] for e in feed][0] == entry['id']
    assert feed[1]['category'] == 'Coffee' and feed[1]['media'] == []
    assert feed[0]['media'][0]['url'] == f"/api/spending/media/{entry['media'][0]['id']}"
    assert client.get(feed[0]['media'][0]['url']).status_code == 200


def test_delete_removes_media_and_files(client):
    entry = receipt(client).get_json()
    root = spending.storage.entry_dir(entry['id'])
    assert root.is_dir()
    assert client.delete(f"/api/spending/{entry['id']}").status_code == 200
    assert not root.exists()
    assert get_db().execute('SELECT COUNT(*) FROM spending_media').fetchone()[0] == 0
    assert client.delete(f"/api/spending/{entry['id']}").status_code == 404


def test_typed_only_table_is_widened_in_place_keeping_rows(client):
    db = get_db()
    db.execute('DROP TABLE spending_media')
    db.execute('DROP TABLE spending_logs')
    db.execute('CREATE TABLE spending_logs (id TEXT PRIMARY KEY, date TEXT NOT NULL,'
               ' category TEXT NOT NULL, amount_cents INTEGER NOT NULL'
               ' CHECK(amount_cents > 0 AND amount_cents <= 100000000), created_at INTEGER NOT NULL)')
    db.execute("INSERT INTO spending_logs VALUES ('01J0000000000000000000000A', '2026-10-08', 'Coffee', 450, 1)")
    db.commit()
    init_db()
    init_db()
    row = get_db().execute('SELECT * FROM spending_logs').fetchone()
    assert (row['category'], row['amount_cents'], row['updated_at']) == ('Coffee', 450, 1)
    entry = receipt(client).get_json()
    assert entry['amountCents'] is None


def test_parse_drops_out_of_range_amounts(monkeypatch):
    monkeypatch.setattr(ai_spending, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ai_spending, 'chat_json', lambda *a, **k: {
        'merchant': ' Loblaws ', 'category': '', 'amountCents': 0, 'notes': None})
    assert ai_spending.parse_spending_entry('', descriptions=RECEIPT) == {
        'merchant': 'Loblaws', 'category': None, 'amountCents': None, 'notes': None}
    assert ai_spending.parse_spending_entry('', descriptions='') is None


def test_receipts_replicate_without_paths_and_the_phone_can_correct_the_amount(client, tmp_path):
    entry = receipt(client).get_json()
    drain()
    capabilities = client.get('/api/mobile/capabilities').get_json()
    assert {'spending_logs', 'spending_media'} <= set(capabilities['collections'])
    assert 'spending_media' in capabilities['mediaCollections']
    assert 'spending_logs' in capabilities['editableCollections']
    page = client.get('/api/mobile/sync', query_string={'collections': 'spending_logs,spending_media'}).get_json()
    assert str(tmp_path) not in json.dumps(page['changes'])
    row = next(c for c in page['changes'] if c['collection'] == 'spending_logs' and c['id'] == entry['id'])
    assert row['data']['amountCents'] == 4286 and 'generatedNotes' not in row['data']

    body = {'id': str(ULID()), 'epoch': page['epoch'], 'collection': 'spending_logs',
            'recordId': entry['id'], 'baseRevision': row['revision'], 'action': 'update',
            'data': {'amountCents': 4100, 'merchant': ' Loblaws City Market '}}
    first = client.post('/api/mobile/operations', json=body)
    assert first.status_code == 200, first.get_json()
    assert client.post('/api/mobile/operations', json=body).get_json() == first.get_json()
    done = get(client, entry['id'])
    assert (done['amountCents'], done['merchant']) == (4100, 'Loblaws City Market')


@pytest.mark.parametrize('data', [{'amountCents': 0}, {'amountCents': '41'}, {'date': '2026-01-01'},
                                  {'merchant': None}, {}])
def test_phone_spending_edit_rejects_bad_fields(client, data):
    entry = receipt(client).get_json()
    page = client.get('/api/mobile/sync', query_string={'collections': 'spending_logs'}).get_json()
    row = next(c for c in page['changes'] if c['id'] == entry['id'])
    body = {'id': str(ULID()), 'epoch': page['epoch'], 'collection': 'spending_logs',
            'recordId': entry['id'], 'baseRevision': row['revision'], 'action': 'update', 'data': data}
    assert client.post('/api/mobile/operations', json=body).status_code == 400
