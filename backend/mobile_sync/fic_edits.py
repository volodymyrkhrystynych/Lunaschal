"""Library edits from the phone: a fic's rating, review, shelf and folders, and
which of its chapters have been read.

Last write wins, with no revision check. A fic's record moves every time a
chapter downloads (its snapshot carries the chapter-derived counts), so a
review written offline would otherwise come back as a conflict whenever the
library updated in the meantime, and there is nothing to merge: these are
one person's choices about their own library.
"""
from backend.fanfic import library
from backend.routes.journal import _client_id
from .feed import change_dict

FIC_FIELDS = {'rating', 'review', 'shelf', 'folderIds'}


def validate(body):
    if not isinstance(body['epoch'], str) or type(body['baseRevision']) is not int or body['baseRevision'] < 0:
        raise ValueError('An epoch and nonnegative baseRevision are required')
    data = body['data']
    if not isinstance(data, dict):
        raise ValueError('Operation data must be an object')
    if body['collection'] == 'fics':
        if body['action'] != 'update' or not data or set(data) - FIC_FIELDS:
            raise ValueError('Only rating, review, shelf and folderIds may be updated')
        if 'rating' in data:
            library.check_rating(data['rating'])
        if 'review' in data:
            library.check_review(data['review'])
        if 'shelf' in data:
            library.check_shelf(data['shelf'])
        if 'folderIds' in data:
            folders = data['folderIds']
            if not isinstance(folders, list) or not all(isinstance(f, str) and _client_id(f) for f in folders):
                raise ValueError('folderIds must be a list of folder ULIDs')
        return
    if body['action'] not in ('create', 'delete'):
        raise ValueError('Chapter reads support create/delete only')
    if set(data) != {'ficId'} or not isinstance(data['ficId'], str) or not _client_id(data['ficId']):
        raise ValueError('Expected the chapter\'s ficId')


def latest(db, collection, record_id):
    return db.execute(
        'SELECT * FROM mobile_sync_changes WHERE collection=? AND record_id=? ORDER BY sequence DESC LIMIT 1',
        (collection, record_id)).fetchone()


def _gone(message):
    return {'error': message, 'conflict': True}, 409


def mutate(db, body):
    data, record_id = body['data'], body['recordId']
    if body['collection'] == 'fics':
        if db.execute('SELECT 1 FROM fics WHERE id=?', (record_id,)).fetchone() is None:
            return _gone('This book is no longer in the library.')
        # Folders first: it is the one part that can be refused, and it
        # checks before writing, so a refusal leaves nothing half-applied.
        if 'folderIds' in data and not library.set_folders(db, record_id, data['folderIds']):
            return _gone('A folder in this edit was deleted. Choose the folders again.')
        sets, params = [], []
        if 'rating' in data:
            sets.append('rating=?')
            params.append(data['rating'])
        if 'review' in data:
            sets.append('review=?')
            params.append(library.clean_review(data['review']))
        if sets:
            db.execute(f"UPDATE fics SET {', '.join(sets)}, updated_at=unixepoch() WHERE id=?",
                       (*params, record_id))
        if 'shelf' in data:
            library.set_shelf(db, record_id, data['shelf'])
    else:
        if not library.set_chapters_read(db, data['ficId'], [record_id], body['action'] == 'create'):
            return _gone('This chapter is no longer in the library.')
        if latest(db, body['collection'], record_id) is None:
            # Unmarking a chapter the server never had marked: record the
            # tombstone, so the phone has a change to acknowledge.
            db.execute("INSERT INTO mobile_sync_changes(collection, record_id, payload)"
                       " VALUES ('fic_chapter_reads', ?, NULL)", (record_id,))
    return {'operationId': body['id'], 'change': change_dict(latest(db, body['collection'], record_id))}, 200
