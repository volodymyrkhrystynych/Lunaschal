"""End-to-end tests for /api/calendar: CRUD, validation, and the expansion of
recurring series into concrete occurrences across the range/date/week reads."""
import json


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
    for body in ({'date': 'nope'}, {'time': 'noon'}, {'repeatFreq': 'yearly'}):
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
