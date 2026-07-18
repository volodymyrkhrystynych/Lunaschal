"""Card versioning: retire/supersede, revision log, semantic-gated FSRS reset."""
import json

import pytest

from backend.ai import learning_verification


def _make_card(client, answer='The answer is 4.'):
    return client.post('/api/learning/cards',
                       json={'question': 'Q?', 'answer': answer}).json['id']


def _mature(client, cid):
    """Give the card real FSRS state and a future due date."""
    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 4})
    from backend.db.connection import get_db
    row = get_db().execute('SELECT fsrs_state, due FROM learning_cards WHERE id=?', (cid,)).fetchone()
    return row['fsrs_state'], row['due']


def test_semantic_revision_retires_and_resets(client, monkeypatch):
    monkeypatch.setattr(learning_verification, 'chat_json',
                        lambda *a, **k: {'semantic': True})
    cid = _make_card(client)
    _mature(client, cid)

    r = client.post(f'/api/learning/cards/{cid}/revise',
                    json={'answer': 'The answer is 8.',
                          'triggerType': 'manual_edit', 'note': 'was wrong'})
    assert r.status_code == 200
    assert r.json['isSemantic'] is True
    new_id = r.json['newCardId']

    from backend.db.connection import get_db
    old = get_db().execute('SELECT * FROM learning_cards WHERE id=?', (cid,)).fetchone()
    new = get_db().execute('SELECT * FROM learning_cards WHERE id=?', (new_id,)).fetchone()
    assert old['state'] == 'retired'
    assert new['state'] == 'active'
    assert new['revised_from'] == cid
    # Semantic → schedule reset: fresh FSRS, due immediately.
    assert new['fsrs_state'] is None
    assert new['due'] <= int(__import__('time').time())
    # History survives on the retired card.
    assert get_db().execute(
        'SELECT COUNT(*) FROM learning_reviews WHERE card_id=?', (cid,)
    ).fetchone()[0] == 1

    # Retired card leaves due/browse; new one is due.
    assert {c['id'] for c in client.get('/api/learning/due').json} == {new_id}
    assert {c['id'] for c in client.get('/api/learning/cards').json} == {new_id}


def test_cosmetic_revision_keeps_schedule(client, monkeypatch):
    monkeypatch.setattr(learning_verification, 'chat_json',
                        lambda *a, **k: {'semantic': False})
    cid = _make_card(client)
    state, due = _mature(client, cid)

    r = client.post(f'/api/learning/cards/{cid}/revise',
                    json={'answer': 'The answer is four — i.e. 4.'})
    assert r.json['isSemantic'] is False
    from backend.db.connection import get_db
    new = get_db().execute('SELECT * FROM learning_cards WHERE id=?',
                           (r.json['newCardId'],)).fetchone()
    assert new['fsrs_state'] == state
    assert new['due'] == due


def test_identical_answer_skips_llm(client, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError('LLM must not be called for normalized-equal answers')
    monkeypatch.setattr(learning_verification, 'chat_json', _boom)
    cid = _make_card(client, answer='Answer, with punctuation!')
    r = client.post(f'/api/learning/cards/{cid}/revise',
                    json={'answer': 'answer with   punctuation'})
    assert r.json['isSemantic'] is False


def test_llm_failure_defaults_to_semantic(client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError('provider down')
    monkeypatch.setattr(learning_verification, 'chat_json', _boom)
    cid = _make_card(client)
    r = client.post(f'/api/learning/cards/{cid}/revise', json={'answer': 'Different.'})
    assert r.json['isSemantic'] is True


def test_revision_log_and_chain(client, monkeypatch):
    monkeypatch.setattr(learning_verification, 'chat_json',
                        lambda *a, **k: {'semantic': True})
    cid = _make_card(client, answer='v1')
    r1 = client.post(f'/api/learning/cards/{cid}/revise',
                     json={'answer': 'v2', 'triggerType': 'web_verification',
                           'sources': [{'title': 'Docs', 'source': 'ctx7', 'quote': 'q'}]})
    mid = r1.json['newCardId']
    r2 = client.post(f'/api/learning/cards/{mid}/revise', json={'answer': 'v3'})
    newest = r2.json['newCardId']

    revisions = client.get(f'/api/learning/cards/{newest}/revisions').json
    assert len(revisions) == 2
    assert revisions[0]['oldAnswer'] == 'v2' and revisions[0]['newAnswer'] == 'v3'
    assert revisions[0]['triggerType'] == 'manual_edit'
    assert revisions[1]['oldAnswer'] == 'v1'
    assert revisions[1]['triggerType'] == 'web_verification'
    assert revisions[1]['sources'][0]['title'] == 'Docs'
    assert '-v1' in revisions[1]['diff'] and '+v2' in revisions[1]['diff']
    assert all(r['isSemantic'] is True for r in revisions)


def test_revise_validation(client, monkeypatch):
    monkeypatch.setattr(learning_verification, 'chat_json',
                        lambda *a, **k: {'semantic': True})
    cid = _make_card(client)
    assert client.post(f'/api/learning/cards/{cid}/revise', json={}).status_code == 400
    assert client.post(f'/api/learning/cards/{cid}/revise',
                       json={'answer': 'x', 'triggerType': 'nope'}).status_code == 400
    assert client.post('/api/learning/cards/missing/revise',
                       json={'answer': 'x'}).status_code == 404
    # Retired cards can't be revised again.
    client.post(f'/api/learning/cards/{cid}/revise', json={'answer': 'y'})
    assert client.post(f'/api/learning/cards/{cid}/revise',
                       json={'answer': 'z'}).status_code == 404


def test_judge_semantic_change_unit():
    assert learning_verification.judge_semantic_change('Same thing.', 'same THING') is False
