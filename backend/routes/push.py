"""Registering a device for push notifications (backend/push/)."""
from flask import Blueprint, jsonify, request

from backend.push import apns, notify

bp = Blueprint('push', __name__, url_prefix='/api/push')


@bp.post('/devices')
def register_device():
    data = request.get_json(silent=True) or {}
    try:
        notify.register_device(data.get('token') or '', data.get('environment') or 'production',
                               data.get('platform') or 'ios')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    # Whether the server can actually send — so the app can say so, rather than
    # registering happily against a server with no APNs key.
    return jsonify({'ok': True, 'configured': apns.configured()})
