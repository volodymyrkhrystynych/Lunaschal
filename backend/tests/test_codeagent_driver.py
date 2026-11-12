"""The driver end to end, with a fake ACP agent and a local stand-in for Docker.

`LocalRuntime` plays the container: it runs commands in the task's work dir
(translating `/work`) and spawns the scripted agent from
fixtures/fake_acp_agent.py there. Everything else — the mirror, out.git, the
bundle, the event log, the approvals — is the real code.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from ulid import ULID

from backend.codeagent import acp, driver, mirror, tasks, worker
from backend.db.connection import get_db
from backend.repos import git as repo_git

FAKE_AGENT = Path(__file__).parent / 'fixtures' / 'fake_acp_agent.py'
ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e', 'GIT_COMMITTER_NAME': 'T',
       'GIT_COMMITTER_EMAIL': 't@e', 'PATH': os.environ['PATH']}


class LocalRuntime:
    def __init__(self, agent_env=None):
        self.agent_env = agent_env or {}
        self.started = []
        self.commands = []

    def start(self, task_id):
        self.started.append(task_id)

    def _cwd(self, task_id):
        return mirror.work_dir(task_id)

    def spawn(self, task_id, command):
        assert command == driver.HARNESS_COMMANDS['pi']
        return subprocess.Popen([sys.executable, str(FAKE_AGENT)], cwd=self._cwd(task_id),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={**ENV, **self.agent_env})

    def run(self, task_id, command, timeout=120, max_bytes=None):
        work = str(self._cwd(task_id))
        cmd = [work if c == '/work' else c for c in command]
        self.commands.append(command)
        return subprocess.run(cmd, cwd=work, capture_output=True, timeout=timeout, env=ENV)

    def stop(self, task_id):
        pass


class NoProxy:
    def __init__(self, path, on_event, lookups=None):
        self.on_event = on_event
        self.lookups = lookups

    def start(self):
        return self

    def stop(self):
        pass


@pytest.fixture
def github(tmp_path):
    seed = tmp_path / 'seed'
    seed.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=seed, check=True, env=ENV)
    (seed / 'a.py').write_text('x = 1\n')
    subprocess.run(['git', 'add', '-A'], cwd=seed, check=True, env=ENV)
    subprocess.run(['git', 'commit', '-qm', 'first'], cwd=seed, check=True, env=ENV)
    bare = tmp_path / 'github.git'
    subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(bare)], check=True, env=ENV)
    return bare


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_ROOT', str(tmp_path / 'agent'))
    monkeypatch.setattr(repo_git, 'assert_clone_url', lambda url: url)
    monkeypatch.setattr(tasks, '_listeners', [])


@pytest.fixture
def repo_id(github):
    rid = str(ULID())
    now = int(time.time())
    get_db().execute(
        "INSERT INTO repos (id, slug, name, remote_url, clone_state, created_at, updated_at, "
        "agent_test_command) VALUES (?, 'demo', 'demo', ?, 'ready', ?, ?, ?)",
        (rid, str(github), now, now, 'echo tests ran; grep -c built a.py'))
    get_db().commit()
    return rid


def make_driver(**agent_env):
    rt = LocalRuntime(agent_env)
    return driver.Driver(rt, proxy_factory=NoProxy), rt


def kinds(task_id):
    return [e['kind'] for e in tasks.events(task_id)]


def test_plan_phase_refuses_writes_and_waits_for_approval(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, rt = make_driver()
    out = d.run(t['id'])
    assert out['status'] == 'awaiting_plan'
    assert '1. Change a.py' in out['plan'] and '(edit was reject)' in out['plan']
    assert out['baseSha'] and out['baseBranch'] == 'main' and out['sessionId'] == 'sess-1'
    approval = tasks.pending_approval(t['id'])
    assert approval['kind'] == 'plan' and approval['request']['plan'] == out['plan']
    perm = [e['payload'] for e in tasks.events(t['id']) if e['kind'] == 'permission']
    assert perm == [{'title': 'edit something', 'kind': 'edit', 'decision': 'refused'}]
    assert {'thought', 'plan', 'message'} <= set(kinds(t['id']))


def test_a_stray_edit_in_the_plan_phase_is_reverted(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver(FAKE_STRAY_EDIT='1')
    d.run(t['id'])
    assert not (mirror.work_dir(t['id']) / 'stray.txt').exists()
    notes = [e['payload'].get('note') for e in tasks.events(t['id']) if e['kind'] == 'status']
    assert any('reverted' in (n or '') for n in notes)


def test_build_phase_commits_exports_tests_and_finishes(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, rt = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build')
    get_db().execute("UPDATE agent_tasks SET status='queued' WHERE id=?", (t['id'],))
    get_db().commit()

    out = d.run(t['id'])

    assert out['status'] == 'done'
    assert out['summary'] == 'Done: changed a.py.'
    assert out['testExit'] == 0 and 'tests ran' in out['testOutput']
    assert [f['path'] for f in out['diffStat']] == ['a.py']
    # The diff came from out.git, and the head matches the agent's commit.
    d_ = mirror.diff(t['id'], out['baseSha'])
    assert '+# built (allow)' in d_['patch']
    assert 'Uncommitted work, committed by Lunaschal' in subprocess.run(
        ['git', 'log', '-1', '--format=%B', out['headSha']], cwd=mirror.out_dir(t['id']),
        capture_output=True, text=True).stdout
    assert 'test' in kinds(t['id'])


def test_a_question_parks_the_task_on_needs_input(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build', pending_input='Do it, and ASK if unsure')
    out = d.run(t['id'])
    assert out['status'] == 'needs_input'
    q = tasks.pending_approval(t['id'])
    assert q['kind'] == 'question' and q['request']['question'] == 'Should b.py change too?'
    # The work so far is already reviewable.
    assert out['diffStat'][0]['path'] == 'a.py'
    assert tasks.get(t['id'])['pendingInput'] is None


def test_a_session_is_resumed_when_the_agent_can_load_it(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver(FAKE_LOAD_SESSION='1')
    d.run(t['id'])
    before = len(tasks.events(t['id']))
    tasks.update(t['id'], phase='build')
    d.run(t['id'])
    # The replayed history ('replayed') was not recorded as new events.
    texts = [e['payload'].get('text') for e in tasks.events(t['id'])[before:]]
    assert 'replayed' not in texts


def test_a_crashed_agent_is_an_error_with_its_stderr(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver(FAKE_CRASH='1')
    out = d.run(t['id'])
    assert out['status'] == 'error'
    assert 'model went away' in out['error']


def test_no_changes_means_done_without_a_diff(repo_id, monkeypatch):
    t = tasks.create(repo_id, 'Change a.py')
    d, rt = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build')
    # Make the agent's write vanish: the build "changes nothing".
    real_run = rt.run

    def run(task_id, command, timeout=120, max_bytes=None):
        if command[:3] == ['git', '-C', '/work'] and command[3] == 'status':
            subprocess.run(['git', 'checkout', '--', '.'], cwd=mirror.work_dir(task_id), env=ENV)
        return real_run(task_id, command, timeout, max_bytes)
    rt.run = run
    out = d.run(t['id'])
    assert out['status'] == 'done' and out['diffStat'] is None


def requeue(task_id, **fields):
    tasks.update(task_id, **fields)
    get_db().execute("UPDATE agent_tasks SET status='queued' WHERE id=?", (task_id,))
    get_db().commit()


def test_a_question_is_answered_read_only(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver()
    out = d.run(t['id'])
    assert out['status'] == 'answered'
    assert out['summary'] == 'a.py sets x = 1.\n(edit was reject)'
    assert out['plan'] is None and out['headSha'] is None
    # Nothing to approve: an answer is read, not signed off.
    assert tasks.pending_approval(t['id']) is None
    statuses = [e['payload'].get('status') for e in tasks.events(t['id']) if e['kind'] == 'status']
    assert statuses == ['answering', 'answered']
    perm = [e['payload'] for e in tasks.events(t['id']) if e['kind'] == 'permission']
    assert perm == [{'title': 'edit something', 'kind': 'edit', 'decision': 'refused'}]


def test_a_stray_edit_while_answering_is_reverted(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver(FAKE_STRAY_EDIT='1')
    assert d.run(t['id'])['status'] == 'answered'
    assert not (mirror.work_dir(t['id']) / 'stray.txt').exists()
    notes = [e['payload'].get('note') for e in tasks.events(t['id']) if e['kind'] == 'status']
    assert 'the question step changed files; they were reverted' in notes


def test_a_follow_up_question_stays_a_question(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver(FAKE_LOAD_SESSION='1')
    d.run(t['id'])
    requeue(t['id'], pending_input='And who reads it?')
    task = tasks.get(t['id'])
    assert d._prompt_for(task, fresh=False).startswith('And who reads it?')
    assert 'not a change' in d._prompt_for(task, fresh=False)
    out = d.run(t['id'])
    assert out['status'] == 'answered' and out['pendingInput'] is None


def test_an_answer_becomes_a_plan_in_the_same_session(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver(FAKE_LOAD_SESSION='1')
    d.run(t['id'])
    requeue(t['id'], phase='plan', pending_input='Make it 2')
    task = tasks.get(t['id'])
    prompt = d._prompt_for(task, fresh=False)
    assert prompt.startswith('Now the user wants a change:\nMake it 2')
    # A resumed session already holds the conversation; it is not repeated.
    assert 'Earlier, the user asked' not in prompt

    out = d.run(t['id'])
    assert out['status'] == 'awaiting_plan' and out['plan'].startswith('## Plan')
    assert tasks.pending_approval(t['id'])['kind'] == 'plan'
    # From here it is an ordinary plan: a revision is a revision.
    requeue(t['id'], pending_input='Also a test')
    assert d._prompt_for(tasks.get(t['id']), fresh=False).startswith('The user reviewed your plan')


def test_a_fresh_session_planning_from_an_answer_is_given_the_conversation(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver()
    d.run(t['id'])
    requeue(t['id'], phase='plan')
    prompt = d._prompt_for(tasks.get(t['id']), fresh=True)
    assert 'Earlier, the user asked:\nWhat does a.py set?' in prompt
    assert 'a.py sets x = 1.' in prompt
    assert driver.PLAN_FROM_ANSWER_DEFAULT in prompt


def test_a_plan_request_survives_a_run_that_died(repo_id):
    # The run clears pending_input before the agent's turn; an interrupted run
    # must not fall back to the generic request.
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver()
    d.run(t['id'])
    worker.plan_from_answer(t['id'], 'Make it 2')
    tasks.update(t['id'], pending_input=None)
    prompt = d._prompt_for(tasks.get(t['id']), fresh=True)
    assert 'Now the user wants a change:\nMake it 2' in prompt
    assert driver.PLAN_FROM_ANSWER_DEFAULT not in prompt


def test_a_fresh_session_after_the_plan_is_told_the_change_not_only_the_question(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver()
    d.run(t['id'])
    worker.plan_from_answer(t['id'], 'Make it 2')
    d.run(t['id'])
    requeue(t['id'], pending_input='Also a test')
    revise = d._prompt_for(tasks.get(t['id']), fresh=True)
    assert 'Task:\nMake it 2\n\nThis grew out of a question about the code:\nWhat does a.py set?' in revise
    requeue(t['id'], phase='build', pending_input=None)
    build = d._prompt_for(tasks.get(t['id']), fresh=True)
    assert 'The task:\nMake it 2' in build


def test_planning_from_a_follow_up_names_every_question_and_the_latest_answer(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver()
    d.run(t['id'])
    worker.send_message(t['id'], 'And who reads it?')
    tasks.update(t['id'], pending_input=None, summary='b.py reads it.')
    get_db().execute("UPDATE agent_tasks SET status='answered' WHERE id=?", (t['id'],))
    get_db().commit()
    worker.plan_from_answer(t['id'])
    prompt = d._prompt_for(tasks.get(t['id']), fresh=True)
    assert 'Earlier, the user asked:\nWhat does a.py set?\n\nThen:\nAnd who reads it?' in prompt
    assert 'the latest answer was:\nb.py reads it.' in prompt


def test_a_fresh_follow_up_question_is_given_the_earlier_answer(repo_id):
    t = tasks.create(repo_id, 'What does a.py set?', mode='ask')
    d, _ = make_driver()
    d.run(t['id'])
    worker.send_message(t['id'], 'And who reads it?')
    prompt = d._prompt_for(tasks.get(t['id']), fresh=True)
    assert 'You answered that already:\na.py sets x = 1.' in prompt
    assert prompt.endswith('The user then asked:\nAnd who reads it?')


def test_permission_choice():
    opts = [{'optionId': 'a', 'kind': 'allow_once'}, {'optionId': 'r', 'kind': 'reject_once'}]
    assert driver._choose(opts, True) == 'a'
    assert driver._choose(opts, False) == 'r'
    assert driver._choose([{'optionId': 'only', 'kind': 'allow_always'}], False) is None


@pytest.mark.parametrize('text, q', [
    ('All done.', None),
    ('Changed it.\nQUESTION: Keep the old API?', 'Keep the old API?'),
    ('QUESTION: first\nmore\nQUESTION: second', 'second'),
])
def test_question_in(text, q):
    assert driver.question_in(text) == q


def test_acp_request_to_a_dead_agent_raises_closed():
    proc = subprocess.Popen([sys.executable, '-c', 'import sys; sys.exit(0)'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.wait()
    client = acp.AcpClient(proc, lambda u: None, lambda p: None)
    with pytest.raises(acp.AcpError):
        client.initialize()


def test_acp_answers_unsupported_methods():
    script = (
        'import json,sys\n'
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':7,'method':'fs/read_text_file','params':{}})+'\\n')\n"
        'sys.stdout.flush()\n'
        'reply = {}\n'
        "while reply.get('id') != 7:\n"
        '    reply = json.loads(sys.stdin.readline())\n'
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':1,'result':{'echo': reply}})+'\\n')\n"
        'sys.stdout.flush()\n'
        'sys.stdin.readline()\n'
    )
    proc = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    client = acp.AcpClient(proc, lambda u: None, lambda p: None)
    result = client.request('anything', {}, timeout=10)
    assert result['echo']['id'] == 7 and result['echo']['error']['code'] == -32601
    client.close()


# ------------------------------------------------- the driver's own test run

def _build_task(repo_id, **fields):
    t = tasks.create(repo_id, 'Change a.py')
    tasks.update(t['id'], phase='build', plan='1. Change a.py', **fields)
    return tasks.get(t['id'])


def test_the_build_prompt_names_the_command_that_decides_pass_or_fail(repo_id):
    d, _ = make_driver()
    text = d._prompt_for(_build_task(repo_id), fresh=False)
    assert 'echo tests ran; grep -c built a.py' in text
    assert 'Run that same command before you say the tests pass' in text


def test_the_default_command_is_named_when_the_repo_has_none(repo_id):
    get_db().execute("UPDATE repos SET agent_test_command='' WHERE id=?", (repo_id,))
    get_db().commit()
    d, _ = make_driver()
    assert driver.DEFAULT_TEST_SCRIPT in d._prompt_for(_build_task(repo_id), fresh=False)


def test_a_follow_up_after_a_failed_run_carries_what_failed(repo_id):
    d, _ = make_driver()
    output = ('What failed:\n== backend test batch 2/21 (15 files) ==\n'
              'FAILED backend/tests/test_b.py::test_two - boom\n\n'
              '── end of the output ──\n5/21 batch(es) had failures')
    task = _build_task(repo_id, pending_input='Tests still failed though')
    tasks.append_event(task['id'], 'test', {'exit': 1, 'tail': output})
    text = d._prompt_for(task, fresh=False)
    assert 'Lunaschal ran the test command itself (at the end of an earlier turn), ' \
        'it FAILED (exit 1)' in text
    assert 'FAILED backend/tests/test_b.py::test_two - boom' in text
    assert text.index('test_b.py::test_two') < text.index('Tests still failed though')
    assert 'echo tests ran; grep -c built a.py' in text


def test_a_follow_up_after_a_passing_run_says_so_and_one_with_no_run_says_nothing(repo_id):
    d, _ = make_driver()
    task = _build_task(repo_id, pending_input='Rename it')
    tasks.append_event(task['id'], 'test', {'exit': 0, 'tail': 'ok'})
    passed = d._prompt_for(task, fresh=False)
    assert 'it passed (exit 0)' in passed
    none = d._prompt_for(_build_task(repo_id, pending_input='Rename it'), fresh=False)
    assert 'Lunaschal ran the test command' not in none


def test_a_failing_run_is_reported_failures_first(repo_id):
    batches = ['== backend test batch 1/2 (1 files) ==',
               'FAILED backend/tests/test_b.py::test_two - boom',
               '==== 1 failed in 0.1s ====',
               '== backend test batch 2/2 (1 files) ==']
    batches += ['==== 9 passed in 0.1s ===='] * 400
    batches += ['1/2 batch(es) had failures']
    script = 'printf "%s\\n" ' + ' '.join(f"'{l}'" for l in batches) + '; exit 1'
    get_db().execute('UPDATE repos SET agent_test_command=? WHERE id=?', (script, repo_id))
    get_db().commit()
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build')
    out = d.run(t['id'])
    assert out['testExit'] == 1
    assert out['testOutput'].startswith('What failed:\n== backend test batch 1/2')
    event = [e for e in tasks.events(t['id']) if e['kind'] == 'test'][-1]['payload']
    assert 'test_b.py::test_two' in event['tail'] and len(event['tail']) <= driver.TEST_EVENT_CHARS
    assert event['tail'].rstrip().endswith('1/2 batch(es) had failures')


def test_a_turn_ending_in_a_question_drops_the_old_test_verdict(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build', pending_input='Do it, and ASK if unsure',
                 test_exit=1, test_output='old failure')
    tasks.append_event(t['id'], 'test', {'exit': 1, 'tail': 'FAILED t.py::old - boom'})
    out = d.run(t['id'])
    assert out['status'] == 'needs_input'
    assert out['testExit'] is None and out['testOutput'] is None
    # The run is still on record, and the answer to the question carries it.
    tasks.update(t['id'], pending_input='Fix the old failure')
    text = d._prompt_for(tasks.get(t['id']), fresh=False)
    assert 'FAILED t.py::old - boom' in text


def test_a_cancelled_or_failed_follow_up_drops_the_old_verdict(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build', pending_input='Try again',
                 test_exit=1, test_output='old failure')
    d.runtime.spawn = lambda *a: (_ for _ in ()).throw(RuntimeError('agent gone'))
    out = d.run(t['id'])
    assert out['status'] == 'error'
    assert out['testExit'] is None and out['testOutput'] is None


def test_a_multi_line_test_command_is_indented_as_one_block(repo_id):
    get_db().execute('UPDATE repos SET agent_test_command=? WHERE id=?',
                     ('cd app\nnpm ci\nnpm test', repo_id))
    get_db().commit()
    d, _ = make_driver()
    text = d._prompt_for(_build_task(repo_id), fresh=False)
    assert '    cd app\n    npm ci\n    npm test' in text


def test_the_test_command_is_read_once_per_turn(repo_id):
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build')
    reads = []
    real = d._test_script
    d._test_script = lambda task: reads.append(1) or real(task)
    d.run(t['id'])
    assert len(reads) == 1


def test_stderr_stays_in_the_batch_that_wrote_it(repo_id):
    script = ("echo '== backend test batch 1/2 (1 files) =='; "
              "echo 'Fatal Python error: Aborted' >&2; "
              "echo '== backend test batch 2/2 (1 files) =='; "
              "echo '==== 3 passed in 0.1s ===='; "
              "echo '1/2 batch(es) had failures'; exit 1")
    get_db().execute('UPDATE repos SET agent_test_command=? WHERE id=?', (script, repo_id))
    get_db().commit()
    t = tasks.create(repo_id, 'Change a.py')
    d, _ = make_driver()
    d.run(t['id'])
    tasks.update(t['id'], phase='build')
    out = d.run(t['id'])
    head = out['testOutput'].partition('── end of the output ──')[0]
    assert 'batch 1/2' in head and 'Fatal Python error' in head
    assert 'batch 2/2' not in head
