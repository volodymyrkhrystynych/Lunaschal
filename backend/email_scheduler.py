"""Background Gmail polling daemon.

Like the chat-title and briefing sweeps, there is no general scheduler in the
Flask backend — this is a small daemon thread on a fixed poll tick, gated by
each account's own `email_sync_interval_minutes`. The sync body lives in
backend/email/sync.py so tests can call it directly.
"""
import os
import threading
import time

from backend.db.connection import get_db
from backend.email import sync

_POLL_SECONDS = 60


def _global_sync_enabled(db) -> bool:
    row = db.execute('SELECT email_sync_enabled FROM settings LIMIT 1').fetchone()
    return bool(row['email_sync_enabled']) if row and row['email_sync_enabled'] is not None else True


def _accounts_due(db) -> list:
    row = db.execute('SELECT email_sync_interval_minutes FROM settings LIMIT 1').fetchone()
    interval_minutes = (row['email_sync_interval_minutes'] if row else None) or 15
    cutoff = int(time.time()) - interval_minutes * 60
    return db.execute(
        """
        SELECT * FROM email_accounts
        WHERE provider='gmail' AND sync_enabled=1 AND refresh_token IS NOT NULL
          AND (last_synced_at IS NULL OR last_synced_at <= ?)
        """,
        (cutoff,),
    ).fetchall()


def _scheduler_loop() -> None:
    while True:
        try:
            db = get_db()
            if _global_sync_enabled(db):
                for account in _accounts_due(db):
                    sync.sync_account(account)
        except Exception as e:
            print(f'Email sync failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_email_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    # Self-heal: classified_at IS NULL is the "pending" state for both
    # never-attempted and previously-failed rows, so anything left over from
    # a crash mid-classification just needs re-enqueuing once at startup.
    try:
        from backend.ai.email import sweep_unclassified
        sweep_unclassified()
    except Exception as e:
        print(f'Email classification sweep failed: {e}')
    threading.Thread(target=_scheduler_loop, daemon=True).start()
