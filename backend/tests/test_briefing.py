"""Tests for the overnight briefing: context gathering (pure DB reads), the
run_briefing sweep (find-or-create day chat, briefing message, proposed todos,
idempotency), and the accept/reject decisions route."""
import json
from datetime import date, datetime

import pytest

from backend.db import connection
from backend.chat_day import day_key_for
from backend.ai import briefing as briefing_mod
from backend import briefing_job

# A fixed "now": Tuesday 2026-07-14 09:00 local.
NOW = int(datetime(2026, 7, 14, 9, 0).timestamp())
TODAY = datetime.fromtimestamp(NOW).date().isoformat()


# --- fixtures ---

def _insert_journal(id, content, created_at):
    connection.get_db().execute(
        'INSERT INTO journal_entries(id, content, created_at, updated_at) VALUES (?,?,?,?)',
        (id, content, created_at, created_at),
    )


def _insert_daily_task(id, title, position):
    connection.get_db().execute(
        'INSERT INTO daily_tasks(id, title, position, created_at, updated_at) VALUES (?,?,?,?,?)',
        (id, title, position, NOW, NOW),
    )


def _complete_daily_task(id, task_id, date):
    connection.get_db().execute(
        'INSERT INTO daily_task_completions(id, task_id, date, created_at) VALUES (?,?,?,?)',
        (id, task_id, date, NOW),
    )


def _insert_todo(id, title, done=0, priority=3, due=None, todo_list='todo'):
    connection.get_db().execute(
        'INSERT INTO todos(id, title, done, list, priority, due, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?,?)',
        (id, title, done, todo_list, priority, due, NOW, NOW),
    )


def _insert_event(id, title, date, time=None, end_time=None,
                  freq=None, byweekday=None):
    connection.get_db().execute(
        'INSERT INTO calendar_events(id, title, date, time, end_time, created_at,'
        ' repeat_freq, repeat_byweekday) VALUES (?,?,?,?,?,?,?,?)',
        (id, title, date, time, end_time, NOW, freq, byweekday),
    )


def _insert_card(id, due, state='active'):
    connection.get_db().execute(
        'INSERT INTO learning_cards(id, question, answer, state, due, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (id, 'q', 'a', state, due, NOW, NOW),
    )


# --- gather_briefing_context ---

def test_gather_context_includes_and_excludes(client):
    _insert_journal('j_recent', 'Working on the overnight agent.', NOW - 3600)
    _insert_journal('j_old', 'Ancient history.', NOW - 5 * 86400)

    _insert_daily_task('dt_pending', 'Meditate', 1)
    _insert_daily_task('dt_done', 'Exercise', 2)
    _complete_daily_task('c1', 'dt_done', TODAY)

    _insert_todo('todo_open', 'Buy milk', done=0)
    _insert_todo('todo_done', 'Old thing', done=1)
    _insert_todo('todo_chore', 'Sweep floor', done=0, todo_list='chores')
    _insert_todo('todo_archived', 'Set aside', done=0, todo_list='archive')

    _insert_event('e_today', 'Standup', TODAY)
    _insert_event('e_far', 'Someday', '2027-01-01')

    _insert_card('card_due', NOW - 10)      # due
    _insert_card('card_future', NOW + 86400)  # not due
    _insert_card('card_pending', NOW - 10, state='pending')  # not active
    connection.get_db().commit()

    ctx = briefing_mod.gather_briefing_context(NOW)

    assert [e['content'] for e in ctx['journal']] == ['Working on the overnight agent.']
    assert [t['title'] for t in ctx['daily_tasks']] == ['Meditate']
    # Open todos + chores are included; done and archived are excluded.
    assert sorted(t['title'] for t in ctx['todos']) == ['Buy milk', 'Sweep floor']
    assert [e['title'] for e in ctx['calendar']] == ['Standup']
    assert ctx['learning_due'] == 1


def test_gather_context_expands_recurring_events(client):
    """A weekday series is a single row; the briefing must still see it on the
    days it actually falls on, not only on its anchor date."""
    # Anchored on Wednesday 2026-07-01, repeating Mon-Fri.
    _insert_event('work', 'Work', '2026-07-01', '09:00', '17:00',
                  freq='weekly', byweekday='1,2,3,4,5')
    connection.get_db().commit()

    ctx = briefing_mod.gather_briefing_context(NOW)
    # NOW is Tuesday the 14th; the lookahead reaches Friday the 17th.
    assert [(e['date'], e['title']) for e in ctx['calendar']] == [
        ('2026-07-14', 'Work'), ('2026-07-15', 'Work'),
        ('2026-07-16', 'Work'), ('2026-07-17', 'Work'),
    ]

    prompt = briefing_mod.build_briefing_prompt(ctx)
    assert '- 2026-07-14 09:00–17:00: Work' in prompt


def test_gather_context_honours_skipped_occurrences(client):
    _insert_event('work', 'Work', '2026-07-01', '09:00', '17:00',
                  freq='weekly', byweekday='1,2,3,4,5')
    connection.get_db().execute(
        "INSERT INTO calendar_event_exceptions(id, event_id, date, action, created_at)"
        " VALUES ('x','work','2026-07-15','skip',?)", (NOW,))
    connection.get_db().commit()

    ctx = briefing_mod.gather_briefing_context(NOW)
    assert '2026-07-15' not in [e['date'] for e in ctx['calendar']]


def test_generate_briefing_uses_model_override(client, monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, model=None, **kwargs):
        captured['model'] = model
        captured['max_tokens'] = kwargs.get('max_tokens')
        return {'briefing': 'hi', 'todos': []}

    monkeypatch.setattr(briefing_mod, 'chat_json', fake_chat_json)

    # No setting -> falls back to the default chat model (None passed).
    briefing_mod.generate_briefing({'now': NOW, 'today': TODAY, 'journal': [],
                                    'daily_tasks': [], 'todos': [], 'calendar': [],
                                    'learning_due': 0})
    assert captured['model'] is None
    # The overnight briefing gets a generous token ceiling, not the tight default.
    assert captured['max_tokens'] == briefing_mod.BRIEFING_MAX_TOKENS

    # Setting present -> that model is used.
    db = connection.get_db()
    if db.execute('SELECT 1 FROM settings LIMIT 1').fetchone():
        db.execute('UPDATE settings SET briefing_model=? WHERE id=1', ('llama3.1:70b',))
    else:
        db.execute(
            'INSERT INTO settings(id, briefing_model, created_at, updated_at) VALUES (1,?,?,?)',
            ('llama3.1:70b', NOW, NOW),
        )
    db.commit()
    briefing_mod.generate_briefing({'now': NOW, 'today': TODAY, 'journal': [],
                                    'daily_tasks': [], 'todos': [], 'calendar': [],
                                    'learning_due': 0})
    assert captured['model'] == 'llama3.1:70b'


def test_generate_briefing_passes_thinking_and_max_tokens_from_settings(client, monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, model=None, **kwargs):
        captured.update(kwargs)
        return {'briefing': 'hi', 'todos': []}

    monkeypatch.setattr(briefing_mod, 'chat_json', fake_chat_json)
    ctx = {'now': NOW, 'today': TODAY, 'journal': [], 'daily_tasks': [],
           'todos': [], 'calendar': [], 'learning_due': 0}

    # Defaults: no thinking, generous ceiling.
    briefing_mod.generate_briefing(ctx)
    assert captured['thinking'] is False
    assert captured['max_tokens'] == briefing_mod.BRIEFING_MAX_TOKENS
    # The completion is grammar-constrained to the briefing's shape, which is what
    # makes FALLBACK_BRIEFING a rare path rather than a routine one.
    assert captured['schema'] is briefing_mod.BRIEFING_SCHEMA
    # No context window is ever passed: llama-server fixes it at load time.
    assert 'num_ctx' not in captured

    # User-configured values flow through.
    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,?,?)',
        (NOW, NOW),
    )
    db.execute(
        'UPDATE settings SET briefing_thinking=?, briefing_max_tokens=? WHERE id=1',
        (1, 8000),
    )
    db.commit()
    briefing_mod.generate_briefing(ctx)
    assert captured['thinking'] is True
    assert captured['max_tokens'] == 8000
    assert 'num_ctx' not in captured


def test_build_prompt_is_pure_and_mentions_data(client):
    ctx = {
        'now': NOW, 'today': TODAY, 'goals': '',
        'journal': [], 'daily_tasks': [{'title': 'Meditate'}],
        'todos': [{'title': 'Buy milk', 'due': None, 'priority': 3, 'list': 'todo'}],
        'calendar': [], 'learning_due': 2,
    }
    prompt = briefing_mod.build_briefing_prompt(ctx)
    assert 'Meditate' in prompt
    assert 'Buy milk' in prompt
    assert '2' in prompt  # learning due count


def test_goals_flow_into_context_and_prompt(client):
    db = connection.get_db()
    db.execute(
        'UPDATE settings SET briefing_goals=? WHERE id=1',
        ('Ship the overnight agent; get back into running.',),
    )
    db.commit()

    ctx = briefing_mod.gather_briefing_context(NOW)
    assert ctx['goals'] == 'Ship the overnight agent; get back into running.'

    prompt = briefing_mod.build_briefing_prompt(ctx)
    assert 'get back into running' in prompt


def test_empty_goals_omitted_from_prompt(client):
    ctx = {
        'now': NOW, 'today': TODAY, 'goals': '   ',
        'journal': [], 'daily_tasks': [], 'todos': [], 'calendar': [],
        'learning_due': 0,
    }
    prompt = briefing_mod.build_briefing_prompt(ctx)
    assert 'stated goals' not in prompt


# --- run_briefing ---

_FAKE = {
    'briefing': '## Morning\n- Do the thing',
    'todos': [
        {'title': 'Draft the report', 'priority': 4, 'list': 'todo', 'due': None},
    ],
}


def _briefing_msgs(conv_id):
    return connection.get_db().execute(
        "SELECT content, metadata FROM messages WHERE conversation_id=? AND role='assistant'",
        (conv_id,),
    ).fetchall()


def test_run_briefing_writes_message_and_proposes_todos(client, monkeypatch):
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: dict(_FAKE))
    result = briefing_job.run_briefing(now=NOW)

    assert result is not None
    assert result['todosProposed'] == 1
    conv_id = result['conversationId']

    # The conversation is keyed to today's chat day.
    row = connection.get_db().execute(
        'SELECT day_key FROM conversations WHERE id=?', (conv_id,)
    ).fetchone()
    assert row['day_key'] == day_key_for(NOW)

    msgs = _briefing_msgs(conv_id)
    assert len(msgs) == 1
    assert msgs[0]['content'] == _FAKE['briefing']
    meta = json.loads(msgs[0]['metadata'])
    assert meta['briefing'] is True
    assert [(p['title'], p['priority'], p['status']) for p in meta['proposedTodos']] == [
        ('Draft the report', 4, 'pending')
    ]

    # Nothing lands in the to-do list until the user accepts.
    count = connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0]
    assert count == 0


def test_run_briefing_is_idempotent(client, monkeypatch):
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: dict(_FAKE))
    first = briefing_job.run_briefing(now=NOW)
    assert first is not None

    second = briefing_job.run_briefing(now=NOW)
    assert second is None  # already briefed today

    conv_id = first['conversationId']
    assert len(_briefing_msgs(conv_id)) == 1

    # force=True re-runs.
    forced = briefing_job.run_briefing(now=NOW, force=True)
    assert forced is not None
    assert len(_briefing_msgs(conv_id)) == 2


def test_run_briefing_validates_dedupes_and_caps(client, monkeypatch):
    _insert_todo('existing', 'buy MILK', done=0)  # case-insensitive twin target
    connection.get_db().commit()

    fake = {
        'briefing': 'hi',
        'todos': [
            {'title': 'Buy milk'},                       # twin of existing -> linked
            {'title': '   '},                            # blank -> skip
            {'title': 'A', 'priority': 99, 'list': 'x', 'due': 'nope'},  # clamped
            {'title': 'A'},                              # same-batch repeat -> skip
            {'title': 'B'}, {'title': 'C'}, {'title': 'D'},
            {'title': 'E'},                              # cap at 5
        ],
    }
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: fake)
    result = briefing_job.run_briefing(now=NOW)

    assert result['todosProposed'] == 5
    proposals = _proposals(result['messageId'])
    assert [p['title'] for p in proposals] == ['Buy milk', 'A', 'B', 'C', 'D']
    a = proposals[1]
    assert a['priority'] == 3      # bad priority -> default
    assert a['list'] == 'todo'     # bad list -> default
    assert a['due'] is None        # bad due -> None
    assert a['linkedId'] is None   # genuinely new
    # 'Buy milk' restates an open todo, so it's kept and tied to it.
    assert (proposals[0]['linkedType'], proposals[0]['linkedId']) == ('todo', 'existing')


# --- the plan never comes back empty ---

def test_empty_model_plan_falls_back_to_the_users_lists(client, monkeypatch):
    """The model sometimes writes its plan into the check-in prose and returns an
    empty todos array, which renders as no plan at all. Fall back to the lists."""
    _insert_daily_task('d_job', 'job search', 1)
    _insert_daily_task('d_code', 'write code constantly', 2)
    _insert_daily_task('d_done', 'already handled', 3)
    _complete_daily_task('c1', 'd_done', TODAY)
    _insert_todo('t_low', 'Someday maybe', priority=1)
    _insert_todo('t_stale', 'Rotting in the backlog', priority=1, due=NOW - 5 * 86400)
    _insert_todo('t_due', 'Call the recruiter back', priority=2, due=NOW)
    _insert_todo('t_high', 'Prep for the interview', priority=5)
    _insert_todo('t_arch', 'Set aside', priority=5, todo_list='archive')
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'Morning!', 'todos': []})

    assert result['todosProposed'] == 5
    assert result['degraded'] is False
    proposals = _proposals(result['messageId'])
    # Daily tasks first, then to-dos by priority — the week-old P1 doesn't get to
    # push the P5 out of the plan just for being overdue.
    assert [p['title'] for p in proposals] == [
        'job search', 'write code constantly',
        'Prep for the interview', 'Call the recruiter back',
        'Rotting in the backlog',
    ]
    # Every fallback item ties back to the row it came from, so crossing one off
    # completes the real one.
    assert [(p['linkedType'], p['linkedId']) for p in proposals] == [
        ('daily', 'd_job'), ('daily', 'd_code'),
        ('todo', 't_high'), ('todo', 't_due'), ('todo', 't_stale'),
    ]
    # A completed daily task and the archive list stay out of it.
    titles = [p['title'] for p in proposals]
    assert 'already handled' not in titles
    assert 'Set aside' not in titles

    # Still a proposal, not a to-do: nothing new was written to the lists.
    assert connection.get_db().execute(
        'SELECT COUNT(*) FROM todos').fetchone()[0] == 5


def test_empty_model_plan_stays_empty_when_nothing_is_pending(client, monkeypatch):
    """Nothing to do is a real answer — don't invent one."""
    result = _run(monkeypatch, {'briefing': 'Morning!', 'todos': []})
    assert result['todosProposed'] == 0
    assert _proposals(result['messageId']) == []


def test_a_partial_model_plan_is_left_alone(client, monkeypatch):
    """The fallback only fires on an *empty* plan; one real item is a plan."""
    _insert_daily_task('d_job', 'job search', 1)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [{'title': 'Only this'}]})
    assert [p['title'] for p in _proposals(result['messageId'])] == ['Only this']


def test_fallback_plan_is_pure_and_ordered(client):
    """Ordering is decided without touching the DB, so it can be reasoned about:
    daily tasks, then priority, with the due date only breaking ties."""
    ctx = {
        'now': NOW, 'today': TODAY, 'goals': '', 'journal': [], 'calendar': [],
        'learning_due': 0,
        'daily_tasks': [{'id': 'd1', 'title': 'Meditate'}],
        'todos': [
            {'title': 'High, undated', 'priority': 5, 'list': 'todo', 'due': None},
            {'title': 'Middling', 'priority': 4, 'list': 'todo', 'due': NOW + 86400},
            {'title': 'Low, due today', 'priority': 1, 'list': 'chores', 'due': NOW},
            {'title': 'Low, overdue', 'priority': 1, 'list': 'todo',
             'due': NOW - 86400},
            {'title': 'Low, undated', 'priority': 1, 'list': 'todo', 'due': None},
        ],
    }
    plan = briefing_mod.fallback_plan(ctx)
    assert [i['title'] for i in plan] == [
        'Meditate', 'High, undated', 'Middling',
        # Equal priority: soonest due first, undated last.
        'Low, overdue', 'Low, due today', 'Low, undated',
    ]
    # Shaped like the model's own output, so it validates and links the same way.
    assert plan[0] == {'title': 'Meditate', 'priority': 4, 'list': 'todo',
                       'due': None, 'linkedTitle': 'Meditate'}
    assert plan[4] == {'title': 'Low, due today', 'priority': 1, 'list': 'chores',
                       'due': NOW, 'linkedTitle': 'Low, due today'}


# --- an unusable completion ---

def _raise_empty(ctx):
    from backend.ai.llm import EmptyCompletion
    raise EmptyCompletion('model returned empty content for a JSON request')


def test_unusable_completion_still_leaves_a_plan_overnight(client, monkeypatch):
    """A truncated or empty completion used to mean the 4am run left nothing at
    all. The check-in says so; the plan is still real."""
    _insert_daily_task('d_job', 'job search', 1)
    connection.get_db().commit()
    monkeypatch.setattr(briefing_job, 'generate_briefing', _raise_empty)

    result = briefing_job.run_briefing(now=NOW)

    assert result is not None
    assert result['degraded'] is True
    assert result['briefing'] == briefing_mod.FALLBACK_BRIEFING
    proposals = _proposals(result['messageId'])
    assert [(p['title'], p['linkedType']) for p in proposals] == [('job search', 'daily')]
    meta = json.loads(connection.get_db().execute(
        'SELECT metadata FROM messages WHERE id=?', (result['messageId'],)
    ).fetchone()['metadata'])
    assert meta['briefing'] is True and meta['degraded'] is True


def test_unusable_completion_writes_nothing_when_nothing_is_pending(client, monkeypatch):
    """No prose and no plan is not worth waking up to."""
    monkeypatch.setattr(briefing_job, 'generate_briefing', _raise_empty)
    assert briefing_job.run_briefing(now=NOW) is None
    assert connection.get_db().execute(
        "SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0] == 0


def test_unusable_completion_still_raises_on_a_manual_run(client, monkeypatch):
    """The route turns this into a readable 502 — see the route test below."""
    from backend.ai.llm import EmptyCompletion

    _insert_daily_task('d_job', 'job search', 1)
    connection.get_db().commit()
    monkeypatch.setattr(briefing_job, 'generate_briefing', _raise_empty)

    with pytest.raises(EmptyCompletion):
        briefing_job.run_briefing(now=NOW, force=True)


def test_run_briefing_skips_when_ai_unconfigured(client, monkeypatch):
    monkeypatch.setattr(briefing_job, 'is_ai_configured', lambda: False)
    assert briefing_job.run_briefing(now=NOW) is None


def test_run_briefing_route_forces(client, monkeypatch):
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: dict(_FAKE))
    r = client.post('/api/chat/briefing/run')
    assert r.status_code == 200
    body = r.get_json()
    assert body['todosProposed'] == 1
    assert body['briefing'] == _FAKE['briefing']


def test_run_briefing_route_reports_empty_completion(client, monkeypatch):
    """An empty JSON-mode completion surfaces as a readable 502, not a 500."""
    from backend.ai.llm import EmptyCompletion

    def boom(ctx):
        raise EmptyCompletion('model returned empty content for a JSON request')

    monkeypatch.setattr(briefing_job, 'generate_briefing', boom)
    r = client.post('/api/chat/briefing/run')
    assert r.status_code == 502
    assert 'no usable briefing' in r.get_json()['error']


# --- accepting / rejecting the proposals ---

def _proposals(message_id):
    row = connection.get_db().execute(
        'SELECT metadata FROM messages WHERE id=?', (message_id,)
    ).fetchone()
    return json.loads(row['metadata'])['proposedTodos']


def _run(monkeypatch, fake=None):
    monkeypatch.setattr(briefing_job, 'generate_briefing',
                        lambda ctx: dict(fake or _FAKE))
    return briefing_job.run_briefing(now=NOW)


def test_accepting_a_proposal_creates_the_todo(client, monkeypatch):
    result = _run(monkeypatch)
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    r = client.post(f'/api/chat/briefing/{msg_id}/todos',
                    json={'decisions': [{'id': pid, 'action': 'accept'}]})
    assert r.status_code == 200
    assert r.get_json()['created'] == 1

    todos = connection.get_db().execute(
        'SELECT id, title, priority FROM todos'
    ).fetchall()
    # The proposal id becomes the todo id, so a re-accept can't duplicate it.
    assert [(t['id'], t['title'], t['priority']) for t in todos] == [
        (pid, 'Draft the report', 4)
    ]
    assert _proposals(msg_id)[0]['status'] == 'accepted'

    # A resolved card is inert.
    again = client.post(f'/api/chat/briefing/{msg_id}/todos',
                        json={'decisions': [{'id': pid, 'action': 'accept'}]})
    assert again.get_json()['created'] == 0
    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 1


def test_accepting_applies_inline_edits(client, monkeypatch):
    result = _run(monkeypatch)
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    client.post(f'/api/chat/briefing/{msg_id}/todos', json={'decisions': [{
        'id': pid, 'action': 'accept', 'title': 'Draft the Q3 report',
        'priority': 2, 'due': NOW + 86400, 'list': 'chores',
    }]})

    row = connection.get_db().execute(
        'SELECT title, priority, due, list FROM todos WHERE id=?', (pid,)
    ).fetchone()
    assert (row['title'], row['priority'], row['due'], row['list']) == (
        'Draft the Q3 report', 2, NOW + 86400, 'chores'
    )
    assert _proposals(msg_id)[0]['title'] == 'Draft the Q3 report'


def test_rejecting_a_proposal_creates_nothing(client, monkeypatch):
    result = _run(monkeypatch)
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    r = client.post(f'/api/chat/briefing/{msg_id}/todos',
                    json={'decisions': [{'id': pid, 'action': 'reject'}]})
    assert r.get_json()['created'] == 0
    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 0
    assert _proposals(msg_id)[0]['status'] == 'rejected'


def test_accept_links_to_a_todo_added_after_the_briefing(client, monkeypatch):
    """The list moved on since the briefing. Accepting links the card to the new
    row and leaves it pending — a twin you can cross off beats a dead card."""
    result = _run(monkeypatch)
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']
    # The user added the same thing by hand in the meantime.
    _insert_todo('manual', 'draft the REPORT', done=0)
    connection.get_db().commit()

    r = client.post(f'/api/chat/briefing/{msg_id}/todos',
                    json={'decisions': [{'id': pid, 'action': 'accept'}]})
    assert r.get_json()['created'] == 0
    item = _proposals(msg_id)[0]
    assert item['status'] == 'pending'
    assert (item['linkedType'], item['linkedId'], item['linkedTitle']) == (
        'todo', 'manual', 'draft the REPORT'
    )
    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 1

    # And now crossing it off completes the real row.
    client.post(f'/api/chat/briefing/{msg_id}/todos',
                json={'decisions': [{'id': pid, 'action': 'done'}]})
    assert connection.get_db().execute(
        'SELECT done FROM todos WHERE id=?', ('manual',)
    ).fetchone()['done'] == 1


def test_bulk_accept_resolves_every_pending_proposal(client, monkeypatch):
    fake = {'briefing': 'hi', 'todos': [{'title': 'A'}, {'title': 'B'}, {'title': 'C'}]}
    result = _run(monkeypatch, fake)
    msg_id = result['messageId']
    ids = [p['id'] for p in _proposals(msg_id)]

    r = client.post(f'/api/chat/briefing/{msg_id}/todos', json={
        'decisions': [{'id': ids[0], 'action': 'reject'}]
    })
    assert r.get_json()['created'] == 0

    r = client.post(f'/api/chat/briefing/{msg_id}/todos', json={
        'decisions': [{'id': i, 'action': 'accept'} for i in ids]
    })
    # The already-rejected one stays rejected.
    assert r.get_json()['created'] == 2
    assert [p['status'] for p in _proposals(msg_id)] == [
        'rejected', 'accepted', 'accepted'
    ]


# --- linking a proposal to its twin ---

def _events(kind=None):
    q = 'SELECT kind, title, ref_id, task_list FROM task_events'
    params = ()
    if kind:
        q += ' WHERE kind=?'
        params = (kind,)
    return connection.get_db().execute(q + ' ORDER BY created_at', params).fetchall()


def test_linked_title_ties_a_proposal_to_an_open_todo(client, monkeypatch):
    """The model paraphrases; the link is what makes them the same task."""
    _insert_todo('groceries', 'Get groceries', done=0)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Buy groceries', 'linkedTitle': 'Get groceries'},
    ]})
    item = _proposals(result['messageId'])[0]
    assert item['title'] == 'Buy groceries'
    assert (item['linkedType'], item['linkedId'], item['linkedTitle']) == (
        'todo', 'groceries', 'Get groceries'
    )


def test_linked_title_ties_a_proposal_to_a_pending_daily_task(client, monkeypatch):
    _insert_daily_task('dt_stretch', 'Stretch', 1)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Do your stretches', 'linkedTitle': 'Stretch'},
    ]})
    item = _proposals(result['messageId'])[0]
    assert (item['linkedType'], item['linkedId']) == ('daily', 'dt_stretch')


def test_a_daily_task_already_done_today_is_not_a_twin(client, monkeypatch):
    """Only *pending* daily tasks are candidates — one already ticked off today
    shouldn't get re-linked and re-completed."""
    _insert_daily_task('dt_stretch', 'Stretch', 1)
    _complete_daily_task('c1', 'dt_stretch', TODAY)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Stretch', 'linkedTitle': 'Stretch'},
    ]})
    assert _proposals(result['messageId'])[0]['linkedId'] is None


def test_unresolvable_linked_title_falls_back_then_gives_up(client, monkeypatch):
    _insert_todo('report', 'Draft the report', done=0)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        # Hallucinated link, but the title itself matches -> fall back to it.
        {'title': 'Draft the report', 'linkedTitle': 'Something imaginary'},
        # Nothing matches either way -> unlinked.
        {'title': 'Call the vet', 'linkedTitle': 'Also imaginary'},
    ]})
    a, b = _proposals(result['messageId'])
    assert (a['linkedType'], a['linkedId'], a['linkedTitle']) == (
        'todo', 'report', 'Draft the report'
    )
    assert (b['linkedType'], b['linkedId'], b['linkedTitle']) == (None, None, None)


def test_linked_title_copied_with_prompt_annotations_still_links(client, monkeypatch):
    """The prompt renders existing items as "Title (due X) [priority 4/5]" and asks
    for the title back verbatim, so the model hands back the decorated line. Left
    alone that matches nothing, and the "cross it off here, cross it off there"
    promise quietly breaks — the title fallback only rescues it when the proposal
    restates the title word for word, which is exactly when the model paraphrases.
    """
    _insert_todo('moe', 'Review the MoE placement doc', done=0)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        # Paraphrased title AND a decorated link — neither lookup works untreated.
        {'title': 'Read the MoE doc',
         'linkedTitle': 'Review the MoE placement doc [priority 4/5]'},
    ]})
    item = _proposals(result['messageId'])[0]
    assert (item['linkedType'], item['linkedId'], item['linkedTitle']) == (
        'todo', 'moe', 'Review the MoE placement doc'
    )


def test_linked_title_strips_several_annotations(client, monkeypatch):
    _insert_todo('moe', 'Review the MoE placement doc', done=0)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Doc review',
         'linkedTitle': 'Review the MoE placement doc (due 2026-07-30) [priority 4/5] [work]'},
    ]})
    assert _proposals(result['messageId'])[0]['linkedId'] == 'moe'


def test_a_title_that_really_contains_brackets_is_not_mangled(client, monkeypatch):
    """Stripping runs only after an exact match fails, so a genuine bracketed
    title keeps its brackets rather than being truncated into a wrong match."""
    _insert_todo('parser', 'Fix [urgent] parser bug', done=0)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Parser fix', 'linkedTitle': 'Fix [urgent] parser bug'},
    ]})
    item = _proposals(result['messageId'])[0]
    assert (item['linkedId'], item['linkedTitle']) == ('parser', 'Fix [urgent] parser bug')


def test_annotation_stripping_does_not_invent_a_link(client, monkeypatch):
    """Stripping must not turn an unmatchable link into a false positive."""
    _insert_todo('report', 'Draft the report', done=0)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Call the vet', 'linkedTitle': 'Something imaginary [priority 4/5]'},
    ]})
    item = _proposals(result['messageId'])[0]
    assert (item['linkedType'], item['linkedId'], item['linkedTitle']) == (None, None, None)


def test_a_done_todo_is_not_a_twin(client, monkeypatch):
    _insert_todo('old', 'Draft the report', done=1)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Draft the report'},
    ]})
    assert _proposals(result['messageId'])[0]['linkedId'] is None


# --- crossing items off the plan ---

def _cross_off(client, msg_id, pid):
    return client.post(f'/api/chat/briefing/{msg_id}/todos',
                       json={'decisions': [{'id': pid, 'action': 'done'}]})


def test_crossing_off_a_linked_todo_completes_the_real_row(client, monkeypatch):
    _insert_todo('groceries', 'Get groceries', done=0)
    connection.get_db().commit()
    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Buy groceries', 'linkedTitle': 'Get groceries'},
    ]})
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    r = _cross_off(client, msg_id, pid)
    assert r.status_code == 200
    assert r.get_json()['created'] == 0

    row = connection.get_db().execute(
        'SELECT done, completed_at FROM todos WHERE id=?', ('groceries',)
    ).fetchone()
    assert row['done'] == 1
    assert row['completed_at'] is not None
    # Exactly one completion event, against the real todo.
    events = _events('todo_completed')
    assert [(e['title'], e['ref_id']) for e in events] == [('Get groceries', 'groceries')]

    item = _proposals(msg_id)[0]
    assert item['status'] == 'done'
    assert item['resolvedAt'] is not None
    # No second todo row was ever created.
    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 1


def test_crossing_off_a_linked_daily_task_records_todays_completion(client, monkeypatch):
    _insert_daily_task('dt_stretch', 'Stretch', 1)
    connection.get_db().commit()
    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Do your stretches', 'linkedTitle': 'Stretch'},
    ]})
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    _cross_off(client, msg_id, pid)

    rows = connection.get_db().execute(
        'SELECT task_id, date FROM daily_task_completions'
    ).fetchall()
    # Dated by when it was crossed off, not by when the briefing was written —
    # a daily task completes for the day you actually did it.
    assert [(r['task_id'], r['date']) for r in rows] == [
        ('dt_stretch', date.today().isoformat())
    ]
    assert [(e['title'], e['task_list']) for e in _events('daily_completed')] == [
        ('Stretch', 'daily')
    ]
    assert _proposals(msg_id)[0]['status'] == 'done'


def test_crossing_off_a_linked_repeating_todo_advances_it(client, monkeypatch):
    connection.get_db().execute(
        'INSERT INTO todos(id, title, done, list, priority, due, repeat_interval,'
        ' repeat_unit, created_at, updated_at) VALUES (?,?,0,?,?,?,?,?,?,?)',
        ('water', 'Water the plants', 'chores', 3, NOW, 1, 'week', NOW, NOW),
    )
    connection.get_db().commit()
    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Water the plants'},
    ]})
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    _cross_off(client, msg_id, pid)

    row = connection.get_db().execute(
        'SELECT done, due FROM todos WHERE id=?', ('water',)
    ).fetchone()
    # Repeating: still open, rolled forward a week rather than finished.
    assert row['done'] == 0
    assert row['due'] > NOW
    assert len(_events('todo_completed')) == 1


def test_crossing_off_an_unlinked_item_logs_an_event_but_creates_no_todo(client, monkeypatch):
    """The whole point of the daily plan: today's work counts without bloating
    the backlog."""
    result = _run(monkeypatch)
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    _cross_off(client, msg_id, pid)

    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 0
    assert [(e['title'], e['ref_id'], e['task_list']) for e in _events('todo_completed')] == [
        ('Draft the report', None, 'todo')
    ]
    assert _proposals(msg_id)[0]['status'] == 'done'


def test_a_crossed_off_item_is_inert(client, monkeypatch):
    _insert_todo('groceries', 'Get groceries', done=0)
    connection.get_db().commit()
    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Buy groceries', 'linkedTitle': 'Get groceries'},
    ]})
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    _cross_off(client, msg_id, pid)
    first_resolved = _proposals(msg_id)[0]['resolvedAt']
    # A second decision can't flip it, re-log it, or re-stamp it.
    r = client.post(f'/api/chat/briefing/{msg_id}/todos',
                    json={'decisions': [{'id': pid, 'action': 'reject'}]})
    assert r.status_code == 200
    item = _proposals(msg_id)[0]
    assert item['status'] == 'done'
    assert item['resolvedAt'] == first_resolved
    assert len(_events('todo_completed')) == 1


def test_rejecting_stamps_resolved_at(client, monkeypatch):
    result = _run(monkeypatch)
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    client.post(f'/api/chat/briefing/{msg_id}/todos',
                json={'decisions': [{'id': pid, 'action': 'reject'}]})
    item = _proposals(msg_id)[0]
    assert item['status'] == 'rejected'
    assert item['resolvedAt'] is not None
    assert _events() == []  # dismissing isn't doing


def test_accepting_a_linked_item_is_a_no_op(client, monkeypatch):
    """It's already on a list — 'add to to-dos' would be the bloat we're avoiding.
    The card stays pending so it can still be crossed off."""
    _insert_todo('groceries', 'Get groceries', done=0)
    connection.get_db().commit()
    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [
        {'title': 'Buy groceries', 'linkedTitle': 'Get groceries'},
    ]})
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']

    r = client.post(f'/api/chat/briefing/{msg_id}/todos',
                    json={'decisions': [{'id': pid, 'action': 'accept'}]})
    assert r.get_json()['created'] == 0
    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 1
    assert _proposals(msg_id)[0]['status'] == 'pending'


def test_decisions_route_rejects_bad_input(client, monkeypatch):
    result = _run(monkeypatch)
    msg_id = result['messageId']
    assert client.post(f'/api/chat/briefing/{msg_id}/todos', json={}).status_code == 400
    assert client.post('/api/chat/briefing/nope/todos',
                       json={'decisions': [{'id': 'x', 'action': 'accept'}]}
                       ).status_code == 404
