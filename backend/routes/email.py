import secrets
import time

from flask import Blueprint, current_app, jsonify, redirect, request
from markupsafe import escape
from ulid import ULID

from backend.db.connection import get_db, row_to_dict, search_emails_fts
from backend.email import gmail_client, imap_client, outlook_client

bp = Blueprint('email', __name__, url_prefix='/api/email')

_PROVIDERS = ('gmail', 'outlook', 'imap')
_OAUTH_PROVIDERS = ('gmail', 'outlook')

# Single-user local app: an in-memory, TTL'd state dict is enough CSRF
# protection for the OAuth dance — no need for a signed cookie or DB row.
# Each entry maps state -> (provider, timestamp): a single shared callback
# route serves both OAuth providers, and it must derive which one a code
# belongs to from this server-trusted state, never from a callback query
# param — otherwise a state minted for one provider could be replayed
# against the other's token-exchange path.
_pending_states: dict[str, tuple[str, float]] = {}
_STATE_TTL_SECONDS = 600


def _new_state(provider: str) -> str:
    now = time.time()
    for s, (_, ts) in list(_pending_states.items()):
        if now - ts > _STATE_TTL_SECONDS:
            del _pending_states[s]
    state = secrets.token_urlsafe(24)
    _pending_states[state] = (provider, now)
    return state


def _consume_state(state: str | None) -> str | None:
    """Returns the provider this state was minted for, or None if the state
    is missing, unknown, already used, or expired."""
    if not state:
        return None
    entry = _pending_states.pop(state, None)
    if entry is None:
        return None
    provider, ts = entry
    if (time.time() - ts) > _STATE_TTL_SECONDS:
        return None
    return provider


def _redirect_uri() -> str:
    return request.host_url.rstrip('/') + '/api/email/oauth/callback'


def _get_oauth_client(provider: str) -> tuple[str | None, str | None]:
    row = get_db().execute(
        'SELECT google_oauth_client_id, google_oauth_client_secret,'
        ' microsoft_oauth_client_id, microsoft_oauth_client_secret FROM settings LIMIT 1'
    ).fetchone()
    if not row:
        return None, None
    if provider == 'gmail':
        return row['google_oauth_client_id'], row['google_oauth_client_secret']
    if provider == 'outlook':
        return row['microsoft_oauth_client_id'], row['microsoft_oauth_client_secret']
    return None, None


def _provider_label(provider: str) -> str:
    return {'gmail': 'Google', 'outlook': 'Microsoft'}.get(provider, provider.capitalize())


def _get_account(provider: str):
    # updated_at, not created_at: reconnecting a previously-used account (e.g.
    # after picking the wrong account on the consent screen and fixing it) is
    # an UPDATE via the ON CONFLICT upsert below, which bumps updated_at but
    # leaves created_at at its original value — ordering by created_at would
    # keep returning a since-reconnected-away-from account instead of the one
    # actually active now.
    return get_db().execute(
        "SELECT * FROM email_accounts WHERE provider=? ORDER BY updated_at DESC LIMIT 1",
        (provider,),
    ).fetchone()


def _has_credentials(account) -> bool:
    if account['provider'] == 'imap':
        return bool(account['imap_password'])
    return bool(account['refresh_token'])


def _reject_second_account(db, provider: str, email_address: str):
    """One connected account per provider slot (see docs/email-view.md):
    reconnecting with a *different* address while one is already active is
    rejected rather than silently switching, so the "most recently updated
    account wins" resolution in _get_account never has to silently orphan
    one of two live accounts for the same provider."""
    existing = db.execute(
        "SELECT email_address FROM email_accounts WHERE provider=? AND sync_enabled=1 AND email_address<>?",
        (provider, email_address),
    ).fetchone()
    if existing:
        return (
            f'A different {_provider_label(provider)} account ({existing["email_address"]}) is already '
            'connected. Disconnect it first, then try again.'
        )
    return None


@bp.get('/oauth/authorize')
def oauth_authorize():
    provider = request.args.get('provider', 'gmail')
    if provider not in _OAUTH_PROVIDERS:
        return jsonify({'error': f'Unknown provider: {provider}'}), 400
    client_id, _ = _get_oauth_client(provider)
    if not client_id:
        return jsonify({
            'error': f'{_provider_label(provider)} OAuth client not configured — '
                     'add a client ID and secret in Settings first.'
        }), 400
    state = _new_state(provider)
    build_auth_url = gmail_client.build_auth_url if provider == 'gmail' else outlook_client.build_auth_url
    auth_url = build_auth_url(client_id, _redirect_uri(), state)
    return redirect(auth_url)


@bp.get('/oauth/callback')
def oauth_callback():
    # Both branches escape: `error` is a query parameter, so it is whatever a
    # crafted link put there, and the provider's own message goes into the
    # same HTML further down. Neither is trusted enough to interpolate raw.
    error = request.args.get('error')
    if error:
        return (
            f'<p>Email connection failed: {escape(error)}. '
            'You can close this tab and try again.</p>'
        ), 400

    provider = _consume_state(request.args.get('state'))
    if not provider:
        return '<p>This connection link expired or was already used. Close this tab and try again.</p>', 400

    code = request.args.get('code')
    if not code:
        return '<p>Missing authorization code.</p>', 400

    client_id, client_secret = _get_oauth_client(provider)
    if not client_id or not client_secret:
        return f'<p>{_provider_label(provider)} OAuth client not configured.</p>', 400

    try:
        if provider == 'gmail':
            token_data = gmail_client.exchange_code(client_id, client_secret, _redirect_uri(), code)
            email_address = gmail_client.get_profile(token_data['access_token'])['emailAddress']
        else:
            token_data = outlook_client.exchange_code(client_id, client_secret, _redirect_uri(), code)
            email_address = outlook_client.decode_id_token_email(token_data.get('id_token') or '')
            if not email_address:
                return '<p>Outlook connection failed: could not determine the account email address.</p>', 502
    except Exception as e:
        return (
            f'<p>{_provider_label(provider)} connection failed: {escape(str(e))}. '
            'You can close this tab and try again.</p>'
        ), 502

    db = get_db()
    guard_error = _reject_second_account(db, provider, email_address)
    if guard_error:
        return f'<p>{escape(guard_error)}</p>', 409

    now = int(time.time())
    expires_at = now + int(token_data.get('expires_in', 3600))
    db.execute(
        """
        INSERT INTO email_accounts
            (id, provider, email_address, access_token, refresh_token, token_expires_at,
             scope, sync_enabled, last_sync_error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
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
            str(ULID()), provider, email_address, token_data['access_token'],
            token_data.get('refresh_token'), expires_at, token_data.get('scope'),
            now, now,
        ),
    )
    db.commit()
    return redirect('/')


@bp.get('/oauth/status')
def oauth_status():
    provider = request.args.get('provider', 'gmail')
    account = _get_account(provider)
    if not account:
        return jsonify({'connected': False})
    return jsonify({
        'connected': _has_credentials(account) and bool(account['sync_enabled']),
        'emailAddress': account['email_address'],
        'lastSyncedAt': account['last_synced_at'],
        'lastSyncError': account['last_sync_error'],
        'syncEnabled': bool(account['sync_enabled']),
    })


@bp.get('/accounts')
def list_accounts():
    accounts = []
    for provider in _PROVIDERS:
        account = _get_account(provider)
        if not account:
            continue
        accounts.append({
            'provider': provider,
            'emailAddress': account['email_address'],
            'connected': _has_credentials(account) and bool(account['sync_enabled']),
            'lastSyncedAt': account['last_synced_at'],
            'lastSyncError': account['last_sync_error'],
            'syncEnabled': bool(account['sync_enabled']),
        })
    return jsonify(accounts)


@bp.post('/oauth/disconnect')
def oauth_disconnect():
    provider = request.args.get('provider', 'gmail')
    account = _get_account(provider)
    if not account:
        return jsonify({'success': True})
    if provider == 'gmail' and account['access_token']:
        gmail_client.revoke_token(account['access_token'])
    elif provider == 'outlook' and account['access_token']:
        outlook_client.revoke_token(account['access_token'])
    db = get_db()
    now = int(time.time())
    # Soft-disconnect: keep the row and every already-synced email — this
    # stops future polling, it doesn't delete local mail.
    if provider == 'imap':
        db.execute(
            'UPDATE email_accounts SET imap_password=NULL, sync_enabled=0, updated_at=? WHERE id=?',
            (now, account['id']),
        )
    else:
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


@bp.post('/imap/connect')
def imap_connect():
    data = request.get_json(silent=True) or {}
    host = (data.get('host') or '').strip()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    email_address = (data.get('emailAddress') or '').strip()
    try:
        port = int(data.get('port') or 993)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid port'}), 400

    if not host or not username or not password or not email_address:
        return jsonify({'error': 'host, username, password, and emailAddress are required'}), 400

    db = get_db()
    guard_error = _reject_second_account(db, 'imap', email_address)
    if guard_error:
        return jsonify({'error': guard_error}), 409

    # Validate by actually connecting before persisting anything — surfaces
    # the server's real error (wrong password, host unreachable, TLS
    # mismatch) instead of saving credentials that will only fail later in
    # the background scheduler where the user won't be watching.
    try:
        conn = imap_client.connect(host, port, username=username, password=password)
        try:
            conn.select(imap_client.INBOX, readonly=True)
            imap_client.folder_status(conn)
        finally:
            conn.logout()
    except imap_client.ImapError as e:
        return jsonify({'error': str(e)}), 400

    now = int(time.time())
    db.execute(
        """
        INSERT INTO email_accounts
            (id, provider, email_address, imap_host, imap_port, imap_username, imap_password,
             sync_enabled, last_sync_error, created_at, updated_at)
        VALUES (?, 'imap', ?, ?, ?, ?, ?, 1, NULL, ?, ?)
        ON CONFLICT(provider, email_address) DO UPDATE SET
            imap_host=excluded.imap_host,
            imap_port=excluded.imap_port,
            imap_username=excluded.imap_username,
            imap_password=excluded.imap_password,
            sync_enabled=1,
            last_sync_error=NULL,
            updated_at=excluded.updated_at
        """,
        (str(ULID()), email_address, host, port, username, password, now, now),
    )
    db.commit()
    return jsonify({'success': True})


@bp.post('/sync')
def sync_now():
    from backend.email import sync

    provider = request.args.get('provider')
    if provider:
        if provider not in _PROVIDERS:
            return jsonify({'error': f'Unknown provider: {provider}'}), 400
        account = _get_account(provider)
        if not account:
            return jsonify({'error': f'No {provider} account connected'}), 400
        return jsonify(sync.sync_account(account))

    results = {}
    for p in _PROVIDERS:
        account = _get_account(p)
        if account and account['sync_enabled']:
            results[account['id']] = sync.sync_account(account)
    if not results:
        return jsonify({'error': 'No email accounts connected'}), 400
    return jsonify(results)


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


@bp.get('/images/<url_hash>')
def get_image(url_hash: str):
    """Serve a stored email image by the hash of its original URL.

    That key is what lets sanitize.py write this path into the markup at
    import time, before the bytes exist. A row that hasn't been fetched yet
    answers 404 — the reader sees a broken image for a while rather than the
    page blocking on a download, and the fetcher will fill it in.

    The response is deliberately same-origin and cacheable forever: the file
    is content-addressed, so the bytes behind a given hash never change.
    """
    from backend.email import media

    row = get_db().execute(
        'SELECT content_hash, extension, content_type, status FROM email_images WHERE url_hash=?',
        (url_hash,),
    ).fetchone()
    if not row or row['status'] != 'stored' or not row['content_hash']:
        return jsonify({'error': 'Image not available'}), 404
    data = media.read(row['content_hash'], row['extension'] or 'bin')
    if data is None:
        # Row says stored but the file is gone — the external drive is
        # unmounted, or it was pruned. Not an error worth logging loudly;
        # the image simply isn't here right now.
        return jsonify({'error': 'Image not available'}), 404
    resp = current_app.response_class(data, mimetype=row['content_type'] or 'application/octet-stream')
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    # Never let a stored SVG execute in our origin: it is remote markup that
    # happens to be served from here, which is precisely the case CSP exists
    # for. Belt and braces alongside the sandbox attribute.
    resp.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'"
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


# Not '/images/status': that is a legal url_hash as far as the route above is
# concerned, and relying on Werkzeug's static-beats-dynamic ordering to
# disambiguate is a subtlety no reader should have to know about.
@bp.get('/image-status')
def images_status():
    from backend.email import images as image_worker
    from backend.email import media

    db = get_db()
    counts = {
        r['status']: r['c']
        for r in db.execute('SELECT status, COUNT(*) c FROM email_images GROUP BY status')
    }
    return jsonify({
        'pending': image_worker.pending_count(db),
        'stored': counts.get('stored', 0),
        'failed': counts.get('failed', 0),
        'skipped': counts.get('skipped', 0),
        'storeAvailable': media.is_available(),
        'storeRoot': str(media.media_root()),
    })


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
