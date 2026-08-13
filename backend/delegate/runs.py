"""Runs the Chat tab's decision->delegate->answer loop on a daemon thread,
independent of the HTTP request that asked for it.

Without this, the whole loop lived inside the Flask request generator for
POST /api/chat/stream, and the reply was only ever saved by the *client*,
after the stream finished successfully. A connection dropped mid-reply (a
backgrounded tab, a network blip) lost the reply outright — nothing had been
written anywhere. Here the thread is the source of truth: it checkpoints the
message row as it goes and finishes it regardless of whether anyone is still
listening, the same shape backend/meetings/pipeline.py already uses for a
recording that must survive a restart.

There is deliberately no broadcast/replay mechanism for the live queue this
returns — it has exactly one subscriber, the HTTP request that started the
run. A client that loses the connection recovers by polling the message row
(status flips to 'done'/'error' when the thread finishes), not by
re-attaching to the same stream.
"""
import json
import queue
import threading
import time

from ulid import ULID

from backend.ai import priority
from backend.db.connection import build_update, get_db
from backend.delegate import chat as delegate_chat

# How often the accumulated content gets checkpointed to the DB while a run is
# in progress, beyond the checkpoints that already happen on every 'step'
# event. Anything shorter turns a fast reply into a DB write per token; this
# just needs to be short enough that a drop loses at most a fraction of a
# second of text.
_FLUSH_INTERVAL = 0.5

# Reasoning is kept, but bounded. Unlike steps it is unbounded model output —
# a thinking model can spend thousands of tokens on one turn — and it rides in
# `metadata` on every message the transcript loads and re-polls. The head is
# what is kept: the trace is read from the top, and a block that starts
# mid-sentence is worse than one that stops mid-sentence.
_MAX_THINKING = 20_000
_THINKING_TRUNCATED = '\n\n[reasoning truncated]'

# Tracked the same way backend/ai/background.py tracks its executor's pending
# futures: not for production (the module-global connection outlives every
# run), but so a test's `client` fixture can wait for a run to actually finish
# before closing the connection out from under it — the same "outlived its own
# teardown" segfault the background/research-worker trackers already guard.
_active: set["threading.Event"] = set()
_lock = threading.Lock()


def start(message_id: str, messages: list[dict], system_prompt: str, *, tools_enabled: bool) -> "queue.Queue":
    """Spawns the run; returns the queue the caller's SSE view should relay
    from. Puts (kind, payload) tuples, same shape as stream_reply, plus a
    terminal ('_end', None) once the thread is done (whether by 'done' or by
    an exception putting an 'error' event first)."""
    q: queue.Queue = queue.Queue()
    done = threading.Event()
    with _lock:
        _active.add(done)
    threading.Thread(
        target=_run, args=(message_id, messages, system_prompt, tools_enabled, q, done),
        daemon=True,
    ).start()
    return q


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until every run started via `start()` has finished. True if they
    all did, False on timeout. Tests only — production never calls this."""
    with _lock:
        events = list(_active)
    deadline = time.monotonic() + timeout
    for event in events:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not event.wait(timeout=max(remaining, 0)):
            return False
    return True


def _append_thinking(current: str, delta: str) -> str:
    """Accumulates reasoning up to `_MAX_THINKING`, then stops. Appending the
    marker once (rather than re-slicing on every delta) keeps a long thinking
    turn from rebuilding a 20 KB string per token."""
    if current.endswith(_THINKING_TRUNCATED):
        return current
    grown = current + delta
    if len(grown) <= _MAX_THINKING:
        return grown
    return grown[:_MAX_THINKING] + _THINKING_TRUNCATED


def _run(message_id: str, messages: list[dict], system_prompt: str, tools_enabled: bool,
          q: "queue.Queue", done: "threading.Event | None" = None) -> None:
    token = priority.begin('chat.stream')
    db = get_db()
    content = ''
    thinking = ''
    steps: list[dict] = []
    last_flush = 0.0
    try:
        for kind, payload in delegate_chat.stream_reply(messages, system_prompt, tools_enabled=tools_enabled):
            q.put((kind, payload))
            if kind == 'content':
                content += payload
            elif kind == 'thinking':
                thinking = _append_thinking(thinking, payload)
            elif kind == 'step':
                steps.append(payload)

            now = time.monotonic()
            if kind == 'step' or now - last_flush >= _FLUSH_INTERVAL:
                # Steps ride along with every checkpoint, not just content, so
                # a client that reopens mid-run (a backgrounded tab, a dropped
                # connection) sees what has actually happened so far instead of
                # a generic "still thinking" label with nothing behind it —
                # metadata.steps is what src/lib/agentSteps.ts's AgentStep list
                # already reads on reload; only proposals/sources wait for the
                # 'done' write below, since neither is known before then.
                build_update(db, 'messages', {
                    'content': content,
                    'metadata': json.dumps({'agent': 'delegate', 'steps': steps,
                                             'thinking': thinking,
                                             'sources': [], 'proposals': []}),
                }, 'id=?', (message_id,))
                db.commit()
                last_flush = now

            if kind == 'done':
                # `flashcard_draft` proposals stage nothing to accept/dismiss —
                # they draft flashcards immediately client-side, and those
                # drafts are already durable rows the instant they exist. Only
                # the other kinds are real confirm cards, so only they get the
                # stable id + 'pending' status a later accept/dismiss resolves
                # by (backend/routes/chat.py's resolve_proposal).
                proposals = [
                    {'id': str(ULID()), 'status': 'pending', **p}
                    for p in payload.get('proposals', []) if p.get('kind') != 'flashcard_draft'
                ]
                metadata = json.dumps({
                    'agent': 'delegate',
                    'steps': payload.get('steps', []),
                    # Kept, not dropped: a turn that spent itself reasoning and
                    # answered with nothing is otherwise an empty row with no
                    # account of where the time went. It is never fed back to
                    # the model — only shown, collapsed, under the trace.
                    'thinking': thinking,
                    # The one thing that explains a reply with no text in it:
                    # a thinking turn can spend its whole `max_tokens` budget
                    # inside <think> and stop before writing a word.
                    'truncated': bool(payload.get('truncated')),
                    # The other explanation for a reply that stops early, and
                    # a different one: the wall-clock budget ran out (Settings
                    # -> Chat timeout) rather than the token budget. The text
                    # above it is real and was kept; it is just not finished.
                    'timedOut': bool(payload.get('timedOut')),
                    'sources': payload.get('sources', []),
                    'proposals': proposals,
                })
                build_update(db, 'messages', {
                    'content': content, 'metadata': metadata, 'status': 'done',
                    # Stamped here rather than at insert: `created_at` is taken
                    # when the run starts, which on a local model is minutes
                    # before there is a reply to show a time for.
                    'finished_at': int(time.time()),
                }, 'id=?', (message_id,))
                db.commit()
    except Exception as e:
        build_update(db, 'messages', {
            'content': content, 'status': 'error', 'error': str(e),
            # A run that died still stopped at a knowable moment, and the
            # partial content it left is worth timestamping.
            'finished_at': int(time.time()),
        }, 'id=?', (message_id,))
        db.commit()
        q.put(('error', str(e)))
    finally:
        priority.end(token)
        q.put(('_end', None))
        if done is not None:
            done.set()
            with _lock:
                _active.discard(done)
