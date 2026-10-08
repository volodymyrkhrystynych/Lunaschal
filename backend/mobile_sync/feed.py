"""Consistent bootstrap and change windows over an immutable revision log.

Each page's cursor fixes its upper watermark. A concurrent edit cannot move a
row out from under a paginated bootstrap: its prior version remains in the log.
Compaction keeps a latest-version baseline and invalidates cursors explicitly.
"""
import base64
import json
import sqlite3
from contextlib import contextmanager

from backend.db import connection
from backend.db.connection import mapping_to_dict
from .registry import COLLECTIONS

PROTOCOL_VERSION = 1
MAX_PAGE_SIZE = 200


class ResetRequired(ValueError):
    pass


@contextmanager
def database(write=False):
    # Independent connections avoid committing another Flask thread's work on
    # the repository's shared connection. WAL keeps read snapshots nonblocking.
    db = sqlite3.connect(connection._DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA recursive_triggers=ON')
    try:
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def encode_cursor(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()


def decode_cursor(token):
    if not isinstance(token, str) or len(token) > 8192:
        raise ValueError('Invalid sync cursor')
    try:
        data = json.loads(base64.b64decode(token, altchars=b'-_', validate=True))
        if not isinstance(data, dict) or set(data) != {'v', 'epoch', 'after', 'through', 'mode', 'collections'}:
            raise ValueError()
        if data['v'] != PROTOCOL_VERSION or data['mode'] not in ('bootstrap', 'delta'):
            raise ValueError()
        if not isinstance(data['epoch'], str):
            raise ValueError()
        for key in ('after', 'through'):
            if type(data[key]) is not int or data[key] < 0:
                raise ValueError()
        if data['after'] > data['through']:
            raise ValueError()
        data['collections'] = validate_collections(data['collections'])
        return data
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError('Invalid sync cursor') from exc


def validate_collections(names):
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n in COLLECTIONS for n in names):
        raise ValueError('Select at least one supported sync collection')
    return sorted(set(names))


def change_dict(row):
    data = json.loads(row['payload']) if row['payload'] is not None else None
    return {'revision': row['sequence'], 'collection': row['collection'],
            'id': row['record_id'], 'deleted': data is None,
            'data': mapping_to_dict(data) if data is not None else None}


def page(*, token=None, collections=None, limit=100):
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f'limit must be between 1 and {MAX_PAGE_SIZE}')
    with database() as db:
        state = db.execute('SELECT id,history_floor FROM mobile_sync_state').fetchone()
        epoch = state['id']
        high = db.execute('SELECT COALESCE(MAX(sequence),0) FROM mobile_sync_changes').fetchone()[0]
        if token:
            if collections is not None:
                raise ValueError('A cursor already fixes its collections; start a new bootstrap to change them')
            cursor = decode_cursor(token)
            if cursor['epoch'] != epoch or cursor['through'] > high:
                raise ResetRequired('Server history changed; bootstrap again and preserve local pending edits')
            if cursor['through'] < state['history_floor'] or (
                cursor['mode'] == 'delta' and cursor['after'] < state['history_floor']
            ):
                raise ResetRequired('Sync history expired; bootstrap again and preserve local pending edits')
            if cursor['after'] == cursor['through']:
                cursor['mode'] = 'delta'
                cursor['through'] = high
        else:
            names = validate_collections(collections if collections is not None else list(COLLECTIONS))
            cursor = {'v': PROTOCOL_VERSION, 'epoch': epoch, 'after': 0, 'through': high,
                      'mode': 'bootstrap', 'collections': names}
        slots = ','.join('?' for _ in cursor['collections'])
        # Only each record's latest version inside the window, in a delta as in
        # a bootstrap: a delta used to send every version a record went through
        # since the cursor, and the device wrote each one only to keep the last.
        # The window is fixed by the cursor, so paging through it stays exact.
        rows = db.execute(f'''
            SELECT * FROM mobile_sync_changes WHERE collection IN ({slots})
            AND sequence>? AND sequence<=?
            AND sequence=(SELECT MAX(c.sequence) FROM mobile_sync_changes c
                WHERE c.collection=mobile_sync_changes.collection
                AND c.record_id=mobile_sync_changes.record_id AND c.sequence<=?)
            ORDER BY sequence LIMIT ?
        ''', [*cursor['collections'], cursor['after'], cursor['through'], cursor['through'], limit + 1]).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        cursor['after'] = rows[-1]['sequence'] if more else cursor['through']
        return {'protocolVersion': PROTOCOL_VERSION, 'epoch': epoch, 'mode': cursor['mode'],
                'changes': [change_dict(r) for r in rows], 'hasMore': more,
                'cursor': encode_cursor(cursor), 'collections': cursor['collections']}


def status(tokens):
    """Whether each cursor has anything to fetch, without fetching it.

    A device checks in often and almost always finds nothing new; this answers
    every scope it holds in one request instead of an empty page per scope.
    """
    if not isinstance(tokens, list) or not tokens or len(tokens) > 16:
        raise ValueError('Send between 1 and 16 cursors')
    cursors = [decode_cursor(token) for token in tokens]
    with database() as db:
        state = db.execute('SELECT id,history_floor FROM mobile_sync_state').fetchone()
        high = db.execute('SELECT COALESCE(MAX(sequence),0) FROM mobile_sync_changes').fetchone()[0]
        out = []
        for cursor in cursors:
            if (cursor['epoch'] != state['id'] or cursor['through'] > high
                    or cursor['through'] < state['history_floor']
                    or (cursor['mode'] == 'delta' and cursor['after'] < state['history_floor'])):
                out.append({'changed': True, 'resetRequired': True})
                continue
            if cursor['after'] < cursor['through']:
                # Partway through a window: the rest of it is still to come.
                out.append({'changed': True, 'resetRequired': False})
                continue
            slots = ','.join('?' for _ in cursor['collections'])
            newer = db.execute(f'''
                SELECT 1 FROM mobile_sync_changes WHERE collection IN ({slots}) AND sequence>? LIMIT 1
            ''', [*cursor['collections'], cursor['through']]).fetchone()
            out.append({'changed': newer is not None, 'resetRequired': False})
        return out
