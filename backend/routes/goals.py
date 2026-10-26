from flask import Blueprint, jsonify, request

from backend.goals import service

bp = Blueprint('goals', __name__, url_prefix='/api/goals')


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify({'error': str(error)}), 400


@bp.errorhandler(LookupError)
def missing(error):
    return jsonify({'error': str(error)}), 404


@bp.get('')
def list_goals():
    return jsonify(service.list_goals())


@bp.post('')
def create_goal():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('A goal object is required')
    return jsonify(service.create(data)), 201


@bp.post('/<goal_id>/check')
def check(goal_id):
    return jsonify(service.evaluate(goal_id))


@bp.post('/<goal_id>/<action>')
def update(goal_id, action):
    return jsonify(service.change_status(goal_id, action))
