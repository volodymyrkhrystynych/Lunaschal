import io
import sqlite3
from datetime import datetime

import pytest
from ulid import ULID

from backend.db.connection import get_db, _ensure_journal_context_prompts
from backend.journal import context_prompts


NOW = int(datetime(2026, 10, 14, 12).timestamp())
YESTERDAY = int(datetime(2026, 10, 13, 12).timestamp())


def entry(at=YESTERDAY, *, kinds=('image',), content='', raw=None, transcript=None, status='idle'):
    db = get_db()
    id = str(ULID())
    db.execute('INSERT INTO journal_entries (id, content, raw_content, created_at, updated_at) VALUES (?,?,?,?,?)',
               (id, content, raw, at, at))
    for kind in kinds:
        db.execute('INSERT INTO journal_attachments (id, entry_id, kind, name, path, transcript, transcript_status, description, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                   (str(ULID()), id, kind, 'Game screenshot' if kind == 'image' else 'Recording',
                    '/fake/file', transcript, status, 'AI description', at))
    db.commit()
    return id


def selected(now=NOW, *, today=False):
    return [r['id'] for r in context_prompts.prepare(get_db(), now=now, include_today=today)]


# Long enough ago that one of today's entries has settled.
SETTLED = NOW - context_prompts.TODAY_SETTLE_SECONDS - 60


def test_all_yesterday_entries_even_above_three_and_no_today_or_older():
    yesterday = [entry(YESTERDAY + i) for i in range(5)]
    entry(SETTLED)
    entry(YESTERDAY - 86400)
    assert selected() == yesterday[::-1]


def test_today_comes_first_uncapped_and_takes_no_backlog_slot():
    today = [entry(SETTLED - i * 60) for i in range(5)]
    old = [entry(YESTERDAY - i * 86400) for i in range(1, 5)]
    assert selected(today=True) == [*today, *old[:3]]
    # Asking again later in the day picks up a new capture and keeps the slots.
    later = entry(NOW + 60)
    assert selected(NOW + 600, today=True) == [later, *today, *old[:3]]


def test_without_the_flag_today_is_left_to_the_backlog():
    # The web's Chat cards: the same list as before today's existed.
    entry(SETTLED)
    old = [entry(YESTERDAY - i * 86400) for i in range(1, 5)]
    assert selected() == old[:3]


def test_today_answered_or_dismissed_drops_out(client, queued_jobs):
    answered, dismissed, waiting = entry(SETTLED - 3), entry(SETTLED - 2), entry(SETTLED - 1)
    assert client.post(f'/api/journal/{answered}/context-prompt', json={'action': 'save', 'content': 'Lunch'}).status_code == 200
    assert client.post(f'/api/journal/{dismissed}/context-prompt', json={'action': 'dismiss'}).status_code == 200
    assert selected(today=True) == [waiting]


def test_unanswered_today_rolls_into_tomorrows_backlog():
    old = [entry(YESTERDAY - i * 86400) for i in range(1, 4)]
    today = [entry(SETTLED - i) for i in range(4)]
    assert selected(today=True) == [*today, *old]
    # Tomorrow they are yesterday's, which joins uncapped and fills the slots.
    assert selected(NOW + 86400) == today


def test_today_waits_for_a_running_transcription():
    entry(SETTLED, kinds=('audio',), status='running')
    spoken = entry(SETTLED, kinds=('audio',), transcript='Talking about lunch', status='done')
    silent = entry(SETTLED + 1, kinds=('audio',), transcript='', status='done')
    assert selected(today=True) == [silent]
    assert spoken not in selected(today=True)


def test_today_waits_for_an_entry_to_settle():
    # A recording is committed at 'idle' before its transcription is marked
    # running: for that moment it looks wordless.
    fresh = entry(NOW - 30, kinds=('audio',), status='idle')
    assert selected(today=True) == []
    assert selected(NOW - 30 + context_prompts.TODAY_SETTLE_SECONDS, today=True) == [fresh]
    # A photo added a moment ago to an older entry restarts the wait.
    older = entry(SETTLED)
    db = get_db()
    db.execute('INSERT INTO journal_attachments (id, entry_id, kind, name, path, created_at) VALUES (?,?,?,?,?,?)',
               (str(ULID()), older, 'image', 'Second', '/fake/file', NOW - 10))
    db.commit()
    assert older not in selected(today=True)
    assert older in selected(NOW + context_prompts.TODAY_SETTLE_SECONDS, today=True)


def test_today_waits_for_an_open_screenshot_session():
    id = entry(SETTLED)
    db = get_db()
    db.execute('INSERT INTO journal_screenshot_sessions (entry_id, is_open, first_captured_at, last_captured_at,'
               ' first_local_date, first_local_time, last_local_date, last_local_time, created_at, updated_at)'
               " VALUES (?,1,?,?,'2026-10-14','11:57','2026-10-14','11:57',?,?)",
               (id, SETTLED, SETTLED, SETTLED, SETTLED))
    db.commit()
    assert selected(today=True) == []
    db.execute('UPDATE journal_screenshot_sessions SET is_open=0 WHERE entry_id=?', (id,))
    db.commit()
    assert selected(today=True) == [id]


@pytest.mark.parametrize('job, listed', [
    (None, False),
    (('journal.autofile', 'pending'), False),
    (('journal.autofile', 'running'), False),
    (('journal.autofile', 'done'), True),
    (('journal.autofile', 'error'), True),
    # The waiter ran out of time: it stays in the journal, untouched.
    (('journal.metadata', 'pending'), True),
])
def test_today_waits_for_a_capture_tab_save_to_be_sorted(job, listed):
    id = entry(SETTLED)
    db = get_db()
    db.execute('UPDATE journal_entries SET auto_file=1 WHERE id=?', (id,))
    if job:
        db.execute('INSERT INTO llm_jobs (id, kind, target_id, status, created_at) VALUES (?,?,?,?,?)',
                   (str(ULID()), job[0], id, job[1], SETTLED))
    db.commit()
    assert (id in selected(today=True)) is listed


def test_top_up_newest_first_and_do_not_refill_completed_slots(client, queued_jobs):
    old = [entry(YESTERDAY - i * 86400) for i in range(1, 5)]
    recent = entry()
    assert selected() == [recent, *old[:2]]
    assert client.post(f'/api/journal/{recent}/context-prompt', json={'action': 'save', 'content': 'Boss fight'}).status_code == 200
    assert client.post(f'/api/journal/{old[0]}/context-prompt', json={'action': 'dismiss'}).status_code == 200
    assert selected() == [old[1]]
    assert selected(NOW + 86400) == old[1:]
    # Reopening the connection (as after a restart) preserves dismissal.
    from backend.db import connection
    connection._conn.close()
    connection._conn = None
    assert selected(NOW + 86400) == old[1:]


def test_empty_yesterday_uses_three_old_entries_and_small_backlog_is_ok():
    old = entry(YESTERDAY - 86400)
    assert selected() == [old]
    assert selected() == [old]


def test_eligibility_ignores_image_captions_and_descriptions_but_not_speech():
    photo = entry(transcript='AI image caption', status='done')
    audio = entry(kinds=('audio',), content=' \n\t', raw='\r\n', transcript=' \n')
    mixed = entry(kinds=('image', 'audio'))
    entry(content='Typed context')
    entry(raw='Original words')
    entry(kinds=('audio',), transcript='Spoken context')
    entry(kinds=('audio',), status='running')
    entry(kinds=('video',))
    entry(kinds=('image', 'file'))
    entry(kinds=())
    assert set(selected()) == {photo, audio, mixed}


def test_four_am_boundary_and_late_arrival():
    before = entry(int(datetime(2026, 10, 14, 3, 59).timestamp()))
    after = entry(int(datetime(2026, 10, 14, 4).timestamp()))
    older = [entry(YESTERDAY - i * 86400) for i in range(1, 4)]
    assert selected() == [before, *older[:2]]
    # 04:00 on the 14th is today's: listed first when asked for, outside the slots.
    assert selected(today=True) == [after, before, *older[:2]]
    late = entry(YESTERDAY)
    assert selected() == [before, late, older[0]]
    assert after not in selected()
    # At 03:59 on the 14th, yesterday is still the 12th.
    assert before not in selected(int(datetime(2026, 10, 14, 3, 59).timestamp()))


def test_prepare_route_returns_media_urls_and_keeps_date(client, monkeypatch):
    monkeypatch.setattr(context_prompts, 'day_key_for', lambda _: '2026-10-14')
    id = entry()
    result = client.post('/api/journal/context-prompts/prepare').get_json()
    assert [r['id'] for r in result] == [id]
    # Today's are the phone's to ask for; an empty body is the web's request.
    assert [r['id'] for r in client.post('/api/journal/context-prompts/prepare', json={}).get_json()] == [id]
    attachment = result[0]['attachments'][0]
    assert attachment['url'].startswith('/api/journal/attachments/')
    assert 'path' not in attachment


def test_prepare_route_lists_today_only_when_the_phone_asks(client, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(context_prompts, 'time', SimpleNamespace(time=lambda: NOW))
    today, backlog = entry(SETTLED), entry()
    route = '/api/journal/context-prompts/prepare'
    assert [r['id'] for r in client.post(route, json={}).get_json()] == [backlog]
    assert [r['id'] for r in client.post(route, json={'includeToday': 'yes'}).get_json()] == [backlog]
    assert [r['id'] for r in client.post(route, json={'includeToday': True}).get_json()] == [today, backlog]


def test_save_is_idempotent_and_preserves_original_entry(client, queued_jobs):
    id = entry()
    selected()
    payload = {'action': 'save', 'content': '  Playing with friends.  '}
    for _ in range(2):
        assert client.post(f'/api/journal/{id}/context-prompt', json=payload).status_code == 200
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    assert row['content'] == row['raw_content'] == 'Playing with friends.'
    assert row['created_at'] == YESTERDAY
    assert row['context_prompt_status'] == 'answered'
    assert selected() == []
    assert len(client.get(f'/api/journal/{id}').get_json()['attachments']) == 1


def test_save_does_not_overwrite_context_added_elsewhere(client, queued_jobs):
    id = entry(content='Added on another device')
    response = client.post(f'/api/journal/{id}/context-prompt', json={'action': 'save', 'content': 'Stale edit'})
    assert response.status_code == 409
    assert client.get(f'/api/journal/{id}').get_json()['content'] == 'Added on another device'


def test_second_device_keeps_its_draft_when_prompt_was_already_answered(client, queued_jobs):
    id = entry()
    route = f'/api/journal/{id}/context-prompt'
    assert client.post(route, json={'action': 'save', 'content': 'From the desktop'}).status_code == 200
    assert client.post(route, json={'action': 'save', 'content': 'From the phone'}).status_code == 409
    assert client.get(f'/api/journal/{id}').get_json()['content'] == 'From the desktop'


@pytest.mark.parametrize('payload', [{'action': 'bad'}, {'action': 'save'}, {'action': 'save', 'content': 123}])
def test_invalid_resolution(client, payload):
    assert client.post(f'/api/journal/{entry()}/context-prompt', json=payload).status_code == 400


def test_recorded_context_attaches_and_transcribes_into_original_entry(client, monkeypatch, tmp_path, queued_jobs):
    from backend.routes import journal
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    monkeypatch.setattr(journal, '_do_attachment_audio', lambda _: 'That was our final match.')
    monkeypatch.setattr(journal, '_polish_bg', lambda *a, **k: None)
    monkeypatch.setattr(journal, '_generate_metadata_bg', lambda *a, **k: None)
    id = entry()
    selected()
    response = client.post('/api/journal/recordings', data={
        'id': id, 'attachmentId': str(ULID()), 'transcribe': 'true',
        'file': (io.BytesIO(b'\x00' * 2048), 'context.webm', 'audio/webm'),
    })
    assert response.status_code == 201
    assert selected() == []  # pending transcription is not another prompt
    attachment = response.get_json()['attachment']
    path = get_db().execute('SELECT path FROM journal_attachments WHERE id=?', (attachment['id'],)).fetchone()[0]
    journal._transcribe_attachment_bg(attachment['id'], id, 'audio', path, 'Context', into_entry=True, now=True)
    row = get_db().execute('SELECT * FROM journal_entries WHERE id=?', (id,)).fetchone()
    assert row['content'] == 'That was our final match.'
    assert row['created_at'] == YESTERDAY
    assert len(client.get(f'/api/journal/{id}').get_json()['attachments']) == 2
    assert selected() == []


def test_migration_is_idempotent_and_existing_rows_start_pending():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE journal_entries (id TEXT PRIMARY KEY)')
    db.execute("INSERT INTO journal_entries VALUES ('existing')")
    _ensure_journal_context_prompts(db)
    _ensure_journal_context_prompts(db)
    assert db.execute('SELECT context_prompt_day, context_prompt_status FROM journal_entries').fetchone() == (None, 'pending')
    db.close()
