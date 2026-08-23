import io
import json
import time

import pytest

from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def paper_root(monkeypatch, tmp_path):
    root = tmp_path / 'paper'
    monkeypatch.setenv('PAPER_ROOT', str(root))
    return root


def _create(client):
    r = client.post('/api/paper')
    assert r.status_code == 201
    return r.get_json()['id']


def _save_page(client, page_id, strokes, width=800, height=1000, png=b'\x89PNG-fake'):
    return client.put(
        f'/api/paper/pages/{page_id}',
        data={
            'strokes': json.dumps(strokes),
            'width': str(width),
            'height': str(height),
            'snapshot': (io.BytesIO(png), 'snapshot.png'),
        },
        content_type='multipart/form-data',
    )


def test_create_makes_first_page_and_lists(client):
    paper_id = _create(client)

    lst = client.get('/api/paper').get_json()
    assert len(lst) == 1
    assert lst[0]['id'] == paper_id
    assert lst[0]['pageCount'] == 1
    assert lst[0]['firstPageImageUrl'] is None  # no snapshot saved yet

    detail = client.get(f'/api/paper/{paper_id}').get_json()
    assert len(detail['pages']) == 1
    assert detail['pages'][0]['position'] == 0


def test_add_pages_increment_position(client):
    paper_id = _create(client)
    p1 = client.post(f'/api/paper/{paper_id}/pages').get_json()
    p2 = client.post(f'/api/paper/{paper_id}/pages').get_json()
    assert p1['position'] == 1
    assert p2['position'] == 2

    pages = client.get(f'/api/paper/{paper_id}').get_json()['pages']
    assert [pg['position'] for pg in pages] == [0, 1, 2]


def test_save_page_persists_strokes_and_image(client, paper_root):
    paper_id = _create(client)
    page_id = client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']

    strokes = [{'eraser': False, 'points': [{'x': 1, 'y': 2, 'pressure': 0.5}]}]
    r = _save_page(client, page_id, strokes, png=b'PNGDATA')
    assert r.status_code == 200

    # Strokes come back for editing.
    content = client.get(f'/api/paper/pages/{page_id}').get_json()
    assert json.loads(content['strokes']) == strokes
    assert content['width'] == 800
    assert content['height'] == 1000

    # PNG is on disk and served back.
    assert (paper_root / paper_id / f'{page_id}.png').read_bytes() == b'PNGDATA'
    img = client.get(f'/api/paper/pages/{page_id}/image')
    assert img.status_code == 200
    assert img.mimetype == 'image/png'
    assert img.data == b'PNGDATA'

    # First-page thumbnail URL now present in the list.
    first_url = client.get('/api/paper').get_json()[0]['firstPageImageUrl']
    assert first_url and first_url.startswith(f'/api/paper/pages/{page_id}/image')


def test_save_page_accepts_strokes_as_a_file_part(client):
    """The client uploads strokes as a file part, not a text field."""
    paper_id = _create(client)
    page_id = client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']

    strokes = [{'tool': 'pen', 'size': 4, 'points': [{'x': 1, 'y': 2, 'pressure': 0.5}]}]
    r = client.put(
        f'/api/paper/pages/{page_id}',
        data={
            'strokes': (io.BytesIO(json.dumps(strokes).encode()), 'strokes.json'),
            'width': '800',
            'height': '1000',
            'snapshot': (io.BytesIO(b'PNG'), 'snapshot.png'),
        },
        content_type='multipart/form-data',
    )
    assert r.status_code == 200
    content = client.get(f'/api/paper/pages/{page_id}').get_json()
    assert json.loads(content['strokes']) == strokes


def test_save_page_accepts_payload_larger_than_the_form_field_cap(client):
    """A densely written page exceeds Werkzeug's 500kB max_form_memory_size.
    Sent as a file part it must still be accepted (this was a 413)."""
    paper_id = _create(client)
    page_id = client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']

    strokes = [
        {
            'tool': 'pen',
            'size': 4,
            'points': [{'x': i / 10, 'y': i / 10, 'pressure': 0.5} for i in range(20000)],
        }
    ]
    payload = json.dumps(strokes).encode()
    assert len(payload) > 500_000  # would be rejected as a plain form field

    r = client.put(
        f'/api/paper/pages/{page_id}',
        data={
            'strokes': (io.BytesIO(payload), 'strokes.json'),
            'width': '800',
            'height': '1000',
            'snapshot': (io.BytesIO(b'PNG'), 'snapshot.png'),
        },
        content_type='multipart/form-data',
    )
    assert r.status_code == 200
    content = client.get(f'/api/paper/pages/{page_id}').get_json()
    assert len(json.loads(content['strokes'])[0]['points']) == 20000


def test_save_page_without_size_keeps_the_stored_coordinate_space(client):
    """Omitting width/height must not NULL them — strokes are stored in that
    space, so losing it would misplace every stroke on the next load."""
    paper_id = _create(client)
    page_id = client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']
    _save_page(client, page_id, [], width=800, height=1000)

    r = client.put(
        f'/api/paper/pages/{page_id}',
        data={'strokes': (io.BytesIO(b'[]'), 'strokes.json')},
        content_type='multipart/form-data',
    )
    assert r.status_code == 200
    content = client.get(f'/api/paper/pages/{page_id}').get_json()
    assert content['width'] == 800
    assert content['height'] == 1000


def test_update_title(client):
    paper_id = _create(client)
    r = client.patch(f'/api/paper/{paper_id}', json={'title': 'Sketches'})
    assert r.status_code == 200
    assert client.get(f'/api/paper/{paper_id}').get_json()['title'] == 'Sketches'


def test_delete_cascades_and_removes_dir(client, paper_root):
    paper_id = _create(client)
    page_id = client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']
    _save_page(client, page_id, [{'eraser': False, 'points': [{'x': 0, 'y': 0, 'pressure': 1}]}])
    assert (paper_root / paper_id).is_dir()

    r = client.delete(f'/api/paper/{paper_id}')
    assert r.status_code == 200

    assert client.get('/api/paper').get_json() == []
    assert client.get(f'/api/paper/{paper_id}').status_code == 404
    assert client.get(f'/api/paper/pages/{page_id}').status_code == 404  # cascaded
    assert not (paper_root / paper_id).exists()


def test_delete_single_page(client, paper_root):
    paper_id = _create(client)
    extra = client.post(f'/api/paper/{paper_id}/pages').get_json()['id']
    _save_page(client, extra, [])
    assert (paper_root / paper_id / f'{extra}.png').is_file()

    r = client.delete(f'/api/paper/pages/{extra}')
    assert r.status_code == 200
    assert not (paper_root / paper_id / f'{extra}.png').exists()
    assert len(client.get(f'/api/paper/{paper_id}').get_json()['pages']) == 1


def test_flag_stays_in_explorer_until_4am_then_moves_to_journal(client):
    paper_id = _create(client)
    page_id = client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']
    _save_page(client, page_id, [])

    # Freshly flagged: still in the explorer (pending), not yet in the journal.
    client.patch(f'/api/paper/{paper_id}', json={'archiveRequested': True})
    listed = client.get('/api/paper').get_json()
    assert any(p['id'] == paper_id and p['pendingArchive'] for p in listed)
    assert client.get(f'/api/paper/{paper_id}').get_json()['archiveRequested'] is True
    assert client.get('/api/paper/journal').get_json() == []

    # Simulate a 4am boundary passing by back-dating the flag two days.
    db = get_db()
    db.execute(
        'UPDATE papers SET archive_requested_at=? WHERE id=?',
        (int(time.time()) - 2 * 86400, paper_id),
    )
    db.commit()

    # Now gone from the explorer and present in the journal with its pages.
    assert all(p['id'] != paper_id for p in client.get('/api/paper').get_json())
    journal = client.get('/api/paper/journal').get_json()
    assert len(journal) == 1
    jp = journal[0]
    assert jp['id'] == paper_id
    assert jp['journalDate']  # a YYYY-MM-DD string
    assert len(jp['pages']) == 1
    assert jp['pages'][0]['imageUrl'].startswith(f'/api/paper/pages/{page_id}/image')


def test_unflagging_returns_paper_to_explorer(client):
    paper_id = _create(client)
    client.patch(f'/api/paper/{paper_id}', json={'archiveRequested': True})
    client.patch(f'/api/paper/{paper_id}', json={'archiveRequested': False})
    listed = client.get('/api/paper').get_json()
    match = [p for p in listed if p['id'] == paper_id]
    assert match and match[0]['pendingArchive'] is False
    assert client.get('/api/paper/journal').get_json() == []


def test_missing_page_and_paper_404(client):
    assert client.get('/api/paper/nope').status_code == 404
    assert client.get('/api/paper/pages/nope').status_code == 404
    assert client.get('/api/paper/pages/nope/image').status_code == 404
    assert client.post('/api/paper/nope/pages').status_code == 404


# --- pasted images ---

def _first_page(client, paper_id):
    return client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']


def _add_image(client, page_id, name='pic.png', data=b'\x89PNG-fake', mime=None, **over):
    form = {'x': '100', 'y': '200', 'width': '400', 'height': '300'}
    form.update({k: str(v) for k, v in over.items()})
    form['image'] = (io.BytesIO(data), name) if mime is None else (io.BytesIO(data), name, mime)
    return client.post(
        f'/api/paper/pages/{page_id}/images',
        data=form,
        content_type='multipart/form-data',
    )


def _jpeg(size=(8, 8)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', size, (120, 120, 120)).save(buf, 'JPEG')
    return buf.getvalue()


def test_add_image_stores_the_file_and_returns_placement(client, paper_root):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)

    resp = _add_image(client, page_id)
    assert resp.status_code == 201, resp.get_json()
    img = resp.get_json()
    assert (img['x'], img['y'], img['width'], img['height']) == (100, 200, 400, 300)
    assert img['rotation'] == 0 and img['flipped'] == 0 and img['locked'] == 0
    # The stored path is server-side detail; the client gets a URL.
    assert 'filePath' not in img
    assert img['url'].startswith(f"/api/paper/images/{img['id']}/file")

    stored = list((paper_root / paper_id).glob('img-*.png'))
    assert len(stored) == 1
    assert stored[0].read_bytes() == b'\x89PNG-fake'


def test_page_read_includes_its_images_in_draw_order(client):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    first = _add_image(client, page_id).get_json()
    second = _add_image(client, page_id).get_json()

    page = client.get(f'/api/paper/pages/{page_id}').get_json()
    assert [i['id'] for i in page['images']] == [first['id'], second['id']]
    assert [i['position'] for i in page['images']] == [0, 1]


def test_add_image_accepts_the_heic_an_ipad_hands_over(client, paper_root):
    """The regression this pins: paper matched on the filename's extension
    alone, and an iPad's photo library hands over `IMG_0042.HEIC`. Every picture
    put on a page from the tablet the feature exists for was answered 400 — and
    since the paste kept showing it until the page was left, the loss was
    invisible until the picture was already gone."""
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)

    resp = _add_image(client, page_id, name='IMG_0042.HEIC', data=_jpeg(),
                      mime='image/heic')
    assert resp.status_code == 201, resp.get_json()

    # Stored as JPEG: no browser renders HEIC, so a page would be carrying a
    # picture it could never draw.
    stored = list((paper_root / paper_id).glob('img-*'))
    assert len(stored) == 1 and stored[0].suffix == '.jpg'
    served = client.get(resp.get_json()['url'])
    assert served.status_code == 200
    assert served.mimetype == 'image/jpeg'
    assert served.data.startswith(b'\xff\xd8\xff')


def test_add_image_reads_the_mime_type_when_the_filename_has_no_extension(client, paper_root):
    """A clipboard picture has no name of its own, so the mime type is all
    there is to go on."""
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)

    resp = _add_image(client, page_id, name='pasted', data=_jpeg(), mime='image/jpeg')
    assert resp.status_code == 201, resp.get_json()
    assert [p.suffix for p in (paper_root / paper_id).glob('img-*')] == ['.jpg']


def test_a_heic_that_cannot_be_decoded_is_refused_and_leaves_nothing_behind(
    client, paper_root
):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)

    resp = _add_image(client, page_id, name='broken.heic', data=b'not an image',
                      mime='image/heic')
    assert resp.status_code == 400
    assert list((paper_root / paper_id).glob('img-*')) == []
    assert client.get(f'/api/paper/pages/{page_id}').get_json()['images'] == []


def test_add_image_rejects_an_unsupported_type(client):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    # An .svg served from our own origin is a script vector, which is why the
    # extension list is closed rather than "anything that isn't obviously bad".
    resp = _add_image(client, page_id, name='payload.svg')
    assert resp.status_code == 400
    assert 'unsupported' in resp.get_json()['error']


def test_add_image_rejects_a_zero_size_box(client):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    assert _add_image(client, page_id, width=0).status_code == 400


def test_transform_updates_geometry(client):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    img = _add_image(client, page_id).get_json()

    resp = client.patch(
        f"/api/paper/images/{img['id']}",
        data=json.dumps({'rotation': 45, 'flipped': True, 'x': 10, 'width': 50}),
        content_type='application/json',
    )
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert out['rotation'] == 45 and out['flipped'] == 1
    assert out['x'] == 10 and out['width'] == 50
    # Untouched fields survive.
    assert out['y'] == 200 and out['height'] == 300


def test_a_locked_image_refuses_geometry_changes(client):
    """The lock has to hold server-side: an in-flight drag can land after it."""
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    img = _add_image(client, page_id).get_json()
    client.patch(f"/api/paper/images/{img['id']}", data=json.dumps({'locked': True}),
                 content_type='application/json')

    resp = client.patch(f"/api/paper/images/{img['id']}", data=json.dumps({'x': 999}),
                        content_type='application/json')
    assert resp.status_code == 409
    page = client.get(f'/api/paper/pages/{page_id}').get_json()
    assert page['images'][0]['x'] == 100


def test_a_locked_image_can_still_be_unlocked(client):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    img = _add_image(client, page_id).get_json()
    client.patch(f"/api/paper/images/{img['id']}", data=json.dumps({'locked': True}),
                 content_type='application/json')

    resp = client.patch(f"/api/paper/images/{img['id']}", data=json.dumps({'locked': False}),
                        content_type='application/json')
    assert resp.status_code == 200
    assert resp.get_json()['locked'] == 0


def test_delete_image_removes_the_row_and_the_file(client, paper_root):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    img = _add_image(client, page_id).get_json()
    assert list((paper_root / paper_id).glob('img-*.png'))

    assert client.delete(f"/api/paper/images/{img['id']}").status_code == 200
    assert client.get(f'/api/paper/pages/{page_id}').get_json()['images'] == []
    assert not list((paper_root / paper_id).glob('img-*.png'))


def test_serve_image_file(client):
    paper_id = _create(client)
    page_id = _first_page(client, paper_id)
    img = _add_image(client, page_id, data=b'\x89PNG-body').get_json()

    resp = client.get(f"/api/paper/images/{img['id']}/file")
    assert resp.status_code == 200
    assert resp.data == b'\x89PNG-body'
    assert resp.mimetype == 'image/png'


def test_deleting_a_page_takes_its_image_rows_with_it(client):
    paper_id = _create(client)
    page_id = client.post(f'/api/paper/{paper_id}/pages').get_json()['id']
    img = _add_image(client, page_id).get_json()

    assert client.delete(f'/api/paper/pages/{page_id}').status_code == 200
    assert client.get(f"/api/paper/images/{img['id']}/file").status_code == 404


def test_a_paper_created_offline_replays_under_its_own_ids(client):
    """Paper is the one feature whose data lives only on the tablet it was
    written on, so a new page cannot wait for a server. The ids are minted on
    the device and the create replays without producing a second paper."""
    body = {'id': '01ARZ3NDEKTSV4RRFFQ69G5FC0', 'pageId': '01ARZ3NDEKTSV4RRFFQ69G5FC1'}
    first = client.post('/api/paper', json=body)
    assert first.status_code == 201
    assert first.get_json() == {'id': body['id'], 'pageId': body['pageId']}
    assert client.post('/api/paper', json=body).status_code == 201

    papers = client.get('/api/paper').get_json()
    assert [p['id'] for p in papers] == [body['id']]
    detail = client.get(f"/api/paper/{body['id']}").get_json()
    assert [pg['id'] for pg in detail['pages']] == [body['pageId']]


def test_a_page_save_written_offline_is_replayable(client):
    """The save is a PUT — last write wins — so replaying a queued page save is
    safe by construction. What this pins down is that the *page* it targets can
    itself have been created offline moments earlier."""
    client.post('/api/paper', json={'id': 'PAP1', 'pageId': 'PAGE1'})

    import io
    for _ in range(2):
        r = client.put(
            '/api/paper/pages/PAGE1',
            data={
                'strokes': (io.BytesIO(b'[{"points":[1,2]}]'), 'strokes.json'),
                'width': '2100',
                'height': '2970',
                'snapshot': (io.BytesIO(b'\x89PNG\r\n\x1a\n'), 'snapshot.png'),
            },
            content_type='multipart/form-data',
        )
        assert r.status_code == 200

    page = client.get('/api/paper/pages/PAGE1').get_json()
    assert page['strokes'] == '[{"points":[1,2]}]'
    assert page['width'] == 2100


def test_a_page_added_offline_replays_under_its_own_id(client):
    """A page added on the tablet carries the id it was drawn on, so the queued
    create can replay without leaving a second, blank page behind it."""
    client.post('/api/paper', json={'id': 'PAP2', 'pageId': 'PAGE-A'})

    body = {'id': 'PAGE-B'}
    assert client.post('/api/paper/PAP2/pages', json=body).status_code == 201
    assert client.post('/api/paper/PAP2/pages', json=body).status_code == 201

    detail = client.get('/api/paper/PAP2').get_json()
    assert [p['id'] for p in detail['pages']] == ['PAGE-A', 'PAGE-B']
