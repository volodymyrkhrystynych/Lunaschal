import json
import time

from backend.chat import compaction
from backend.db.connection import get_db


def _conversation():
    db = get_db()
    now = int(time.time())
    db.execute(
        'INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        ('c1', None, now, now),
    )
    db.commit()
    return db


def _message(db, message_id, role, content, metadata=None, offset=0):
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at) '
        'VALUES (?,?,?,?,?,?)',
        (message_id, 'c1', role, content, metadata, int(time.time()) + offset),
    )
    db.commit()


def _summary():
    return {
        'summary': 'The user researched chocolate.',
        'facts': ['The novel was published in 1964.'],
        'decisions': [],
        'openThreads': ['Compare editions.'],
        'sources': [{'title': 'Charlie', 'url': '/api/knowledge/wiki/Charlie'}],
    }


def test_rolling_compaction_replaces_only_the_covered_prefix(monkeypatch):
    _conversation()
    monkeypatch.setattr(compaction, 'SOFT_LIMIT_TOKENS', 12)
    monkeypatch.setattr(compaction, 'RECENT_TARGET_TOKENS', 6)
    monkeypatch.setattr(compaction, 'summarize', lambda messages, previous=None: _summary())
    messages = [
        {'id': 'm1', 'role': 'user', 'content': 'old question ' * 5},
        {'id': 'm2', 'role': 'assistant', 'content': 'old answer ' * 5},
        {'id': 'm3', 'role': 'user', 'content': 'recent'},
    ]

    remaining, context = compaction.compact_for_prompt(messages, 'c1')

    assert [m['id'] for m in remaining] == ['m3']
    assert 'published in 1964' in context
    row = get_db().execute(
        "SELECT * FROM chat_compactions WHERE conversation_id='c1'"
    ).fetchone()
    assert json.loads(row['source_message_ids']) == ['m1', 'm2']
    assert row['status'] == 'done'


def test_a_later_turn_reuses_the_saved_rolling_summary(monkeypatch):
    db = _conversation()
    now = int(time.time())
    db.execute(
        "INSERT INTO chat_compactions(id, conversation_id, kind, source_message_ids, "
        "content, status, carry_context, created_at, updated_at) "
        "VALUES ('co1','c1','rolling',?,?,'done',1,?,?)",
        (json.dumps(['m1', 'm2']), json.dumps(_summary()), now, now),
    )
    db.commit()
    monkeypatch.setattr(compaction, 'summarize',
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))

    remaining, context = compaction.compact_for_prompt([
        {'id': 'm1', 'role': 'user', 'content': 'old'},
        {'id': 'm2', 'role': 'assistant', 'content': 'old'},
        {'id': 'm3', 'role': 'user', 'content': 'new'},
    ], 'c1')

    assert [m['id'] for m in remaining] == ['m3']
    assert 'Compare editions' in context


def test_new_chat_is_committed_even_when_compaction_fails(monkeypatch, run_jobs_sync):
    db = _conversation()
    _message(db, 'm1', 'user', 'remember this')
    monkeypatch.setattr(compaction, 'summarize',
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('offline')))

    result = compaction.create_break('c1')

    marker = db.execute('SELECT * FROM messages WHERE id=?', (result['id'],)).fetchone()
    assert json.loads(marker['metadata'])['break'] is True
    assert marker['status'] == 'error'
    compacted = db.execute(
        'SELECT * FROM chat_compactions WHERE id=?', (result['compactionId'],)
    ).fetchone()
    assert compacted['status'] == 'error'


def test_new_chat_handoff_and_clean_slate(monkeypatch, run_jobs_sync):
    db = _conversation()
    _message(db, 'm1', 'user', 'remember this')
    monkeypatch.setattr(compaction, 'summarize', lambda *_a, **_k: _summary())

    compaction.create_break('c1', carry_context=True)
    assert 'Durable context' in compaction.handoff_context('c1')
    assert 'published in 1964' in compaction.handoff_context('c1')

    _message(db, 'm2', 'user', 'a disposable topic', offset=2)
    compaction.create_break('c1', carry_context=False)
    assert compaction.handoff_context('c1') == ''


def test_recent_offline_evidence_is_available_on_followups():
    db = _conversation()
    evidence = {'kind': 'offline_knowledge', 'archiveId': 'wiki',
                'path': 'Charlie', 'title': 'Charlie',
                'excerpt': 'Published in 1964.'}
    _message(db, 'm1', 'assistant', 'It was 1964.',
             json.dumps({'evidence': [evidence]}))

    context = compaction.evidence_context('c1')

    assert 'Published in 1964' in context
    assert 'archiveId' in context


def test_new_chat_route_queues_a_handoff(client, monkeypatch, run_jobs_sync):
    db = _conversation()
    _message(db, 'm1', 'user', 'remember this')
    monkeypatch.setattr(compaction, 'summarize', lambda *_a, **_k: _summary())

    response = client.post('/api/chat/conversations/c1/break', json={
        'carryContext': True,
    })

    assert response.status_code == 201
    assert response.get_json()['status'] == 'pending'
    marker = db.execute(
        'SELECT status, metadata FROM messages WHERE id=?',
        (response.get_json()['id'],),
    ).fetchone()
    assert marker['status'] == 'done'
    assert json.loads(marker['metadata'])['carryContext'] is True


def test_new_chat_route_rejects_an_unrelated_conversation(client):
    response = client.post('/api/chat/conversations/missing/break', json={})
    assert response.status_code == 404


def test_startup_retries_a_failed_handoff(monkeypatch, run_jobs_sync):
    db = _conversation()
    _message(db, 'm1', 'user', 'remember this')
    attempts = {'count': 0}

    def flaky(*_args, **_kwargs):
        attempts['count'] += 1
        if attempts['count'] == 1:
            raise RuntimeError('offline')
        return _summary()

    monkeypatch.setattr(compaction, 'summarize', flaky)
    result = compaction.create_break('c1')
    assert db.execute(
        'SELECT status FROM chat_compactions WHERE id=?',
        (result['compactionId'],),
    ).fetchone()['status'] == 'error'

    compaction.recover_pending()

    assert attempts['count'] == 2
    assert db.execute(
        'SELECT status FROM chat_compactions WHERE id=?',
        (result['compactionId'],),
    ).fetchone()['status'] == 'done'
