"""Ideas CRUD, voice capture, and Paper-page sketches.

No AI is involved at this phase — every route here is plain SQLite work.
"""
import time

from ulid import ULID

from backend.db.connection import get_db


def _create_idea(client, title='Habit tracking', raw='grid of habits in the day view'):
    r = client.post('/api/ideas', json={'title': title, 'rawContent': raw})
    assert r.status_code == 201
    return r.get_json()['id']


def _make_page(client, with_snapshot=True):
    """A paper + one page, inserted directly: the paper routes need a real PNG
    upload to set image_path, which isn't what these tests are about."""
    db = get_db()
    now = int(time.time())
    paper_id, page_id = str(ULID()), str(ULID())
    db.execute(
        'INSERT INTO papers(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (paper_id, 'Sketches', now, now),
    )
    db.execute(
        'INSERT INTO paper_pages(id, paper_id, position, strokes, image_path, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (page_id, paper_id, 0, '[]', '/data/paper/x.png' if with_snapshot else None, now, now),
    )
    db.commit()
    return paper_id, page_id


def test_create_and_get(client):
    idea_id = _create_idea(client)
    body = client.get(f'/api/ideas/{idea_id}').get_json()
    assert body['title'] == 'Habit tracking'
    assert body['rawContent'] == 'grid of habits in the day view'
    assert body['content'] == ''
    assert body['status'] == 'new'


def test_create_requires_title_or_content(client):
    assert client.post('/api/ideas', json={}).status_code == 400
    assert client.post('/api/ideas', json={'title': '  ', 'rawContent': ' '}).status_code == 400
    # Either one alone is enough.
    assert client.post('/api/ideas', json={'rawContent': 'just a thought'}).status_code == 201


def test_list_omits_body_columns_and_counts_sketches(client):
    idea_id = _create_idea(client)
    _, page_id = _make_page(client)
    client.post(f'/api/ideas/{idea_id}/sketches', json={'pageId': page_id})

    rows = client.get('/api/ideas').get_json()
    assert len(rows) == 1
    assert 'rawContent' not in rows[0]
    assert 'content' not in rows[0]
    assert rows[0]['sketchCount'] == 1


def test_list_orders_by_updated_desc(client):
    first = _create_idea(client, title='older')
    second = _create_idea(client, title='newer')
    get_db().execute('UPDATE ideas SET updated_at=? WHERE id=?', (1, first))
    get_db().commit()
    titles = [r['title'] for r in client.get('/api/ideas').get_json()]
    assert titles == ['newer', 'older']
    assert second  # created second, still listed first


def test_patch_fields_and_status_validation(client):
    idea_id = _create_idea(client)
    r = client.patch(f'/api/ideas/{idea_id}', json={'title': ' renamed ', 'status': 'ready'})
    assert r.status_code == 200
    body = client.get(f'/api/ideas/{idea_id}').get_json()
    assert body['title'] == 'renamed'
    assert body['status'] == 'ready'

    bad = client.patch(f'/api/ideas/{idea_id}', json={'status': 'nonsense'})
    assert bad.status_code == 400
    assert client.get(f'/api/ideas/{idea_id}').get_json()['status'] == 'ready'


def test_patch_normalizes_tags(client):
    idea_id = _create_idea(client)
    client.patch(f'/api/ideas/{idea_id}', json={'tags': ['  UI ', 'ui', 'Backend', '']})
    assert client.get(f'/api/ideas/{idea_id}').get_json()['tags'] == '["ui", "backend"]'


def test_raw_content_survives_a_content_write(client):
    """raw_content is what was actually said; only `content` is AI-owned."""
    idea_id = _create_idea(client, raw='um so like a habit grid thing')
    client.patch(f'/api/ideas/{idea_id}', json={'content': 'A habit grid in the day view.'})
    body = client.get(f'/api/ideas/{idea_id}').get_json()
    assert body['rawContent'] == 'um so like a habit grid thing'
    assert body['content'] == 'A habit grid in the day view.'


def test_get_missing_is_404(client):
    assert client.get('/api/ideas/nope').status_code == 404


def test_voice_capture(client):
    r = client.post('/api/ideas/voice', json={'rawContent': '  spoken idea  '})
    assert r.status_code == 201
    body = client.get(f"/api/ideas/{r.get_json()['id']}").get_json()
    assert body['rawContent'] == 'spoken idea'
    assert body['title'] == ''

    assert client.post('/api/ideas/voice', json={'rawContent': '   '}).status_code == 400


# --- Sketches ---

def test_add_list_and_caption_a_sketch(client):
    idea_id = _create_idea(client)
    _, page_id = _make_page(client)

    r = client.post(f'/api/ideas/{idea_id}/sketches',
                    json={'pageId': page_id, 'caption': 'two-panel layout'})
    assert r.status_code == 201
    sketch_id = r.get_json()['id']

    rows = client.get(f'/api/ideas/{idea_id}/sketches').get_json()
    assert len(rows) == 1
    assert rows[0]['caption'] == 'two-panel layout'
    assert rows[0]['imageUrl'].startswith(f'/api/paper/pages/{page_id}/image?v=')
    # Join internals stay server-side.
    assert 'imagePath' not in rows[0]

    client.patch(f'/api/ideas/sketches/{sketch_id}', json={'caption': 'revised'})
    assert client.get(f'/api/ideas/{idea_id}/sketches').get_json()[0]['caption'] == 'revised'


def test_sketch_positions_increment_and_reorder(client):
    idea_id = _create_idea(client)
    _, page_a = _make_page(client)
    _, page_b = _make_page(client)
    a = client.post(f'/api/ideas/{idea_id}/sketches', json={'pageId': page_a}).get_json()['id']
    client.post(f'/api/ideas/{idea_id}/sketches', json={'pageId': page_b})

    assert [s['position'] for s in client.get(f'/api/ideas/{idea_id}/sketches').get_json()] == [0, 1]
    client.patch(f'/api/ideas/sketches/{a}', json={'position': 5})
    order = [s['pageId'] for s in client.get(f'/api/ideas/{idea_id}/sketches').get_json()]
    assert order == [page_b, page_a]


def test_sketch_requires_existing_idea_and_page(client):
    idea_id = _create_idea(client)
    _, page_id = _make_page(client)
    assert client.post(f'/api/ideas/{idea_id}/sketches', json={}).status_code == 400
    assert client.post(f'/api/ideas/{idea_id}/sketches',
                       json={'pageId': 'missing'}).status_code == 404
    assert client.post('/api/ideas/missing/sketches',
                       json={'pageId': page_id}).status_code == 404


def test_delete_sketch(client):
    idea_id = _create_idea(client)
    _, page_id = _make_page(client)
    sketch_id = client.post(f'/api/ideas/{idea_id}/sketches',
                            json={'pageId': page_id}).get_json()['id']
    client.delete(f'/api/ideas/sketches/{sketch_id}')
    assert client.get(f'/api/ideas/{idea_id}/sketches').get_json() == []


def test_deleting_an_idea_cascades_its_sketches(client):
    idea_id = _create_idea(client)
    _, page_id = _make_page(client)
    client.post(f'/api/ideas/{idea_id}/sketches', json={'pageId': page_id})

    client.delete(f'/api/ideas/{idea_id}')
    assert client.get('/api/ideas').get_json() == []
    left = get_db().execute('SELECT COUNT(*) AS n FROM idea_sketches').fetchone()['n']
    assert left == 0


def test_deleting_a_paper_page_cascades_the_sketch(client):
    """A borrowed page that goes away takes the sketch row with it, rather than
    leaving a row whose image URL 404s."""
    idea_id = _create_idea(client)
    _, page_id = _make_page(client)
    client.post(f'/api/ideas/{idea_id}/sketches', json={'pageId': page_id})

    get_db().execute('DELETE FROM paper_pages WHERE id=?', (page_id,))
    get_db().commit()
    assert client.get(f'/api/ideas/{idea_id}/sketches').get_json() == []


# --- Paper page picker ---

def test_paper_pages_picker_lists_only_pages_with_snapshots(client):
    _, with_snap = _make_page(client, with_snapshot=True)
    _, without = _make_page(client, with_snapshot=False)

    rows = client.get('/api/ideas/paper-pages').get_json()
    ids = [r['pageId'] for r in rows]
    assert with_snap in ids
    assert without not in ids
    assert rows[0]['paperTitle'] == 'Sketches'
    assert rows[0]['imageUrl'].startswith(f'/api/paper/pages/{with_snap}/image?v=')


def test_paper_pages_route_is_not_shadowed_by_the_id_route(client):
    """/paper-pages must win over /<idea_id>; a 404 here means the rules collide."""
    assert client.get('/api/ideas/paper-pages').status_code == 200
