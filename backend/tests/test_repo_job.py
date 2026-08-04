"""The nightly repo-context snapshot job and its scheduling decision."""
import json
import time
from datetime import date, datetime

import pytest
from ulid import ULID

from backend.ai.repo_context import render_change_summary, summarize_delta
from backend.db.connection import get_db
from backend.research import repo_job, repo_scheduler


@pytest.fixture
def no_llm(monkeypatch):
    """The default for these tests: the deterministic half only."""
    monkeypatch.setattr(repo_job, 'summarize_delta', lambda *a, **k: None)


def _snapshots():
    # Same ordering as the production queries: generated_at is second-resolution,
    # so the ULID id is what actually orders same-second rows.
    return get_db().execute(
        'SELECT * FROM repo_snapshots ORDER BY generated_at DESC, id DESC'
    ).fetchall()


def test_run_writes_a_snapshot_of_the_real_repo(client, no_llm):
    result = repo_job.run_repo_snapshot(force=True)
    assert result is not None
    assert result['routeCount'] > 100
    assert result['tableCount'] > 10

    rows = _snapshots()
    assert len(rows) == 1
    assert rows[0]['git_sha'] and len(rows[0]['git_sha']) == 40
    assert 'POST /api/ideas/voice' in rows[0]['digest']
    facts = json.loads(rows[0]['facts'])
    assert facts['routes'] and facts['tables']


def test_a_second_run_at_the_same_sha_is_a_no_op(client, no_llm):
    repo_job.run_repo_snapshot(force=True)
    assert repo_job.run_repo_snapshot() is None
    assert len(_snapshots()) == 1


def test_force_reruns_at_the_same_sha_and_links_the_previous(client, no_llm):
    first = repo_job.run_repo_snapshot(force=True)
    second = repo_job.run_repo_snapshot(force=True)
    assert second is not None
    rows = _snapshots()
    assert len(rows) == 2
    assert rows[0]['prev_snapshot_id'] == first['id']


def test_a_failed_summary_still_stores_the_facts(client, monkeypatch):
    """The deterministic extraction is the product; the prose is a convenience.
    An LLM failure must never cost us the facts."""
    def boom(*a, **k):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(repo_job, 'summarize_delta', boom)
    result = repo_job.run_repo_snapshot(force=True)

    assert result is not None
    rows = _snapshots()
    assert rows[0]['change_summary'] is None
    assert rows[0]['digest']
    assert json.loads(rows[0]['facts'])['routes']


def test_summary_is_stored_when_the_model_answers(client, monkeypatch):
    monkeypatch.setattr(
        repo_job,
        'summarize_delta',
        lambda commits, diffstat: {
            'headline': 'Added the Ideas tab.',
            'changes': [{'area': 'Ideas', 'summary': 'Voice capture and Paper sketches.'}],
        },
    )
    repo_job.run_repo_snapshot(force=True)
    summary = _snapshots()[0]['change_summary']
    assert 'Added the Ideas tab.' in summary
    assert '**Ideas** — Voice capture and Paper sketches.' in summary


def test_current_snapshot_returns_the_newest(client, no_llm):
    """generated_at is a second-resolution int, so two refreshes in the same
    second tie. The ULID id breaks the tie in creation order — without it,
    clicking "Refresh now" twice could leave the app reading the older
    snapshot."""
    assert repo_job.current_snapshot() is None
    # `now` is pinned so the tie is constructed rather than hoped for — a slow
    # run would otherwise cross a second boundary and stop testing anything.
    pinned = 1_785_700_000
    repo_job.run_repo_snapshot(now=pinned, force=True)
    second = repo_job.run_repo_snapshot(now=pinned, force=True)

    rows = _snapshots()
    assert rows[0]['generated_at'] == rows[1]['generated_at'] == pinned
    assert repo_job.current_snapshot()['id'] == second['id']


def test_a_same_second_rerun_links_the_right_previous(client, no_llm):
    """The same tie, on the write path: _latest_row picks the parent."""
    pinned = 1_785_700_000
    first = repo_job.run_repo_snapshot(now=pinned, force=True)
    second = repo_job.run_repo_snapshot(now=pinned, force=True)
    third = repo_job.run_repo_snapshot(now=pinned, force=True)
    links = {r['id']: r['prev_snapshot_id'] for r in _snapshots()}
    assert links[second['id']] == first['id']
    assert links[third['id']] == second['id']


def test_pruning_keeps_only_the_most_recent(client, no_llm):
    db = get_db()
    now = int(time.time())
    for i in range(repo_job.KEEP_SNAPSHOTS + 5):
        db.execute(
            'INSERT INTO repo_snapshots(id, git_sha, facts, digest, generated_at, created_at)'
            ' VALUES (?,?,?,?,?,?)',
            (str(ULID()), f'sha{i}', '{}', '', now - (100 - i), now),
        )
    db.commit()
    repo_job.run_repo_snapshot(force=True)
    assert len(_snapshots()) == repo_job.KEEP_SNAPSHOTS


def test_run_refuses_a_directory_that_is_not_this_repo(client, monkeypatch, tmp_path):
    """The guard against a misconfigured root sending the extractors across
    the filesystem."""
    monkeypatch.setattr(repo_job.repo_facts, 'repo_root', lambda: tmp_path)
    assert repo_job.run_repo_snapshot(force=True) is None
    assert _snapshots() == []


def test_view_drift_warnings_are_recorded(client, monkeypatch):
    monkeypatch.setattr(repo_job, 'summarize_delta', lambda *a, **k: None)
    real = repo_job.repo_facts.build_facts

    def with_warning(*args, **kwargs):
        facts = real(*args, **kwargs)
        facts['views']['warnings'] = ['VIEWS and Sidebar navItems disagree: ideas']
        return facts

    monkeypatch.setattr(repo_job.repo_facts, 'build_facts', with_warning)
    result = repo_job.run_repo_snapshot(force=True)
    assert result['warnings'] == ['VIEWS and Sidebar navItems disagree: ideas']
    assert json.loads(_snapshots()[0]['warnings'])


# --- Routes ---

def test_repo_context_route_is_null_before_the_first_run(client):
    assert client.get('/api/ideas/repo-context').get_json() is None


def test_refresh_then_read(client, no_llm):
    assert client.post('/api/ideas/repo-context/refresh').status_code == 201
    body = client.get('/api/ideas/repo-context').get_json()
    assert body['gitSha']
    assert 'Lunaschal repo inventory' in body['digest']
    assert body['warnings'] == []
    # The raw extraction is tens of KB and the client only renders the digest.
    assert 'facts' not in body
    # generated_at is in TIMESTAMP_COLS, so it arrives as ISO, not an int.
    assert body['generatedAt'].startswith('20')


# --- Scheduling decision ---

def test_should_run_only_inside_the_window_once_per_date():
    at = lambda h: datetime(2026, 8, 2, h, 30)
    assert repo_scheduler.should_run(True, 3, at(3), None)
    assert repo_scheduler.should_run(True, 3, at(4), None)
    # Outside [hour, hour + 2)
    assert not repo_scheduler.should_run(True, 3, at(2), None)
    assert not repo_scheduler.should_run(True, 3, at(5), None)
    # Disabled
    assert not repo_scheduler.should_run(False, 3, at(3), None)
    # Already ran today
    assert not repo_scheduler.should_run(True, 3, at(3), date(2026, 8, 2))
    # Ran yesterday, so today is due
    assert repo_scheduler.should_run(True, 3, at(3), date(2026, 8, 1))


def test_window_does_not_collide_with_the_other_two_schedulers():
    """The title sweep owns 02:00-03:00 and the briefing 05:00-07:00."""
    from backend.briefing_scheduler import WINDOW_SPAN_HOURS as briefing_span
    from backend.chat_title_scheduler import TITLE_WINDOW_END_HOUR

    hour = repo_scheduler.DEFAULT_HOUR
    end = hour + repo_scheduler.WINDOW_SPAN_HOURS
    assert hour >= TITLE_WINDOW_END_HOUR
    assert end <= 5  # the briefing's default hour
    assert briefing_span  # referenced so the coupling is visible here


def test_settings_defaults_and_overrides(client):
    assert repo_scheduler.repo_context_settings() == (True, 3)
    get_db().execute(
        'UPDATE settings SET repo_context_enabled=0, repo_context_hour=1'
    )
    get_db().commit()
    assert repo_scheduler.repo_context_settings() == (False, 1)


def test_scheduler_does_not_start_under_the_werkzeug_reloader_parent(monkeypatch):
    started = []
    monkeypatch.setattr(
        repo_scheduler.threading,
        'Thread',
        lambda *a, **k: started.append(1) or type('T', (), {'start': lambda self: None})(),
    )
    monkeypatch.setenv('FLASK_DEBUG', '1')
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    repo_scheduler.start_repo_context_scheduler()
    assert started == []


# --- The delta summarizer ---

def test_summarize_delta_returns_none_without_ai(monkeypatch):
    import backend.ai.repo_context as rc
    monkeypatch.setattr(rc, 'is_ai_configured', lambda: False)
    assert rc.summarize_delta(['abc feat: x'], '') is None


def test_summarize_delta_returns_none_with_no_commits(monkeypatch):
    import backend.ai.repo_context as rc
    monkeypatch.setattr(rc, 'is_ai_configured', lambda: True)
    assert rc.summarize_delta([], 'stat') is None


def test_summarize_delta_parses_and_drops_empty_changes(monkeypatch):
    import backend.ai.repo_context as rc
    monkeypatch.setattr(rc, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(rc, 'chat_json', lambda *a, **k: {
        'headline': 'Ideas tab landed.',
        'changes': [
            {'area': 'Ideas', 'summary': 'Voice capture.'},
            {'area': 'Noise', 'summary': ''},
        ],
    })
    result = rc.summarize_delta(['abc feat: ideas'], 'stat')
    assert result['headline'] == 'Ideas tab landed.'
    assert result['changes'] == [{'area': 'Ideas', 'summary': 'Voice capture.'}]


def test_summarize_delta_swallows_model_errors(monkeypatch):
    import backend.ai.repo_context as rc
    monkeypatch.setattr(rc, 'is_ai_configured', lambda: True)

    def boom(*a, **k):
        raise RuntimeError('empty completion')

    monkeypatch.setattr(rc, 'chat_json', boom)
    assert rc.summarize_delta(['abc'], '') is None


def test_render_change_summary_is_pure():
    assert render_change_summary(None) is None
    assert render_change_summary({'headline': 'x', 'changes': []}) == 'x'
    out = render_change_summary({
        'headline': 'Head',
        'changes': [{'area': 'A', 'summary': 'did a'}, {'area': '', 'summary': 'did b'}],
    })
    assert '- **A** — did a' in out
    assert '- did b' in out
