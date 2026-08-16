"""Sync: the upsert rules, the deterministic score, and the interval gate.

The two upsert tests here are the load-bearing ones. Boards re-list the same
posting every night, so a sync that overwrites the row wholesale would resurrect
every job the user rejected — and a feed you have to reject the same posting in
twice is a feed nobody opens again.
"""
import json
import time

import pytest

from backend.db.connection import get_db
from backend.jobs import sync
from backend.jobs.sources.base import SourceError, SourceResult

HOUR = 3600
NOW = int(time.time())


@pytest.fixture
def profile(client):
    """Enough of a profile for the keyword report to have something to say."""
    role_id = client.post('/api/jobs/profile/roles', json={
        'company': 'Acme', 'title': 'Engineer', 'ord': 0,
    }).get_json()['id']
    client.post('/api/jobs/profile/bullets', json={
        'roleId': role_id, 'text': 'Built services in Python on Postgres.', 'ord': 0,
    })
    client.post('/api/jobs/profile/skills', json={'name': 'Python'})
    return role_id


def make_search(db, kind='greenhouse', params=None, interval=24, last_run=None,
                enabled=1):
    search_id = f'search-{kind}-{int(time.time() * 1000000) % 1000000}'
    db.execute(
        'INSERT INTO job_searches (id, kind, label, params, enabled, interval_hours,'
        ' last_run_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (search_id, kind, kind, json.dumps(params or {'slug': 'acme'}), enabled,
         interval, last_run, NOW, NOW),
    )
    db.commit()
    return {'id': search_id, 'kind': kind, 'params': json.dumps(params or {'slug': 'acme'})}


def posting(source_id='gh-1', title='Python Engineer', description='We need Python.'):
    return {
        'sourceId': source_id, 'title': title, 'company': 'Acme',
        'location': 'Toronto', 'remote': False, 'salaryMin': None,
        'salaryMax': None, 'salaryCurrency': '', 'description': description,
        'url': f'https://example.com/{source_id}', 'postedAt': '2026-08-01T00:00:00Z',
        'raw': {'id': source_id},
    }


# --------------------------------------------------------------------------
# The two rules that keep the feed trustworthy
# --------------------------------------------------------------------------

def test_a_resync_does_not_undismiss_a_dismissed_job(client, profile, monkeypatch):
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[posting()]))

    sync.sync_search(db, search)
    job_id = db.execute("SELECT id FROM jobs WHERE source_id='gh-1'").fetchone()['id']
    client.post(f'/api/jobs/{job_id}/dismiss')

    sync.sync_search(db, search)

    assert db.execute('SELECT dismissed FROM jobs WHERE id=?', (job_id,)).fetchone()['dismissed'] == 1


def test_a_resync_preserves_created_at(client, profile, monkeypatch):
    """`created_at` is what 'new since yesterday' is measured from."""
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[posting()]))

    sync.sync_search(db, search)
    first = db.execute("SELECT id, created_at FROM jobs WHERE source_id='gh-1'").fetchone()

    time.sleep(1.1)
    sync.sync_search(db, search)
    second = db.execute('SELECT created_at, updated_at FROM jobs WHERE id=?',
                        (first['id'],)).fetchone()

    assert second['created_at'] == first['created_at']
    assert second['updated_at'] > first['created_at']


def test_a_resync_refreshes_volatile_fields(client, profile, monkeypatch):
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[posting()]))
    sync.sync_search(db, search)

    monkeypatch.setattr(sync, 'fetch_source', lambda kind, params, creds=None: SourceResult(
        jobs=[posting(title='Senior Python Engineer', description='Python and Docker.')]
    ))
    sync.sync_search(db, search)

    row = db.execute("SELECT title, description FROM jobs WHERE source_id='gh-1'").fetchone()
    assert row['title'] == 'Senior Python Engineer'
    assert 'Docker' in row['description']


def test_the_same_posting_counts_as_updated_not_added(client, profile, monkeypatch):
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[posting()]))

    assert sync.sync_search(db, search)['added'] == 1
    second = sync.sync_search(db, search)
    assert (second['added'], second['updated']) == (0, 1)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_sync_scores_every_posting_without_a_model(client, profile, monkeypatch):
    """The whole reason scoring is deterministic: no model call, so a 200-job
    sync is free and the feed sorts the moment it lands."""
    def explode(*a, **k):
        raise AssertionError('sync must not call the model')

    monkeypatch.setattr('backend.ai.llm.chat_json', explode)
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source', lambda kind, params, creds=None: SourceResult(
        jobs=[posting(description='We need Python, Postgres and Kubernetes.')]
    ))

    sync.sync_search(db, search)
    row = db.execute("SELECT match_score, match_reasons FROM jobs WHERE source_id='gh-1'").fetchone()

    assert row['match_score'] is not None
    reasons = json.loads(row['match_reasons'])
    assert 'python' in reasons['matched']
    assert 'kubernetes' in reasons['missing']


def test_an_empty_profile_leaves_the_score_null(client, monkeypatch):
    """NULL means 'not scored yet'. Zero would be a claim about the posting."""
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[posting()]))

    sync.sync_search(db, search)
    row = db.execute("SELECT match_score FROM jobs WHERE source_id='gh-1'").fetchone()
    assert row['match_score'] is None


def test_a_snippet_scored_posting_is_flagged_partial(client, profile, monkeypatch):
    db = get_db()
    search = make_search(db, kind='adzuna', params={'what': 'python'})
    snippet = {**posting(source_id='az-1'), 'descriptionIsSnippet': True}
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[snippet]))

    sync.sync_search(db, search)
    row = db.execute("SELECT match_reasons FROM jobs WHERE source_id='az-1'").fetchone()
    assert json.loads(row['match_reasons'])['partial'] is True


def test_rescore_all_follows_the_profile(client, profile, monkeypatch):
    """The profile changes far more often than postings arrive, so a score
    computed against last month's skills is worse than no score."""
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source', lambda kind, params, creds=None: SourceResult(
        jobs=[posting(description='We need Python and Kubernetes.')]
    ))
    sync.sync_search(db, search)
    before = db.execute("SELECT match_score FROM jobs WHERE source_id='gh-1'").fetchone()['match_score']

    client.post('/api/jobs/profile/skills', json={'name': 'Kubernetes'})
    sync.rescore_all(db)
    after = db.execute("SELECT match_score FROM jobs WHERE source_id='gh-1'").fetchone()['match_score']

    assert after > before


# --------------------------------------------------------------------------
# Upsert edge cases
# --------------------------------------------------------------------------

def test_a_posting_with_no_title_is_skipped(client, profile, monkeypatch):
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source', lambda kind, params, creds=None: SourceResult(
        jobs=[posting(title=''), posting(source_id='gh-2', title='Real')]
    ))
    sync.sync_search(db, search)
    assert db.execute('SELECT COUNT(*) AS c FROM jobs').fetchone()['c'] == 1


def test_two_sources_may_share_a_source_id(client, profile, monkeypatch):
    """UNIQUE is on (source, source_id) — Greenhouse's id 1 and Lever's id 1
    are different postings."""
    db = get_db()
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[posting('1')]))
    sync.sync_search(db, make_search(db, kind='greenhouse'))
    sync.sync_search(db, make_search(db, kind='lever'))
    assert db.execute('SELECT COUNT(*) AS c FROM jobs').fetchone()['c'] == 2


# --------------------------------------------------------------------------
# The interval gate and failure handling
# --------------------------------------------------------------------------

def test_a_search_is_due_when_never_run(client):
    db = get_db()
    make_search(db, last_run=None)
    assert len(sync.due_searches(db, NOW)) == 1


def test_a_search_run_an_hour_ago_is_not_due(client):
    db = get_db()
    make_search(db, interval=24, last_run=NOW - HOUR)
    assert sync.due_searches(db, NOW) == []


def test_a_search_past_its_interval_is_due(client):
    db = get_db()
    make_search(db, interval=6, last_run=NOW - 7 * HOUR)
    assert len(sync.due_searches(db, NOW)) == 1


def test_a_disabled_search_is_never_due(client):
    db = get_db()
    make_search(db, enabled=0, last_run=None)
    assert sync.due_searches(db, NOW) == []


def test_a_failing_search_still_stamps_last_run_at(client, monkeypatch):
    """Otherwise a permanently broken search is retried on every single tick."""
    db = get_db()
    search = make_search(db)

    def boom(kind, params, creds=None):
        raise SourceError('board is down')

    monkeypatch.setattr(sync, 'fetch_source', boom)
    result = sync.sync_search(db, search)

    row = db.execute('SELECT last_run_at, last_error FROM job_searches WHERE id=?',
                     (search['id'],)).fetchone()
    assert result['error'] == 'board is down'
    assert row['last_run_at'] is not None
    assert row['last_error'] == 'board is down'


def test_a_later_success_clears_the_stored_error(client, profile, monkeypatch):
    db = get_db()
    search = make_search(db)
    monkeypatch.setattr(sync, 'fetch_source',
                        lambda k, p, creds=None: (_ for _ in ()).throw(SourceError('down')))
    sync.sync_search(db, search)

    monkeypatch.setattr(sync, 'fetch_source',
                        lambda k, p, creds=None: SourceResult(jobs=[posting()]))
    sync.sync_search(db, search)

    row = db.execute('SELECT last_error, last_count FROM job_searches WHERE id=?',
                     (search['id'],)).fetchone()
    assert row['last_error'] is None
    assert row['last_count'] == 1


def test_one_broken_search_does_not_stop_the_others(client, profile, monkeypatch):
    db = get_db()
    make_search(db, kind='greenhouse', params={'slug': 'broken'})
    make_search(db, kind='lever', params={'slug': 'working'})

    def selective(kind, params, creds=None):
        if kind == 'greenhouse':
            raise SourceError('down')
        return SourceResult(jobs=[posting('lv-1')])

    monkeypatch.setattr(sync, 'fetch_source', selective)
    monkeypatch.setattr(sync, 'INTER_REQUEST_DELAY', 0)
    result = sync.run_sync_sweep(NOW)

    assert result['searches'] == 2
    assert result['added'] == 1


# --------------------------------------------------------------------------
# Timestamp parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize('value,expected', [
    ('2026-08-01T00:00:00Z', 1785542400),
    ('2026-08-01T00:00:00+00:00', 1785542400),
    (1785542400000, 1785542400),   # Lever milliseconds
    (1785542400, 1785542400),
])
def test_posted_at_parses_the_shapes_boards_actually_send(value, expected):
    assert sync.parse_posted_at(value) == expected


@pytest.mark.parametrize('junk', [None, '', 'yesterday', {}, []])
def test_an_unparseable_date_is_none_not_now(junk):
    """A wrong posting date silently re-sorts the feed."""
    assert sync.parse_posted_at(junk) is None
