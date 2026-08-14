"""Route tests for the cookbook (`backend/routes/cookbook.py`).

LLM parsing and URL fetching are mocked so these tests cover the routes' own
logic — CRUD, FTS search (including the index triggers), tag aggregation and
filtering, media upload/serve/delete, and the import endpoint's validation —
with no network calls.
"""
import io

import pytest

from backend.db.connection import get_db
from backend.routes import cookbook


@pytest.fixture(autouse=True)
def recipe_root(monkeypatch, tmp_path):
    root = tmp_path / 'recipes'
    monkeypatch.setenv('RECIPE_ROOT', str(root))
    return root


def _create(client, title='Borscht', content='Beets, beef, simmer.', tags=None):
    r = client.post('/api/cookbook', json={'title': title, 'content': content, 'tags': tags})
    assert r.status_code == 201
    return r.get_json()['id']


def _create_multipart(client, media=None, **fields):
    data = {'title': 'Borscht', 'content': 'Beets, beef, simmer.', **fields}
    if media is not None:
        data['media'] = media
    return client.post('/api/cookbook', data=data, content_type='multipart/form-data')


def _file(name='photo.jpg', content=b'JPEGBYTES'):
    return (io.BytesIO(content), name)


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


# --- Generate ---

def test_generate_persists_invented_recipe(client, monkeypatch):
    monkeypatch.setattr(cookbook, 'generate_recipe', lambda prompt: {
        'title': 'Vegan Chocolate Cake', 'content': '## Ingredients\n- cocoa', 'tags': ['vegan'],
    })
    r = client.post('/api/cookbook/generate', json={'prompt': 'vegan chocolate cake'})
    assert r.status_code == 201
    data = r.get_json()
    assert data['recipe']['title'] == 'Vegan Chocolate Cake'
    assert data['recipe']['sourceUrl'] is None
    assert client.get(f"/api/cookbook/{data['id']}").status_code == 200


def test_generate_requires_prompt(client):
    assert client.post('/api/cookbook/generate', json={}).status_code == 400
    assert client.post('/api/cookbook/generate', json={'prompt': '  '}).status_code == 400


def test_generate_unproducible_is_422_and_persists_nothing(client, monkeypatch):
    monkeypatch.setattr(cookbook, 'generate_recipe', lambda prompt: None)
    r = client.post('/api/cookbook/generate', json={'prompt': 'what is the capital of France'})
    assert r.status_code == 422
    assert client.get('/api/cookbook').get_json() == []


# --- Media ---

def test_create_with_media_saves_file_and_serves_it(client, recipe_root):
    r = _create_multipart(client, media=_file(content=b'JPEGBYTES'))
    assert r.status_code == 201
    body = r.get_json()
    assert len(body['media']) == 1
    m = body['media'][0]
    assert m['kind'] == 'image'
    assert (recipe_root / body['id']).is_dir()

    served = client.get(m['url'])
    assert served.status_code == 200
    assert served.data == b'JPEGBYTES'


def test_video_media_detected_as_video(client):
    r = _create_multipart(client, media=_file(name='clip.mov', content=b'MOVDATA'))
    assert r.get_json()['media'][0]['kind'] == 'video'


def test_audio_media_detected_as_audio_and_served(client):
    audio = (io.BytesIO(b'MP3BYTES'), 'note.mp3', 'audio/mpeg')
    r = client.post(
        '/api/cookbook',
        data={'title': 'Borscht', 'content': 'Beets, beef, simmer.', 'media': audio},
        content_type='multipart/form-data',
    )
    assert r.status_code == 201
    m = r.get_json()['media'][0]
    assert m['kind'] == 'audio'
    served = client.get(m['url'])
    assert served.status_code == 200
    assert served.data == b'MP3BYTES'


def test_video_and_audio_webm_do_not_collide(client):
    """audio/webm must not be classified as the 'video' kind_for_ext('webm') path."""
    audio = (io.BytesIO(b'WEBMAUDIO'), 'note.webm', 'audio/webm')
    r = client.post(
        '/api/cookbook',
        data={'title': 'Borscht', 'content': 'Beets, beef, simmer.', 'media': audio},
        content_type='multipart/form-data',
    )
    assert r.get_json()['media'][0]['kind'] == 'audio'


def test_multipart_create_accepts_tags_as_json_array(client):
    r = _create_multipart(client, tags='["soup", "beets"]')
    body = r.get_json()
    assert body['tags'] == '["soup", "beets"]'


def test_multipart_create_accepts_tags_as_comma_string(client):
    r = _create_multipart(client, tags='soup, beets')
    body = r.get_json()
    assert body['tags'] == '["soup", "beets"]'


def test_add_media_to_existing_recipe(client):
    id = _create(client)
    r = client.post(f'/api/cookbook/{id}/media', data={'media': _file()},
                    content_type='multipart/form-data')
    assert r.status_code == 201
    assert len(client.get(f'/api/cookbook/{id}').get_json()['media']) == 1


def test_delete_single_media(client):
    id = _create(client)
    added = client.post(f'/api/cookbook/{id}/media', data={'media': _file()},
                        content_type='multipart/form-data').get_json()
    media_id = added['media'][0]['id']

    r = client.delete(f'/api/cookbook/media/{media_id}')
    assert r.status_code == 200
    assert client.get(f'/api/cookbook/media/{media_id}').status_code == 404
    assert client.get(f'/api/cookbook/{id}').get_json()['media'] == []


def test_delete_recipe_removes_media_and_dir(client, recipe_root):
    r = _create_multipart(client, media=_file())
    body = r.get_json()
    id, media_id = body['id'], body['media'][0]['id']
    assert (recipe_root / id).is_dir()

    assert client.delete(f'/api/cookbook/{id}').status_code == 200
    assert client.get(f'/api/cookbook/media/{media_id}').status_code == 404  # cascaded
    assert not (recipe_root / id).exists()


def test_media_missing_404s(client):
    assert client.get('/api/cookbook/media/nope').status_code == 404
    assert client.delete('/api/cookbook/media/nope').status_code == 404
    assert client.post('/api/cookbook/nope/media', data={'media': _file()},
                       content_type='multipart/form-data').status_code == 404


# --- HTML stripping (pure unit) ---

def test_strip_html_drops_script_and_style():
    html = '<html><head><style>p{}</style></head><body><script>evil()</script><p>Hello</p><p>World</p></body></html>'
    text = cookbook._strip_html(html)
    assert 'Hello' in text and 'World' in text
    assert 'evil' not in text and 'p{}' not in text
