"""Active-library media manifests. Stored paths never cross the API boundary."""
import hashlib
import mimetypes
from functools import lru_cache
from pathlib import Path

from backend.journal import storage as journal
from backend.paper import storage as paper
from backend.study import storage as study
from backend.newspapers import storage as newspapers
from .feed import database

# table -> (path column, resolver). Resolvers only accept existing feature roots.
MEDIA = {
    'journal_attachments': ('path', journal.resolve_stored_path),
    'paper_pages': ('image_path', paper.resolve_stored_path),
    'paper_page_images': ('file_path', paper.resolve_stored_path),
    'study_sources': ('file_path', study.resolve_stored_path),
    'newspaper_frontpages': ('image_path', newspapers.resolve_stored_path),
}


def validate_collection(collection):
    if collection not in MEDIA:
        raise ValueError('Unsupported media collection')


def active_path(collection, row):
    if collection == 'journal_attachments' and row['kind'] == 'youtube':
        return None
    if collection == 'study_sources' and row['kind'] in study.ARCHIVED_KINDS:
        return None
    column, resolver = MEDIA[collection]
    raw = row[column]
    if not raw:
        return None
    # Pass the canonical path through the existing root guard too; this also
    # refuses symlinks which escape the allowed storage root.
    path = resolver(str(Path(raw).resolve()))
    if collection == 'study_sources' and path is not None:
        # Study's resolver accepts archive roots as well. Bulk defaults must not.
        if path.parent.parent != study.study_root():
            return None
    return path if path is not None and path.is_file() else None


@lru_cache(maxsize=512)
def _digest(path, size, mtime, ctime):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def describe(collection, row):
    path = active_path(collection, row)
    result = {'collection': collection, 'id': row['id'], 'available': False,
              'size': None, 'sha256': None, 'mime': None, 'url': None}
    if path is None:
        result['reason'] = 'Archive media excluded or file unavailable'
        return result, None
    before = path.stat()
    digest = _digest(str(path), before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise FileExistsError('File changed while preparing its manifest; retry')
    result.update(available=True, size=after.st_size, sha256=digest,
                  mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                  url=f'/api/mobile/media/{collection}/{row["id"]}/file?sha256={digest}')
    return result, path


def manifest(collection, *, after='', limit=50):
    validate_collection(collection)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100')
    if not isinstance(after, str) or len(after) > 128:
        raise ValueError('Invalid media page key')
    with database() as db:
        rows = db.execute(f'SELECT * FROM {collection} WHERE id>? ORDER BY id LIMIT ?',
                          (after, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {'items': [describe(collection, row)[0] for row in rows],
            'hasMore': more, 'after': rows[-1]['id'] if rows else after}


def lookup(collection, record_id):
    validate_collection(collection)
    with database() as db:
        row = db.execute(f'SELECT * FROM {collection} WHERE id=?', (record_id,)).fetchone()
    return describe(collection, row) if row else (None, None)
