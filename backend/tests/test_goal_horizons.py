"""Goal horizons: periods, the pre-horizon migration, and the journal entries
every goal change leaves behind."""
import sqlite3
from datetime import datetime

import pytest

from backend.db import connection
from backend.db.connection import get_db
from backend.goals import periods, service, tools


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    # Wednesday 2026-11-04, noon.
    now = int(datetime(2026, 11, 4, 12).timestamp())
    monkeypatch.setattr(service.time, 'time', lambda: now)
    monkeypatch.setattr('backend.day_boundary.time.time', lambda: now, raising=False)
    return now


def entries(goal_id):
    return get_db().execute(
        'SELECT content, raw_content, created_at FROM journal_entries WHERE goal_id=? ORDER BY created_at, rowid',
        (goal_id,)).fetchall()


# --- periods -----------------------------------------------------------------

@pytest.mark.parametrize('horizon,day,expected', [
    ('week', '2026-11-04', ('2026-11-02', '2026-11-08')),
    ('week', '2026-11-02', ('2026-11-02', '2026-11-08')),
    ('week', '2026-11-08', ('2026-11-02', '2026-11-08')),
    ('week', '2026-12-31', ('2026-12-28', '2027-01-03')),
    ('month', '2026-02-14', ('2026-02-01', '2026-02-28')),
    ('month', '2028-02-14', ('2028-02-01', '2028-02-29')),
    ('month', '2026-12-31', ('2026-12-01', '2026-12-31')),
    ('quarter', '2026-11-04', ('2026-10-01', '2026-12-31')),
    ('quarter', '2026-03-31', ('2026-01-01', '2026-03-31')),
    ('quarter', '2026-04-01', ('2026-04-01', '2026-06-30')),
    ('year', '2026-11-04', ('2026-01-01', '2026-12-31')),
])
def test_period_bounds(horizon, day, expected):
    assert periods.period_bounds(horizon, day) == expected


def test_labels_and_overdue():
    assert periods.label('week', '2026-11-02') == 'Week of Nov 2'
    assert periods.label('month', '2026-11-01') == 'November 2026'
    assert periods.label('quarter', '2026-10-01') == 'Q4 2026'
    assert periods.label('year', '2026-01-01') == '2026'
    assert periods.label('overarching', None) == 'Overarching'
    assert periods.is_over('2026-11-08', '2026-11-09')
    assert not periods.is_over('2026-11-08', '2026-11-08')
    assert not periods.is_over(None, '2030-01-01')


def test_a_late_streak_goal_keeps_its_whole_streak():
    # Seven days from a Friday reach past Sunday; the period stretches.
    assert periods.goal_period('week', '2026-10-16', 7) == ('2026-10-12', '2026-10-22')
    assert periods.goal_period('week', '2026-10-16', None) == ('2026-10-12', '2026-10-18')


# --- service -----------------------------------------------------------------

def test_text_goal_leaves_a_dictated_style_journal_entry_and_is_never_judged(monkeypatch):
    goal = service.create({'id': 'g-week', 'horizon': 'week', 'title': 'Finish the coach plan',
                           'details': 'Phase one, then tests.'})
    assert goal['horizon'] == 'week'
    assert (goal['periodStart'], goal['periodEnd']) == ('2026-11-02', '2026-11-08')
    assert goal['criteria'] is None and goal['targetDays'] is None and not goal['hasRule']
    [entry] = entries('g-week')
    assert entry['raw_content'] == entry['content']
    assert entry['content'].startswith('New goal for the week of Nov 2: Finish the coach plan')
    assert 'Phase one, then tests.' in entry['content']
    monkeypatch.setattr(service.evidence, 'judge', lambda *a: pytest.fail('text goals are not judged'))
    assert service.evaluate('g-week')['status'] == 'active'


def test_create_replay_by_id_writes_one_goal_and_one_entry():
    first = service.create({'id': 'g1', 'horizon': 'month', 'title': 'Read two books'})
    again = service.create({'id': 'g1', 'horizon': 'month', 'title': 'Read two books'})
    assert first['id'] == again['id'] == 'g1'
    assert len(entries('g1')) == 1
    assert get_db().execute('SELECT COUNT(*) FROM goals').fetchone()[0] == 1


def test_criteria_goal_defaults_its_streak_to_the_rest_of_the_period():
    goal = service.create({'horizon': 'week', 'title': 'Stretch', 'criteria': 'Stretch for ten minutes',
                           'reward': 'New mat'})
    assert goal['targetDays'] == 5  # Wednesday through Sunday
    assert 'Daily rule: Stretch for ten minutes (5 days in a row) — reward: New mat.' in entries(goal['id'])[0]['content']


def test_reward_without_a_rule_is_refused():
    with pytest.raises(ValueError, match='criterion'):
        service.create({'horizon': 'week', 'title': 'x', 'reward': 'cake'})


def test_overarching_goal_is_one_at_a_time_and_each_version_is_an_entry():
    first = service.set_overarching('Get healthy and ship Lunaschal.', goal_id='o1')
    assert service.set_overarching('Get healthy and ship Lunaschal.')['id'] == 'o1'  # unchanged
    second = service.set_overarching('Get strong.\nRun a 10k by spring.', goal_id='o2')
    assert service.overarching()['id'] == 'o2'
    assert second['title'] == 'Get strong.'
    assert service.get('o1')['status'] == 'ended' and service.get('o1')['endedAt']
    [entry] = entries('o2')
    assert 'New overarching goal:\n\nGet strong.' in entry['content']
    assert 'It replaces:\n\nGet healthy and ship Lunaschal.' in entry['content']
    with pytest.raises(sqlite3.IntegrityError):
        db = get_db()
        db.execute("UPDATE goals SET ended_at=NULL, status='active' WHERE id='o1'")
    get_db().rollback()
    assert first['id'] == 'o1'


def test_edit_records_the_change_and_rules_are_immutable():
    goal = service.create({'id': 'g', 'horizon': 'year', 'title': 'Learn piano'})
    assert service.edit('g', {'title': 'Learn piano'})['updatedAt'] == goal['updatedAt']  # no-op
    edited = service.edit('g', {'title': 'Play a Chopin nocturne', 'details': 'Op. 9 No. 2'})
    assert edited['title'] == 'Play a Chopin nocturne'
    last = entries('g')[-1]['content']
    assert last.startswith('Changed a goal for 2026: Play a Chopin nocturne (was: Learn piano)')
    assert 'Op. 9 No. 2' in last
    with pytest.raises(ValueError, match='cannot change'):
        service.edit('g', {'criteria': 'Practice daily'})
    assert len(entries('g')) == 2


def test_end_and_cancel_close_a_goal_with_an_entry():
    a = service.create({'id': 'a', 'horizon': 'week', 'title': 'A'})
    b = service.create({'id': 'b', 'horizon': 'week', 'title': 'B'})
    assert service.change_status(a['id'], 'end')['status'] == 'ended'
    assert service.change_status(b['id'], 'cancel')['status'] == 'cancelled'
    assert entries('a')[-1]['content'] == 'Ended the goal for the week of Nov 2: A'
    assert entries('b')[-1]['content'] == 'Dropped the goal for the week of Nov 2: B'
    with pytest.raises(ValueError, match='ended'):
        service.edit('a', {'title': 'A2'})
    # Ending again is a no-op, not a second entry.
    service.change_status('a', 'end')
    assert len(entries('a')) == 2


def test_current_groups_by_horizon_and_flags_unset_and_overdue():
    service.set_overarching('Be well.')
    service.create({'id': 'w', 'horizon': 'week', 'title': 'This week'})
    get_db().execute("INSERT INTO goals (id,horizon,title,start_date,period_start,period_end,created_at,updated_at) "
                     "VALUES ('old','month','Last month','2026-10-01','2026-10-01','2026-10-31',1,1)")
    get_db().commit()
    data = service.current()
    assert data['overarching']['details'] == 'Be well.'
    by = {h['horizon']: h for h in data['horizons']}
    assert [g['id'] for g in by['week']['goals']] == ['w'] and not by['week']['unset']
    assert [g['id'] for g in by['month']['goals']] == ['old']
    assert by['month']['goals'][0]['overdue'] and by['month']['unset']
    assert by['quarter']['unset'] and by['year']['unset']
    assert by['quarter']['label'] == 'Q4 2026'


def test_goal_entries_are_history_but_never_evidence():
    goal = service.create({'id': 'g', 'horizon': 'week', 'title': 'Pushups',
                           'criteria': 'Do pushups', 'reward': 'Cake', 'startDate': '2026-11-04'})
    history = service.history('g')
    assert len(history) == 1 and 'Pushups' in history[0]['content']
    journal, _ = service._sources(goal, '2026-11-04')
    assert journal == []
    with pytest.raises(LookupError):
        service.history('missing')


def test_context_text_reads_overarching_and_horizons():
    assert service.context_text() == ''
    service.set_overarching('Ship it.')
    service.create({'horizon': 'week', 'title': 'Write tests'})
    text = service.context_text()
    assert text.startswith('Overarching goal:\nShip it.')
    assert '- Write tests' in text
    assert 'This month (November 2026): no goals set yet.' in text


# --- routes and tools --------------------------------------------------------

def test_routes(client):
    r = client.post('/api/goals?capturedAt=2026-11-03T09:00:00Z',
                    json={'id': 'r1', 'horizon': 'quarter', 'title': 'Q goal'})
    assert r.status_code == 201 and r.json['periodLabel'] == 'Q4 2026'
    assert entries('r1')[0]['created_at'] == int(datetime.fromisoformat('2026-11-03T09:00:00+00:00').timestamp())
    assert client.patch('/api/goals/r1', json={'title': 'Q goal!'}).json['title'] == 'Q goal!'
    assert client.put('/api/goals/overarching', json={'id': 'o', 'text': 'Big one'}).json['horizon'] == 'overarching'
    assert client.put('/api/goals/overarching', json={}).status_code == 400
    current = client.get('/api/goals/current').json
    assert current['overarching']['id'] == 'o'
    assert len(client.get('/api/goals/r1/history').json) == 2
    assert client.post('/api/goals/r1/end').json['status'] == 'ended'
    assert client.patch('/api/goals/missing', json={'title': 'x'}).status_code == 404
    assert client.post('/api/goals', json={'horizon': 'decade', 'title': 'x'}).status_code == 400


def test_tools_set_and_list(monkeypatch):
    import json
    text, event = tools.run_tool('set_overarching_goal', {'text': 'Be kind to myself.'})
    assert event['ok']
    text, event = tools.run_tool('create_goal', {'horizon': 'month', 'title': 'Cook at home'})
    goal_id = json.loads(text)['id']
    text, event = tools.run_tool('edit_goal', {'goalId': goal_id, 'details': 'Four nights a week'})
    assert json.loads(text)['details'] == 'Four nights a week'
    listed = json.loads(tools.run_tool('list_goals', {})[0])
    assert listed['overarching']['details'] == 'Be kind to myself.'
    month = next(h for h in listed['horizons'] if h['horizon'] == 'month')
    assert month['goals'][0]['title'] == 'Cook at home'
    # The tool cannot pass a rule change through; the service would refuse one too.
    text, event = tools.run_tool('edit_goal', {'goalId': goal_id, 'criteria': 'x'})
    assert event['ok'] and json.loads(text)['criteria'] is None


# --- migration ---------------------------------------------------------------

OLD_GOALS = """
CREATE TABLE goals (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, criteria TEXT NOT NULL, reward TEXT NOT NULL,
    start_date TEXT NOT NULL, target_days INTEGER NOT NULL CHECK(target_days BETWEEN 1 AND 366),
    exercise TEXT, minimum_reps INTEGER,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','earned','redeemed','cancelled','needs_review')),
    earned_at INTEGER, redeemed_at INTEGER, last_checked_at INTEGER,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""


def test_pre_horizon_goals_are_rebuilt_without_losing_receipts(monkeypatch):
    db = get_db()
    db.commit()
    db.execute('PRAGMA foreign_keys=OFF')
    db.execute('DROP TABLE goals')
    db.executescript(OLD_GOALS)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute("INSERT INTO goals (id,title,criteria,reward,start_date,target_days,status,earned_at,redeemed_at,created_at,updated_at) "
               "VALUES ('wk','Read','Read 20 min','Book','2026-10-16',7,'redeemed',5,6,1,1),"
               "       ('mo','Run','Run','Shoes','2026-10-01',30,'active',NULL,NULL,1,1)")
    db.execute("INSERT INTO goal_days (id,goal_id,date,fingerprint,verdict,evidence,checked_at) "
               "VALUES ('d1','wk','2026-10-16','f','met','[]',1)")
    db.execute("UPDATE settings SET briefing_goals='Move more.\nSleep by 11.' WHERE id=1")
    db.commit()

    connection._ensure_goal_horizons(db)
    connection._ensure_goal_horizons(db)  # idempotent

    wk, mo = service.get('wk'), service.get('mo')
    assert (wk['horizon'], wk['periodStart'], wk['periodEnd']) == ('week', '2026-10-12', '2026-10-22')
    assert (wk['status'], wk['earnedAt'] is not None, wk['redeemedAt'] is not None) == ('redeemed', True, True)
    assert [d['date'] for d in wk['days']] == ['2026-10-16']
    assert (mo['horizon'], mo['periodStart'], mo['periodEnd']) == ('month', '2026-10-01', '2026-10-31')
    over = service.overarching()
    assert over['details'] == 'Move more.\nSleep by 11.' and over['title'] == 'Move more.'
    assert db.execute('SELECT briefing_goals FROM settings WHERE id=1').fetchone()[0] is None
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM goals WHERE horizon='overarching'").fetchone()[0] == 1
