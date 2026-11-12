"""The chat bar as more than a list of strings (backend/day_plan.py): rows
linked to the to-do or habit they restate, ticked in both directions, and a
memory of what the user keeps skipping so the plan stops pushing it."""
import json
import time
from datetime import date, timedelta

import pytest

from backend import briefing_job, day_plan
from backend import day_plan_backoff as backoff
from backend.ai import briefing as briefing_mod
from backend.db import connection
from backend.day_boundary import day_key_for


def _db():
    return connection.get_db()


def _today():
    return day_key_for()


def _days_ago(n):
    return (date.fromisoformat(_today()) - timedelta(days=n)).isoformat()


def _todo(id, title, done=0, todo_list='todo'):
    _db().execute(
        'INSERT INTO todos(id, title, done, list, priority, created_at, updated_at)'
        ' VALUES (?,?,?,?,3,0,0)', (id, title, done, todo_list))
    _db().commit()


def _habit(id, title):
    _db().execute('INSERT INTO daily_tasks(id, title, position, created_at, updated_at)'
                  ' VALUES (?,?,0,0,0)', (id, title))
    _db().commit()


def _bar_row(id, title, day=None, source=(None, None), done=0):
    _db().execute(
        'INSERT INTO chat_todos(id, day_key, title, priority, done, created_at, updated_at,'
        ' source_type, source_id) VALUES (?,?,?,3,?,0,0,?,?)',
        (id, day or _today(), title, done, source[0], source[1]))
    _db().commit()


def _bar_done(id):
    return _db().execute('SELECT done FROM chat_todos WHERE id=?', (id,)).fetchone()['done']


def _events(kind):
    return _db().execute('SELECT COUNT(*) FROM task_events WHERE kind=?', (kind,)).fetchone()[0]


def _suggestion(key):
    row = _db().execute('SELECT * FROM chat_todo_suggestions WHERE item_key=?', (key,)).fetchone()
    return dict(row) if row else None


def _fake_briefing(monkeypatch, todos=None, capture=None):
    def fake(ctx):
        if capture is not None:
            capture.update(ctx)
        return {'briefing': 'Morning.', 'todos': list(todos or []), 'events': []}
    monkeypatch.setattr(briefing_job, 'generate_briefing', fake)


# --- the backoff rule (pure) ---

@pytest.mark.parametrize('streak,daily,gap', [
    (0, False, 0), (1, False, 0), (2, False, 1), (3, False, 3), (4, False, 7), (9, False, 7),
    (2, True, 1), (4, True, 1),
])
def test_gap_grows_with_the_streak_and_stays_short_for_a_habit(streak, daily, gap):
    assert backoff.gap_days(streak, daily=daily) == gap


def test_eligible_from_counts_from_the_day_after_the_skip():
    assert backoff.eligible_from('2026-07-10', 1) is None
    assert backoff.eligible_from('2026-07-10', 2) == '2026-07-12'
    assert backoff.eligible_from('2026-07-10', 3) == '2026-07-14'


def test_linked_title_annotations_are_stripped():
    assert day_plan.clean_linked_title('Buy milk (due 2026-07-30) [priority 4/5] [shopping]') == 'Buy milk'


# --- ticking, both ways ---

def test_ticking_a_linked_bar_row_completes_the_todo_with_one_notification(client):
    _todo('t1', 'Renew passport')
    _bar_row('c1', 'Renew passport', source=('todo', 't1'))

    assert client.patch('/api/tasks/chat-todos/c1', json={'done': True}).status_code == 200
    assert _db().execute("SELECT done FROM todos WHERE id='t1'").fetchone()['done'] == 1
    assert _bar_done('c1') == 1
    assert _events('todo_completed') == 1
    assert _events('chat_todo_completed') == 0

    client.patch('/api/tasks/chat-todos/c1', json={'done': False})
    assert _db().execute("SELECT done FROM todos WHERE id='t1'").fetchone()['done'] == 0
    assert _bar_done('c1') == 0
    assert _events('todo_completed') == 0


def test_ticking_the_todo_in_tasks_ticks_the_bar_row(client):
    _todo('t1', 'Renew passport')
    _bar_row('c1', 'Renew passport', source=('todo', 't1'))

    client.patch('/api/tasks/todos/t1', json={'done': True})
    assert _bar_done('c1') == 1
    client.patch('/api/tasks/todos/t1', json={'done': False})
    assert _bar_done('c1') == 0


def test_a_linked_habit_ticks_both_ways(client):
    _habit('h1', 'Stretch')
    _bar_row('c1', 'Stretch', source=('daily', 'h1'))

    client.patch('/api/tasks/chat-todos/c1', json={'done': True})
    assert _db().execute("SELECT COUNT(*) FROM daily_task_completions WHERE task_id='h1'"
                         ).fetchone()[0] == 1
    assert _events('daily_completed') == 1

    client.delete('/api/tasks/h1/complete')
    assert _bar_done('c1') == 0
    client.post('/api/tasks/h1/complete')
    assert _bar_done('c1') == 1


def test_a_bar_row_whose_todo_was_deleted_ticks_as_an_ordinary_item(client):
    _bar_row('c1', 'Gone', source=('todo', 'missing'))
    client.patch('/api/tasks/chat-todos/c1', json={'done': True})
    assert _bar_done('c1') == 1
    assert _events('chat_todo_completed') == 1


def test_promoting_a_linked_row_is_refused(client):
    _todo('t1', 'Renew passport')
    _bar_row('c1', 'Renew passport', source=('todo', 't1'))
    resp = client.post('/api/tasks/chat-todos/c1/promote', json={'title': 'Renew passport'})
    assert resp.status_code == 409
    assert _db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 1


def test_the_list_route_returns_the_link_and_the_reason(client):
    _todo('t1', 'Renew passport')
    client.post('/api/tasks/chat-todos', json={'items': [{'title': 'renew passport'}]})
    row = client.get('/api/tasks/chat-todos').get_json()[0]
    assert (row['sourceType'], row['sourceId']) == ('todo', 't1')
    assert 'reason' in row


# --- the briefing links what it restates ---

def test_briefing_links_items_by_linked_title_and_by_title(client, monkeypatch):
    _todo('t1', 'Buy groceries')
    _habit('h1', 'Stretch')
    _fake_briefing(monkeypatch, [
        {'title': 'Get groceries', 'priority': 3, 'linkedTitle': 'Buy groceries [priority 3/5]',
         'basis': 'list', 'reason': 'fridge is empty'},
        {'title': 'Stretch', 'priority': 3, 'linkedTitle': None, 'basis': 'list', 'reason': 'habit'},
    ])
    briefing_job.run_briefing()
    rows = {r['title']: r for r in _db().execute('SELECT * FROM chat_todos')}
    assert (rows['Get groceries']['source_type'], rows['Get groceries']['source_id']) == ('todo', 't1')
    assert rows['Get groceries']['reason'] == 'fridge is empty'
    assert (rows['Stretch']['source_type'], rows['Stretch']['source_id']) == ('daily', 'h1')


def test_the_fallback_plan_links_by_id(client, monkeypatch):
    _todo('t1', 'Buy groceries')
    _fake_briefing(monkeypatch, [])
    briefing_job.run_briefing()
    row = _db().execute('SELECT source_type, source_id FROM chat_todos').fetchone()
    assert tuple(row) == ('todo', 't1')


def test_new_items_are_capped_and_tied_to_their_goal(client, monkeypatch):
    monkeypatch.setattr(briefing_mod, 'goal_titles', lambda today: {'run a 10k': 'g1'})
    _fake_briefing(monkeypatch, [
        {'title': 'Run 3 km', 'priority': 3, 'basis': 'goal', 'basisRef': 'Run a 10k', 'reason': 'goal'},
        {'title': 'Call landlord', 'priority': 3, 'basis': 'journal', 'basisRef': '2026-07-13',
         'reason': 'you wrote you need to'},
        {'title': 'Third new one', 'priority': 3, 'basis': 'journal', 'basisRef': None, 'reason': 'x'},
        {'title': 'Unknown goal', 'priority': 3, 'basis': 'goal', 'basisRef': 'Nope', 'reason': 'x'},
    ])
    briefing_job.run_briefing()
    rows = [tuple(r) for r in _db().execute(
        'SELECT title, source_type, source_id FROM chat_todos ORDER BY created_at, title')]
    assert rows == [('Call landlord', 'journal', None), ('Run 3 km', 'goal', 'g1')]


def test_the_plan_reads_earlier_journal_entries(client, monkeypatch):
    now = int(time.time())
    _db().execute('INSERT INTO journal_entries(id, content, created_at, updated_at) VALUES (?,?,?,?)',
                  ('j1', 'I really need to call the landlord about the heater.',
                   now - 2 * 86400, now - 2 * 86400))
    _db().commit()
    seen = {}
    _fake_briefing(monkeypatch, [], capture=seen)
    briefing_job.run_briefing(now=now)
    assert [e['id'] for e in seen['earlier_journal']] == ['j1']
    assert 'call the landlord' in briefing_mod.build_briefing_prompt(seen)


# --- what the user keeps skipping ---

def _skip(title, days, source=(None, None)):
    """`title` was in the bar, undone, on each of the last `days` days."""
    for n in range(days, 0, -1):
        _bar_row(f'{title}-{n}', title, day=_days_ago(n), source=source)


def test_a_skip_or_two_is_counted_and_then_held_back(client, monkeypatch):
    _todo('t1', 'Clean the garage')
    _skip('Clean the garage', 2, source=('todo', 't1'))
    seen = {}
    _fake_briefing(monkeypatch, [{'title': 'Clean the garage', 'priority': 3, 'basis': 'list',
                                  'linkedTitle': 'Clean the garage', 'reason': 'x'}], capture=seen)
    briefing_job.run_briefing()

    s = _suggestion('todo:t1')
    assert s['ignored_streak'] == 2 and s['total_ignored'] == 2
    # Left out of what the model reads, and out of the bar even though the
    # model wrote it anyway.
    assert [t['title'] for t in seen['todos']] == []
    assert seen['skipped'] == [{'title': 'Clean the garage', 'ignored_streak': 2}]
    assert _db().execute('SELECT COUNT(*) FROM chat_todos WHERE day_key=?', (_today(),)).fetchone()[0] == 0


def test_tallying_is_idempotent_across_a_forced_rerun(client, monkeypatch):
    _skip('Water plants', 2)
    _fake_briefing(monkeypatch, [])
    briefing_job.run_briefing()
    briefing_job.run_briefing(force=True)
    assert _suggestion('title:water plants')['ignored_streak'] == 2


def test_doing_it_resets_the_streak(client, monkeypatch):
    _todo('t1', 'Clean the garage')
    _skip('Clean the garage', 2, source=('todo', 't1'))
    _fake_briefing(monkeypatch, [])
    briefing_job.run_briefing()
    assert _suggestion('todo:t1')['ignored_streak'] == 2

    client.patch('/api/tasks/todos/t1', json={'done': True})
    s = _suggestion('todo:t1')
    assert s['ignored_streak'] == 0 and s['snoozed_until'] is None


def test_three_skips_stage_a_review_card_once(client, monkeypatch):
    _todo('t1', 'Clean the garage')
    _skip('Clean the garage', 3, source=('todo', 't1'))
    _fake_briefing(monkeypatch, [])
    result = briefing_job.run_briefing()

    meta = json.loads(_db().execute('SELECT metadata FROM messages WHERE id=?',
                                    (result['messageId'],)).fetchone()['metadata'])
    reviews = [p for p in meta['proposals'] if p['kind'] == 'todo_review']
    assert [(p['data']['title'], p['data']['streak'], p['data']['choice']) for p in reviews] == [
        ('Clean the garage', 3, 'drop')]
    # Not also carried over as a "keep this for today?" card.
    assert not [p for p in meta['proposals'] if p['kind'] == 'todo']

    result = briefing_job.run_briefing(force=True)
    meta = json.loads(_db().execute('SELECT metadata FROM messages WHERE id=?',
                                    (result['messageId'],)).fetchone()['metadata'])
    assert not [p for p in meta.get('proposals', []) if p['kind'] == 'todo_review']


def _review_card(client, monkeypatch, todo_id='t1', title='Clean the garage'):
    _todo(todo_id, title)
    _skip(title, 3, source=('todo', todo_id))
    _fake_briefing(monkeypatch, [])
    result = briefing_job.run_briefing()
    meta = json.loads(_db().execute('SELECT metadata FROM messages WHERE id=?',
                                    (result['messageId'],)).fetchone()['metadata'])
    card = next(p for p in meta['proposals'] if p['kind'] == 'todo_review')
    return result['messageId'], card


def test_letting_it_go_archives_the_todo_and_stops_suggesting_it(client, monkeypatch):
    message_id, card = _review_card(client, monkeypatch)
    resp = client.post(f'/api/chat/proposals/{message_id}/{card["id"]}', json={'action': 'accept'})
    assert resp.status_code == 200
    assert _db().execute("SELECT list FROM todos WHERE id='t1'").fetchone()['list'] == 'archive'
    assert _suggestion('todo:t1')['dropped_at'] is not None
    assert 'todo:t1' in day_plan.held_back_keys(_db(), _today())


def test_snoozing_holds_it_back_a_week(client, monkeypatch):
    message_id, card = _review_card(client, monkeypatch)
    client.post(f'/api/chat/proposals/{message_id}/{card["id"]}',
                json={'action': 'accept', 'data': {**card['data'], 'choice': 'snooze'}})
    until = _suggestion('todo:t1')['snoozed_until']
    assert until == (date.fromisoformat(_today()) + timedelta(days=8)).isoformat()
    assert _db().execute("SELECT list FROM todos WHERE id='t1'").fetchone()['list'] == 'todo'


def test_breaking_it_down_adds_first_steps_to_the_bar(client, monkeypatch):
    import backend.ai.todo as todo_ai
    monkeypatch.setattr(todo_ai, 'chat_json', lambda *a, **k: {'steps': ['Clear one shelf', 'Bag the trash']})
    message_id, card = _review_card(client, monkeypatch)
    resp = client.post(f'/api/chat/proposals/{message_id}/{card["id"]}',
                       json={'action': 'accept', 'data': {**card['data'], 'choice': 'breakdown'}})
    assert resp.status_code == 200
    assert resp.get_json()['proposal']['result']['steps'] == ['Clear one shelf', 'Bag the trash']
    titles = [r['title'] for r in _db().execute(
        'SELECT title FROM chat_todos WHERE day_key=? ORDER BY created_at, title', (_today(),))]
    assert titles == ['Bag the trash', 'Clear one shelf']
    assert _suggestion('todo:t1')['snoozed_until'] is not None


def test_a_failed_breakdown_leaves_the_card_open(client, monkeypatch):
    import backend.ai.todo as todo_ai

    def boom(*a, **k):
        raise RuntimeError('model down')
    monkeypatch.setattr(todo_ai, 'chat_json', boom)
    message_id, card = _review_card(client, monkeypatch)
    resp = client.post(f'/api/chat/proposals/{message_id}/{card["id"]}',
                       json={'action': 'accept', 'data': {**card['data'], 'choice': 'breakdown'}})
    assert resp.status_code == 502


def test_dismissing_a_bar_item_is_remembered(client):
    _bar_row('c1', 'Water plants')
    client.delete('/api/tasks/chat-todos/c1')
    s = _suggestion('title:water plants')
    assert s['dismissed_at'] is not None and s['ignored_streak'] == 1
    assert 'title:water plants' in day_plan.held_back_keys(_db(), _today())


def test_carry_over_keeps_the_link(client, monkeypatch):
    _todo('t1', 'Renew passport')
    _bar_row('y1', 'Renew passport', day=_days_ago(1), source=('todo', 't1'))
    _fake_briefing(monkeypatch, [{'title': 'Something else', 'priority': 3, 'basis': 'list',
                                  'reason': 'x', 'linkedTitle': None}])
    result = briefing_job.run_briefing()
    meta = json.loads(_db().execute('SELECT metadata FROM messages WHERE id=?',
                                    (result['messageId'],)).fetchone()['metadata'])
    card = next(p for p in meta['proposals'] if p['kind'] == 'todo')
    client.post(f'/api/chat/proposals/{result["messageId"]}/{card["id"]}', json={'action': 'accept'})
    row = _db().execute("SELECT source_type, source_id FROM chat_todos WHERE title='Renew passport'"
                        ' AND day_key=?', (_today(),)).fetchone()
    assert tuple(row) == ('todo', 't1')


# --- review fixes ---

def _repeating_todo(id, title, due):
    _db().execute(
        'INSERT INTO todos(id, title, done, list, priority, due, repeat_interval, repeat_unit,'
        " created_at, updated_at) VALUES (?,?,0,'todo',3,?,1,'week',0,0)", (id, title, due))
    _db().commit()


def _due(id):
    return _db().execute('SELECT due FROM todos WHERE id=?', (id,)).fetchone()['due']


def test_bar_rows_from_before_links_are_not_tallied_as_skips(client):
    # Ticked in Tasks every day, never in the bar — which used to stay unticked.
    for n in range(7, 0, -1):
        _bar_row(f'old-{n}', 'Stretch', day=_days_ago(n))
    connection._mark_legacy_bar_judged(_db())
    briefing_job.tally_previous_days(_db(), _today(), int(time.time()))

    s = _suggestion('title:stretch')
    assert s['ignored_streak'] == 0 and s['snoozed_until'] is None
    assert not day_plan.is_held_back(day_plan.held_back_keys(_db(), _today()), None, None, 'Stretch')


def test_a_repeated_tick_advances_a_repeating_todo_once(client):
    due = int(time.time()) + 86400
    _repeating_todo('t1', 'Water plants', due)
    _bar_row('c1', 'Water plants', source=('todo', 't1'))

    client.patch('/api/tasks/chat-todos/c1', json={'done': True})
    advanced = _due('t1')
    client.patch('/api/tasks/chat-todos/c1', json={'done': True})

    assert advanced > due and _due('t1') == advanced
    assert _events('todo_completed') == 1


def test_unticking_a_repeating_todo_puts_back_only_that_occurrence(client):
    due = int(time.time()) + 86400
    _repeating_todo('t1', 'Water plants', due)
    # A completion from an earlier week, already in the Journal.
    _db().execute("INSERT INTO task_events(id, kind, title, ref_id, created_at)"
                  " VALUES ('e0', 'todo_completed', 'Water plants', 't1', 1000)")
    _db().commit()
    _bar_row('c1', 'Water plants', source=('todo', 't1'))

    client.patch('/api/tasks/chat-todos/c1', json={'done': True})
    assert _events('todo_completed') == 2
    client.patch('/api/tasks/chat-todos/c1', json={'done': False})

    assert _due('t1') == due
    assert _bar_done('c1') == 0
    ids = [r['id'] for r in _db().execute("SELECT id FROM task_events WHERE kind='todo_completed'")]
    assert ids == ['e0']


def test_a_todo_finished_since_yesterday_is_not_carried_over(client, monkeypatch):
    _todo('t1', 'Renew passport', done=1)
    _bar_row('y1', 'Renew passport', day=_days_ago(1), source=('todo', 't1'))
    _fake_briefing(monkeypatch, [])
    result = briefing_job.run_briefing()
    meta = json.loads(_db().execute('SELECT metadata FROM messages WHERE id=?',
                                    (result['messageId'],)).fetchone()['metadata'])
    assert not [p for p in meta.get('proposals', []) if p['kind'] == 'todo']


def test_a_review_for_a_finished_todo_resets_instead_of_asking(client, monkeypatch):
    _todo('t1', 'Clean the garage')
    _skip('Clean the garage', 3, source=('todo', 't1'))
    briefing_job.tally_previous_days(_db(), _today(), int(time.time()))
    _db().execute("UPDATE todos SET done=1 WHERE id='t1'")
    assert briefing_job.stage_todo_reviews(_db(), int(time.time())) == []
    s = _suggestion('todo:t1')
    assert s['ignored_streak'] == 0 and s['dropped_at'] is None


def test_accepting_a_card_for_a_todo_already_in_the_bar_adds_nothing(client):
    from backend.routes.chat import _accept_todo
    _todo('t1', 'Renew passport')
    _bar_row('c1', 'Renew passport', source=('todo', 't1'))

    result = _accept_todo(_db(), {'title': 'renew the passport form', 'sourceType': 'todo',
                                  'sourceId': 't1'}, {})
    assert result == {'id': 'c1'}
    assert _db().execute('SELECT COUNT(*) FROM chat_todos').fetchone()[0] == 1


def test_the_plan_leaves_out_idea_recordings(client):
    now = int(time.time())
    _db().execute("INSERT INTO ideas(id, content, created_at, updated_at) VALUES ('i1', 'x', 0, 0)")
    for id, idea in (('j1', None), ('j2', 'i1')):
        _db().execute('INSERT INTO journal_entries(id, content, created_at, updated_at, idea_id)'
                      ' VALUES (?,?,?,?,?)', (id, 'I need to build the export.', now - 2 * 86400,
                                              now - 2 * 86400, idea))
    _db().commit()
    assert [e['id'] for e in briefing_mod.earlier_journal(_db(), now)] == ['j1']
