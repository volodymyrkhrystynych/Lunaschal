import time
from flask import Blueprint, jsonify, request, make_response
from backend.db.connection import get_db
from backend.auth import (
    hash_password, check_password, generate_token,
    set_auth_cookie, clear_auth_cookie, require_auth,
)

bp = Blueprint('settings', __name__, url_prefix='/api/settings')


def _get_settings():
    row = get_db().execute('SELECT * FROM settings LIMIT 1').fetchone()
    return dict(row) if row else None


@bp.get('/is-setup-complete')
def is_setup_complete():
    s = _get_settings()
    return jsonify({'complete': bool(s and s.get('password_hash'))})


@bp.post('/setup')
def setup():
    body = request.json or {}
    password = body.get('password', '')
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    s = _get_settings()
    if s and s.get('password_hash'):
        return jsonify({'error': 'Setup already complete'}), 400
    now = int(time.time())
    hashed = hash_password(password)
    db = get_db()
    if s:
        db.execute('UPDATE settings SET password_hash=?, updated_at=? WHERE id=1', (hashed, now))
    else:
        db.execute(
            'INSERT INTO settings(id, password_hash, created_at, updated_at) VALUES (1,?,?,?)',
            (hashed, now, now),
        )
    db.commit()
    resp = make_response(jsonify({'success': True}))
    set_auth_cookie(resp, generate_token())
    return resp


@bp.post('/login')
def login():
    body = request.json or {}
    password = body.get('password', '')
    s = _get_settings()
    if not s or not s.get('password_hash') or not check_password(password, s['password_hash']):
        return jsonify({'error': 'Invalid password'}), 401
    resp = make_response(jsonify({'success': True}))
    set_auth_cookie(resp, generate_token())
    return resp


@bp.post('/logout')
def logout():
    resp = make_response(jsonify({'success': True}))
    clear_auth_cookie(resp)
    return resp


@bp.get('')
@require_auth
def get_settings():
    s = _get_settings()
    if not s:
        return jsonify(None)
    return jsonify({
        'aiProvider': s.get('ai_provider'),
        'aiModel': s.get('ai_model'),
        'hasOpenaiKey': bool(s.get('openai_api_key')),
        'hasGoogleKey': bool(s.get('google_api_key')),
        'ollamaUrl': s.get('ollama_url'),
        'ollamaModel': s.get('ollama_model'),
    })


@bp.patch('/ai')
@require_auth
def update_ai():
    body = request.json or {}
    field_map = {
        'aiProvider': 'ai_provider', 'aiModel': 'ai_model',
        'openaiApiKey': 'openai_api_key', 'googleApiKey': 'google_api_key',
        'ollamaUrl': 'ollama_url', 'ollamaModel': 'ollama_model',
    }
    updates: dict = {'updated_at': int(time.time())}
    for camel, snake in field_map.items():
        if camel in body:
            updates[snake] = body[camel]
    db = get_db()
    s = _get_settings()
    now = int(time.time())
    if s:
        set_clause = ', '.join(f'{k}=?' for k in updates)
        db.execute(f'UPDATE settings SET {set_clause} WHERE id=1', list(updates.values()))
    else:
        updates['created_at'] = now
        updates['id'] = 1
        cols = ', '.join(updates)
        ph = ', '.join('?' * len(updates))
        db.execute(f'INSERT INTO settings({cols}) VALUES ({ph})', list(updates.values()))
    db.commit()
    return jsonify({'success': True})


@bp.post('/change-password')
@require_auth
def change_password():
    body = request.json or {}
    current = body.get('currentPassword', '')
    new_pw = body.get('newPassword', '')
    if len(new_pw) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400
    s = _get_settings()
    if not s or not s.get('password_hash') or not check_password(current, s['password_hash']):
        return jsonify({'error': 'Current password is incorrect'}), 401
    now = int(time.time())
    get_db().execute('UPDATE settings SET password_hash=?, updated_at=? WHERE id=1', (hash_password(new_pw), now))
    get_db().commit()
    return jsonify({'success': True})
