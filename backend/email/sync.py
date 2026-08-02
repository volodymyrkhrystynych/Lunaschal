"""Per-account Gmail sync: History API for steady-state incremental polling,
a date-range listing for first connect and as the recovery path when a
history cursor has expired (Gmail only retains history for a bounded
window). Shaped like backend/newspapers/sync.py::sync_today — try/except,
returns a status dict, never raises, so one bad cycle can't kill the
scheduler thread that calls this in a loop."""
import json
import time
from datetime import date, timedelta

from ulid import ULID

from backend.ai.background import run_bg
from backend.db.connection import get_db
from backend.email import gmail_client

# Re-anchor window used only when a history cursor has expired — short and
# safe because UNIQUE(account_id, gmail_id) makes re-fetching idempotent.
HISTORY_RECOVERY_DAYS = 3


def _get_oauth_settings(db) -> dict | None:
    row = db.execute(
        'SELECT google_oauth_client_id, google_oauth_client_secret, email_backfill_days FROM settings LIMIT 1'
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
    row_id = str(ULID())
    db.execute(
        """
        INSERT INTO emails
            (id, account_id, gmail_id, thread_id, subject, sender, sender_email,
             snippet, body_text, label_ids, received_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row_id, account_id, parsed['gmailId'], parsed['threadId'], parsed['subject'],
            parsed['sender'], parsed['senderEmail'], parsed['snippet'], parsed['bodyText'],
            json.dumps(parsed['labelIds']), parsed['receivedAt'], int(time.time()),
        ),
    )
    db.commit()
    return row_id


def _backfill(db, account_row, access_token: str, backfill_days: int) -> list[str]:
    after_date = (date.today() - timedelta(days=backfill_days)).strftime('%Y/%m/%d')
    new_ids: list[str] = []
    page_token = None
    while True:
        page = gmail_client.list_message_ids_since(access_token, after_date, page_token)
        for msg in page.get('messages') or []:
            row_id = _insert_message(db, account_row['id'], msg['id'], access_token)
            if row_id:
                new_ids.append(row_id)
        page_token = page.get('nextPageToken')
        if not page_token:
            break
    profile = gmail_client.get_profile(access_token)
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
    """account_row: an email_accounts row (sqlite3.Row or dict)."""
    from backend.ai.email import classify_email

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
        backfill_days = (settings.get('email_backfill_days') if settings else None) or 30

        if account_row['history_id'] is None:
            new_ids = _backfill(db, account_row, access_token, backfill_days)
        else:
            try:
                new_ids = _incremental(db, account_row, access_token)
            except gmail_client.HistoryExpiredError:
                new_ids = _backfill(db, account_row, access_token, HISTORY_RECOVERY_DAYS)

        now = int(time.time())
        db.execute(
            'UPDATE email_accounts SET last_synced_at=?, last_sync_error=NULL, updated_at=? WHERE id=?',
            (now, now, account_row['id']),
        )
        db.commit()
        for email_id in new_ids:
            run_bg(lambda eid=email_id: classify_email(eid))
        return {'status': 'ok', 'newCount': len(new_ids)}
    except Exception as e:
        now = int(time.time())
        db.execute(
            'UPDATE email_accounts SET last_sync_error=?, updated_at=? WHERE id=?',
            (str(e), now, account_row['id']),
        )
        db.commit()
        return {'status': 'error', 'error': str(e)}
