"""POST /api/chat/proposals/<message_id>/<proposal_id>: accepting or
dismissing one delegate confirm card in place. Proposals are stamped with a
stable id and 'pending' status by backend/delegate/runs.py when the run that
staged them finishes (see test_delegate_runs.py); this is the only route that
ever moves one out of that state."""
import json
import time

from backend.db.connection import get_db


def _seed_message(proposals: list[dict]) -> tuple[str, str]:
    """A conversation + one assistant message carrying `proposals`. Returns
    (message_id, proposal_id) for the first proposal."""
    db = get_db()
    now = int(time.time())
    conv_id = 'conv1'
    msg_id = 'msg1'
    db.execute(
        'INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (conv_id, None, now, now),
    )
    metadata = json.dumps({'agent': 'delegate', 'steps': [], 'sources': [], 'proposals': proposals})
    db.execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata,"
        " status, created_at) VALUES (?,?,'assistant','done',?,'done',?)",
        (msg_id, conv_id, metadata, now),
    )
    db.commit()
    return msg_id, proposals[0]['id']


def _proposal(id_, kind, data):
    return {'id': id_, 'kind': kind, 'status': 'pending', 'data': data}


def _resolve(client, msg_id, proposal_id, action):
    return client.post(
        f'/api/chat/proposals/{msg_id}/{proposal_id}', json={'action': action}
    )


def _metadata(client, msg_id):
    row = get_db().execute('SELECT metadata FROM messages WHERE id=?', (msg_id,)).fetchone()
    return json.loads(row['metadata'])


def test_accepting_a_calendar_proposal_inserts_the_event(client):
    msg_id, p_id = _seed_message([
        _proposal('p1', 'calendar', {
            'title': 'Dentist appointment', 'description': 'Checkup',
            'date': '2026-08-05', 'tags': ['health'],
        }),
    ])
    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 200
    proposal = resp.get_json()['proposal']
    assert proposal['status'] == 'accepted'
    event_id = proposal['result']['id']

    row = get_db().execute('SELECT * FROM calendar_events WHERE id=?', (event_id,)).fetchone()
    assert row['title'] == 'Dentist appointment'
    assert row['date'] == '2026-08-05'
    assert json.loads(row['tags']) == ['health']

    assert _metadata(client, msg_id)['proposals'][0]['status'] == 'accepted'


def test_accepting_a_calorie_proposal_inserts_the_log(client):
    msg_id, p_id = _seed_message([
        _proposal('p1', 'calorie', {'description': 'burger', 'calories': 650}),
    ])
    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 200
    log_id = resp.get_json()['proposal']['result']['id']

    row = get_db().execute('SELECT * FROM calorie_logs WHERE id=?', (log_id,)).fetchone()
    assert row['description'] == 'burger'
    assert row['calories'] == 650


def test_accepting_a_calorie_proposal_with_bad_calories_leaves_it_pending(client):
    msg_id, p_id = _seed_message([
        _proposal('p1', 'calorie', {'description': 'burger', 'calories': 'lots'}),
    ])
    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 400
    assert _metadata(client, msg_id)['proposals'][0]['status'] == 'pending'


def test_accepting_a_task_proposal_inserts_the_todo(client):
    msg_id, p_id = _seed_message([
        _proposal('p1', 'task', {'title': 'call the dentist', 'list': 'todo'}),
    ])
    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 200
    todo_id = resp.get_json()['proposal']['result']['id']

    row = get_db().execute('SELECT * FROM todos WHERE id=?', (todo_id,)).fetchone()
    assert row['title'] == 'call the dentist'
    assert row['list'] == 'todo'
    assert row['done'] == 0


def test_accepting_a_task_proposal_with_an_invalid_list_leaves_it_pending(client):
    msg_id, p_id = _seed_message([
        _proposal('p1', 'task', {'title': 'x', 'list': 'nonsense'}),
    ])
    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 400
    assert _metadata(client, msg_id)['proposals'][0]['status'] == 'pending'


def test_accepting_a_flashcards_proposal_generates_and_queues_cards(client, monkeypatch):
    monkeypatch.setattr(
        'backend.ai.learning_generation.generate_cards',
        lambda text: [{'question': 'Q1', 'answer': 'A1'}, {'question': 'Q2', 'answer': 'A2'}],
    )
    msg_id, p_id = _seed_message([
        _proposal('p1', 'flashcards', {'topic': 'React hooks'}),
    ])
    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 200
    assert resp.get_json()['proposal']['result']['count'] == 2

    rows = get_db().execute(
        "SELECT * FROM learning_cards WHERE source_type='chat'"
    ).fetchall()
    assert len(rows) == 2
    assert all(r['state'] == 'pending' for r in rows)


def test_accepting_a_flashcards_proposal_that_generates_nothing_leaves_it_pending(client, monkeypatch):
    monkeypatch.setattr('backend.ai.learning_generation.generate_cards', lambda text: [])
    msg_id, p_id = _seed_message([_proposal('p1', 'flashcards', {'topic': 'React hooks'})])

    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 502
    assert _metadata(client, msg_id)['proposals'][0]['status'] == 'pending'


def test_dismissing_a_proposal_marks_it_dismissed_without_writing_anything(client):
    msg_id, p_id = _seed_message([
        _proposal('p1', 'task', {'title': 'call the dentist'}),
    ])
    resp = _resolve(client, msg_id, p_id, 'dismiss')
    assert resp.status_code == 200
    assert resp.get_json()['proposal']['status'] == 'dismissed'
    assert get_db().execute('SELECT COUNT(*) AS n FROM todos').fetchone()['n'] == 0


def test_an_already_resolved_proposal_cannot_be_resolved_again(client):
    msg_id, p_id = _seed_message([
        _proposal('p1', 'task', {'title': 'call the dentist'}),
    ])
    _resolve(client, msg_id, p_id, 'dismiss')

    resp = _resolve(client, msg_id, p_id, 'accept')
    assert resp.status_code == 400
    # Still dismissed, not flipped to accepted by the second call.
    assert _metadata(client, msg_id)['proposals'][0]['status'] == 'dismissed'


def test_an_unknown_message_404s(client):
    resp = _resolve(client, 'no-such-message', 'p1', 'accept')
    assert resp.status_code == 404


def test_an_unknown_proposal_id_404s(client):
    msg_id, _ = _seed_message([_proposal('p1', 'task', {'title': 'x'})])
    resp = _resolve(client, msg_id, 'no-such-proposal', 'accept')
    assert resp.status_code == 404


def test_an_invalid_action_400s(client):
    msg_id, p_id = _seed_message([_proposal('p1', 'task', {'title': 'x'})])
    resp = _resolve(client, msg_id, p_id, 'maybe')
    assert resp.status_code == 400
