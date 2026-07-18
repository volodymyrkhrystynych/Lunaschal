"""FSRS review endpoint, due queue, and stats."""
import json
import time


def _make_card(client, **extra):
    r = client.post('/api/learning/cards',
                    json={'question': 'Q?', 'answer': 'A.', **extra})
    return r.json['id']


def test_review_updates_schedule_and_logs(client):
    cid = _make_card(client)
    r = client.post(f'/api/learning/cards/{cid}/review',
                    json={'rating': 3, 'suggestedRating': 4,
                          'userAnswer': 'my answer', 'answerMode': 'typed',
                          'coverage': {'claims': [], 'summary': 's'}})
    assert r.status_code == 200
    assert r.json['due'] is not None

    from backend.db.connection import get_db
    card = get_db().execute('SELECT * FROM learning_cards WHERE id=?', (cid,)).fetchone()
    assert card['fsrs_state'] is not None
    assert json.loads(card['fsrs_state'])['stability'] is not None

    review = get_db().execute('SELECT * FROM learning_reviews WHERE card_id=?', (cid,)).fetchone()
    assert review['rating'] == 3
    assert review['suggested_rating'] == 4
    assert review['user_answer'] == 'my answer'
    assert review['answer_mode'] == 'typed'
    assert json.loads(review['review_log'])['rating'] == 3


def test_easy_schedules_out_of_due_queue(client):
    cid = _make_card(client)
    assert [c['id'] for c in client.get('/api/learning/due').json] == [cid]
    client.post(f'/api/learning/cards/{cid}/review', json={'rating': 4})
    assert client.get('/api/learning/due').json == []


def test_review_validation(client):
    cid = _make_card(client)
    for bad in (0, 5, 'x', None):
        r = client.post(f'/api/learning/cards/{cid}/review', json={'rating': bad})
        assert r.status_code == 400
    assert client.post('/api/learning/cards/nope/review', json={'rating': 3}).status_code == 404


def test_non_active_cards_not_reviewable(client):
    cid = _make_card(client)
    from backend.db.connection import get_db
    get_db().execute("UPDATE learning_cards SET state='retired' WHERE id=?", (cid,))
    get_db().commit()
    assert client.post(f'/api/learning/cards/{cid}/review', json={'rating': 3}).status_code == 404
    assert client.get('/api/learning/due').json == []


def test_stats_counts(client):
    a = _make_card(client)
    _make_card(client)
    from backend.db.connection import get_db
    # Make one card pending and one mastered-looking.
    get_db().execute(
        "UPDATE learning_cards SET fsrs_state=? WHERE id=?",
        (json.dumps({'stability': 42.0}), a),
    )
    get_db().execute(
        "INSERT INTO learning_cards(id, question, answer, state, created_at, updated_at)"
        " VALUES ('p1', 'Q', 'A', 'pending', 1, 1)"
    )
    get_db().commit()

    stats = client.get('/api/learning/stats').json
    assert stats == {'total': 2, 'due': 2, 'pending': 1, 'mastered': 1, 'learning': 1}


def test_due_and_stats_folder_filter(client):
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    inside = _make_card(client, folderId=fid)
    _make_card(client)

    due = client.get(f'/api/learning/due?folderId={fid}').json
    assert [c['id'] for c in due] == [inside]
    assert client.get(f'/api/learning/stats?folderId={fid}').json['total'] == 1


def test_due_ordering_and_future_exclusion(client):
    early = _make_card(client)
    late = _make_card(client)
    future = _make_card(client)
    from backend.db.connection import get_db
    now = int(time.time())
    get_db().execute('UPDATE learning_cards SET due=? WHERE id=?', (now - 100, early))
    get_db().execute('UPDATE learning_cards SET due=? WHERE id=?', (now - 50, late))
    get_db().execute('UPDATE learning_cards SET due=? WHERE id=?', (now + 9999, future))
    get_db().commit()
    assert [c['id'] for c in client.get('/api/learning/due').json] == [early, late]
