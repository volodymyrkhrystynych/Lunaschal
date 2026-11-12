"""The Code tab's HTTP surface and the worker's state transitions."""
import time

import pytest
from ulid import ULID

from backend.codeagent import github, mirror, tasks, worker
from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_ROOT', str(tmp_path / 'agent'))
    monkeypatch.setattr(tasks, '_listeners', [])
    monkeypatch.setattr(worker, '_runtime', None)


@pytest.fixture
def repo_id(client):
    rid = str(ULID())
    now = int(time.time())
    get_db().execute(
        "INSERT INTO repos (id, slug, name, remote_url, clone_state, is_default, created_at, "
        "updated_at) VALUES (?, 'lunaschal', 'Lunaschal', 'https://github.com/me/Lunaschal', "
        "'ready', 1, ?, ?)", (rid, now, now))
    get_db().commit()
    return rid


def at(task_id, status, **fields):
    """Put a task into a state directly, the way the driver would leave it."""
    get_db().execute('UPDATE agent_tasks SET status=? WHERE id=?', (status, task_id))
    get_db().commit()
    if fields:
        tasks.update(task_id, **fields)


def test_create_needs_a_repo_and_a_prompt(client):
    assert client.post('/api/code/tasks', json={'prompt': 'x'}).status_code == 400


def test_create_uses_the_default_repo_and_the_configured_agent(client, repo_id):
    get_db().execute("UPDATE settings SET code_agent_harness='omp'")
    get_db().commit()
    resp = client.post('/api/code/tasks', json={'prompt': 'Add a test'})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['status'] == 'queued' and body['repoId'] == repo_id and body['harness'] == 'omp'
    assert body['pending'] is None and body['running'] is False


def test_create_refuses_when_the_disk_is_nearly_full(client, repo_id, monkeypatch):
    monkeypatch.setattr(mirror, 'free_bytes', lambda: 1024)
    resp = client.post('/api/code/tasks', json={'prompt': 'x'})
    assert resp.status_code == 400 and '10 GB' in resp.get_json()['error']


def test_list_and_poll_events(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    tasks.append_event(t['id'], 'message', {'text': 'hello'})
    listed = client.get('/api/code/tasks').get_json()
    assert listed['tasks'][0]['id'] == t['id'] and listed['waiting'] == 0
    got = client.get(f"/api/code/tasks/{t['id']}?after=1").get_json()
    assert [e['payload'] for e in got['events']] == [{'text': 'hello'}]
    assert client.get('/api/code/tasks/nope').status_code == 404


def test_approving_the_plan_queues_the_build(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    at(t['id'], 'awaiting_plan', plan='1. fix')
    tasks.open_approval(t['id'], 'plan', {'plan': '1. fix'})
    assert client.get('/api/code/tasks').get_json()['waiting'] == 1
    body = client.post(f"/api/code/tasks/{t['id']}/plan", json={'approve': True}).get_json()
    assert body['status'] == 'queued' and body['phase'] == 'build' and body['pending'] is None
    # Twice is refused, not double-queued.
    assert client.post(f"/api/code/tasks/{t['id']}/plan", json={'approve': True}).status_code == 409


def test_revising_the_plan_requeues_planning_with_the_note(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    at(t['id'], 'awaiting_plan', plan='1. fix')
    tasks.open_approval(t['id'], 'plan', {'plan': '1. fix'})
    body = client.post(f"/api/code/tasks/{t['id']}/plan", json={'text': 'Also add a test'}).get_json()
    assert body['status'] == 'queued' and body['phase'] == 'plan'
    assert body['pendingInput'] == 'Also add a test'
    assert client.post(f"/api/code/tasks/{t['id']}/plan", json={'text': ''}).status_code == 409


def test_messages(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    at(t['id'], 'running', plan='p', phase='build')
    assert client.post(f"/api/code/tasks/{t['id']}/messages", json={'text': 'hi'}).status_code == 409
    at(t['id'], 'needs_input')
    q = tasks.open_approval(t['id'], 'question', {'question': 'Which?'})
    body = client.post(f"/api/code/tasks/{t['id']}/messages", json={'text': 'The first'}).get_json()
    assert body['status'] == 'queued' and body['phase'] == 'build'
    assert body['pendingInput'] == 'The first'
    row = get_db().execute('SELECT status, response FROM agent_approvals WHERE id=?', (q,)).fetchone()
    assert (row['status'], row['response']) == ('answered', 'The first')


def test_asking_a_question(client, repo_id):
    body = client.post('/api/code/tasks', json={'prompt': 'Where is X?', 'mode': 'ask'}).get_json()
    assert body['phase'] == 'ask' and body['status'] == 'queued'
    resp = client.post('/api/code/tasks', json={'prompt': 'x', 'mode': 'build'})
    assert resp.status_code == 400


def test_a_follow_up_to_an_answer_is_another_question(client, repo_id):
    t = tasks.create(repo_id, 'Where is X?', mode='ask')
    at(t['id'], 'answered', summary='In y.py.')
    body = client.post(f"/api/code/tasks/{t['id']}/messages", json={'text': 'And Z?'}).get_json()
    assert body['status'] == 'queued' and body['phase'] == 'ask'
    assert body['pendingInput'] == 'And Z?'


def test_planning_from_an_answer(client, repo_id):
    t = tasks.create(repo_id, 'Where is X?', mode='ask')
    at(t['id'], 'answering')
    assert client.post(f"/api/code/tasks/{t['id']}/make-plan", json={}).status_code == 409
    at(t['id'], 'answered', summary='In y.py.')
    body = client.post(f"/api/code/tasks/{t['id']}/make-plan",
                       json={'text': 'Move it to z.py'}).get_json()
    assert body['status'] == 'queued' and body['phase'] == 'plan'
    assert body['pendingInput'] == 'Move it to z.py'
    last = tasks.events(t['id'])[-2]['payload']
    assert last == {'text': 'Move it to z.py', 'action': 'plan', 'request': 'Move it to z.py'}
    # The title becomes the PR's, so it names the change, not the question.
    assert body['title'] == 'Move it to z.py'
    # Twice is refused: it is a plan now.
    assert client.post(f"/api/code/tasks/{t['id']}/make-plan", json={}).status_code == 409


def test_planning_from_an_answer_with_nothing_said(client, repo_id):
    t = tasks.create(repo_id, 'Where is X?', mode='ask')
    at(t['id'], 'answered', summary='In y.py.')
    body = client.post(f"/api/code/tasks/{t['id']}/make-plan", json={}).get_json()
    assert body['phase'] == 'plan' and body['pendingInput'] is None
    assert body['title'] == 'Where is X?'


def test_an_answered_question_can_be_discarded_not_pushed(client, repo_id):
    t = tasks.create(repo_id, 'Where is X?', mode='ask')
    at(t['id'], 'answered', summary='In y.py.')
    assert client.post(f"/api/code/tasks/{t['id']}/push").status_code == 409
    assert client.post(f"/api/code/tasks/{t['id']}/discard").get_json()['status'] == 'discarded'


def test_continue_an_interrupted_build(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    at(t['id'], 'interrupted', phase='build', plan='p', error='Interrupted by an app restart.')
    body = client.post(f"/api/code/tasks/{t['id']}/continue").get_json()
    assert body['status'] == 'queued' and body['error'] is None
    assert body['pendingInput'] == worker.CONTINUE_TEXT
    assert client.post(f"/api/code/tasks/{t['id']}/continue").status_code == 409


def test_cancel_and_discard_remove_the_workspace(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    mirror.work_dir(t['id']).mkdir(parents=True)
    body = client.post(f"/api/code/tasks/{t['id']}/cancel").get_json()
    assert body['status'] == 'cancelled'
    assert not mirror.task_dir(t['id']).exists()
    t2 = tasks.create(repo_id, 'Other')
    at(t2['id'], 'done')
    assert client.post(f"/api/code/tasks/{t2['id']}/discard").get_json()['status'] == 'discarded'


def test_cancel_signals_the_running_task(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    at(t['id'], 'running')
    import threading
    ev = threading.Event()
    worker._current.update(task_id=t['id'], cancel=ev)
    try:
        client.post(f"/api/code/tasks/{t['id']}/cancel")
        assert ev.is_set()
        assert client.post(f"/api/code/tasks/{t['id']}/discard").status_code == 409
    finally:
        worker._current.update(task_id=None, cancel=None)


def test_push_opens_a_pr_as_the_machine_account(client, repo_id, monkeypatch):
    t = tasks.create(repo_id, 'Fix it')
    at(t['id'], 'done', head_sha='a' * 40, base_branch='main', test_exit=0)
    get_db().execute("UPDATE settings SET github_agent_token='machine-token'")
    get_db().commit()
    pushed, opened = [], []
    monkeypatch.setattr(mirror, 'push', lambda tid, url, tok, base: pushed.append(
        (tid, url, tok, base)) or f'agent/{tid}')

    def fake_pr(token, owner, repo, **kw):
        opened.append((token, owner, repo, kw))
        return 'https://github.com/me/Lunaschal/pull/9'
    monkeypatch.setattr(github, 'open_pull_request', fake_pr)

    body = client.post(f"/api/code/tasks/{t['id']}/push").get_json()

    assert body['status'] == 'pushed' and body['prUrl'].endswith('/pull/9')
    assert pushed == [(t['id'], 'https://github.com/me/Lunaschal', 'machine-token', 'main')]
    token, owner, repo, kw = opened[0]
    assert (owner, repo, kw['head'], kw['base']) == ('me', 'Lunaschal', f"agent/{t['id']}", 'main')
    assert 'tests passed' not in kw['body'] and '**passed**' in kw['body']


def test_push_is_refused_without_work_or_a_token(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    assert client.post(f"/api/code/tasks/{t['id']}/push").status_code == 409
    at(t['id'], 'done')
    assert 'did not change' in client.post(f"/api/code/tasks/{t['id']}/push").get_json()['error']
    at(t['id'], 'done', head_sha='a' * 40, base_branch='main')
    mirror.out_dir(t['id']).mkdir(parents=True)
    assert 'token' in client.post(f"/api/code/tasks/{t['id']}/push").get_json()['error']


def test_diff_without_work_is_empty(client, repo_id):
    t = tasks.create(repo_id, 'Fix it')
    assert client.get(f"/api/code/tasks/{t['id']}/diff").get_json()['files'] == []


def test_run_next_hands_the_oldest_queued_task_to_the_driver(client, repo_id, monkeypatch):
    ran = []

    class FakeDriver:
        def run(self, task_id, cancel):
            ran.append(task_id)
            assert worker.current_task() == task_id
            return tasks.set_status(task_id, 'awaiting_plan')
    monkeypatch.setattr(worker, '_driver', FakeDriver())
    a = tasks.create(repo_id, 'first')
    tasks.create(repo_id, 'second')
    assert worker.run_next() and ran == [a['id']]
    assert worker.current_task() is None


def test_sweep_discards_old_untouched_tasks(client, repo_id):
    old = tasks.create(repo_id, 'old')
    at(old['id'], 'done')
    fresh = tasks.create(repo_id, 'fresh')
    at(fresh['id'], 'done')
    long_ago = int(time.time()) - worker.RETENTION_SECONDS - 10
    get_db().execute('UPDATE agent_tasks SET updated_at=? WHERE id=?', (long_ago, old['id']))
    get_db().commit()
    assert worker.sweep() == 1
    assert tasks.get(old['id'])['status'] == 'discarded'
    assert tasks.get(fresh['id'])['status'] == 'done'


def test_pr_body_reports_the_measured_tests():
    body = github.pr_body({'prompt': 'p', 'plan': 'plan', 'summary': 's', 'testExit': 1,
                           'harness': 'omp'})
    assert 'failed (exit 1)' in body and '`omp`' in body


def test_a_pr_from_a_question_states_the_change_and_the_question(repo_id):
    t = tasks.create(repo_id, 'Where is X?', mode='ask')
    at(t['id'], 'answered', summary='In y.py.')
    worker.send_message(t['id'], 'And Z?')
    at(t['id'], 'answered', summary='In z.py.')
    worker.plan_from_answer(t['id'], 'Move X next to Z')
    body = github.pr_body(tasks.get(t['id']), tasks.question_thread(t['id']))
    assert body.startswith('## Task\n\nMove X next to Z\n\n## Question\n\nWhere is X?\n\nThen:\n\nAnd Z?')
    # A task that began as a change is unchanged.
    plain = tasks.create(repo_id, 'Add a test')
    assert tasks.question_thread(plain['id']) is None
    assert github.pr_body(tasks.get(plain['id'])).startswith('## Task\n\nAdd a test')
