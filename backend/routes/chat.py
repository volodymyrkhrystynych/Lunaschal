import time
import json
from datetime import date
from flask import Blueprint, jsonify, request, Response, stream_with_context
from ulid import ULID
from backend.db.connection import get_db, row_to_dict
from backend.chat_day import day_key_for
from backend.ai import priority
from backend.ai.provider import is_ai_configured
from backend.ai.chat_title import generate_conversation_title
from backend.delegate import chat as delegate_chat
from backend.delegate import runs
from backend.todo_recurrence import (
    VALID_LISTS, parse_due_date, parse_priority, parse_repeat,
)

bp = Blueprint('chat', __name__, url_prefix='/api/chat')


@bp.get('/conversations')
def list_conversations():
    rows = get_db().execute(
        'SELECT * FROM conversations WHERE writing_project_id IS NULL AND idea_id IS NULL ORDER BY updated_at DESC'
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/today')
def get_today():
    """The current chat day's conversation with its messages, or null if none yet.

    `mode` is a leftover of the retired Web Search tab, which kept its own
    conversation per day. Only 'chat' is reachable now; the parameter stays so
    the old rows remain addressable.
    """
    mode = request.args.get('mode', 'chat')
    db = get_db()
    row = db.execute(
        'SELECT * FROM conversations WHERE day_key=? AND writing_project_id IS NULL AND idea_id IS NULL AND mode=?',
        (day_key_for(), mode),
    ).fetchone()
    if not row:
        return jsonify(None)
    conv = row_to_dict(row)
    msgs = db.execute(
        'SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at', (conv['id'],)
    ).fetchall()
    conv['messages'] = [row_to_dict(m) for m in msgs]
    return jsonify(conv)


@bp.get('/journal-conversations')
def journal_conversations():
    """Past chat days for the Journal feed (excludes the current live day).

    Both 'chat' and the retired 'websearch' mode qualify: those days already
    happened, and dropping them from the feed would erase history rather than
    retire a feature.
    """
    rows = get_db().execute(
        '''SELECT c.id, c.title, c.day_key, c.mode, c.created_at, c.updated_at,
                  (SELECT COUNT(*) FROM messages m
                   WHERE m.conversation_id = c.id AND m.role IN ('user', 'assistant')) AS message_count
           FROM conversations c
           WHERE c.day_key IS NOT NULL AND c.day_key < ? AND c.writing_project_id IS NULL AND c.idea_id IS NULL
             AND EXISTS (SELECT 1 FROM messages m2
                         WHERE m2.conversation_id = c.id AND m2.role IN ('user', 'assistant'))
           ORDER BY c.day_key DESC''',
        (day_key_for(),),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.get('/conversations/<id>')
def get_conversation(id):
    db = get_db()
    row = db.execute('SELECT * FROM conversations WHERE id=?', (id,)).fetchone()
    if not row:
        return jsonify(None)
    conv = row_to_dict(row)
    msgs = db.execute(
        'SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at', (id,)
    ).fetchall()
    conv['messages'] = [row_to_dict(m) for m in msgs]
    return jsonify(conv)


@bp.post('/conversations')
def create_conversation():
    """Find-or-create the current chat day's conversation. Titles stay NULL
    until the nightly title job fills them in. `mode` defaults to 'chat' —
    see get_today for why the column outlived the tab."""
    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'chat')
    db = get_db()
    dk = day_key_for()
    existing = db.execute(
        'SELECT id FROM conversations WHERE day_key=? AND writing_project_id IS NULL AND idea_id IS NULL AND mode=?',
        (dk, mode),
    ).fetchone()
    if existing:
        return jsonify({'id': existing['id']}), 200
    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO conversations(id, title, day_key, mode, created_at, updated_at) VALUES (?,?,?,?,?,?)',
        (id, None, dk, mode, now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


@bp.post('/conversations/<id>/generate-title')
def generate_title(id):
    """Synchronously (re)generate an AI title for one conversation."""
    db = get_db()
    msgs = db.execute(
        'SELECT role, content, metadata FROM messages WHERE conversation_id=? ORDER BY created_at',
        (id,),
    ).fetchall()
    title = generate_conversation_title([dict(m) for m in msgs])
    if not title:
        return jsonify({'title': None})
    db.execute('UPDATE conversations SET title=? WHERE id=?', (title, id))
    db.commit()
    return jsonify({'title': title})


@bp.patch('/conversations/<id>/title')
def update_title(id):
    body = request.json or {}
    title = body.get('title', '')
    get_db().execute(
        'UPDATE conversations SET title=?, updated_at=? WHERE id=?',
        (title, int(time.time()), id),
    )
    get_db().commit()
    return jsonify({'success': True})


@bp.delete('/conversations/<id>')
def delete_conversation(id):
    get_db().execute('DELETE FROM conversations WHERE id=?', (id,))
    get_db().commit()
    return jsonify({'success': True})


@bp.post('/conversations/<id>/messages')
def add_message(id):
    body = request.json or {}
    msg_id = str(ULID())
    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at) VALUES (?,?,?,?,?,?)',
        (msg_id, id, body.get('role'), body.get('content'), body.get('metadata'), now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, id))
    db.commit()
    return jsonify({'id': msg_id}), 201


@bp.post('/briefing/run')
def run_briefing_now():
    """Generate today's overnight briefing on demand (also used by the daemon)."""
    from backend.briefing_job import run_briefing
    from backend.ai.llm import EmptyCompletion
    try:
        result = run_briefing(force=True)
    except EmptyCompletion as e:
        return jsonify({'error': f'The model returned no usable briefing ({e}). '
                                 'Try a different chat/briefing model.'}), 502
    if result is None:
        return jsonify({'error': 'AI provider not configured or nothing to brief'}), 400
    return jsonify(result)


def _cross_off(db, item: dict, now: int) -> None:
    """Cross off one plan item, completing its linked row when it has one.

    An unlinked item never becomes a `todos` row — that's the whole point of the
    daily plan — but it still logs an event so the day's work shows up in the
    Journal feed alongside everything else that got finished."""
    from backend.routes.tasks import (
        _log_event, complete_daily_task, complete_todo_row,
    )

    linked_id = item.get('linkedId')
    linked_type = item.get('linkedType')
    if linked_id and linked_type == 'todo':
        complete_todo_row(db, linked_id, now)
    elif linked_id and linked_type == 'daily':
        complete_daily_task(db, linked_id, date.fromtimestamp(now).isoformat(), now)
    else:
        _log_event(db, 'todo_completed', item['title'], None, item.get('list'), None)


def _apply_briefing_decision(db, item: dict, decision: dict, now: int) -> bool:
    """Resolve one plan item in place. Returns True if a todo row was inserted."""
    from backend.routes.tasks import _parse_priority, _parse_due
    from backend.todo_recurrence import VALID_LISTS

    action = decision.get('action')
    if action == 'reject':
        item['status'] = 'rejected'
        item['resolvedAt'] = now
        return False
    if action == 'done':
        _cross_off(db, item, now)
        item['status'] = 'done'
        item['resolvedAt'] = now
        return False

    # Already on one of the user's lists — there is nothing to add. Left pending
    # so it can still be crossed off.
    if item.get('linkedId'):
        return False

    # Accepting may carry inline edits from the card.
    title = (decision.get('title') if 'title' in decision else item.get('title')) or ''
    title = title.strip()
    if not title:
        return False
    if 'priority' in decision:
        priority, err = _parse_priority(decision.get('priority'))
        if err:
            priority = item.get('priority') or 3
    else:
        priority = item.get('priority') or 3
    if 'due' in decision:
        due, err = _parse_due(decision.get('due'))
        if err:
            due = item.get('due')
    else:
        due = item.get('due')
    todo_list = decision.get('list', item.get('list')) or 'todo'
    if todo_list not in VALID_LISTS:
        todo_list = 'todo'

    item.update({'title': title, 'priority': priority, 'due': due, 'list': todo_list})

    # Re-check for a same-titled open todo at accept time: the list may have
    # moved on since the briefing was written. Link to it and stay pending
    # rather than resolving the card — a twin you can cross off is more useful
    # than a dead "already on your list" row.
    dupe = db.execute(
        'SELECT id, title FROM todos WHERE done=0 AND lower(title)=? AND id!=?',
        (title.lower(), item['id']),
    ).fetchone()
    if dupe:
        item['linkedType'] = 'todo'
        item['linkedId'] = dupe['id']
        item['linkedTitle'] = dupe['title']
        return False

    # The proposal's own id becomes the todo id, so a double-accept is a no-op.
    db.execute(
        'INSERT OR IGNORE INTO todos(id, title, done, list, notes, due, repeat_interval,'
        ' repeat_unit, priority, created_at, updated_at) VALUES (?,?,0,?,?,?,?,?,?,?,?)',
        (item['id'], title, todo_list, None, due, None, None, priority, now, now),
    )
    item['status'] = 'accepted'
    item['resolvedAt'] = now
    return True


@bp.post('/briefing/<message_id>/todos')
def decide_briefing_todos(message_id):
    """Resolve items on the briefing's plan for the day: cross one off (`done`),
    dismiss it (`reject`), or add it to the to-do list for later (`accept`,
    optionally with inline edits). Decisions are written back into the message
    metadata so the chat cards keep the day's record across reloads."""
    body = request.json or {}
    decisions = body.get('decisions')
    if not isinstance(decisions, list) or not decisions:
        return jsonify({'error': 'decisions required'}), 400

    db = get_db()
    row = db.execute('SELECT metadata FROM messages WHERE id=?', (message_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    meta = json.loads(row['metadata']) if row['metadata'] else {}
    proposed = meta.get('proposedTodos')
    if not isinstance(proposed, list):
        return jsonify({'error': 'message has no proposed to-dos'}), 400

    by_id = {i.get('id'): i for i in proposed if isinstance(i, dict)}
    now = int(time.time())
    created = 0
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        item = by_id.get(decision.get('id'))
        # Only pending proposals are actionable — a resolved card can't flip.
        if not item or item.get('status') != 'pending':
            continue
        if _apply_briefing_decision(db, item, decision, now):
            created += 1

    meta['proposedTodos'] = proposed
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), message_id))
    db.commit()
    return jsonify({'proposedTodos': proposed, 'created': created})


class _AcceptRejected(ValueError):
    """A proposal's data failed validation — 400, proposal stays pending."""


class _GenerationFailed(RuntimeError):
    """The LLM call an accept required came back empty — 502, proposal stays
    pending so the user can retry."""


def _accept_calendar(db, data: dict) -> dict:
    title = (data.get('title') or '').strip()
    if not title:
        raise _AcceptRejected('title required')
    when = (data.get('date') or '').strip()
    _, err = parse_due_date(when)
    if err or not when:
        raise _AcceptRejected('date must be a real date as YYYY-MM-DD')

    # all_day is explicitly the whole day, not merely untimed, so setting it
    # clears the clock rather than sitting alongside one. Rows with a NULL time
    # and all_day=0 predate the flag and stay merely untimed.
    all_day = data.get('allDay') is True
    start = None if all_day else ((data.get('time') or '').strip() or None)
    end = None if all_day else ((data.get('endTime') or '').strip() or None)

    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO calendar_events(id, title, description, date, time, end_time,'
        ' all_day, tags, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (id, title, data.get('description', ''), when, start, end,
         1 if all_day else 0, json.dumps(data.get('tags', [])), now),
    )
    return {'id': id}


def _accept_calorie(db, data: dict) -> dict:
    description = (data.get('description') or '').strip()
    if not description:
        raise _AcceptRejected('description required')
    calories = data.get('calories')
    if isinstance(calories, bool) or not isinstance(calories, int) or not (0 <= calories <= 20000):
        raise _AcceptRejected('calories must be an integer from 0 to 20000')

    now = int(time.time())
    id = str(ULID())
    day = data.get('date') or date.today().isoformat()
    db.execute(
        'INSERT INTO calorie_logs(id, date, description, calories, created_at) VALUES (?,?,?,?,?)',
        (id, day, description, calories, now),
    )
    return {'id': id}


def _accept_task(db, data: dict) -> dict:
    title = (data.get('title') or '').strip()
    if not title:
        raise _AcceptRejected('title required')
    todo_list = data.get('list') or 'todo'
    if todo_list not in VALID_LISTS:
        raise _AcceptRejected(f'list must be one of {", ".join(VALID_LISTS)}')

    # These four used to be hard-coded null/null/null/3 here, so a to-do the
    # user had given a deadline and an urgency for landed in the list bare.
    # `due` travels through the proposal as the YYYY-MM-DD the model wrote and
    # becomes a timestamp only here, at the DB boundary.
    due, err = parse_due_date(data.get('due'))
    if err:
        raise _AcceptRejected(err)
    priority, err = parse_priority(data.get('priority'))
    if err:
        raise _AcceptRejected(err)
    repeat, err = parse_repeat(data.get('repeatInterval'), data.get('repeatUnit'))
    if err:
        raise _AcceptRejected(err)
    notes = (data.get('notes') or '').strip() or None

    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO todos(id, title, done, list, notes, due, repeat_interval,'
        ' repeat_unit, priority, created_at, updated_at) VALUES (?,?,0,?,?,?,?,?,?,?,?)',
        (id, title, todo_list, notes, due, repeat[0], repeat[1], priority, now, now),
    )
    return {'id': id}


def _accept_flashcards(db, data: dict) -> dict:
    from backend.ai.learning_generation import generate_cards
    from backend.routes.learning import _insert_cards
    from backend.tags import tags_json

    topic = (data.get('topic') or '').strip()
    if not topic:
        raise _AcceptRejected('topic required')
    related = db.execute(
        'SELECT content FROM journal_entries WHERE content LIKE ? LIMIT 3',
        (f'%{topic}%',),
    ).fetchall()
    text = f'Topic to learn: {topic}'
    if related:
        context = '\n\n---\n\n'.join(r['content'] for r in related)
        text += f"\n\nRelated notes from the user's journal:\n{context}"
    cards = generate_cards(text)
    if not cards:
        raise _GenerationFailed('No cards could be generated')
    ids = _insert_cards(
        cards, folder_id=data.get('folderId'), tags=tags_json(data.get('tags')),
        source_type='chat', source_id=None, derived_from=None,
        generation_context=text[:8000],
    )
    return {'count': len(ids)}


# The delegate only ever stages these four as real confirm cards — `note`
# drafts immediately with no accept/dismiss step (backend/delegate/runs.py
# filters it out before a proposal ever gets a persisted id).
_ACCEPT_HANDLERS = {
    'calendar': _accept_calendar,
    'calorie': _accept_calorie,
    'task': _accept_task,
    'flashcards': _accept_flashcards,
}


@bp.post('/proposals/<message_id>/<proposal_id>')
def resolve_proposal(message_id, proposal_id):
    """Accept or dismiss one delegate confirm card in place — the same shape
    as decide_briefing_todos, generalized across the delegate's proposal
    kinds. A proposal is stamped with a stable id and 'pending' status the
    moment the run that staged it finishes, so this is the only place one
    ever leaves that state — surviving a reload or a dropped connection
    exactly like the reply itself now does."""
    body = request.json or {}
    action = body.get('action')
    if action not in ('accept', 'dismiss'):
        return jsonify({'error': "action must be 'accept' or 'dismiss'"}), 400

    db = get_db()
    row = db.execute('SELECT metadata FROM messages WHERE id=?', (message_id,)).fetchone()
    if not row:
        return jsonify({'error': 'message not found'}), 404
    meta = json.loads(row['metadata']) if row['metadata'] else {}
    proposals = meta.get('proposals')
    if not isinstance(proposals, list):
        return jsonify({'error': 'message has no proposals'}), 404
    proposal = next(
        (p for p in proposals if isinstance(p, dict) and p.get('id') == proposal_id), None
    )
    if not proposal:
        return jsonify({'error': 'proposal not found'}), 404
    # Only pending proposals are actionable — a resolved card can't flip.
    if proposal.get('status') != 'pending':
        return jsonify({'error': 'proposal already resolved'}), 400

    if action == 'dismiss':
        proposal['status'] = 'dismissed'
    else:
        handler = _ACCEPT_HANDLERS.get(proposal.get('kind'))
        if handler is None:
            return jsonify({'error': f"unknown proposal kind {proposal.get('kind')!r}"}), 400
        # The card is editable, so the accepted values are whatever the user
        # has in front of them, not what the model first staged. Edited data
        # replaces the staged payload wholesale and goes through the same
        # handler — the handlers are the validation boundary, and nothing
        # arriving here is trusted just because a proposal exists.
        edited = body.get('data')
        if edited is not None and not isinstance(edited, dict):
            return jsonify({'error': 'data must be an object'}), 400
        data = edited if edited is not None else (proposal.get('data') or {})
        try:
            result = handler(db, data)
        except _AcceptRejected as e:
            # Left 'pending' on purpose: a card that failed validation is one
            # the user still has to fix, so it must not collapse to a resolved
            # line that quietly lost their edit.
            return jsonify({'error': str(e)}), 400
        except _GenerationFailed as e:
            return jsonify({'error': str(e)}), 502
        # Stored back so a reload renders what was actually saved rather than
        # what was originally proposed.
        proposal['data'] = data
        proposal['result'] = result
        proposal['status'] = 'accepted'

    proposal['resolvedAt'] = int(time.time())
    meta['proposals'] = proposals
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), message_id))
    db.commit()
    return jsonify({'proposal': proposal})


def _format_event(kind: str, payload) -> str:
    if kind == 'step':
        return f'data: {json.dumps(payload)}\n\n'
    if kind == 'done':
        return f'data: {json.dumps({"done": True, **payload})}\n\n'
    return f'data: {json.dumps({kind: payload})}\n\n'


@bp.post('/stream')
def stream():
    """The Chat tab's one streaming endpoint.

    Four event kinds go out, all under SSE's single `data:` framing:
    `{tool: ...}` as each delegate tool call finishes, `{thinking: ...}` and
    `{content: ...}` as the reply streams, and one `{done: true, steps,
    sources, proposals}` before `[DONE]`. The browser persists `steps`/`sources`
    onto the assistant message and turns `proposals` into confirm cards — the
    live events are gone after a reload, so the same trace has to arrive twice.

    A `conversationId` in the body means this is the Chat tab talking to a
    real conversation it wants recorded — the reply then generates on a
    background thread (backend/delegate/runs.py) instead of inside this
    request, so a dropped connection doesn't lose it: the thread checkpoints
    the assistant row itself and finishes it regardless of who's still
    listening. Callers with no conversation to record into (the voice
    listener, morning check-in, Writing discussions — none of which have
    anywhere durable to put the reply) keep the original inline path.
    """
    if not is_ai_configured():
        return jsonify({'error': 'AI provider not configured'}), 400
    body = request.json or {}
    messages = body.get('messages', [])
    system_prompt = body.get('systemPrompt', '')
    conversation_id = body.get('conversationId')

    if conversation_id:
        message_id = str(ULID())
        now = int(time.time())
        db = get_db()
        db.execute(
            "INSERT INTO messages(id, conversation_id, role, content, metadata,"
            " status, created_at) VALUES (?,?,'assistant','',NULL,'streaming',?)",
            (message_id, conversation_id, now),
        )
        db.commit()
        q = runs.start(message_id, messages, system_prompt, tools_enabled=not system_prompt)

        def generate():
            yield f'data: {json.dumps({"messageId": message_id})}\n\n'
            while True:
                kind, payload = q.get()
                if kind == '_end':
                    break
                yield _format_event(kind, payload)
            yield 'data: [DONE]\n\n'

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
        )

    # Acquired here, in the view, rather than inside generate(): the generator
    # body does not run until Werkzeug pulls the first item, so acquiring there
    # would leave the window between "user pressed Enter" and "first token"
    # looking idle to background work. Released in the generator's finally,
    # which also runs on GeneratorExit when the client disconnects mid-stream.
    # One mark spans the whole turn, delegate sub-loop included — the user is
    # waiting on all of it, the same way ideas.discuss holds one across both its
    # blocking gather and its streamed answer.
    token = priority.begin('chat.stream')

    def generate():
        try:
            # A caller-supplied systemPrompt means this is not the Chat tab —
            # it's the voice listener, a task nudge or the morning check-in,
            # none of which have a card to confirm anything on.
            for kind, payload in delegate_chat.stream_reply(
                messages, system_prompt, tools_enabled=not system_prompt
            ):
                yield _format_event(kind, payload)
            yield 'data: [DONE]\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
        finally:
            # Must not yield here — that would raise "generator ignored
            # GeneratorExit". priority.end only touches a dict under a lock.
            priority.end(token)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )
