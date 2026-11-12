"""What the user says they need to do in the journal reaches today's chat as
cards (backend/journal/todos.py), so it never has to be said twice."""
import json
import time

import pytest

from backend.ai import job_handlers  # noqa: F401 — registers the handlers
from backend.ai import jobs
from backend.ai import journal_todos as extractor
from backend.ai.service import InferencePaused
from backend.db import connection
from backend.day_boundary import day_key_for
from backend.journal.todos import offer_todos_from_entry


def _db():
    return connection.get_db()


def _entry(id, content, created_at=None, idea_id=None):
    now = created_at or int(time.time())
    _db().execute(
        'INSERT INTO journal_entries(id, content, created_at, updated_at, idea_id) VALUES (?,?,?,?,?)',
        (id, content, now, now, idea_id))
    _db().commit()


def _model(monkeypatch, todos):
    calls = []

    def fake(prompt, **kwargs):
        calls.append(prompt)
        return {'todos': todos}
    monkeypatch.setattr(extractor, 'chat_json', fake)
    return calls


def _cards():
    out = []
    for r in _db().execute("SELECT metadata FROM messages WHERE role='assistant' ORDER BY created_at"):
        meta = json.loads(r['metadata'] or '{}')
        out += [p for p in meta.get('proposals', []) if p['kind'] == 'todo']
    return out


def test_a_need_in_an_entry_becomes_a_card_in_todays_chat(client, monkeypatch):
    _entry('j1', 'Heater is broken again. I need to call the landlord tomorrow morning.')
    _model(monkeypatch, [{'title': 'Call the landlord', 'reason': 'I need to call the landlord',
                          'linkedTitle': None}])

    staged = offer_todos_from_entry('j1')

    assert [c['data']['title'] for c in staged] == ['Call the landlord']
    card = _cards()[0]
    assert card['status'] == 'pending' and card['fromJournal'] == 'j1'
    assert card['data']['sourceType'] == 'journal' and card['data']['sourceId'] == 'j1'
    conv = _db().execute('SELECT day_key FROM conversations').fetchone()
    assert conv['day_key'] == day_key_for()


def test_accepting_the_card_puts_it_in_the_bar_with_its_reason(client, monkeypatch):
    _entry('j1', 'I need to call the landlord.')
    _model(monkeypatch, [{'title': 'Call the landlord', 'reason': 'need to call the landlord',
                          'linkedTitle': None}])
    offer_todos_from_entry('j1')
    message = _db().execute("SELECT id, metadata FROM messages WHERE role='assistant'").fetchone()
    card = json.loads(message['metadata'])['proposals'][0]

    resp = client.post(f'/api/chat/proposals/{message["id"]}/{card["id"]}', json={'action': 'accept'})
    assert resp.status_code == 200
    row = _db().execute('SELECT title, source_type, source_id, reason FROM chat_todos').fetchone()
    assert tuple(row) == ('Call the landlord', 'journal', 'j1', 'need to call the landlord')


def test_an_entry_naming_an_open_todo_offers_that_todo(client, monkeypatch):
    _db().execute("INSERT INTO todos(id, title, done, list, priority, created_at, updated_at)"
                  " VALUES ('t1', 'Book dentist', 0, 'todo', 3, 0, 0)")
    _entry('j1', 'Tooth hurts, really should book the dentist.')
    _model(monkeypatch, [{'title': 'Book a dentist appointment', 'reason': 'should book the dentist',
                          'linkedTitle': 'Book dentist'}])

    staged = offer_todos_from_entry('j1')
    assert staged[0]['data']['title'] == 'Book dentist'
    assert (staged[0]['data']['sourceType'], staged[0]['data']['sourceId']) == ('todo', 't1')


def test_nothing_already_in_the_bar_or_turned_down_is_offered(client, monkeypatch):
    today = day_key_for()
    _db().execute("INSERT INTO chat_todos(id, day_key, title, priority, done, created_at, updated_at)"
                  " VALUES ('c1', ?, 'Buy milk', 3, 0, 0, 0)", (today,))
    _db().execute("INSERT INTO chat_todo_suggestions(item_key, title, dropped_at, updated_at)"
                  " VALUES ('title:water the plants', 'Water the plants', 1, 1)")
    _entry('j1', 'Out of milk. Plants look sad.')
    _model(monkeypatch, [
        {'title': 'buy milk', 'reason': 'out of milk', 'linkedTitle': None},
        {'title': 'Water the plants', 'reason': 'plants look sad', 'linkedTitle': None},
    ])
    assert offer_todos_from_entry('j1') == []
    assert _cards() == []


def test_a_replayed_job_does_not_stage_the_cards_twice(client, monkeypatch):
    _entry('j1', 'Need to renew my passport.')
    calls = _model(monkeypatch, [{'title': 'Renew passport', 'reason': 'need to renew',
                                  'linkedTitle': None}])
    offer_todos_from_entry('j1')
    offer_todos_from_entry('j1')
    assert len(_cards()) == 1
    assert len(calls) == 1


def test_a_second_entry_saying_the_same_thing_is_not_offered_again(client, monkeypatch):
    _entry('j1', 'Need to renew my passport.')
    _entry('j2', 'Still need to renew that passport.')
    _model(monkeypatch, [{'title': 'Renew passport', 'reason': 'need to renew', 'linkedTitle': None}])
    offer_todos_from_entry('j1')
    assert offer_todos_from_entry('j2') == []


def test_idea_recordings_old_entries_and_empty_answers_are_left_alone(client, monkeypatch):
    calls = _model(monkeypatch, [])
    _db().execute("INSERT INTO ideas(id, content, created_at, updated_at) VALUES ('i1', 'x', 0, 0)")
    _entry('idea', 'I need to build a better parser.', idea_id='i1')
    _entry('old', 'I need to call mum.', created_at=int(time.time()) - 3 * 86400)
    _entry('plain', 'Lovely walk today.')
    assert offer_todos_from_entry('idea') == []
    assert offer_todos_from_entry('old') == []
    assert offer_todos_from_entry('plain') == []
    assert len(calls) == 1  # only the plain one reached the model
    assert _db().execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_a_paused_model_propagates_so_the_job_stays_pending(client, monkeypatch):
    _entry('j1', 'Need to renew my passport.')

    def paused(*a, **k):
        raise InferencePaused('paused')
    monkeypatch.setattr(extractor, 'chat_json', paused)
    with pytest.raises(InferencePaused):
        offer_todos_from_entry('j1')


def test_the_metadata_job_queues_the_extraction(client, monkeypatch):
    import backend.routes.journal as journal_routes
    monkeypatch.setattr(journal_routes, '_generate_metadata_bg', lambda *a, **k: None)
    _entry('j1', 'Need to renew my passport.')
    jobs._HANDLERS['journal.metadata']('j1', {'content': 'x'})
    kinds = [r['kind'] for r in _db().execute(
        "SELECT kind FROM llm_jobs WHERE target_id='j1' AND status='pending'")]
    assert kinds == ['journal.extract_todos']


def test_the_chat_agent_sees_cards_waiting_for_an_answer(client, monkeypatch):
    from backend.ai.chat import format_plate_context
    _entry('j1', 'Need to renew my passport.')
    _model(monkeypatch, [{'title': 'Renew passport', 'reason': 'need to renew', 'linkedTitle': None}])
    offer_todos_from_entry('j1')
    assert '- Renew passport' in format_plate_context().split('waiting for their answer:')[1]


def test_two_wordings_of_one_todo_in_an_entry_make_one_card(client, monkeypatch):
    _db().execute("INSERT INTO todos(id, title, done, list, priority, created_at, updated_at)"
                  " VALUES ('t1', 'Call dentist', 0, 'todo', 3, 0, 0)")
    _entry('j1', 'Need to call the dentist. Must ring the dentist office.')
    _model(monkeypatch, [
        {'title': 'Call the dentist', 'reason': 'need to call the dentist', 'linkedTitle': 'Call dentist'},
        {'title': 'Ring the dentist office', 'reason': 'ring the dentist', 'linkedTitle': 'Call dentist'},
    ])
    staged = offer_todos_from_entry('j1')
    assert [c['data']['title'] for c in staged] == ['Call dentist']


def test_a_dismissed_card_is_not_offered_again(client, monkeypatch):
    _entry('j1', 'I need to call the landlord.')
    _entry('j2', 'Really must call the landlord.')
    _model(monkeypatch, [{'title': 'Call the landlord', 'reason': 'call the landlord',
                          'linkedTitle': None}])
    offer_todos_from_entry('j1')
    message = _db().execute("SELECT id, metadata FROM messages WHERE role='assistant'").fetchone()
    card = json.loads(message['metadata'])['proposals'][0]
    resp = client.post(f'/api/chat/proposals/{message["id"]}/{card["id"]}', json={'action': 'dismiss'})
    assert resp.status_code == 200

    assert offer_todos_from_entry('j2') == []
