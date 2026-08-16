"""HTTP for discovery: saved searches, the triage feed, dismiss, rationale.

The feed is the phone screen, so its contract is tested here rather than
inferred: what appears on it, in what order, and what has left it.
"""
import json
import time

import pytest

from backend.db.connection import get_db
from backend.jobs import sync
from backend.jobs.sources.base import SourceResult

NOW = int(time.time())


@pytest.fixture
def profile(client):
    role_id = client.post('/api/jobs/profile/roles', json={
        'company': 'Acme', 'title': 'Engineer', 'ord': 0,
    }).get_json()['id']
    client.post('/api/jobs/profile/bullets', json={
        'roleId': role_id, 'text': 'Built services in Python on Postgres.', 'ord': 0,
    })
    client.post('/api/jobs/profile/skills', json={'name': 'Python'})
    return role_id


def make_job(client, title='Engineer', description='We need Python.', score=None):
    job_id = client.post('/api/jobs', json={
        'title': title, 'company': 'Acme', 'description': description,
    }).get_json()['id']
    if score is not None:
        db = get_db()
        db.execute('UPDATE jobs SET match_score=? WHERE id=?', (score, job_id))
        db.commit()
    return job_id


# --------------------------------------------------------------------------
# Saved searches
# --------------------------------------------------------------------------

def test_a_search_round_trips(client):
    created = client.post('/api/jobs/searches', json={
        'kind': 'greenhouse', 'label': 'Acme', 'params': {'slug': 'acme'},
        'intervalHours': 12,
    })
    assert created.status_code == 201
    body = created.get_json()
    assert body['params'] == {'slug': 'acme'}
    assert body['intervalHours'] == 12

    listed = client.get('/api/jobs/searches').get_json()
    assert [s['id'] for s in listed] == [body['id']]


def test_an_unknown_source_is_rejected(client):
    response = client.post('/api/jobs/searches', json={'kind': 'monster'})
    assert response.status_code == 400


def test_a_hostile_slug_is_rejected_at_the_form_not_at_3am(client):
    """An invalid source that fails silently in the nightly sweep is worse
    than a rejected form."""
    response = client.post('/api/jobs/searches', json={
        'kind': 'lever', 'params': {'slug': '../../evil'},
    })
    assert response.status_code == 400
    assert 'slug' in response.get_json()['error'].lower()


def test_adzuna_needs_no_slug(client):
    response = client.post('/api/jobs/searches', json={
        'kind': 'adzuna', 'params': {'what': 'python', 'where': 'Toronto'},
    })
    assert response.status_code == 201


def test_a_search_can_be_disabled_and_re_enabled(client):
    search_id = client.post('/api/jobs/searches', json={
        'kind': 'ashby', 'params': {'slug': 'acme'},
    }).get_json()['id']

    # Booleans cross this API as SQLite's 0/1, the same as `jobs.remote` and
    # `jobs.dismissed`; row_to_dict does not widen them.
    assert not client.patch(f'/api/jobs/searches/{search_id}',
                            json={'enabled': False}).get_json()['enabled']
    assert client.patch(f'/api/jobs/searches/{search_id}',
                        json={'enabled': True}).get_json()['enabled']


def test_patching_an_unknown_search_is_a_404(client):
    assert client.patch('/api/jobs/searches/nope', json={'label': 'x'}).status_code == 404


def test_a_search_can_be_deleted(client):
    search_id = client.post('/api/jobs/searches', json={
        'kind': 'lever', 'params': {'slug': 'acme'},
    }).get_json()['id']
    client.delete(f'/api/jobs/searches/{search_id}')
    assert client.get('/api/jobs/searches').get_json() == []


def test_running_one_search_by_hand(client, profile, monkeypatch):
    monkeypatch.setattr(sync, 'fetch_source', lambda kind, params, creds=None: SourceResult(
        jobs=[{'sourceId': 'gh-9', 'title': 'Python Engineer', 'company': 'Acme',
               'description': 'Python.', 'url': 'https://x/9', 'raw': {}}]
    ))
    search_id = client.post('/api/jobs/searches', json={
        'kind': 'greenhouse', 'params': {'slug': 'acme'},
    }).get_json()['id']

    result = client.post(f'/api/jobs/searches/{search_id}/run').get_json()

    assert result['added'] == 1


def test_running_an_unknown_search_is_a_404(client):
    assert client.post('/api/jobs/searches/nope/run').status_code == 404


# --------------------------------------------------------------------------
# The feed
# --------------------------------------------------------------------------

def test_the_feed_sorts_by_score_with_unscored_last(client):
    low = make_job(client, 'Low', score=0.2)
    high = make_job(client, 'High', score=0.9)
    unscored = make_job(client, 'Unscored')

    feed = client.get('/api/jobs/feed').get_json()

    assert [item['id'] for item in feed] == [high, low, unscored]


def test_a_dismissed_job_leaves_the_feed(client):
    job_id = make_job(client)
    client.post(f'/api/jobs/{job_id}/dismiss')
    assert client.get('/api/jobs/feed').get_json() == []


def test_dismissal_can_be_undone(client):
    job_id = make_job(client)
    client.post(f'/api/jobs/{job_id}/dismiss')
    client.post(f'/api/jobs/{job_id}/dismiss', json={'dismissed': False})
    assert [j['id'] for j in client.get('/api/jobs/feed').get_json()] == [job_id]


def test_dismissing_an_unknown_job_is_a_404(client):
    assert client.post('/api/jobs/nope/dismiss').status_code == 404


def test_a_queued_job_leaves_the_feed(client, profile):
    """It has left triage — showing it would offer Queue on something queued."""
    job_id = make_job(client)
    client.post(f'/api/jobs/{job_id}/queue', json={})
    assert client.get('/api/jobs/feed').get_json() == []


def test_a_job_with_a_manual_application_leaves_the_feed(client):
    job_id = make_job(client)
    client.post('/api/jobs/applications', json={'jobId': job_id})
    assert client.get('/api/jobs/feed').get_json() == []


def test_the_feed_inlines_the_keyword_report(client, profile, monkeypatch):
    """The most useful line on the card, and free to compute."""
    monkeypatch.setattr(sync, 'fetch_source', lambda kind, params, creds=None: SourceResult(
        jobs=[{'sourceId': 'gh-1', 'title': 'Dev', 'company': 'Acme',
               'description': 'We need Python and Kubernetes.', 'raw': {}}]
    ))
    search_id = client.post('/api/jobs/searches', json={
        'kind': 'greenhouse', 'params': {'slug': 'acme'},
    }).get_json()['id']
    client.post(f'/api/jobs/searches/{search_id}/run')

    item = client.get('/api/jobs/feed').get_json()[0]

    assert 'python' in item['matchReasons']['matched']
    assert 'kubernetes' in item['matchReasons']['missing']


def test_the_feed_truncates_descriptions(client):
    """A hundred full postings is megabytes over a phone connection."""
    make_job(client, description='x' * 5000)
    item = client.get('/api/jobs/feed').get_json()[0]
    assert len(item['description']) <= 600


def test_the_feed_respects_its_limit(client):
    for i in range(5):
        make_job(client, f'Job {i}')
    assert len(client.get('/api/jobs/feed?limit=2').get_json()) == 2


# --------------------------------------------------------------------------
# The rationale — advisory only
# --------------------------------------------------------------------------

def test_the_rationale_never_changes_the_score(client, profile, monkeypatch):
    """The sort is deterministic and stable; the model only narrates."""
    job_id = make_job(client, score=0.5)
    monkeypatch.setattr('backend.ai.job_match.assess_match', lambda *a, **k: {
        'verdict': 'weak', 'rationale': 'They want Kubernetes.', 'angle': '',
    })

    client.post(f'/api/jobs/{job_id}/rationale')

    score = get_db().execute('SELECT match_score FROM jobs WHERE id=?',
                             (job_id,)).fetchone()['match_score']
    assert score == 0.5


def test_the_rationale_is_stored_beside_the_computed_report(client, profile, monkeypatch):
    job_id = make_job(client)
    db = get_db()
    db.execute('UPDATE jobs SET match_reasons=? WHERE id=?',
               (json.dumps({'matched': ['python'], 'missing': ['go']}), job_id))
    db.commit()
    monkeypatch.setattr('backend.ai.job_match.assess_match', lambda *a, **k: {
        'verdict': 'possible', 'rationale': 'Close enough.', 'angle': 'Payments.',
    })

    client.post(f'/api/jobs/{job_id}/rationale')

    reasons = json.loads(db.execute('SELECT match_reasons FROM jobs WHERE id=?',
                                    (job_id,)).fetchone()['match_reasons'])
    assert reasons['matched'] == ['python']
    assert reasons['assessment']['verdict'] == 'possible'


def test_the_rationale_reports_503_when_the_model_is_down(client, profile, monkeypatch):
    job_id = make_job(client)
    monkeypatch.setattr('backend.ai.job_match.assess_match', lambda *a, **k: None)
    assert client.post(f'/api/jobs/{job_id}/rationale').status_code == 503


def test_the_rationale_needs_a_profile(client):
    job_id = make_job(client)
    assert client.post(f'/api/jobs/{job_id}/rationale').status_code == 400


def test_rescoring_re_ranks_the_feed_against_the_current_profile(client, profile,
                                                                 monkeypatch):
    """The profile changes far more often than postings arrive."""
    def explode(*a, **k):
        raise AssertionError('rescoring must not call the model')

    monkeypatch.setattr('backend.ai.llm.chat_json', explode)
    monkeypatch.setattr(sync, 'fetch_source', lambda kind, params, creds=None: SourceResult(
        jobs=[{'sourceId': 'gh-1', 'title': 'Dev', 'company': 'Acme',
               'description': 'We need Python and Kubernetes.', 'raw': {}}]
    ))
    search_id = client.post('/api/jobs/searches', json={
        'kind': 'greenhouse', 'params': {'slug': 'acme'},
    }).get_json()['id']
    client.post(f'/api/jobs/searches/{search_id}/run')
    before = client.get('/api/jobs/feed').get_json()[0]['matchScore']

    client.post('/api/jobs/profile/skills', json={'name': 'Kubernetes'})
    client.post('/api/jobs/rescore')

    assert client.get('/api/jobs/feed').get_json()[0]['matchScore'] > before


def test_the_rationale_releases_its_priority_mark(client, profile, monkeypatch):
    """A leaked mark makes background work defer to a call that already ended."""
    from backend.ai import priority

    def boom(*a, **k):
        raise RuntimeError('model exploded')

    monkeypatch.setattr('backend.ai.job_match.assess_match', boom)
    job_id = make_job(client)

    with pytest.raises(RuntimeError):
        client.post(f'/api/jobs/{job_id}/rationale')

    assert priority.active() is False
