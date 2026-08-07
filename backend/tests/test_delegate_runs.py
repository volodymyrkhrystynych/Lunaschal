"""backend/delegate/runs.py: the chat reply's generation, decoupled from
whatever HTTP request asked for it.

`_run` is exercised directly (no real thread) wherever the DB checkpoint
matters, so the assertions aren't racing a background thread. `start` is only
exercised where the point is that it actually runs on one.
"""
import json
import queue
import threading
import time

import pytest

from backend.db.connection import get_db
from backend.delegate import runs


def _new_conversation(db) -> str:
    conv_id = 'conv1'
    now = int(time.time())
    db.execute(
        'INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (conv_id, None, now, now),
    )
    return conv_id


def _new_streaming_message(db, conv_id: str, msg_id: str = 'm1') -> str:
    now = int(time.time())
    db.execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata,"
        " status, created_at) VALUES (?,?,'assistant','',NULL,'streaming',?)",
        (msg_id, conv_id, now),
    )
    db.commit()
    return msg_id


def _row(db, msg_id: str):
    return db.execute('SELECT * FROM messages WHERE id=?', (msg_id,)).fetchone()


def test_run_checkpoints_content_and_flips_to_done(client, monkeypatch):
    db = get_db()
    conv_id = _new_conversation(db)
    msg_id = _new_streaming_message(db, conv_id)

    def fake_stream_reply(messages, system_prompt, delegate=True):
        yield ('content', 'Hello ')
        yield ('content', 'there.')
        yield ('done', {
            'steps': [{'tool': 'web_search', 'ok': True}],
            'sources': [{'url': 'https://ex.com', 'title': 'Ex'}],
            'proposals': [],
        })

    monkeypatch.setattr(runs.delegate_chat, 'stream_reply', fake_stream_reply)

    q: queue.Queue = queue.Queue()
    runs._run(msg_id, [{'role': 'user', 'content': 'hi'}], '', True, q)

    row = _row(db, msg_id)
    assert row['status'] == 'done'
    assert row['content'] == 'Hello there.'
    metadata = json.loads(row['metadata'])
    assert metadata['steps'] == [{'tool': 'web_search', 'ok': True}]
    assert metadata['sources'] == [{'url': 'https://ex.com', 'title': 'Ex'}]
    assert metadata['proposals'] == []

    # The live queue got every event plus a terminal sentinel.
    events = []
    while True:
        kind, payload = q.get_nowait()
        events.append(kind)
        if kind == '_end':
            break
    assert events == ['content', 'content', 'done', '_end']


def test_run_stamps_confirm_card_proposals_and_drops_note(client, monkeypatch):
    """Only calendar/calorie/task/flashcards are real confirm cards — each
    gets a stable id and 'pending' status so a later accept/dismiss
    (backend/routes/chat.py's resolve_proposal) can find it by id. `note`
    drafts immediately client-side with no confirm step, so it never gets a
    persisted id at all."""
    db = get_db()
    conv_id = _new_conversation(db)
    msg_id = _new_streaming_message(db, conv_id)

    def fake_stream_reply(messages, system_prompt, delegate=True):
        yield ('done', {
            'steps': [],
            'sources': [],
            'proposals': [
                {'kind': 'task', 'data': {'title': 'call the dentist'}},
                {'kind': 'note', 'data': {'content': 'warm up before deadlifts'}},
            ],
        })

    monkeypatch.setattr(runs.delegate_chat, 'stream_reply', fake_stream_reply)

    runs._run(msg_id, [{'role': 'user', 'content': 'hi'}], '', True, queue.Queue())

    metadata = json.loads(_row(db, msg_id)['metadata'])
    assert len(metadata['proposals']) == 1
    proposal = metadata['proposals'][0]
    assert proposal['kind'] == 'task'
    assert proposal['status'] == 'pending'
    assert proposal['data'] == {'title': 'call the dentist'}
    assert proposal['id']  # a real, non-empty stable id


def test_run_marks_error_on_exception_and_keeps_partial_content(client, monkeypatch):
    db = get_db()
    conv_id = _new_conversation(db)
    msg_id = _new_streaming_message(db, conv_id)

    def fake_stream_reply(messages, system_prompt, delegate=True):
        yield ('content', 'partial')
        raise RuntimeError('llama-server died')

    monkeypatch.setattr(runs.delegate_chat, 'stream_reply', fake_stream_reply)

    q: queue.Queue = queue.Queue()
    runs._run(msg_id, [{'role': 'user', 'content': 'hi'}], '', True, q)

    row = _row(db, msg_id)
    assert row['status'] == 'error'
    assert row['error'] == 'llama-server died'
    assert row['content'] == 'partial'

    kinds = []
    while True:
        kind, _ = q.get_nowait()
        kinds.append(kind)
        if kind == '_end':
            break
    assert kinds == ['content', 'error', '_end']


def test_run_releases_the_priority_mark_even_on_failure(client, monkeypatch):
    from backend.ai import priority

    priority.reset()
    db = get_db()
    conv_id = _new_conversation(db)
    msg_id = _new_streaming_message(db, conv_id)

    def boom(messages, system_prompt, delegate=True):
        raise RuntimeError('boom')
        yield  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(runs.delegate_chat, 'stream_reply', boom)
    runs._run(msg_id, [], '', True, queue.Queue())

    assert priority.active() is False


def test_start_runs_on_a_real_thread_and_finishes_the_row(client, monkeypatch):
    db = get_db()
    conv_id = _new_conversation(db)
    msg_id = _new_streaming_message(db, conv_id)

    def fake_stream_reply(messages, system_prompt, delegate=True):
        yield ('content', 'async reply')
        yield ('done', {'steps': [], 'sources': [], 'proposals': []})

    monkeypatch.setattr(runs.delegate_chat, 'stream_reply', fake_stream_reply)

    q = runs.start(msg_id, [{'role': 'user', 'content': 'hi'}], '', delegate=True)

    # Drain the live queue to its terminal sentinel — the same thing the SSE
    # view's generator does — which only arrives once the thread is done.
    while True:
        kind, _ = q.get(timeout=5)
        if kind == '_end':
            break

    row = _row(db, msg_id)
    assert row['status'] == 'done'
    assert row['content'] == 'async reply'


def test_start_creates_the_row_as_streaming_before_the_thread_finishes(client, monkeypatch):
    """The row has to already exist (and read 'streaming') the moment the
    view returns — well before the reply is done — since that's what a
    reconnecting client polls to find."""
    db = get_db()
    conv_id = _new_conversation(db)
    msg_id = _new_streaming_message(db, conv_id)

    release = threading.Event()

    def slow_stream_reply(messages, system_prompt, delegate=True):
        release.wait(timeout=5)
        yield ('content', 'done waiting')
        yield ('done', {'steps': [], 'sources': [], 'proposals': []})

    monkeypatch.setattr(runs.delegate_chat, 'stream_reply', slow_stream_reply)

    q = runs.start(msg_id, [{'role': 'user', 'content': 'hi'}], '', delegate=True)

    # The thread is blocked on `release`, so the row must still read exactly
    # what the view inserted: 'streaming', empty content.
    row = _row(db, msg_id)
    assert row['status'] == 'streaming'
    assert row['content'] == ''

    # Let the thread finish and drain it fully before the test (and the
    # `client` fixture's connection teardown) proceeds — an outstanding
    # thread mid-write when the connection closes segfaults the interpreter
    # rather than raising (same hazard backend/ai/background.py's own
    # wait_idle guards against).
    release.set()
    while True:
        kind, _ = q.get(timeout=5)
        if kind == '_end':
            break


def test_reset_stale_message_runs_marks_orphaned_rows_as_error(client):
    from backend.db.connection import _reset_stale_message_runs

    db = get_db()
    conv_id = _new_conversation(db)
    streaming_id = _new_streaming_message(db, conv_id, 'm-streaming')
    db.execute(
        "INSERT INTO messages(id, conversation_id, role, content, metadata,"
        " status, created_at) VALUES ('m-done',?,'assistant','all good',NULL,'done',?)",
        (conv_id, int(time.time())),
    )
    db.commit()

    _reset_stale_message_runs(db)

    streaming_row = _row(db, streaming_id)
    assert streaming_row['status'] == 'error'
    assert streaming_row['error'] == 'Interrupted by an app restart.'

    done_row = _row(db, 'm-done')
    assert done_row['status'] == 'done'
    assert done_row['error'] is None
