"""Background fetcher for images referenced by email HTML.

Deliberately the lowest-priority network work in the app. Nothing waits on
it: the mail is already readable, the classifier reads text, and an image
that arrives an hour late costs nothing. So this runs on its own slow daemon
loop with a per-request delay, rather than on backend/ai/background.py's
executor (which is for work a user triggered seconds ago) or inside the sync
path (where it would stall the import of the next message behind a CDN).

Three properties worth keeping:

- **The URLs come from email, so they are hostile input.** Every fetch goes
  through research/web.py's `assert_public_url`, the same SSRF guard the
  research agent uses — without it, a crafted `<img src>` pointed at
  127.0.0.1:5000 or a cloud metadata endpoint would be fetched by the server
  with whatever local access it has.
- **Dedup happens twice.** The `email_images` primary key stops a repeated
  URL being fetched again; the content hash stops identical bytes being
  stored again (see media.py). The second is what collapses a mailbox full
  of one company's logo into a single file.
- **A missing drive is a pause, not a failure.** When the external store is
  unavailable the loop simply doesn't run — rows stay `pending` and are
  picked up whenever it comes back, instead of being burned through as
  failures while the disk is unplugged.
"""

import threading
import time

import requests

from backend.db.connection import get_db
from backend.email import media
from backend.research.web import UnsafeUrl, assert_public_url

_POLL_SECONDS = 30
# Politeness gap between fetches. This is a background archive job with no
# reader waiting; there is no reason to hit a CDN hard.
_FETCH_DELAY_SECONDS = 0.5
_BATCH = 25
_MAX_ATTEMPTS = 3
_TIMEOUT = 20

_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) Lunaschal/1.0'


def _mark(db, url_hash: str, **fields) -> None:
    from backend.db.connection import build_update
    build_update(db, 'email_images', fields, 'url_hash=?', (url_hash,))
    db.commit()


def queue_images(db, images: list[tuple[str, str]]) -> int:
    """Record (url_hash, url) pairs as pending. Returns how many were new.

    INSERT OR IGNORE, so a URL already known — pending, stored or failed — is
    left exactly as it is. Re-seeing a logo must not reset a terminal state
    or re-queue bytes already on disk.
    """
    if not images:
        return 0
    now = int(time.time())
    cur = db.executemany(
        'INSERT OR IGNORE INTO email_images (url_hash, url, status, created_at)'
        " VALUES (?, ?, 'pending', ?)",
        [(h, u, now) for h, u in images],
    )
    db.commit()
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def _fetch_one(db, row) -> None:
    url_hash, url = row['url_hash'], row['url']
    attempts = row['attempt_count'] + 1
    try:
        assert_public_url(url)
    except UnsafeUrl as e:
        # Terminal: a URL that resolves somewhere private will still resolve
        # somewhere private tomorrow, and retrying is how an SSRF probe gets
        # a second chance.
        _mark(db, url_hash, status='skipped', error=str(e), attempt_count=attempts)
        return

    try:
        resp = requests.get(
            url, timeout=_TIMEOUT, stream=True,
            headers={'User-Agent': _USER_AGENT, 'Accept': 'image/*'},
        )
        resp.raise_for_status()
        content_type = resp.headers.get('Content-Type', '')
        if not content_type.split(';')[0].strip().lower().startswith('image/'):
            _mark(db, url_hash, status='skipped', attempt_count=attempts,
                  error=f'Not an image: {content_type or "no content-type"}')
            return

        # Read with a ceiling rather than trusting Content-Length, which is
        # advisory and absent on chunked responses.
        chunks, total = [], 0
        for chunk in resp.iter_content(64 * 1024):
            total += len(chunk)
            if total > media.MAX_IMAGE_BYTES:
                _mark(db, url_hash, status='skipped', attempt_count=attempts,
                      error=f'Larger than {media.MAX_IMAGE_BYTES} bytes')
                return
            chunks.append(chunk)
        data = b''.join(chunks)
        if not data:
            _mark(db, url_hash, status='skipped', error='Empty response',
                  attempt_count=attempts)
            return

        digest, ext, size = media.store(data, content_type)
        _mark(
            db, url_hash, status='stored', content_hash=digest, extension=ext,
            content_type=content_type.split(';')[0].strip().lower(),
            byte_size=size, error=None, attempt_count=attempts,
            fetched_at=int(time.time()),
        )
    except Exception as e:
        # 'failed' stays retryable until _MAX_ATTEMPTS, then stops being
        # selected — a dead CDN shouldn't be asked forever.
        _mark(db, url_hash, status='failed', error=str(e)[:300], attempt_count=attempts)


def fetch_pending(limit: int = _BATCH) -> int:
    """Fetch up to `limit` queued images. Returns how many were attempted."""
    if not media.is_available():
        return 0
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM email_images
        WHERE status='pending' OR (status='failed' AND attempt_count < ?)
        ORDER BY created_at
        LIMIT ?
        """,
        (_MAX_ATTEMPTS, limit),
    ).fetchall()
    for i, row in enumerate(rows):
        if i:
            time.sleep(_FETCH_DELAY_SECONDS)
        _fetch_one(db, row)
    return len(rows)


def pending_count(db) -> int:
    return db.execute(
        "SELECT COUNT(*) c FROM email_images"
        " WHERE status='pending' OR (status='failed' AND attempt_count < ?)",
        (_MAX_ATTEMPTS,),
    ).fetchone()['c']


def _loop() -> None:
    while True:
        try:
            fetch_pending()
        except Exception as e:
            print(f'Email image fetch failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_image_fetcher() -> None:
    threading.Thread(target=_loop, daemon=True).start()
