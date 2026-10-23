"""What the trail is turned into beyond the map: geofence stays, the work
calendar, commutes, weekly patterns, and the location of things recorded
without one (weather, meals). Tiles are off; nothing reaches the network."""
import json
from datetime import datetime

import pytest

from backend import place_roles
from backend.db.connection import get_db
from backend.location import commute, days, ingest, lookup, patterns, whereabouts, work_calendar
from backend.location.trail import Fix
from backend.place_roles import RolePlace

DAY = '2026-07-08'  # a Wednesday
HOME = (43.6677, -79.3948)
OFFICE = (43.6532, -79.3832)


def at(hhmm: str, date: str = DAY) -> float:
    return datetime.fromisoformat(f'{date}T{hhmm}:00').timestamp()


def captured(hhmm: str) -> str:
    return datetime.fromtimestamp(at(hhmm)).astimezone().isoformat()


@pytest.fixture(autouse=True)
def _no_tiles(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCATION_TILE_URL', '')
    monkeypatch.setenv('LOCATION_ROOT', str(tmp_path / 'location'))


@pytest.fixture
def places():
    db = get_db()
    for pid, name, (lat, lon) in (('PLACE-HOME', 'Home', HOME), ('PLACE-OFFICE', 'Office', OFFICE)):
        db.execute('INSERT INTO saved_places (id, name, latitude, longitude, radius_m, created_at, updated_at)'
                   ' VALUES (?,?,?,?,150,0,0)', (pid, name, lat, lon))
    db.commit()
    place_roles.assign(db, 'home', 'PLACE-HOME', '2026-01-01')
    place_roles.assign(db, 'work', 'PLACE-OFFICE', '2026-01-01')


def region(pid, place_id, arrive, depart, coords):
    return {'id': pid, 'ts': arrive, 'latitude': coords[0], 'longitude': coords[1], 'kind': 'region',
            'placeId': place_id, **({'departed': depart} if depart else {})}


def fine(pid, ts, lat, lon, activity=None):
    return {'id': pid, 'ts': ts, 'latitude': lat, 'longitude': lon, 'kind': 'fine', 'accuracy': 10,
            **({'activity': activity} if activity else {})}


def workday(client, *, leave_home='08:30', arrive='09:00', leave='17:30', home_again='18:05', activity='cycling'):
    """Home overnight, a ride to the office, the day there, a ride back."""
    points = [
        region('region-home-am', 'PLACE-HOME', at('04:00'), at(leave_home), HOME),
        region('region-office', 'PLACE-OFFICE', at(arrive), at(leave) if leave else None, OFFICE),
    ]
    for i in range(5):
        t = at(leave_home) + (at(arrive) - at(leave_home)) * (i + 1) / 6
        lat = HOME[0] + (OFFICE[0] - HOME[0]) * (i + 1) / 6
        lon = HOME[1] + (OFFICE[1] - HOME[1]) * (i + 1) / 6
        points.append(fine(f'fix-am-{i:04d}', t, lat, lon, activity))
    if leave and home_again:
        points.append(region('region-home-pm', 'PLACE-HOME', at(home_again), None, HOME))
        for i in range(5):
            t = at(leave) + (at(home_again) - at(leave)) * (i + 1) / 6
            lat = OFFICE[0] + (HOME[0] - OFFICE[0]) * (i + 1) / 6
            lon = OFFICE[1] + (HOME[1] - OFFICE[1]) * (i + 1) / 6
            points.append(fine(f'fix-pm-{i:04d}', t, lat, lon, activity))
    res = client.post('/api/location/points', json={'points': points})
    assert res.status_code == 200, res.get_json()
    assert 'stayDays' not in res.get_json() and 'span' not in res.get_json()
    return res.get_json()


def work_series(time_='09:00', end='17:00', **extra):
    db = get_db()
    db.execute("INSERT INTO calendar_events (id, title, date, time, end_time, created_at, repeat_freq,"
               " repeat_interval, repeat_byweekday, category_tags, classified_at)"
               " VALUES ('WORK', 'Work', '2026-01-05', ?, ?, 0, 'weekly', 1, '1,2,3,4,5', ?, 0)",
               (time_, end, extra.get('category_tags', json.dumps(['work']))))
    db.commit()


def occurrence(day=DAY):
    from backend.calendar_query import events_in_range
    return [e for e in events_in_range(get_db(), day, day) if e['id'] == 'WORK']


# --- ingest ---

def test_region_stays_need_their_place_and_activity_is_kept():
    result = ingest.ingest({'points': [
        {'id': 'region-noplace', 'ts': at('09:00'), 'latitude': 1, 'longitude': 1, 'kind': 'region'},
        fine('fix-walking1', at('09:00'), 1, 1, 'walking'),
        fine('fix-flying01', at('09:01'), 1, 1, 'teleporting'),
    ]})
    assert result['rejectedCount'] == 1 and 'placeId' in result['rejected'][0]['reason']
    rows = {r['id']: r['activity'] for r in get_db().execute('SELECT id, activity FROM location_points')}
    assert rows == {'fix-walking1': 'walking', 'fix-flying01': None}


# --- whereabouts: a visit and a geofence stay at one place are one stay ---

def test_overlapping_visit_and_region_stay_are_not_counted_twice():
    office = RolePlace('work', 'PLACE-OFFICE', 'Office', *OFFICE, 150)
    fixes = [
        Fix(at('09:00'), *OFFICE, 'region', None, at('17:00'), 'PLACE-OFFICE'),
        Fix(at('09:20'), *OFFICE, 'visit', 30, at('16:50')),
        Fix(at('17:10'), *OFFICE, 'visit', 30, at('17:30')),  # back in after 10 min: same stay
    ]
    stays = whereabouts.stays(fixes, [office], at('23:00'))
    [span] = whereabouts.intervals(stays)
    assert (span.start, span.end) == (at('09:00'), at('17:30'))
    summary = whereabouts.summarize(stays, (at('04:00'), at('04:00', '2026-07-09')), at('23:00'))
    assert summary['work'][0]['seconds'] == int(8.5 * 3600)


def test_a_region_stay_at_a_place_that_is_no_longer_a_role_matches_nothing():
    office = RolePlace('work', 'PLACE-OFFICE', 'Office', *OFFICE, 150)
    old = Fix(at('09:00'), *OFFICE, 'region', None, at('12:00'), 'PLACE-FORMER')
    assert whereabouts.match(old, [office]) is None


# --- commutes ---

def test_a_commute_each_way_with_its_mode_from_motion(client, places):
    workday(client)
    summary = days.day_summary(DAY, None, place_roles.roles_for_day(place_roles.list_periods(get_db()), DAY),
                               at('23:00'))
    there, back = summary['commutes']
    assert (there['direction'], there['from'], there['to']) == ('toWork', 'Home', 'Office')
    assert there['seconds'] == 30 * 60 and there['mode'] == 'cycling' and there['modeSource'] == 'motion'
    assert 1500 < there['meters'] < 2500 and there['tracked']
    assert (back['direction'], back['seconds']) == ('toHome', 35 * 60)


def test_without_motion_the_mode_comes_from_speed(client, places):
    workday(client, activity=None)
    roles = place_roles.roles_for_day(place_roles.list_periods(get_db()), DAY)
    trip = days.day_summary(DAY, None, roles, at('23:00'))['commutes'][0]
    # ~2 km in 30 minutes is a walk.
    assert (trip['mode'], trip['modeSource']) == ('walking', 'speed')


def test_a_stop_somewhere_else_on_the_way_is_not_a_commute():
    home = RolePlace('home', 'PLACE-HOME', 'Home', *HOME, 150)
    office = RolePlace('work', 'PLACE-OFFICE', 'Office', *OFFICE, 150)
    fixes = [
        Fix(at('04:00'), *HOME, 'region', None, at('08:00'), 'PLACE-HOME'),
        Fix(at('08:15'), 43.70, -79.40, 'visit', 30, at('08:50')),  # coffee somewhere
        Fix(at('09:00'), *OFFICE, 'region', None, at('17:00'), 'PLACE-OFFICE'),
    ]
    assert commute.commutes(whereabouts.stays(fixes, [home, office], at('23:00')), fixes) == []


# --- the work calendar ---

def test_the_recurring_work_occurrence_moves_to_the_real_hours(client, places):
    work_series()
    workday(client, arrive='09:12', leave='17:41')
    [occ] = occurrence()
    assert (occ['time'], occ['end_time']) == ('09:12', '17:41')
    # Other days of the series are untouched.
    assert occurrence('2026-07-09')[0]['time'] == '09:00'


def test_while_still_at_work_only_the_start_moves(client, places):
    work_series()
    workday(client, arrive='09:12', leave=None)
    [occ] = occurrence()
    assert (occ['time'], occ['end_time']) == ('09:12', '17:00')
    # The exit lands later and finishes it.
    client.post('/api/location/points', json={'points': [
        region('region-office', 'PLACE-OFFICE', at('09:12'), at('18:02'), OFFICE)]})
    assert (occurrence()[0]['time'], occurrence()[0]['end_time']) == ('09:12', '18:02')


def test_a_day_with_no_work_event_gets_a_one_off(client, places):
    workday(client, arrive='10:00', leave='14:00')
    row = get_db().execute("SELECT * FROM calendar_events WHERE title='At Office'").fetchone()
    assert (row['date'], row['time'], row['end_time']) == (DAY, '10:00', '14:00')
    assert json.loads(row['category_tags']) == ['work'] and row['classified_at'] is not None
    # A replay updates it rather than adding a second.
    workday(client, arrive='10:00', leave='14:30')
    rows = get_db().execute("SELECT end_time FROM calendar_events WHERE title='At Office'").fetchall()
    assert [r['end_time'] for r in rows] == ['14:30']


def test_an_edit_by_the_user_releases_the_day_for_good(client, places):
    work_series()
    workday(client, arrive='09:12', leave='17:41')
    client.patch(f'/api/calendar/WORK/occurrence/{DAY}', json={'newTime': '09:00', 'newEndTime': '17:00'})
    workday(client, arrive='09:12', leave='18:30')
    assert (occurrence()[0]['time'], occurrence()[0]['end_time']) == ('09:00', '17:00')
    assert get_db().execute('SELECT released FROM work_attendance').fetchone()[0] == 1


def test_an_occurrence_the_user_already_moved_is_theirs(client, places):
    work_series()
    client.patch(f'/api/calendar/WORK/occurrence/{DAY}', json={'newTime': '11:00', 'newEndTime': '15:00'})
    workday(client)
    assert occurrence()[0]['time'] == '11:00'
    assert get_db().execute("SELECT COUNT(*) FROM calendar_events WHERE title='At Office'").fetchone()[0] == 0


def test_a_short_visit_to_the_office_is_not_a_workday(client, places):
    work_series()
    workday(client, arrive='12:00', leave='12:20', home_again=None)
    assert occurrence()[0]['time'] == '09:00'


def test_a_free_text_work_tag_counts_too(client, places):
    work_series(category_tags=None)
    get_db().execute("UPDATE calendar_events SET tags='[\"Work\"]' WHERE id='WORK'")
    get_db().commit()
    workday(client, arrive='09:30', leave='17:00')
    assert occurrence()[0]['time'] == '09:30'


# --- weekly patterns ---

def test_patterns_count_office_days_of_observed_days(client, places):
    workday(client)
    get_db().execute("INSERT INTO location_points (id, fix_ts, day_key, latitude, longitude, kind, received_at)"
                     " VALUES ('fix-thursday', ?, '2026-07-09', ?, ?, 'coarse', 0)", (at('12:00', '2026-07-09'), *HOME))
    get_db().commit()
    result = patterns.build(days.summaries_since('2026-07-06'))
    assert (result['observedDays'], result['officeDays']) == (2, 1)
    [week] = result['weeks']
    assert week['weekStart'] == '2026-07-06' and week['officeDays'] == 1
    assert week['avgArrival'] == '09:00' and week['avgDeparture'] == '17:30'
    assert week['commute']['toWork'] == {'count': 1, 'avgSeconds': 1800}
    assert result['weekdays'][2] == {'weekday': 'Wed', 'observedDays': 1, 'officeDays': 1}


def test_patterns_route(client):
    res = client.get('/api/location/patterns?days=14')
    assert res.status_code == 200 and res.get_json()['days'] == 14
    assert client.get('/api/location/patterns?days=x').status_code == 400


# --- things recorded without a location ---

def test_fix_near_prefers_a_stay_spanning_the_moment(client, places):
    workday(client)
    assert lookup.fix_near(get_db(), at('13:00')) == OFFICE
    assert lookup.fix_near(get_db(), at('08:45')) is not None
    assert lookup.fix_near(get_db(), at('12:00', '2026-08-01')) is None


def test_a_meal_without_coordinates_takes_the_trail_and_the_saved_place(client, places):
    workday(client)
    res = client.post('/api/food', json={'text': 'lunch', 'capturedAt': captured('12:30')})
    meal = get_db().execute('SELECT * FROM food_entries WHERE id=?', (res.get_json()['id'],)).fetchone()
    assert (meal['latitude'], meal['longitude'], meal['place']) == (*OFFICE, 'Office')


def test_a_meal_place_the_user_typed_is_kept(client, places):
    res = client.post('/api/food', json={'text': 'x', 'place': 'Picnic', 'latitude': HOME[0], 'longitude': HOME[1]})
    assert get_db().execute('SELECT place FROM food_entries WHERE id=?', (res.get_json()['id'],)).fetchone()[0] == 'Picnic'


def test_a_meal_uploaded_before_its_trail_is_located_when_the_trail_lands(client, places):
    res = client.post('/api/food', json={'text': 'dinner', 'capturedAt': captured('19:00')})
    meal_id = res.get_json()['id']
    assert get_db().execute('SELECT place FROM food_entries WHERE id=?', (meal_id,)).fetchone()[0] is None
    workday(client)  # the evening's home stay arrives afterwards
    client.post('/api/location/points', json={'points': [fine('fix-evening1', at('19:05'), *HOME)]})
    assert get_db().execute('SELECT place FROM food_entries WHERE id=?', (meal_id,)).fetchone()[0] == 'Home'


def test_weather_for_an_unlocated_entry_is_looked_up_where_the_trail_was(client, places, monkeypatch):
    from backend.weather import entry
    workday(client)
    db = get_db()
    db.execute("INSERT INTO journal_entries (id, content, created_at, updated_at) VALUES ('J1', 'x', ?, ?)",
               (int(at('13:00')), int(at('13:00'))))
    db.commit()
    asked = []
    monkeypatch.setattr(entry, 'weather_at', lambda lat, lon, ts, **kw: asked.append((lat, lon)) or {'temp': 20})
    entry.sweep(now=at('14:00'))
    assert asked == [OFFICE]


def test_an_open_stay_whose_exit_never_arrived_is_believed_for_a_day_only(client, places):
    client.post('/api/location/points', json={'points': [
        region('region-stale-01', 'PLACE-OFFICE', at('09:00'), None, OFFICE)]})
    assert lookup.fix_near(get_db(), at('18:00')) == OFFICE
    assert lookup.fix_near(get_db(), at('12:00', '2026-07-10')) is None
