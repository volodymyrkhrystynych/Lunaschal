from flask import Blueprint, jsonify, request

from backend.capture_time import optional_capture_time
from backend.goals import service

bp = Blueprint('goals', __name__, url_prefix='/api/goals')


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify({'error': str(error)}), 400


@bp.errorhandler(LookupError)
def missing(error):
    return jsonify({'error': str(error)}), 404


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('A goal object is required')
    return data


def _acted_at() -> int | None:
    """The phone's `capturedAt` for an offline-queued change: the journal entry
    it leaves belongs to when the goal was set, not when it synced."""
    return optional_capture_time(request.args)


@bp.get('')
def list_goals():
    return jsonify(service.list_goals())


@bp.get('/current')
def current_goals():
    return jsonify(service.current())


@bp.post('')
def create_goal():
    return jsonify(service.create(_body(), acted_at=_acted_at())), 201


@bp.put('/overarching')
def set_overarching():
    data = _body()
    return jsonify(service.set_overarching(data.get('text'), goal_id=data.get('id'),
                                           acted_at=_acted_at()))


@bp.patch('/<goal_id>')
def edit_goal(goal_id):
    return jsonify(service.edit(goal_id, _body(), acted_at=_acted_at()))


@bp.get('/<goal_id>/history')
def goal_history(goal_id):
    return jsonify(service.history(goal_id))


@bp.post('/<goal_id>/check')
def check(goal_id):
    return jsonify(service.evaluate(goal_id))


@bp.post('/<goal_id>/<action>')
def update(goal_id, action):
    return jsonify(service.change_status(goal_id, action, acted_at=_acted_at()))
