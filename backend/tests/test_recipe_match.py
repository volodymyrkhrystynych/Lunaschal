"""The homemade/existing-recipe match check: backend/ai/food.py's
classify_homemade_match (grammar-bounded matchIndex) and
backend/food/recipe_match.py's check_homemade_recipe_match (the branches that
decide whether a food entry gets offered a link).
"""
import json
import time

from ulid import ULID

from backend.ai import food as ai_food
from backend.db.connection import get_db
from backend.food import recipe_match
from backend.routes.cookbook import _insert_recipe


def _insert_entry(dish=None, place=None, notes=None, recipe_id=None, recipe_match_status=None):
    db = get_db()
    now = int(time.time())
    id = str(ULID())
    db.execute(
        'INSERT INTO food_entries(id, dish, place, notes, recipe_id, recipe_match_status,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        (id, dish, place, notes, recipe_id, recipe_match_status, now, now),
    )
    db.commit()
    return id


# --- classify_homemade_match ---


def test_classify_returns_none_without_candidates(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    assert ai_food.classify_homemade_match('Soup', None, None, []) is None


def test_classify_returns_none_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: False)
    candidates = [{'id': 'r1', 'title': 'Soup', 'tags': []}]
    assert ai_food.classify_homemade_match('Soup', None, None, candidates) is None


def test_classify_bounds_match_index_to_the_candidate_list(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    seen = {}

    def fake_chat_json(text, system, schema):
        seen['schema'] = schema
        return {'homemade': True, 'matchIndex': 2, 'confidence': 'high'}

    monkeypatch.setattr(ai_food, 'chat_json', fake_chat_json)
    candidates = [{'id': 'r1', 'title': 'A', 'tags': []}, {'id': 'r2', 'title': 'B', 'tags': ['x']}]
    result = ai_food.classify_homemade_match('B', None, None, candidates)

    bound = seen['schema']['properties']['matchIndex']['anyOf'][0]
    assert bound['minimum'] == 1 and bound['maximum'] == 2
    assert result == {'homemade': True, 'matchIndex': 2, 'confidence': 'high'}


def test_classify_discards_an_out_of_range_index(monkeypatch):
    """The schema already forbids this during decoding, but a defensive check
    here means a model quirk degrades to "no match" rather than an IndexError
    downstream in check_homemade_recipe_match."""
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ai_food, 'chat_json', lambda text, system, schema: {
        'homemade': True, 'matchIndex': 99, 'confidence': 'high',
    })
    candidates = [{'id': 'r1', 'title': 'A', 'tags': []}]
    result = ai_food.classify_homemade_match('A', None, None, candidates)
    assert result['matchIndex'] is None


def test_classify_defaults_a_bad_confidence_to_low(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ai_food, 'chat_json', lambda text, system, schema: {
        'homemade': False, 'matchIndex': None, 'confidence': 'sure!',
    })
    candidates = [{'id': 'r1', 'title': 'A', 'tags': []}]
    result = ai_food.classify_homemade_match('A', None, None, candidates)
    assert result['confidence'] == 'low'


def test_classify_returns_none_on_a_failed_call(monkeypatch):
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)

    def boom(*a, **k):
        raise RuntimeError('connection refused')

    monkeypatch.setattr(ai_food, 'chat_json', boom)
    candidates = [{'id': 'r1', 'title': 'A', 'tags': []}]
    assert ai_food.classify_homemade_match('A', None, None, candidates) is None


# --- check_homemade_recipe_match ---


def _status(entry_id):
    return get_db().execute(
        'SELECT recipe_match_status FROM food_entries WHERE id=?', (entry_id,)
    ).fetchone()['recipe_match_status']


def test_no_op_when_there_is_no_dish(monkeypatch):
    called = []
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: called.append(1))
    entry_id = _insert_entry(dish=None)
    recipe_match.check_homemade_recipe_match(entry_id)
    assert not called
    assert _status(entry_id) is None


def test_no_op_when_already_linked(monkeypatch):
    called = []
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: called.append(1))
    recipe_id = _insert_recipe('Soup', '## Ingredients\n- water', None)
    entry_id = _insert_entry(dish='Soup', recipe_id=recipe_id)
    recipe_match.check_homemade_recipe_match(entry_id)
    assert not called


def test_no_op_when_already_checked(monkeypatch):
    called = []
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: called.append(1))
    entry_id = _insert_entry(dish='Soup', recipe_match_status='none')
    recipe_match.check_homemade_recipe_match(entry_id)
    assert not called


def test_marks_none_when_the_collection_is_empty(monkeypatch):
    called = []
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: called.append(1))
    entry_id = _insert_entry(dish='Soup')
    recipe_match.check_homemade_recipe_match(entry_id)
    assert not called  # nothing to compare against, so the model is never asked
    assert _status(entry_id) == 'none'


def test_a_strong_match_proposes_a_link_in_todays_chat(monkeypatch):
    recipe_id = _insert_recipe('Grandma\'s Borscht', '## Ingredients\n- beets', None)
    entry_id = _insert_entry(dish='Borscht')
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: {
        'homemade': True, 'matchIndex': 1, 'confidence': 'high',
    })

    recipe_match.check_homemade_recipe_match(entry_id)

    assert _status(entry_id) == 'proposed'
    db = get_db()
    row = db.execute(
        "SELECT metadata FROM messages WHERE role='assistant'"
    ).fetchone()
    assert row is not None
    meta = json.loads(row['metadata'])
    proposal = meta['proposals'][0]
    assert proposal['kind'] == 'recipe_link'
    assert proposal['status'] == 'pending'
    assert proposal['data'] == {
        'entryId': entry_id, 'recipeId': recipe_id,
        'dish': 'Borscht', 'recipeTitle': "Grandma's Borscht",
    }


def test_low_confidence_does_not_propose_anything(monkeypatch):
    _insert_recipe('Borscht', '## Ingredients\n- beets', None)
    entry_id = _insert_entry(dish='Borscht')
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: {
        'homemade': True, 'matchIndex': 1, 'confidence': 'low',
    })

    recipe_match.check_homemade_recipe_match(entry_id)

    assert _status(entry_id) == 'none'
    assert get_db().execute("SELECT COUNT(*) c FROM messages").fetchone()['c'] == 0


def test_not_homemade_does_not_propose_anything(monkeypatch):
    _insert_recipe('Pad Thai', '## Ingredients\n- noodles', None)
    entry_id = _insert_entry(dish='Pad Thai')
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: {
        'homemade': False, 'matchIndex': None, 'confidence': 'high',
    })

    recipe_match.check_homemade_recipe_match(entry_id)

    assert _status(entry_id) == 'none'
    assert get_db().execute("SELECT COUNT(*) c FROM messages").fetchone()['c'] == 0


def test_a_failed_classification_leaves_the_status_unset(monkeypatch):
    """Unmarked rather than 'none', so a transient AI outage doesn't
    permanently block a future retrigger."""
    _insert_recipe('Borscht', '## Ingredients\n- beets', None)
    entry_id = _insert_entry(dish='Borscht')

    def boom(*a, **k):
        raise RuntimeError('boom')

    monkeypatch.setattr(recipe_match, 'classify_homemade_match', boom)
    recipe_match.check_homemade_recipe_match(entry_id)
    assert _status(entry_id) is None
