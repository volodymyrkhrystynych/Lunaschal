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


def test_generate_recipe_invents_result(monkeypatch):
    def fake_chat_json(prompt, system=None, **kwargs):
        assert prompt == 'quick vegan chocolate cake'
        assert system == recipes._GENERATE_SYSTEM
        return {
            'title': 'Vegan Chocolate Cake',
            'content': '## Ingredients\n- cocoa\n\n## Instructions\n1. Bake it',
            'tags': ['vegan', 'dessert'],
        }

    monkeypatch.setattr(recipes, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(recipes, 'chat_json', fake_chat_json)

    result = recipes.generate_recipe('quick vegan chocolate cake')

    assert result == {
        'title': 'Vegan Chocolate Cake',
        'content': '## Ingredients\n- cocoa\n\n## Instructions\n1. Bake it',
        'tags': ['vegan', 'dessert'],
    }


def test_generate_recipe_none_when_not_food_related(monkeypatch):
    monkeypatch.setattr(recipes, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(recipes, 'chat_json', lambda prompt, system=None: {'title': None})

    assert recipes.generate_recipe('what is the capital of France') is None


def test_generate_recipe_none_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(recipes, 'is_ai_configured', lambda: False)
    assert recipes.generate_recipe('vegan chocolate cake') is None


def test_generate_recipe_none_when_prompt_blank():
    assert recipes.generate_recipe('   ') is None
