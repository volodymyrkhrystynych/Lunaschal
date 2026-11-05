"""Yesterday's unfinished chat-bar to-dos come back as `todo` suggestion cards
on the morning briefing — never as rows mixed into today's bar — and accepting
one writes it into today's bar."""
import json
import time
from datetime import date, timedelta

from backend.db import connection
from backend.day_boundary import day_key_for
from backend import briefing_job

_FAKE = {
    'briefing': '## Morning\n- Do the thing',
    'todos': [{'title': 'Draft the report', 'priority': 4, 'list': 'todo', 'due': None}],
}


def _yesterday(now):
    return (date.fromisoformat(day_key_for(now)) - timedelta(days=1)).isoformat()


def _insert_chat_todo(id, day_key, title, done=0, priority=3, notes=None):
    db = connection.get_db()
    db.execute(
        'INSERT INTO chat_todos(id, day_key, title, notes, priority, done, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,0,0)',
        (id, day_key, title, notes, priority, done),
    )
    db.commit()


def _run(monkeypatch, now, force=False, fake=None):
    monkeypatch.setattr(briefing_job, 'generate_briefing', lambda ctx: dict(fake or _FAKE))
    return briefing_job.run_briefing(now=now, force=force)


def _proposals(message_id):
    row = connection.get_db().execute(
        'SELECT metadata FROM messages WHERE id=?', (message_id,)
    ).fetchone()
    return json.loads(row['metadata']).get('proposals', [])


def _bar(day_key):
    return [r['title'] for r in connection.get_db().execute(
        'SELECT title FROM chat_todos WHERE day_key=? ORDER BY created_at, id', (day_key,)
    )]


def test_unfinished_items_become_cards_and_stay_out_of_todays_bar(client, monkeypatch):
    now = int(time.time())
    yesterday = _yesterday(now)
    _insert_chat_todo('a', yesterday, 'Call the dentist', priority=4, notes='before noon')
    _insert_chat_todo('b', yesterday, 'Already done', done=1)
    _insert_chat_todo('c', '2000-01-01', 'Ancient item')

    result = _run(monkeypatch, now)

    assert result['todosCarried'] == 1
    carried = [p for p in _proposals(result['messageId']) if p['kind'] == 'todo']
    assert [(p['data']['title'], p['data']['priority'], p['data']['notes'], p['carriedFrom'], p['status'])
            for p in carried] == [('Call the dentist', 4, 'before noon', yesterday, 'pending')]
    # Today's bar holds only today's plan.
    assert _bar(day_key_for(now)) == ['Draft the report']


def test_a_title_the_plan_already_restated_is_not_offered_again(client, monkeypatch):
    now = int(time.time())
    _insert_chat_todo('a', _yesterday(now), 'draft the REPORT')
    result = _run(monkeypatch, now)
    assert result['todosCarried'] == 0
    assert all(p['kind'] != 'todo' for p in _proposals(result['messageId']))


def test_a_forced_rerun_does_not_duplicate_or_reoffer_cards(client, monkeypatch):
    now = int(time.time())
    _insert_chat_todo('a', _yesterday(now), 'Call the dentist')
    first = _run(monkeypatch, now)
    assert first['todosCarried'] == 1
    forced = _run(monkeypatch, now, force=True)
    assert forced['todosCarried'] == 0


def test_carried_cards_keep_a_briefing_whose_plan_is_empty(client, monkeypatch):
    # No prose and no plan would normally leave nothing; the cards are worth a message.
    now = int(time.time())
    _insert_chat_todo('a', _yesterday(now), 'Call the dentist')

    def _raise_empty(ctx):
        from backend.ai.llm import EmptyCompletion
        raise EmptyCompletion('empty')

    monkeypatch.setattr(briefing_job, 'generate_briefing', _raise_empty)
    monkeypatch.setattr(briefing_job, 'fallback_plan', lambda ctx: [])
    result = briefing_job.run_briefing(now=now)
    assert result is not None and result['todosCarried'] == 1


def _resolve(client, message_id, proposal_id, **body):
    return client.post(f'/api/chat/proposals/{message_id}/{proposal_id}', json=body)


def test_accepting_a_card_adds_it_to_todays_bar_once(client, monkeypatch):
    now = int(time.time())
    today = day_key_for(now)
    _insert_chat_todo('a', _yesterday(now), 'Call the dentist', priority=2)
    _insert_chat_todo('b', _yesterday(now), 'Water plants')
    result = _run(monkeypatch, now)
    cards = {p['data']['title']: p for p in _proposals(result['messageId']) if p['kind'] == 'todo'}

    resp = _resolve(client, result['messageId'], cards['Call the dentist']['id'], action='accept')
    assert resp.status_code == 200
    assert resp.get_json()['proposal']['status'] == 'accepted'
    row = connection.get_db().execute(
        'SELECT title, priority, done FROM chat_todos WHERE id=?',
        (resp.get_json()['proposal']['result']['id'],),
    ).fetchone()
    assert (row['title'], row['priority'], row['done']) == ('Call the dentist', 2, 0)
    assert _bar(today) == ['Draft the report', 'Call the dentist']

    # Dismissing writes nothing.
    resp = _resolve(client, result['messageId'], cards['Water plants']['id'], action='dismiss')
    assert resp.get_json()['proposal']['status'] == 'dismissed'
    assert _bar(today) == ['Draft the report', 'Call the dentist']


def test_accept_returns_the_existing_row_when_the_title_is_already_in_the_bar(client, monkeypatch):
    now = int(time.time())
    _insert_chat_todo('a', _yesterday(now), 'Call the dentist')
    result = _run(monkeypatch, now)
    card = next(p for p in _proposals(result['messageId']) if p['kind'] == 'todo')
    _insert_chat_todo('today', day_key_for(now), 'call the dentist ')

    resp = _resolve(client, result['messageId'], card['id'], action='accept')
    assert resp.get_json()['proposal']['result'] == {'id': 'today'}
    assert _bar(day_key_for(now)).count('call the dentist ') == 1
    assert 'Call the dentist' not in _bar(day_key_for(now))


def test_accept_validates_the_edited_card(client, monkeypatch):
    now = int(time.time())
    _insert_chat_todo('a', _yesterday(now), 'Call the dentist')
    result = _run(monkeypatch, now)
    card = next(p for p in _proposals(result['messageId']) if p['kind'] == 'todo')

    assert _resolve(client, result['messageId'], card['id'], action='accept',
                    data={'title': '  '}).status_code == 400
    assert _resolve(client, result['messageId'], card['id'], action='accept',
                    data={'title': 'x', 'priority': 9}).status_code == 400
    assert _proposals(result['messageId'])[-1]['status'] == 'pending'
