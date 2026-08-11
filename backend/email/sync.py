"""Per-account Gmail sync: History API for steady-state incremental polling,
a full-mailbox listing for first connect and as the recovery path when a
history cursor has expired (Gmail only retains history for a bounded
window). The goal is a complete local mirror for backup, so there's no
day-bounded window anywhere — re-listing the whole mailbox is cheap even on
recovery, since already-synced messages are skipped before the expensive
per-message fetch (see _insert_message). Shaped like
backend/newspapers/sync.py::sync_today — try/except, returns a status dict,
never raises, so one bad cycle can't kill the scheduler thread that calls
this in a loop."""
import json
import threading
import time

from ulid import ULID

from backend.ai.background import run_bg
from backend.db.connection import get_db
from backend.email import gmail_client, images
from backend.email.sanitize import sanitize_email_html

# One sync per account at a time. The scheduler treats an account as due for
# the whole time `last_synced_at IS NULL`, which on a first connect is the
# entire multi-hour backfill — so a manual POST /api/email/sync landing
# mid-backfill used to start a second concurrent walk of the same mailbox.
# The two raced on _insert_message's check-then-insert and one died on the
# (account_id, gmail_id) unique index, which cost far more than the duplicate
# work: the crash discarded that run's classification queue and left
# history_id NULL, so the next sync restarted from zero.
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


def _insert_message(db, account_id: str, gmail_id: str, access_token: str) -> str | None:
    """Fetch + parse + insert one message if not already present. Returns the
    new row's id, or None if it was already synced (safe to call twice)."""
    existing = db.execute(
        'SELECT id FROM emails WHERE account_id=? AND gmail_id=?', (account_id, gmail_id)
    ).fetchone()
    if existing:
        return None
    raw = gmail_client.get_message(access_token, gmail_id)
    parsed = gmail_client.parse_message(raw)
    body_html, image_refs = sanitize_email_html(parsed.get('bodyHtml') or '')
    row_id = str(ULID())
    # ON CONFLICT DO NOTHING rather than a bare INSERT: the SELECT above is a
    # check-then-act, and the per-account lock only covers callers in this
    # process. Letting the index decide turns a lost race into "someone else
    # already has it" instead of an IntegrityError that aborts the whole sync.
    cur = db.execute(
        """
        INSERT INTO emails
            (id, account_id, gmail_id, thread_id, subject, sender, sender_email,
             snippet, body_text, body_html, label_ids, received_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account_id, gmail_id) DO NOTHING
        """,
        (
            row_id, account_id, parsed['gmailId'], parsed['threadId'], parsed['subject'],
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


def _sync_account_locked(account_row) -> dict:
    db = get_db()
    settings = _get_oauth_settings(db)
    client_id = settings.get('google_oauth_client_id') if settings else None
    client_secret = settings.get('google_oauth_client_secret') if settings else None
    if not client_id or not client_secret:
        return {'status': 'error', 'error': 'Google OAuth client not configured'}

    try:
        access_token = gmail_client.get_valid_access_token(
            db, dict(account_row), client_id, client_secret
        )

        if account_row['history_id'] is None:
            new_ids = _backfill(db, account_row, access_token)
        else:
            try:
                new_ids = _incremental(db, account_row, access_token)
            except gmail_client.HistoryExpiredError:
                new_ids = _backfill(db, account_row, access_token)

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
