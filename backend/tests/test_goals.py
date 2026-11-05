import json
from datetime import datetime, timedelta

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.goals import evidence, service, tools


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now = int(datetime(2026, 10, 22, 12).timestamp())
    monkeypatch.setattr(service.time, 'time', lambda: now)
    # All tests mock inference; no provider may be contacted.
    monkeypatch.setattr(evidence, 'is_ai_configured', lambda: False)
    return now


def make_goal(**changes):
    return service.create(dict(title='Daily pushups', criteria='Do pushups', reward='Theater trip',
        targetDays=7, startDate='2026-10-16', exercise='pushups', minimumReps=10) | changes)


def workout(day, reps=10, name='pushups'):
    db = get_db()
    wid, eid = str(ULID()), str(ULID())
    db.execute('INSERT INTO workout_sessions (id,date,location_type,created_at,updated_at) VALUES (?,?,?,?,?)',
               (wid,day,'home',1,1))
    db.execute('INSERT INTO workout_exercises (id,session_id,name_raw,name_canonical) VALUES (?,?,?,?)',
               (eid,wid,name,name))
    db.execute('INSERT INTO workout_sets (id,exercise_id,set_order,reps) VALUES (?,?,?,?)',
               (str(ULID()),eid,0,reps))
    db.commit()
    return wid


def journal(day, text='I did ten pushups.', hour=12):
    db = get_db()
    jid = str(ULID())
    ts = int(datetime.fromisoformat(day).replace(hour=hour).timestamp())
    db.execute('INSERT INTO journal_entries (id,content,created_at,updated_at) VALUES (?,?,?,?)',
               (jid,text,ts,ts))
    db.commit()
    return jid


def week():
    return [workout(f'2026-10-{day}') for day in range(16, 23)]


def test_seventh_day_awards_at_noon_and_repeated_checks_keep_one_receipt():
    goal = make_goal()
    week()
    result = service.evaluate(goal['id'])
    assert result['status'] == 'earned'
    assert result['currentStreak'] == 7
    assert result['earnedAt'] is not None
    again = service.evaluate(goal['id'])
    assert again['earnedAt'] == result['earnedAt']
    assert get_db().execute('SELECT COUNT(*) FROM goal_days').fetchone()[0] == 7


def test_unfinished_today_preserves_yesterdays_streak_but_a_gap_breaks_it():
    goal = make_goal()
    for day in range(16,22):
        workout(f'2026-10-{day}')
    assert service.evaluate(goal['id'])['currentStreak'] == 6
    get_db().execute("DELETE FROM workout_sessions WHERE date='2026-10-21'")
    get_db().commit()
    assert service.evaluate(goal['id'])['currentStreak'] == 0


def test_workout_and_journal_count_one_day_and_workout_needs_no_ai(monkeypatch):
    goal = make_goal(targetDays=1, startDate='2026-10-22')
    workout('2026-10-22',5)
    workout('2026-10-22',5)
    journal('2026-10-22')
    monkeypatch.setattr(evidence, 'judge', lambda *a: pytest.fail('Workout proof should avoid inference'))
    result = service.evaluate(goal['id'])
    assert result['currentStreak'] == 1
    assert result['status'] == 'earned'


def test_insufficient_reps_and_other_exercises_do_not_count():
    goal = make_goal(targetDays=1)
    workout('2026-10-22',9)
    workout('2026-10-22',100,'squats')
    assert service.evaluate(goal['id'])['status'] == 'active'


def test_journal_can_fill_missing_day_and_cache_avoids_reinterpreting(monkeypatch):
    goal = make_goal()
    for day in range(16,22):
        workout(f'2026-10-{day}')
    jid = journal('2026-10-22')
    calls = []
    def judge(*args):
        calls.append(args)
        return 'met', [{'kind':'journal','id':jid,'text':'I did ten pushups.'}]
    monkeypatch.setattr(evidence, 'judge', judge)
    assert service.evaluate(goal['id'])['status'] == 'earned'
    service.evaluate(goal['id'])
    assert len(calls) == 1


def test_failed_ai_is_unresolved_and_explicit_check_retries(monkeypatch):
    goal = make_goal(targetDays=1)
    journal('2026-10-22')
    first = service.evaluate(goal['id'])
    assert first['status'] == 'active'
    assert first['days'][-1]['verdict'] == 'uncertain'
    monkeypatch.setattr(evidence, 'judge', lambda *a: ('met', []))
    assert service.evaluate(goal['id'], retry=False)['status'] == 'active'
    assert service.evaluate(goal['id'])['status'] == 'earned'


def test_edit_and_delete_recompute_and_flag_previous_reward(monkeypatch):
    goal = make_goal(targetDays=1, startDate='2026-10-22')
    jid = journal('2026-10-22')
    monkeypatch.setattr(evidence, 'judge', lambda *a: ('met', []))
    earned = service.evaluate(goal['id'])
    get_db().execute('UPDATE journal_entries SET content=? WHERE id=?', ('I planned to do pushups.',jid))
    get_db().commit()
    monkeypatch.setattr(evidence, 'judge', lambda *a: ('not_met', []))
    result = service.evaluate(goal['id'])
    assert result['status'] == 'needs_review'
    assert result['earnedAt'] == earned['earnedAt']
    with pytest.raises(ValueError):
        service.change_status(goal['id'],'redeem')
    get_db().execute('DELETE FROM journal_entries WHERE id=?', (jid,))
    get_db().commit()
    assert service.evaluate(goal['id'])['days'][0]['evidence'] == []


def test_redeemed_reward_is_preserved_when_sources_are_corrected():
    goal = make_goal()
    ids = week()
    service.evaluate(goal['id'])
    redeemed = service.change_status(goal['id'],'redeem')
    assert service.change_status(goal['id'],'redeem')['redeemedAt'] == redeemed['redeemedAt']
    get_db().execute('DELETE FROM workout_sessions WHERE id=?',(ids[0],))
    get_db().commit()
    result = service.evaluate(goal['id'])
    assert result['status'] == 'redeemed'
    assert result['needsReview']


def test_4am_boundary_and_future_days(monkeypatch):
    goal = make_goal(targetDays=1, startDate='2026-10-21')
    jid = journal('2026-10-22',hour=2)
    workout('2026-10-23')
    monkeypatch.setattr(evidence, 'judge', lambda *a: ('met',[{'kind':'journal','id':jid,'text':'I did ten pushups.'}]))
    result = service.evaluate(goal['id'])
    assert result['days'][0]['date'] == '2026-10-21'
    assert result['days'][0]['verdict'] == 'met'
    assert result['days'][-1]['date'] == '2026-10-22'
    assert result['days'][-1]['verdict'] == 'not_met'


def test_late_entry_can_restore_a_past_streak():
    goal = make_goal()
    for day in range(16,23):
        if day != 19:
            workout(f'2026-10-{day}')
    assert service.evaluate(goal['id'])['status'] == 'active'
    workout('2026-10-19')
    assert service.evaluate(goal['id'])['status'] == 'earned'


def test_repeated_create_is_idempotent_and_cancel_stops_checks():
    goal = make_goal()
    assert make_goal()['id'] == goal['id']
    assert service.change_status(goal['id'],'cancel')['status'] == 'cancelled'
    week()
    assert service.evaluate(goal['id'])['status'] == 'cancelled'
    assert make_goal()['id'] != goal['id']


@pytest.mark.parametrize('changes', [
    {'targetDays':True}, {'targetDays':0}, {'targetDays':367}, {'targetDays':1.5},
    {'startDate':'bad'}, {'startDate':'2025-01-01'}, {'reward':''},
    {'minimumReps':False}, {'minimumReps':-1}, {'exercise':None,'minimumReps':10},
])
def test_validation(changes):
    with pytest.raises(ValueError):
        make_goal(**changes)


def test_generic_journal_goal_works(monkeypatch):
    goal = make_goal(exercise=None, minimumReps=None, criteria='Read for 20 minutes',targetDays=1)
    journal('2026-10-22','Read for twenty minutes.')
    monkeypatch.setattr(evidence,'judge',lambda criteria,*a: ('met',[]) if criteria == 'Read for 20 minutes' else ('not_met',[]))
    assert service.evaluate(goal['id'])['status'] == 'earned'


@pytest.mark.parametrize('verdict',['not_met','uncertain'])
def test_judge_preserves_negative_and_ambiguous_verdicts(monkeypatch,verdict):
    monkeypatch.setattr(evidence,'is_ai_configured',lambda:True)
    monkeypatch.setattr(evidence,'chat_json',lambda *a,**kw: {'verdict':verdict,'citations':[]})
    assert evidence.judge('Do pushups','2026-10-22',[{'id':'j','text':'Maybe later.'}]) == (verdict,[])


def test_judge_requires_real_id_and_verbatim_quote(monkeypatch):
    monkeypatch.setattr(evidence,'is_ai_configured',lambda:True)
    monkeypatch.setattr(evidence,'chat_json',lambda *a,**kw: {'verdict':'met','citations':[{'id':'j','quote':'I did pushups.'}]})
    with pytest.raises(RuntimeError):
        evidence.judge('Do pushups','2026-10-22',[{'id':'j','text':'I will do pushups.'}])
    assert evidence.judge('Do pushups','2026-10-22',[{'id':'j','text':'I did pushups.'}])[0] == 'met'


def test_tool_and_route_check_use_same_immediate_evaluator(client):
    goal = make_goal()
    week()
    text, event = tools.run_tool('check_goal',{'goalId':goal['id']})
    assert event['ok'] and json.loads(text)['status'] == 'earned'
    response = client.post(f"/api/goals/{goal['id']}/check")
    assert response.status_code == 200
    assert response.json['earnedAt'] == json.loads(text)['earnedAt']
    assert client.get('/api/goals').json[0]['id'] == goal['id']
    assert client.post('/api/goals/missing/check').status_code == 404
    assert client.post('/api/goals',json=[]).status_code == 400
    assert client.post('/api/goals',json={'title':'x'}).status_code == 400


def test_goals_are_only_available_in_chat():
    from backend.delegate.chat import _toolbox
    kwargs = dict(conversation_id=None,checkpoint=None,deadline=None)
    chat = _toolbox('chat',**kwargs)
    assert tools.TOOL_NAMES <= set(chat[1])
    research = _toolbox('research',**kwargs)
    assert tools.TOOL_NAMES.isdisjoint(research[1])
    assert _toolbox('none',**kwargs) is None


def test_background_sweep_uses_evaluator_and_scheduler_guard(monkeypatch):
    from backend.goals import scheduler
    goal = make_goal(targetDays=1)
    workout('2026-10-22')
    scheduler.sweep()
    assert service.get(goal['id'])['status'] == 'earned'
    monkeypatch.setenv('LUNASCHAL_NO_SCHEDULERS','1')
    monkeypatch.setattr(scheduler.threading,'Thread',lambda **kw:pytest.fail('scheduler started in tests'))
    scheduler.start_scheduler()


def test_source_edit_during_a_later_days_check_cannot_award(monkeypatch):
    goal = make_goal()
    ids = [workout(f'2026-10-{day}') for day in range(16,22)]
    journal('2026-10-22')
    def judge(*args):
        get_db().execute('DELETE FROM workout_sessions WHERE id=?', (ids[0],))
        get_db().commit()
        return 'met', []
    monkeypatch.setattr(evidence, 'judge', judge)
    result = service.evaluate(goal['id'])
    assert result['status'] == 'active'
    assert result['days'][0]['verdict'] == 'uncertain'


def test_preempted_check_is_incomplete_and_releases_for_retry(monkeypatch):
    from backend.ai import service as ai_service
    goal = make_goal(targetDays=1)
    journal('2026-10-16')
    def preempt(*args):
        ai_service._mark_deferred('preempted')
        raise RuntimeError('Preempted')
    monkeypatch.setattr(evidence,'judge',preempt)
    try:
        result = service.evaluate(goal['id'])
        assert result['checkIncomplete']
        assert result['earnedAt'] is None
        with pytest.raises(ValueError, match='interrupted'):
            service.change_status(goal['id'],'redeem')
    finally:
        ai_service.clear_deferral()
    monkeypatch.setattr(evidence,'judge',lambda *a: ('met',[]))
    assert service.evaluate(goal['id'])['status'] == 'earned'


def test_chat_gathering_loop_can_check_and_report_a_midday_award(monkeypatch):
    from types import SimpleNamespace
    from backend.delegate import chat
    goal = make_goal()
    week()
    turns = iter([
        SimpleNamespace(content='', tool_calls=[SimpleNamespace(id='goal-check', function=SimpleNamespace(
            name='check_goal', arguments=json.dumps({'goalId':goal['id']})))]),
        SimpleNamespace(content='', tool_calls=None),
    ])
    def turn(*args, **kwargs):
        reply = next(turns)
        return reply, 'tool_calls' if reply.tool_calls else 'stop'
    monkeypatch.setattr(chat.tool_loop,'chat_tool_turn',turn)
    seen = []
    def answer(messages):
        seen.extend(messages)
        yield 'content', 'Your theater trip is earned.'
    monkeypatch.setattr(chat,'chat_stream_events',answer)
    events = list(chat.stream_reply([{'role':'user','content':'Did I earn my theater trip yet?'}]))
    steps = [value for kind,value in events if kind == 'step']
    assert any(step['tool'] == 'check_goal' and step['ok'] for step in steps)
    assert any(m['role'] == 'tool' and '"status": "earned"' in m['content'] for m in seen)
    assert service.get(goal['id'])['status'] == 'earned'
