import base64
import json

import pytest
from ulid import ULID

from backend.db.connection import get_db, init_db
from backend.mobile_sync.feed import decode_cursor, encode_cursor
from backend.routes import journal


@pytest.fixture(autouse=True)
def no_enrichment(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal, name, lambda *a, **k: None)


def entry(client, text='original'):
    return client.post('/api/journal', json={'content': text}).json['id']


def start(client, **args):
    return client.get('/api/mobile/sync', query_string={'collections': 'journal_entries', **args})


def test_all_writers_and_deletes_are_captured_atomically(client):
    id = entry(client)
    initial = start(client).json
    assert initial['changes'][0]['id'] == id
    assert initial['changes'][0]['data']['content'] == 'original'
    db = get_db()
    db.execute('UPDATE journal_entries SET content=? WHERE id=?', ('AI enrichment', id))
    db.commit()
    delta = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor']}).json
    assert delta['mode'] == 'delta'
    assert delta['changes'][0]['data']['content'] == 'AI enrichment'
    assert delta['changes'][0]['revision'] > initial['changes'][0]['revision']
    client.delete(f'/api/journal/{id}')
    deleted = client.get('/api/mobile/sync', query_string={'cursor': delta['cursor']}).json
    assert deleted['changes'][0]['deleted'] is True
    assert deleted['changes'][0]['data'] is None


def test_bootstrap_pagination_does_not_miss_a_row_edited_between_pages(client):
    first, second = entry(client, 'first'), entry(client, 'second')
    initial = start(client, limit=1).json
    assert initial['hasMore'] is True
    client.patch(f'/api/journal/{second}', json={'content': 'new second'})
    client.delete(f'/api/journal/{first}')
    page = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor'], 'limit': 1}).json
    assert page['changes'][0]['id'] == second
    assert page['changes'][0]['data']['content'] == 'second'
    assert page['hasMore'] is False
    delta = client.get('/api/mobile/sync', query_string={'cursor': page['cursor']}).json
    assert [(r['id'], r['deleted']) for r in delta['changes']] == [(second, False), (first, True)]
    assert delta['changes'][0]['data']['content'] == 'new second'


def test_rollback_never_publishes_a_change(client):
    id = entry(client)
    initial = start(client).json
    db = get_db()
    db.execute('UPDATE journal_entries SET content=? WHERE id=?', ('uncommitted', id))
    # Independent WAL reader cannot see an uncommitted trigger result.
    pending = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor']}).json
    assert pending['changes'] == []
    db.rollback()
    result = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor']}).json
    assert result['changes'] == []


def test_bootstrap_returns_latest_version_and_tombstones(client):
    id, gone = entry(client), entry(client)
    client.patch(f'/api/journal/{id}', json={'title': 'renamed'})
    client.delete(f'/api/journal/{gone}')
    items = start(client).json['changes']
    assert len(items) == 2
    assert items[0]['data']['title'] == 'renamed'
    assert items[1]['deleted'] is True


def test_restart_keeps_epoch_and_does_not_duplicate_baseline(client):
    entry(client)
    before = start(client).json
    init_db()
    after = start(client).json
    assert before == after


def test_first_install_backfills_existing_rows(client):
    id = entry(client)
    db = get_db()
    db.execute('DELETE FROM mobile_sync_changes')
    db.commit()
    init_db()
    assert start(client).json['changes'][0]['id'] == id


def test_projection_migration_rotates_epoch_and_rebuilds_without_stale_triggers(client):
    id = entry(client)
    initial = start(client).json
    db = get_db()
    db.execute("UPDATE mobile_sync_state SET schema_hash='older-projection'")
    db.commit()
    init_db()
    rebuilt = start(client).json
    assert rebuilt['epoch'] != initial['epoch']
    assert rebuilt['changes'][0]['id'] == id
    client.patch(f'/api/journal/{id}', json={'title': 'after migration'})
    assert start(client).json['changes'][0]['data']['title'] == 'after migration'


def test_epoch_or_cursor_ahead_of_restored_server_requires_rebootstrap(client):
    entry(client)
    initial = start(client).json
    for updates in ({'epoch': str(ULID())}, {'through': 999999}):
        cursor = {**decode_cursor(initial['cursor']), **updates}
        response = client.get('/api/mobile/sync', query_string={'cursor': encode_cursor(cursor)})
        assert response.status_code == 410
        assert response.json['resetRequired'] is True


def test_secrets_and_local_paths_are_not_replica_collections(client):
    response = client.get('/api/mobile/capabilities').json
    assert 'settings' not in response['collections']
    assert 'llm_jobs' not in response['collections']
    from backend.mobile_sync.registry import COLLECTIONS
    assert not any('path' in name or 'token' in name or 'password' in name
                   for columns in COLLECTIONS.values() for name in columns)
    assert start(client, collections='settings').status_code == 400


@pytest.mark.parametrize('token', ['not base64', base64.b64encode(b'[]').decode(), 'x' * 8193])
def test_malformed_cursor_is_a_400(client, token):
    assert client.get('/api/mobile/sync', query_string={'cursor': token}).status_code == 400


@pytest.mark.parametrize('limit', ['0', '-1', '201', '1.5', 'nonsense'])
def test_page_limits_are_validated(client, limit):
    assert start(client, limit=limit).status_code == 400


def test_cursor_cannot_silently_change_collection_scope(client):
    initial = start(client).json
    assert client.get('/api/mobile/sync', query_string={
        'cursor': initial['cursor'], 'collections': 'fics',
    }).status_code == 400


def test_streaming_chat_does_not_log_each_token(client):
    db = get_db()
    conversation, message = str(ULID()), str(ULID())
    db.execute('INSERT INTO conversations(id,created_at,updated_at) VALUES (?,1,1)', (conversation,))
    db.execute("INSERT INTO messages(id,conversation_id,role,content,status,created_at) VALUES (?,?,'assistant','','streaming',1)", (message, conversation))
    for token in ['a', 'ab', 'abc']:
        db.execute('UPDATE messages SET content=? WHERE id=?', (token, message))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM mobile_sync_changes WHERE collection='messages'").fetchone()[0] == 0
    db.execute("UPDATE messages SET status='done' WHERE id=?", (message,))
    db.commit()
    changes = client.get('/api/mobile/sync', query_string={'collections': 'messages'}).json['changes']
    assert len(changes) == 1
    assert changes[0]['data']['content'] == 'abc'


def operation_body(client, id, action='update', data=None):
    page = start(client).json
    revision = next(row['revision'] for row in page['changes'] if row['id'] == id)
    return {'id': str(ULID()), 'epoch': page['epoch'], 'collection': 'journal_entries',
            'recordId': id, 'baseRevision': revision, 'action': action,
            'data': data if data is not None else {'content': 'edited offline'}}


def test_operation_replay_has_one_write_and_same_acknowledgement(client):
    id = entry(client)
    body = operation_body(client, id)
    first = client.post('/api/mobile/operations', json=body)
    assert first.status_code == 200
    replay = client.post('/api/mobile/operations', json=body)
    assert replay.json == first.json
    assert get_db().execute('SELECT COUNT(*) FROM mobile_sync_operations').fetchone()[0] == 1
    assert len(start(client).json['changes']) == 1
    assert get_db().execute('SELECT COUNT(*) FROM mobile_sync_changes WHERE record_id=?', (id,)).fetchone()[0] == 2
    altered = client.post('/api/mobile/operations', json={**body, 'data': {'content': 'different'}})
    assert altered.status_code == 409
    assert client.get(f'/api/journal/{id}').json['content'] == 'edited offline'


def test_concurrent_edit_conflict_preserves_server_and_local_input(client):
    id = entry(client)
    local = operation_body(client, id)
    client.patch(f'/api/journal/{id}', json={'content': 'other device'})
    response = client.post('/api/mobile/operations', json=local)
    assert response.status_code == 409
    assert response.json['conflict'] is True
    assert response.json['current']['data']['content'] == 'other device'
    assert client.get(f'/api/journal/{id}').json['content'] == 'other device'
    assert client.post('/api/mobile/operations', json=local).json == response.json
    # Choosing to apply the edit requires a new operation and current revision.
    resolved = operation_body(client, id, data=local['data'])
    assert client.post('/api/mobile/operations', json=resolved).status_code == 200


def test_edit_after_delete_never_resurrects_record(client):
    id = entry(client)
    local = operation_body(client, id)
    client.delete(f'/api/journal/{id}')
    response = client.post('/api/mobile/operations', json=local)
    assert response.status_code == 409
    assert response.json['current']['deleted'] is True
    assert client.get(f'/api/journal/{id}').status_code == 404


def test_operation_delete_is_idempotent(client):
    id = entry(client)
    body = operation_body(client, id, action='delete', data={})
    response = client.post('/api/mobile/operations', json=body)
    assert response.status_code == 200
    assert response.json['change']['deleted'] is True
    assert client.post('/api/mobile/operations', json=body).json == response.json
    assert client.get(f'/api/journal/{id}').status_code == 404


def test_operation_rollback_does_not_leave_domain_write(client):
    id = entry(client)
    body = operation_body(client, id)
    db = get_db()
    db.execute("CREATE TRIGGER refuse_mobile_ack BEFORE INSERT ON mobile_sync_operations BEGIN SELECT RAISE(ABORT, 'disk error simulation'); END")
    db.commit()
    import sqlite3
    from backend.mobile_sync.operations import apply
    with pytest.raises(sqlite3.IntegrityError):
        apply(body)
    assert client.get(f'/api/journal/{id}').json['content'] == 'original'
    assert db.execute('SELECT COUNT(*) FROM mobile_sync_changes WHERE record_id=?', (id,)).fetchone()[0] == 1


@pytest.mark.parametrize('updates', [
    {'data': {'raw_content': 'clobber original'}}, {'data': {'content': None}},
    {'data': {'tags': 'tag'}}, {'baseRevision': True}, {'baseRevision': 0},
    {'collection': 'settings'}, {'id': '../secret'}, {'id': []}, {'data': {}},
])
def test_operation_rejects_invalid_or_unwritable_fields(client, updates):
    id = entry(client)
    body = {**operation_body(client, id), **updates}
    assert client.post('/api/mobile/operations', json=body).status_code == 400
    assert client.get(f'/api/journal/{id}').json['content'] == 'original'


def test_replace_and_cascade_deletes_are_visible(client):
    db = get_db()
    paper, page = str(ULID()), str(ULID())
    db.execute('INSERT INTO papers(id,created_at,updated_at) VALUES (?,1,1)', (paper,))
    db.execute('INSERT INTO paper_pages(id,paper_id,created_at,updated_at) VALUES (?,?,1,1)', (page, paper))
    db.commit()
    initial = client.get('/api/mobile/sync', query_string={'collections': 'papers,paper_pages'}).json
    db.execute('INSERT OR REPLACE INTO papers(id,title,created_at,updated_at) VALUES (?,\'replacement\',2,2)', (paper,))
    db.commit()
    delta = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor']}).json['changes']
    # The replace's own delete is superseded by the row it re-inserted (a delta
    # sends each record's latest version); the cascade's delete is not.
    assert {row['id']: row['deleted'] for row in delta} == {paper: False, page: True}
    assert next(r for r in delta if r['id'] == paper)['data']['title'] == 'replacement'


def test_compaction_retains_bootstrap_baseline_and_rejects_expired_cursors(client):
    from backend.mobile_sync.maintenance import compact
    id, deleted = entry(client), entry(client)
    original = start(client).json
    client.patch(f'/api/journal/{id}', json={'content': 'latest'})
    client.delete(f'/api/journal/{deleted}')
    db = get_db()
    db.execute('UPDATE mobile_sync_changes SET created_at=1')
    db.commit()
    assert compact(keep_days=1, now=200000) == 2
    stale = client.get('/api/mobile/sync', query_string={'cursor': original['cursor']})
    assert stale.status_code == 410
    baseline = start(client).json
    assert len(baseline['changes']) == 2
    assert baseline['changes'][0]['data']['content'] == 'latest'
    assert baseline['changes'][1]['deleted'] is True
    assert client.get('/api/mobile/sync', query_string={'cursor': baseline['cursor']}).json['changes'] == []


def test_compacted_bootstrap_can_page_through_old_baseline_rows(client):
    from backend.mobile_sync.maintenance import compact
    first, second = entry(client), entry(client)
    client.patch(f'/api/journal/{second}', json={'title': 'new'})
    db = get_db()
    db.execute('UPDATE mobile_sync_changes SET created_at=1')
    db.commit()
    compact(keep_days=1, now=200000)
    initial = start(client, limit=1).json
    assert initial['changes'][0]['id'] == first
    following = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor'], 'limit': 1})
    assert following.status_code == 200
    assert following.json['changes'][0]['id'] == second


def test_explicit_restore_rotation_invalidates_even_an_in_range_cursor(client):
    from backend.mobile_sync.maintenance import rotate_epoch
    entry(client)
    original = start(client).json
    new_epoch = rotate_epoch()
    assert new_epoch != original['epoch']
    assert client.get('/api/mobile/sync', query_string={'cursor': original['cursor']}).status_code == 410
    assert len(start(client).json['changes']) == 1


def test_calendar_series_and_exceptions_replicate_read_only(client):
    created = client.post('/api/calendar', json={
        'title': 'Gym', 'date': '2026-10-05', 'time': '07:00', 'endTime': '08:00',
        'tags': ['health'], 'repeatFreq': 'weekly', 'repeatByweekday': [1, 3],
    })
    assert created.status_code in (200, 201), created.json
    id = created.json['id']
    assert client.delete(f'/api/calendar/{id}/occurrence/2026-10-07').status_code in (200, 204)
    page = client.get('/api/mobile/sync',
                      query_string={'collections': 'calendar_events,calendar_event_exceptions'}).json
    by_collection = {row['collection']: row['data'] for row in page['changes']}
    event = by_collection['calendar_events']
    assert event['id'] == id and event['repeatFreq'] == 'weekly' and event['repeatByweekday'] == '1,3'
    assert event['endTime'] == '08:00' and json.loads(event['tags']) == ['health']
    assert 'journalId' not in event and 'classificationError' not in event
    skip = by_collection['calendar_event_exceptions']
    assert skip['eventId'] == id and skip['date'] == '2026-10-07' and skip['action'] == 'skip'
    edit = {'id': str(ULID()), 'epoch': page['epoch'], 'collection': 'calendar_events', 'recordId': id,
            'baseRevision': page['changes'][0]['revision'], 'action': 'update', 'data': {'title': 'x'}}
    assert client.post('/api/mobile/operations', json=edit).status_code == 400


def test_a_delta_sends_only_each_records_latest_version(client):
    id = entry(client)
    initial = start(client).json
    for text in ('second', 'third', 'fourth'):
        client.patch(f'/api/journal/{id}', json={'content': text})
    delta = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor']}).json
    assert [(r['id'], r['data']['content']) for r in delta['changes']] == [(id, 'fourth')]


def test_a_paged_delta_still_reaches_every_record(client):
    ids = [entry(client, f'e{n}') for n in range(3)]
    initial = start(client).json
    for id in ids:
        client.patch(f'/api/journal/{id}', json={'content': 'edited ' + id})
    client.patch(f'/api/journal/{ids[0]}', json={'content': 'edited again'})
    seen, cursor = {}, initial['cursor']
    while True:
        page = client.get('/api/mobile/sync', query_string={'cursor': cursor, 'limit': 1}).json
        seen.update({r['id']: r['data']['content'] for r in page['changes']})
        cursor = page['cursor']
        if not page['hasMore']:
            break
    assert seen == {ids[0]: 'edited again', ids[1]: 'edited ' + ids[1], ids[2]: 'edited ' + ids[2]}


def status(client, *cursors):
    return client.post('/api/mobile/sync/status', json={'cursors': list(cursors)})


def test_status_says_whether_a_scope_has_anything_new_without_sending_it(client):
    id = entry(client)
    journal_scope = start(client).json['cursor']
    fics_scope = client.get('/api/mobile/sync', query_string={'collections': 'fics'}).json['cursor']
    assert status(client, journal_scope, fics_scope).json['cursors'] == [
        {'changed': False, 'resetRequired': False}, {'changed': False, 'resetRequired': False}]
    client.patch(f'/api/journal/{id}', json={'content': 'edited'})
    assert status(client, journal_scope, fics_scope).json['cursors'] == [
        {'changed': True, 'resetRequired': False}, {'changed': False, 'resetRequired': False}]
    caught_up = client.get('/api/mobile/sync', query_string={'cursor': journal_scope}).json['cursor']
    assert status(client, caught_up).json['cursors'] == [{'changed': False, 'resetRequired': False}]


def test_status_reports_a_scope_partway_through_a_window(client):
    entry(client), entry(client)
    partway = start(client, limit=1).json
    assert partway['hasMore'] is True
    assert status(client, partway['cursor']).json['cursors'] == [{'changed': True, 'resetRequired': False}]


def test_status_reports_an_expired_or_foreign_cursor_as_needing_a_bootstrap(client):
    from backend.mobile_sync.maintenance import compact, rotate_epoch
    id = entry(client)
    original = start(client).json['cursor']
    client.patch(f'/api/journal/{id}', json={'content': 'latest'})
    db = get_db()
    db.execute('UPDATE mobile_sync_changes SET created_at=1')
    db.commit()
    compact(keep_days=1, now=200000)
    assert status(client, original).json['cursors'] == [{'changed': True, 'resetRequired': True}]
    fresh = start(client).json['cursor']
    rotate_epoch()
    assert status(client, fresh).json['cursors'] == [{'changed': True, 'resetRequired': True}]


@pytest.mark.parametrize('body', [None, {}, {'cursors': []}, {'cursors': ['nope']}, {'cursors': ['x'] * 17}])
def test_status_rejects_malformed_requests(client, body):
    assert client.post('/api/mobile/sync/status', json=body).status_code == 400


def test_capabilities_advertise_the_status_check(client):
    assert client.get('/api/mobile/capabilities').json['syncStatus'] is True


def test_the_morning_job_compacts_after_the_briefing_once_a_day(client, monkeypatch):
    from datetime import datetime
    from backend import briefing_scheduler
    order = []
    monkeypatch.setattr(briefing_scheduler, '_briefing_settings', lambda: (True, 4))
    monkeypatch.setattr(briefing_scheduler, 'run_nightly', lambda: order.append('briefing'))
    monkeypatch.setattr('backend.mobile_sync.maintenance.compact',
                        lambda keep_days: order.append(('compact', keep_days)) or 0)
    assert briefing_scheduler._tick(datetime(2026, 10, 8, 3, 59), None) is None
    ran = briefing_scheduler._tick(datetime(2026, 10, 8, 4, 0), None)
    assert order == ['briefing', ('compact', 90)]
    assert briefing_scheduler._tick(datetime(2026, 10, 8, 4, 30), ran) == ran
    assert order == ['briefing', ('compact', 90)], 'once a day'


def test_compaction_runs_even_when_the_briefing_is_off_or_fails(client, monkeypatch):
    from datetime import datetime
    from backend import briefing_scheduler
    compacted = []
    monkeypatch.setattr('backend.mobile_sync.maintenance.compact',
                        lambda keep_days: compacted.append(keep_days) or 0)
    monkeypatch.setattr(briefing_scheduler, '_briefing_settings', lambda: (False, 4))
    briefing_scheduler._tick(datetime(2026, 10, 8, 4, 0), None)

    def explode():
        raise RuntimeError('briefing failed')
    monkeypatch.setattr(briefing_scheduler, '_briefing_settings', lambda: (True, 4))
    monkeypatch.setattr(briefing_scheduler, 'run_nightly', explode)
    briefing_scheduler._tick(datetime(2026, 10, 9, 4, 0), None)
    assert compacted == [90, 90]
