"""Persisted review attempts: an in-progress session survives leaving the view.

The grade itself is covered in test_learning_grading.py; these are about the
attempt row's lifecycle — saved on answer, resumed on return, deleted on rating.
"""
import pytest

from backend.ai import background
from backend.routes import learning as learning_routes


def _make_card(client, **extra):
    return client.post('/api/learning/cards',
                       json={'question': 'Q?', 'answer': 'A.', **extra}).json['id']


def _answer(client, card_id, answer='my answer', **extra):
    return client.post('/api/learning/attempts',
                       json={'cardId': card_id, 'mode': 'answered',
                             'answer': answer, **extra})


@pytest.fixture(autouse=True)
def no_bg(monkeypatch):
    """Record what the route queues instead of running it."""
    queued = []
    monkeypatch.setattr(background, 'run_bg', queued.append)
    learning_routes._grading_queued.clear()
    yield queued
    learning_routes._grading_queued.clear()


def test_answer_is_saved_and_queued_for_grading(client, no_bg):
    cid = _make_card(client)
    assert _answer(client, cid).status_code == 200

    attempts = client.get('/api/learning/attempts').json
    assert len(attempts) == 1
    assert attempts[0]['cardId'] == cid
    assert attempts[0]['mode'] == 'answered'
    assert attempts[0]['answer'] == 'my answer'
    assert attempts[0]['answerMode'] == 'typed'
    assert attempts[0]['gradeStatus'] == 'pending'
    assert attempts[0]['coverage'] is None
    assert len(no_bg) == 1


def test_flipped_card_is_saved_but_never_graded(client, no_bg):
    cid = _make_card(client)
    r = client.post('/api/learning/attempts', json={'cardId': cid, 'mode': 'skipped'})
    assert r.status_code == 200

    attempt = client.get('/api/learning/attempts').json[0]
    assert attempt['mode'] == 'skipped'
    assert attempt['answer'] is None
    assert attempt['answerMode'] == 'self'
    assert attempt['gradeStatus'] == 'skipped'
    assert no_bg == []


def test_returning_mid_session_does_not_re_ask_answered_cards(client):
    """The whole point: answer some cards, come back, and the deck still holds
    them as answered rather than handing them out again."""
    cards = [_make_card(client) for _ in range(4)]
    _answer(client, cards[0], 'first')
    client.post('/api/learning/attempts', json={'cardId': cards[1], 'mode': 'skipped'})

    # A fresh mount refetches both.
    due = [c['id'] for c in client.get('/api/learning/due').json]
    attempts = client.get('/api/learning/attempts').json
    assert set(due) == set(cards)
    # Answered cards sort to the front, so the client's "first unanswered card
    # is at attempts.length" seeding holds.
    assert set(due[:2]) == {cards[0], cards[1]}
    assert [a['cardId'] for a in attempts] == [cards[0], cards[1]]
    assert [a['answer'] for a in attempts] == ['first', None]


def test_re_answering_replaces_the_open_attempt(client, no_bg):
    cid = _make_card(client)
    _answer(client, cid, 'first try')
    _answer(client, cid, 'second try')

    attempts = client.get('/api/learning/attempts').json
    assert len(attempts) == 1
    assert attempts[0]['answer'] == 'second try'
    assert attempts[0]['gradeStatus'] == 'pending'


def test_replayed_save_is_idempotent(client, no_bg):
    """An offline-queued save can arrive twice; the second must not re-queue
    grading or wipe a grade that already landed."""
    from backend.db.connection import get_db
    cid = _make_card(client)
    body = {'id': '01ATTEMPT', 'cardId': cid, 'mode': 'answered', 'answer': 'a'}
    client.post('/api/learning/attempts', json=body)
    get_db().execute(
        "UPDATE learning_attempts SET grade_status='done', suggested_rating=3 WHERE id=?",
        ('01ATTEMPT',))
    get_db().commit()

    client.post('/api/learning/attempts', json=body)
    attempts = client.get('/api/learning/attempts').json
    assert len(attempts) == 1
    assert attempts[0]['gradeStatus'] == 'done'
    assert attempts[0]['suggestedRating'] == 3
    assert len(no_bg) == 1


def test_rating_closes_the_attempt(client):
    cid = _make_card(client)
    _answer(client, cid)
    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 3})
    assert client.get('/api/learning/attempts').json == []


def test_replayed_review_still_closes_the_attempt(client):
    """The offline queue can replay a review whose FSRS update already applied;
    the attempt must not be left behind to resurrect the card in the session."""
    cid = _make_card(client)
    _answer(client, cid)
    body = {'rating': 3, 'reviewId': '01REVIEW'}
    client.post(f'/api/learning/cards/{cid}/review', json=body)
    _answer(client, cid)  # a stray attempt re-created before the replay lands
    assert client.post(f'/api/learning/cards/{cid}/review', json=body).status_code == 200
    assert client.get('/api/learning/attempts').json == []


def test_attempts_respect_folder_and_tag_filters(client, no_bg):
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    in_folder = _make_card(client, folderId=fid)
    tagged = _make_card(client, tags=['physics'])
    loose = _make_card(client)
    for cid in (in_folder, tagged, loose):
        _answer(client, cid)

    assert [a['cardId'] for a in
            client.get(f'/api/learning/attempts?folderId={fid}').json] == [in_folder]
    assert [a['cardId'] for a in
            client.get('/api/learning/attempts?tag=physics').json] == [tagged]
    assert len(client.get('/api/learning/attempts').json) == 3


def test_listing_requeues_a_grade_orphaned_by_a_restart(client, no_bg):
    cid = _make_card(client)
    _answer(client, cid)
    assert len(no_bg) == 1

    # A restart loses the in-memory "already queued" set but keeps the row.
    learning_routes._grading_queued.clear()
    assert client.get('/api/learning/attempts').json[0]['gradeStatus'] == 'pending'
    assert len(no_bg) == 2

    # ...and doesn't pile up duplicates on every poll.
    client.get('/api/learning/attempts')
    assert len(no_bg) == 2


def test_speech_requested_persists_and_is_restamped_on_reanswer(client, no_bg):
    """The live toggle state at submit time, not session-wide — re-answering
    with a different value must overwrite the stored flag."""
    cid = _make_card(client)
    _answer(client, cid, 'first try')
    assert client.get('/api/learning/attempts').json[0]['speechRequested'] == 0

    _answer(client, cid, 'second try', speechMode=True)
    assert client.get('/api/learning/attempts').json[0]['speechRequested'] == 1


def test_attempt_on_a_pending_card_is_rejected(client):
    """Only active cards are reviewable, so only they can be answered."""
    from backend.db.connection import get_db
    card = _make_card(client)
    get_db().execute("UPDATE learning_cards SET state='pending' WHERE id=?", (card,))
    get_db().commit()
    assert _answer(client, card).status_code == 404
