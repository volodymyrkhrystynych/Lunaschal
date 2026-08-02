import secrets
import time

from flask import Blueprint, jsonify, redirect, request
from ulid import ULID

from backend.db.connection import get_db, row_to_dict, search_emails_fts
from backend.email import gmail_client

bp = Blueprint('email', __name__, url_prefix='/api/email')

# Single-user local app: an in-memory, TTL'd state dict is enough CSRF
# protection for the OAuth dance — no need for a signed cookie or DB row.
_pending_states: dict[str, float] = {}
_STATE_TTL_SECONDS = 600


def _new_state() -> str:
    now = time.time()
    for s, ts in list(_pending_states.items()):
        if now - ts > _STATE_TTL_SECONDS:
            del _pending_states[s]
    state = secrets.token_urlsafe(24)
    _pending_states[state] = now
    return state


def _consume_state(state: str | None) -> bool:
    if not state:
        return False
    ts = _pending_states.pop(state, None)
    return ts is not None and (time.time() - ts) <= _STATE_TTL_SECONDS


def _redirect_uri() -> str:
    return request.host_url.rstrip('/') + '/api/email/oauth/callback'


def _get_oauth_client() -> tuple[str | None, str | None]:
    row = get_db().execute(
        'SELECT google_oauth_client_id, google_oauth_client_secret FROM settings LIMIT 1'
    ).fetchone()
    if not row:
        return None, None
    return row['google_oauth_client_id'], row['google_oauth_client_secret']


def _get_account():
    return get_db().execute(
        "SELECT * FROM email_accounts WHERE provider='gmail' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()


@bp.get('/oauth/authorize')
def oauth_authorize():
    client_id, _ = _get_oauth_client()
    if not client_id:
        return jsonify({
            'error': 'Google OAuth client not configured — add a client ID and secret in Settings first.'
        }), 400
    state = _new_state()
    auth_url = gmail_client.build_auth_url(client_id, _redirect_uri(), state)
    return redirect(auth_url)


@bp.get('/oauth/callback')
def oauth_callback():
    error = request.args.get('error')
    if error:
        return f'<p>Gmail connection failed: {error}. You can close this tab and try again.</p>', 400

    if not _consume_state(request.args.get('state')):
        return '<p>This connection link expired or was already used. Close this tab and try again.</p>', 400

    code = request.args.get('code')
    if not code:
        return '<p>Missing authorization code.</p>', 400

    client_id, client_secret = _get_oauth_client()
    if not client_id or not client_secret:
        return '<p>Google OAuth client not configured.</p>', 400

    try:
        token_data = gmail_client.exchange_code(client_id, client_secret, _redirect_uri(), code)
        profile = gmail_client.get_profile(token_data['access_token'])
    except Exception as e:
        return f'<p>Gmail connection failed: {e}. You can close this tab and try again.</p>', 502

    db = get_db()
    now = int(time.time())
    expires_at = now + int(token_data.get('expires_in', 3600))
    db.execute(
        """
        INSERT INTO email_accounts
            (id, provider, email_address, access_token, refresh_token, token_expires_at,
             scope, sync_enabled, last_sync_error, created_at, updated_at)
        VALUES (?, 'gmail', ?, ?, ?, ?, ?, 1, NULL, ?, ?)
        ON CONFLICT(provider, email_address) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=COALESCE(excluded.refresh_token, email_accounts.refresh_token),
            token_expires_at=excluded.token_expires_at,
            scope=excluded.scope,
            sync_enabled=1,
            last_sync_error=NULL,
            updated_at=excluded.updated_at
        """,
        (
            str(ULID()), profile['emailAddress'], token_data['access_token'],
            token_data.get('refresh_token'), expires_at, token_data.get('scope'),
            now, now,
        ),
    )
    db.commit()
    return redirect('/')


@bp.get('/oauth/status')
def oauth_status():
    account = _get_account()
    if not account:
        return jsonify({'connected': False})
    return jsonify({
        'connected': bool(account['refresh_token']) and bool(account['sync_enabled']),
        'emailAddress': account['email_address'],
        'lastSyncedAt': account['last_synced_at'],
        'lastSyncError': account['last_sync_error'],
        'syncEnabled': bool(account['sync_enabled']),
    })


@bp.post('/oauth/disconnect')
def oauth_disconnect():
    account = _get_account()
    if not account:
        return jsonify({'success': True})
    if account['access_token']:
        gmail_client.revoke_token(account['access_token'])
    db = get_db()
    now = int(time.time())
    # Soft-disconnect: keep the row, history_id, and every already-synced
    # email — this stops future polling, it doesn't delete local mail.
    db.execute(
        """
        UPDATE email_accounts
        SET access_token=NULL, refresh_token=NULL, scope=NULL, sync_enabled=0, updated_at=?
        WHERE id=?
        """,
        (now, account['id']),
    )
    db.commit()
    return jsonify({'success': True})


@bp.post('/sync')
def sync_now():
    from backend.email import sync
    account = _get_account()
    if not account:
        return jsonify({'error': 'No Gmail account connected'}), 400
    return jsonify(sync.sync_account(account))


@bp.get('')
def list_emails():
    db = get_db()
    category = request.args.get('category')
    job_status = request.args.get('jobStatus')
    query = request.args.get('query')
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = max(int(request.args.get('offset', 0)), 0)

    where, params = [], []
    if category:
        where.append('category=?')
        params.append(category)
    if job_status:
        where.append('job_status=?')
        params.append(job_status)
    if query:
        ids = [r['id'] for r in search_emails_fts(query, limit=500)]
        if not ids:
            return jsonify([])
        where.append(f"id IN ({','.join('?' * len(ids))})")
        params.extend(ids)

    where_clause = f"WHERE {' AND '.join(where)}" if where else ''
    rows = db.execute(
        f'SELECT * FROM emails {where_clause} ORDER BY received_at DESC LIMIT ? OFFSET ?',
        (*params, limit, offset),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/stats')
def stats():
    db = get_db()
    counts = {
        r['job_status']: r['count']
        for r in db.execute(
            """
            SELECT job_status, COUNT(*) as count FROM emails
            WHERE category='job_application' AND job_status IS NOT NULL
            GROUP BY job_status
            """
        ).fetchall()
    }
    next_steps = db.execute(
        "SELECT * FROM emails WHERE job_status='interview_next_step' ORDER BY received_at DESC LIMIT 10"
    ).fetchall()
    return jsonify({
        'sentCount': counts.get('sent', 0),
        'rejectionCount': counts.get('rejection', 0),
        'interviewNextStepCount': counts.get('interview_next_step', 0),
        'otherUpdateCount': counts.get('other_update', 0),
        'nextSteps': [row_to_dict(r) for r in next_steps],
    })


@bp.get('/<email_id>')
def get_email(email_id):
    row = get_db().execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))
