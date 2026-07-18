"""Unit tests for the Ollama branch of `backend.ai.recipes.parse_recipe` —
confirms it uses the single configured Ollama model directly (no CPU-forcing
fallback helper, which recipes.py used to depend on via journal.py)."""
from types import SimpleNamespace

import pytest

from backend.ai import recipes


def _fake_openai(monkeypatch, content: str):
    openai = pytest.importorskip('openai')
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured['kwargs'] = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    return captured


def test_parse_recipe_uses_configured_model_no_cpu_options(monkeypatch):
    content = (
        '{"title": "Pasta", "content": "## Ingredients\\n- pasta\\n\\n## Instructions\\n1. Boil it",'
        ' "tags": ["italian", "dinner"]}'
    )
    captured = _fake_openai(monkeypatch, content)
    monkeypatch.setattr(recipes, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(recipes, 'get_provider_config', lambda: {
        'ollama_url': 'http://localhost:11434',
        'ollama_model': 'llama3.2',
    })

    result = recipes.parse_recipe('some scraped recipe text')

    assert result == {
        'title': 'Pasta',
        'content': '## Ingredients\n- pasta\n\n## Instructions\n1. Boil it',
        'tags': ['italian', 'dinner'],
    }
    assert captured['kwargs']['model'] == 'llama3.2'
    assert 'extra_body' not in captured['kwargs']
    assert captured['kwargs']['response_format'] == {'type': 'json_object'}
