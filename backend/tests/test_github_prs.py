"""Merged PRs filed into the Journal (backend/github_prs/)."""
import json
from datetime import datetime, timezone

import pytest

from backend.ai import github_prs as ai_prs
from backend.db.connection import get_db
from backend.github_prs import fetch, sync


def _iso(y, mo, d, h=12, mi=0) -> str:
    """A local wall-clock moment as GitHub's UTC ISO string."""
    ts = datetime(y, mo, d, h, mi).timestamp()
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _pull(number, merged=None, updated=None, title=None, body='## Task\nDo the thing.'):
    return {'number': number, 'title': title or f'PR {number}', 'body': body,
            'html_url': f'https://github.com/o/r/pull/{number}',
            'merged_at': merged, 'updated_at': updated or merged or _iso(2026, 1, 1)}


def _commit(iso):
    return {'commit': {'author': {'date': iso}, 'committer': {'date': iso}}}


class FakeGitHub:
    """Stands in for fetch.closed_pulls / fetch.pull_commits."""

    def __init__(self, pulls, commits=None, per_page=100, limit_after=None):
        self.pulls = pulls
        self.commits = commits or {}
        self.per_page = per_page
        self.limit_after = limit_after
        self.pages_read = []
        self.commit_calls = []

    def closed_pulls(self, token, owner, repo, page):
        self.pages_read.append(page)
        ordered = sorted(self.pulls, key=lambda p: p['updated_at'], reverse=True)
        return ordered[(page - 1) * self.per_page: page * self.per_page]

    def pull_commits(self, token, owner, repo, number):
        if self.limit_after is not None and len(self.commit_calls) >= self.limit_after:
            raise fetch.RateLimited('limit')
        self.commit_calls.append(number)
        return self.commits.get(number, [_commit(_iso(2026, 1, 5))])


@pytest.fixture
def github(monkeypatch):
    def install(fake):
        monkeypatch.setattr(fetch, 'closed_pulls', fake.closed_pulls)
        monkeypatch.setattr(fetch, 'pull_commits', fake.pull_commits)
        monkeypatch.setattr(fetch, 'PER_PAGE', fake.per_page)
        return fake
    return install


def _set_repo(repo='o/r', token=''):
    db = get_db()
    db.execute('UPDATE settings SET pr_journal_repo=?, github_agent_token=?', (repo, token))
    db.commit()


def _entries():
    return get_db().execute(
        "SELECT * FROM journal_entries WHERE title LIKE 'Merged #%' ORDER BY created_at").fetchall()


# ------------------------------------------------------------------ pure half

def test_merged_from_skips_prs_closed_without_merging():
    out = sync.merged_from([_pull(1, merged=_iso(2026, 1, 2)), _pull(2, merged=None)])
    assert [p['number'] for p in out] == [1]
    assert out[0]['merged_at'] == int(datetime(2026, 1, 2, 12).timestamp())


def test_commit_days_counts_on_the_4am_day_oldest_first():
    days = sync.commit_days([
        _commit(_iso(2026, 1, 8, 10)),
        _commit(_iso(2026, 1, 7, 15)),
        _commit(_iso(2026, 1, 8, 1, 30)),  # before 4am: still the 7th
        _commit(_iso(2026, 1, 7, 23)),
    ])
    assert days == [{'day': '2026-01-07', 'count': 3}, {'day': '2026-01-08', 'count': 1}]


def test_commit_days_uses_the_author_date_not_the_rebase():
    commit = {'commit': {'author': {'date': _iso(2026, 1, 3)},
                         'committer': {'date': _iso(2026, 1, 9)}}}
    assert sync.commit_days([commit]) == [{'day': '2026-01-03', 'count': 1}]


def test_entry_text_lists_days_with_plurals_and_only_foreign_years():
    pr = {'number': 7, 'title': 'T', 'url': 'https://x/7',
          'merged_at': int(datetime(2026, 1, 8, 12).timestamp())}
    days = [{'day': '2025-12-30', 'count': 2}, {'day': '2026-01-07', 'count': 5},
            {'day': '2026-01-08', 'count': 1}]
    assert sync.entry_text(pr, days) == (
        'Commits:\nDec 30, 2025 — 2 commits\nJan 7 — 5 commits\nJan 8 — 1 commit\n\nhttps://x/7')
    assert sync.entry_text(pr, days, ' It changed. ').startswith('It changed.\n\nCommits:')


# ------------------------------------------------------------------ fetching

class _Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data or [], headers or {}
        self.ok = status < 400
        self.text = ''

    def json(self):
        return self._data


def test_token_header_sent_only_when_set(monkeypatch):
    seen = []
    monkeypatch.setattr(fetch.requests, 'get',
                        lambda url, headers, params, timeout: seen.append(headers) or _Resp())
    fetch.closed_pulls('tok', 'o', 'r', 1)
    fetch.closed_pulls('', 'o', 'r', 1)
    assert seen[0]['Authorization'] == 'Bearer tok'
    assert 'Authorization' not in seen[1]


def test_exhausted_rate_limit_raises_rate_limited(monkeypatch):
    monkeypatch.setattr(fetch.requests, 'get', lambda *a, **k: _Resp(
        403, headers={'X-RateLimit-Remaining': '0'}))
    with pytest.raises(fetch.RateLimited):
        fetch.pull_commits(None, 'o', 'r', 1)


def test_pull_commits_follows_pages(monkeypatch):
    pages = {1: [_commit(_iso(2026, 1, 1))] * 100, 2: [_commit(_iso(2026, 1, 2))] * 3}
    monkeypatch.setattr(fetch.requests, 'get',
                        lambda url, headers, params, timeout: _Resp(data=pages[params['page']]))
    assert len(fetch.pull_commits(None, 'o', 'r', 1)) == 103


# ------------------------------------------------------------------ sync

def test_sync_files_each_merged_pr_on_its_merge_day(github, queued_jobs):
    _set_repo()
    github(FakeGitHub([
        _pull(1, merged=_iso(2026, 1, 2)),
        _pull(2, merged=None, updated=_iso(2026, 1, 3)),
        _pull(3, merged=_iso(2026, 1, 9), body=''),
    ], commits={1: [_commit(_iso(2026, 1, 1)), _commit(_iso(2026, 1, 2))]}))

    assert sync.sync() == 2
    rows = _entries()
    assert [r['title'] for r in rows] == ['Merged #1: PR 1', 'Merged #3: PR 3']
    assert rows[0]['created_at'] == int(datetime(2026, 1, 2, 12).timestamp())
    assert 'Jan 1 — 1 commit\nJan 2 — 1 commit' in rows[0]['content']
    assert json.loads(rows[0]['tags']) == ['coding']
    # Only a PR with a body gets a summary job.
    assert [(j['kind'], j['payload']['number']) for j in queued_jobs] == [(sync.SUMMARY_JOB, 1)]
    assert queued_jobs[0]['target_id'] == rows[0]['id']


def test_sync_is_idempotent_and_a_deleted_entry_stays_deleted(github, queued_jobs):
    _set_repo()
    github(FakeGitHub([_pull(1, merged=_iso(2026, 1, 2))]))
    assert sync.sync() == 1
    db = get_db()
    db.execute('DELETE FROM journal_entries WHERE id=?', (_entries()[0]['id'],))
    db.commit()
    assert sync.sync() == 0
    assert _entries() == []


def test_empty_repo_setting_turns_the_sync_off(github, queued_jobs):
    _set_repo('')
    fake = github(FakeGitHub([_pull(1, merged=_iso(2026, 1, 2))]))
    assert sync.sync() == 0
    assert fake.pages_read == []


def test_backfill_walks_every_page_and_incremental_stops_at_the_cursor(github, queued_jobs):
    _set_repo()
    pulls = [_pull(n, merged=_iso(2026, 1, n)) for n in range(1, 8)]
    fake = github(FakeGitHub(pulls, per_page=2))
    assert sync.sync() == 7
    assert fake.pages_read == [1, 2, 3, 4]

    pulls.append(_pull(20, merged=_iso(2026, 1, 20)))
    fake = github(FakeGitHub(pulls, per_page=2))
    assert sync.sync() == 1
    # Page 1 holds #20 and #7; page 2 (#6, #5) is already past the cursor's slack.
    assert fake.pages_read == [1, 2]
    assert fake.commit_calls == [20]


def test_rate_limit_ends_the_tick_and_the_next_one_finishes(github, queued_jobs):
    _set_repo()
    pulls = [_pull(n, merged=_iso(2026, 1, n)) for n in range(1, 6)]
    github(FakeGitHub(pulls, limit_after=2))
    assert sync.sync() == 2
    assert [r['title'] for r in _entries()] == ['Merged #1: PR 1', 'Merged #2: PR 2']

    github(FakeGitHub(pulls))
    assert sync.sync() == 3
    assert len(_entries()) == 5


# ------------------------------------------------------------------ summary

def test_summary_is_written_into_the_entry(github, run_jobs_sync, monkeypatch):
    monkeypatch.setattr(ai_prs, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ai_prs, 'chat_text', lambda prompt, system: 'Made it better.')
    _set_repo()
    github(FakeGitHub([_pull(1, merged=_iso(2026, 1, 2))]))
    sync.sync()
    entry = _entries()[0]
    assert entry['content'].startswith('Made it better.\n\nCommits:\nJan 5 — 1 commit')
    row = get_db().execute('SELECT summarized_at FROM merged_prs').fetchone()
    assert row['summarized_at']


def test_summary_never_overwrites_a_hand_edit(github, queued_jobs, monkeypatch):
    monkeypatch.setattr(ai_prs, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ai_prs, 'chat_text', lambda prompt, system: 'Model words.')
    _set_repo()
    github(FakeGitHub([_pull(1, merged=_iso(2026, 1, 2))]))
    sync.sync()
    db = get_db()
    entry_id = _entries()[0]['id']
    db.execute("UPDATE journal_entries SET content='My own words.' WHERE id=?", (entry_id,))
    db.commit()
    sync.summarize(entry_id, 'o/r', 1)
    assert _entries()[0]['content'] == 'My own words.'


def test_without_a_model_the_entry_keeps_commits_and_link(github, run_jobs_sync, monkeypatch):
    monkeypatch.setattr(ai_prs, 'is_ai_configured', lambda: False)
    _set_repo()
    github(FakeGitHub([_pull(1, merged=_iso(2026, 1, 2))]))
    sync.sync()
    assert _entries()[0]['content'] == 'Commits:\nJan 5 — 1 commit\n\nhttps://github.com/o/r/pull/1'
