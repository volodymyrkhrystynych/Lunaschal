"""Idempotent replay for offline-queued writes.

When the client is offline it generates a ULID up front and queues the write;
on reconnect the queued mutation replays. These tests cover the server-side
guarantees that make a replay safe: client-supplied ids on journal/todo creates
(INSERT OR IGNORE) and reviewId de-duplication on learning reviews.
"""
import pytest

from backend.db.connection import get_db
from backend.routes import journal as journal_route


@pytest.fixture(autouse=True)
def _no_journal_bg(monkeypatch):
    # Journal create fans out to daemon threads that touch the shared sqlite
    # connection; stub them so tests stay single-threaded (as other route
    # tests do). We're testing the insert idempotency, not the polish pipeline.
    for name in (
        '_sync_embeddings_bg',
        '_generate_metadata_bg',
        '_polish_bg',
        '_notify_subscribers',
    ):
        monkeypatch.setattr(journal_route, name, lambda *a, **k: None)


def test_journal_create_accepts_client_id(client):
    r = client.post('/api/journal', json={'id': 'CLIENT01', 'content': 'hello'})
    assert r.status_code == 201
    assert r.get_json()['id'] == 'CLIENT01'
    assert [e['id'] for e in client.get('/api/journal').get_json()] == ['CLIENT01']


def test_journal_create_replay_is_idempotent(client):
    client.post('/api/journal', json={'id': 'CLIENT01', 'content': 'hello'})
    # Replaying the same queued create must be a no-op, not a duplicate row.
    r = client.post('/api/journal', json={'id': 'CLIENT01', 'content': 'hello'})
    assert r.status_code == 201
    assert [e['id'] for e in client.get('/api/journal').get_json()] == ['CLIENT01']


def test_journal_create_replay_does_not_overwrite(client):
    # A late replay with drifted content must not clobber the saved entry.
    client.post('/api/journal', json={'id': 'CLIENT02', 'content': 'original'})
    client.post('/api/journal', json={'id': 'CLIENT02', 'content': 'changed'})
    assert client.get('/api/journal/CLIENT02').get_json()['content'] == 'original'


def test_journal_create_without_id_still_generates_one(client):
    r = client.post('/api/journal', json={'content': 'auto'})
    assert r.status_code == 201
    assert r.get_json()['id']


def test_todo_create_accepts_client_id_and_is_idempotent(client):
    r = client.post('/api/tasks/todos', json={'id': 'TODO01', 'title': 'buy milk'})
    assert r.status_code == 201
    assert r.get_json()['id'] == 'TODO01'

    client.post('/api/tasks/todos', json={'id': 'TODO01', 'title': 'buy milk'})
    todos = client.get('/api/tasks/todos').get_json()
    assert [t['id'] for t in todos] == ['TODO01']


def _make_card(client):
    return client.post(
        '/api/learning/cards', json={'question': 'Q?', 'answer': 'A.'}
    ).get_json()['id']


def test_learning_review_dedupes_on_review_id(client):
    cid = _make_card(client)
    r1 = client.post(
        f'/api/learning/cards/{cid}/review', json={'rating': 3, 'reviewId': 'REV01'}
    )
    assert r1.status_code == 200
    due1 = r1.get_json()['due']
    state1 = get_db().execute(
        'SELECT fsrs_state FROM learning_cards WHERE id=?', (cid,)
    ).fetchone()['fsrs_state']

    # Replaying the same review must not advance FSRS a second time.
    r2 = client.post(
        f'/api/learning/cards/{cid}/review', json={'rating': 3, 'reviewId': 'REV01'}
    )
    assert r2.status_code == 200
    assert r2.get_json()['due'] == due1
    state2 = get_db().execute(
        'SELECT fsrs_state FROM learning_cards WHERE id=?', (cid,)
    ).fetchone()['fsrs_state']
    assert state2 == state1

    count = get_db().execute(
        'SELECT COUNT(*) FROM learning_reviews WHERE card_id=?', (cid,)
    ).fetchone()[0]
    assert count == 1


def test_learning_reviews_with_distinct_ids_both_apply(client):
    cid = _make_card(client)
    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 3, 'reviewId': 'REVa'})
    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 3, 'reviewId': 'REVb'})
    count = get_db().execute(
        'SELECT COUNT(*) FROM learning_reviews WHERE card_id=?', (cid,)
    ).fetchone()[0]
    assert count == 2
