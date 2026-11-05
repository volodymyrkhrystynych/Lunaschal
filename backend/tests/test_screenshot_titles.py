"""Chat names the screenshot runs the global shortcut files as "Screenshots":
`find_untitled_screenshots` lists them, `propose_screenshot_titles` stages a
card, and accepting it renames the entry and the event (backend/journal/
screenshot_titles.py)."""
import io
import json
import time

import pytest
from PIL import Image

from backend.db.connection import get_db
from backend.delegate import tools as proposal_tools
from backend.journal import screenshot_titles
from backend.lifewiki.tools import LifeTools


@pytest.fixture(autouse=True)
def _media(monkeypatch, tmp_path):
    from backend.routes import journal
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'media'))
    monkeypatch.setattr(journal, '_transcribe_attachment_bg', lambda *a, **kw: None)


def _png():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8)).save(buf, 'PNG')
    return buf.getvalue()


def _upload(client, attachment_id, captured_at):
    resp = client.post('/api/journal/screenshots', data={
        'attachmentId': attachment_id,
        'capturedAt': captured_at,
        'file': (io.BytesIO(_png()), 'screenshot.png', 'image/png'),
    })
    assert resp.status_code == 201
    return resp.get_json()['id']


def _run(client, prefix='A', day='2026-09-12', captions=('Flight search results for Lviv',)):
    """A two-shot run (so it has an event), captioned, closed by a normal
    journal insert the way the next unrelated entry would close it."""
    entry_id = _upload(client, f'01J0000000000000000000{prefix}001', f'{day}T18:04:17-04:00')
    _upload(client, f'01J0000000000000000000{prefix}002', f'{day}T18:41:00-04:00')
    db = get_db()
    ids = [r['id'] for r in db.execute(
        'SELECT id FROM journal_attachments WHERE entry_id=? ORDER BY created_at', (entry_id,))]
    for att_id, caption in zip(ids, captions):
        db.execute("UPDATE journal_attachments SET transcript=?, transcript_status='done'"
                   ' WHERE id=?', (caption, att_id))
    db.execute('UPDATE journal_screenshot_sessions SET is_open=0')
    db.commit()
    return entry_id


def _event(entry_id):
    return get_db().execute(
        'SELECT e.* FROM calendar_events e JOIN journal_screenshot_sessions s'
        ' ON s.calendar_event_id = e.id WHERE s.entry_id=?', (entry_id,)).fetchone()


def _title(entry_id):
    return get_db().execute('SELECT title FROM journal_entries WHERE id=?',
                            (entry_id,)).fetchone()['title']


def _now_for(day):
    return time.mktime(time.strptime(f'{day} 23:00', '%Y-%m-%d %H:%M'))


# --- finding them ---------------------------------------------------------

def test_lists_unnamed_runs_with_span_and_captions(client):
    entry_id = _run(client, captions=('Flight search results for Lviv', 'Seat map'))
    runs = screenshot_titles.untitled_runs(get_db(), 7, now=_now_for('2026-09-13'))
    assert runs == [{
        'entryId': entry_id, 'date': '2026-09-12', 'time': '18:04',
        'endDate': '2026-09-12', 'endTime': '18:41', 'shots': 2,
        'captions': ['Flight search results for Lviv', 'Seat map'],
        'note': '', 'hasEvent': True,
    }]


def test_a_run_named_by_hand_is_not_listed(client):
    named = _run(client, 'A')
    client.patch(f'/api/journal/{named}', json={'title': 'Trip planning'})
    get_db().execute("UPDATE calendar_events SET title='Trip planning'")
    get_db().commit()
    assert screenshot_titles.untitled_runs(get_db(), 7, now=_now_for('2026-09-13')) == []


def test_old_runs_are_outside_the_window(client):
    _run(client, day='2026-08-01')
    assert screenshot_titles.untitled_runs(get_db(), 7, now=_now_for('2026-09-13')) == []
    assert len(screenshot_titles.untitled_runs(get_db(), 60, now=_now_for('2026-09-13'))) == 1


def test_find_tool_prints_the_ids_the_proposal_needs(client, monkeypatch):
    entry_id = _run(client)
    monkeypatch.setattr(time, 'time', lambda: _now_for('2026-09-13'))
    text, event = LifeTools().run_tool('find_untitled_screenshots', {'days': 3})
    assert f'entryId {entry_id}' in text
    assert '2026-09-12 18:04–18:41' in text
    assert 'Flight search results for Lviv' in text
    assert event == {'tool': 'find_untitled_screenshots', 'arg': '3 days', 'ok': True, 'count': 1}


def test_find_tool_says_so_when_there_is_nothing(client):
    text, event = LifeTools().run_tool('find_untitled_screenshots', {})
    assert 'No screenshot runs' in text
    assert event['count'] == 0 and event['ok']


# --- proposing names ------------------------------------------------------

def test_proposal_stages_one_card_without_writing(client):
    entry_id = _run(client)
    text, event = proposal_tools.run_tool('propose_screenshot_titles', {
        'items': [{'entryId': entry_id, 'title': '  Comparing   flights '}],
    })
    assert 'Nothing has been saved yet' in text
    assert event['proposal'] == {'kind': 'screenshot_titles', 'data': {'items': [{
        'entryId': entry_id, 'title': 'Comparing flights', 'date': '2026-09-12',
        'time': '18:04', 'endTime': '18:41', 'shots': 2,
    }]}}
    assert _title(entry_id) == 'Screenshots'


def test_proposal_refuses_ids_that_are_not_unnamed_runs(client):
    entry_id = _run(client)
    text, event = proposal_tools.run_tool('propose_screenshot_titles', {
        'items': [{'entryId': 'nope', 'title': 'Something'},
                  {'entryId': entry_id, 'title': 'Screenshots'}],
    })
    assert not event['ok'] and 'proposal' not in event
    assert 'not an unnamed screenshot run' in text
    assert 'other than "Screenshots"' in text


def test_proposal_keeps_the_good_items_and_reports_the_rest(client):
    entry_id = _run(client)
    text, event = proposal_tools.run_tool('propose_screenshot_titles', {
        'items': [{'entryId': entry_id, 'title': 'Flights'},
                  {'entryId': 'nope', 'title': 'Other'}],
    })
    assert event['ok'] and len(event['proposal']['data']['items']) == 1
    assert 'Skipped: nope' in text


# --- accepting the card ---------------------------------------------------

def _card(client, items):
    db = get_db()
    now = int(time.time())
    db.execute("INSERT INTO conversations(id, title, created_at, updated_at)"
               " VALUES ('c1', NULL, ?, ?)", (now, now))
    db.execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata, status, created_at)"
        " VALUES ('m1','c1','assistant','done',?,'done',?)",
        (json.dumps({'proposals': [{'id': 'p1', 'kind': 'screenshot_titles',
                                    'status': 'pending', 'data': {'items': items}}]}), now))
    db.commit()


def _accept(client, data=None):
    body = {'action': 'accept'}
    if data is not None:
        body['data'] = data
    return client.post('/api/chat/proposals/m1/p1', json=body)


def test_accepting_renames_entry_and_event_and_closes_the_run(client):
    entry_id = _run(client)
    get_db().execute('UPDATE journal_screenshot_sessions SET is_open=1')
    get_db().commit()
    _card(client, [{'entryId': entry_id, 'title': 'Comparing flights'}])

    resp = _accept(client)
    assert resp.status_code == 200
    assert resp.get_json()['proposal']['result'] == {'renamed': 1, 'skipped': []}
    assert _title(entry_id) == 'Comparing flights'
    assert _event(entry_id)['title'] == 'Comparing flights'
    # Named means user-authored: the next capture starts a run of its own.
    later = _upload(client, '01J0000000000000000000Z001', '2026-09-12T19:00:00-04:00')
    assert later != entry_id


def test_accepting_uses_the_edited_title(client):
    entry_id = _run(client)
    _card(client, [{'entryId': entry_id, 'title': 'Flights'}])
    assert _accept(client, {'items': [{'entryId': entry_id, 'title': 'Booking Lviv'}]}).status_code == 200
    assert _title(entry_id) == 'Booking Lviv'


def test_a_name_typed_into_the_calendar_by_hand_survives(client):
    entry_id = _run(client)
    get_db().execute('UPDATE calendar_events SET title=? WHERE id=?',
                     ('My own name', _event(entry_id)['id']))
    get_db().commit()
    _card(client, [{'entryId': entry_id, 'title': 'Flights'}])
    assert _accept(client).status_code == 200
    assert _title(entry_id) == 'Flights'
    assert _event(entry_id)['title'] == 'My own name'


def test_a_run_renamed_while_the_card_waited_is_skipped(client):
    first = _run(client, 'A')
    second = _run(client, 'B', day='2026-09-11')
    client.patch(f'/api/journal/{second}', json={'title': 'Already done'})
    get_db().execute("UPDATE calendar_events SET title='Already done' WHERE id=?",
                     (_event(second)['id'],))
    get_db().commit()
    _card(client, [{'entryId': first, 'title': 'Flights'},
                   {'entryId': second, 'title': 'Other'}])

    result = _accept(client).get_json()['proposal']['result']
    assert result['renamed'] == 1
    assert result['skipped'] == [{'entryId': second, 'reason': 'already named "Already done"'}]
    assert _title(second) == 'Already done'


def test_a_card_with_nothing_left_to_rename_stays_pending(client):
    entry_id = _run(client)
    client.delete(f'/api/journal/{entry_id}')
    _card(client, [{'entryId': entry_id, 'title': 'Flights'}])
    resp = _accept(client)
    assert resp.status_code == 400
    assert 'no longer exists' in resp.get_json()['error']
    meta = json.loads(get_db().execute("SELECT metadata FROM messages WHERE id='m1'")
                      .fetchone()['metadata'])
    assert meta['proposals'][0]['status'] == 'pending'


def test_a_blank_edited_title_refuses_the_whole_card(client):
    first = _run(client, 'A')
    second = _run(client, 'B', day='2026-09-11')
    _card(client, [{'entryId': first, 'title': 'Flights'},
                   {'entryId': second, 'title': ' '}])
    assert _accept(client).status_code == 400
    assert _title(first) == 'Screenshots'


# --- wiring ---------------------------------------------------------------

def test_both_tools_are_on_the_chat_turn():
    from backend.delegate import chat
    names = {t['function']['name'] for t in chat.TOOLS}
    assert {'find_untitled_screenshots', 'propose_screenshot_titles'} <= names
