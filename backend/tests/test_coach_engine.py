"""The reminder engine (backend/coach/bandit.py, policy.py, rewards.py, engine.py)."""
import json
import random
from datetime import datetime

import pytest
from ulid import ULID

from backend.coach import bandit, config, engine, policy, rewards, scheduler
from backend.db.connection import get_db

DAY = '2026-11-03'


def ts(hour, minute=0, day=DAY):
    return int(datetime.fromisoformat(day).replace(hour=hour, minute=minute).timestamp())


def row(behavior='weight', arm='plain', status='sent', reward=1.0, day=DAY, slot='09:00', **kw):
    return {'behavior': behavior, 'arm': arm, 'status': status, 'reward': reward, 'day': day,
            'slot': slot, 'excused': 0, 'mode': 'policy', **kw}


# --- bandit ------------------------------------------------------------------

def test_posteriors_count_sent_by_arm_and_held_as_no_send():
    post = bandit.posteriors([row(), row(reward=0.0), row(status='held', arm='none', reward=1.0),
                              row(reward=None), row(excused=1), row(mode='snooze')], DAY)['weight']
    assert (post['arms']['plain'].alpha, post['arms']['plain'].beta) == (2.0, 2.0)
    assert post['arms']['none'].alpha == 2.0
    assert set(post['slots']) == {'09:00'} and post['slots']['09:00'].n == 2


def test_old_evidence_is_discounted():
    old = bandit.posteriors([row(day='2026-08-05')], DAY)['weight']['arms']['plain']
    new = bandit.posteriors([row()], DAY)['weight']['arms']['plain']
    assert old.n < 0.2 < new.n == 1.0


def test_the_no_send_control_holds_its_share():
    rng = random.Random(7)
    picks = [bandit.choose_arm(None, rng, 0.25) for _ in range(10_000)]
    control = sum(1 for arm, mode in picks if mode == 'control') / len(picks)
    assert abs(control - 0.25) < 0.03
    assert all(arm == 'none' for arm, mode in picks if mode == 'control')


def test_it_converges_on_the_framing_that_works():
    rng = random.Random(1)
    truth = {'plain': 0.3, 'goal': 0.3, 'identity': 0.8, 'streak': 0.3, 'tiny': 0.3}
    rows = []
    for _ in range(400):
        post = bandit.posteriors(rows, DAY).get('weight')
        arm, mode = bandit.choose_arm(post, rng, 0.0)
        if arm != 'none':
            rows.append(row(arm=arm, reward=float(rng.random() < truth[arm])))
    late = [r['arm'] for r in rows[-100:]]
    assert late.count('identity') > 70


def test_a_behaviour_that_happens_anyway_stops_being_sent():
    rng = random.Random(3)
    rows = [row(status='held', arm='none', reward=1.0) for _ in range(40)]
    rows += [row(arm=a, reward=1.0) for a in bandit.ARMS for _ in range(2)]
    post = bandit.posteriors(rows, DAY)['weight']
    held = sum(1 for _ in range(500) if bandit.choose_arm(post, rng, 0.0)[0] == 'none')
    assert held > 50


def test_slot_choice_follows_what_worked():
    rows = [row(slot='07:30', reward=0.0) for _ in range(20)] + [row(slot='09:00', reward=1.0) for _ in range(20)]
    post = bandit.posteriors(rows, DAY)['weight']
    rng = random.Random(2)
    assert sum(bandit.choose_slot(post, ['07:30', '09:00'], rng) == '09:00' for _ in range(200)) > 190


# --- policy and rewards -------------------------------------------------------

@pytest.mark.parametrize('hour,start,end,quiet', [
    (23, 22, 8, True), (3, 22, 8, True), (8, 22, 8, False), (12, 22, 8, False),
    (14, 13, 15, True), (15, 13, 15, False), (10, 9, 9, False),
])
def test_quiet_hours_wrap_midnight(hour, start, end, quiet):
    assert policy.in_quiet_hours(hour, start, end) is quiet


def test_blocking_rules_in_order():
    rng = random.Random(0)
    base = dict(behavior='habit:x', hour=12, quiet=(22, 8), sent_today=0, cap=3, done=False, unprompted_run=0, rng=rng)
    assert policy.blocked(**base) is None
    assert policy.blocked(**base | {'done': True}) == 'skipped_logged'
    assert policy.blocked(**base | {'hour': 23}) == 'skipped_quiet'
    assert policy.blocked(**base | {'sent_today': 3}) == 'skipped_cap'
    faded = [policy.blocked(**base | {'unprompted_run': 6, 'rng': random.Random(i)}) for i in range(700)]
    assert 60 < faded.count(None) < 140  # about one in seven still goes out
    assert policy.blocked(**base | {'behavior': 'weight', 'unprompted_run': 9}) is None


def test_slots_after_midnight_belong_to_the_same_4am_day():
    assert policy.slot_time(DAY, '09:00') == ts(9)
    assert policy.slot_time(DAY, '01:00') == ts(1, day='2026-11-04')


def test_burden_decays():
    now = ts(12)
    assert policy.burden([now], now) == 1.0
    assert policy.burden([now - 86400], now) == 0.5


def test_reward_windows():
    planned = ts(9)
    assert rewards.window_end('weight', planned, ts(4, day='2026-11-04')) == planned + 7200
    assert rewards.window_end('habit:x', planned, ts(4, day='2026-11-04')) == ts(4, day='2026-11-04')
    assert rewards.outcome([planned + 3600], planned, planned + 7200) == 1.0
    assert rewards.outcome([planned - 60, planned + 7200], planned, planned + 7200) == 0.0


# --- the engine over a real database -----------------------------------------

def habit(title='Stretch', task_id=None):
    task_id = task_id or str(ULID())
    get_db().execute('INSERT INTO daily_tasks (id,title,position,created_at,updated_at) VALUES (?,?,1,1,1)',
                     (task_id, title))
    get_db().commit()
    return task_id


def enable(**kw):
    config.update(get_db(), {'enabled': True, **kw})


def decisions():
    return {r['behavior']: dict(r) for r in get_db().execute('SELECT * FROM coach_decisions')}


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    from backend.ai import provider
    monkeypatch.setattr(provider, 'is_ai_configured', lambda: False)


def test_plan_one_decision_per_behaviour_at_a_slot_still_ahead():
    task = habit()
    config.update(get_db(), {'behaviors': {'calories': False}})
    assert engine.plan_day(ts(10), random.Random(0)) == 2 + 4  # weight, the habit, goals for 4 horizons
    planned = decisions()
    assert planned['weight']['slot'] == '11:00'  # 07:30 and 09:00 have passed
    assert planned[f'habit:{task}']['planned_for'] > ts(10)
    assert 'calories' not in planned
    assert engine.plan_day(ts(10, 5), random.Random(0)) == 0


def test_a_full_day_sends_holds_skips_and_scores(monkeypatch):
    task = habit()
    enable(dailyCap=1, noSendRate=0.1)
    config.update(get_db(), {'behaviors': {'calories': False}})
    engine.plan_day(ts(4, 30), random.Random(0))
    # The weigh-in happened before its slot: skipped, not sent.
    get_db().execute("INSERT INTO body_weight_logs (id,date,weight,created_at,updated_at) VALUES ('w',?,80,?,?)",
                     (DAY, ts(6), ts(6)))
    get_db().commit()
    fired = engine.fire_due(ts(23, 59), random.Random(5))
    statuses = {d['behavior']: d['status'] for d in fired}
    assert statuses['weight'] == 'skipped_logged'
    sent = [d for d in fired if d['status'] == 'sent']
    assert len(sent) <= 1  # the daily cap
    assert all(d['status'] in ('sent', 'held', 'skipped_cap', 'skipped_logged', 'skipped_quiet') for d in fired)
    for d in sent:
        msg = get_db().execute('SELECT content, metadata FROM messages WHERE id=?', (d['message_id'],)).fetchone()
        coach = json.loads(msg['metadata'])['coach']
        assert coach['decisionId'] == d['id'] and msg['content'].startswith('**')
    # The habit got done in the evening: scored once the 4am day is over.
    get_db().execute('INSERT INTO daily_task_completions (id,task_id,date,created_at) VALUES (?,?,?,?)',
                     (str(ULID()), task, DAY, ts(21)))
    get_db().commit()
    assert engine.resolve(ts(23, 59)) == 0
    engine.resolve(ts(5, day='2026-11-04'))
    habit_row = decisions()[f'habit:{task}']
    if habit_row['status'] in ('sent', 'held'):
        assert habit_row['reward'] == 1.0


def test_excused_days_are_marked_and_left_out_of_learning():
    enable()
    get_db().execute("INSERT INTO coach_decisions (id,behavior,day,slot,planned_for,status,arm,created_at) "
                     "VALUES ('d','weight',?,'09:00',?,'held','none',1)", (DAY, ts(9)))
    get_db().execute("INSERT INTO day_excuses (id,day,target_kind,reason,kind,created_at) "
                     "VALUES ('e',?,'day','Flu','external',1)", (DAY,))
    get_db().commit()
    engine.resolve(ts(12))
    assert decisions()['weight']['excused'] == 1
    assert bandit.posteriors([dict(r) for r in get_db().execute('SELECT * FROM coach_decisions')], DAY) == {}


def test_templates_go_out_when_the_model_is_unavailable():
    task = habit('Weigh in')
    get_db().execute("UPDATE daily_tasks SET cue='after coffee' WHERE id=?", (task,))
    get_db().commit()
    for arm in bandit.ARMS:
        title, body, by_model = __import__('backend.coach.compose', fromlist=['compose']).compose(
            f'habit:{task}', arm, engine._facts(get_db(), f'habit:{task}', DAY))
        assert title == 'Weigh in' and body and not by_model
    _, body, _ = __import__('backend.coach.compose', fromlist=['x']).compose('weight', 'tiny', {})
    assert body == 'Just step on the scale — five seconds.'


def test_the_loop_runs_the_engine_only_when_switched_on():
    habit()
    did = scheduler.tick(ts(12))
    assert 'planned' not in did
    enable()
    did = scheduler.tick(ts(12))
    assert did['planned'] > 0


def test_settings_are_validated():
    with pytest.raises(ValueError):
        config.update(get_db(), {'noSendRate': 0.0})
    with pytest.raises(ValueError):
        config.update(get_db(), {'quietStart': 25})
    with pytest.raises(ValueError):
        config.update(get_db(), {'enabled': 'yes'})
    with pytest.raises(ValueError):
        config.update(get_db(), {'mystery': 1})
    assert config.update(get_db(), {'behaviors': {'weight': False}})['behaviors']['weight'] is False


def test_what_was_learned_reaches_the_prompt_once_it_is_sure(client, monkeypatch):
    monkeypatch.setattr(engine, 'day_key_for', lambda *a: '2026-10-31')
    task = habit('Stretch')
    for i in range(30):
        get_db().execute('INSERT INTO coach_decisions (id,behavior,day,slot,planned_for,status,arm,mode,reward,'
                         'resolved_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,1,1)',
                         (str(ULID()), f'habit:{task}', f'2026-10-{i + 1:02d}', '18:00', ts(18), 'sent', 'identity',
                          'policy', 1.0 if i % 10 else 0.0))
    get_db().commit()
    text = engine.context_text('2026-10-31')
    assert "Stretch: 'identity' reminders work best" in text and 'best around 18:00' in text
    [stat] = client.get('/api/coach/stats').json
    assert stat['label'] == 'Stretch' and stat['sent'] == 30 and stat['doneWhenSent'] == 0.9
    from backend.ai.chat import build_chat_system_prompt
    assert "'identity' reminders work best" in build_chat_system_prompt()


def test_routes(client):
    assert client.get('/api/coach/settings').json['enabled'] is False
    assert client.patch('/api/coach/settings', json={'enabled': True, 'dailyCap': 4}).json['dailyCap'] == 4
    assert client.patch('/api/coach/settings', json={'dailyCap': 99}).status_code == 400
    get_db().execute("INSERT INTO coach_decisions (id,behavior,day,slot,planned_for,status,created_at) "
                     "VALUES ('d','weight',?,'09:00',1,'sent',1)", (DAY,))
    get_db().commit()
    assert client.post('/api/coach/nudges/d/response', json={'action': 'snooze'}).json['response'] == 'snooze'
    assert client.post('/api/coach/nudges/d/response', json={'action': 'maybe'}).status_code == 400
    assert client.post('/api/coach/nudges/zz/response', json={'action': 'done'}).status_code == 404
