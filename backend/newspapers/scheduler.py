"""Persistent, single-download queue, with Toronto-time daily scheduling."""
import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ulid import ULID

from backend.db.connection import get_db
from backend.newspapers import issues, pressreader

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_wake = threading.Event()
_thread = None


def queue_issue(date, *, retry=False, wake=True):
    issues.validate_date(date)
    now = int(time.time())
    with _lock:
        db = get_db()
        if issues.get_issue(date):
            return {'date': date, 'status': 'complete', 'error': ''}
        db.execute('INSERT OR IGNORE INTO newspaper_downloads (id, date, created_at, updated_at) VALUES (?, ?, ?, ?)',
                   (str(ULID()), date, now, now))
        if retry:
            db.execute("UPDATE newspaper_downloads SET status='queued', attempts=0, next_attempt_at=0, error='', updated_at=? WHERE date=? AND status IN ('failed', 'sign-in-required', 'complete')", (now, date))
        db.commit()
        row = dict(db.execute('SELECT * FROM newspaper_downloads WHERE date=?', (date,)).fetchone())
    if wake:
        _wake.set()
    return row


def tick(now=None):
    now = int(time.time()) if now is None else now
    local = datetime.fromtimestamp(now, ZoneInfo('America/Toronto'))
    db = get_db()
    settings = db.execute('SELECT newspapers_auto_download FROM settings WHERE id=1').fetchone()
    if settings and settings[0] and local.hour >= 6:
        queue_issue(local.date().isoformat(), wake=False)
    with _lock:
        # A successful interactive reconnect wakes requests blocked by sign-in.
        try:
            session_updated = int(pressreader.session_path().stat().st_mtime)
        except OSError:
            session_updated = 0
        db.execute("UPDATE newspaper_downloads SET status='queued', attempts=0, next_attempt_at=0 WHERE status='sign-in-required' AND updated_at < ?", (session_updated,))
        row = db.execute("SELECT * FROM newspaper_downloads WHERE status='queued' OR (status='failed' AND attempts < 4 AND next_attempt_at <= ?) ORDER BY created_at, date LIMIT 1", (now,)).fetchone()
        if row is None:
            db.commit()
            return
        date = row['date']
        db.execute("UPDATE newspaper_downloads SET status='downloading', attempts=attempts+1, error='', updated_at=? WHERE date=?", (now, date))
        db.commit()
    status, error = 'complete', ''
    try:
        # Covers a crash after the PDF was stored but before the job completed.
        if not issues.get_issue(date):
            pressreader.download_issue(date)
    except FileExistsError:
        pass  # Manual import won the race; its immutable issue is already ready.
    except pressreader.SignInRequired as exc:
        status, error = 'sign-in-required', str(exc)
    except pressreader.DownloadError as exc:
        status, error = 'failed', str(exc)
    except (ValueError, OSError):
        status, error = 'failed', 'Could not archive the issue PDF. Check the archive drive and retry.'
    except Exception:
        # Provider exceptions may contain credentials or a signed URL.
        status, error = 'failed', 'The issue download failed. Reconnect PressReader or retry later.'
    with _lock:
        db.execute('UPDATE newspaper_downloads SET status=?, error=?, next_attempt_at=?, updated_at=? WHERE date=?',
                   (status, error, now + 3600, int(time.time()), date))
        db.commit()


def start_newspaper_scheduler():
    global _thread
    if os.environ.get('LUNASCHAL_NO_SCHEDULERS'):
        return
    with _lock:
        if _thread and _thread.is_alive():
            _wake.set()
            return

        def run():
            while True:
                _wake.clear()
                try:
                    tick()
                except Exception:
                    logger.warning('Newspaper download queue tick failed')
                _wake.wait(60)

        _thread = threading.Thread(target=run, name='newspaper-downloads', daemon=True)
        _thread.start()
