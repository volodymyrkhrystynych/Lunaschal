"""External-drive catalog and storage for large Piano files."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ulid import ULID
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from backend.db.connection import get_db, row_to_dict
from backend.ops.backup_config import get_config
from backend.piano import library
from backend.piano.musicxml import ScoreImportError, normalize_score, score_metadata

COLLECTION = 'piano'
ARCHIVE_ENV = 'PIANO_ARCHIVE_ROOT'
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 250

_SCORE_SUFFIXES = {'.musicxml', '.xml', '.mxl'}
_MIDI_SUFFIXES = {'.mid', '.midi'}
_PDF_SUFFIXES = {'.pdf'}
_ARCHIVE_SUFFIXES = {'.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.xz'}
_AUDIO_SUFFIXES = {'.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.opus'}
_VIDEO_SUFFIXES = {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v'}
_IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.tif', '.tiff'}


class ArchiveUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveLocation:
    root: Path | None
    configured: bool
    available: bool
    writable: bool
    destination: str | None
    reason: str | None


def location(db=None) -> ArchiveLocation:
    override = os.environ.get(ARCHIVE_ENV, '').strip()
    if override:
        root = Path(override).expanduser().resolve()
        probe = root if root.exists() else root.parent
        available = probe.is_dir()
        writable = available and os.access(probe, os.W_OK)
        return ArchiveLocation(
            root=root,
            configured=True,
            available=available,
            writable=writable,
            destination=str(root),
            reason=None if available else 'The archive folder is unavailable.',
        )

    cfg = get_config(db or get_db())
    destination = cfg['path'].strip()
    if not destination:
        return ArchiveLocation(
            root=None,
            configured=False,
            available=False,
            writable=False,
            destination=None,
            reason='Choose the main backup folder in Settings first.',
        )
    base = Path(destination).expanduser()
    if not base.is_dir():
        return ArchiveLocation(
            root=(base / 'archive' / COLLECTION),
            configured=True,
            available=False,
            writable=False,
            destination=destination,
            reason='The backup drive is not connected.',
        )
    writable = os.access(base, os.W_OK)
    return ArchiveLocation(
        root=(base / 'archive' / COLLECTION).resolve(),
        configured=True,
        available=True,
        writable=writable,
        destination=destination,
        reason=None if writable else 'The backup drive is not writable.',
    )


def require_root(db=None, *, create: bool = False) -> Path:
    state = location(db)
    if not state.available or state.root is None:
        raise ArchiveUnavailable(state.reason or 'The archive is unavailable.')
    if create and not state.writable:
        raise ArchiveUnavailable(state.reason or 'The archive is not writable.')
    if create:
        state.root.mkdir(parents=True, exist_ok=True)
    return state.root


def status(db=None) -> dict:
    connection = db or get_db()
    state = location(connection)
    summary = connection.execute(
        'SELECT COUNT(*) AS item_count, '
        'COALESCE(SUM(size_bytes), 0) AS size_bytes, '
        'COALESCE(SUM(favorite), 0) AS favorite_count '
        'FROM media_archive_items WHERE collection=?',
        (COLLECTION,),
    ).fetchone()
    free_bytes = total_bytes = None
    probe = state.root if state.root and state.root.exists() else (
        state.root.parent if state.root else None
    )
    if state.available and probe:
        try:
            usage = shutil.disk_usage(probe)
            free_bytes, total_bytes = usage.free, usage.total
        except OSError:
            pass
    return {
        'configured': state.configured,
        'available': state.available,
        'writable': state.writable,
        'root': str(state.root) if state.root else None,
        'destination': state.destination,
        'reason': state.reason,
        'itemCount': summary['item_count'],
        'favoriteCount': summary['favorite_count'],
        'sizeBytes': summary['size_bytes'],
        'freeBytes': free_bytes,
        'totalBytes': total_bytes,
    }


def list_items(
    *,
    query: str = '',
    favorites_only: bool = False,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db=None,
) -> dict:
    connection = db or get_db()
    limit = max(1, min(MAX_PAGE_SIZE, int(limit)))
    offset = max(0, int(offset))
    where = ['collection=?']
    params: list[object] = [COLLECTION]
    if favorites_only:
        where.append('favorite=1')
    query = query.strip()
    if query:
        where.append('(title LIKE ? OR creator LIKE ? OR source_filename LIKE ?)')
        needle = f'%{query}%'
        params.extend((needle, needle, needle))
    clause = ' AND '.join(where)
    total = connection.execute(
        f'SELECT COUNT(*) FROM media_archive_items WHERE {clause}', params
    ).fetchone()[0]
    rows = connection.execute(
        'SELECT * FROM media_archive_items '
        f'WHERE {clause} ORDER BY favorite DESC, title COLLATE NOCASE, id '
        'LIMIT ? OFFSET ?',
        [*params, limit, offset],
    ).fetchall()
    root = location(connection).root
    return {
        'items': [_item_dict(row, root) for row in rows],
        'total': total,
        'limit': limit,
        'offset': offset,
    }


def store_upload(upload: FileStorage, *, source_url: str | None = None, db=None) -> dict:
    if not upload.filename:
        raise ValueError('Choose a file to archive.')
    filename = secure_filename(upload.filename)
    if not filename:
        raise ValueError('The file name is not usable.')
    connection = db or get_db()
    root = require_root(connection, create=True)
    item_id = str(ULID())
    directory = root / 'managed' / item_id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / filename
    try:
        digest = _stream_upload(upload, path)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('The file is empty.')
        row = _insert_path(
            path,
            root=root,
            item_id=item_id,
            source_url=source_url,
            content_type=upload.mimetype or None,
            sha256=digest,
            parse_score=True,
            db=connection,
        )
        connection.commit()
        return _item_dict(row, root)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def scan(*, db=None) -> dict:
    """Index files already placed under the archive root.

    No hashes and no MusicXML parsing here: both make a 250,000-score dataset
    unnecessarily expensive. A score is fully validated when it is favorited.
    Rows are committed in batches so an interrupted scan still makes progress.
    """
    connection = db or get_db()
    root = require_root(connection, create=True)
    indexed = updated = skipped = 0
    pending = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if not name.startswith('.') and not (Path(directory) / name).is_symlink()
        ]
        for filename in filenames:
            path = Path(directory) / filename
            if filename.startswith('.') or path.is_symlink() or not path.is_file():
                skipped += 1
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                skipped += 1
                continue
            existing = connection.execute(
                'SELECT id, size_bytes FROM media_archive_items '
                'WHERE collection=? AND relative_path=?',
                (COLLECTION, relative),
            ).fetchone()
            if existing:
                size = path.stat().st_size
                if size != existing['size_bytes']:
                    connection.execute(
                        'UPDATE media_archive_items SET size_bytes=?, updated_at=? WHERE id=?',
                        (size, int(time.time()), existing['id']),
                    )
                    updated += 1
                    pending += 1
            else:
                _insert_path(path, root=root, db=connection)
                indexed += 1
                pending += 1
            if pending >= 500:
                connection.commit()
                pending = 0
    connection.commit()
    return {'indexed': indexed, 'updated': updated, 'skipped': skipped}


def set_favorite(item_id: str, favorite: bool, *, db=None) -> dict | None:
    connection = db or get_db()
    row = connection.execute(
        'SELECT * FROM media_archive_items WHERE id=? AND collection=?',
        (item_id, COLLECTION),
    ).fetchone()
    if row is None:
        return None
    root = require_root(connection)
    piece_id = row['piano_piece_id']
    created_piece_id = None
    removed_piece_id = None
    try:
        if favorite and row['practice_compatible'] and not piece_id:
            path = resolve_path(root, row['relative_path'])
            if path is None or not path.is_file():
                raise ArchiveUnavailable('The archived file is missing.')
            try:
                score = normalize_score(path.read_bytes(), row['source_filename'])
            except ScoreImportError:
                # A scan classifies by extension so it can catalog hundreds of
                # thousands of files cheaply. First favorite is the validation
                # boundary; a broken score remains a perfectly valid archive
                # favorite, it simply stops claiming it can enter Practice.
                connection.execute(
                    'UPDATE media_archive_items '
                    'SET practice_compatible=0 WHERE id=?',
                    (item_id,),
                )
            else:
                created_piece_id, piece = library.store_piece(
                    score, row['source_filename'], db=connection, commit=False
                )
                piece_id = created_piece_id
                connection.execute(
                    'UPDATE media_archive_items SET title=?, creator=? WHERE id=?',
                    (piece['title'], piece['composer'], item_id),
                )
        elif not favorite and piece_id:
            removed_piece_id = piece_id
            library.delete_piece(
                piece_id, db=connection, commit=False, delete_files=False
            )
            piece_id = None
        connection.execute(
            'UPDATE media_archive_items '
            'SET favorite=?, piano_piece_id=?, updated_at=? WHERE id=?',
            (1 if favorite else 0, piece_id, int(time.time()), item_id),
        )
        connection.commit()
        if removed_piece_id:
            from backend.piano import storage

            storage.delete_piano_dir(removed_piece_id)
    except Exception:
        connection.rollback()
        if created_piece_id:
            # The insert was rolled back, but the filesystem write was not.
            from backend.piano import storage

            storage.delete_piano_dir(created_piece_id)
        raise
    updated = connection.execute(
        'SELECT * FROM media_archive_items WHERE id=?', (item_id,)
    ).fetchone()
    return _item_dict(updated, root)


def item_path(item_id: str, *, db=None) -> tuple[Path, object] | None:
    connection = db or get_db()
    row = connection.execute(
        'SELECT * FROM media_archive_items WHERE id=? AND collection=?',
        (item_id, COLLECTION),
    ).fetchone()
    if row is None:
        return None
    root = require_root(connection)
    path = resolve_path(root, row['relative_path'])
    if path is None or not path.is_file():
        raise ArchiveUnavailable('The archived file is missing.')
    return path, row


def resolve_path(root: Path, relative_path: str) -> Path | None:
    if Path(relative_path).is_absolute():
        return None
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _insert_path(
    path: Path,
    *,
    root: Path,
    item_id: str | None = None,
    source_url: str | None = None,
    content_type: str | None = None,
    sha256: str | None = None,
    parse_score: bool = False,
    db=None,
):
    connection = db or get_db()
    item_id = item_id or str(ULID())
    relative = path.relative_to(root).as_posix()
    media_type, compatible = classify_file(path)
    title = _title_from_filename(path.name)
    creator = None
    if parse_score and compatible:
        try:
            score = normalize_score(path.read_bytes(), path.name)
            title, creator = score_metadata(score, title)
        except ScoreImportError:
            compatible = False
    now = int(time.time())
    content_type = content_type or mimetypes.guess_type(path.name)[0]
    connection.execute(
        'INSERT INTO media_archive_items '
        '(id,collection,title,creator,media_type,source_filename,relative_path,'
        'source_url,content_type,size_bytes,sha256,practice_compatible,favorite,'
        'created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)',
        (
            item_id,
            COLLECTION,
            title,
            creator,
            media_type,
            path.name,
            relative,
            source_url,
            content_type,
            path.stat().st_size,
            sha256,
            1 if compatible else 0,
            now,
            now,
        ),
    )
    return connection.execute(
        'SELECT * FROM media_archive_items WHERE id=?', (item_id,)
    ).fetchone()


def classify_file(path: Path) -> tuple[str, bool]:
    suffix = path.suffix.lower()
    if suffix in _SCORE_SUFFIXES:
        return 'score', True
    if suffix in _MIDI_SUFFIXES:
        return 'midi', False
    if suffix in _PDF_SUFFIXES:
        return 'document', False
    if suffix in _ARCHIVE_SUFFIXES:
        return 'archive', False
    if suffix in _AUDIO_SUFFIXES:
        return 'audio', False
    if suffix in _VIDEO_SUFFIXES:
        return 'video', False
    if suffix in _IMAGE_SUFFIXES:
        return 'image', False
    return 'file', False


def _item_dict(row, root: Path | None) -> dict:
    result = row_to_dict(row)
    path = resolve_path(root, row['relative_path']) if root else None
    result['available'] = bool(path and path.is_file())
    result['fileUrl'] = f"/api/piano/archive/items/{row['id']}/file"
    return result


def _title_from_filename(filename: str) -> str:
    name = filename
    for suffix in ('.tar.gz', '.tar.bz2', '.tar.xz'):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    else:
        name = Path(name).stem
    return re.sub(r'[_-]+', ' ', name).strip() or 'Untitled file'


def _stream_upload(upload: FileStorage, path: Path) -> str:
    """Copy and hash in one pass so a multi-gigabyte upload is not reread."""
    digest = hashlib.sha256()
    with path.open('wb') as handle:
        for chunk in iter(lambda: upload.stream.read(1024 * 1024), b''):
            handle.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()
