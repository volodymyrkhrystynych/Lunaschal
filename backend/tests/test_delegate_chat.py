"""The Chat tab's decide-act-answer turn, and its SSE framing.

The model is faked at two seams: `_decision_calls` (the decision turn) and
`chat_stream_events` (the answer). What's under test is the glue between them —
what the tools stage, what crosses from the delegate into the answering prompt,
and what reaches the browser.

The `propose_*` tools run on *this* turn rather than inside the delegate: the
delegate is handed one `task` string with the conversation already paraphrased
out of it, so a deadline or an urgency mentioned in passing never survived the
hand-off.
"""
import json
from types import SimpleNamespace

import pytest

from backend.delegate import chat as delegate_chat


def _call(name='delegate', args=None, call_id='c1'):
    return SimpleNamespace(
        id=call_id,
        content=None,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(args if args is not None else {'task': 'do the thing'}),
        ),
    )


@pytest.fixture
def answered(monkeypatch):
    """Stub the answering turn and hand back what prompt it was given."""
    seen = {}

    def fake_stream_events(messages):
        seen['messages'] = messages
        yield ('content', 'Sure thing.')

    monkeypatch.setattr(delegate_chat, 'chat_stream_events', fake_stream_events)
    return seen


def _decides(monkeypatch, calls):
    monkeypatch.setattr(delegate_chat, '_decision_calls', lambda messages: list(calls))


def _no_tools(monkeypatch):
    _decides(monkeypatch, [])


def _delegates(monkeypatch, result, task='do the thing'):
    _decides(monkeypatch, [_call('delegate', {'task': task})])
    monkeypatch.setattr(
        delegate_chat.agent, 'run_events',
        lambda t, **kw: iter([('step', {'tool': 'web_search', 'ok': True}),
                              ('result', result)]),
    )


def _drain(messages=None):
    return list(delegate_chat.stream_reply(messages or [{'role': 'user', 'content': 'hi'}]))


def test_an_ordinary_message_runs_no_tools_at_all(monkeypatch, answered):
    _no_tools(monkeypatch)
    events = _drain()

    assert [k for k, _ in events] == ['content', 'done']
    assert events[-1][1] == {'steps': [], 'sources': [], 'proposals': [],
                             'truncated': False}


def test_a_proposal_tool_runs_on_this_turn_and_stages_a_card(monkeypatch, answered):
    """It used to take a delegate round-trip to get here, and the detail the
    user gave had to survive being paraphrased into a task string on the way."""
    _decides(monkeypatch, [_call('propose_task', {
        'title': 'Book the flights', 'due': '2026-08-14', 'priority': 5,
    })])
    events = _drain()

    payload = events[-1][1]
    assert payload['proposals'] == [{
        'kind': 'task',
        'data': {'title': 'Book the flights', 'list': 'todo', 'due': '2026-08-14',
                 'priority': 5, 'notes': None, 'repeatInterval': None,
                 'repeatUnit': None},
    }]
    assert payload['steps'][0]['tool'] == 'propose_task'


def test_the_answering_turn_is_told_the_card_is_not_saved(monkeypatch, answered):
    """A reply that says "added that for you" over an unconfirmed card is worse
    than not offering at all."""
    _decides(monkeypatch, [_call('propose_task', {'title': 'Buy milk'})])
    _drain()

    tool_message = next(m for m in answered['messages'] if m['role'] == 'tool')
    assert 'Nothing has been saved yet' in tool_message['content']


def test_several_tool_calls_on_one_turn_all_run(monkeypatch, answered):
    """"add buy milk, and remind me about the Dave thing" is one card and one
    question. The old code kept the first delegate call and dropped the rest."""
    _decides(monkeypatch, [
        _call('propose_task', {'title': 'Buy milk'}, call_id='c1'),
        _call('ask_user', {'question': 'When is the Dave thing?'}, call_id='c2'),
    ])
    events = _drain()

    payload = events[-1][1]
    assert [s['tool'] for s in payload['steps']] == ['propose_task', 'ask_user']
    # Asking never cancels an unrelated card, and never adds one of its own.
    assert len(payload['proposals']) == 1
    assert payload['proposals'][0]['data']['title'] == 'Buy milk'
    assert len([m for m in answered['messages'] if m['role'] == 'tool']) == 2


def test_asking_for_clarification_stages_nothing(monkeypatch, answered):
    """The whole reason to ask is that there is no honest payload yet, so a
    turn that only asks must leave no card behind."""
    _decides(monkeypatch, [_call('ask_user', {'question': 'This Friday or next?'})])
    events = _drain()

    payload = events[-1][1]
    assert payload['proposals'] == []
    assert payload['steps'][0]['tool'] == 'ask_user'
    tool_message = next(m for m in answered['messages'] if m['role'] == 'tool')
    assert 'This Friday or next?' in tool_message['content']


def test_only_the_summary_crosses_into_the_answering_prompt(monkeypatch, answered):
    """The compression *is* the point of delegating: the main chat's context
    must not grow by the delegate's whole transcript."""
    _delegates(monkeypatch, {
        'steps': [{'tool': 'web_search', 'ok': True}],
        'sources': [],
        'summary': 'FSRS 5 was released in July 2024.',
        'truncated': False,
    })
    _drain()

    tool_messages = [m for m in answered['messages'] if m['role'] == 'tool']
    assert len(tool_messages) == 1
    assert tool_messages[0]['content'] == 'FSRS 5 was released in July 2024.'


def test_the_transcript_keeps_a_well_formed_tool_exchange(monkeypatch, answered):
    """A tool result with no preceding assistant tool_call is a malformed
    history that llama-server's template renders wrong."""
    _delegates(monkeypatch, {'steps': [], 'sources': [],
                             'summary': 'done', 'truncated': False})
    _drain()

    roles = [m['role'] for m in answered['messages']]
    assert roles.index('assistant') < roles.index('tool')
    assistant = next(m for m in answered['messages'] if m['role'] == 'assistant')
    assert assistant['tool_calls'][0]['function']['name'] == 'delegate'


def test_proposals_and_steps_ride_the_done_event(monkeypatch, answered):
    """The browser persists these onto the message metadata and turns proposals
    into confirm cards — a reload has to redraw the same trace, and the live
    events are gone by then."""
    _decides(monkeypatch, [_call('propose_calorie_log',
                                 {'description': 'burger', 'calories': 650})])
    events = _drain()

    kind, payload = events[-1]
    assert kind == 'done'
    assert payload['proposals'] == [
        {'kind': 'calorie', 'data': {'description': 'burger', 'calories': 650}}
    ]
    # The step streamed live *and* rides the done event.
    assert ('step', payload['steps'][0]) in events
    assert len(payload['steps']) == 1


def test_a_delegate_run_contributes_its_sources(monkeypatch, answered):
    _delegates(monkeypatch, {
        'steps': [{'tool': 'web_search', 'ok': True}],
        'sources': [{'url': 'https://example.com', 'title': 'Example'}],
        'summary': 'Found it.',
        'truncated': False,
    })
    payload = _drain()[-1][1]
    assert payload['sources'] == [{'url': 'https://example.com', 'title': 'Example'}]


def test_a_failed_decision_turn_still_produces_a_reply(monkeypatch, answered):
    """The classifier's sin was swallowing failure into a fake result with no
    reply and no error. Losing the delegate must cost the delegate only."""
    def boom(messages, tools, max_tokens=None):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(delegate_chat, 'chat_tool_turn', boom)
    events = _drain()

    assert [k for k, _ in events] == ['content', 'done']


def test_the_decision_turn_is_capped_and_offers_the_whole_toolbox(monkeypatch, answered):
    """Uncapped, a model that decided *not* to act writes a whole reply we are
    about to throw away — dead air before the real answer starts."""
    seen = {}

    def fake_turn(messages, tools, max_tokens=None):
        seen['max_tokens'] = max_tokens
        seen['tools'] = tools
        return SimpleNamespace(content='', tool_calls=None), 'stop'

    monkeypatch.setattr(delegate_chat, 'chat_tool_turn', fake_turn)
    _drain()

    assert seen['max_tokens'] == delegate_chat.DECISION_MAX_TOKENS
    offered = {t['function']['name'] for t in seen['tools']}
    assert offered == {'delegate', 'propose_task', 'propose_calendar_event',
                       'propose_calorie_log', 'propose_food_log', 'propose_recipe',
                       'propose_note_to_self', 'propose_flashcards',
                       'remember', 'revise_memory', 'ask_user'}


def test_a_tool_call_the_model_invented_is_ignored(monkeypatch, answered):
    """Dispatching an unknown name would answer "Unknown tool" into the
    transcript, which reads to the model as a broken tool rather than one that
    never existed."""
    def fake_turn(messages, tools, max_tokens=None):
        return SimpleNamespace(
            content='', tool_calls=[_call('propose_mortgage', {'x': 1})],
        ), 'tool_calls'

    monkeypatch.setattr(delegate_chat, 'chat_tool_turn', fake_turn)
    events = _drain()

    assert events[-1][1]['steps'] == []
    assert not any(m['role'] == 'tool' for m in answered['messages'])


def test_thinking_and_content_stay_separate_channels(monkeypatch):
    """Reasoning rendered into the reply is the failure the split exists to
    prevent."""
    _no_tools(monkeypatch)
    monkeypatch.setattr(
        delegate_chat, 'chat_stream_events',
        lambda messages: iter([('thinking', 'hmm'), ('content', 'Hello.')]),
    )
    events = _drain()
    assert [k for k, _ in events] == ['thinking', 'content', 'done']


def test_a_truncated_answer_is_reported_on_done_not_as_a_delta(monkeypatch):
    """A turn that spent its whole output budget inside <think> answers with
    nothing, and looks exactly like a model that had nothing to say. The flag
    rides on `done` — it isn't known until the stream ends, and it is a fact
    about the reply, which is what gets persisted."""
    _no_tools(monkeypatch)
    monkeypatch.setattr(
        delegate_chat, 'chat_stream_events',
        lambda messages: iter([('thinking', 'round in circles'),
                               ('truncated', True)]),
    )
    events = _drain()

    assert [k for k, _ in events] == ['thinking', 'done']
    assert events[-1][1]['truncated'] is True


def test_tools_disabled_skips_the_decision_turn(monkeypatch, answered):
    """The voice listener and nudges speak their replies aloud, where a staged
    card is invisible and the decision turn is pure added latency."""
    def boom(*args, **kwargs):
        raise AssertionError('the decision turn must not run')

    monkeypatch.setattr(delegate_chat, 'chat_tool_turn', boom)
    events = list(delegate_chat.stream_reply(
        [{'role': 'user', 'content': 'hi'}], 'CUSTOM PROMPT', tools_enabled=False
    ))
    assert [k for k, _ in events] == ['content', 'done']


# --- SSE framing on the route ---

def _post(client, body):
    resp = client.post('/api/chat/stream', data=json.dumps(body),
                       content_type='application/json')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _events(payload: str) -> list[dict]:
    return [json.loads(line[6:]) for line in payload.splitlines()
            if line.startswith('data: ') and line[6:] != '[DONE]']


def test_the_route_frames_every_event_kind(client, monkeypatch):
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.routes.chat.delegate_chat.stream_reply',
        lambda messages, system_prompt, tools_enabled=True: iter([
            ('step', {'tool': 'web_search', 'ok': True, 'count': 3}),
            ('thinking', 'weighing it up'),
            ('content', 'Here you go.'),
            ('done', {'steps': [], 'sources': [], 'proposals': []}),
        ]),
    )
    body = _post(client, {'messages': [{'role': 'user', 'content': 'hi'}]})

    events = _events(body)
    # A step event goes out bare, keyed by `tool` — the shape the browser
    # already keys on, and what old websearch-mode messages persisted.
    assert events[0]['tool'] == 'web_search'
    assert events[1] == {'thinking': 'weighing it up'}
    assert events[2] == {'content': 'Here you go.'}
    assert events[3]['done'] is True
    assert body.rstrip().endswith('data: [DONE]')


def test_a_mid_stream_failure_reaches_the_browser(client, monkeypatch):
    """The bug this whole change came from was a request that produced no
    reply, no error and no log line."""
    def exploding(messages, system_prompt, tools_enabled=True):
        yield ('content', 'partial')
        raise RuntimeError('llama-server died')

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.delegate_chat.stream_reply', exploding)

    events = _events(_post(client, {'messages': []}))
    assert events[-1]['error'] == 'llama-server died'


def test_the_priority_mark_is_released_after_the_turn(client, monkeypatch):
    """Held across the delegate sub-loop *and* the streamed answer, then
    released — a leaked mark defers background work for a whole MARK_TTL."""
    from backend.ai import priority

    priority.reset()
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.routes.chat.delegate_chat.stream_reply',
        lambda messages, system_prompt, tools_enabled=True: iter([('content', 'hi')]),
    )
    _post(client, {'messages': []})
    assert priority.active() is False


def _new_conversation(db):
    import time
    conv_id = 'conv1'
    now = int(time.time())
    db.execute(
        'INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (conv_id, None, now, now),
    )
    db.commit()
    return conv_id


def test_a_conversation_id_persists_the_reply_via_a_background_run(client, monkeypatch):
    """The reply generates on a thread independent of this request — this
    test drives the SSE route end-to-end (a real thread, a real DB row) and
    checks the *row*, not just the framed events, since the row is what a
    disconnected client recovers from."""
    from backend.db.connection import get_db, row_to_dict

    db = get_db()
    conv_id = _new_conversation(db)
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.routes.chat.delegate_chat.stream_reply',
        lambda messages, system_prompt, tools_enabled=True: iter([
            ('content', 'Here you go.'),
            ('done', {'steps': [{'tool': 'web_search', 'ok': True}],
                      'sources': [{'url': 'https://ex.com'}], 'proposals': []}),
        ]),
    )

    body = _post(client, {
        'messages': [{'role': 'user', 'content': 'hi'}],
        'conversationId': conv_id,
    })

    events = _events(body)
    assert 'messageId' in events[0]
    message_id = events[0]['messageId']
    assert any(e.get('content') == 'Here you go.' for e in events)

    row = row_to_dict(db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone())
    assert row['status'] == 'done'
    assert row['content'] == 'Here you go.'
    assert row['role'] == 'assistant'
    assert row['conversationId'] == conv_id
    metadata = json.loads(row['metadata'])
    assert metadata['steps'] == [{'tool': 'web_search', 'ok': True}]


def test_a_conversation_id_run_that_fails_leaves_an_error_row(client, monkeypatch):
    from backend.db.connection import get_db, row_to_dict

    db = get_db()
    conv_id = _new_conversation(db)
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)

    def exploding(messages, system_prompt, tools_enabled=True):
        yield ('content', 'partial')
        raise RuntimeError('llama-server died')

    monkeypatch.setattr('backend.routes.chat.delegate_chat.stream_reply', exploding)

    body = _post(client, {'messages': [], 'conversationId': conv_id})
    events = _events(body)
    message_id = events[0]['messageId']
    assert events[-1]['error'] == 'llama-server died'

    row = row_to_dict(db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone())
    assert row['status'] == 'error'
    assert row['error'] == 'llama-server died'
    assert row['content'] == 'partial'


def test_no_conversation_id_keeps_the_inline_legacy_path(client, monkeypatch):
    """Voice/morning-checkin/Writing discussions never pass a conversationId —
    they must keep getting the original inline generator, with no message row
    created anywhere."""
    from backend.db.connection import get_db

    db = get_db()
    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.routes.chat.delegate_chat.stream_reply',
        lambda messages, system_prompt, tools_enabled=True: iter([('content', 'hi')]),
    )

    _post(client, {'messages': [], 'systemPrompt': 'You are a nudge.'})

    assert db.execute('SELECT COUNT(*) AS n FROM messages').fetchone()['n'] == 0


def test_a_caller_supplied_prompt_turns_the_tools_off(client, monkeypatch):
    """The voice listener, nudges and the morning check-in speak their replies
    aloud — there is no card to confirm a proposal on and nowhere to read a
    clarifying question, so the whole decision turn is pure added latency."""
    seen = {}

    def fake(messages, system_prompt, tools_enabled=True):
        seen['tools_enabled'] = tools_enabled
        yield ('content', 'ok')

    monkeypatch.setattr('backend.routes.chat.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.routes.chat.delegate_chat.stream_reply', fake)

    _post(client, {'messages': [], 'systemPrompt': 'You are a nudge.'})
    assert seen['tools_enabled'] is False

    _post(client, {'messages': []})
    assert seen['tools_enabled'] is True


def test_an_attached_photos_reading_reaches_the_answering_prompt(client, monkeypatch, answered):
    """The photo has to become text *before* `stamp_messages` runs — that helper
    rebuilds each message as `[today 21:58] <content>`, so anything not already
    in `content` by then never reaches the model at all."""
    _no_tools(monkeypatch)
    db = client.application  # noqa: F841 - the fixture is what gives us a DB
    from backend.db.connection import get_db

    conn = get_db()
    conn.execute("INSERT INTO conversations(id, day_key, mode, created_at, updated_at)"
                 " VALUES ('c1','2026-08-09','chat',0,0)")
    conn.execute(
        "INSERT INTO chat_attachments(id, conversation_id, path, description,"
        " description_status, position, created_at)"
        " VALUES ('a1','c1','/tmp/x.jpg','A plate of vareniki.','done',0,0)"
    )
    conn.commit()

    _drain([{'role': 'user', 'content': 'what is this',
             'createdAt': '2026-08-09T12:00:00', 'attachmentIds': ['a1']}])

    user_turn = next(m for m in answered['messages'] if m['role'] == 'user')
    assert 'A plate of vareniki.' in user_turn['content']
    # Still stamped, so the two features compose rather than one clobbering the other.
    assert user_turn['content'].startswith('[')
