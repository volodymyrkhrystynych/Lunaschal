"""Desktop-compatible chapter bookmarks with replay and conflict protection."""
import math
import time

from backend.routes.journal import _client_id
from .feed import change_dict


def validate(body):
    if body['action'] not in ('create', 'delete'):
        raise ValueError('Bookmarks support create/delete only')
    if not isinstance(body['epoch'], str) or type(body['baseRevision']) is not int or body['baseRevision'] < 0:
        raise ValueError('An epoch and nonnegative baseRevision are required')
    data = body['data']
    if body['action'] == 'delete':
        if data != {} or body['baseRevision'] < 1:
            raise ValueError('Bookmark deletion requires an existing revision and empty data')
        return
    if not isinstance(data, dict) or set(data) != {'ficId', 'chapterId', 'type', 'scrollPosition', 'previousContinueId'}:
        raise ValueError('Expected book, chapter, type, scroll position and previous continue bookmark')
    if not all(isinstance(data[key], str) and _client_id(data[key]) for key in ('ficId', 'chapterId')):
        raise ValueError('Book and chapter IDs must be ULIDs')
    if data['type'] not in ('favorite', 'continue'):
        raise ValueError('Unknown bookmark type')
    position = data['scrollPosition']
    if type(position) not in (int, float) or not math.isfinite(position) or not 0 <= position <= 1:
        raise ValueError('Bookmark position must be between zero and one')
    previous = data['previousContinueId']
    if previous is not None and (not isinstance(previous, str) or not _client_id(previous)):
        raise ValueError('Previous continue bookmark must be a ULID or null')
    if data['type'] == 'favorite' and (previous is not None or body['baseRevision'] != 0):
        raise ValueError('A favorite must not replace a continue bookmark')
    if (previous is None) != (body['baseRevision'] == 0):
        raise ValueError('Previous bookmark and revision must agree')


def latest(db, id):
    return db.execute(
        "SELECT * FROM mobile_sync_changes WHERE collection='fic_bookmarks' AND record_id=? ORDER BY sequence DESC LIMIT 1",
        (id,)).fetchone()


def conflict(current=None):
    return {'error': 'Bookmark changed elsewhere. Your saved choice is kept for review.',
            'conflict': True, 'current': change_dict(current) if current else None}, 409


def mutate(db, body):
    data = body['data']
    if body['action'] == 'delete':
        current = latest(db, body['recordId'])
        if current is None or current['payload'] is None or current['sequence'] != body['baseRevision']:
            return conflict(current)
        db.execute('DELETE FROM fic_bookmarks WHERE id=?', (body['recordId'],))
    else:
        if db.execute('SELECT 1 FROM fic_chapters WHERE id=? AND fic_id=?',
                      (data['chapterId'], data['ficId'])).fetchone() is None:
            return {'error': 'This chapter is no longer in the library.', 'conflict': True}, 409
        if latest(db, body['recordId']) is not None:
            return conflict(latest(db, body['recordId']))
        if data['type'] == 'continue':
            existing = db.execute("SELECT id FROM fic_bookmarks WHERE fic_id=? AND type='continue'", (data['ficId'],)).fetchone()
            previous = existing['id'] if existing else None
            current = latest(db, previous) if previous else None
            if previous != data['previousContinueId'] or (current and current['sequence'] != body['baseRevision']):
                return conflict(current)
            db.execute("DELETE FROM fic_bookmarks WHERE fic_id=? AND type='continue'", (data['ficId'],))
        db.execute('INSERT INTO fic_bookmarks(id,fic_id,chapter_id,type,scroll_position,created_at) VALUES (?,?,?,?,?,?)',
                   (body['recordId'], data['ficId'], data['chapterId'], data['type'], data['scrollPosition'], int(time.time())))
    return {'operationId': body['id'], 'change': change_dict(latest(db, body['recordId']))}, 200
