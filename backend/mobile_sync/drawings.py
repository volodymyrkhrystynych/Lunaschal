"""Explicit native drawing saves: immutable files plus a transactional receipt.

No PencilKit decoder exists on the server. Originals stay opaque; the validated
PNG supplies the shared Paper preview. Web pages are never converted implicitly.
"""
import hashlib
import io
import json
import os
import re
import tempfile
import time

from PIL import Image

from backend.paper import storage
from backend.routes.journal import _client_id
from .feed import change_dict, database

MAX_INK = 64 * 1024 * 1024
MAX_PREVIEW = 16 * 1024 * 1024
MAX_REQUEST = MAX_INK + MAX_PREVIEW + 64 * 1024


def _read(part, maximum):
    if part is None:
        raise ValueError('Both ink and preview files are required')
    data = part.read(maximum + 1)
    if not data or len(data) > maximum:
        raise ValueError('Drawing file is empty or exceeds its size limit')
    return data


def _publish(path, data):
    # A same-root symlink into another paper is unsafe too. A failed DB commit
    # may leave an unreferenced immutable file, but never damage saved content.
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve() != path:
        raise ValueError('Drawing storage is not a canonical paper directory')
    fd, temporary = tempfile.mkstemp(prefix='.native-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _prune(directory, page_id, keep):
    # Called while holding the write transaction. Both its old committed files
    # and proposed replacements are kept, so rollback cannot lose either copy.
    pattern = re.compile(rf'native-{re.escape(page_id)}-[0-9a-f]{{64}}\.(drawing|png)')
    for path in directory.iterdir():
        if pattern.fullmatch(path.name) and str(path) not in keep and path.resolve() == path:
            try:
                path.unlink()
            except OSError:
                pass  # Retrying a later Save can finish harmless cleanup.


def apply(body, ink_part, preview_part):
    if not isinstance(body, dict) or set(body) != {
        'id', 'epoch', 'paperId', 'pageId', 'baseRevision', 'title', 'format',
    }:
        raise ValueError('Invalid native drawing metadata')
    for field in ('id', 'paperId', 'pageId'):
        if not isinstance(body[field], str) or not _client_id(body[field]):
            raise ValueError(f'{field} must be a ULID')
    if (not isinstance(body['epoch'], str) or not body['epoch']
            or type(body['baseRevision']) is not int or body['baseRevision'] < 0
            or body['format'] != 'pencilkit-v1'
            or not isinstance(body['title'], str) or len(body['title']) > 500):
        raise ValueError('Invalid native drawing version or title')
    ink = _read(ink_part, MAX_INK)
    preview = _read(preview_part, MAX_PREVIEW)
    try:
        with Image.open(io.BytesIO(preview)) as image:
            if image.format != 'PNG' or image.size != (1240, 1754):
                raise ValueError('Preview must be a 1240 × 1754 PNG')
            image.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Invalid drawing preview') from exc
    ink_hash = hashlib.sha256(ink).hexdigest()
    preview_hash = hashlib.sha256(preview).hexdigest()
    fingerprint = hashlib.sha256(json.dumps(
        {'kind': 'native-drawing', **body, 'ink': ink_hash, 'preview': preview_hash},
        sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    with database(write=True) as db:
        prior = db.execute('SELECT * FROM mobile_sync_operations WHERE id=?', (body['id'],)).fetchone()
        if prior:
            if prior['request_hash'] != fingerprint:
                return {'error': 'Operation id already used for different content'}, 409
            return json.loads(prior['response']), prior['status']
        if db.execute('SELECT id FROM mobile_sync_state').fetchone()[0] != body['epoch']:
            return {'error': 'Server history changed', 'resetRequired': True}, 410
        current = db.execute("SELECT * FROM mobile_sync_changes WHERE collection='paper_native_ink' AND record_id=? ORDER BY sequence DESC LIMIT 1",
                             (body['pageId'],)).fetchone()
        page = db.execute('SELECT paper_id,image_path FROM paper_pages WHERE id=?', (body['pageId'],)).fetchone()
        native = db.execute('SELECT id,file_path FROM paper_native_ink WHERE id=?', (body['pageId'],)).fetchone()
        new = body['baseRevision'] == 0
        if new:
            # Reuse of a deleted identity must not resurrect a paper or page.
            conflict = current is not None or page is not None or db.execute(
                "SELECT 1 FROM mobile_sync_changes WHERE collection='paper_pages' AND record_id=? LIMIT 1",
                (body['pageId'],),
            ).fetchone() is not None or db.execute(
                "SELECT 1 FROM mobile_sync_changes WHERE collection='papers' AND record_id=? LIMIT 1",
                (body['paperId'],),
            ).fetchone() is not None or db.execute('SELECT 1 FROM papers WHERE id=?', (body['paperId'],)).fetchone() is not None
        else:
            conflict = (page is None or page['paper_id'] != body['paperId'] or native is None
                        or current is None or current['sequence'] != body['baseRevision'])
        now = int(time.time())
        if conflict:
            result, status = {'error': 'Drawing changed; keep this copy and resolve the conflict',
                              'conflict': True, 'current': change_dict(current) if current else None}, 409
        else:
            directory = storage.paper_dir(body['paperId'])
            ink_path = directory / f"native-{body['pageId']}-{ink_hash}.drawing"
            preview_path = directory / f"native-{body['pageId']}-{preview_hash}.png"
            _publish(ink_path, ink)
            _publish(preview_path, preview)
            _prune(directory, body['pageId'], {str(ink_path), str(preview_path),
                   native['file_path'] if native else None, page['image_path'] if page else None})
            if new:
                db.execute('INSERT INTO papers(id,title,created_at,updated_at,content_updated_at) VALUES (?,?,?,?,?)',
                           (body['paperId'], body['title'].strip() or 'Untitled drawing', now, now, now))
                db.execute('INSERT INTO paper_pages(id,paper_id,width,height,image_path,created_at,updated_at) VALUES (?,?,2100,2970,?,?,?)',
                           (body['pageId'], body['paperId'], str(preview_path), now, now))
            else:
                db.execute('UPDATE paper_pages SET image_path=?,updated_at=? WHERE id=?',
                           (str(preview_path), now, body['pageId']))
                # A web rename or filing action is independent of native ink.
                db.execute('UPDATE papers SET updated_at=?,content_updated_at=? WHERE id=?', (now, now, body['paperId']))
            db.execute('''INSERT INTO paper_native_ink(id,format,file_path,sha256,preview_sha256,created_at,updated_at)
                          VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                          file_path=excluded.file_path,sha256=excluded.sha256,
                          preview_sha256=excluded.preview_sha256,updated_at=excluded.updated_at''',
                       (body['pageId'], body['format'], str(ink_path), ink_hash, preview_hash, now, now))
            changes = [change_dict(db.execute('SELECT * FROM mobile_sync_changes WHERE collection=? AND record_id=? ORDER BY sequence DESC LIMIT 1',
                        (collection, id)).fetchone()) for collection, id in (
                            ('papers', body['paperId']), ('paper_pages', body['pageId']), ('paper_native_ink', body['pageId']))]
            result, status = {'operationId': body['id'], 'changes': changes}, 200
        db.execute('INSERT INTO mobile_sync_operations(id,request_hash,response,status,created_at) VALUES (?,?,?,?,?)',
                   (body['id'], fingerprint, json.dumps(result), status, now))
        return result, status
