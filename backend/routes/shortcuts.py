import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

bp = Blueprint('shortcuts', __name__, url_prefix='/api/shortcuts')

SHORTCUTS_PATH = Path(os.environ.get('SHORTCUTS_PATH', './data/shortcuts.json'))

_EMPTY = {'version': 1, 'bindings': {}}


@bp.get('')
def get_shortcuts():
    if not SHORTCUTS_PATH.exists():
        return jsonify(_EMPTY)
    try:
        data = json.loads(SHORTCUTS_PATH.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return jsonify(_EMPTY)
    if not isinstance(data, dict) or not isinstance(data.get('bindings'), dict):
        return jsonify(_EMPTY)
    return jsonify({'version': data.get('version', 1), 'bindings': data['bindings']})


@bp.put('')
def put_shortcuts():
    body = request.json or {}
    bindings = body.get('bindings')
    if not isinstance(bindings, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in bindings.items()
    ):
        return jsonify({'error': 'bindings must be a string-to-string map'}), 400
    SHORTCUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHORTCUTS_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'version': 1, 'bindings': bindings}, indent=2), encoding='utf-8')
    tmp.replace(SHORTCUTS_PATH)
    return jsonify({'success': True})
