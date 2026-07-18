"""Grading pipeline: claim caching, embedding gate, rating mapping, voice path."""
import json
import struct

import pytest

from backend.ai import learning_generation, learning_grading
from backend.routes import learning as learning_routes


def _vec(*floats) -> bytes:
    return struct.pack(f'{len(floats)}f', *floats)


def _make_card(client, question='What is X?', answer='X is a thing.'):
    r = client.post('/api/learning/cards', json={'question': question, 'answer': answer})
    assert r.status_code == 201
    return r.json['id']


@pytest.fixture
def stub_llm(monkeypatch):
    """Stub decompose/coverage with call counters."""
    calls = {'decompose': 0, 'coverage': 0, 'normalize': 0}

    def _decompose(question, answer):
        calls['decompose'] += 1
        return [{'text': 'X is a thing', 'essential': True}]

    def _coverage(claims, user_answer):
        calls['coverage'] += 1
        return {
            'claims': [{**c, 'covered': True, 'note': ''} for c in claims],
            'summary': 'Got it.',
        }

    def _normalize(text):
        calls['normalize'] += 1
        return 'normalized ' + text

    monkeypatch.setattr(learning_grading, 'decompose_claims', _decompose)
    monkeypatch.setattr(learning_grading, 'check_coverage', _coverage)
    monkeypatch.setattr(learning_generation, 'normalize_transcript', _normalize)
    return calls


def test_grade_returns_coverage_and_suggestion(client, stub_llm):
    cid = _make_card(client)
    r = client.post(f'/api/learning/cards/{cid}/grade', json={'answer': 'X is a thing'})
    assert r.status_code == 200
    assert r.json['suggestedRating'] == 4
    assert r.json['coverage']['claims'][0]['covered'] is True
    assert r.json['normalizedAnswer'] == 'X is a thing'
    assert stub_llm['normalize'] == 0


def test_claims_cached_after_first_grade(client, stub_llm):
    cid = _make_card(client)
    client.post(f'/api/learning/cards/{cid}/grade', json={'answer': 'a'})
    client.post(f'/api/learning/cards/{cid}/grade', json={'answer': 'b'})
    assert stub_llm['decompose'] == 1
    assert stub_llm['coverage'] == 2

    from backend.db.connection import get_db
    row = get_db().execute('SELECT claims FROM learning_cards WHERE id=?', (cid,)).fetchone()
    assert json.loads(row['claims']) == [{'text': 'X is a thing', 'essential': True}]


def test_voice_answers_normalized_before_grading(client, stub_llm):
    cid = _make_card(client)
    r = client.post(f'/api/learning/cards/{cid}/grade',
                    json={'answer': 'um so X is a thing', 'answerMode': 'voice'})
    assert stub_llm['normalize'] == 1
    assert r.json['normalizedAnswer'] == 'normalized um so X is a thing'


def test_embedding_gate_short_circuits_llm(client, stub_llm, monkeypatch):
    # Stored answer embeds to (1,0,0); user answer to (0,1,0) → cosine 0 < gate.
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0, 0.0))
    cid = _make_card(client)
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(0.0, 1.0, 0.0))

    r = client.post(f'/api/learning/cards/{cid}/grade', json={'answer': 'total nonsense'})
    assert r.status_code == 200
    assert r.json['suggestedRating'] == 1
    assert r.json['coverage']['gated'] is True
    assert stub_llm['coverage'] == 0 and stub_llm['decompose'] == 0


def test_similar_embedding_still_runs_llm(client, stub_llm, monkeypatch):
    # High similarity must NOT skip the claim check (negation blindness).
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0, 0.0))
    cid = _make_card(client)
    r = client.post(f'/api/learning/cards/{cid}/grade', json={'answer': 'X is not a thing'})
    assert stub_llm['coverage'] == 1
    assert 'gated' not in r.json['coverage']


def test_grade_unconfigured_embeddings_falls_through(client, stub_llm):
    # embed_answer returns None without a provider; gate silently disabled.
    cid = _make_card(client)
    r = client.post(f'/api/learning/cards/{cid}/grade', json={'answer': 'whatever'})
    assert r.status_code == 200
    assert stub_llm['coverage'] == 1


def test_grade_validation(client, stub_llm):
    cid = _make_card(client)
    assert client.post(f'/api/learning/cards/{cid}/grade', json={}).status_code == 400
    assert client.post('/api/learning/cards/nope/grade', json={'answer': 'x'}).status_code == 404


@pytest.mark.parametrize('claims,expected', [
    # All essential covered, no nuance missed → Easy.
    ([{'essential': True, 'covered': True}], 4),
    # Essentials covered but nuance missed → Good.
    ([{'essential': True, 'covered': True}, {'essential': False, 'covered': False}], 3),
    # One of two essentials missed (>= half covered) → Hard.
    ([{'essential': True, 'covered': True}, {'essential': True, 'covered': False}], 2),
    # Most essentials missed → Again.
    ([{'essential': True, 'covered': False}, {'essential': True, 'covered': False},
      {'essential': True, 'covered': True}], 1),
    # No essential-marked claims: all claims count as essential.
    ([{'essential': False, 'covered': True}, {'essential': False, 'covered': True}], 4),
])
def test_suggest_rating_mapping(claims, expected):
    coverage = {'claims': [{'text': 't', 'note': '', **c} for c in claims]}
    assert learning_grading.suggest_rating(coverage) == expected


def test_suggest_rating_gated_or_empty_is_again():
    assert learning_grading.suggest_rating(learning_grading.gated_coverage()) == 1
    assert learning_grading.suggest_rating({'claims': []}) == 1
