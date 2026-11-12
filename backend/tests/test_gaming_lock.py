"""The gaming lock: derived state, the end-of-work card, the override, its
mark on the day's review, and the loop that closes launchers
(backend/gaming/, backend/coach/tools.py's end_workday)."""
import json
import os
import signal
from datetime import datetime

import pytest
from ulid import ULID

from backend.coach import review, tools
from backend.day_boundary import day_key_for
from backend.db.connection import get_db
from backend.gaming import enforcer, lock, processes

# Wednesday 2026-11-11 at 18:00.
NOW = int(datetime(2026, 11, 11, 18).timestamp())
DAY = '2026-11-11'
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]


def enable(days=None):
    lock.update_settings(get_db(), {'enabled': True, **({'days': days} if days is not None else {})})


def habit(title):
    task_id = str(ULID())
    created = int(datetime(2026, 11, 1, 6).timestamp())
    get_db().execute('INSERT INTO daily_tasks (id,title,position,created_at,updated_at) VALUES (?,?,?,?,?)',
                     (task_id, title, 1, created, created))
    get_db().commit()
    return task_id


def tick_habit(task_id, day=DAY):
    get_db().execute('INSERT INTO daily_task_completions (id,task_id,date,created_at) VALUES (?,?,?,?)',
                     (str(ULID()), task_id, day, NOW))
    get_db().commit()


# --- state -------------------------------------------------------------------

def test_off_by_default_and_never_locked():
    db = get_db()
    assert lock.settings(db)['enabled'] is False
    assert lock.settings(db)['days'] == lock.DEFAULT_DAYS
    assert lock.locked(db, NOW) is False


def test_locked_on_a_lock_weekday_until_unlocked_and_relocks_at_4am():
    db = get_db()
    enable()
    assert lock.locked(db, NOW) is True
    lock.unlock(db, DAY, 'review', rating=4, now=NOW)
    assert lock.locked(db, NOW) is False
    # 02:00 on Thursday is still Wednesday's 4am day...
    assert lock.locked(db, int(datetime(2026, 11, 12, 2).timestamp())) is False
    # ...and 04:30 is Thursday's, which nobody has unlocked.
    assert lock.locked(db, int(datetime(2026, 11, 12, 4, 30).timestamp())) is True


def test_weekend_is_not_locked_with_default_days():
    enable()
    saturday = int(datetime(2026, 11, 14, 18).timestamp())
    assert lock.locked(get_db(), saturday) is False
    assert lock.status(get_db(), saturday)['applies'] is False


def test_settings_validation():
    db = get_db()
    with pytest.raises(ValueError):
        lock.update_settings(db, {'enabled': 1})
    with pytest.raises(ValueError):
        lock.update_settings(db, {'days': [7]})
    with pytest.raises(ValueError):
        lock.update_settings(db, {'days': [True]})
    with pytest.raises(ValueError):
        lock.update_settings(db, {'nope': 1})
    s = lock.update_settings(db, {'days': [4, 0, 0], 'patterns': [' steam ', '', 'lutris']})
    assert s['days'] == [0, 4] and s['patterns'] == ['steam', 'lutris']


def test_override_needs_a_reason_and_the_first_unlock_wins():
    db = get_db()
    enable()
    with pytest.raises(ValueError):
        lock.unlock(db, DAY, 'override', reason='bored', now=NOW)
    row = lock.unlock(db, DAY, 'override', reason='Friends online for a raid', now=NOW)
    again = lock.unlock(db, DAY, 'review', rating=5, now=NOW)
    assert again['id'] == row['id'] and again['method'] == 'override'


def test_status_reports_work_end_from_the_calendar():
    db = get_db()
    db.execute("INSERT INTO calendar_events (id,title,date,time,end_time,tags,created_at) "
               "VALUES (?,?,?,?,?,?,?)", (str(ULID()), 'Work', DAY, '09:00', '17:30', '["work"]', NOW))
    db.commit()
    enable()
    assert lock.status(db, NOW)['workEnd'] == '17:30'
    row = lock.unlock(db, DAY, 'review', rating=3, now=NOW)
    assert row['work_end'] == '17:30'


# --- the day review ------------------------------------------------------------

def test_override_is_a_missed_item_and_a_review_unlock_is_nothing():
    db = get_db()
    enable(ALL_DAYS)
    tick_habit(habit('Stretch'))
    before = review.draft(DAY, NOW)
    assert before['rating'] == 5
    lock.lift(db, 'review', now=NOW)
    assert review.draft(DAY, NOW)['rating'] == 5
    assert not [i for i in review.get(DAY)['items'] if i['kind'] == 'gaming']


def test_override_lowers_the_rating():
    db = get_db()
    enable(ALL_DAYS)
    tick_habit(habit('Stretch'))
    state = lock.lift(db, 'override', reason='Friends online for a raid', now=NOW)
    assert state['locked'] is False and state['method'] == 'override'
    r = review.get(DAY)
    [item] = [i for i in r['items'] if i['kind'] == 'gaming']
    assert item['done'] is False and item['note'] == 'Friends online for a raid'
    assert r['rating'] == 3  # 1 of 2


def test_lift_refuses_on_a_day_the_lock_does_not_cover():
    with pytest.raises(ValueError):
        lock.lift(get_db(), 'review', now=NOW)


# --- the chat tool and its card --------------------------------------------------

def test_end_workday_quotes_the_rating_and_stages_the_card():
    enable(ALL_DAYS)
    habit('Stretch')
    tick_habit(habit('Read'), day=day_key_for())
    text, event = tools.run_tool('end_workday', {})
    result = json.loads(text)
    assert result['review']['rating'] == 3
    assert result['openItems'] == ['Stretch']
    proposal = event['proposal']
    assert proposal['kind'] == 'unlock_gaming'
    assert proposal['data']['rating'] == 3 and proposal['data']['pauseInference'] is True
    assert 'Nothing is unlocked' in result['gaming']


def test_end_workday_stages_nothing_when_unlocked_or_off():
    _, event = tools.run_tool('end_workday', {})
    assert 'proposal' not in event
    enable(ALL_DAYS)
    lock.lift(get_db(), 'review')
    text, event = tools.run_tool('end_workday', {})
    assert 'proposal' not in event and 'already unlocked' in json.loads(text)['gaming']


def _card_message(data):
    db = get_db()
    conv, msg, pid = str(ULID()), str(ULID()), str(ULID())
    db.execute('INSERT INTO conversations (id,title,created_at,updated_at) VALUES (?,?,?,?)', (conv, 'x', NOW, NOW))
    db.execute("INSERT INTO messages (id,conversation_id,role,content,metadata,created_at) "
               "VALUES (?,?,'assistant','',?,?)",
               (msg, conv, json.dumps({'proposals': [{'id': pid, 'status': 'pending', 'kind': 'unlock_gaming',
                                                       'data': data}]}), NOW))
    db.commit()
    return msg, pid


def test_accepting_the_card_unlocks_and_pauses_inference(client, monkeypatch):
    from backend.routes import settings as settings_routes
    paused = []
    monkeypatch.setattr(settings_routes, 'pause_inference_now', lambda: paused.append(1) or {'paused': True})
    enable(ALL_DAYS)
    msg, pid = _card_message({'day': day_key_for(), 'rating': 4, 'pauseInference': True})
    r = client.post(f'/api/chat/proposals/{msg}/{pid}', json={'action': 'accept'})
    assert r.status_code == 200, r.json
    assert paused == [1]
    row = lock.unlock_row(get_db(), day_key_for())
    assert row['method'] == 'review'
    assert client.get('/api/gaming').json['locked'] is False


def test_accepting_without_pause_leaves_inference_alone(client, monkeypatch):
    from backend.routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, 'pause_inference_now', lambda: pytest.fail('should not pause'))
    enable(ALL_DAYS)
    msg, pid = _card_message({'day': day_key_for(), 'pauseInference': False})
    assert client.post(f'/api/chat/proposals/{msg}/{pid}', json={'action': 'accept'}).status_code == 200


def test_yesterdays_card_cannot_unlock_today(client):
    enable(ALL_DAYS)
    msg, pid = _card_message({'day': '2000-01-01', 'pauseInference': False})
    r = client.post(f'/api/chat/proposals/{msg}/{pid}', json={'action': 'accept'})
    assert r.status_code == 400
    assert client.get('/api/gaming').json['locked'] is True


# --- routes ----------------------------------------------------------------------

def test_routes_settings_and_override(client):
    r = client.patch('/api/gaming/settings', json={'enabled': True, 'days': ALL_DAYS})
    assert r.status_code == 200 and r.json['locked'] is True
    assert client.patch('/api/gaming/settings', json={'days': 'weekdays'}).status_code == 400
    assert client.post('/api/gaming/override', json={'reason': 'meh'}).status_code == 400
    assert client.post('/api/gaming/override', json={}).status_code == 400
    r = client.post('/api/gaming/override', json={'reason': 'Friends online for a raid'})
    assert r.status_code == 200 and r.json['method'] == 'override' and r.json['locked'] is False


# --- processes ---------------------------------------------------------------------

def fake_proc(tmp_path, procs):
    root = tmp_path / 'proc'
    root.mkdir()
    (root / 'self').mkdir()
    for pid, comm, cmdline in procs:
        d = root / str(pid)
        d.mkdir()
        (d / 'comm').write_text(comm + '\n')
        (d / 'cmdline').write_bytes('\0'.join(cmdline).encode() + b'\0')
    return root


def test_find_matches_names_paths_and_wine(tmp_path):
    root = fake_proc(tmp_path, [
        (10, 'steam', ['/home/u/.steam/steam/ubuntu12_32/steam', '-silent']),
        (11, 'steamwebhelper', ['./steamwebhelper']),
        (12, 'bash', ['bash']),
        (13, 'Battle.net Laun', ['C:\\Program Files\\Battle.net\\Battle.net Launcher.exe']),
        (14, 'wineserver', ['/usr/bin/wineserver']),
        (15, 'GameThread', ['/home/u/.steam/steam/steamapps/common/Hades/Hades']),
        (16, 'python3', ['python3', 'main.py']),
    ])
    found = dict(processes.find(lock.DEFAULT_PATTERNS, proc=root, uid=os.getuid(), self_pid=16))
    assert found == {10: 'steam', 11: 'steamwebhelper', 13: 'Battle.net Launcher.exe',
                     15: 'steam game', 14: 'wineserver'}


def test_wineserver_is_left_alone_without_a_wine_launcher(tmp_path):
    root = fake_proc(tmp_path, [(14, 'wineserver', ['/usr/bin/wineserver']),
                                (10, 'steam', ['/usr/bin/steam'])])
    assert dict(processes.find(lock.DEFAULT_PATTERNS, proc=root, uid=os.getuid())) == {10: 'steam'}


def test_other_users_processes_are_never_matched(tmp_path):
    root = fake_proc(tmp_path, [(10, 'steam', ['/usr/bin/steam'])])
    assert processes.find(lock.DEFAULT_PATTERNS, proc=root, uid=os.getuid() + 1) == []


def test_close_terms_then_kills_survivors():
    sent = []
    signalled = processes.close([1, 2, 3], kill=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
                                if pid == 3 else sent.append((pid, sig)),
                                sleep=lambda s: None, alive=lambda pid: pid == 2)
    assert signalled == [1, 2]
    assert sent == [(1, signal.SIGTERM), (2, signal.SIGTERM), (2, signal.SIGKILL)]


# --- the loop ----------------------------------------------------------------------

def test_tick_closes_only_while_locked_and_announces_once_a_day(tmp_path, monkeypatch):
    from backend.coach import deliver
    pushes = []
    monkeypatch.setattr(deliver, 'push', lambda *a, **kw: pushes.append(kw) or {'pushed': 0, 'errors': []})
    root = fake_proc(tmp_path, [(10, 'steam', ['/usr/bin/steam'])])
    closed = []

    def close(pids):
        closed.append(pids)
        return pids

    assert enforcer.tick(NOW, proc=root, close=close) == []  # lock off
    enable()
    assert enforcer.tick(NOW, proc=root, close=close) == [(10, 'steam')]
    enforcer.tick(NOW + 60, proc=root, close=close)
    assert closed == [[10], [10]]
    assert len(pushes) == 1
    messages = get_db().execute("SELECT content FROM messages WHERE role='assistant'").fetchall()
    assert len(messages) == 1 and 'closed steam' in messages[0]['content']
    lock.unlock(get_db(), DAY, 'review', now=NOW)
    assert enforcer.tick(NOW + 120, proc=root, close=close) == []
