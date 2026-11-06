"""The proxy's rescue of tool calls written inside the thinking block.

Every fixture is a buffered chat.completion shaped the way llama-server returns
one; the notation is Qwen3-Coder's XML, which is what Qwen3.6 emits.
"""
import json

import pytest

from backend.codeagent import toolfix

READ = {'type': 'function', 'function': {
    'name': 'read', 'parameters': {
        'type': 'object',
        'properties': {'path': {'type': 'string'}, 'limit': {'type': 'integer'}},
        'required': ['path'], 'additionalProperties': False,
    }}}
BASH = {'type': 'function', 'function': {
    'name': 'bash', 'parameters': {
        'type': 'object', 'properties': {'command': {'type': 'string'}},
        'required': ['command'],
    }}}
REQUEST = {'messages': [{'role': 'user', 'content': 'look at a.py'}], 'tools': [READ, BASH]}

CALL = ('<tool_call>\n<function=read>\n<parameter=path>\nsrc/a.py\n</parameter>\n'
        '<parameter=limit>\n40\n</parameter>\n</function>\n</tool_call>')


def response(reasoning='', content='', finish='stop', tool_calls=None):
    message = {'role': 'assistant', 'content': content, 'reasoning_content': reasoning}
    if tool_calls:
        message['tool_calls'] = tool_calls
    return {'choices': [{'index': 0, 'message': message, 'finish_reason': finish}]}


def test_a_call_closing_the_reasoning_is_promoted():
    v = toolfix.check(REQUEST, response(f'I should read the file first.\n{CALL}\n'))
    assert v.action == 'promote'
    message = v.response['choices'][0]['message']
    assert v.response['choices'][0]['finish_reason'] == 'tool_calls'
    [call] = message['tool_calls']
    assert call['type'] == 'function' and call['id'].startswith('call_')
    assert call['function']['name'] == 'read'
    # The schema says limit is an integer, so the text "40" becomes 40.
    assert json.loads(call['function']['arguments']) == {'path': 'src/a.py', 'limit': 40}
    assert message['reasoning_content'] == 'I should read the file first.'
    assert message['content'] is None


def test_consecutive_calls_are_all_promoted():
    second = ('<tool_call>\n<function=bash>\n<parameter=command>\nls -la\n'
              '</parameter>\n</function>\n</tool_call>')
    v = toolfix.check(REQUEST, response(f'Two things.\n{CALL}\n{second}'))
    assert v.action == 'promote'
    names = [c['function']['name'] for c in v.response['choices'][0]['message']['tool_calls']]
    assert names == ['read', 'bash']


def test_json_notation_is_promoted_too():
    call = '<tool_call>\n{"name": "bash", "arguments": {"command": "pytest -q"}}\n</tool_call>'
    v = toolfix.check(REQUEST, response(f'Run the tests.\n{call}'))
    assert v.action == 'promote'
    assert v.calls == [{'name': 'bash', 'arguments': {'command': 'pytest -q'}}]


def test_inline_think_with_only_a_closing_tag_is_read_as_reasoning():
    # Qwen's template puts <think> in the prompt, so inline output starts mid-thought.
    v = toolfix.check(REQUEST, {'choices': [{'finish_reason': 'stop', 'message': {
        'content': f'Reading it.\n{CALL}\n</think>\n\n'}}]})
    assert v.action == 'promote'


def test_inline_think_block_is_read_as_reasoning():
    v = toolfix.check(REQUEST, {'choices': [{'finish_reason': 'stop', 'message': {
        'content': f'<think>Reading it.\n{CALL}</think>'}}]})
    assert v.action == 'promote'


@pytest.mark.parametrize('reasoning, why', [
    (CALL.replace('</tool_call>', ''), 'not closed'),
    (CALL.replace('function=read', 'function=write'), "'write' was not offered"),
    (CALL.replace('<parameter=path>\nsrc/a.py\n</parameter>\n', ''), "missing required argument 'path'"),
    (CALL.replace('\n40\n', '\nforty\n'), 'expected integer'),
    (CALL.replace('</function>', '<parameter=mode>\nx\n</parameter>\n</function>'), "unexpected argument 'mode'"),
    ('<tool_call>\nnot a call\n</tool_call>', 'neither XML nor JSON'),
])
def test_a_broken_call_asks_for_a_poke(reasoning, why):
    v = toolfix.check(REQUEST, response(reasoning))
    assert v.action == 'poke'
    assert why in v.reason
    assert v.attempted.startswith(toolfix.TOOL_OPEN)


@pytest.mark.parametrize('resp, why', [
    (response(CALL, tool_calls=[{'id': 'x', 'type': 'function',
                                 'function': {'name': 'read', 'arguments': '{}'}}]), 'already parsed'),
    (response(CALL, finish='length'), 'finish_reason=length'),
    (response(CALL, content='Here is the answer.'), 'visible content'),
    (response('Just thinking, no calls here.'), 'no call'),
    (response(CALL + '\n' + 'But wait, I should reconsider this approach entirely. ' * 10),
     'continued after the call'),
])
def test_leaves_alone(resp, why):
    v = toolfix.check(REQUEST, resp)
    assert v.action == 'ok'
    assert why in v.reason


def test_a_request_without_tools_is_never_touched():
    v = toolfix.check({'messages': []}, response(CALL))
    assert v.action == 'ok'


def test_promote_does_not_mutate_the_original():
    resp = response(f'thinking\n{CALL}')
    toolfix.check(REQUEST, resp)
    assert 'tool_calls' not in resp['choices'][0]['message']


def test_poke_messages_append_the_attempt_and_the_nudge():
    v = toolfix.check(REQUEST, response(CALL.replace('</tool_call>', '')))
    msgs = toolfix.poke_messages(REQUEST, v)
    assert msgs[:-2] == REQUEST['messages']
    assert msgs[-2]['role'] == 'assistant' and toolfix.TOOL_OPEN in msgs[-2]['content']
    assert msgs[-1] == {'role': 'user', 'content': toolfix.POKE_TEXT}
