import json
import time

import pytest


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setenv('NOTEBOOK_ROOT', str(tmp_path / 'notebook'))


def test_toggle_on_fresh_stamps_due_now(client):
    r = client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})
    assert r.status_code == 200

    state = client.get('/api/notebook/review/state', query_string={'path': 'a.md'}).json
    assert state['enabled'] is True
    assert state['due'] is not None
    assert state['fsrsState'] is None


def test_toggle_off_then_on_preserves_schedule(client):
    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})
    client.post('/api/notebook/review/rate', json={'path': 'a.md', 'rating': 3})
    after_rate = client.get('/api/notebook/review/state', query_string={'path': 'a.md'}).json
    assert after_rate['fsrsState'] is not None

    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': False})
    off_state = client.get('/api/notebook/review/state', query_string={'path': 'a.md'}).json
    assert off_state['enabled'] is False
    assert off_state['fsrsState'] == after_rate['fsrsState']

    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})
    on_again = client.get('/api/notebook/review/state', query_string={'path': 'a.md'}).json
    assert on_again['fsrsState'] == after_rate['fsrsState']
    assert on_again['due'] == after_rate['due']


def test_due_ordering_and_future_exclusion(client):
    from backend.db.connection import get_db

    for path in ('early.md', 'late.md', 'future.md'):
        client.post('/api/notebook/review/toggle', json={'path': path, 'enabled': True})

    now = int(time.time())
    db = get_db()
    db.execute("UPDATE notebook_review_state SET due=? WHERE path='early.md'", (now - 100,))
    db.execute("UPDATE notebook_review_state SET due=? WHERE path='late.md'", (now - 50,))
    db.execute("UPDATE notebook_review_state SET due=? WHERE path='future.md'", (now + 9999,))
    db.commit()

    due = client.get('/api/notebook/review/due').json
    assert [d['path'] for d in due] == ['early.md', 'late.md']


def test_rate_updates_schedule(client):
    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})
    r = client.post('/api/notebook/review/rate', json={'path': 'a.md', 'rating': 3})
    assert r.status_code == 200
    assert r.json['due'] is not None

    from backend.db.connection import get_db
    row = get_db().execute(
        "SELECT * FROM notebook_review_state WHERE path='a.md'"
    ).fetchone()
    assert row['fsrs_state'] is not None
    assert json.loads(row['fsrs_state'])['stability'] is not None


def test_rate_validates_rating_range(client):
    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})
    r = client.post('/api/notebook/review/rate', json={'path': 'a.md', 'rating': 5})
    assert r.status_code == 400


def test_rate_404s_on_unknown_path(client):
    r = client.post('/api/notebook/review/rate', json={'path': 'nope.md', 'rating': 3})
    assert r.status_code == 404


def test_rate_404s_on_disabled_path(client):
    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})
    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': False})
    r = client.post('/api/notebook/review/rate', json={'path': 'a.md', 'rating': 3})
    assert r.status_code == 404


def test_file_rename_updates_review_row_path(client):
    client.post('/api/notebook/files/write', json={'path': 'a.md', 'content': 'x'})
    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})

    r = client.post('/api/notebook/files/rename', json={'from': 'a.md', 'to': 'b.md'})
    assert r.status_code == 200

    old_state = client.get('/api/notebook/review/state', query_string={'path': 'a.md'}).json
    assert old_state['enabled'] is False  # no row left under the old path
    new_state = client.get('/api/notebook/review/state', query_string={'path': 'b.md'}).json
    assert new_state['enabled'] is True


def test_directory_rename_rewrites_descendant_review_rows(client):
    client.post('/api/notebook/files/write', json={'path': 'dir/child.md', 'content': 'x'})
    client.post('/api/notebook/review/toggle', json={'path': 'dir/child.md', 'enabled': True})

    r = client.post('/api/notebook/files/rename', json={'from': 'dir', 'to': 'dir2'})
    assert r.status_code == 200

    old_state = client.get('/api/notebook/review/state', query_string={'path': 'dir/child.md'}).json
    assert old_state['enabled'] is False
    new_state = client.get('/api/notebook/review/state', query_string={'path': 'dir2/child.md'}).json
    assert new_state['enabled'] is True


def test_file_delete_removes_review_row(client):
    client.post('/api/notebook/files/write', json={'path': 'a.md', 'content': 'x'})
    client.post('/api/notebook/review/toggle', json={'path': 'a.md', 'enabled': True})

    r = client.delete('/api/notebook/files', query_string={'path': 'a.md'})
    assert r.status_code == 200

    state = client.get('/api/notebook/review/state', query_string={'path': 'a.md'}).json
    assert state['enabled'] is False


def test_directory_delete_removes_descendant_review_rows(client):
    client.post('/api/notebook/files/write', json={'path': 'dir/child.md', 'content': 'x'})
    client.post('/api/notebook/review/toggle', json={'path': 'dir/child.md', 'enabled': True})

    r = client.delete('/api/notebook/files', query_string={'path': 'dir'})
    assert r.status_code == 200

    state = client.get('/api/notebook/review/state', query_string={'path': 'dir/child.md'}).json
    assert state['enabled'] is False
