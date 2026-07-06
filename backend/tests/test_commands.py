"""Unit tests for the voice-command LLM parser (`backend.ai.commands`).

The real provider call is faked, so these tests cover the parser's own logic —
today's-date injection into the system prompt, JSON decoding of the reply, and
the safe fallback for an unrecognized provider — without hitting the network.
"""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from backend.ai import commands


def test_unknown_provider_returns_safe_fallback(monkeypatch):
    monkeypatch.setattr(commands, 'get_provider_config', lambda: {'provider': 'unsupported'})
    result = commands.parse_voice_command([{'role': 'user', 'content': 'hi'}])
    assert result['action'] == 'none'
    assert result['speak']  # always something for TTS to say


def test_openai_path_parses_json_and_injects_today(monkeypatch):
    openai = pytest.importorskip('openai')
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured['messages'] = kwargs['messages']
            content = json.dumps({
                'action': 'create_todo', 'speak': 'Added a todo: milk.',
                'todo': {'title': 'milk'},
            })
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    monkeypatch.setattr(commands, 'get_provider_config', lambda: {
        'provider': 'openai', 'openai_api_key': 'x', 'model': 'gpt-test',
        'ollama_url': '', 'ollama_model': '',
    })

    result = commands.parse_voice_command([{'role': 'user', 'content': 'add milk to my list'}])
    assert result['action'] == 'create_todo'
    assert result['todo']['title'] == 'milk'

    system_msg = captured['messages'][0]
    assert system_msg['role'] == 'system'
    assert date.today().isoformat() in system_msg['content']  # relative-date anchor injected
