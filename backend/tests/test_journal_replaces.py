"""An iPad newspaper filed again later in the day replaces the entry it was
filed as before: `POST /api/journal` with `replaces` deletes those entries."""
import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.routes import journal


@pytest.fixture(autouse=True)
def no_enrichment(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal, name, lambda *a, **k: None)


def create(client, text, **extra):
    id = str(ULID())
    response = client.post('/api/journal', json={'id': id, 'content': text, **extra})
    return id, response


def ids():
    return {row[0] for row in get_db().execute('SELECT id FROM journal_entries')}


def test_a_replacement_deletes_what_it_replaces(client):
    morning, _ = create(client, 'Toronto Star, 2026-10-08')
    other, _ = create(client, 'Unrelated entry')
    evening, response = create(client, 'Toronto Star, 2026-10-08', replaces=morning)
    assert response.status_code == 201
    assert ids() == {other, evening}


def test_several_and_unknown_ids_are_fine(client):
    first, _ = create(client, 'first filing')
    second, _ = create(client, 'second filing')
    never_arrived = str(ULID())
    final, response = create(client, 'final filing', replaces=f'{first},{never_arrived},{second}')
    assert response.status_code == 201
    assert ids() == {final}


def test_a_replay_is_idempotent_and_keeps_the_new_entry(client):
    old, _ = create(client, 'old')
    new = str(ULID())
    body = {'id': new, 'content': 'new', 'replaces': old}
    assert client.post('/api/journal', json=body).status_code == 201
    assert client.post('/api/journal', json=body).status_code == 201
    assert ids() == {new}


def test_an_entry_never_deletes_itself(client):
    id = str(ULID())
    response = client.post('/api/journal', json={'id': id, 'content': 'self', 'replaces': id})
    assert response.status_code == 201
    assert ids() == {id}


@pytest.mark.parametrize('value', ['not-a-ulid', f'{ULID()},../etc', ',', 42])
def test_malformed_replaces_is_refused_without_saving_or_deleting(client, value):
    kept, _ = create(client, 'kept')
    new, response = create(client, 'new', replaces=value)
    assert response.status_code == 400
    assert ids() == {kept}


def test_the_replaced_entry_reaches_the_ipad_as_a_delete(client):
    old, _ = create(client, 'old')
    start = client.get('/api/mobile/sync', query_string={'collections': 'journal_entries'}).json
    new, _ = create(client, 'new', replaces=old)
    delta = client.get('/api/mobile/sync', query_string={'cursor': start['cursor']}).json
    changes = {change['id']: change for change in delta['changes']}
    assert changes[old]['deleted'] is True
    assert changes[new]['data']['content'] == 'new'
