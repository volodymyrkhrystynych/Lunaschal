"""The food log on the phone: replicated meals, their media, and offline edits."""
import io
import json

import pytest
from ulid import ULID

from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def food_root(monkeypatch, tmp_path):
    root = tmp_path / 'food'
    monkeypatch.setenv('FOOD_ROOT', str(root))
    return root


def meal(client, **fields):
    id = str(ULID())
    data = {'id': id, 'text': '', 'dish': 'Ramen', 'notes': 'rich broth', **fields}
    data['media'] = [(io.BytesIO(b'\xff\xd8\xffFAKEJPEG'), 'bowl.jpg')]
    assert client.post('/api/food', data=data, content_type='multipart/form-data').status_code == 201
    return id


def page(client):
    return client.get('/api/mobile/sync', query_string={'collections': 'food_entries,food_media'}).json


def food_edit(client, id, data, **overrides):
    current = page(client)
    revision = next(row['revision'] for row in current['changes']
                    if row['collection'] == 'food_entries' and row['id'] == id)
    return {'id': str(ULID()), 'epoch': current['epoch'], 'collection': 'food_entries', 'recordId': id,
            'baseRevision': revision, 'action': 'update', 'data': data, **overrides}


def test_meals_and_their_media_replicate_without_paths(client, food_root):
    id = meal(client)
    changes = page(client)['changes']
    entry = next(row['data'] for row in changes if row['collection'] == 'food_entries')
    media = next(row['data'] for row in changes if row['collection'] == 'food_media')
    assert entry['id'] == id and entry['dish'] == 'Ramen' and entry['notes'] == 'rich broth'
    assert 'generatedNotes' not in entry and 'weatherCheckedAt' not in entry
    assert media['entryId'] == id and media['kind'] == 'image'
    assert 'path' not in media and str(food_root) not in json.dumps(changes)
    capabilities = client.get('/api/mobile/capabilities').json
    assert {'food_entries', 'food_media'} <= set(capabilities['collections'])
    assert 'food_media' in capabilities['mediaCollections']
    assert 'food_entries' in capabilities['editableCollections']


def test_food_media_manifest_serves_the_photo(client, food_root):
    meal(client)
    item = client.get('/api/mobile/media', query_string={'collection': 'food_media'}).json['items'][0]
    assert item['available'] is True and str(food_root) not in str(item)
    assert client.get(item['url']).data == b'\xff\xd8\xffFAKEJPEG'


def test_offline_food_edit_applies_once_and_hand_notes_win(client):
    id = meal(client)
    get_db().execute("UPDATE food_entries SET generated_notes='model said' WHERE id=?", (id,))
    get_db().commit()
    body = food_edit(client, id, {'dish': ' Shoyu ramen ', 'notes': 'edited on the phone', 'place': ''})
    first = client.post('/api/mobile/operations', json=body)
    assert first.status_code == 200, first.json
    assert client.post('/api/mobile/operations', json=body).json == first.json
    row = get_db().execute('SELECT dish, notes, place, generated_notes FROM food_entries WHERE id=?', (id,)).fetchone()
    assert (row['dish'], row['notes'], row['place'], row['generated_notes']) == \
        ('Shoyu ramen', 'edited on the phone', None, None)
    assert first.json['change']['data']['dish'] == 'Shoyu ramen'


def test_stale_food_edit_conflicts_without_overwriting(client):
    id = meal(client)
    body = food_edit(client, id, {'dish': 'from the phone'})
    assert client.patch(f'/api/food/{id}', json={'dish': 'from the desktop'}).status_code == 200
    response = client.post('/api/mobile/operations', json=body)
    assert response.status_code == 409 and response.json['conflict'] is True
    assert response.json['current']['data']['dish'] == 'from the desktop'
    assert client.get(f'/api/food/{id}').json['dish'] == 'from the desktop'


@pytest.mark.parametrize('overrides', [
    {'data': {'raw_content': 'clobber'}}, {'data': {'rating': 5}}, {'data': {'dish': None}},
    {'data': {}}, {'action': 'delete', 'data': {}},
])
def test_food_edit_rejects_other_fields_and_actions(client, overrides):
    id = meal(client)
    assert client.post('/api/mobile/operations', json={**food_edit(client, id, {'dish': 'x'}), **overrides}).status_code == 400
    assert client.get(f'/api/food/{id}').json['dish'] == 'Ramen'


def test_added_media_replays_by_client_id(client):
    id = meal(client)
    photo, clip = str(ULID()), str(ULID())

    def add():
        return client.post(f'/api/food/{id}/media', content_type='multipart/form-data', data={
            'mediaIds': json.dumps([photo]),
            'media': [(io.BytesIO(b'\xff\xd8\xffSECOND'), 'side.jpg')],
        })

    first = add()
    assert first.status_code == 201
    assert first.json['id'] == id and [m['id'] for m in first.json['media']] == [photo]
    replay = add()
    assert [m['id'] for m in replay.json['media']] == [photo]
    rows = get_db().execute('SELECT id, position FROM food_media WHERE entry_id=? ORDER BY position', (id,)).fetchall()
    assert [r['id'] for r in rows][1:] == [photo] and [r['position'] for r in rows] == [0, 1]
    assert clip not in [r['id'] for r in rows]
