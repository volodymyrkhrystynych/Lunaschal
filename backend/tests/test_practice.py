"""Tests for the Practice tab: pure session-scoring logic (no DB) plus the
Flask routes end-to-end against a throwaway DB via the `client` fixture."""
from backend.practice import queue
from backend.practice.grading import rating_label
from backend.practice.snippets import SNIPPETS


FAKE_SNIPPETS = [
    {'id': 'a', 'language': 'javascript', 'category': 'loops', 'title': 'A', 'code': 'a'},
    {'id': 'b', 'language': 'javascript', 'category': 'loops', 'title': 'B', 'code': 'b'},
    {'id': 'c', 'language': 'css', 'category': 'layout', 'title': 'C', 'code': 'c'},
]


def test_build_session_prioritizes_unattempted_then_weak_snippets():
    now = 1_000_000
    progress = {
        'a': {'attempts_count': 5, 'last_accuracy': 99.0, 'last_wpm': 80.0, 'last_practiced_at': now},
        'b': {'attempts_count': 3, 'last_accuracy': 50.0, 'last_wpm': 20.0, 'last_practiced_at': now},
    }
    ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
    # 'c' is never attempted -> always first; 'b' is weaker than 'a' -> scores higher
    assert ids == ['c', 'b', 'a']


def test_build_session_respects_language_filter():
    ids = queue.build_session(FAKE_SNIPPETS, {}, size=10, language='css')
    assert ids == ['c']


def test_build_session_respects_category_filter():
    ids = queue.build_session(FAKE_SNIPPETS, {}, size=10, category='layout')
    assert ids == ['c']


def test_build_session_caps_result_size():
    ids = queue.build_session(FAKE_SNIPPETS, {}, size=2)
    assert len(ids) == 2


def test_build_session_recency_breaks_ties_between_equally_weak_snippets():
    now = 1_000_000
    progress = {
        'a': {
            'attempts_count': 1, 'last_accuracy': 90.0, 'last_wpm': 40.0,
            'last_practiced_at': now - 86400,  # practiced 1 day ago
        },
        'b': {
            'attempts_count': 1, 'last_accuracy': 90.0, 'last_wpm': 40.0,
            'last_practiced_at': now - 86400 * 10,  # practiced 10 days ago
        },
    }
    ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
    assert ids == ['c', 'b', 'a']


def test_build_session_is_ordered_worst_first_not_shuffled():
    """A batch larger than one snippet must come back strictly ranked, since
    the frontend now walks it as "next lowest, next lowest" rather than
    treating it as a shuffled bag of equally-due snippets."""
    now = 1_000_000
    progress = {
        'a': {'attempts_count': 5, 'last_accuracy': 95.0, 'last_wpm': 60.0, 'last_practiced_at': now},
        'b': {'attempts_count': 5, 'last_accuracy': 40.0, 'last_wpm': 10.0, 'last_practiced_at': now},
    }
    for _ in range(10):
        ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
        assert ids == ['c', 'b', 'a']


def test_rating_label_thresholds():
    assert rating_label(60, 50) == 'Needs work'
    assert rating_label(85, 10) == 'Good'
    assert rating_label(96, 10) == 'Good'  # accurate but too slow
    assert rating_label(99, 45) == 'Great'


def test_session_endpoint_filters_by_language(client):
    resp = client.get('/api/practice/session?language=css&size=3')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) <= 3
    assert data
    assert all(s['language'] == 'css' for s in data)


def test_submit_attempt_updates_progress_and_returns_rating(client):
    snippet_id = SNIPPETS[0]['id']
    resp = client.post(
        '/api/practice/attempts',
        json={'snippetId': snippet_id, 'wpm': 45, 'accuracy': 98, 'errorCount': 1},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rating'] == 'Great'
    assert data['progress']['attemptsCount'] == 1
    assert data['progress']['lastWpm'] == 45
    assert data['progress']['snippetId'] == snippet_id

    resp2 = client.post(
        '/api/practice/attempts',
        json={'snippetId': snippet_id, 'wpm': 30, 'accuracy': 70, 'errorCount': 5},
    )
    data2 = resp2.get_json()
    assert data2['rating'] == 'Needs work'
    assert data2['progress']['attemptsCount'] == 2
    assert data2['progress']['bestWpm'] == 45
    assert data2['progress']['bestAccuracy'] == 98


def test_submit_attempt_rejects_unknown_snippet(client):
    resp = client.post(
        '/api/practice/attempts', json={'snippetId': 'nope', 'wpm': 1, 'accuracy': 1}
    )
    assert resp.status_code == 400


def test_stats_endpoint_aggregates_by_language(client):
    snippet_id = next(s['id'] for s in SNIPPETS if s['language'] == 'html')
    client.post(
        '/api/practice/attempts',
        json={'snippetId': snippet_id, 'wpm': 50, 'accuracy': 100, 'errorCount': 0},
    )
    resp = client.get('/api/practice/stats')
    data = resp.get_json()
    assert data['totalAttempts'] == 1
    assert data['byLanguage']['html']['attempts'] == 1
    assert data['byLanguage']['css']['attempts'] == 0
