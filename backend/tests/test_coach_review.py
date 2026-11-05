"""The day review, excuses, the evening check-in and goal rollover
(backend/coach/, backend/goals/rollover.py)."""
import json
from datetime import datetime

import pytest
from ulid import ULID

from backend import briefing_job
from backend.coach import review, scheduler, tools
from backend.db.connection import get_db
from backend.goals import rollover, service as goals

# Tuesday 2026-11-03 at 09:00: "yesterday" is Monday the 2nd.
NOW = int(datetime(2026, 11, 3, 9).timestamp())
DAY = '2026-11-02'


def ts(day, hour=12):
    return int(datetime.fromisoformat(day).replace(hour=hour).timestamp())


def habit(title, created=DAY, cue=None):
    task_id = str(ULID())
    get_db().execute('INSERT INTO daily_tasks (id,title,position,cue,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                     (task_id, title, 1, cue, ts(created, 6), ts(created, 6)))
    get_db().commit()
    return task_id


def tick(task_id, day=DAY):
    get_db().execute('INSERT INTO daily_task_completions (id,task_id,date,created_at) VALUES (?,?,?,?)',
                     (str(ULID()), task_id, day, ts(day)))
    get_db().commit()


def weigh(day=DAY):
    get_db().execute('INSERT INTO body_weight_logs (id,date,weight,created_at,updated_at) VALUES (?,?,?,?,?)',
                     (str(ULID()), day, 80.0, ts(day), ts(day)))
    get_db().commit()


def calories(day=DAY):
    get_db().execute('INSERT INTO calorie_logs (id,date,description,calories,created_at) VALUES (?,?,?,?,?)',
                     (str(ULID()), day, 'Lunch', 600, ts(day)))
    get_db().commit()


@pytest.fixture
def model(monkeypatch):
    """The summary model; tests set `.reply` or `.fail`."""
    from backend.ai import llm, provider

    class Model:
        reply = {'summary': 'A steady day.', 'goalContribution': '', 'replan': []}
        fail = False
        calls = []

    def chat_json(prompt, system=None, schema=None, **kw):
        Model.calls.append(json.loads(prompt))
        if Model.fail:
            raise RuntimeError('model down')
        return Model.reply

    monkeypatch.setattr(provider, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(llm, 'chat_json', chat_json)
    Model.calls = []
    return Model


# --- what a day holds --------------------------------------------------------

def test_a_day_holds_habits_and_only_the_logging_the_user_has_taken_up():
    a, b = habit('Stretch'), habit('Read')
    habit('Started tomorrow', created='2026-11-03')
    tick(a)
    r = review.draft(DAY, NOW)
    assert [(i['kind'], i['title'], i['done']) for i in r['items']] == [('habit', 'Stretch', True),
                                                                      ('habit', 'Read', False)]
    weigh('2026-10-20')
    calories('2026-10-20')
    kinds = [i['kind'] for i in review.draft(DAY, NOW)['items']]
    assert kinds == ['habit', 'habit', 'weight', 'calories']
    assert b


def test_a_meal_counts_as_logging_calories():
    calories('2026-10-20')
    get_db().execute('INSERT INTO food_entries (id,raw_content,created_at,updated_at) VALUES (?,?,?,?)',
                     ('meal', 'Pasta', ts(DAY, 13), ts(DAY, 13)))
    get_db().commit()
    [item] = review.draft(DAY, NOW)['items']
    assert item['kind'] == 'calories' and item['done']


def test_behaviors_switch_targets_off():
    weigh('2026-10-20')
    habit('Stretch')
    get_db().execute("""UPDATE settings SET coach_behaviors='{"weight": false, "habits": false}' WHERE id=1""")
    get_db().commit()
    assert review.draft(DAY, NOW)['items'] == []


# --- excuses -----------------------------------------------------------------

def test_an_excuse_takes_an_item_out_of_the_score_and_freezes_the_streak(client):
    a, b = habit('Stretch'), habit('Read')
    tick(a)
    tick(b, '2026-11-01')
    assert review.draft(DAY, NOW)['rating'] == 3  # 1 of 2
    excuse, r = review.record_excuse(DAY, 'habit', 'read', 'Stuck at the hospital', 'external', now=NOW)
    assert excuse['target_id'] == b and excuse['target_title'] == 'Read'
    assert (r['rating'], r['excused'], r['done']) == (5, 1, 1)
    tasks = {t['id']: t for t in client.get('/api/tasks').json}
    assert DAY in tasks[b]['excused']


def test_an_internal_reason_is_recorded_without_excusing():
    a = habit('Stretch')
    _, r = review.record_excuse(DAY, 'habit', 'Stretch', 'Just didn\'t feel like it', 'internal', now=NOW)
    assert r['rating'] == 1 and r['excused'] == 0
    assert r['items'][0]['excuse'] == "Just didn't feel like it"
    assert a


def test_unknown_targets_are_refused_with_the_options():
    habit('Stretch')
    with pytest.raises(ValueError, match='Stretch'):
        review.record_excuse(DAY, 'habit', 'Swim', 'x', 'external', now=NOW)
    with pytest.raises(ValueError):
        review.record_excuse(DAY, 'mood', None, 'x', 'external', now=NOW)
    with pytest.raises(ValueError):
        review.record_excuse(DAY, 'day', None, '', 'external', now=NOW)


def test_an_excuse_rewrites_the_review_card_already_sent(model):
    habit('Stretch')
    briefing = {'briefing': 'Morning!', 'todos': [], 'events': []}
    import backend.briefing_job as bj
    bj_generate = bj.generate_briefing
    try:
        bj.generate_briefing = lambda ctx: dict(briefing)
        result = briefing_job.run_briefing(now=NOW)
    finally:
        bj.generate_briefing = bj_generate
    row = get_db().execute('SELECT content, metadata FROM messages WHERE id=?', (result['messageId'],)).fetchone()
    assert row['content'].startswith('**Yesterday ★☆☆☆☆** — 0 of 1 done\n\nA steady day.\n\nMorning!')
    assert json.loads(row['metadata'])['review']['rating'] == 1
    review.record_excuse(DAY, 'day', None, 'Flu', 'external', now=NOW)
    meta = json.loads(get_db().execute('SELECT metadata FROM messages WHERE id=?',
                                       (result['messageId'],)).fetchone()['metadata'])
    assert meta['review']['rating'] is None and meta['review']['excused'] == 1


# --- finalizing --------------------------------------------------------------

def test_finalize_writes_the_summary_once(model):
    habit('Stretch')
    r = review.finalize(DAY, NOW)
    assert (r['status'], r['summary'], r['degraded']) == ('final', 'A steady day.', False)
    assert model.calls[0]['items'][0] == {'title': 'Stretch', 'kind': 'habit', 'status': 'missed', 'reason': None}
    review.finalize(DAY, NOW)
    assert len(model.calls) == 1


def test_a_model_that_cannot_answer_leaves_the_template_and_the_rating(model):
    a, b = habit('Stretch'), habit('Read')
    tick(a)
    model.fail = True
    r = review.finalize(DAY, NOW)
    assert r['degraded'] and r['rating'] == 3
    assert r['summary'] == '1 of 2 done. Missed: Read.'


def test_recurring_obstacles_reach_the_model_and_come_back_as_habit_plan_cards(client, model):
    task = habit('Weigh in', created='2026-10-25')
    for day in ('2026-10-30', '2026-11-01', DAY):
        review.record_excuse(day, 'habit', 'Weigh in', 'Ran late', 'internal', obstacle='late mornings', now=NOW)
    model.reply = {'summary': 'Close.', 'goalContribution': 'Nothing toward the 10k.',
                   'replan': [{'habitTitle': 'Weigh in', 'obstacle': 'late mornings',
                               'ifThen': 'When I start the kettle, I will step on the scale'},
                              {'habitTitle': 'No such habit', 'obstacle': 'x', 'ifThen': 'y'}]}
    import backend.briefing_job as bj
    original = bj.generate_briefing
    bj.generate_briefing = lambda ctx: {'briefing': 'Hi', 'todos': [], 'events': []}
    try:
        result = briefing_job.run_briefing(now=NOW)
    finally:
        bj.generate_briefing = original
    assert model.calls[0]['recurring'][0]['days'] == 3
    meta = json.loads(get_db().execute('SELECT metadata FROM messages WHERE id=?',
                                       (result['messageId'],)).fetchone()['metadata'])
    [plan] = [p for p in meta['proposals'] if p['kind'] == 'habit_plan']
    assert plan['data']['taskId'] == task
    assert 'Nothing toward the 10k.' in review.get(DAY)['summary']
    r = client.post(f"/api/chat/proposals/{result['messageId']}/{plan['id']}", json={'action': 'accept'})
    assert r.status_code == 200, r.json
    cue = get_db().execute('SELECT cue FROM daily_tasks WHERE id=?', (task,)).fetchone()['cue']
    assert cue == 'When I start the kettle, I will step on the scale'


def test_override_moves_one_star_with_a_reason():
    a, b = habit('Stretch'), habit('Read')
    tick(a)
    r = review.override(DAY, 4, 'Read on the train, just not logged', now=NOW)
    assert (r['rating'], r['computedRating']) == (4, 3)
    with pytest.raises(ValueError):
        review.override(DAY, 5, 'Please', now=NOW)
    with pytest.raises(ValueError):
        review.override(DAY, 4, '', now=NOW)
    # The arithmetic moving out of reach drops the override.
    tick(b)
    assert review.draft(DAY, NOW)['rating'] == 5
    assert review.get(DAY)['ratingOverride'] is None


# --- the evening check-in and the loop ---------------------------------------

def test_checkin_lists_whats_open_once():
    a, b = habit('Stretch', created='2026-11-03'), habit('Read', created='2026-11-03')
    tick(a, '2026-11-03')
    evening = int(datetime(2026, 11, 3, 21, 5).timestamp())
    message_id = scheduler.post_checkin(evening)
    row = get_db().execute('SELECT content, metadata FROM messages WHERE id=?', (message_id,)).fetchone()
    assert row['content'].startswith('Evening check-in. Still open today: Read.')
    assert json.loads(row['metadata'])['coach']['open'] == [{'kind': 'habit', 'id': b, 'title': 'Read'}]
    assert scheduler.post_checkin(evening + 60) is None


def test_tick_reads_settings_every_time_and_finalizes_after_the_briefing_window(monkeypatch):
    habit('Stretch')
    evening = int(datetime(2026, 11, 3, 21, 5).timestamp())
    first = scheduler.tick(evening)
    assert first['checkin'] is None  # off by default
    assert first['finalized'] == DAY
    get_db().execute('UPDATE settings SET coach_checkin_enabled=1 WHERE id=1')
    get_db().commit()
    did = scheduler.tick(evening)  # the switch is read again, no restart
    assert did['checkin'] and did['finalized'] is None
    assert review.get(DAY)['status'] == 'final'
    early = int(datetime(2026, 11, 4, 5).timestamp())
    assert scheduler.tick(early)['finalized'] is None  # the briefing's window


# --- chat tools --------------------------------------------------------------

def test_tools_record_read_and_cue(monkeypatch):
    monkeypatch.setattr(review, 'yesterday', lambda now=None: DAY)
    habit('Stretch')
    text, event = tools.run_tool('record_excuse', {'day': 'yesterday', 'targetKind': 'habit', 'target': 'stretch',
                                                    'reason': 'Back pain', 'kind': 'external'})
    assert event['ok'] and json.loads(text)['excused']
    read = json.loads(tools.run_tool('get_day_review', {'day': 'yesterday'})[0])
    assert read['items'][0]['status'] == 'excused'
    _, event = tools.run_tool('record_excuse', {'day': 'last week', 'targetKind': 'day', 'reason': 'x', 'kind': 'external'})
    assert not event['ok']
    text, event = tools.run_tool('set_habit_cue', {'habit': 'Stretch', 'cue': 'after brushing teeth'})
    assert event['ok']
    assert get_db().execute("SELECT cue FROM daily_tasks").fetchone()['cue'] == 'after brushing teeth'


def test_coach_tools_are_in_the_chat_toolbox():
    from backend.delegate.chat import _toolbox
    chat = _toolbox('chat', conversation_id=None, checkpoint=None, deadline=None)
    assert tools.TOOL_NAMES <= set(chat[1])


def test_routes(client):
    habit('Stretch')
    r = client.get('/api/coach/reviews?days=3')
    assert r.status_code == 200 and 'today' in r.json
    r = client.post(f'/api/coach/reviews/{DAY}/excuses',
                    json={'targetKind': 'habit', 'target': 'Stretch', 'reason': 'Travel', 'kind': 'external'})
    assert r.status_code == 201 and r.json['review']['excused'] == 1
    assert client.get('/api/coach/reviews/not-a-day').status_code == 400


def test_task_cue_route(client):
    task = habit('Weigh in')
    assert client.patch(f'/api/tasks/{task}', json={'cue': ' after coffee '}).status_code == 200
    [row] = client.get('/api/tasks').json
    assert row['cue'] == 'after coffee' and row['title'] == 'Weigh in'
    client.patch(f'/api/tasks/{task}', json={'cue': ''})
    assert client.get('/api/tasks').json[0]['cue'] is None
    assert client.patch(f'/api/tasks/{task}', json={}).status_code == 400


# --- rollover ----------------------------------------------------------------

def test_rollover_grades_ends_journals_and_asks_for_new_goals(model, monkeypatch):
    # Last week's goals, seen on Tuesday the 3rd (week of Nov 2 is current).
    monkeypatch.setattr(goals.time, 'time', lambda: ts('2026-10-27'))
    text_goal = goals.create({'id': 'text', 'horizon': 'week', 'title': 'Clear the inbox'})
    rule = goals.create({'id': 'rule', 'horizon': 'week', 'title': 'Pushups', 'criteria': 'Do pushups',
                         'startDate': '2026-10-27', 'targetDays': 4})
    monkeypatch.setattr(goals.time, 'time', lambda: NOW)
    for day in ('2026-10-27', '2026-10-28'):
        get_db().execute('INSERT INTO goal_days (id,goal_id,date,fingerprint,verdict,evidence,checked_at) '
                         "VALUES (?,?,?,?,?,?,?)", (str(ULID()), 'rule', day, 'x', 'met', '[]', NOW))
    get_db().commit()
    monkeypatch.setattr(goals, 'evaluate', lambda goal_id, **kw: goals.get(goal_id))
    model.reply = {'rating': 4, 'review': 'Got it down to six.'}
    graded = rollover.run(NOW)
    assert {g['id']: (g['rating'], g['status']) for g in graded} == {'text': (4, 'ended'), 'rule': (3, 'ended')}
    assert goals.get('rule')['review'] == 'Met 2 of 4 days.'
    entries = [r['content'] for r in get_db().execute(
        "SELECT content FROM journal_entries WHERE goal_id='text' ORDER BY rowid")]
    assert entries[-1] == 'Reviewed the goal for the week of Oct 26: Clear the inbox — 4/5.\n\nGot it down to six.'
    msg = get_db().execute("SELECT content, metadata FROM messages WHERE metadata LIKE '%rollover%'").fetchone()
    assert msg['content'].endswith('What are your goals for this week (Week of Nov 2)?')
    assert json.loads(msg['metadata'])['coach']['horizons'] == ['week']
    # Idempotent: nothing left to close.
    assert rollover.run(NOW) == []
    assert text_goal and rule


def test_rollover_without_a_model_ends_ungraded_but_a_preempted_one_waits(model, monkeypatch):
    from backend.ai import service as ai_service
    monkeypatch.setattr(goals.time, 'time', lambda: ts('2026-10-27'))
    goals.create({'id': 'g', 'horizon': 'week', 'title': 'Inbox'})
    monkeypatch.setattr(goals.time, 'time', lambda: NOW)

    def preempted(*a, **kw):
        ai_service._mark_deferred('preempted')
        raise RuntimeError('Preempted')
    from backend.ai import llm
    monkeypatch.setattr(llm, 'chat_json', preempted)
    try:
        assert rollover.run(NOW) == []
        assert goals.get('g')['endedAt'] is None
    finally:
        ai_service.clear_deferral()
    model.fail = True
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('down')))
    [g] = rollover.run(NOW)
    assert g['rating'] is None and g['review'].startswith('Not graded')


def test_overarching_goals_never_roll_over(model):
    goals.set_overarching('Be well.')
    assert rollover.run(NOW) == []


# --- habit suggestions and the Sunday look-back ------------------------------

def _briefing(monkeypatch, reply, now=NOW):
    seen = {}

    def generate(ctx):
        seen.update(ctx)
        return dict(reply)
    monkeypatch.setattr(briefing_job, 'generate_briefing', generate)
    result = briefing_job.run_briefing(now=now, force=True)
    meta = json.loads(get_db().execute('SELECT metadata FROM messages WHERE id=?',
                                       (result['messageId'],)).fetchone()['metadata'])
    return seen, meta


def test_a_suggested_habit_is_a_card_once_a_week_and_accepting_adds_it(client, monkeypatch):
    suggestion = {'title': 'Walk after lunch', 'cue': 'after I put my plate away', 'why': 'For the 10k.'}
    reply = {'briefing': 'Hi', 'todos': [], 'events': [], 'habitSuggestion': suggestion}
    seen, meta = _briefing(monkeypatch, reply)
    assert seen['suggest_habit'] is True
    [card] = [p for p in meta['proposals'] if p['kind'] == 'habit']
    r = client.post(f"/api/chat/proposals/{get_db().execute('SELECT id FROM messages ORDER BY created_at DESC').fetchone()[0]}/{card['id']}",
                    json={'action': 'accept'})
    assert r.status_code == 200, r.json
    row = get_db().execute("SELECT title, cue FROM daily_tasks").fetchone()
    assert (row['title'], row['cue']) == ('Walk after lunch', 'after I put my plate away')
    # Within the week, no second invitation and any suggestion is ignored.
    seen, meta = _briefing(monkeypatch, reply, NOW + 2 * 86400)
    assert seen['suggest_habit'] is False
    assert not [p for p in meta.get('proposals', []) if p['kind'] == 'habit']


def test_no_suggestion_into_a_full_list(monkeypatch):
    for i in range(10):
        habit(f'H{i}')
    seen, meta = _briefing(monkeypatch, {'briefing': 'Hi', 'todos': [], 'events': [],
                                         'habitSuggestion': {'title': 'X', 'cue': 'y', 'why': 'z'}})
    assert seen['suggest_habit'] is False


def test_sunday_brings_the_week(monkeypatch):
    for day in ('2026-10-27', '2026-10-31'):
        review.draft(day, NOW)
    sunday = int(datetime(2026, 11, 1, 9).timestamp())
    seen, _ = _briefing(monkeypatch, {'briefing': 'Hi', 'todos': [], 'events': []}, sunday)
    assert [d['day'] for d in seen['week']] == ['2026-10-31', '2026-10-27']
    from backend.ai.briefing import build_briefing_prompt
    assert "It's Sunday." in build_briefing_prompt(seen)
    weekday, _ = _briefing(monkeypatch, {'briefing': 'Hi', 'todos': [], 'events': []}, NOW)
    assert 'week' not in weekday
