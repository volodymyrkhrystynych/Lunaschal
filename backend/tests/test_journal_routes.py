"""Journal tag columns must come back normalized.

src/lib/tags.ts deliberately does no deduping client-side ("normalization is
owned by the backend, which re-normalizes every create/update payload"), and the
Journal view keys its tag pills by the tag string — so a duplicate that reaches
the DB becomes a React duplicate-key warning and a doubled pill.
"""
import json

import pytest

from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _no_background_work(monkeypatch):
    """Creating an entry normally kicks off embedding sync, polish and metadata
    threads. They outlive the per-test DB (the fixture closes the connection at
    teardown, which the threads then use), and none of them are under test here.
    """
    for name in ('_sync_embeddings_bg', '_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)


def _tags(client, entry_id):
    row = client.get(f'/api/journal/{entry_id}').get_json()
    return json.loads(row['tags']) if row['tags'] else None


def test_create_normalizes_tags(client):
    r = client.post('/api/journal', json={
        'content': 'Read a lot today.',
        'title': 'Reading',
        'tags': ['Reading', 'reading', ' mood ', ''],
    })
    assert r.status_code == 201
    assert _tags(client, r.get_json()['id']) == ['reading', 'mood']


def test_patch_normalizes_tags(client):
    created = client.post('/api/journal', json={
        'content': 'Something.', 'title': 'T', 'tags': ['work'],
    }).get_json()

    client.patch(f"/api/journal/{created['id']}", json={'tags': ['Work', 'work', 'coding']})
    assert _tags(client, created['id']) == ['work', 'coding']


def test_empty_tags_normalize_to_null_not_empty_array(client):
    created = client.post('/api/journal', json={
        'content': 'Something.', 'title': 'T', 'tags': ['work'],
    }).get_json()

    client.patch(f"/api/journal/{created['id']}", json={'tags': []})
    row = client.get(f"/api/journal/{created['id']}").get_json()
    assert row['tags'] is None
