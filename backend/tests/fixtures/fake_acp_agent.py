"""A scripted ACP agent for the code-agent tests. Speaks JSON-RPC over stdio.

Behaviour comes from the prompt text and a few env vars:

- a prompt containing PLAN_MARKER is the plan phase: it asks permission to
  edit (expecting a reject) and replies with a plan. FAKE_STRAY_EDIT=1 makes
  it write a file anyway, the way pi (no permission system) could.
- any other prompt is the build phase: it asks to execute (expecting an
  allow), then appends to a.py in its cwd without committing. A prompt
  containing ASK makes it end with a QUESTION: line instead.
- FAKE_LOAD_SESSION=1 advertises session/load.
- FAKE_CRASH=1 exits during session/prompt.
"""
import json
import os
import sys

PLAN_MARKER = 'Do not modify any files'
_next = [1000]
_log = []


def send(msg):
    sys.stdout.write(json.dumps(msg) + '\n')
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def update(session, upd):
    send({'jsonrpc': '2.0', 'method': 'session/update',
          'params': {'sessionId': session, 'update': upd}})


def say(session, text, kind='agent_message_chunk'):
    update(session, {'sessionUpdate': kind, 'content': {'type': 'text', 'text': text}})


def ask_permission(session, kind):
    _next[0] += 1
    req_id = _next[0]
    send({'jsonrpc': '2.0', 'id': req_id, 'method': 'session/request_permission', 'params': {
        'sessionId': session,
        'toolCall': {'toolCallId': f'tc{req_id}', 'title': f'{kind} something', 'kind': kind},
        'options': [
            {'optionId': 'allow', 'name': 'Allow', 'kind': 'allow_once'},
            {'optionId': 'reject', 'name': 'Reject', 'kind': 'reject_once'},
        ],
    }})
    while True:
        msg = read()
        if msg.get('id') == req_id and 'result' in msg:
            outcome = msg['result']['outcome']
            return outcome.get('optionId') if outcome.get('outcome') == 'selected' else None
        _log.append(msg)


def main():
    while True:
        msg = read()
        method, params, mid = msg.get('method'), msg.get('params') or {}, msg.get('id')
        if method == 'initialize':
            send({'jsonrpc': '2.0', 'id': mid, 'result': {
                'protocolVersion': 1,
                'agentCapabilities': {'loadSession': os.environ.get('FAKE_LOAD_SESSION') == '1'},
            }})
        elif method == 'session/new':
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'sessionId': 'sess-1'}})
        elif method == 'session/load':
            update(params['sessionId'], {'sessionUpdate': 'user_message_chunk',
                                         'content': {'type': 'text', 'text': 'replayed'}})
            send({'jsonrpc': '2.0', 'id': mid, 'result': None})
        elif method == 'session/prompt':
            session = params['sessionId']
            text = params['prompt'][0]['text']
            if os.environ.get('FAKE_CRASH') == '1':
                sys.stderr.write('fatal: model went away\n')
                sys.exit(3)
            say(session, 'Thinking it over.', 'agent_thought_chunk')
            if PLAN_MARKER in text:
                decision = ask_permission(session, 'edit')
                if os.environ.get('FAKE_STRAY_EDIT') == '1':
                    with open('stray.txt', 'w') as f:
                        f.write('oops\n')
                update(session, {'sessionUpdate': 'plan', 'entries': [
                    {'content': 'Change a.py', 'priority': 'high', 'status': 'pending'}]})
                say(session, f'## Plan\n1. Change a.py\n(edit was {decision})')
            else:
                decision = ask_permission(session, 'execute')
                update(session, {'sessionUpdate': 'tool_call', 'toolCallId': 'w1',
                                 'title': 'Edit a.py', 'kind': 'edit', 'status': 'completed'})
                with open('a.py', 'a') as f:
                    f.write(f'# built ({decision})\n')
                if 'ASK' in text:
                    say(session, 'I changed a.py.\nQUESTION: Should b.py change too?')
                else:
                    say(session, 'Done: ')
                    say(session, 'changed a.py.')
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'stopReason': 'end_turn'}})
        elif mid is not None:
            send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': method}})


if __name__ == '__main__':
    main()
