"""Chat-generated recipes (`propose_recipe`/`_accept_recipe`) and the
homemade-match link card (`recipe_link`/`_accept_recipe_link`).

propose_recipe mirrors propose_food_log's staging tests; recipe_link is
different in kind — its card is never model-authored, it's dropped in by
backend/food/recipe_match.py's background check, so these tests stage it the
same way test_chat_food_proposal.py stages a food card and exercise only the
accept/dismiss side.
"""
import json

import pytest

from backend.db.connection import get_db
from backend.delegate import tools
from backend.food import recipe_match
from backend.routes import chat as chat_routes
from backend.routes import food as food_routes


@pytest.fixture(autouse=True)
def sync_bg(monkeypatch):
    # Both routes' background hooks: creating a food entry below can trigger
    # structure_food_entry -> check_homemade_recipe_match on food.py's own
    # run_bg, independent of chat.py's.
    monkeypatch.setattr(chat_routes, 'run_bg', lambda fn: fn())
    monkeypatch.setattr(food_routes, 'run_bg', lambda fn: fn())
    # No AI configured in tests; keep the incidental background work quiet and
    # deterministic rather than letting it hit a real (refused) connection.
    monkeypatch.setattr(food_routes, 'parse_food_entry', lambda text, **kwargs: None)
    monkeypatch.setattr(recipe_match, 'classify_homemade_match', lambda *a, **k: None)


def _conversation_with_assistant_message(client):
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    assistant_id = client.post(
        f'/api/chat/conversations/{conv}/messages',
        json={'role': 'assistant', 'content': 'noted'},
    ).get_json()['id']
    return conv, assistant_id


def _stage(client, assistant_id, kind, data, proposal_id='p1'):
    meta = {'proposals': [{'id': proposal_id, 'status': 'pending', 'kind': kind, 'data': data}]}
    db = get_db()
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), assistant_id))
    db.commit()


def _accept(client, assistant_id, proposal_id='p1', data=None):
    body = {'action': 'accept'}
    if data is not None:
        body['data'] = data
    return client.post(f'/api/chat/proposals/{assistant_id}/{proposal_id}', json=body)


# --- propose_recipe staging ---


def test_propose_recipe_stages_a_recipe_proposal():
    text, event = tools.run_tool('propose_recipe', {
        'title': 'Weeknight Tonkotsu',
        'content': '## Ingredients\n- pork bones\n\n## Instructions\n1. Simmer 12h',
        'tags': ['japanese', 'ramen'],
    })
    assert event['proposal']['kind'] == 'recipe'
    assert event['proposal']['data']['title'] == 'Weeknight Tonkotsu'
    assert 'Nothing has been saved yet' in text


def test_propose_recipe_needs_a_title():
    _, event = tools.run_tool('propose_recipe', {'title': '  ', 'content': 'stuff'})
    assert event['ok'] is False


def test_propose_recipe_needs_content():
    _, event = tools.run_tool('propose_recipe', {'title': 'Soup', 'content': ''})
    assert event['ok'] is False


def test_propose_recipe_tags_default_to_empty():
    _, event = tools.run_tool('propose_recipe', {'title': 'Soup', 'content': 'stuff'})
    assert event['proposal']['data']['tags'] == []


# --- Accepting a recipe card ---


def test_accepting_writes_a_real_recipe(client):
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe', {
        'title': 'Weeknight Tonkotsu', 'content': '## Ingredients\n- pork bones',
        'tags': ['japanese'],
    })
    r = _accept(client, assistant_id)
    assert r.status_code == 200
    recipe_id = r.get_json()['proposal']['result']['id']

    got = client.get(f'/api/cookbook/{recipe_id}')
    assert got.status_code == 200
    assert got.get_json()['title'] == 'Weeknight Tonkotsu'


def test_accepting_a_recipe_with_no_title_is_rejected(client):
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe', {'title': '', 'content': 'stuff'})
    assert _accept(client, assistant_id).status_code == 400


def test_editing_a_recipe_card_replaces_the_staged_content(client):
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe', {'title': 'Draft', 'content': 'draft content'})
    r = _accept(client, assistant_id, data={'title': 'Final Title', 'content': 'final content'})
    recipe_id = r.get_json()['proposal']['result']['id']
    got = client.get(f'/api/cookbook/{recipe_id}').get_json()
    assert got['title'] == 'Final Title'
    assert got['content'] == 'final content'


def test_dismissing_a_recipe_card_writes_nothing(client):
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe', {'title': 'Soup', 'content': 'stuff'})
    client.post(f'/api/chat/proposals/{assistant_id}/p1', json={'action': 'dismiss'})
    assert get_db().execute('SELECT COUNT(*) c FROM recipes').fetchone()['c'] == 0


# --- Accepting/dismissing a recipe_link card ---


def _seed_entry_and_recipe(client):
    db = get_db()
    recipe_id = client.post('/api/cookbook', json={
        'title': 'Grandma\'s Borscht', 'content': '## Ingredients\n- beets',
    }).get_json()['id']
    entry_id = client.post('/api/food', json={'text': 'made borscht', 'dish': 'Borscht'}).get_json()['id']
    return entry_id, recipe_id


def test_accepting_a_recipe_link_sets_the_entrys_recipe_id(client):
    entry_id, recipe_id = _seed_entry_and_recipe(client)
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe_link', {
        'entryId': entry_id, 'recipeId': recipe_id, 'dish': 'Borscht', 'recipeTitle': "Grandma's Borscht",
    })
    r = _accept(client, assistant_id)
    assert r.status_code == 200
    assert r.get_json()['proposal']['result']['recipeId'] == recipe_id

    entry = client.get(f'/api/food/{entry_id}').get_json()
    assert entry['recipe']['id'] == recipe_id


def test_dismissing_a_recipe_link_leaves_the_entry_unlinked(client):
    entry_id, recipe_id = _seed_entry_and_recipe(client)
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe_link', {
        'entryId': entry_id, 'recipeId': recipe_id, 'dish': 'Borscht', 'recipeTitle': "Grandma's Borscht",
    })
    client.post(f'/api/chat/proposals/{assistant_id}/p1', json={'action': 'dismiss'})
    entry = client.get(f'/api/food/{entry_id}').get_json()
    assert entry['recipe'] is None


def test_accepting_a_recipe_link_for_a_missing_entry_is_rejected(client):
    _, recipe_id = _seed_entry_and_recipe(client)
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe_link', {
        'entryId': 'nonexistent', 'recipeId': recipe_id, 'dish': 'Borscht', 'recipeTitle': 'x',
    })
    assert _accept(client, assistant_id).status_code == 400


def test_accepting_a_recipe_link_for_a_missing_recipe_is_rejected(client):
    entry_id, _ = _seed_entry_and_recipe(client)
    conv, assistant_id = _conversation_with_assistant_message(client)
    _stage(client, assistant_id, 'recipe_link', {
        'entryId': entry_id, 'recipeId': 'nonexistent', 'dish': 'Borscht', 'recipeTitle': 'x',
    })
    assert _accept(client, assistant_id).status_code == 400
