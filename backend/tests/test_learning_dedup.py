"""Approval-time duplicate hint: answer-embedding cosine, hint-not-block."""
import struct

import pytest

from backend.ai import learning_generation
from backend.learning import dedup
from backend.routes import learning as learning_routes


def _vec(*floats) -> bytes:
    return struct.pack(f'{len(floats)}f', *floats)


@pytest.fixture
def fake_generate(monkeypatch):
    monkeypatch.setattr(
        learning_generation, 'generate_cards',
        lambda text, direction=None: [{'question': 'Q?', 'answer': 'A.'}],
    )


def _queue_card(client) -> str:
    return client.post('/api/learning/generate', json={'text': 'dump'}).json['ids'][0]


def test_cosine():
    assert dedup.cosine(_vec(1, 0), _vec(1, 0)) == pytest.approx(1.0)
    assert dedup.cosine(_vec(1, 0), _vec(0, 1)) == pytest.approx(0.0)
    assert dedup.cosine(_vec(1, 1), _vec(1, 0)) == pytest.approx(0.7071, abs=1e-3)
    # Missing blobs and dimension mismatches are incomparable, not errors.
    assert dedup.cosine(None, _vec(1, 0)) is None
    assert dedup.cosine(_vec(1, 0, 0), _vec(1, 0)) is None
    assert dedup.cosine(_vec(0, 0), _vec(1, 0)) is None


def test_duplicate_hint_and_force(client, fake_generate, monkeypatch):
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0))
    existing = client.post('/api/learning/cards',
                           json={'question': 'Existing?', 'answer': 'Same fact.'}).json['id']
    pending = _queue_card(client)

    r = client.post(f'/api/learning/queue/{pending}/approve', json={})
    assert r.status_code == 200
    assert r.json['status'] == 'duplicateHint'
    assert r.json['similar']['id'] == existing
    assert r.json['score'] >= dedup.DEDUP_THRESHOLD
    # The hint must not change state.
    assert client.get(f'/api/learning/cards/{pending}').json['state'] == 'pending'

    r = client.post(f'/api/learning/queue/{pending}/approve', json={'force': True})
    assert r.json['status'] == 'approved'


def test_dissimilar_answers_approve_directly(client, fake_generate, monkeypatch):
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0))
    client.post('/api/learning/cards', json={'question': 'E?', 'answer': 'Other.'})
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(0.0, 1.0))
    pending = _queue_card(client)
    r = client.post(f'/api/learning/queue/{pending}/approve', json={})
    assert r.json['status'] == 'approved'


def test_dimension_mismatch_skipped(client, fake_generate, monkeypatch):
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0, 0.0))
    client.post('/api/learning/cards', json={'question': 'E?', 'answer': 'Old-provider fact.'})
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0))
    pending = _queue_card(client)
    r = client.post(f'/api/learning/queue/{pending}/approve', json={})
    assert r.json['status'] == 'approved'


def test_unconfigured_embeddings_disable_hint(client, fake_generate):
    # Default test env has no embedding provider; embed_answer yields None.
    client.post('/api/learning/cards', json={'question': 'E?', 'answer': 'A.'})
    pending = _queue_card(client)
    r = client.post(f'/api/learning/queue/{pending}/approve', json={})
    assert r.json['status'] == 'approved'


def test_retired_cards_not_dedup_candidates(client, fake_generate, monkeypatch):
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0))
    existing = client.post('/api/learning/cards',
                           json={'question': 'E?', 'answer': 'Same.'}).json['id']
    from backend.db.connection import get_db
    get_db().execute("UPDATE learning_cards SET state='retired' WHERE id=?", (existing,))
    get_db().commit()

    pending = _queue_card(client)
    r = client.post(f'/api/learning/queue/{pending}/approve', json={})
    assert r.json['status'] == 'approved'
