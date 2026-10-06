"""The Apple Health mirror: batch ingest, night detection, and how a Watch
night slots between a manual correction and the activity-derived guess."""
import uuid
from datetime import datetime

from backend import sleep
from backend.apple_health import ingest, nights
from backend.db.connection import get_db

DAY = '2026-07-08'
NEXT = '2026-07-09'
AFTER = int(datetime.fromisoformat('2026-07-10T12:00:00').timestamp())
SLEEP = nights.SLEEP_TYPE


def at(date: str, hhmm: str) -> int:
    return int(datetime.fromisoformat(f'{date}T{hhmm}:00').timestamp())


def uid() -> str:
    return str(uuid.uuid4()).upper()


def sample(start, end, value, type_=SLEEP, kind='category', **extra) -> dict:
    return {'uuid': extra.pop('uuid', uid()), 'type': type_, 'kind': kind,
            'start': start, 'end': end, 'value': value, **extra}


def asleep(start, end, stage=3) -> dict:
    return sample(start, end, stage)


def count(table: str) -> int:
    return get_db().execute(f'SELECT COUNT(*) c FROM {table}').fetchone()['c']


# --- ingest ---

def test_a_replayed_batch_changes_nothing():
    batch = {'samples': [
        sample(at(DAY, '09:00'), at(DAY, '09:01'), 72, type_='HKQuantityTypeIdentifierHeartRate',
               kind='quantity', unit='count/min'),
    ]}
    assert ingest.ingest(batch)['samples'] == 1
    assert ingest.ingest(batch)['samples'] == 1
    assert count('health_samples') == 1


def test_a_bad_item_is_skipped_not_a_reason_to_refuse_the_batch():
    # A refused batch would never advance the phone's anchor, so the same bad
    # sample would come back on every sync forever.
    good = sample(at(DAY, '09:00'), at(DAY, '09:01'), 500, type_='HKQuantityTypeIdentifierStepCount',
                  kind='quantity', unit='count')
    result = ingest.ingest({'samples': [
        {'uuid': 'nope', 'type': SLEEP, 'kind': 'category', 'start': 1, 'end': 2},
        sample(at(DAY, '10:00'), at(DAY, '09:00'), 1),           # ends before it starts
        sample(at(DAY, '09:00'), at(DAY, '10:00'), 1, type_='DROP TABLE'),
        sample(at(DAY, '09:00'), at(DAY, '10:00'), float('nan'), type_='HKQuantityTypeIdentifierStepCount',
               kind='quantity', unit='count'),
        sample(at(DAY, '09:00'), at(DAY, '10:00'), 5, type_='HKQuantityTypeIdentifierStepCount',
               kind='quantity'),                                  # quantity without a unit
        good,
    ]})
    assert result['samples'] == 1
    assert result['rejectedCount'] == 5
    assert [r['index'] for r in result['rejected']] == [0, 1, 2, 3, 4]
    assert count('health_samples') == 1


def test_a_malformed_batch_is_refused():
    for body in (None, [], {'samples': 'x'}, {'samples': [{}] * (ingest.MAX_SAMPLES + 1)}):
        try:
            ingest.ingest(body)
        except ingest.BatchError:
            continue
        raise AssertionError(f'accepted {body!r:.40}')


def test_a_deletion_in_health_deletes_the_mirror_row():
    s = sample(at(DAY, '00:00'), at(DAY, '07:00'), 3)
    w = {'uuid': uid(), 'activityType': 37, 'activityName': 'running',
         'start': at(DAY, '18:00'), 'end': at(DAY, '18:30'), 'duration': 1800, 'energy': 300.5}
    ingest.ingest({'samples': [s], 'workouts': [w]})
    assert count('health_samples') == 1 and count('health_workouts') == 1
    result = ingest.ingest({'deleted': [s['uuid'].lower(), w['uuid']]})
    assert result['deleted'] == 2
    assert count('health_samples') == 0 and count('health_workouts') == 0


def test_a_daily_total_is_replaced_not_added_to():
    # The phone recomputes the last few days on every sync, because the Watch
    # can deliver a walk hours after it happened.
    row = {'date': DAY, 'type': 'HKQuantityTypeIdentifierStepCount', 'value': 4000, 'unit': 'count'}
    ingest.ingest({'daily': [row]})
    ingest.ingest({'daily': [{**row, 'value': 9000}]})
    got = get_db().execute('SELECT value FROM health_daily').fetchall()
    assert [r['value'] for r in got] == [9000]


def test_metadata_too_large_is_dropped_whole():
    big = sample(at(DAY, '09:00'), at(DAY, '09:01'), 1, metadata={'x': 'y' * 10000})
    small = sample(at(DAY, '09:00'), at(DAY, '09:01'), 1, metadata={'HKTimeZone': 'America/Toronto'})
    ingest.ingest({'samples': [big, small]})
    rows = {r['id']: r['metadata'] for r in get_db().execute('SELECT id, metadata FROM health_samples')}
    assert rows[big['uuid']] is None
    assert rows[small['uuid']] == '{"HKTimeZone":"America/Toronto"}'


# --- picking the night ---

def test_short_wakes_do_not_split_a_night():
    boundary = at(NEXT, '04:00')
    samples = [(at(DAY, '23:30'), at(NEXT, '03:00'), 3),
               (at(NEXT, '03:00'), at(NEXT, '03:10'), nights.AWAKE),
               (at(NEXT, '03:10'), at(NEXT, '07:15'), 4)]
    assert nights.pick_night(samples, boundary) == (at(DAY, '23:30'), at(NEXT, '07:15'))


def test_an_afternoon_nap_is_not_a_night():
    # Exactly the night the Watch wasn't worn: the nap is the only candidate.
    boundary = at(NEXT, '04:00')
    assert nights.pick_night([(at(NEXT, '14:00'), at(NEXT, '16:00'), 1)], boundary) is None


def test_the_longest_qualifying_session_wins():
    boundary = at(NEXT, '04:00')
    samples = [(at(DAY, '21:00'), at(DAY, '22:45'), 1),      # dozed on the couch
               (at(NEXT, '00:30'), at(NEXT, '08:00'), 1)]
    assert nights.pick_night(samples, boundary) == (at(NEXT, '00:30'), at(NEXT, '08:00'))


def test_in_bed_is_only_a_fallback_for_a_night_with_no_stages():
    boundary = at(NEXT, '04:00')
    bed = (at(DAY, '22:00'), at(NEXT, '08:00'), nights.IN_BED)
    assert nights.pick_night([bed], boundary) == (at(DAY, '22:00'), at(NEXT, '08:00'))
    staged = [bed, (at(DAY, '23:15'), at(NEXT, '07:30'), 1)]
    assert nights.pick_night(staged, boundary) == (at(DAY, '23:15'), at(NEXT, '07:30'))


def test_overlapping_sources_are_not_counted_twice():
    # Phone and Watch both wrote 01:00-02:00; that hour of sleep is one hour.
    merged = nights.merge([(at(NEXT, '01:00'), at(NEXT, '02:00')), (at(NEXT, '01:00'), at(NEXT, '02:00'))])
    assert merged == [(at(NEXT, '01:00'), at(NEXT, '02:00'), 3600.0)]


# --- precedence in the day's wake/sleep ---

def journal(ts: int) -> None:
    get_db().execute('INSERT INTO journal_entries(id, content, created_at, updated_at) VALUES (?,?,?,?)',
                     (uid(), 'note', ts, ts))
    get_db().commit()


def test_a_watch_night_beats_the_activity_guess():
    journal(at(DAY, '09:00'))
    journal(at(DAY, '23:50'))
    ingest.ingest({'samples': [
        asleep(at('2026-07-07', '23:40'), at(DAY, '07:20')),   # the night DAY began with
        asleep(at(NEXT, '00:20'), at(NEXT, '07:45')),          # the night DAY ended with
    ]})
    day = sleep.resolve_day(DAY, now=AFTER)
    assert (day['wakeAt'], day['wakeSource']) == (at(DAY, '07:20'), 'health')
    assert (day['sleepAt'], day['sleepSource']) == (at(NEXT, '00:20'), 'health')


def test_a_manual_correction_still_wins_and_each_end_is_independent():
    ingest.ingest({'samples': [
        asleep(at('2026-07-07', '23:40'), at(DAY, '07:20')),
        asleep(at(NEXT, '00:20'), at(NEXT, '07:45')),
    ]})
    sleep.set_day(DAY, wake=None, sleep=at(NEXT, '01:30'))
    day = sleep.resolve_day(DAY, now=AFTER)
    assert (day['wakeAt'], day['wakeSource']) == (at(DAY, '07:20'), 'health')
    assert (day['sleepAt'], day['sleepSource']) == (at(NEXT, '01:30'), 'manual')


def test_a_night_without_the_watch_falls_back_to_activity():
    journal(at(DAY, '09:00'))
    journal(at(DAY, '23:50'))
    ingest.ingest({'samples': [asleep(at('2026-07-07', '23:40'), at(DAY, '07:20'))]})
    day = sleep.resolve_day(DAY, now=AFTER)
    assert day['wakeSource'] == 'health'
    assert (day['sleepAt'], day['sleepSource']) == (at(DAY, '23:50'), 'auto')


# --- routes ---

def test_sync_route_and_activity_card(client):
    today = '2026-07-08'
    body = {
        'daily': [
            {'date': today, 'type': 'HKQuantityTypeIdentifierAppleExerciseTime', 'value': 42, 'unit': 'min'},
            {'date': today, 'type': 'HKQuantityTypeIdentifierStepCount', 'value': 8100, 'unit': 'count'},
        ],
        'workouts': [{'uuid': uid(), 'activityType': 52, 'activityName': 'walking',
                      'start': at(today, '12:00'), 'end': at(today, '12:40'), 'duration': 2400}],
    }
    res = client.post('/api/apple-health/sync', json=body)
    assert res.status_code == 200
    assert res.get_json()['daily'] == 2
    assert client.post('/api/apple-health/sync', json={'samples': 3}).status_code == 400

    from backend.apple_health import queries
    card = queries.activity(today, 7)
    assert len(card['days']) == 7
    assert card['days'][-1] == {'date': today, 'exerciseMinutes': 42, 'steps': 8100, 'activeEnergyKcal': None}
    assert card['days'][0]['exerciseMinutes'] is None
    assert [w['activityName'] for w in card['workouts']] == ['walking']
    assert card['lastSyncedAt'] is not None


def test_samples_types_and_csv_export(client):
    hr = 'HKQuantityTypeIdentifierHeartRate'
    client.post('/api/apple-health/sync', json={'samples': [
        sample(at(DAY, '09:00'), at(DAY, '09:00'), 61, type_=hr, kind='quantity', unit='count/min'),
        sample(at(DAY, '10:00'), at(DAY, '10:00'), 88, type_=hr, kind='quantity', unit='count/min'),
    ]})
    types = client.get('/api/apple-health/types').get_json()
    assert types == [{'type': hr, 'kind': 'quantity', 'unit': 'count/min', 'count': 2,
                      'first': at(DAY, '09:00'), 'last': at(DAY, '10:00')}]
    rows = client.get(f'/api/apple-health/samples?type={hr}&from={DAY}&to={DAY}').get_json()
    assert [r['value'] for r in rows] == [61, 88]
    assert client.get('/api/apple-health/samples?type=bad').status_code == 400
    csv = client.get(f'/api/apple-health/export.csv?type={hr}&from={DAY}&to={DAY}')
    assert csv.mimetype == 'text/csv'
    lines = csv.get_data(as_text=True).strip().splitlines()
    assert lines[0].startswith('id,start,end,value') and len(lines) == 3


def test_calendar_sleep_route_reports_the_health_source(client):
    ingest.ingest({'samples': [asleep(at('2026-07-07', '23:40'), at(DAY, '07:20'))]})
    payload = client.get(f'/api/calendar/sleep/{DAY}').get_json()
    assert payload['wakeSource'] == 'health'
    assert payload['wakeAt'] == at(DAY, '07:20')
