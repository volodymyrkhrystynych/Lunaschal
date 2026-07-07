"""Route tests for flashcard tagging (`backend/routes/flashcard.py`).

Covers tag normalization on create/update, the `tag` filter on the list /
due / stats endpoints, and the tag summary endpoint — all against a real
temporary SQLite DB via the Flask test client.
"""


def _create(client, front, back, tags=None):
    payload = {'front': front, 'back': back}
    if tags is not None:
        payload['tags'] = tags
    r = client.post('/api/flashcards', json=payload)
    assert r.status_code == 201
    return r.get_json()['id']


def test_create_normalizes_tags(client):
    card_id = _create(client, 'q', 'a', ['  JavaScript ', 'javascript', '', 'Python', 42])
    card = client.get(f'/api/flashcards/{card_id}').get_json()
    # trimmed, lowercased, deduped case-insensitively; blanks and non-strings dropped
    assert card['tags'] == ['javascript', 'python']


def test_create_without_tags_returns_empty_list(client):
    card_id = _create(client, 'q', 'a')
    card = client.get(f'/api/flashcards/{card_id}').get_json()
    assert card['tags'] == []


def test_list_filters_by_tag(client):
    _create(client, 'js q', 'js a', ['javascript'])
    _create(client, 'py q', 'py a', ['python'])
    _create(client, 'both q', 'both a', ['javascript', 'python'])
    _create(client, 'untagged q', 'untagged a')

    assert len(client.get('/api/flashcards').get_json()) == 4

    js = client.get('/api/flashcards?tag=javascript').get_json()
    assert sorted(c['front'] for c in js) == ['both q', 'js q']

    # filter is case-insensitive on the query side too
    py = client.get('/api/flashcards?tag=Python').get_json()
    assert sorted(c['front'] for c in py) == ['both q', 'py q']

    assert client.get('/api/flashcards?tag=rust').get_json() == []


def test_due_respects_tag_filter(client):
    # new cards have next_review = now, so all are immediately due
    _create(client, 'js q', 'js a', ['javascript'])
    _create(client, 'py q', 'py a', ['python'])

    due = client.get('/api/flashcards/due?tag=javascript').get_json()
    assert [c['front'] for c in due] == ['js q']
    assert due[0]['tags'] == ['javascript']


def test_stats_on_empty_db_returns_zeros(client):
    # SUM() over zero rows yields NULL — the COALESCE must turn that into 0
    stats = client.get('/api/flashcards/stats').get_json()
    assert stats == {'total': 0, 'due': 0, 'mastered': 0, 'learning': 0}


def test_stats_respect_tag_filter(client):
    _create(client, 'js q', 'js a', ['javascript'])
    _create(client, 'py q1', 'py a1', ['python'])
    _create(client, 'py q2', 'py a2', ['python'])

    overall = client.get('/api/flashcards/stats').get_json()
    assert overall['total'] == 3

    py = client.get('/api/flashcards/stats?tag=python').get_json()
    assert py == {'total': 2, 'due': 2, 'mastered': 0, 'learning': 2}


def test_tags_endpoint_lists_counts(client):
    _create(client, 'q1', 'a1', ['javascript', 'python'])
    _create(client, 'q2', 'a2', ['python'])
    _create(client, 'q3', 'a3')

    tags = client.get('/api/flashcards/tags').get_json()
    assert tags == [
        {'name': 'python', 'count': 2},
        {'name': 'javascript', 'count': 1},
    ]


def test_update_replaces_and_clears_tags(client):
    card_id = _create(client, 'q', 'a', ['javascript'])

    r = client.patch(f'/api/flashcards/{card_id}', json={'tags': ['Python']})
    assert r.status_code == 200
    assert client.get(f'/api/flashcards/{card_id}').get_json()['tags'] == ['python']

    client.patch(f'/api/flashcards/{card_id}', json={'tags': []})
    assert client.get(f'/api/flashcards/{card_id}').get_json()['tags'] == []
    assert client.get('/api/flashcards/tags').get_json() == []


def test_review_flow_with_tag_filter(client):
    card_id = _create(client, 'js q', 'js a', ['javascript'])

    r = client.post(f'/api/flashcards/{card_id}/review', json={'grade': 3})
    assert r.status_code == 200
    assert r.get_json()['interval'] == 1

    # graded "Easy" → scheduled a day out → no longer due under the tag filter
    assert client.get('/api/flashcards/due?tag=javascript').get_json() == []
