"""The Code tab: coding tasks run by a local agent in a sealed container.

Handlers only read rows and queue transitions; the agent itself runs on
backend/codeagent/worker.py's thread, minutes to an hour per phase, which no
HTTP request waits on. The phone polls `GET /tasks/<id>?after=<seq>` for new
events, the same shape ChatModel's poll already uses.
"""
from flask import Blueprint, jsonify, request

from backend.codeagent import mirror, tasks, worker
from backend.codeagent.github import GitHubError
from backend.repos.git import GitError

bp = Blueprint('code_agent', __name__, url_prefix='/api/code')


def _task_payload(task: dict) -> dict:
    task['pending'] = tasks.pending_approval(task['id'])
    task['running'] = worker.current_task() == task['id']
    return task


def _refused(e: Exception, code: int = 409):
    return jsonify({'error': str(e)}), code


@bp.get('/tasks')
def list_tasks():
    limit = min(max(request.args.get('limit', 50, type=int), 1), 200)
    return jsonify({
        'tasks': [_task_payload(t) for t in tasks.list_tasks(limit)],
        'waiting': tasks.waiting_count(),
    })


@bp.post('/tasks')
def create_task():
    data = request.get_json(silent=True) or {}
    try:
        task = worker.create_task(data.get('repoId') or '', data.get('prompt') or '',
                                  harness=data.get('harness') or None,
                                  title=data.get('title') or '',
                                  mode=data.get('mode') or 'plan')
    except (worker.ActionRefused, ValueError) as e:
        return _refused(e, 400)
    return jsonify(_task_payload(task)), 201


@bp.get('/tasks/<task_id>')
def get_task(task_id):
    try:
        task = tasks.get(task_id)
    except tasks.TaskNotFound:
        return jsonify({'error': 'Not found'}), 404
    after = max(request.args.get('after', 0, type=int), 0)
    return jsonify({'task': _task_payload(task), 'events': tasks.events(task_id, after)})


@bp.get('/tasks/<task_id>/diff')
def get_diff(task_id):
    try:
        task = tasks.get(task_id)
    except tasks.TaskNotFound:
        return jsonify({'error': 'Not found'}), 404
    if not task.get('headSha') or not task.get('baseSha'):
        return jsonify({'files': [], 'commits': [], 'patch': '', 'truncated': False})
    try:
        return jsonify(mirror.diff(task_id, task['baseSha']))
    except (GitError, FileNotFoundError):
        # The workspace was cleaned up after a push or a discard.
        return jsonify({'error': 'The diff is no longer available'}), 410


def _action(fn, *args):
    try:
        return jsonify(_task_payload(fn(*args)))
    except tasks.TaskNotFound:
        return jsonify({'error': 'Not found'}), 404
    except (worker.ActionRefused, tasks.BadTransition) as e:
        return _refused(e)
    except (GitHubError, GitError) as e:
        return _refused(e, 502)


@bp.post('/tasks/<task_id>/plan')
def answer_plan(task_id):
    data = request.get_json(silent=True) or {}
    if data.get('approve'):
        return _action(worker.approve_plan, task_id)
    return _action(worker.revise_plan, task_id, data.get('text') or '')


@bp.post('/tasks/<task_id>/make-plan')
def make_plan(task_id):
    """An answered question becomes a change, starting with its plan."""
    data = request.get_json(silent=True) or {}
    return _action(worker.plan_from_answer, task_id, data.get('text') or '')


@bp.post('/tasks/<task_id>/messages')
def send_message(task_id):
    data = request.get_json(silent=True) or {}
    return _action(worker.send_message, task_id, data.get('text') or '')


@bp.post('/tasks/<task_id>/continue')
def continue_task(task_id):
    return _action(worker.continue_task, task_id)


@bp.post('/tasks/<task_id>/cancel')
def cancel_task(task_id):
    return _action(worker.cancel_task, task_id)


@bp.post('/tasks/<task_id>/discard')
def discard_task(task_id):
    return _action(worker.discard_task, task_id)


@bp.post('/tasks/<task_id>/push')
def push_task(task_id):
    return _action(worker.push_task, task_id)
