"""The Code tab's rows: lifecycle, event order, approvals, restart recovery."""
import pytest

from backend.codeagent import tasks
from backend.db import connection
from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def no_listeners(monkeypatch):
    monkeypatch.setattr(tasks, '_listeners', [])


@pytest.fixture
def task():
    return tasks.create('repo1', 'Fix the flaky test in test_foo.py\nMore detail here.')


def test_create_queues_and_records_the_prompt(task):
    assert task['status'] == 'queued'
    assert task['title'] == 'Fix the flaky test in test_foo.py'
    assert task['harness'] == 'pi'
    [ev] = tasks.events(task['id'])
    assert ev['kind'] == 'user' and ev['payload']['text'].startswith('Fix the flaky')


@pytest.mark.parametrize('prompt, harness', [('  ', 'pi'), ('x', 'claude')])
def test_create_rejects(prompt, harness):
    with pytest.raises(ValueError):
        tasks.create('r', prompt, harness=harness)


def test_events_are_dense_and_pollable(task):
    for i in range(3):
        tasks.append_event(task['id'], 'message', {'text': str(i)})
    seqs = [e['seq'] for e in tasks.events(task['id'])]
    assert seqs == [1, 2, 3, 4]
    assert [e['payload']['text'] for e in tasks.events(task['id'], after=2)] == ['1', '2']


def test_set_status_stamps_times_logs_and_notifies(task):
    heard = []
    tasks.add_listener(lambda t, s: heard.append(s))
    tasks.set_status(task['id'], 'planning')
    t = tasks.set_status(task['id'], 'awaiting_plan', plan='1. do it')
    assert t['startedAt'] and t['plan'] == '1. do it'
    t = tasks.set_status(task['id'], 'done', diff_stat=[{'path': 'a', 'added': 1, 'removed': 0}])
    assert t['finishedAt'] and t['diffStat'][0]['path'] == 'a'
    assert heard[-2:] == ['awaiting_plan', 'done']
    kinds = [e['payload'].get('status') for e in tasks.events(task['id']) if e['kind'] == 'status']
    assert kinds == ['planning', 'awaiting_plan', 'done']


def test_a_failing_listener_does_not_fail_the_transition(task):
    def boom(t, s):
        raise RuntimeError('apns down')
    tasks.add_listener(boom)
    assert tasks.set_status(task['id'], 'error', error='x')['status'] == 'error'


def test_update_refuses_unknown_columns(task):
    with pytest.raises(ValueError):
        tasks.update(task['id'], status='done')


def test_one_pending_approval_at_a_time(task):
    first = tasks.open_approval(task['id'], 'plan', {'plan': 'a'})
    second = tasks.open_approval(task['id'], 'question', {'text': 'which?'})
    pending = tasks.pending_approval(task['id'])
    assert pending['id'] == second and pending['request'] == {'text': 'which?'}
    with pytest.raises(tasks.BadTransition):
        tasks.answer_approval(first, 'approved')
    tasks.answer_approval(second, 'answered', 'the second one')
    assert tasks.pending_approval(task['id']) is None
    with pytest.raises(tasks.BadTransition):
        tasks.answer_approval(second, 'answered', 'again')


def test_next_queued_is_oldest_first():
    a = tasks.create('r', 'first')
    tasks.create('r', 'second')
    assert tasks.next_queued()['id'] == a['id']


def test_restart_interrupts_in_flight_tasks_only():
    states = ['queued', 'planning', 'running', 'testing', 'awaiting_plan', 'needs_input', 'done']
    ids = {}
    for s in states:
        t = tasks.create('r', s)
        get_db().execute('UPDATE agent_tasks SET status=? WHERE id=?', (s, t['id']))
        ids[s] = t['id']
    get_db().commit()
    connection._reset_stale_agent_tasks(get_db())
    after = {s: tasks.get(i)['status'] for s, i in ids.items()}
    assert after == {'queued': 'queued', 'planning': 'interrupted', 'running': 'interrupted',
                     'testing': 'interrupted', 'awaiting_plan': 'awaiting_plan',
                     'needs_input': 'needs_input', 'done': 'done'}


def test_settings_and_repo_columns_exist():
    row = get_db().execute('SELECT github_agent_token, code_agent_harness FROM settings').fetchone()
    assert row['code_agent_harness'] == 'pi'
    cols = {r[1] for r in get_db().execute('PRAGMA table_info(repos)')}
    assert 'agent_test_command' in cols


def test_a_question_starts_in_the_ask_phase():
    t = tasks.create('r', 'Where are pushes sent from?', mode='ask')
    assert t['phase'] == 'ask' and t['status'] == 'queued'
    assert tasks.create('r', 'Change it')['phase'] == 'plan'
    with pytest.raises(ValueError):
        tasks.create('r', 'x', mode='build')


def test_answering_is_in_flight_and_answered_waits(monkeypatch):
    seen = []
    tasks.add_listener(lambda t, s: seen.append(s))
    t = tasks.create('r', 'Why?', mode='ask')
    assert tasks.set_status(t['id'], 'answering')['startedAt'] is not None
    tasks.set_status(t['id'], 'answered', summary='Because.')
    assert seen == ['answered']

    get_db().execute("UPDATE agent_tasks SET status='answering' WHERE id=?", (t['id'],))
    get_db().commit()
    connection._reset_stale_agent_tasks(get_db())
    assert tasks.get(t['id'])['status'] == 'interrupted'


OLD_AGENT_TASKS = """
CREATE TABLE agent_tasks (
    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL, harness TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','planning','awaiting_plan','running',
                         'needs_input','testing','done','interrupted','error',
                         'cancelled','pushed','discarded')),
    phase TEXT NOT NULL DEFAULT 'plan' CHECK(phase IN ('plan','build')),
    plan TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE agent_task_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);
INSERT INTO agent_tasks (id, repo_id, prompt, harness, status, phase, plan, created_at, updated_at)
    VALUES ('t1', 'r', 'Fix it', 'pi', 'done', 'build', '1. fix', 1, 2);
INSERT INTO agent_task_events (id, task_id, seq, kind, created_at) VALUES ('e1', 't1', 1, 'user', 1);
"""


def test_an_older_table_is_rebuilt_to_accept_questions(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / 'old.db', isolation_level=None)
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(OLD_AGENT_TASKS)

    connection._ensure_agent_task_ask(db)
    connection._ensure_agent_task_ask(db)  # idempotent

    # The rows survived, and the cascade did not take the event log with the DROP.
    assert db.execute("SELECT status, phase, plan FROM agent_tasks").fetchall() == [
        ('done', 'build', '1. fix')]
    assert db.execute('SELECT COUNT(*) FROM agent_task_events').fetchone()[0] == 1
    db.execute("UPDATE agent_tasks SET status='answered', phase='ask'")
    cols = {r[1] for r in db.execute('PRAGMA table_info(agent_tasks)')}
    assert {'summary', 'session_id', 'pending_input'} <= cols
    indexes = {r[1] for r in db.execute("PRAGMA index_list('agent_tasks')")}
    assert {'idx_agent_tasks_status', 'idx_agent_tasks_updated'} <= indexes
