"""Tests for the overnight briefing: context gathering (pure DB reads), the
run_briefing sweep (find-or-create day chat, briefing message, proposed todos,
idempotency), and the accept/reject decisions route."""
import json
from datetime import datetime

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


def test_generate_briefing_passes_reasoning_and_max_tokens_from_settings(client, monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, model=None, **kwargs):
        captured.update(kwargs)
        return {'briefing': 'hi', 'todos': []}

    monkeypatch.setattr(briefing_mod, 'chat_json', fake_chat_json)
    ctx = {'now': NOW, 'today': TODAY, 'journal': [], 'daily_tasks': [],
           'todos': [], 'calendar': [], 'learning_due': 0}

    # Defaults: no thinking, generous ceiling, roomy context window.
    briefing_mod.generate_briefing(ctx)
    assert captured['reasoning_effort'] == 'none'
    assert captured['max_tokens'] == briefing_mod.BRIEFING_MAX_TOKENS
    # Same window as the chat model, so sharing it costs no KV re-allocation.
    from backend.ai.llm import LLM_NUM_CTX
    assert captured['num_ctx'] == LLM_NUM_CTX

    # User-configured values flow through.
    db = connection.get_db()
    db.execute(
        'INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,?,?)',
        (NOW, NOW),
    )
    db.execute(
        'UPDATE settings SET briefing_reasoning_effort=?, briefing_max_tokens=?,'
        ' briefing_num_ctx=? WHERE id=1',
        ('high', 8000, 32768),
    )
    db.commit()
    briefing_mod.generate_briefing(ctx)
    assert captured['reasoning_effort'] == 'high'
    assert captured['max_tokens'] == 8000
    assert captured['num_ctx'] == 32768


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
    _insert_todo('existing', 'buy MILK', done=0)  # case-insensitive dupe target
    connection.get_db().commit()

    fake = {
        'briefing': 'hi',
        'todos': [
            {'title': 'Buy milk'},                       # dupe of existing -> skip
            {'title': '   '},                            # blank -> skip
            {'title': 'A', 'priority': 99, 'list': 'x', 'due': 'nope'},  # clamped
            {'title': 'B'}, {'title': 'C'}, {'title': 'D'},
            {'title': 'E'}, {'title': 'F'},              # cap at 5 new
        ],
    }
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: fake)
    result = briefing_job.run_briefing(now=NOW)

    assert result['todosProposed'] == 5  # A,B,C,D,E (F dropped by cap)
    proposals = _proposals(result['messageId'])
    assert [p['title'] for p in proposals] == ['A', 'B', 'C', 'D', 'E']
    a = proposals[0]
    assert a['priority'] == 3      # bad priority -> default
    assert a['list'] == 'todo'     # bad list -> default
    assert a['due'] is None        # bad due -> None
    # 'Buy milk' duplicated an open todo, so it was never proposed.
    assert 'Buy milk' not in [p['title'] for p in proposals]


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


def test_accept_dedupes_against_a_todo_added_after_the_briefing(client, monkeypatch):
    result = _run(monkeypatch)
    msg_id = result['messageId']
    pid = _proposals(msg_id)[0]['id']
    # The user added the same thing by hand in the meantime.
    _insert_todo('manual', 'draft the REPORT', done=0)
    connection.get_db().commit()

    r = client.post(f'/api/chat/briefing/{msg_id}/todos',
                    json={'decisions': [{'id': pid, 'action': 'accept'}]})
    assert r.get_json()['created'] == 0
    assert _proposals(msg_id)[0]['status'] == 'duplicate'
    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 1


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


def test_decisions_route_rejects_bad_input(client, monkeypatch):
    result = _run(monkeypatch)
    msg_id = result['messageId']
    assert client.post(f'/api/chat/briefing/{msg_id}/todos', json={}).status_code == 400
    assert client.post('/api/chat/briefing/nope/todos',
                       json={'decisions': [{'id': 'x', 'action': 'accept'}]}
                       ).status_code == 404
