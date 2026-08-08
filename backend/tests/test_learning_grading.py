"""Grading pipeline: claim caching, embedding gate, rating mapping, voice path.

Grading runs on the background worker now, so these drive it through
POST /attempts with run_bg made synchronous, then read the graded attempt back.
"""
import json
import struct

import pytest

from backend.ai import background, learning_generation, learning_grading
from backend.learning import dedup
from backend.routes import learning as learning_routes


def _vec(*floats) -> bytes:
    return struct.pack(f'{len(floats)}f', *floats)


def _make_card(client, question='What is X?', answer='X is a thing.'):
    r = client.post('/api/learning/cards', json={'question': question, 'answer': answer})
    assert r.status_code == 201
    return r.json['id']


def _grade(client, card_id, answer, **extra):
    """Answer a card and return its graded attempt."""
    r = client.post('/api/learning/attempts',
                    json={'cardId': card_id, 'mode': 'answered', 'answer': answer, **extra})
    assert r.status_code == 200
    return next(a for a in client.get('/api/learning/attempts').json
                if a['cardId'] == card_id)


@pytest.fixture(autouse=True)
def inline_bg(monkeypatch):
    """Run background grading inline so tests can assert on the result."""
    monkeypatch.setattr(background, 'run_bg', lambda fn: fn())


@pytest.fixture
def stub_llm(monkeypatch):
    """Stub decompose/coverage with call counters."""
    calls = {'decompose': 0, 'coverage': 0, 'normalize': 0, 'speech': []}

    def _decompose(question, answer):
        calls['decompose'] += 1
        return [{'text': 'X is a thing', 'essential': True}]

    def _coverage(claims, user_answer, speech=False):
        calls['coverage'] += 1
        calls['speech'].append(speech)
        out = {
            'claims': [{**c, 'covered': True, 'note': ''} for c in claims],
            'summary': 'Got it.',
        }
        if speech:
            out['speechSummary'] = 'You got it.'
        return out

    def _normalize(text):
        calls['normalize'] += 1
        return 'normalized ' + text

    monkeypatch.setattr(learning_grading, 'decompose_claims', _decompose)
    monkeypatch.setattr(learning_grading, 'check_coverage', _coverage)
    monkeypatch.setattr(learning_generation, 'normalize_transcript', _normalize)
    return calls


def test_grade_returns_coverage_and_suggestion(client, stub_llm):
    cid = _make_card(client)
    attempt = _grade(client, cid, 'X is a thing')
    assert attempt['gradeStatus'] == 'done'
    assert attempt['suggestedRating'] == 4
    assert attempt['coverage']['claims'][0]['covered'] is True
    assert attempt['normalizedAnswer'] == 'X is a thing'
    assert stub_llm['normalize'] == 0


def test_claims_cached_after_first_grade(client, stub_llm):
    cid = _make_card(client)
    _grade(client, cid, 'a')
    _grade(client, cid, 'b')
    assert stub_llm['decompose'] == 1
    assert stub_llm['coverage'] == 2

    from backend.db.connection import get_db
    row = get_db().execute('SELECT claims FROM learning_cards WHERE id=?', (cid,)).fetchone()
    assert json.loads(row['claims']) == [{'text': 'X is a thing', 'essential': True}]


def test_voice_answers_normalized_before_grading(client, stub_llm):
    cid = _make_card(client)
    attempt = _grade(client, cid, 'um so X is a thing', answerMode='voice')
    assert stub_llm['normalize'] == 1
    assert attempt['normalizedAnswer'] == 'normalized um so X is a thing'


def test_embedding_gate_short_circuits_llm(client, stub_llm, monkeypatch):
    # Stored answer embeds to (1,0,0); user answer to (0,1,0) → cosine 0 < gate.
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0, 0.0))
    cid = _make_card(client)
    monkeypatch.setattr(dedup, 'embed_answer', lambda text: _vec(0.0, 1.0, 0.0))

    attempt = _grade(client, cid, 'total nonsense')
    assert attempt['suggestedRating'] == 1
    assert attempt['coverage']['gated'] is True
    assert stub_llm['coverage'] == 0 and stub_llm['decompose'] == 0


def test_similar_embedding_still_runs_llm(client, stub_llm, monkeypatch):
    # High similarity must NOT skip the claim check (negation blindness).
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0, 0.0))
    monkeypatch.setattr(dedup, 'embed_answer', lambda text: _vec(1.0, 0.0, 0.0))
    cid = _make_card(client)
    attempt = _grade(client, cid, 'X is not a thing')
    assert stub_llm['coverage'] == 1
    assert 'gated' not in attempt['coverage']


def test_grade_unconfigured_embeddings_falls_through(client, stub_llm):
    # embed_answer returns None without a provider; gate silently disabled.
    cid = _make_card(client)
    attempt = _grade(client, cid, 'whatever')
    assert attempt['gradeStatus'] == 'done'
    assert stub_llm['coverage'] == 1


def test_grade_failure_marks_attempt_error(client, stub_llm, monkeypatch):
    def _boom(claims, user_answer, speech=False):
        raise RuntimeError('model exploded')

    monkeypatch.setattr(learning_grading, 'check_coverage', _boom)
    cid = _make_card(client)
    attempt = _grade(client, cid, 'whatever')
    assert attempt['gradeStatus'] == 'error'
    assert attempt['coverage'] is None


def test_grade_after_rating_is_a_noop(client, stub_llm, monkeypatch):
    """Rating a card deletes its attempt; a grade still in flight must not
    resurrect the row."""
    from backend.db.connection import get_db
    from backend.learning.attempts import grade_attempt

    cid = _make_card(client)
    monkeypatch.setattr(background, 'run_bg', lambda fn: None)  # defer the grade
    client.post('/api/learning/attempts',
                json={'cardId': cid, 'mode': 'answered', 'answer': 'x'})
    attempt_id = get_db().execute(
        'SELECT id FROM learning_attempts WHERE card_id=?', (cid,)).fetchone()['id']
    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 3})

    grade_attempt(attempt_id)  # the deferred worker finally runs
    assert get_db().execute('SELECT COUNT(*) c FROM learning_attempts').fetchone()['c'] == 0


def test_attempt_validation(client, stub_llm):
    cid = _make_card(client)
    assert client.post('/api/learning/attempts',
                       json={'cardId': cid, 'mode': 'answered'}).status_code == 400
    assert client.post('/api/learning/attempts',
                       json={'cardId': cid, 'mode': 'nonsense'}).status_code == 400
    assert client.post('/api/learning/attempts',
                       json={'cardId': 'nope', 'mode': 'skipped'}).status_code == 404


def test_speech_summary_omitted_by_default(client, stub_llm):
    cid = _make_card(client)
    attempt = _grade(client, cid, 'X is a thing')
    assert stub_llm['speech'] == [False]
    assert 'speechSummary' not in attempt['coverage']


def test_speech_summary_included_when_requested(client, stub_llm):
    cid = _make_card(client)
    attempt = _grade(client, cid, 'X is a thing', speechMode=True)
    assert stub_llm['speech'] == [True]
    assert attempt['coverage']['speechSummary'] == 'You got it.'


def test_reanswering_updates_speech_requested(client, stub_llm):
    """Re-answering a card re-stamps the toggle's current state — the point
    of storing it per-attempt rather than per-session."""
    cid = _make_card(client)
    _grade(client, cid, 'first try')
    assert stub_llm['speech'][-1] is False
    attempt = _grade(client, cid, 'second try', speechMode=True)
    assert stub_llm['speech'][-1] is True
    assert 'speechSummary' in attempt['coverage']


def test_gated_coverage_speech_summary(client, monkeypatch):
    # Force the embedding gate to fire so gated_coverage() runs for real.
    monkeypatch.setattr(learning_routes, 'embed_answer', lambda text: _vec(1.0, 0.0, 0.0))
    from backend.learning import dedup
    monkeypatch.setattr(dedup, 'embed_answer', lambda text: _vec(0.0, 1.0, 0.0))

    cid = _make_card(client)
    attempt = _grade(client, cid, 'total nonsense', speechMode=True)
    assert attempt['coverage']['gated'] is True
    assert attempt['coverage']['speechSummary']


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
