"""Unit tests for `backend.ai.recipes.parse_recipe` — confirms it calls the
shared chat_json helper (backend.ai.llm) with the recipe system prompt and
processes the result correctly."""
from backend.ai import recipes


def test_parse_recipe_parses_result(monkeypatch):
    def fake_chat_json(text, system=None, **kwargs):
        assert text == 'some scraped recipe text'
        assert system == recipes._RECIPE_SYSTEM
        return {
            'title': 'Pasta',
            'content': '## Ingredients\n- pasta\n\n## Instructions\n1. Boil it',
            'tags': ['italian', 'dinner'],
        }

    monkeypatch.setattr(recipes, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(recipes, 'chat_json', fake_chat_json)

    result = recipes.parse_recipe('some scraped recipe text')

    assert result == {
        'title': 'Pasta',
        'content': '## Ingredients\n- pasta\n\n## Instructions\n1. Boil it',
        'tags': ['italian', 'dinner'],
    }


def test_parse_recipe_none_when_no_title(monkeypatch):
    monkeypatch.setattr(recipes, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(recipes, 'chat_json', lambda text, system=None: {'title': None})

    assert recipes.parse_recipe('no recipe here') is None


def test_parse_recipe_none_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(recipes, 'is_ai_configured', lambda: False)
    assert recipes.parse_recipe('some scraped recipe text') is None
