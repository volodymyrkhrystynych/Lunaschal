"""Per-account email sync, dispatched by provider.

Gmail: History API for steady-state incremental polling, a full-mailbox
listing for first connect and as the recovery path when a history cursor has
expired (Gmail only retains history for a bounded window). Outlook and
generic IMAP share one path (backend/email/imap_client.py): UID SEARCH for
incremental polling, keyed on UIDVALIDITY/UIDNEXT instead of a Gmail-style
opaque cursor — a UIDVALIDITY change is IMAP's equivalent of Gmail's history
cursor expiring, and gets the same "old cursor is meaningless, re-list
everything" recovery.

The goal for every provider is a complete local mailbox mirror for backup,
so there's no day-bounded window anywhere — re-listing the whole mailbox is
cheap even on recovery, since already-synced messages are skipped before the
expensive per-message fetch (see _store_parsed_message's ON CONFLICT). Shaped
like backend/newspapers/sync.py::sync_today — try/except, returns a status
dict, never raises, so one bad cycle can't kill the scheduler thread that
calls this in a loop.
"""
import json
import threading
import time

from ulid import ULID

from backend.ai.background import run_bg
from backend.db.connection import get_db
from backend.email import gmail_client, imap_client, images, media, outlook_client
from backend.email.sanitize import sanitize_email_html

# One sync per account at a time. The scheduler treats an account as due for
# the whole time `last_synced_at IS NULL`, which on a first connect is the
# entire multi-hour backfill — so a manual POST /api/email/sync landing
# mid-backfill used to start a second concurrent walk of the same mailbox.
# The two raced on the insert path's check-then-insert and one died on the
# (account_id, provider_message_id) unique index, which cost far more than
# the duplicate work: the crash discarded that run's classification queue and
# left the sync cursor NULL, so the next sync restarted from zero.
#
# Non-reentrant and non-blocking: a second caller is told the account is
# already syncing rather than being queued behind a job that may run for
# hours. In-memory is sufficient — a process that dies holding this releases
# it by dying, which is exactly the semantics a DB flag would have to
# reconstruct with a startup sweep.
_account_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(account_id: str) -> threading.Lock:
    with _locks_guard:
        return _account_locks.setdefault(account_id, threading.Lock())


def _enqueue_classification(row_id: str) -> None:
    """Queue one message for classification, as soon as it lands.

    This used to be a single loop over `new_ids` after the whole sync
    returned, which had two costs on a first connect. A full-mailbox backfill
    runs for hours, so nothing was labelled until the very last page arrived —
    every category filter and the job dashboard read as empty the entire time,
    looking exactly like a classifier that didn't work. And because `new_ids`
    was a local list, a crash anywhere in the walk discarded the queue for
    everything already imported: those rows kept `classified_at IS NULL` with
    no error recorded, and only a process restart's sweep_unclassified() would
    ever pick them up. Enqueueing per message makes the work survive the run
    that produced it.
    """
    from backend.ai.email import classify_email
    run_bg(lambda eid=row_id: classify_email(eid))


def _get_oauth_settings(db) -> dict | None:
    row = db.execute(
        'SELECT google_oauth_client_id, google_oauth_client_secret FROM settings LIMIT 1'
    ).fetchone()
    return dict(row) if row else None


def _get_outlook_oauth_settings(db) -> dict | None:
    row = db.execute(
        'SELECT microsoft_oauth_client_id, microsoft_oauth_client_secret FROM settings LIMIT 1'
    ).fetchone()
    return dict(row) if row else None


def _store_inline_images(db, provider_message_id: str, parsed: dict) -> dict[str, str]:
    """Store Gmail Content-ID parts and return cid -> local URL."""
    local = {}
    if parsed.get('inlineImages') and not media.is_available():
        # Unlike remote URLs, Gmail attachment bytes cannot be queued without
        # storing a second binary copy in SQLite. Retry the message next sync
        # once the configured media disk is back instead of losing its CID
        # references forever.
        raise RuntimeError('Email media store is unavailable')
    now = int(time.time())
    for item in parsed.get('inlineImages') or []:
        data = item.get('data')
        cid = item.get('cid')
        if not cid or not data:
            continue
        if len(data) > media.MAX_IMAGE_BYTES:
            continue
        key = media.url_hash(f'gmail:{provider_message_id}:cid:{cid.lower()}')
        digest, ext, size = media.store(data, item.get('contentType'))
        db.execute(
            """INSERT OR REPLACE INTO email_images
               (url_hash, url, content_hash, extension, content_type, byte_size,
                status, attempt_count, error, created_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, 'stored', 0, NULL, ?, ?)""",
            (key, f'cid:{cid}', digest, ext, item.get('contentType'), size, now, now),
        )
        local[cid.lower()] = f'/api/email/images/{key}'
    return local


def _store_parsed_message(db, account_id: str, provider_message_id: str, parsed: dict) -> str | None:
    """Sanitize + insert a parsed message (gmail_client.parse_message or
    imap_client.parse_message output — both use the same flat shape). Returns
    the new row's id, or None if it was already synced.

    ON CONFLICT DO NOTHING rather than a bare INSERT: callers do their own
    existence check first, which is a check-then-act, and the per-account
    lock only covers callers in this process. Letting the index decide turns
    a lost race into "someone else already has it" instead of an
    IntegrityError that aborts the whole sync.
    """
    inline_urls = _store_inline_images(db, provider_message_id, parsed)
    body_html, image_refs = sanitize_email_html(parsed.get('bodyHtml') or '', inline_urls)
    row_id = str(ULID())
    cur = db.execute(
        """
        INSERT INTO emails
            (id, account_id, provider_message_id, thread_id, subject, sender, sender_email,
             snippet, body_text, body_html, label_ids, received_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account_id, provider_message_id) DO NOTHING
        """,
        (
            row_id, account_id, provider_message_id, parsed['threadId'], parsed['subject'],
            parsed['sender'], parsed['senderEmail'], parsed['snippet'], parsed['bodyText'],
            body_html, json.dumps(parsed['labelIds']), parsed['receivedAt'], int(time.time()),
        ),
    )
    if not cur.rowcount:
        db.commit()
        return None
    images.queue_images(db, image_refs)
    db.commit()
    return row_id


def _insert_message(db, account_id: str, gmail_id: str, access_token: str) -> str | None:
    """Fetch + parse + insert one Gmail message if not already present.
    Returns the new row's id, or None if it was already synced, or if it
    404s (safe to call twice).

    A message that was listed (history.list or messages.list) can be gone by
    the time this fetches it — Gmail auto-purges spam/trash, and a user can
    delete between the list and the fetch. That's routine for a mailbox-wide
    mirror, not a sync failure: letting it raise would abort the whole
    backfill/incremental walk without advancing the cursor, so every retry
    hits the same already-gone message and the account never syncs again.
    """
    existing = db.execute(
        'SELECT id FROM emails WHERE account_id=? AND provider_message_id=?', (account_id, gmail_id)
    ).fetchone()
    if existing:
        return None
    try:
        raw = gmail_client.get_message(access_token, gmail_id)
    except gmail_client.GmailApiError as e:
        if e.status_code == 404:
            return None
        raise
    parsed = gmail_client.parse_message(raw)
    for item in parsed.get('inlineImages') or []:
        if item.get('data') is None and item.get('attachmentId'):
            item['data'] = gmail_client.get_attachment(
                access_token, gmail_id, item['attachmentId']
            )
    return _store_parsed_message(db, account_id, parsed['gmailId'], parsed)


def _insert_imap_message(db, account_id: str, uid: int, conn) -> str | None:
    """Fetch + parse + insert one IMAP message (Outlook or generic) if not
    already present. Returns the new row's id, or None if already synced, or
    if the message is gone (deleted between SEARCH and FETCH) — same
    routine-not-failure reasoning as _insert_message's Gmail 404 handling."""
    existing = db.execute(
        'SELECT id FROM emails WHERE account_id=? AND provider_message_id=?', (account_id, str(uid))
    ).fetchone()
    if existing:
        return None
    raw = imap_client.fetch_message(conn, uid)
    if raw is None:
        return None
    parsed = imap_client.parse_message(raw, uid)
    return _store_parsed_message(db, account_id, parsed['providerMessageId'], parsed)


def _backfill(db, account_row, access_token: str) -> list[str]:
    # Snapshot the history-id baseline before paging through messages, not
    # after: messages.list is newest-first, so a message that arrives while
    # a multi-page backfill is in flight can land before pagination reaches
    # it and be skipped by this run. If the baseline is captured afterwards
    # (via get_profile), it's guaranteed >= that message's own history
    # event, so the next incremental sync's list_history(start_history_id=
    # baseline) excludes it too — permanently. Capturing the baseline first
    # means the worst case is a redundant, idempotent re-fetch next cycle
    # instead of silent data loss.
    profile = gmail_client.get_profile(access_token)
    new_ids: list[str] = []
    page_token = None
    while True:
        page = gmail_client.list_all_message_ids(access_token, page_token)
        for msg in page.get('messages') or []:
            row_id = _insert_message(db, account_row['id'], msg['id'], access_token)
            if row_id:
                new_ids.append(row_id)
                _enqueue_classification(row_id)
        page_token = page.get('nextPageToken')
        if not page_token:
            break
    db.execute(
        'UPDATE email_accounts SET history_id=? WHERE id=?',
        (profile.get('historyId'), account_row['id']),
    )
    db.commit()
    return new_ids


def _incremental(db, account_row, access_token: str) -> list[str]:
    new_ids: list[str] = []
    page_token = None
    latest_history_id = account_row['history_id']
    while True:
        page = gmail_client.list_history(access_token, account_row['history_id'], page_token)
        for entry in page.get('history') or []:
            for added in entry.get('messagesAdded') or []:
                gmail_id = (added.get('message') or {}).get('id')
                if gmail_id:
                    row_id = _insert_message(db, account_row['id'], gmail_id, access_token)
                    if row_id:
                        new_ids.append(row_id)
                        _enqueue_classification(row_id)
        if page.get('historyId'):
            latest_history_id = page['historyId']
        page_token = page.get('nextPageToken')
        if not page_token:
            break
    db.execute(
        'UPDATE email_accounts SET history_id=? WHERE id=?',
        (latest_history_id, account_row['id']),
    )
    db.commit()
    return new_ids


def _imap_backfill(db, account_row, conn, status: dict) -> list[str]:
    """Full-mailbox listing — first connect, or UIDVALIDITY has changed
    since last sync (see imap_client.search_all_uids). `status` is the
    UIDVALIDITY/UIDNEXT snapshot taken *before* the walk (same
    before-not-after reasoning as Gmail's _backfill baseline)."""
    new_ids: list[str] = []
    for uid in imap_client.search_all_uids(conn):
        row_id = _insert_imap_message(db, account_row['id'], uid, conn)
        if row_id:
            new_ids.append(row_id)
            _enqueue_classification(row_id)
    db.execute(
        'UPDATE email_accounts SET uid_validity=?, uid_next=? WHERE id=?',
        (status['uidValidity'], status['uidNext'], account_row['id']),
    )
    db.commit()
    return new_ids


def _imap_incremental(db, account_row, conn, status: dict) -> list[str]:
    new_ids: list[str] = []
    for uid in imap_client.search_uids_since(conn, account_row['uid_next']):
        row_id = _insert_imap_message(db, account_row['id'], uid, conn)
        if row_id:
            new_ids.append(row_id)
            _enqueue_classification(row_id)
    db.execute(
        'UPDATE email_accounts SET uid_validity=?, uid_next=? WHERE id=?',
        (status['uidValidity'], status['uidNext'], account_row['id']),
    )
    db.commit()
    return new_ids


def sync_account(account_row) -> dict:
    """account_row: an email_accounts row (sqlite3.Row or dict).

    Returns {'status': 'busy'} without doing anything if this account is
    already syncing — see _account_locks.
    """
    lock = _lock_for(account_row['id'])
    if not lock.acquire(blocking=False):
        return {'status': 'busy', 'newCount': 0}
    try:
        return _sync_account_locked(account_row)
    finally:
        lock.release()


def _missing_config_error(db, provider: str) -> dict | None:
    """Provider misconfiguration (no OAuth client in Settings) is not an
    account-specific sync failure — it's returned directly, before ever
    touching the account row, rather than being written to last_sync_error."""
    if provider == 'gmail':
        settings = _get_oauth_settings(db)
        if not (settings and settings.get('google_oauth_client_id') and settings.get('google_oauth_client_secret')):
            return {'status': 'error', 'error': 'Google OAuth client not configured'}
    elif provider == 'outlook':
        settings = _get_outlook_oauth_settings(db)
        if not (settings and settings.get('microsoft_oauth_client_id') and settings.get('microsoft_oauth_client_secret')):
            return {'status': 'error', 'error': 'Microsoft OAuth client not configured'}
    return None


def _sync_gmail(db, account_row) -> list[str]:
    settings = _get_oauth_settings(db)
    access_token = gmail_client.get_valid_access_token(
        db, dict(account_row), settings['google_oauth_client_id'], settings['google_oauth_client_secret']
    )
    if account_row['history_id'] is None:
        return _backfill(db, account_row, access_token)
    try:
        return _incremental(db, account_row, access_token)
    except gmail_client.HistoryExpiredError:
        return _backfill(db, account_row, access_token)


def _sync_imap(db, account_row) -> list[str]:
    provider = account_row['provider']
    if provider == 'outlook':
        settings = _get_outlook_oauth_settings(db)
        access_token = outlook_client.get_valid_access_token(
            db, dict(account_row),
            settings['microsoft_oauth_client_id'], settings['microsoft_oauth_client_secret'],
        )
        conn = imap_client.connect(
            outlook_client.IMAP_HOST, outlook_client.IMAP_PORT,
            username=account_row['email_address'], access_token=access_token,
        )
    else:  # 'imap'
        conn = imap_client.connect(
            account_row['imap_host'], account_row['imap_port'],
            username=account_row['imap_username'], password=account_row['imap_password'],
        )
    try:
        conn.select(imap_client.INBOX, readonly=True)
        status = imap_client.folder_status(conn)
        if account_row['uid_validity'] is None or status['uidValidity'] != account_row['uid_validity']:
            return _imap_backfill(db, account_row, conn, status)
        return _imap_incremental(db, account_row, conn, status)
    finally:
        conn.logout()


def _sync_account_locked(account_row) -> dict:
    db = get_db()
    provider = account_row['provider']

    config_error = _missing_config_error(db, provider)
    if config_error:
        return config_error

    try:
        new_ids = _sync_gmail(db, account_row) if provider == 'gmail' else _sync_imap(db, account_row)

        now = int(time.time())
        db.execute(
            'UPDATE email_accounts SET last_synced_at=?, last_sync_error=NULL, updated_at=? WHERE id=?',
            (now, now, account_row['id']),
        )
        db.commit()
        return {'status': 'ok', 'newCount': len(new_ids)}
    except Exception as e:
        now = int(time.time())
        db.execute(
            'UPDATE email_accounts SET last_sync_error=?, updated_at=? WHERE id=?',
            (str(e), now, account_row['id']),
        )
        db.commit()
        return {'status': 'error', 'error': str(e)}
