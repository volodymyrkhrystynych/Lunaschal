"""Shared alert discovery for the manual refresh and periodic server poll."""
import threading
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.fanfic import download, xenforo
from backend.fanfic.xenforo import KNOWN_SITES

_lock = threading.Lock()


def refresh():
    # Serialize manual and automatic refreshes, including placeholder inserts.
    with _lock:
        return _refresh()


def _refresh():
    db = get_db()
    domains = [
        r['domain'] for r in
        db.execute('SELECT domain FROM site_cookies ORDER BY domain').fetchall()
        if r['domain'] in KNOWN_SITES
    ]
    errors: dict[str, str] = {}
    alerts_seen = 0
    # (site, thread_id) -> (ref, newest alert timestamp)
    newest: dict[tuple[str, str], tuple[xenforo.ThreadRef, int | None]] = {}
    for domain in domains:
        try:
            items = download.fetch_alerts(domain)
        except Exception as e:
            errors[domain] = str(e)
            continue
        alerts_seen += len(items)
        for item in items:
            key = (item.ref.domain, item.ref.thread_id)
            prev = newest.get(key)
            if prev is None or (item.alert_at or 0) > (prev[1] or 0):
                newest[key] = (item.ref, item.alert_at)

    flagged = new_imports = skipped_active = skipped_fresh = 0
    now = int(time.time())
    for (site, thread_id), (ref, alert_at) in newest.items():
        row = db.execute(
            'SELECT id, update_pending, download_status, last_checked_at'
            ' FROM fics WHERE site=? AND thread_id=?', (site, thread_id)).fetchone()
        if row:
            if (row['update_pending'] or row['download_status'] == 'downloading'
                    or download.is_active(row['id'])):
                skipped_active += 1
            elif (row['download_status'] == 'complete' and alert_at is not None
                  and row['last_checked_at'] is not None and alert_at < row['last_checked_at']):
                skipped_fresh += 1
            else:
                db.execute('UPDATE fics SET update_pending=1 WHERE id=?', (row['id'],))
                flagged += 1
        else:
            placeholder = ref.slug.replace('-', ' ').strip() or 'Importing…'
            db.execute(
                'INSERT INTO fics(id, title, source_type, source_url, site, thread_id,'
                ' update_pending, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)',
                (str(ULID()), placeholder, 'xenforo', ref.thread_url, ref.domain,
                 ref.thread_id, now, now))
            new_imports += 1
    db.commit()
    return {
        'flagged': flagged, 'newImports': new_imports,
        'skippedActive': skipped_active, 'skippedFresh': skipped_fresh,
        'alertsSeen': alerts_seen, 'errors': errors,
    }
