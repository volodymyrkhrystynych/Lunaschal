"""The loop that feeds the research worker: what's due, and when it may run."""
import time

import pytest

from backend.ai import priority
from backend.db.connection import get_db
from backend.research import research_scheduler as sched, worker
from backend.research.research_job import (
    RESEARCH_COOLDOWN_SECONDS, plan_next, run_research_task,
)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    priority.reset()
    worker.reset()
    yield
    worker.cancel()
    worker.wait_idle()
    worker.reset()
    priority.reset()


@pytest.fixture
def snapshot(client, monkeypatch):
    import backend.research.repo_job as job
    monkeypatch.setattr(job, 'summarize_delta', lambda *a, **k: None)
    job.run_repo_snapshot(force=True)


@pytest.fixture
def searchable(monkeypatch):
    monkeypatch.setattr(sched, 'research_enabled', lambda: True)
    import backend.research.research_job as rj
    monkeypatch.setattr('backend.research.web.is_search_configured', lambda: True)
    return rj


def _idea(client, title='Global search'):
    return client.post('/api/ideas', json={'title': title, 'rawContent': 'search all of it'}).get_json()['id']


def _assess(client, idea_id, monkeypatch, verdict='no'):
    import backend.research.assess as mod
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': verdict, 'confidence': 0.3, 'rationale': 'r',
        'evidenceIndexes': [], 'openQuestions': [],
    })
    return mod.run_assessment(idea_id)


# --- plan_next ---

def test_nothing_is_due_with_no_ideas(client):
    assert plan_next() is None


def test_an_unassessed_idea_is_due_for_assessment(client, snapshot):
    idea_id = _idea(client)
    assert plan_next() == ('assess', idea_id)


def test_no_snapshot_means_no_assessment_is_scheduled(client):
    """There is nothing to judge an idea against yet, and assessing anyway
    would only store a row saying so."""
    _idea(client)
    assert plan_next() is None


def test_an_assessed_idea_is_not_reassessed(client, snapshot, monkeypatch):
    idea_id = _idea(client)
    _assess(client, idea_id, monkeypatch)
    # No search provider configured, so nothing else is due either.
    assert plan_next() is None


def test_a_new_snapshot_makes_the_assessment_due_again(client, snapshot, monkeypatch):
    import backend.research.repo_job as job
    idea_id = _idea(client)
    _assess(client, idea_id, monkeypatch)
    assert plan_next() is None

    job.run_repo_snapshot(force=True)
    assert plan_next() == ('assess', idea_id)


def test_editing_an_idea_makes_it_due_again(client, snapshot, monkeypatch):
    idea_id = _idea(client)
    _assess(client, idea_id, monkeypatch)
    assert plan_next() is None

    get_db().execute(
        'UPDATE ideas SET updated_at=? WHERE id=?', (int(time.time()) + 60, idea_id)
    )
    get_db().commit()
    assert plan_next() == ('assess', idea_id)


def test_settled_ideas_are_left_alone(client, snapshot):
    idea_id = _idea(client)
    for status in ('shipped', 'parked'):
        get_db().execute('UPDATE ideas SET status=? WHERE id=?', (status, idea_id))
        get_db().commit()
        assert plan_next() is None, status


def test_an_idea_already_being_worked_on_is_skipped(client, snapshot):
    idea_id = _idea(client)
    get_db().execute("UPDATE ideas SET research_state='running' WHERE id=?", (idea_id,))
    get_db().commit()
    assert plan_next() is None


def test_assessment_is_always_preferred_over_research(client, snapshot, monkeypatch, searchable):
    """Assessment is cheap, needs no web, and tells the research pass what to
    look for."""
    old = _idea(client, 'Older idea')
    _assess(client, old, monkeypatch)
    new = _idea(client, 'Newer idea')
    assert plan_next() == ('assess', new)


def test_research_is_due_once_everything_is_assessed(client, snapshot, monkeypatch, searchable):
    idea_id = _idea(client)
    _assess(client, idea_id, monkeypatch)
    assert plan_next() == ('research', idea_id)


def test_research_needs_a_search_provider(client, snapshot, monkeypatch):
    """Otherwise every pass would just record that the web was unavailable."""
    monkeypatch.setattr('backend.research.web.is_search_configured', lambda: False)
    idea_id = _idea(client)
    _assess(client, idea_id, monkeypatch)
    assert plan_next() is None


def test_a_recently_researched_idea_is_on_cooldown(client, snapshot, monkeypatch, searchable):
    idea_id = _idea(client)
    _assess(client, idea_id, monkeypatch)
    now = int(time.time())
    get_db().execute('UPDATE ideas SET researched_at=? WHERE id=?', (now, idea_id))
    get_db().commit()
    assert plan_next(now) is None
    # ...and due again once the cooldown lapses.
    assert plan_next(now + RESEARCH_COOLDOWN_SECONDS + 1) == ('research', idea_id)


# --- tick ---

def test_tick_does_nothing_while_disabled(client, snapshot, monkeypatch):
    monkeypatch.setattr(sched, 'research_enabled', lambda: False)
    _idea(client)
    assert sched.tick() is None


def test_research_is_off_by_default(client):
    """It makes outbound web requests; that is not something to start unasked."""
    assert sched.research_enabled() is False
    get_db().execute('UPDATE settings SET research_enabled=1')
    get_db().commit()
    assert sched.research_enabled() is True


def test_tick_submits_one_task(client, snapshot, monkeypatch):
    monkeypatch.setattr(sched, 'research_enabled', lambda: True)
    import backend.research.research_job as rj
    ran = []
    monkeypatch.setattr(rj, 'run_task', lambda kind, idea_id, cancel: ran.append(kind))

    idea_id = _idea(client)
    assert sched.tick() == ('assess', idea_id)
    assert worker.wait_idle()
    assert ran == ['assess']


def test_tick_defers_while_the_user_is_waiting(client, snapshot, monkeypatch):
    """The whole point: background work yields to an interactive call."""
    monkeypatch.setattr(sched, 'research_enabled', lambda: True)
    _idea(client)
    token = priority.begin('chat.stream')
    assert sched.tick() is None

    priority.end(token)
    # Still inside the quiet period straight after the call ends.
    monkeypatch.setattr(sched, 'QUIET_SECONDS', 3600.0)
    assert sched.tick() is None
    # And due once things have been quiet long enough.
    monkeypatch.setattr(sched, 'QUIET_SECONDS', 0.0)
    assert sched.tick() is not None


def test_tick_does_not_stack_jobs(client, snapshot, monkeypatch):
    monkeypatch.setattr(sched, 'research_enabled', lambda: True)
    monkeypatch.setattr(sched, 'QUIET_SECONDS', 0.0)
    _idea(client)
    release = __import__('threading').Event()
    worker.submit('research', lambda cancel: release.wait(5))
    for _ in range(200):
        if worker.status()['running']:
            break
        time.sleep(0.01)

    assert sched.tick() is None, 'one job at a time'
    release.set()
    assert worker.wait_idle()


def test_scheduler_does_not_start_under_the_reloader_parent(monkeypatch):
    started = []
    monkeypatch.setattr(
        sched.threading, 'Thread',
        lambda *a, **k: started.append(1) or type('T', (), {'start': lambda self: None})(),
    )
    monkeypatch.setenv('FLASK_DEBUG', '1')
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    sched.start_research_scheduler()
    assert started == []


# --- The research task ---

def _stub_research(monkeypatch, articles, sources=None):
    from backend.research import agent
    import backend.ai.idea_research as ir
    monkeypatch.setattr(agent, 'gather', lambda system, user, **kw: {
        'messages': [{'role': 'tool', 'content': 'FSRS models memory as three variables.'}],
        'steps': [{'tool': 'web_search', 'ok': True}],
        'sources': sources if sources is not None else [{'url': 'https://ex.com/a', 'title': 'A'}],
        'turns': 2, 'truncated': False,
    })
    monkeypatch.setattr(ir, 'decide_articles', lambda idea, transcript, existing: articles)


def test_research_writes_and_links_articles(client, snapshot, monkeypatch):
    idea_id = _idea(client)
    _stub_research(monkeypatch, [{
        'slug': 'spaced-repetition', 'title': 'Spaced repetition',
        'summary': 'How FSRS schedules.', 'content': 'Body.', 'note': 'first pass',
    }])
    result = run_research_task(idea_id)

    assert result['articles'] == ['spaced-repetition']
    from backend.research import wiki
    assert [a['slug'] for a in wiki.articles_for_idea(idea_id)] == ['spaced-repetition']
    assert wiki.get_article('spaced-repetition')['content'] == 'Body.'

    row = get_db().execute(
        'SELECT researched_at, research_state FROM ideas WHERE id=?', (idea_id,)
    ).fetchone()
    assert row['researched_at'] and row['research_state'] == 'idle'


def test_research_skips_a_locked_article(client, snapshot, monkeypatch):
    """A locked article is the user's; the pass must step around it, not fail."""
    from backend.research import wiki
    wiki.upsert_article('spaced-repetition', 'Mine', 'Mine.', 'Do not touch.')
    get_db().execute("UPDATE wiki_articles SET locked=1 WHERE slug='spaced-repetition'")
    get_db().commit()

    idea_id = _idea(client)
    _stub_research(monkeypatch, [{
        'slug': 'spaced-repetition', 'title': 'Agent version',
        'summary': 's', 'content': 'Overwritten.',
    }])
    result = run_research_task(idea_id)

    assert result['articles'] == []
    assert wiki.get_article('spaced-repetition')['content'] == 'Do not touch.'
    assert get_db().execute(
        'SELECT research_state FROM ideas WHERE id=?', (idea_id,)
    ).fetchone()['research_state'] == 'idle'


def test_research_finding_nothing_still_marks_the_idea_done(client, snapshot, monkeypatch):
    idea_id = _idea(client)
    _stub_research(monkeypatch, [])
    assert run_research_task(idea_id)['articles'] == []
    assert get_db().execute(
        'SELECT researched_at FROM ideas WHERE id=?', (idea_id,)
    ).fetchone()['researched_at']


def test_a_failed_pass_leaves_the_idea_retryable(client, snapshot, monkeypatch):
    """research_state must not be left 'running' or the planner skips it forever."""
    from backend.research import agent

    def boom(*a, **kw):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(agent, 'gather', boom)
    idea_id = _idea(client)
    with pytest.raises(RuntimeError):
        run_research_task(idea_id)

    row = get_db().execute(
        'SELECT research_state, research_error, researched_at FROM ideas WHERE id=?',
        (idea_id,),
    ).fetchone()
    assert row['research_state'] == 'idle'
    assert row['research_error'] == 'Research failed'
    assert row['researched_at'] is None, 'not on cooldown, so it will be retried'


def test_research_task_on_a_missing_idea(client):
    assert run_research_task('nope')['articles'] == []


# --- Routes ---

def test_manual_research_queues_a_job(client, monkeypatch):
    import backend.routes.ideas as routes
    monkeypatch.setattr(routes, 'is_ai_configured', lambda: True)
    import backend.research.research_job as rj
    monkeypatch.setattr(rj, 'run_task', lambda kind, idea_id, cancel: None)

    idea_id = _idea(client)
    r = client.post(f'/api/ideas/{idea_id}/research')
    assert r.status_code == 202
    assert worker.wait_idle()


def test_manual_research_reports_a_busy_worker(client, monkeypatch):
    import backend.routes.ideas as routes
    monkeypatch.setattr(routes, 'is_ai_configured', lambda: True)
    release = __import__('threading').Event()
    worker.submit('research', lambda cancel: release.wait(5))
    for _ in range(200):
        if worker.status()['running']:
            break
        time.sleep(0.01)

    idea_id = _idea(client)
    assert client.post(f'/api/ideas/{idea_id}/research').status_code == 409
    release.set()
    assert worker.wait_idle()


def test_research_status_and_cancel_routes(client):
    assert client.get('/api/ideas/research/status').get_json()['running'] is False
    assert client.post('/api/ideas/research/cancel').get_json()['cancelled'] is False


def test_manual_research_on_a_missing_idea(client, monkeypatch):
    import backend.routes.ideas as routes
    monkeypatch.setattr(routes, 'is_ai_configured', lambda: True)
    assert client.post('/api/ideas/nope/research').status_code == 404
