"""Work / leisure / mix: switch validation and replay, the spans a day is drawn
from, work that overlaps reading turning to mix, and what the coach reads."""
import pytest
from ulid import ULID

from backend import activity_state, sleep
from backend.activity_state import clip, parse_switch, spans, totals, with_reading
from backend.db.connection import get_db
from backend.day_boundary import day_bounds
from backend.tests.test_fanfic_routes import fanfic_root, make_fic  # noqa: F401

DAY = '2026-07-13'
START, END = day_bounds(DAY)
H = 3600


def body(**overrides):
    return {'id': str(ULID()), 'state': 'work', 'at': START + 5 * H, 'source': 'watch', **overrides}


# --- parse_switch -------------------------------------------------------------

def test_a_switch_is_parsed_and_its_id_uppercased():
    ulid = str(ULID())
    row = parse_switch(body(id=ulid.lower()), START + 6 * H)
    assert row == {'id': ulid, 'state': 'work', 'at': START + 5 * H, 'source': 'watch'}


def test_source_defaults_to_the_phone():
    b = body()
    del b['source']
    assert parse_switch(b, END)['source'] == 'phone'


@pytest.mark.parametrize('overrides', [
    {'id': 'nope'}, {'id': None}, {'state': 'nap'}, {'at': 'noon'}, {'at': True},
    {'at': START + 8 * H}, {'source': 'ipad'},
])
def test_bad_switches_are_refused(overrides):
    with pytest.raises(ValueError):
        parse_switch(body(**overrides), START + 6 * H)


def test_a_switch_within_the_watch_clock_slack_is_accepted():
    parse_switch(body(at=START + 6 * H + 200), START + 6 * H)


# --- spans --------------------------------------------------------------------

def test_the_day_opens_in_yesterdays_last_state_and_the_open_state_runs_to_now():
    switches = [('a', 'leisure', START - 2 * H), ('b', 'work', START + 5 * H),
                ('c', 'mix', START + 9 * H)]
    assert spans(switches, START, END, START + 10 * H) == [
        {'id': 'a', 'state': 'leisure', 'startedAt': START, 'endedAt': START + 5 * H},
        {'id': 'b', 'state': 'work', 'startedAt': START + 5 * H, 'endedAt': START + 9 * H},
        {'id': 'c', 'state': 'mix', 'startedAt': START + 9 * H, 'endedAt': START + 10 * H},
    ]


def test_nothing_before_the_first_switch_and_a_finished_day_ends_at_4am():
    assert spans([('b', 'work', START + 5 * H)], START, END, END + 9 * H) == [
        {'id': 'b', 'state': 'work', 'startedAt': START + 5 * H, 'endedAt': END}]


def test_switching_to_the_same_state_does_not_split_the_span():
    switches = [('a', 'work', START + H), ('b', 'work', START + 2 * H)]
    assert spans(switches, START, END, START + 3 * H) == [
        {'id': 'a', 'state': 'work', 'startedAt': START + H, 'endedAt': START + 3 * H}]


def test_a_switch_that_arrives_late_still_sorts_by_when_it_was_made():
    late_from_watch = [('c', 'leisure', START + 3 * H), ('b', 'work', START + H)]
    assert [s['state'] for s in spans(late_from_watch, START, END, START + 4 * H)] == ['work', 'leisure']


def test_a_day_in_the_future_has_no_spans():
    assert spans([('a', 'work', START - H)], START, END, START - 1) == []


# --- with_reading -------------------------------------------------------------

def test_work_overlapping_reading_becomes_mix_for_that_stretch_only():
    work = [{'id': 'w', 'state': 'work', 'startedAt': START + 5 * H, 'endedAt': START + 9 * H}]
    reading = [(START + 6 * H, START + 7 * H), (START + 6 * H + 1800, START + 7 * H + 1800)]
    assert with_reading(work, reading) == [
        {'id': 'w', 'state': 'work', 'startedAt': START + 5 * H, 'endedAt': START + 6 * H},
        {'id': f'w@{START + 6 * H}', 'state': 'mix', 'startedAt': START + 6 * H,
         'endedAt': START + 7 * H + 1800},
        {'id': f'w@{START + 7 * H + 1800}', 'state': 'work', 'startedAt': START + 7 * H + 1800,
         'endedAt': START + 9 * H},
    ]


def test_reading_during_leisure_stays_leisure_and_mix_joins_a_neighbouring_mix():
    day = [{'id': 'w', 'state': 'work', 'startedAt': START + 5 * H, 'endedAt': START + 6 * H},
           {'id': 'm', 'state': 'mix', 'startedAt': START + 6 * H, 'endedAt': START + 7 * H},
           {'id': 'l', 'state': 'leisure', 'startedAt': START + 7 * H, 'endedAt': START + 9 * H}]
    reading = [(START + 5 * H + 1800, START + 6 * H), (START + 8 * H, START + 8 * H + 600)]
    out = with_reading(day, reading)
    assert [(s['state'], s['startedAt'] - START, s['endedAt'] - START) for s in out] == [
        ('work', 5 * H, 5 * H + 1800), ('mix', 5 * H + 1800, 7 * H), ('leisure', 7 * H, 9 * H)]


def test_clip_trims_to_the_waking_day_and_leaves_an_unknown_end_alone():
    day = [{'id': 'l', 'state': 'leisure', 'startedAt': START, 'endedAt': END}]
    assert clip(day, START + 3 * H, None) == [{**day[0], 'startedAt': START + 3 * H}]
    assert clip(day, END, None) == []


def test_totals_name_every_state():
    day = [{'id': 'w', 'state': 'work', 'startedAt': 0, 'endedAt': 90}]
    assert totals(day) == {'work': 90, 'leisure': 0, 'mix': 0}


# --- routes and the day read --------------------------------------------------

def post(client, **overrides):
    return client.post('/api/activity-state/switches', json=body(**overrides))


def test_a_replayed_switch_returns_the_stored_row_and_changes_nothing(client):
    ulid = str(ULID())
    first = post(client, id=ulid, state='work')
    assert first.status_code == 201
    replay = post(client, id=ulid, state='leisure')
    assert replay.status_code == 201
    assert replay.get_json() == first.get_json() == {
        'id': ulid, 'state': 'work', 'at': START + 5 * H, 'source': 'watch'}
    assert get_db().execute('SELECT COUNT(*) FROM activity_states').fetchone()[0] == 1


def test_a_bad_switch_is_a_400(client):
    assert post(client, state='nap').status_code == 400


def test_current_is_the_latest_made_not_the_latest_arrived(client):
    assert client.get('/api/activity-state/current').get_json() is None
    post(client, state='mix', at=START + 9 * H)
    post(client, state='work', at=START + 5 * H)  # the Watch's, delivered late
    assert client.get('/api/activity-state/current').get_json()['state'] == 'mix'


def test_the_calendar_rail_folds_in_reading_and_stops_at_bedtime(client, fanfic_root):
    sleep.set_day(DAY, wake=START + 4 * H, sleep=START + 18 * H)
    post(client, state='leisure', at=START - 3 * H)   # last night: opens the day
    post(client, state='work', at=START + 5 * H)
    fic_id, (ch,) = make_fic(chapters=[('One', 'text')])
    client.put(f'/api/fanfic/{fic_id}/reading-spans/s1', json={
        'chapterId': ch, 'startedAt': START + 6 * H, 'endedAt': START + 6 * H + 1200,
        'activeSeconds': 1100, 'startFraction': 0, 'endFraction': 0.5})
    rail = client.get(f'/api/calendar/activity/{DAY}').get_json()
    assert [(s['state'], s['startedAt'] - START, s['endedAt'] - START) for s in rail] == [
        ('leisure', 4 * H, 5 * H), ('work', 5 * H, 6 * H),
        ('mix', 6 * H, 6 * H + 1200), ('work', 6 * H + 1200, 18 * H)]
    assert activity_state.day_minutes(get_db(), DAY) == {'work': 760, 'leisure': 60, 'mix': 20}


def test_a_day_nothing_was_switched_has_no_rail_and_no_minutes(client):
    assert client.get(f'/api/calendar/activity/{DAY}').get_json() == []
    assert activity_state.day_minutes(get_db(), DAY) is None
    assert client.get('/api/calendar/activity/nope').status_code == 400
