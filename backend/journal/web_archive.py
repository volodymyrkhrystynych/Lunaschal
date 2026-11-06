"""A link on a journal entry, printed to a PDF by the user's own browser.

Any link that is not a YouTube video becomes a `kind='webpage'` attachment in
`import_status='queued'`, and waits there for the browser extension's archive
tab (`extension/archive.html`) to come by. The server never fetches the page
itself, and that is the point: the browser is the one that is logged in, so a
page behind the user's own session archives exactly as they saw it. Since
Chrome 136 a remote-debugging port is ignored on the default profile, so there
is no way to drive *that* browser from here — the extension has to pull.

The row is its own queue. `poll` leases the oldest queued page to one browser
for `LEASE_SECONDS`; a tab closed mid-page lets the lease run out and the page
is taken again. Nothing here is in memory except who was seen recently, which
only feeds the card's "archiver offline" line and is fine to lose on restart.

Three outcomes, and only one keeps a file:

- `ok` — the PDF is stored under ./data/journal/<id>/ (the SSD, and so the
  backup: pages are small and a dead link is exactly when the copy matters),
  with a screenshot poster beside it and the PDF's text in `transcript` so the
  entry's title and the chat agent can read what the page said.
- `login` — the extension decided the page was a login wall or a paywall
  (`extension/lib/archive.js`). A PDF of a sign-in form is worse than nothing,
  so none is kept; the card shows the link with a "needs login" badge. The
  browser *is* logged in, so this means even the user's session cannot see it.
- `error` — anything else. Kept as a message, never retried on its own.
"""
import io
import re
import threading
import time
from urllib.parse import urlparse

from backend.db.connection import get_db
from backend.journal import storage
from backend.research.web import UnsafeUrl, assert_public_url

LEASE_SECONDS = 180
# A control tab polls every 5 s. Six missed polls is a browser that is gone,
# not one that is busy printing — a print happens between polls, not instead
# of them for this long.
ONLINE_SECONDS = 30
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_POSTER_BYTES = 5 * 1024 * 1024
# Enough for the title and the chat agent; a 300-page PDF of a manual does not
# belong in a column that is read into prompts.
MAX_TEXT_CHARS = 100_000
MAX_TITLE_CHARS = 300

OUTCOMES = ('ok', 'login', 'error')
LOGIN_MESSAGE = 'This page needs a login, even in your browser — not archived.'

_lock = threading.RLock()
_seen: dict[str, float] = {}


class ArchiveConflict(ValueError):
    """The submit is for a page this browser does not hold any more."""


def valid_link(url: str) -> str:
    """The URL as stored, or ValueError. Public http(s) only.

    Checked here at attach time *and* against the final URL at submit: the
    browser that prints this runs on the server, so a link to the LAN — the
    qBittorrent WebUI, the router — would be printed and filed in the journal.
    """
    url = (url or '').strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('That does not look like a web link.')
    if parsed.username or parsed.password:
        raise ValueError('Links with a username or password in them are not archived.')
    try:
        assert_public_url(url)
    except UnsafeUrl as e:
        raise ValueError(str(e)) from e
    return url


def _client(client_id) -> str:
    if not isinstance(client_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', client_id):
        raise ValueError('Invalid browser connection identifier')
    _seen[client_id] = time.time()
    return client_id


def online() -> bool:
    cutoff = time.time() - ONLINE_SECONDS
    with _lock:
        return any(seen > cutoff for seen in _seen.values())


def status() -> dict:
    queued = get_db().execute(
        "SELECT COUNT(*) AS n FROM journal_attachments"
        " WHERE kind='webpage' AND import_status='queued'"
    ).fetchone()['n']
    return {'online': online(), 'queued': queued}


def connect(client_id) -> dict:
    with _lock:
        _client(client_id)
    return status()


def disconnect(client_id) -> None:
    """Forget the browser and hand back whatever it was holding, now rather
    than in three minutes."""
    with _lock:
        if isinstance(client_id, str):
            _seen.pop(client_id, None)
        db = get_db()
        db.execute(
            'UPDATE journal_attachments SET archive_client=NULL, archive_lease_until=NULL'
            " WHERE kind='webpage' AND import_status='queued' AND archive_client=?",
            (client_id,),
        )
        db.commit()


def poll(client_id) -> dict:
    """Lease the oldest queued page to this browser, or nothing.

    A page this browser already holds comes back first — a control tab reloaded
    mid-print picks its own job up again instead of waiting out its own lease.
    """
    with _lock:
        _client(client_id)
        db = get_db()
        now = int(time.time())
        row = db.execute(
            'SELECT id, source_url FROM journal_attachments'
            " WHERE kind='webpage' AND import_status='queued'"
            ' AND (archive_client=? OR archive_lease_until IS NULL OR archive_lease_until<=?)'
            ' ORDER BY (archive_client=?) DESC, created_at, id LIMIT 1',
            (client_id, now, client_id),
        ).fetchone()
        job = None
        if row:
            db.execute(
                'UPDATE journal_attachments SET archive_client=?, archive_lease_until=?'
                ' WHERE id=?',
                (client_id, now + LEASE_SECONDS, row['id']),
            )
            db.commit()
            job = {'id': row['id'], 'url': row['source_url']}
        return {'job': job, 'archiver': status()}


def page_text(pdf: bytes) -> str:
    """The PDF's text, for the title and the chat agent. Empty on any failure —
    a page printed as pictures is still a page worth keeping."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf))
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            text = (page.extract_text() or '').strip()
            if text:
                parts.append(text)
                total += len(text)
            if total >= MAX_TEXT_CHARS:
                break
        return '\n\n'.join(parts)[:MAX_TEXT_CHARS]
    except Exception:  # noqa: BLE001 — text is a bonus, the PDF is the record
        return ''


def submit(attachment_id: str, client_id, *, outcome: str, title: str | None = None,
           final_url: str | None = None, message: str | None = None,
           pdf: bytes | None = None, poster: bytes | None = None) -> str:
    """Record what the browser made of a page. Returns the entry id.

    ValueError for a malformed submit (a 400); ArchiveConflict when this browser
    no longer holds the page (a 409) — its lease ran out and another browser
    took it, or the page was deleted or retried under it.
    """
    if outcome not in OUTCOMES:
        raise ValueError('Unknown outcome')
    title = (title or '').strip()[:MAX_TITLE_CHARS] or None
    with _lock:
        _client(client_id)
        db = get_db()
        row = db.execute(
            'SELECT id, entry_id, kind, import_status, archive_client'
            ' FROM journal_attachments WHERE id=?',
            (attachment_id,),
        ).fetchone()
        if (row is None or row['kind'] != 'webpage' or row['import_status'] != 'queued'
                or row['archive_client'] != client_id):
            raise ArchiveConflict('This page is no longer waiting for this browser.')

        if outcome == 'ok':
            try:
                if final_url:
                    valid_link(final_url)
            except ValueError:
                outcome, message = 'error', 'The link redirected to a private address; not archived.'
        if outcome == 'ok':
            if not pdf or not pdf.startswith(b'%PDF-'):
                raise ValueError('The archive is not a PDF.')
            if len(pdf) > MAX_PDF_BYTES:
                outcome, message = 'error', 'The page printed to more than 50 MB; not archived.'

        fields: dict = {'archive_client': None, 'archive_lease_until': None}
        if title:
            fields['name'] = title
        if outcome == 'ok':
            fields.update(_store(attachment_id, pdf, poster))
            text = page_text(pdf)
            fields.update(
                import_status='ready', import_error=None,
                transcript=text or None,
                transcript_status='done' if text else 'idle',
            )
        elif outcome == 'login':
            fields.update(import_status='login_required',
                          import_error=(message or '').strip()[:500] or LOGIN_MESSAGE)
        else:
            fields.update(import_status='error',
                          import_error=(message or '').strip()[:500] or 'The browser could not archive this page.')

        cols = ', '.join(f'{k}=?' for k in fields)
        db.execute(f'UPDATE journal_attachments SET {cols} WHERE id=?',
                   (*fields.values(), attachment_id))
        db.commit()
        return row['entry_id']


def _store(attachment_id: str, pdf: bytes, poster: bytes | None) -> dict:
    dest = storage.attachment_path(attachment_id, 'pdf')
    if dest is None:
        raise ValueError('Invalid attachment id')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf)
    fields = {'path': str(dest), 'mime': 'application/pdf', 'size': len(pdf)}
    # A JPEG and nothing else: the thumbnail route serves this as image/jpeg.
    if poster and poster.startswith(b'\xff\xd8\xff') and len(poster) <= MAX_POSTER_BYTES:
        thumb = storage.thumb_path(attachment_id)
        if thumb is not None:
            thumb.write_bytes(poster)
            fields['thumb_path'] = str(thumb)
    return fields


def retry(attachment_id: str) -> bool:
    """Put a page that failed, or needed a login, back in the queue.

    The login case is the one this exists for: the user signs in to the site in
    that browser, then presses Retry.
    """
    with _lock:
        db = get_db()
        cur = db.execute(
            "UPDATE journal_attachments SET import_status='queued', import_error=NULL,"
            ' archive_client=NULL, archive_lease_until=NULL'
            " WHERE id=? AND kind='webpage' AND import_status IN ('error','login_required')",
            (attachment_id,),
        )
        db.commit()
        return bool(cur.rowcount)


def _reset_for_tests() -> None:
    with _lock:
        _seen.clear()
