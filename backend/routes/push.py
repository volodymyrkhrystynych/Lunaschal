"""Registering phones for notifications, and checking on them.

The device token is the only thing the phone sends; the signing key lives in
the server's environment (backend/push/apns.py) and no route ever returns it,
or even its path — Settings shows only whether push is configured.
"""
from flask import Blueprint, jsonify, request

from backend.coach.deliver import push
from backend.db.connection import get_db, row_to_dict
from backend.push import apns, notify

bp = Blueprint('push', __name__, url_prefix='/api/push')


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


@bp.post('/devices')
def register():
    body = request.get_json(silent=True) or {}
    coach = body.get('coach')
    try:
        notify.register_device(_text(body.get('token')) or '', body.get('environment') or 'production',
                               _text(body.get('platform')) or 'ios', bundle_id=_text(body.get('bundleId')),
                               app_version=_text(body.get('appVersion')),
                               coach=coach if isinstance(coach, bool) else None)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    # Whether the server can actually send — so the app can say so, rather than
    # registering happily against a server with no APNs key.
    return jsonify({'ok': True, 'configured': apns.configured()})


@bp.delete('/devices/<token>')
def unregister(token):
    db = get_db()
    db.execute('DELETE FROM push_devices WHERE token=?', (token.lower(),))
    db.commit()
    return jsonify({'success': True})


@bp.get('/status')
def status():
    rows = get_db().execute('SELECT * FROM push_devices ORDER BY last_seen_at DESC').fetchall()
    devices = []
    for r in rows:
        device = row_to_dict(r)
        # Enough to tell two phones apart; the token itself stays here.
        device['token'] = f"…{r['token'][-6:]}"
        devices.append(device)
    return jsonify({'configured': apns.configured(), 'devices': devices})


@bp.post('/test')
def test():
    if not apns.configured():
        return jsonify({'error': 'APNs is not configured on the server (APNS_KEY_PATH, APNS_KEY_ID)'}), 409
    result = push('Lunaschal', 'Test notification — reminders will arrive like this.',
                  category='COACH_TEST', data={'kind': 'test'})
    return jsonify(result)
