"""Daily spending: integer cents, offline replay, original dates, and deletion."""
from datetime import datetime, timezone

import pytest
from ulid import ULID

from backend.db.connection import get_db, init_db


def test_spending_keeps_cents_categories_and_daily_totals(client, monkeypatch):
    monkeypatch.setattr('backend.routes.lifestyle._today', lambda: '2026-10-08')
    first = client.post('/api/lifestyle/spending', json={'category': " McDonald's ", 'amountCents': 1500})
    assert first.status_code == 201
    assert first.json['category'] == "McDonald's"
    assert first.json['date'] == '2026-10-08'
    client.post('/api/lifestyle/spending', json={'category': 'Groceries', 'amountCents': 3001})
    client.post('/api/lifestyle/spending', json={'category': 'Yesterday', 'amountCents': 999, 'date': '2026-10-07'})
    day = client.get('/api/lifestyle/spending').json
    assert day['totalCents'] == 4501
    assert len(day['entries']) == 2
    assert all(type(e['amountCents']) is int for e in day['entries'])
    assert client.get('/api/lifestyle/spending?date=2026-10-06').json['totalCents'] == 0


def test_spending_replay_keeps_original_amount_day_and_capture_time(client):
    logged = datetime(2026, 10, 4, 1, 30, tzinfo=timezone.utc)
    body = {'id': str(ULID()), 'category': 'Groceries', 'amountCents': 3010,
            'date': '2026-10-03', 'capturedAt': logged.isoformat()}
    first = client.post('/api/lifestyle/spending', json=body)
    assert first.status_code == 201
    replay = client.post('/api/lifestyle/spending', json={**body, 'amountCents': 9999, 'date': '2026-10-08'})
    assert replay.json == first.json
    row = get_db().execute('SELECT * FROM spending_logs WHERE id=?', (body['id'],)).fetchone()
    assert row['created_at'] == int(logged.timestamp())
    assert client.get('/api/lifestyle/spending?date=2026-10-03').json['totalCents'] == 3010
    assert client.get('/api/lifestyle/spending?date=2026-10-08').json['entries'] == []


@pytest.mark.parametrize('value', [None, True, False, 0, -1, 1.2, 1500.0, '1500', 'NaN', [], {}, 100000001])
def test_spending_refuses_invalid_amounts(client, value):
    response = client.post('/api/lifestyle/spending', json={'category': 'Groceries', 'amountCents': value})
    assert response.status_code == 400
    assert get_db().execute('SELECT COUNT(*) FROM spending_logs').fetchone()[0] == 0


@pytest.mark.parametrize('value', [None, '', '  ', 15, [], {}, 'x' * 201])
def test_spending_requires_a_short_text_category(client, value):
    assert client.post('/api/lifestyle/spending', json={'category': value, 'amountCents': 15}).status_code == 400


@pytest.mark.parametrize('extra', [{'date': '2026-02-30'}, {'date': 'yesterday'}, {'id': '../bad'},
                                  {'id': ''}, {'id': 12}, {'capturedAt': 'noon'}])
def test_spending_rejects_invalid_identity_and_dates(client, extra):
    assert client.post('/api/lifestyle/spending', json={'category': 'Food', 'amountCents': 15, **extra}).status_code == 400


def test_spending_future_clock_is_clamped_and_delete_updates_total(client, monkeypatch):
    monkeypatch.setattr('backend.routes.lifestyle.time.time', lambda: 1_790_000_000)
    response = client.post('/api/lifestyle/spending', json={
        'category': 'Groceries', 'amountCents': 3000, 'capturedAt': '2999-01-01T00:00:00+00:00',
    })
    entry = response.json
    assert get_db().execute('SELECT created_at FROM spending_logs WHERE id=?', (entry['id'],)).fetchone()[0] == 1_790_000_000
    assert client.delete(f'/api/lifestyle/spending/{entry["id"]}').status_code == 200
    # The native queue treats an already-absent row as a completed deletion.
    assert client.delete(f'/api/lifestyle/spending/{entry["id"]}').status_code == 404
    assert client.get(f'/api/lifestyle/spending?date={entry["date"]}').json['totalCents'] == 0


def test_spending_schema_is_added_to_existing_databases_idempotently(client):
    db = get_db()
    db.execute('DROP TABLE spending_logs')
    db.commit()
    init_db()
    entry = client.post('/api/lifestyle/spending', json={'category': 'Groceries', 'amountCents': 3000}).json
    init_db()
    assert get_db().execute('SELECT amount_cents FROM spending_logs WHERE id=?', (entry['id'],)).fetchone()[0] == 3000
