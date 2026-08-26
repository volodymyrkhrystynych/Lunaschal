"""Tests for the overnight briefing: context gathering (pure DB reads), the
run_briefing sweep (find-or-create day chat, briefing message, seeding
chat_todos, idempotency)."""
import json
from datetime import date, datetime

import pytest

from backend.db import connection
from backend.day_boundary import day_key_for
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
    _insert_todo('todo_second', 'Sweep floor', done=0)
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
    # Open todos are included; done and archived are excluded.
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


def _chat_todos():
    return connection.get_db().execute(
        'SELECT id, title, priority, due, day_key FROM chat_todos ORDER BY created_at'
    ).fetchall()


def _run(monkeypatch, fake=None):
    monkeypatch.setattr(briefing_job, 'generate_briefing',
                        lambda ctx: dict(fake or _FAKE))
    return briefing_job.run_briefing(now=NOW)


def test_run_briefing_writes_message_and_seeds_chat_todos(client, monkeypatch):
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: dict(_FAKE))
    result = briefing_job.run_briefing(now=NOW)

    assert result is not None
    assert result['todosAdded'] == 1
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
    assert 'proposedTodos' not in meta

    # The item lands straight in chat_todos — no accept step.
    rows = _chat_todos()
    assert [(r['title'], r['priority'], r['day_key']) for r in rows] == [
        ('Draft the report', 4, day_key_for(NOW))
    ]

    # Nothing is written to the permanent to-do list.
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

    # force=True re-runs the briefing message, but the title was already
    # seeded by the first run so nothing new lands in the bar.
    forced = briefing_job.run_briefing(now=NOW, force=True)
    assert forced is not None
    assert len(_briefing_msgs(conv_id)) == 2
    assert forced['todosAdded'] == 0
    assert len(_chat_todos()) == 1


def test_run_briefing_validates_dedupes_and_caps(client, monkeypatch):
    """The plan is deliberately allowed to restate an open todo or a pending
    daily task — see test_empty_model_plan_falls_back_to_the_users_lists for
    that. This one covers the validation/clamping and per-batch dedup that
    apply regardless of what the titles are."""
    fake = {
        'briefing': 'hi',
        'todos': [
            {'title': '   '},                            # blank -> skip
            {'title': 'A', 'priority': 99, 'list': 'x', 'due': 'nope'},  # clamped
            {'title': 'A'},                              # same-batch repeat -> skip
            {'title': 'B'}, {'title': 'C'}, {'title': 'D'},
            {'title': 'E'}, {'title': 'F'},               # cap at 5
        ],
    }
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: fake)
    result = briefing_job.run_briefing(now=NOW)

    assert result['todosAdded'] == 5
    rows = _chat_todos()
    assert [r['title'] for r in rows] == ['A', 'B', 'C', 'D', 'E']
    a = rows[0]
    assert a['priority'] == 3      # bad priority -> default
    assert a['due'] is None        # bad due -> None


def test_run_briefing_skips_a_title_already_added_earlier_today(client, monkeypatch):
    """A chat-added to-do earlier today and a briefing item that restates it
    shouldn't both land in the bar — _today_taken_titles covers the chat_todos
    table itself, not just the permanent lists."""
    connection.get_db().execute(
        'INSERT INTO chat_todos(id, day_key, title, priority, due, done, created_at, updated_at)'
        " VALUES ('c1', ?, 'Buy milk', 3, NULL, 0, ?, ?)",
        (day_key_for(NOW), NOW, NOW),
    )
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [{'title': 'Buy milk'}]})
    assert result['todosAdded'] == 0
    assert len(_chat_todos()) == 1


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

    assert result['todosAdded'] == 5
    assert result['degraded'] is False
    rows = _chat_todos()
    # Daily tasks first, then to-dos by priority — the week-old P1 doesn't get to
    # push the P5 out of the plan just for being overdue.
    assert [r['title'] for r in rows] == [
        'job search', 'write code constantly',
        'Prep for the interview', 'Call the recruiter back',
        'Rotting in the backlog',
    ]
    # A completed daily task and the archive list stay out of it.
    titles = [r['title'] for r in rows]
    assert 'already handled' not in titles
    assert 'Set aside' not in titles

    # Still just the bar: the 5 pre-existing todo/archive fixture rows are
    # untouched, nothing new was inserted into the permanent list.
    assert connection.get_db().execute('SELECT COUNT(*) FROM todos').fetchone()[0] == 5


def test_empty_model_plan_stays_empty_when_nothing_is_pending(client, monkeypatch):
    """Nothing to do is a real answer — don't invent one."""
    result = _run(monkeypatch, {'briefing': 'Morning!', 'todos': []})
    assert result['todosAdded'] == 0
    assert _chat_todos() == []


def test_a_partial_model_plan_is_left_alone(client, monkeypatch):
    """The fallback only fires on an *empty* plan; one real item is a plan."""
    _insert_daily_task('d_job', 'job search', 1)
    connection.get_db().commit()

    result = _run(monkeypatch, {'briefing': 'hi', 'todos': [{'title': 'Only this'}]})
    assert [r['title'] for r in _chat_todos()] == ['Only this']


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
            {'title': 'Low, due today', 'priority': 1, 'list': 'todo', 'due': NOW},
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
    # Shaped like the model's own output, so it validates the same way.
    assert plan[0] == {'title': 'Meditate', 'priority': 4, 'list': 'todo',
                       'due': None, 'linkedTitle': 'Meditate'}
    assert plan[4] == {'title': 'Low, due today', 'priority': 1, 'list': 'todo',
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
    assert [r['title'] for r in _chat_todos()] == ['job search']
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
    assert body['todosAdded'] == 1
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
