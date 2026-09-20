"""Day reconstruction: evidence, dates, places, and the review lifecycle."""
import json
from datetime import datetime

import pytest

from backend import briefing_job
from backend.briefing_events import gather_day, stage_events, validate_event, calendar_for_day
from backend.db.connection import get_db
from backend.day_boundary import day_bounds
from backend.places import nearby_places

DAY = '2026-07-13'
TODAY = '2026-07-14'
START, END = day_bounds(DAY)
NOW = int(datetime(2026, 7, 14, 9).timestamp())


def journal(id, text, at):
    get_db().execute('INSERT INTO journal_entries(id,content,created_at,updated_at) VALUES (?,?,?,?)', (id, text, at, at))


def event(title='Chores', **changes):
    return dict(title=title, description='Cooking and cleaning', date=DAY, time='07:00', endTime='09:00',
                location='Home', tags=['Home', 'home'], evidence='Evening entry describes morning chores; times estimated.',
                sourceIds=['journal:evening'], **changes)


def prepare(monkeypatch, items=None):
    journal('evening', 'Before my first appointment I cooked and did chores. After the last I talked with my brother.', END - 5 * 3600)
    db = get_db()
    db.commit()
    monkeypatch.setattr(briefing_job, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: {
        'briefing': 'Good morning', 'todos': [], 'events': items if items is not None else [event()],
    })
    result = briefing_job.run_briefing(now=NOW)
    meta = json.loads(db.execute('SELECT metadata FROM messages WHERE id=?', (result['messageId'],)).fetchone()[0])
    return result, meta['proposals']


def test_entire_day_not_recent_entry_cap_and_location_evidence(client):
    journal('before', 'Exclude previous day', START - 1)
    for i in range(35):
        journal(str(i), 'Full entry ' + str(i), START + i * 60)
    journal('last', 'Late-night family visit', END - 1)
    journal('after', 'Exclude current day', END)
    db = get_db()
    place = client.post('/api/memory/places', json={'name': 'Home', 'latitude': 43.65, 'longitude': -79.38}).get_json()
    assert place['radiusM'] == 150
    db.execute('UPDATE journal_entries SET latitude=43.65,longitude=-79.38 WHERE id=?', ('last',))
    db.execute('INSERT INTO food_entries(id,dish,place,raw_content,latitude,longitude,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
               ('meal', 'Soup', 'Home', 'Cooked lunch', 43.65, -79.38, START + 8 * 3600, START))
    db.execute('INSERT INTO journal_attachments(id,entry_id,kind,name,path,description,latitude,longitude,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
               ('photo', 'last', 'image', 'Family', 'fake.jpg', 'Two people in a kitchen', 43.65, -79.38, END - 1))
    db.execute('INSERT INTO transcriptions(id,text,source,created_at) VALUES (?,?,?,?)', ('speech', 'Finished chores', 'journal', START + 3600))
    db.execute('INSERT INTO task_events(id,kind,title,created_at) VALUES (?,?,?,?)', ('task', 'completed', 'Laundry', START + 3600))
    db.commit()
    ctx = gather_day(db, TODAY)
    ids = {s['sourceId'] for s in ctx['sources']}
    assert len([s for s in ids if s.startswith('journal:')]) == 36
    assert 'journal:before' not in ids and 'journal:after' not in ids
    assert {'food:meal', 'transcription:speech', 'task:task'} <= ids
    last = next(s for s in ctx['sources'] if s['sourceId'] == 'journal:last')
    assert last['nearbyPlaces'] == ['Home']
    assert last['attachments'][0]['nearbyPlaces'] == ['Home']
    assert last['attachments'][0]['description'] == 'Two people in a kitchen'


def test_commentary_and_all_other_feed_kinds(client):
    db = get_db()
    journal('reading', 'This fictional battle was exciting', START + 5 * 3600)
    db.execute("INSERT INTO fics(id,title,source_type,created_at,updated_at) VALUES ('fic','A novel','epub',?,?)", (START, START))
    db.execute("INSERT INTO journal_entry_fic_refs(id,journal_entry_id,fic_id,created_at) VALUES ('ref','reading','fic',?)", (START,))
    db.execute("INSERT INTO papers(id,title,archive_requested_at,content_updated_at,created_at,updated_at) VALUES ('paper','Drawing',?,?,?,?)", (START + 600, START + 300, START, START))
    db.execute("INSERT INTO study_sources(id,title,kind,archive_requested_at,created_at,updated_at) VALUES ('study','Physics','pdf',?,?,?)", (START + 600, START, START))
    db.execute("INSERT INTO newspaper_issues(id,date,pdf_path,byte_size,page_count,created_at) VALUES ('news',?,'fake.pdf',1,1,?)", (DAY, START))
    db.execute("INSERT INTO conversations(id,day_key,created_at,updated_at) VALUES ('chat',?,?,?)", (DAY, START, START))
    db.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES ('user','chat','user','I went for a walk',?)", (START,))
    db.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES ('bot','chat','assistant','Maybe you went swimming',?)", (START,))
    db.commit()
    sources = {s['sourceId']: s for s in gather_day(db, TODAY)['sources']}
    assert {'journal:reading', 'paper:paper', 'study:study', 'newspaper:news', 'message:user'} <= sources.keys()
    assert 'message:bot' not in sources
    assert sources['journal:reading']['chapterCommentary'][0]['story'] == 'A novel'
    assert sources['newspaper:news']['last_read_at'] is None


def test_calendar_includes_recurrence_exceptions_and_midnight(client):
    db = get_db()
    for id, day, start, end, freq in [('work', '2026-07-01', '09:00', '17:00', 'daily'),
                                     ('late', TODAY, '01:00', '02:00', None),
                                     ('today', TODAY, '09:00', None, None),
                                     ('carry', '2026-07-12', '23:00', '05:00', None)]:
        db.execute('INSERT INTO calendar_events(id,title,date,time,end_time,repeat_freq,created_at) VALUES (?,?,?,?,?,?,?)',
                   (id, id, day, start, end, freq, NOW))
    db.execute("INSERT INTO calendar_event_exceptions(id,event_id,date,action,created_at) VALUES ('skip','work',?,'skip',?)", (DAY, NOW))
    db.commit()
    assert {e['title'] for e in calendar_for_day(db, DAY)} == {'late', 'carry'}
    db.execute('DELETE FROM calendar_event_exceptions')
    assert {e['title'] for e in calendar_for_day(db, DAY)} == {'work', 'late', 'carry'}


def test_proposals_edit_accept_dismiss_and_rerun(client, monkeypatch):
    chores = event()
    family = {**event('Family time'), 'time': '20:00', 'endTime': '21:00'}
    result, proposals = prepare(monkeypatch, [chores, family])
    db = get_db()
    assert result['eventsSuggested'] == 2
    assert db.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0
    p = proposals[0]
    data = {**p['data'], 'title': 'Cooking and laundry', 'time': '07:30', 'location': 'Brother’s house'}
    url = f"/api/chat/proposals/{result['messageId']}/{p['id']}"
    response = client.post(url, json={'action': 'accept', 'data': data})
    assert response.status_code == 200
    row = db.execute('SELECT * FROM calendar_events').fetchone()
    assert (row['title'], row['date'], row['time']) == ('Cooking and laundry', DAY, '07:30')
    assert 'Location: Brother’s house' in row['description']
    assert json.loads(row['tags']) == ['home']
    assert client.post(url, json={'action': 'accept'}).status_code == 400
    assert client.post(f"/api/chat/proposals/{result['messageId']}/{proposals[1]['id']}", json={'action': 'dismiss'}).status_code == 200
    # A changed title from the model still cannot recreate the same evidence/span.
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: {'briefing': 'Again', 'todos': [], 'events': [{**chores, 'title': 'Morning tasks'}, family]})
    assert briefing_job.run_briefing(now=NOW, force=True)['eventsSuggested'] == 0
    assert db.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 1


# {'time': '03:00'} rolls to the small hours of TODAY (see the roll test) and is
# then rejected for running to 09:00, past the 04:00 boundary -- still a bad edit.
@pytest.mark.parametrize('patch', [{'date': TODAY}, {'time': '99:00'}, {'time': '03:00'},
                                  {'endTime': '05:00'}, {'allDay': True}, {'title': []}])
def test_bad_edits_leave_pending(client, monkeypatch, patch):
    result, proposals = prepare(monkeypatch)
    p = proposals[0]
    response = client.post(f"/api/chat/proposals/{result['messageId']}/{p['id']}", json={'action': 'accept', 'data': {**p['data'], **patch}})
    assert response.status_code == 400
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0
    meta = json.loads(get_db().execute('SELECT metadata FROM messages WHERE id=?', (result['messageId'],)).fetchone()[0])
    assert meta['proposals'][0]['status'] == 'pending'


def test_late_night_and_untimed_validation():
    assert validate_event({**event(), 'date': TODAY, 'time': '01:00', 'endTime': '03:00'}, DAY)['date'] == TODAY
    assert validate_event({**event(), 'time': '23:00', 'endTime': '02:00'}, DAY)['date'] == DAY
    assert validate_event({**event(), 'time': None, 'endTime': None}, DAY)['time'] is None


def test_small_hours_roll_to_the_next_calendar_date():
    """A 01:00 filed under the day key is the tail of that 4am day, not an error.

    There is no 01:00 on DAY inside DAY's window, so the date is unambiguous and
    gets corrected rather than rejected -- which is what kept an activity
    narrated at 3am out of the suggestions entirely.
    """
    rolled = validate_event({**event(), 'time': '01:00', 'endTime': '03:00'}, DAY)
    assert (rolled['date'], rolled['time'], rolled['endTime']) == (TODAY, '01:00', '03:00')
    # 03:59 is the last minute that rolls; 04:00 is already the day's own start.
    assert validate_event({**event(), 'time': '03:59', 'endTime': None}, DAY)['date'] == TODAY
    assert validate_event({**event(), 'time': '04:00', 'endTime': None}, DAY)['date'] == DAY
    # Rolling does not buy extra room: the span must still finish by 04:00.
    with pytest.raises(ValueError):
        validate_event({**event(), 'time': '02:00', 'endTime': '09:00'}, DAY)
    # Only the day key rolls. A date that is neither bound stays an error.
    with pytest.raises(ValueError):
        validate_event({**event(), 'date': '2026-07-11', 'time': '01:00', 'endTime': None}, DAY)


def test_three_am_account_of_the_last_two_hours_is_staged(client, monkeypatch):
    """End to end: the model writes the day key it was handed for a 01:00 event.

    stage_events swallows validation errors, so before the roll this suggestion
    vanished silently instead of reaching the card.
    """
    journal('late', 'It is 3am. I spent the last two hours sorting photographs.', END - 3600)
    get_db().commit()
    late = {**event('Sorting photographs'), 'date': DAY, 'time': '01:00', 'endTime': '03:00',
            'sourceIds': ['journal:late'], 'evidence': '3am entry describes the previous two hours.'}
    result, proposals = prepare(monkeypatch, [late])
    assert result['eventsSuggested'] == 1
    assert proposals[0]['data']['date'] == TODAY
    assert proposals[0]['data']['time'] == '01:00'
    # And it saves onto the right calendar date when accepted.
    assert client.post(f"/api/chat/proposals/{result['messageId']}/{proposals[0]['id']}",
                       json={'action': 'accept'}).status_code == 200
    row = get_db().execute('SELECT date,time,end_time FROM calendar_events').fetchone()
    assert (row['date'], row['time'], row['end_time']) == (TODAY, '01:00', '03:00')


def test_gather_day_names_the_dates_at_both_ends():
    assert (gather_day(get_db(), TODAY)['day'], gather_day(get_db(), TODAY)['nextDate']) == (DAY, TODAY)


def test_unknown_sources_duplicates_and_new_calendar_event(client, monkeypatch):
    result, proposals = prepare(monkeypatch)
    db = get_db()
    ctx = gather_day(db, TODAY)
    assert stage_events(db, [{**event('Invented'), 'sourceIds': ['journal:missing']}], ctx) == []
    db.execute('INSERT INTO calendar_events(id,title,date,created_at) VALUES (?,?,?,?)', ('existing', 'Chores', DAY, NOW))
    db.commit()
    assert stage_events(db, [event()], ctx) == []
    response = client.post(f"/api/chat/proposals/{result['messageId']}/{proposals[0]['id']}", json={'action': 'accept'})
    assert response.status_code == 400
    assert db.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 1


def test_places_crud_validation_and_prompt(client):
    from backend.ai.chat import build_chat_system_prompt
    created = client.post('/api/memory/places', json={'name': 'Home', 'notes': 'My apartment', 'latitude': 43.65, 'longitude': -79.38})
    assert created.status_code == 200
    place = created.get_json()
    assert 'My apartment' in build_chat_system_prompt(NOW)
    assert client.post('/api/memory/places', json={'name': 'home'}).status_code == 400
    for bad in ({'latitude': 20}, {'latitude': 91, 'longitude': 0}, {'radiusM': 0}, {'name': []}):
        assert client.post('/api/memory/places', json={'name': 'Work', **bad}).status_code == 400
    assert client.put(f"/api/memory/places/{place['id']}", json={'name': 'Home', 'notes': 'New address'}).status_code == 200
    assert client.get('/api/memory/places').get_json()[0]['notes'] == 'New address'
    assert client.delete(f"/api/memory/places/{place['id']}").status_code == 200
    assert client.get('/api/memory/places').get_json() == []


def test_nearby_places_preserves_ambiguity_and_unknowns():
    places = [{'name': name, 'latitude': 43.65, 'longitude': -79.38, 'radius_m': 150} for name in ('Home', 'Work')]
    assert set(nearby_places(43.65, -79.38, places)) == {'Home', 'Work'}
    assert nearby_places(None, None, places) == []
    assert nearby_places(45, -79.38, places) == []


def test_three_activity_example_reaches_prompt_and_reviews_separately(client, monkeypatch):
    from backend.ai import briefing
    db = get_db()
    for id, start, end in [('first', '10:00', '11:00'), ('second', '16:00', '17:00')]:
        db.execute('INSERT INTO calendar_events(id,title,date,time,end_time,created_at) VALUES (?,?,?,?,?,?)',
                   (id, id, DAY, start, end, START))
    journal('reading', 'Chapter 5 was excellent. I read until my next appointment.', START + 9 * 3600)
    journal('evening', 'I cooked and cleaned before the first appointment, then talked with my brother after the last one.', START + 17 * 3600)
    db.commit()
    monkeypatch.setattr(briefing_job, 'is_ai_configured', lambda: True)

    def complete(prompt, **kwargs):
        # The model sees the whole day before reconstructing the chronology.
        assert 'cooked and cleaned before' in prompt
        assert 'Chapter 5 was excellent' in prompt
        assert '10:00' in prompt and '16:00' in prompt
        assert 'recording time is not the' in kwargs['system']
        assert 'events' in kwargs['schema']['required']
        return {'briefing': 'Good morning', 'todos': [], 'events': [
            event(),
            {**event('Leisure reading'), 'time': '11:00', 'endTime': '16:00', 'sourceIds': ['journal:reading']},
            {**event('Family time'), 'time': '18:00', 'endTime': '20:00'},
            # The deterministic guard rejects a duplicate even if the model
            # disregarded its instruction to skip an existing event.
            {**event('first'), 'time': '10:00', 'endTime': '11:00'},
        ]}

    monkeypatch.setattr(briefing, 'chat_json', complete)
    result = briefing_job.run_briefing(now=NOW)
    assert result['eventsSuggested'] == 3
    meta = json.loads(db.execute('SELECT metadata FROM messages WHERE id=?', (result['messageId'],)).fetchone()[0])
    assert [p['data']['title'] for p in meta['proposals']] == ['Chores', 'Leisure reading', 'Family time']
    assert db.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 2


def test_two_devices_cannot_approve_the_same_suggestion_twice(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    result, proposals = prepare(monkeypatch)
    url = f"/api/chat/proposals/{result['messageId']}/{proposals[0]['id']}"
    barrier = Barrier(2)
    app = client.application

    def approve():
        with app.test_client() as device:
            barrier.wait(timeout=5)
            return device.post(url, json={'action': 'accept'}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [200, 400]
    assert get_db().execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 1


def test_two_untimed_activities_can_cite_one_evening_recap(client):
    journal('evening', 'I cooked and then visited my brother', END - 3600)
    db = get_db()
    ctx = gather_day(db, TODAY)
    proposals = stage_events(db, [{**event(title), 'time': None, 'endTime': None} for title in ('Cooking', 'Family time')], ctx)
    assert len(proposals) == 2
