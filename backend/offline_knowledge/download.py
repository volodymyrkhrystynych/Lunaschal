"""Fetching ZIM archives from the Kiwix mirrors, resumably.

Stack Overflow is 107 GB. Everything odd about this module follows from that
one number:

* **The transfer is resumable and the row is the record of it.** The live rate
  and ETA sit in an in-memory registry like `backend/fanfic/download.py`'s
  `_dl_progress`, but the byte count is checkpointed to `knowledge_downloads`
  so a restart resumes instead of restarting. `connection.py`'s
  `_reset_stale_knowledge_downloads` parks an orphaned row at `paused` rather
  than `error` for the same reason.
* **A resumed `.part` is verified against the Metalink's piece hashes before
  it is trusted.** A mirror may have rotated to a newer build between sessions,
  in which case appending to yesterday's bytes produces a file that is wrong
  and only says so after the final hash -- by which point the alternative to
  keeping it is fetching 107 GB again. Reading the part back at 4 MiB per sha-1
  is a couple of minutes; re-downloading is not.
* **`verifying` is a status.** A transfer assembled across two sessions cannot
  carry an incremental hash, so the whole file is read once at the end.
* **One download at a time.** The mirrors are donated bandwidth and the archive
  drive is one spindle.

**This module writes into `settings.knowledge_root`, the user's own archive
folder.** That is a deliberate reversal: the reader half of this feature
promised never to write there, and the user chose one root over two when the
downloader was added. It brings a state the reader never had to worry about --
the root can be unwritable -- and the four ways that happens want four
different sentences, which `root_state` is for.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

import requests
from ulid import ULID

from backend.db.connection import get_db
from backend.offline_knowledge import archive as _archive_mod
from backend.offline_knowledge import catalog, registry
from backend.research.web import UnsafeUrl, assert_public_url

logger = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
# Checkpoint the byte count this often, not every chunk: 107 GB is 107,000
# chunks, and that many writes to a WAL nobody is reading buys nothing.
CHECKPOINT_BYTES = 16 * 1024 * 1024
# Headroom over the archive's own size, matching backend/piano/archive.py and
# backend/routes/backup.py. A 107 GB transfer that dies at 98% for want of a
# gigabyte costs a day.
SPACE_MARGIN = 1.05
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 120
PART_SUFFIX = '.part'

_progress: dict[str, dict] = {}
_guard = threading.Lock()
# Ids asked to stop. Pause and cancel both land here; the row says which it
# was, so the worker does not have to.
_stopping: set[str] = set()
_worker: threading.Thread | None = None
_worker_guard = threading.Lock()


class DownloadRefused(RuntimeError):
    """The download cannot even be attempted, and the message says why."""


class _Stopped(Exception):
    """Pause or cancel, raised out of the byte or hash loop."""


# --- the destination ---

def _is_readonly_mount(path: Path) -> bool:
    """A filesystem mounted read-only, as distinct from one this user cannot
    write to. `os.access` answers False for both and their fixes are unrelated
    -- fsck versus `uid=`/`gid=` in fstab, which is what exFAT needs, since it
    stores no POSIX ownership of its own. Same split, and the same `ST_RDONLY`
    bit, as `backend/routes/backup.py`.
    """
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except OSError:
        return False


def root_state() -> dict:
    """Where downloads would go, and whether they could.

    Four outcomes, deliberately not collapsed into a boolean: each one is a
    different thing for the user to go and do.
    """
    root = _archive_mod.configured_root()
    if root is None:
        return {'root': None, 'state': 'unset',
                'reason': 'No archive folder is configured. Choose one in '
                          'Settings → Knowledge Library.'}
    if not root.is_dir():
        return {'root': str(root), 'state': 'missing',
                'reason': f'{root} is not a folder — the drive may be unplugged.'}
    if _is_readonly_mount(root):
        return {'root': str(root), 'state': 'readonly',
                'reason': f'{root} is on a read-only filesystem, so downloads '
                          'cannot be saved there.'}
    if not os.access(root, os.W_OK):
        return {'root': str(root), 'state': 'permissions',
                'reason': f'{root} is not writable by this user. An exFAT drive '
                          'needs uid=/gid= in its mount options.'}
    return {'root': str(root), 'state': 'writable', 'reason': None}


def _require_root() -> Path:
    state = root_state()
    if state['state'] != 'writable':
        raise DownloadRefused(state['reason'])
    return Path(state['root'])


# --- progress registry ---

def progress(download_id: str) -> dict | None:
    with _guard:
        found = _progress.get(download_id)
        return dict(found) if found else None


def all_progress() -> dict[str, dict]:
    with _guard:
        return {k: dict(v) for k, v in _progress.items()}


def _set_progress(download_id: str, **kw) -> None:
    with _guard:
        _progress.setdefault(download_id, {}).update(kw)


def _clear_progress(download_id: str) -> None:
    with _guard:
        _progress.pop(download_id, None)


def _should_stop(download_id: str) -> bool:
    with _guard:
        return download_id in _stopping


def request_stop(download_id: str) -> None:
    with _guard:
        _stopping.add(download_id)


def _clear_stop(download_id: str) -> None:
    with _guard:
        _stopping.discard(download_id)


# --- rows ---

def _now() -> int:
    return int(time.time())


def _set_status(download_id: str, status: str, **columns) -> None:
    db = get_db()
    sets = ['status=?', 'updated_at=?']
    params: list = [status, _now()]
    for key, value in columns.items():
        sets.append(f'{key}=?')
        params.append(value)
    if status in ('done', 'error'):
        sets.append('finished_at=?')
        params.append(_now())
    params.append(download_id)
    db.execute(f'UPDATE knowledge_downloads SET {", ".join(sets)} WHERE id=?', params)
    db.commit()


def row(download_id: str) -> dict | None:
    found = get_db().execute(
        'SELECT * FROM knowledge_downloads WHERE id=?', (download_id,)
    ).fetchone()
    return dict(found) if found else None


def rows() -> list[dict]:
    return [dict(r) for r in get_db().execute(
        'SELECT * FROM knowledge_downloads ORDER BY created_at DESC'
    ).fetchall()]


def queue(entry: dict, meta: dict) -> dict:
    """Record one archive to fetch. `entry` is a catalogue entry, `meta` its
    parsed Metalink -- both re-fetched server-side, never taken from the client.
    """
    root = _require_root()
    filename = meta.get('filename') or f"{entry.get('name', 'archive')}.zim"
    if '/' in filename or '\\' in filename or not filename.endswith('.zim'):
        raise DownloadRefused(f'The catalogue offered an implausible filename: {filename!r}')
    if not meta.get('mirrors'):
        raise DownloadRefused('The catalogue listed no mirrors for this archive.')

    dest = root / filename
    if dest.exists():
        raise DownloadRefused(f'{filename} is already in the archive folder.')

    db = get_db()
    clash = db.execute(
        "SELECT id FROM knowledge_downloads WHERE filename=?"
        " AND status IN ('queued','downloading','verifying','paused')",
        (filename,),
    ).fetchone()
    if clash:
        raise DownloadRefused(f'{filename} is already queued.')

    now = _now()
    download_id = str(ULID())
    db.execute(
        'INSERT INTO knowledge_downloads (id, zim_name, filename, title, '
        ' catalog_uuid, meta4_url, source_url, total_bytes, downloaded_bytes, '
        ' sha256, md5, piece_length, pieces_sha1, dest_path, status, '
        ' created_at, updated_at) '
        "VALUES (?,?,?,?,?,?,'',?,0,?,?,?,?,?,'queued',?,?)",
        (download_id, entry.get('name', ''), filename, entry.get('title', ''),
         entry.get('uuid', ''), entry.get('meta4Url', ''), meta.get('size'),
         meta.get('sha256'), meta.get('md5'), meta.get('pieceLength'),
         json.dumps(meta.get('pieces') or []), str(dest), now, now),
    )
    db.commit()
    ensure_worker()
    return row(download_id)


def resume(download_id: str) -> dict | None:
    found = row(download_id)
    if not found or found['status'] not in ('paused', 'error'):
        return found
    _clear_stop(download_id)
    _set_status(download_id, 'queued', error=None)
    ensure_worker()
    return row(download_id)


def pause(download_id: str) -> dict | None:
    found = row(download_id)
    if not found:
        return None
    request_stop(download_id)
    if found['status'] in ('queued',):
        _set_status(download_id, 'paused')
    return row(download_id)


def delete(download_id: str) -> bool:
    found = row(download_id)
    if not found:
        return False
    request_stop(download_id)
    db = get_db()
    db.execute('DELETE FROM knowledge_downloads WHERE id=?', (download_id,))
    db.commit()
    part = Path(found['dest_path'] + PART_SUFFIX)
    try:
        part.unlink(missing_ok=True)
    except OSError:
        logger.warning('could not remove %s', part)
    _clear_progress(download_id)
    _clear_stop(download_id)
    return True


# --- the transfer ---

def _verified_prefix(part: Path, piece_length: int, pieces: list[str]) -> int:
    """How many bytes of an existing `.part` are provably the right bytes.

    Hashes each complete piece and stops at the first that disagrees, so a
    `.part` written against a build the mirror has since replaced is truncated
    back to the last good boundary instead of being appended to. Returns the
    byte offset to resume from.
    """
    if not pieces or not piece_length:
        # No piece list: the only check available is the whole-file hash at the
        # end, so resume optimistically and let `verifying` catch it.
        try:
            return part.stat().st_size
        except OSError:
            return 0
    good = 0
    try:
        with part.open('rb') as handle:
            for expected in pieces:
                block = handle.read(piece_length)
                if len(block) < piece_length:
                    break  # a partial final piece is never trusted
                if hashlib.sha1(block).hexdigest() != expected:
                    break
                good += piece_length
    except OSError:
        return 0
    return good


def _open_stream(url: str, offset: int):
    headers = {'Accept-Encoding': 'identity'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    resp = requests.get(
        url, headers=headers, stream=True, allow_redirects=True,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
    )
    resp.raise_for_status()
    # A server that ignores Range answers 200 with the whole file. Appending
    # that to what we already have writes a second copy of the archive into
    # the middle of the first, and the only sign is a checksum failure hours
    # later -- so treat it as "start over" rather than as success.
    if offset and resp.status_code != 206:
        resp.close()
        return None
    return resp


def _fetch(found: dict, dest: Path) -> None:
    download_id = found['id']
    part = Path(str(dest) + PART_SUFFIX)
    pieces = json.loads(found['pieces_sha1'] or '[]')
    piece_length = found['piece_length'] or 0
    total = found['total_bytes'] or 0

    meta = catalog.fetch_meta4(found['meta4_url']) if found['meta4_url'] else {}
    mirrors = meta.get('mirrors') or ([found['source_url']] if found['source_url'] else [])
    if not mirrors:
        raise DownloadRefused('No mirrors are listed for this archive any more.')

    # Kiwix republishes an archive under the same URL when it rebuilds it, so
    # a download resumed days later can find a *different file* behind the
    # same name. Piece verification alone would not catch it -- the bytes
    # already on disk are consistent with the build they came from -- and the
    # mismatch would only surface at the final whole-file hash. Compare what
    # the catalogue publishes now against what was queued, and start over
    # against the new build rather than splicing two of them together.
    fresh = meta.get('sha256')
    if fresh and found['sha256'] and fresh != found['sha256']:
        logger.info('%s was rebuilt upstream; restarting the download',
                    found['filename'])
        part.unlink(missing_ok=True)
        pieces = meta.get('pieces') or []
        piece_length = meta.get('pieceLength') or 0
        total = meta.get('size') or 0
        get_db().execute(
            'UPDATE knowledge_downloads SET sha256=?, md5=?, total_bytes=?, '
            'piece_length=?, pieces_sha1=?, downloaded_bytes=0, updated_at=? '
            'WHERE id=?',
            (fresh, meta.get('md5'), total, piece_length or None,
             json.dumps(pieces), _now(), download_id))
        get_db().commit()
        found = row(download_id) or found

    offset = _verified_prefix(part, piece_length, pieces) if part.exists() else 0
    if part.exists() and offset < part.stat().st_size:
        with part.open('r+b') as handle:
            handle.truncate(offset)

    last_error: Exception | None = None
    for url in mirrors:
        try:
            assert_public_url(url)
        except UnsafeUrl as exc:
            # The catalogue chooses these hosts, so this is the SSRF shape --
            # web.py's guard, not repos/git.py's, whose threat is git's own
            # transports.
            logger.warning('refusing mirror %s: %s', url, exc)
            last_error = exc
            continue
        try:
            resp = _open_stream(url, offset)
            if resp is None:
                offset = 0
                part.unlink(missing_ok=True)
                resp = _open_stream(url, 0)
                if resp is None:
                    continue
            _set_status(download_id, 'downloading', source_url=url, error=None)
            _stream_to(resp, part, download_id, offset, total)
            return
        except requests.RequestException as exc:
            last_error = exc
            logger.warning('mirror %s failed: %s', url, exc)
            # No re-verification here: whatever this session appended sits on
            # top of a prefix already checked above, so the file size is
            # trustworthy and re-hashing 107 GB per failed mirror is not.
            offset = part.stat().st_size if part.exists() else 0
            continue
    raise DownloadRefused(f'Every mirror failed. Last error: {last_error}')


def _stream_to(resp, part: Path, download_id: str, offset: int, total: int) -> None:
    done = offset
    since_checkpoint = 0
    started = time.monotonic()
    started_at = done
    with resp, part.open('ab' if offset else 'wb') as handle:
        for chunk in resp.iter_content(CHUNK_BYTES):
            if _should_stop(download_id):
                handle.flush()
                _set_status(download_id, 'paused', downloaded_bytes=done)
                raise _Stopped()
            if not chunk:
                continue
            handle.write(chunk)
            done += len(chunk)
            since_checkpoint += len(chunk)
            elapsed = max(time.monotonic() - started, 1e-6)
            _set_progress(
                download_id, downloadedBytes=done, totalBytes=total,
                bytesPerSecond=(done - started_at) / elapsed, phase='downloading',
            )
            if since_checkpoint >= CHECKPOINT_BYTES:
                handle.flush()
                get_db().execute(
                    'UPDATE knowledge_downloads SET downloaded_bytes=?, updated_at=?'
                    ' WHERE id=?', (done, _now(), download_id))
                get_db().commit()
                since_checkpoint = 0
    get_db().execute(
        'UPDATE knowledge_downloads SET downloaded_bytes=?, updated_at=? WHERE id=?',
        (done, _now(), download_id))
    get_db().commit()


def _verify(found: dict, part: Path) -> None:
    expected = found['sha256']
    if not expected:
        return
    _set_status(found['id'], 'verifying')
    _set_progress(found['id'], phase='verifying')
    digest = hashlib.sha256()
    with part.open('rb') as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b''):
            if _should_stop(found['id']):
                _set_status(found['id'], 'paused')
                raise _Stopped()
            digest.update(block)
    if digest.hexdigest() != expected:
        # The `.part` is kept on purpose. A half-good file that is obviously a
        # `.part` can be resumed or deleted; the same bytes sitting in the
        # library under a real `.zim` name are a corrupt archive the reader
        # will try to open.
        raise DownloadRefused(
            'The downloaded file does not match the checksum the catalogue '
            'published. The partial file has been kept so it can be resumed.'
        )


def run_one(download_id: str) -> None:
    found = row(download_id)
    if not found:
        return
    dest = Path(found['dest_path'])
    part = Path(str(dest) + PART_SUFFIX)
    _set_progress(download_id, phase='starting', downloadedBytes=found['downloaded_bytes'],
                  totalBytes=found['total_bytes'])
    try:
        _require_root()
        total = found['total_bytes'] or 0
        if total:
            free = shutil.disk_usage(dest.parent).free + (
                part.stat().st_size if part.exists() else 0)
            if free < total * SPACE_MARGIN:
                raise DownloadRefused(
                    f'Not enough free space: {total * SPACE_MARGIN / 1e9:.1f} GB '
                    f'needed, {free / 1e9:.1f} GB free.'
                )
        _fetch(found, dest)
        # Re-read: _fetch rewrites the hashes if the archive was rebuilt
        # upstream mid-transfer.
        _verify(row(download_id) or found, part)
        part.replace(dest)
        # `expected_size` is what makes the `truncated` health state reachable:
        # a file later found smaller than what was published is damaged, not
        # merely small.
        _set_status(download_id, 'done', downloaded_bytes=dest.stat().st_size, error=None)
        try:
            registry.sync(force=True)
            get_db().execute(
                'UPDATE knowledge_archives SET expected_size=? WHERE path=?',
                (dest.stat().st_size, str(dest)))
            get_db().commit()
            _archive_mod._open.cache_clear()
        except Exception:
            logger.exception('archive scan after download failed')
    except _Stopped:
        logger.info('knowledge download %s paused', download_id)
    except Exception as exc:
        logger.warning('knowledge download %s failed: %s', download_id, exc)
        _set_status(download_id, 'error', error=str(exc))
    finally:
        _clear_progress(download_id)
        _clear_stop(download_id)


# --- the serial worker ---

def _next_queued() -> str | None:
    found = get_db().execute(
        "SELECT id FROM knowledge_downloads WHERE status='queued'"
        ' ORDER BY created_at LIMIT 1'
    ).fetchone()
    return found['id'] if found else None


def _drain(app) -> None:
    global _worker
    try:
        with app.app_context():
            while True:
                download_id = _next_queued()
                if not download_id:
                    return
                if _should_stop(download_id):
                    _set_status(download_id, 'paused')
                    _clear_stop(download_id)
                    continue
                run_one(download_id)
    finally:
        with _worker_guard:
            _worker = None


def ensure_worker() -> None:
    """Start the single drain thread if it is not already running.

    One at a time, in `created_at` order: the mirrors are donated bandwidth and
    the archive drive is one spindle, so three concurrent 107 GB pulls would be
    slower than three sequential ones as well as ruder.
    """
    global _worker
    try:
        from flask import current_app
        app = current_app._get_current_object()
    except Exception:
        return
    with _worker_guard:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_drain, args=(app,), daemon=True)
        _worker.start()
