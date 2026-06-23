import os

from flask import Blueprint, jsonify, request

from backend.auth import NETWORK_MODE, COOKIE_NAME, is_localhost, make_token, decode_token
from backend.db.connection import get_db

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


@bp.get('/status')
def auth_status():
    if not NETWORK_MODE or is_localhost(request):
        return jsonify({'authenticated': True, 'networkMode': False})
    token = request.cookies.get(COOKIE_NAME)
    authenticated = bool(token and decode_token(token))
    return jsonify({'authenticated': authenticated, 'networkMode': True})


@bp.post('/login')
def login():
    if not NETWORK_MODE or is_localhost(request):
        return jsonify({'success': True})

    data = request.json or {}
    password = data.get('password', '')
    code = str(data.get('code', '')).strip()

    expected_password = os.environ.get('LUNASCHAL_PASSWORD', '')
    if not expected_password:
        return jsonify({'error': 'LUNASCHAL_PASSWORD env var not set on server'}), 500

    row = get_db().execute('SELECT network_code FROM settings LIMIT 1').fetchone()
    expected_code = row['network_code'] if row else None
    if not expected_code:
        return jsonify({'error': 'Network code not initialised — restart the server'}), 500

    if password != expected_password or code != expected_code:
        return jsonify({'error': 'Incorrect password or display code'}), 401

    resp = jsonify({'success': True})
    resp.set_cookie(
        COOKIE_NAME,
        make_token(),
        max_age=30 * 86400,
        httponly=True,
        samesite='Lax',
        secure=False,
    )
    return resp


@bp.post('/logout')
def logout():
    resp = jsonify({'success': True})
    resp.delete_cookie(COOKIE_NAME)
    return resp
