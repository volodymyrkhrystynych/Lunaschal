"""End-to-end tests for /api/calendar: CRUD, validation, and the expansion of
recurring series into concrete occurrences across the range/date/week reads."""
import json

from backend.ai import background as ai_background


def create(client, **body):
    body.setdefault('title', 'Work')
    body.setdefault('date', '2026-07-01')
    resp = client.post('/api/calendar', data=json.dumps(body),
                       content_type='application/json')
    return resp


def create_ok(client, **body):
    resp = create(client, **body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()['id']


def listing(client, start, end):
    resp = client.get(f'/api/calendar?start={start}&end={end}')
    assert resp.status_code == 200
    return resp.get_json()


# --- create / validate ---

def test_create_requires_title_and_date(client):
    assert create(client, title='').status_code == 400
    assert create(client, date='').status_code == 400


def test_create_rejects_bad_date(client):
    resp = create(client, date='07/01/2026')
    assert resp.status_code == 400
    assert 'YYYY-MM-DD' in resp.get_json()['error']


def test_create_rejects_bad_time(client):
    assert create(client, time='9am').status_code == 400
    assert create(client, endTime='25:99').status_code == 400


def test_create_accepts_a_recurrence_rule(client):
    id = create_ok(client, repeatFreq='weekly', repeatInterval=1,
                   repeatByweekday=[1, 2, 3, 4, 5], repeatUntil='2026-12-31')
    event = client.get(f'/api/calendar/{id}').get_json()
    assert event['repeatFreq'] == 'weekly'
    assert event['repeatInterval'] == 1
    assert event['repeatByweekday'] == '1,2,3,4,5'
    assert event['repeatUntil'] == '2026-12-31'


def test_create_rejects_bad_recurrence(client):
    assert create(client, repeatFreq='fortnightly').status_code == 400
    assert create(client, repeatFreq='weekly', repeatInterval=0).status_code == 400
    assert create(client, repeatFreq='weekly', repeatByweekday=[7]).status_code == 400
    assert create(client, repeatFreq='weekly', repeatByweekday='1,2').status_code == 400
    assert create(client, repeatFreq='daily', repeatUntil='soon').status_code == 400


def test_create_rejects_until_before_start(client):
    resp = create(client, date='2026-07-10', repeatFreq='daily', repeatUntil='2026-07-01')
    assert resp.status_code == 400


# --- range expansion ---

def test_range_expands_a_weekday_series(client):
    """The whole point: 'work Mon-Fri' is one row but every weekday in view."""
    create_ok(client, title='Work', date='2026-07-01', time='09:00', endTime='17:00',
              repeatFreq='weekly', repeatByweekday=[1, 2, 3, 4, 5])
    events = listing(client, '2026-07-06', '2026-07-12')
    assert [e['date'] for e in events] == [
        '2026-07-06', '2026-07-07', '2026-07-08', '2026-07-09', '2026-07-10']
    assert all(e['title'] == 'Work' and e['isRecurring'] for e in events)
    assert all(e['time'] == '09:00' and e['endTime'] == '17:00' for e in events)
    # Each instance carries the series id, so the details modal can fetch it.
    assert len({e['id'] for e in events}) == 1


def test_range_keeps_one_off_events(client):
    create_ok(client, title='Dentist', date='2026-07-08', time='11:00')
    events = listing(client, '2026-07-06', '2026-07-12')
    assert [(e['title'], e['isRecurring']) for e in events] == [('Dentist', False)]


def test_range_sorts_by_date_then_time(client):
    create_ok(client, title='Late', date='2026-07-08', time='16:00')
    create_ok(client, title='Early', date='2026-07-08', time='08:00')
    create_ok(client, title='Yesterday', date='2026-07-07')
    assert [e['title'] for e in listing(client, '2026-07-01', '2026-07-31')] == [
        'Yesterday', 'Early', 'Late']


def test_range_respects_repeat_until(client):
    create_ok(client, date='2026-07-01', repeatFreq='daily', repeatUntil='2026-07-03')
    assert [e['date'] for e in listing(client, '2026-07-01', '2026-07-31')] == [
        '2026-07-01', '2026-07-02', '2026-07-03']


def test_range_never_precedes_the_anchor(client):
    create_ok(client, date='2026-07-15', repeatFreq='daily')
    assert listing(client, '2026-07-01', '2026-07-10') == []


def test_empty_range_args(client):
    create_ok(client)
    assert client.get('/api/calendar').get_json() == []


def test_list_by_date_expands(client):
    create_ok(client, date='2026-07-01', repeatFreq='daily')
    resp = client.get('/api/calendar/date/2026-07-20')
    assert [e['date'] for e in resp.get_json()] == ['2026-07-20']


def test_list_by_week_covers_sunday_to_saturday(client):
    create_ok(client, date='2026-07-01', repeatFreq='daily')
    # 2026-07-08 is a Wednesday; its Sunday-based week is Jul 5 - Jul 11.
    resp = client.get('/api/calendar/week/2026-07-08')
    assert [e['date'] for e in resp.get_json()] == [
        f'2026-07-{d:02d}' for d in range(5, 12)]


def test_bad_date_paths_are_rejected(client):
    assert client.get('/api/calendar/date/nope').status_code == 400
    assert client.get('/api/calendar/week/nope').status_code == 400
    assert client.get('/api/calendar/related-journals/nope').status_code == 400


# --- related journals: 4am day boundary ---

def _journal_entry(client, content, created_at):
    from backend.db import connection
    r = client.post('/api/journal', json={'content': content})
    assert r.status_code == 201, r.get_json()
    entry_id = r.get_json()['id']
    db = connection.get_db()
    db.execute('UPDATE journal_entries SET created_at=? WHERE id=?', (created_at, entry_id))
    db.commit()
    return entry_id


def test_related_journals_uses_the_4am_anchored_day(client, monkeypatch):
    """A journal entry written at 1am still belongs to the calendar day
    before, matching journal.py's merge-candidates window, not literal
    midnight-to-midnight."""
    from backend.routes import journal as journal_routes
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)
    from datetime import datetime

    evening = _journal_entry(
        client, 'Evening reflections.',
        int(datetime(2026, 7, 8, 22, 0, 0).timestamp()),
    )
    still_that_night = _journal_entry(
        client, 'Still awake past midnight.',
        int(datetime(2026, 7, 9, 1, 0, 0).timestamp()),
    )
    next_morning = _journal_entry(
        client, 'A fresh new day.',
        int(datetime(2026, 7, 9, 5, 0, 0).timestamp()),
    )

    ids = {e['id'] for e in client.get('/api/calendar/related-journals/2026-07-08').get_json()}
    assert ids == {evening, still_that_night}
    assert next_morning not in ids


# --- update / delete ---

def test_patch_updates_recurrence(client):
    id = create_ok(client, repeatFreq='daily')
    resp = client.patch(f'/api/calendar/{id}',
                        data=json.dumps({'repeatFreq': 'weekly',
                                         'repeatByweekday': [1, 3]}),
                        content_type='application/json')
    assert resp.status_code == 200
    event = client.get(f'/api/calendar/{id}').get_json()
    assert (event['repeatFreq'], event['repeatByweekday']) == ('weekly', '1,3')


def test_clearing_the_rule_clears_its_parameters(client):
    id = create_ok(client, repeatFreq='weekly', repeatInterval=2,
                   repeatByweekday=[1, 3], repeatUntil='2026-12-31')
    client.patch(f'/api/calendar/{id}', data=json.dumps({'repeatFreq': None}),
                 content_type='application/json')
    event = client.get(f'/api/calendar/{id}').get_json()
    assert event['repeatFreq'] is None
    # Stale weekdays must not survive, or re-enabling would inherit them.
    assert event['repeatInterval'] is None
    assert event['repeatByweekday'] is None
    assert event['repeatUntil'] is None


def test_patch_rejects_bad_values(client):
    id = create_ok(client)
    for body in ({'date': 'nope'}, {'time': 'noon'}, {'repeatFreq': 'hourly'}):
        resp = client.patch(f'/api/calendar/{id}', data=json.dumps(body),
                            content_type='application/json')
        assert resp.status_code == 400, body


def test_patch_and_delete_404_on_missing(client):
    assert client.patch('/api/calendar/nope', data=json.dumps({'title': 'x'}),
                        content_type='application/json').status_code == 404
    assert client.delete('/api/calendar/nope').status_code == 404


def test_delete_removes_the_whole_series(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    assert client.delete(f'/api/calendar/{id}').status_code == 200
    assert listing(client, '2026-07-01', '2026-07-31') == []


# --- per-occurrence edits ---

def test_skip_one_occurrence(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily',
                   repeatUntil='2026-07-04')
    assert client.delete(f'/api/calendar/{id}/occurrence/2026-07-02').status_code == 200
    assert [e['date'] for e in listing(client, '2026-07-01', '2026-07-31')] == [
        '2026-07-01', '2026-07-03', '2026-07-04']


def test_skip_is_idempotent(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily',
                   repeatUntil='2026-07-03')
    client.delete(f'/api/calendar/{id}/occurrence/2026-07-02')
    client.delete(f'/api/calendar/{id}/occurrence/2026-07-02')
    assert [e['date'] for e in listing(client, '2026-07-01', '2026-07-31')] == [
        '2026-07-01', '2026-07-03']


def test_move_one_occurrence(client):
    id = create_ok(client, date='2026-07-01', time='09:00', repeatFreq='daily',
                   repeatUntil='2026-07-03')
    resp = client.patch(f'/api/calendar/{id}/occurrence/2026-07-02',
                        data=json.dumps({'newDate': '2026-07-05',
                                         'newTime': '14:00'}),
                        content_type='application/json')
    assert resp.status_code == 200
    events = listing(client, '2026-07-01', '2026-07-31')
    assert [e['date'] for e in events] == ['2026-07-01', '2026-07-03', '2026-07-05']
    moved = [e for e in events if e['date'] == '2026-07-05'][0]
    assert moved['time'] == '14:00'
    assert moved['occurrenceDate'] == '2026-07-02'


def test_moving_then_skipping_the_same_occurrence(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily',
                   repeatUntil='2026-07-03')
    client.patch(f'/api/calendar/{id}/occurrence/2026-07-02',
                 data=json.dumps({'newDate': '2026-07-05'}),
                 content_type='application/json')
    client.delete(f'/api/calendar/{id}/occurrence/2026-07-02')
    assert [e['date'] for e in listing(client, '2026-07-01', '2026-07-31')] == [
        '2026-07-01', '2026-07-03']


def test_occurrence_edits_404_on_missing_series(client):
    assert client.delete('/api/calendar/nope/occurrence/2026-07-02').status_code == 404
    assert client.patch('/api/calendar/nope/occurrence/2026-07-02',
                        data=json.dumps({}),
                        content_type='application/json').status_code == 404


def test_occurrence_edits_validate_dates(client):
    id = create_ok(client, repeatFreq='daily')
    assert client.delete(f'/api/calendar/{id}/occurrence/nope').status_code == 400
    assert client.patch(f'/api/calendar/{id}/occurrence/2026-07-02',
                        data=json.dumps({'newDate': 'nope'}),
                        content_type='application/json').status_code == 400


# --- ending a series from a date ("this and future") ---

def test_end_series_keeps_the_past(client):
    """Cancelling an ongoing commitment must not erase the days already worked."""
    id = create_ok(client, title='Work', date='2026-07-01', time='09:00',
                   repeatFreq='weekly', repeatByweekday=[1, 2, 3, 4, 5])
    resp = client.delete(f'/api/calendar/{id}/from/2026-07-15')
    assert resp.status_code == 200
    assert resp.get_json()['deleted'] is False

    kept = [e['date'] for e in listing(client, '2026-07-01', '2026-07-31')]
    assert kept[0] == '2026-07-01'
    assert kept[-1] == '2026-07-14'      # the day before the cancellation
    assert '2026-07-15' not in kept


def test_end_series_caps_the_rule(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    client.delete(f'/api/calendar/{id}/from/2026-07-15')
    assert client.get(f'/api/calendar/{id}').get_json()['repeatUntil'] == '2026-07-14'


def test_end_series_from_the_first_occurrence_removes_it(client):
    # Nothing ever happened, so there is no history to protect.
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    resp = client.delete(f'/api/calendar/{id}/from/2026-07-01')
    assert resp.get_json()['deleted'] is True
    assert client.get(f'/api/calendar/{id}').status_code == 404


def test_end_series_drops_future_exceptions(client):
    from backend.db import connection
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    client.delete(f'/api/calendar/{id}/occurrence/2026-07-05')   # before the cut
    client.delete(f'/api/calendar/{id}/occurrence/2026-07-20')   # after it
    client.delete(f'/api/calendar/{id}/from/2026-07-15')
    left = [r[0] for r in connection.get_db().execute(
        'SELECT date FROM calendar_event_exceptions WHERE event_id=?', (id,))]
    assert left == ['2026-07-05']


def test_end_series_validates(client):
    id = create_ok(client, repeatFreq='daily')
    assert client.delete(f'/api/calendar/{id}/from/nope').status_code == 400
    assert client.delete('/api/calendar/nope/from/2026-07-15').status_code == 404


# --- splitting a series on edit ("this and future") ---

def test_update_from_preserves_past_values(client):
    """Moving work hours must not retroactively claim the old days ran late."""
    id = create_ok(client, title='Work', date='2026-07-01', time='09:00',
                   endTime='17:00', repeatFreq='daily')
    resp = client.patch(f'/api/calendar/{id}/from/2026-07-15',
                        data=json.dumps({'time': '10:00', 'endTime': '18:00'}),
                        content_type='application/json')
    assert resp.status_code == 200
    assert resp.get_json()['split'] is True

    events = listing(client, '2026-07-01', '2026-07-31')
    before = [e for e in events if e['date'] < '2026-07-15']
    after = [e for e in events if e['date'] >= '2026-07-15']
    assert before and after
    assert {e['time'] for e in before} == {'09:00'}
    assert {e['time'] for e in after} == {'10:00'}
    assert {e['endTime'] for e in after} == {'18:00'}
    # No day is lost or duplicated across the seam.
    assert [e['date'] for e in events] == [
        f'2026-07-{d:02d}' for d in range(1, 32)]


def test_update_from_links_the_new_series(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    new_id = client.patch(f'/api/calendar/{id}/from/2026-07-15',
                          data=json.dumps({'title': 'Work (new hours)'}),
                          content_type='application/json').get_json()['id']
    assert new_id != id
    new = client.get(f'/api/calendar/{new_id}').get_json()
    assert new['splitFrom'] == id
    assert new['date'] == '2026-07-15'
    assert new['repeatFreq'] == 'daily'
    # The original keeps its old title and stops the day before.
    old = client.get(f'/api/calendar/{id}').get_json()
    assert old['title'] == 'Work'
    assert old['repeatUntil'] == '2026-07-14'


def test_update_from_carries_the_end_date(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily',
                   repeatUntil='2026-07-20')
    new_id = client.patch(f'/api/calendar/{id}/from/2026-07-10',
                          data=json.dumps({'title': 'Renamed'}),
                          content_type='application/json').get_json()['id']
    assert client.get(f'/api/calendar/{new_id}').get_json()['repeatUntil'] == '2026-07-20'
    assert [e['date'] for e in listing(client, '2026-07-01', '2026-07-31')] == [
        f'2026-07-{d:02d}' for d in range(1, 21)]


def test_update_from_moves_later_exceptions(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    client.delete(f'/api/calendar/{id}/occurrence/2026-07-20')
    client.patch(f'/api/calendar/{id}/from/2026-07-15',
                 data=json.dumps({'title': 'Renamed'}),
                 content_type='application/json')
    # The skipped day must stay skipped after the split.
    assert '2026-07-20' not in [
        e['date'] for e in listing(client, '2026-07-01', '2026-07-31')]


def test_update_from_the_first_occurrence_edits_in_place(client):
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    resp = client.patch(f'/api/calendar/{id}/from/2026-07-01',
                        data=json.dumps({'title': 'Renamed'}),
                        content_type='application/json')
    assert resp.get_json() == {'id': id, 'split': False}
    assert client.get(f'/api/calendar/{id}').get_json()['title'] == 'Renamed'


def test_update_from_on_a_one_off_edits_in_place(client):
    id = create_ok(client, date='2026-07-01')
    resp = client.patch(f'/api/calendar/{id}/from/2026-07-01',
                        data=json.dumps({'title': 'Renamed'}),
                        content_type='application/json')
    assert resp.get_json()['split'] is False
    assert client.get(f'/api/calendar/{id}').get_json()['title'] == 'Renamed'


def test_update_from_validates(client):
    id = create_ok(client, repeatFreq='daily')
    assert client.patch(f'/api/calendar/{id}/from/nope', data=json.dumps({}),
                        content_type='application/json').status_code == 400
    assert client.patch(f'/api/calendar/{id}/from/2026-07-15',
                        data=json.dumps({'time': 'noon'}),
                        content_type='application/json').status_code == 400
    assert client.patch('/api/calendar/nope/from/2026-07-15', data=json.dumps({}),
                        content_type='application/json').status_code == 404


def test_plain_patch_still_rewrites_everything(client):
    """"All events" remains available for genuinely retroactive corrections."""
    id = create_ok(client, title='Wrok', date='2026-07-01', repeatFreq='daily')
    client.patch(f'/api/calendar/{id}', data=json.dumps({'title': 'Work'}),
                 content_type='application/json')
    titles = {e['title'] for e in listing(client, '2026-07-01', '2026-07-31')}
    assert titles == {'Work'}


def test_deleting_the_series_cascades_exceptions(client):
    from backend.db import connection
    id = create_ok(client, date='2026-07-01', repeatFreq='daily')
    client.delete(f'/api/calendar/{id}/occurrence/2026-07-02')
    client.delete(f'/api/calendar/{id}')
    left = connection.get_db().execute(
        'SELECT COUNT(*) FROM calendar_event_exceptions').fetchone()[0]
    assert left == 0


# --- all-day ---

def test_create_all_day_event(client):
    id = create_ok(client, allDay=True)
    event = client.get(f'/api/calendar/{id}').get_json()
    assert event['allDay'] == 1
    assert event['time'] is None and event['endTime'] is None


def test_create_rejects_all_day_with_a_time(client):
    resp = create(client, allDay=True, time='09:00')
    assert resp.status_code == 400
    assert 'all-day' in resp.get_json()['error']


def test_events_default_to_not_all_day(client):
    """Rows that predate the flag stay merely untimed, not retroactively
    relabelled — so an event created without a time is allDay=0."""
    id = create_ok(client)
    assert client.get(f'/api/calendar/{id}').get_json()['allDay'] == 0


def test_switching_to_all_day_clears_the_stored_times(client):
    id = create_ok(client, time='09:00', endTime='17:00')
    resp = client.patch(f'/api/calendar/{id}', data=json.dumps({'allDay': True}),
                        content_type='application/json')
    assert resp.status_code == 200, resp.get_json()
    event = client.get(f'/api/calendar/{id}').get_json()
    assert event['allDay'] == 1
    assert event['time'] is None and event['endTime'] is None


def test_switching_off_all_day_leaves_it_untimed(client):
    id = create_ok(client, allDay=True)
    client.patch(f'/api/calendar/{id}', data=json.dumps({'allDay': False}),
                 content_type='application/json')
    event = client.get(f'/api/calendar/{id}').get_json()
    assert event['allDay'] == 0
    assert event['time'] is None


def test_all_day_yearly_birthday_expands(client):
    """The combination the feature exists for: an all-day yearly event."""
    id = create_ok(client, title='Birthday', date='1990-08-09',
                   allDay=True, repeatFreq='yearly')
    events = listing(client, '2026-01-01', '2028-12-31')
    mine = [e for e in events if e['id'] == id]
    assert [e['date'] for e in mine] == ['2026-08-09', '2027-08-09', '2028-08-09']
    assert all(e['allDay'] == 1 and e['isRecurring'] for e in mine)


def test_all_day_survives_a_this_and_future_split(client):
    id = create_ok(client, date='2026-01-01', allDay=True, repeatFreq='yearly')
    resp = client.patch(f'/api/calendar/{id}/from/2028-01-01',
                        data=json.dumps({'title': 'Renamed'}),
                        content_type='application/json')
    assert resp.status_code == 200, resp.get_json()
    new_id = resp.get_json()['id']
    assert client.get(f'/api/calendar/{new_id}').get_json()['allDay'] == 1


# --- Free-text tags -------------------------------------------------------
#
# The column and the _SPLIT_COLUMNS carry both predate any way for the user to
# write one, so none of this had coverage: the write path used a raw json.dumps
# and every other feature's tags went through backend/tags.py.


def test_tags_are_normalized_on_create(client):
    """"Work", "work " and "work" are one tag everywhere else in the app, and a
    calendar event is not a good place for them to be three."""
    id = create_ok(client, tags=['Work', 'work ', 'Deadline', 'work'])
    assert json.loads(client.get(f'/api/calendar/{id}').get_json()['tags']) == [
        'work', 'deadline',
    ]


def test_tags_are_normalized_on_update(client):
    id = create_ok(client, tags=['work'])
    resp = client.patch(f'/api/calendar/{id}', data=json.dumps({'tags': ['  Family ']}),
                        content_type='application/json')
    assert resp.status_code == 200
    assert json.loads(client.get(f'/api/calendar/{id}').get_json()['tags']) == ['family']


def test_clearing_the_tags_stores_null_not_an_empty_array(client):
    """'[]' reads as "has tags" to anything checking the column for NULL, and
    the tag-count endpoint would carry the row for nothing."""
    id = create_ok(client, tags=['work'])
    client.patch(f'/api/calendar/{id}', data=json.dumps({'tags': []}),
                 content_type='application/json')
    assert client.get(f'/api/calendar/{id}').get_json()['tags'] is None


def test_a_non_list_tags_payload_is_dropped_rather_than_stored(client):
    """It used to be serialized verbatim and then degrade to "no tags" on read,
    which looks identical to the tags never having been sent."""
    id = create_ok(client, tags='work')
    assert client.get(f'/api/calendar/{id}').get_json()['tags'] is None


def test_the_tags_endpoint_counts_across_every_event(client):
    create_ok(client, date='2026-07-01', tags=['work', 'urgent'])
    create_ok(client, date='2026-09-15', tags=['work'])
    create_ok(client, date='2026-11-02')

    tags = client.get('/api/calendar/tags').get_json()
    # Count descending, then name — the shape the cookbook pill row reads.
    assert tags == [{'name': 'work', 'count': 2}, {'name': 'urgent', 'count': 1}]


def test_tags_survive_a_this_and_future_split(client):
    """`tags` is in _SPLIT_COLUMNS; without that the new series comes back
    untagged and silently drops out of its own filter."""
    id = create_ok(client, date='2026-01-01', tags=['work'], repeatFreq='yearly')
    resp = client.patch(f'/api/calendar/{id}/from/2028-01-01',
                        data=json.dumps({'title': 'Renamed'}),
                        content_type='application/json')
    assert resp.status_code == 200, resp.get_json()
    new_id = resp.get_json()['id']
    assert json.loads(client.get(f'/api/calendar/{new_id}').get_json()['tags']) == ['work']


# --- manual category tags ---

def test_category_tags_can_be_set_on_create(client):
    id = create_ok(client, categoryTags=['work', 'indoors'])
    event = client.get(f'/api/calendar/{id}').get_json()
    assert json.loads(event['categoryTags']) == ['work', 'indoors']
    assert event['classifiedAt'] is not None
    assert event['classificationError'] is None


def test_category_tags_can_be_set_manually_on_update(client):
    """The whole point: setting these by hand is what makes the Journal feed
    draw the event's grouping border around entries in its time window,
    without waiting on a transcribed description and the AI classifier."""
    id = create_ok(client)
    resp = client.patch(f'/api/calendar/{id}', data=json.dumps({'categoryTags': ['family', 'outside']}),
                        content_type='application/json')
    assert resp.status_code == 200, resp.get_json()
    event = client.get(f'/api/calendar/{id}').get_json()
    assert json.loads(event['categoryTags']) == ['family', 'outside']
    assert event['classifiedAt'] is not None


def test_setting_category_tags_manually_clears_a_prior_classification_error(client):
    id = create_ok(client)
    client.patch(f'/api/calendar/{id}', data=json.dumps({'categoryTags': ['leisure']}),
                 content_type='application/json')
    # Simulate a later failed re-classification, then a manual override.
    from backend.db.connection import get_db
    get_db().execute("UPDATE calendar_events SET classification_error='boom' WHERE id=?", (id,))
    get_db().commit()
    client.patch(f'/api/calendar/{id}', data=json.dumps({'categoryTags': ['work']}),
                 content_type='application/json')
    event = client.get(f'/api/calendar/{id}').get_json()
    assert event['classificationError'] is None


def test_category_tags_reject_values_outside_the_closed_vocabulary(client):
    resp = create(client, categoryTags=['work', 'made-up'])
    assert resp.status_code == 400


def test_category_tags_are_deduped_and_capped_at_three(client):
    id = create_ok(client, categoryTags=['work', 'work', 'leisure', 'family', 'outside'])
    event = client.get(f'/api/calendar/{id}').get_json()
    assert json.loads(event['categoryTags']) == ['work', 'leisure', 'family']


def test_clearing_category_tags_stores_null_not_an_empty_array(client):
    id = create_ok(client, categoryTags=['work'])
    client.patch(f'/api/calendar/{id}', data=json.dumps({'categoryTags': []}),
                 content_type='application/json')
    assert client.get(f'/api/calendar/{id}').get_json()['categoryTags'] is None


def test_a_client_supplied_id_replays_without_duplicating(client):
    """An event created offline carries the id the browser minted, so the
    queued write can be replayed — after a dropped answer, or a reload that
    replayed the whole queue — without producing a second event."""
    body = {'id': '01ARZ3NDEKTSV4RRFFQ69G5FAV', 'title': 'Dentist', 'date': '2026-08-05'}
    first = client.post('/api/calendar', json=body)
    assert first.status_code == 201
    assert first.get_json()['id'] == body['id']

    second = client.post('/api/calendar', json=body)
    assert second.status_code == 201

    events = client.get('/api/calendar?start=2026-08-01&end=2026-08-31').get_json()
    assert [e['title'] for e in events] == ['Dentist']
