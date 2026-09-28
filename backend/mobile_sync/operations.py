"""Revision-checked journal edits with transactional replay acknowledgements."""
import hashlib
import json
import time

from backend.routes.journal import _client_id, _close_screenshot_session, delete_journal_entry
from backend.tags import tags_json
from .feed import change_dict, database


def apply(body):
    if not isinstance(body, dict) or set(body) != {
        'id', 'epoch', 'collection', 'recordId', 'baseRevision', 'action', 'data',
    }:
        raise ValueError('Expected id, epoch, collection, recordId, baseRevision, action and data')
    if not isinstance(body['id'], str) or not _client_id(body['id']):
        raise ValueError('Operation id must be a ULID')
    if not isinstance(body['recordId'], str) or not _client_id(body['recordId']):
        raise ValueError('Record id must be a ULID')
    if body['collection'] != 'journal_entries' or body['action'] not in ('update', 'delete'):
        raise ValueError('This operation supports journal update/delete only')
    if not isinstance(body['epoch'], str) or type(body['baseRevision']) is not int or body['baseRevision'] < 1:
        raise ValueError('An epoch and positive baseRevision are required')
    data = body['data']
    if not isinstance(data, dict) or set(data) - {'content', 'title', 'tags'}:
        raise ValueError('Only content, title and tags may be edited')
    if body['action'] == 'delete' and data:
        raise ValueError('Delete data must be empty')
    if body['action'] == 'update' and not data:
        raise ValueError('An update must contain at least one field')
    for field in ('content', 'title'):
        if field in data and not isinstance(data[field], str):
            raise ValueError(f'{field} must be text')
    if 'tags' in data and (not isinstance(data['tags'], list) or not all(isinstance(t, str) for t in data['tags'])):
        raise ValueError('tags must be a list of strings')
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with database(write=True) as db:
        previous = db.execute('SELECT * FROM mobile_sync_operations WHERE id=?', (body['id'],)).fetchone()
        if previous:
            if previous['request_hash'] != fingerprint:
                return {'error': 'Operation id already used for different content'}, 409
            return json.loads(previous['response']), previous['status']
        epoch = db.execute('SELECT id FROM mobile_sync_state').fetchone()[0]
        if epoch != body['epoch']:
            return {'error': 'Server history changed', 'resetRequired': True}, 410
        current = db.execute('SELECT * FROM mobile_sync_changes WHERE collection=? AND record_id=? ORDER BY sequence DESC LIMIT 1',
                             (body['collection'], body['recordId'])).fetchone()
        if current is None or current['payload'] is None or current['sequence'] != body['baseRevision']:
            result, status = {'error': 'Record changed; keep your edit and resolve the conflict',
                              'conflict': True, 'current': change_dict(current) if current else None}, 409
        else:
            if body['action'] == 'delete':
                delete_journal_entry(db, body['recordId'])
            else:
                updates = {**data, 'updated_at': int(time.time())}
                if 'tags' in updates:
                    updates['tags'] = tags_json(updates['tags'])
                setters = ','.join(f'"{name}"=?' for name in updates)
                db.execute(f'UPDATE journal_entries SET {setters} WHERE id=?',
                           [*updates.values(), body['recordId']])
                _close_screenshot_session(db, body['recordId'])
            revision = db.execute('SELECT * FROM mobile_sync_changes WHERE collection=? AND record_id=? ORDER BY sequence DESC LIMIT 1',
                                  (body['collection'], body['recordId'])).fetchone()
            result, status = {'operationId': body['id'], 'change': change_dict(revision)}, 200
        db.execute('INSERT INTO mobile_sync_operations(id,request_hash,response,status,created_at) VALUES (?,?,?,?,?)',
                   (body['id'], fingerprint, json.dumps(result), status, int(time.time())))
        return result, status
