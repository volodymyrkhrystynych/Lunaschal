"""The Code tab's worker and the actions a person can take on a task.

One task phase runs at a time: the GPU lane has two slots and chat needs one,
so a second concurrent agent would only make both slower. The queue is the
`agent_tasks` table itself (status `queued`, oldest first), so it survives a
restart; a separate thread from backend/research/worker.py's, so a long agent
run never holds up a repo pull.

Every action is a state transition guarded by the state it starts from, and
ends by waking the worker if it queued something.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from backend.codeagent import mirror, tasks

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 10 * 1024 ** 3
RETENTION_SECONDS = 14 * 24 * 3600
SWEEP_SECONDS = 3600
IDLE_WAIT = 30.0

# States from which the user may say something to the agent.
MESSAGEABLE = frozenset({'needs_input', 'done', 'error', 'interrupted', 'awaiting_plan'})
# States a task can be thrown away from (anything not running).
DISCARDABLE = frozenset({'awaiting_plan', 'needs_input', 'done', 'error', 'interrupted',
                         'cancelled', 'queued'})

_wake = threading.Event()
_lock = threading.Lock()
_current: dict = {'task_id': None, 'cancel': None}
_thread: threading.Thread | None = None
_driver = None
_runtime = None


class ActionRefused(ValueError):
    pass


def configure(driver, runtime) -> None:
    """Install the driver and runtime. `start` does this with the Docker ones;
    tests install fakes."""
    global _driver, _runtime
    _driver, _runtime = driver, runtime


def wake() -> None:
    _wake.set()


def current_task() -> str | None:
    return _current['task_id']


# ----------------------------------------------------------------- the loop

def run_next() -> bool:
    """Run one queued phase, if any. Returns whether it ran something."""
    task = tasks.next_queued()
    if task is None:
        return False
    cancel = threading.Event()
    with _lock:
        _current.update(task_id=task['id'], cancel=cancel)
    try:
        result = _driver.run(task['id'], cancel)
        if result and result.get('status') == 'cancelled':
            cleanup(task['id'])
    finally:
        with _lock:
            _current.update(task_id=None, cancel=None)
    return True


def sweep(now: float | None = None) -> int:
    """Discard tasks nobody has touched in RETENTION_SECONDS, and drop the
    files of tasks that are already closed. Returns how many were discarded."""
    from backend.db.connection import get_db
    cutoff = int((now or time.time()) - RETENTION_SECONDS)
    stale = get_db().execute(
        "SELECT id FROM agent_tasks WHERE updated_at < ? AND status IN "
        "('awaiting_plan','needs_input','done','error','interrupted')", (cutoff,),
    ).fetchall()
    for row in stale:
        cleanup(row['id'])
        tasks.set_status(row['id'], 'discarded')
    return len(stale)


def _loop() -> None:
    last_sweep = 0.0
    while True:
        try:
            if time.time() - last_sweep > SWEEP_SECONDS:
                last_sweep = time.time()
                sweep()
            if run_next():
                continue
        except Exception:
            logger.exception('code agent worker tick failed')
        _wake.wait(IDLE_WAIT)
        _wake.clear()


def start(repo_root: Path) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    if _driver is None:
        from backend.codeagent.driver import DockerRuntime, Driver
        runtime = DockerRuntime(repo_root)
        configure(Driver(runtime, on_stuck=_notify_stuck), runtime)
    _thread = threading.Thread(target=_loop, daemon=True, name='codeagent-worker')
    _thread.start()


def _notify_stuck(task: dict) -> None:
    from backend.push import notify
    notify.task_changed(task, 'stuck')


def cleanup(task_id: str) -> None:
    try:
        if _runtime is not None:
            _runtime.stop(task_id)
    except Exception:
        logger.exception('could not remove the container for %s', task_id)
    mirror.delete_task(task_id)


# --------------------------------------------------------------- actions

def _settings() -> dict:
    from backend.db.connection import get_db
    row = get_db().execute(
        'SELECT github_agent_token, code_agent_harness FROM settings LIMIT 1').fetchone()
    return dict(row) if row else {}


def create_task(repo_id: str, prompt: str, *, harness: str | None = None,
                title: str = '') -> dict:
    from backend.repos import registry
    repo = registry.get_repo(repo_id) if repo_id else registry.default_repo()
    if not repo:
        raise ActionRefused('Pick a registered repository first')
    if mirror.free_bytes() < MIN_FREE_BYTES:
        raise ActionRefused('Less than 10 GB of disk is free; discard some tasks first')
    harness = harness or _settings().get('code_agent_harness') or 'pi'
    task = tasks.create(repo['id'], prompt, title=title, harness=harness,
                        base_branch=repo.get('branch') or '')
    wake()
    return task


def _requeue(task_id: str, **fields) -> dict:
    tasks.update(task_id, **fields)
    task = tasks.set_status(task_id, 'queued')
    wake()
    return task


def approve_plan(task_id: str) -> dict:
    task = tasks.get(task_id)
    if task['status'] != 'awaiting_plan':
        raise ActionRefused('There is no plan waiting for approval')
    approval = tasks.pending_approval(task_id)
    if approval:
        tasks.answer_approval(approval['id'], 'approved')
    tasks.append_event(task_id, 'user', {'text': 'Approved the plan.', 'action': 'approve'})
    return _requeue(task_id, phase='build', pending_input=None)


def revise_plan(task_id: str, text: str) -> dict:
    text = (text or '').strip()
    if not text:
        raise ActionRefused('Say what should change in the plan')
    task = tasks.get(task_id)
    if task['status'] != 'awaiting_plan':
        raise ActionRefused('There is no plan waiting for approval')
    approval = tasks.pending_approval(task_id)
    if approval:
        tasks.answer_approval(approval['id'], 'rejected', text)
    tasks.append_event(task_id, 'user', {'text': text, 'action': 'revise'})
    return _requeue(task_id, phase='plan', pending_input=text)


def send_message(task_id: str, text: str) -> dict:
    """An answer to the agent's question, or a follow-up once it has finished."""
    text = (text or '').strip()
    if not text:
        raise ActionRefused('The message is empty')
    task = tasks.get(task_id)
    if task['status'] == 'awaiting_plan':
        return revise_plan(task_id, text)
    if task['status'] not in MESSAGEABLE:
        raise ActionRefused('The agent is busy; wait for it to finish or cancel it')
    approval = tasks.pending_approval(task_id)
    if approval:
        tasks.answer_approval(approval['id'], 'answered', text)
    tasks.append_event(task_id, 'user', {'text': text, 'action': 'message'})
    # A task that failed before its plan was approved is still planning.
    phase = 'build' if task.get('plan') and task['phase'] == 'build' else task['phase']
    return _requeue(task_id, phase=phase, pending_input=text)


CONTINUE_TEXT = ('You were interrupted. Your work so far is in /work — check `git log` and '
                 '`git status` — so continue from where you left off.')


def continue_task(task_id: str) -> dict:
    task = tasks.get(task_id)
    if task['status'] not in ('interrupted', 'error'):
        raise ActionRefused('Only an interrupted or failed task can be continued')
    tasks.append_event(task_id, 'user', {'text': 'Continue.', 'action': 'continue'})
    pending = task.get('pendingInput') or (CONTINUE_TEXT if task['phase'] == 'build' else None)
    return _requeue(task_id, pending_input=pending, error=None)


def cancel_task(task_id: str) -> dict:
    task = tasks.get(task_id)
    with _lock:
        running = _current['task_id'] == task_id
        if running:
            _current['cancel'].set()
    if running:
        tasks.append_event(task_id, 'user', {'text': 'Cancelled.', 'action': 'cancel'})
        return tasks.get(task_id)
    if task['status'] not in DISCARDABLE:
        raise ActionRefused('That task has already finished')
    cleanup(task_id)
    return tasks.set_status(task_id, 'cancelled')


def discard_task(task_id: str) -> dict:
    task = tasks.get(task_id)
    if task['status'] not in DISCARDABLE or current_task() == task_id:
        raise ActionRefused('Cancel the task before discarding it')
    cleanup(task_id)
    return tasks.set_status(task_id, 'discarded')


def push_task(task_id: str) -> dict:
    """Push the agent branch as the machine account and open the PR."""
    from backend.codeagent import github
    from backend.repos import registry
    task = tasks.get(task_id)
    if task['status'] not in ('done', 'needs_input'):
        raise ActionRefused('Only finished work can be pushed')
    if not task.get('headSha'):
        raise ActionRefused('The agent did not change anything')
    repo = registry.get_repo(task['repoId'])
    if not repo:
        raise ActionRefused('The repository is no longer registered')
    token = _settings().get('github_agent_token') or ''
    try:
        branch = mirror.push(task_id, repo['remoteUrl'], token, task['baseBranch'])
    except mirror.PushRefused as e:
        raise ActionRefused(str(e))
    owner, name = mirror.github_repo(repo['remoteUrl'])
    url = github.open_pull_request(token, owner, name, head=branch, base=task['baseBranch'],
                                   title=task['title'], body=github.pr_body(task))
    tasks.append_event(task_id, 'user', {'text': f'Pushed {branch}.', 'action': 'push',
                                         'prUrl': url})
    result = tasks.set_status(task_id, 'pushed', pr_url=url)
    cleanup(task_id)
    return result
