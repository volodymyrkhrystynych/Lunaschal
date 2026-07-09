"""Route tests for the cookbook (`backend/routes/cookbook.py`).

LLM parsing and URL fetching are mocked so these tests cover the routes' own
logic — CRUD, FTS search (including the index triggers), tag aggregation and
filtering, and the import endpoint's validation — with no network calls.
"""
import pytest

from backend.db.connection import get_db
from backend.routes import cookbook


@pytest.fixture(autouse=True)
def _no_bg_embeddings(monkeypatch):
    monkeypatch.setattr(cookbook, '_sync_embeddings_bg', lambda recipe_id: None)


def _create(client, title='Borscht', content='Beets, beef, simmer.', tags=None):
    r = client.post('/api/cookbook', json={'title': title, 'content': content, 'tags': tags})
    assert r.status_code == 201
    return r.get_json()['id']


# --- CRUD ---

def test_create_and_get(client):
    id = _create(client, tags=['soup', 'ukrainian'])
    data = client.get(f'/api/cookbook/{id}').get_json()
    assert data['title'] == 'Borscht'
    assert data['content'] == 'Beets, beef, simmer.'
    assert data['tags'] == '["soup", "ukrainian"]'
    assert data['sourceUrl'] is None
    assert data['createdAt']


def test_create_requires_title_and_content(client):
    assert client.post('/api/cookbook', json={'title': 'X'}).status_code == 400
    assert client.post('/api/cookbook', json={'content': 'Y'}).status_code == 400
    assert client.post('/api/cookbook', json={'title': ' ', 'content': 'Y'}).status_code == 400


def test_list_orders_newest_first(client):
    first = _create(client, title='First')
    second = _create(client, title='Second')
    ids = [r['id'] for r in client.get('/api/cookbook').get_json()]
    assert set(ids) == {first, second}
    # Same created_at second is possible; ULIDs are monotonic so DESC keeps insert order stable.
    recipes = client.get('/api/cookbook').get_json()
    assert len(recipes) == 2


def test_patch_updates_fields(client):
    id = _create(client)
    r = client.patch(f'/api/cookbook/{id}', json={'title': 'Green Borscht', 'tags': ['soup']})
    assert r.get_json()['success'] is True
    data = client.get(f'/api/cookbook/{id}').get_json()
    assert data['title'] == 'Green Borscht'
    assert data['tags'] == '["soup"]'


def test_delete_removes_recipe(client):
    id = _create(client)
    assert client.delete(f'/api/cookbook/{id}').get_json()['success'] is True
    assert client.get(f'/api/cookbook/{id}').status_code == 404


def test_get_missing_is_404(client):
    assert client.get('/api/cookbook/nope').status_code == 404


# --- FTS search ---

def test_search_matches_prefix(client):
    _create(client, title='Chicken curry', content='Chicken thighs, curry paste, coconut milk.')
    _create(client, title='Borscht', content='Beets and beef.')
    results = client.get('/api/cookbook/search?query=chick').get_json()
    assert [r['title'] for r in results] == ['Chicken curry']


def test_search_no_match_is_empty(client):
    _create(client)
    assert client.get('/api/cookbook/search?query=zzzz').get_json() == []
    assert client.get('/api/cookbook/search?query=').get_json() == []


def test_search_index_follows_update_and_delete(client):
    id = _create(client, title='Pancakes', content='Flour and milk.')
    client.patch(f'/api/cookbook/{id}', json={'content': 'Buckwheat flour and kefir.'})
    assert [r['id'] for r in client.get('/api/cookbook/search?query=buckwheat').get_json()] == [id]

    client.delete(f'/api/cookbook/{id}')
    assert client.get('/api/cookbook/search?query=buckwheat').get_json() == []


def test_search_matches_tags(client):
    id = _create(client, tags=['ukrainian'])
    assert [r['id'] for r in client.get('/api/cookbook/search?query=ukrainian').get_json()] == [id]


# --- Tags ---

def test_tags_aggregation_counts(client):
    _create(client, title='A', tags=['soup', 'quick'])
    _create(client, title='B', tags=['soup'])
    _create(client, title='C')
    assert client.get('/api/cookbook/tags').get_json() == [
        {'name': 'soup', 'count': 2},
        {'name': 'quick', 'count': 1},
    ]


def test_list_filters_by_tag(client):
    soup = _create(client, title='Soup', tags=['soup'])
    _create(client, title='Cake', tags=['dessert'])
    ids = [r['id'] for r in client.get('/api/cookbook?tag=soup').get_json()]
    assert ids == [soup]


# --- Import ---

def test_import_text_persists_parsed_recipe(client, monkeypatch):
    monkeypatch.setattr(cookbook, 'parse_recipe', lambda text: {
        'title': 'Pancakes', 'content': '## Ingredients\n- flour', 'tags': ['breakfast'],
    })
    r = client.post('/api/cookbook/import', json={'text': 'some pasted recipe'})
    assert r.status_code == 201
    data = r.get_json()
    assert data['recipe']['title'] == 'Pancakes'
    assert data['recipe']['sourceUrl'] is None
    assert client.get(f"/api/cookbook/{data['id']}").status_code == 200


def test_import_url_records_source(client, monkeypatch):
    monkeypatch.setattr(cookbook, '_fetch_url_text', lambda url: 'page text with a recipe')
    monkeypatch.setattr(cookbook, 'parse_recipe', lambda text: {
        'title': 'Ramen', 'content': '## Ingredients\n- noodles', 'tags': [],
    })
    r = client.post('/api/cookbook/import', json={'url': 'https://example.com/ramen'})
    assert r.status_code == 201
    assert r.get_json()['recipe']['sourceUrl'] == 'https://example.com/ramen'


def test_import_unparseable_is_422_and_persists_nothing(client, monkeypatch):
    monkeypatch.setattr(cookbook, 'parse_recipe', lambda text: None)
    r = client.post('/api/cookbook/import', json={'text': 'my grocery list'})
    assert r.status_code == 422
    assert client.get('/api/cookbook').get_json() == []


def test_import_requires_exactly_one_of_text_or_url(client):
    assert client.post('/api/cookbook/import', json={}).status_code == 400
    assert client.post('/api/cookbook/import', json={'text': 'a', 'url': 'https://b'}).status_code == 400


def test_import_rejects_non_http_url(client):
    assert client.post('/api/cookbook/import', json={'url': 'file:///etc/passwd'}).status_code == 400


# --- HTML stripping (pure unit) ---

def test_strip_html_drops_script_and_style():
    html = '<html><head><style>p{}</style></head><body><script>evil()</script><p>Hello</p><p>World</p></body></html>'
    text = cookbook._strip_html(html)
    assert 'Hello' in text and 'World' in text
    assert 'evil' not in text and 'p{}' not in text
