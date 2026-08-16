"""backend/ai/food.py's parse_food_entry: structuring a raw food note, and
the memory-document reference it now shares with Journal's Polish and Ideas'
capture for fixing a misheard dish/place name.
"""
from backend.ai import food as ai_food


def _fake_response(**over):
    return {
        'notes': 'Rich broth, would go again.',
        'dish': None,
        'place': None,
        'rating': None,
        'tags': [],
        'recipe': None,
        **over,
    }


def test_returns_none_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: False)
    assert ai_food.parse_food_entry('ramen at Kinton') is None


def test_returns_none_for_blank_text(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    assert ai_food.parse_food_entry('   ') is None


def test_prompt_carries_no_context_block_without_memory(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    seen = {}

    def _fake(prompt, system=None, schema=None):
        seen['prompt'] = prompt
        return _fake_response()
    monkeypatch.setattr(ai_food, 'chat_json', _fake)

    ai_food.parse_food_entry('ramen at Kinton')
    assert seen['prompt'] == 'ramen at Kinton'


def test_prompt_carries_the_memory_document_as_context(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    seen = {}

    def _fake(prompt, system=None, schema=None):
        seen['prompt'] = prompt
        return _fake_response()
    monkeypatch.setattr(ai_food, 'chat_json', _fake)

    ai_food.parse_food_entry(
        'ramen at kin tin', memory='Their favourite ramen spot is Kinton.'
    )
    assert 'ramen at kin tin' in seen['prompt']
    assert 'Their favourite ramen spot is Kinton.' in seen['prompt']
