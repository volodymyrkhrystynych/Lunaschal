"""The Watch's pomodoro runs: validation, replay, 4am bucketing, the summary."""
import time

import pytest
from ulid import ULID

from backend.day_boundary import day_bounds, day_key_for
from backend.lifestyle.pomodoro import parse_session, summarize

NOW = day_bounds('2026-07-20')[0] + 8 * 3600  # 12:00 on the 20th


def body(**overrides):
    start = overrides.pop('start', NOW - 1500)
    out = {'id': str(ULID()), 'kind': 'work', 'startedAt': start, 'endedAt': start + 1500,
           'plannedSeconds': 1500, 'completed': True}
    out.update(overrides)
    return out


def test_a_run_is_filed_under_the_4am_day_it_started_in():
    late = day_bounds('2026-07-20')[1] - 1800  # 03:30 on the 21st's calendar date
    row = parse_session(body(start=late), NOW + 86400)
    assert row['date'] == '2026-07-20'
    assert row['completed'] == 1


def test_the_id_is_uppercased_so_a_replay_matches():
    ulid = str(ULID())
    assert parse_session(body(id=ulid.lower()), NOW)['id'] == ulid


@pytest.mark.parametrize('overrides', [
    {'id': 'not-a-ulid'},
    {'id': None},
    {'kind': 'nap'},
    {'startedAt': 'noon'},
    {'completed': 'yes'},
    {'plannedSeconds': 0},
    {'endedAt': NOW - 3000},
    {'endedAt': NOW + 5 * 3600},
    {'start': NOW + 3600},
])
def test_bad_runs_are_refused(overrides):
    with pytest.raises(ValueError):
        parse_session(body(**overrides), NOW)


def test_the_summary_keeps_empty_days_and_counts_only_finished_work_blocks():
    def row(kind, day, minutes, completed=True):
        start = day_bounds(day)[0] + 3600
        return {'kind': kind, 'date': day, 'started_at': start,
                'ended_at': start + minutes * 60, 'completed': completed}

    rows = [row('work', '2026-07-18', 25), row('break', '2026-07-18', 5),
            row('work', '2026-07-18', 12, completed=False), row('timeout', '2026-07-20', 10),
            row('work', '2026-07-01', 25)]  # outside the window
    days = summarize(rows, '2026-07-18', '2026-07-20')
    assert [d['date'] for d in days] == ['2026-07-18', '2026-07-19', '2026-07-20']
    assert days[0] == {'date': '2026-07-18', 'focusMinutes': 37, 'breakMinutes': 5,
                       'timeoutMinutes': 0, 'completedBlocks': 1}
    assert days[1]['focusMinutes'] == 0
    assert days[2]['timeoutMinutes'] == 10


def test_routes_store_replay_summarize_and_delete(client):
    start = int(time.time()) - 1600
    run = body(start=start)
    first = client.post('/api/lifestyle/pomodoro/sessions', json=run)
    assert first.status_code == 201
    stored = first.get_json()
    assert stored['id'] == run['id'] and stored['completed'] is True
    assert stored['date'] == day_key_for(start)

    # A replay from the outbox returns the same row and adds nothing.
    again = client.post('/api/lifestyle/pomodoro/sessions', json={**run, 'kind': 'timeout'})
    assert again.status_code == 201 and again.get_json()['kind'] == 'work'

    summary = client.get('/api/lifestyle/pomodoro?days=7').get_json()
    assert len(summary['days']) == 7
    assert summary['days'][-1]['date'] == day_key_for()
    assert sum(d['completedBlocks'] for d in summary['days']) == 1
    assert [s['id'] for s in summary['sessions']] == [run['id']]

    assert client.delete(f"/api/lifestyle/pomodoro/sessions/{run['id']}").status_code == 200
    assert client.delete(f"/api/lifestyle/pomodoro/sessions/{run['id']}").status_code == 404
    assert client.get('/api/lifestyle/pomodoro').get_json()['sessions'] == []


def test_routes_refuse_a_bad_run(client):
    res = client.post('/api/lifestyle/pomodoro/sessions', json=body(kind='nap'))
    assert res.status_code == 400
    assert 'kind' in res.get_json()['error']
    assert client.get('/api/lifestyle/pomodoro?days=0').status_code == 400
