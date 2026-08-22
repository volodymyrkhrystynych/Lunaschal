import json
import io
from datetime import datetime

import pytest
from PIL import Image

from backend.routes import food


def _exif_image(fmt='JPEG', dt='2026:03:14 09:30:00',
                gps=('N', (43.0, 39.0, 11.0), 'W', (79.0, 22.0, 59.0))):
    """A tiny image carrying a DateTimeOriginal and (optionally) a GPS fix."""
    img = Image.new('RGB', (8, 8), (120, 120, 120))
    exif = img.getexif()
    if dt:
        exif[0x0132] = dt
        exif.get_ifd(0x8769)[0x9003] = dt
    if gps:
        lat_ref, lat, lon_ref, lon = gps
        g = exif.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = lat_ref, lat, lon_ref, lon
    buf = io.BytesIO()
    img.save(buf, fmt, exif=exif)
    return buf.getvalue()


def _exif_jpeg(**kw):
    return _exif_image(fmt='JPEG', **kw)


@pytest.fixture(autouse=True)
def food_root(monkeypatch, tmp_path):
    root = tmp_path / 'food'
    monkeypatch.setenv('FOOD_ROOT', str(root))
    return root


@pytest.fixture(autouse=True)
def sync_bg(monkeypatch):
    """Run the background structuring inline so create->structure is deterministic."""
    monkeypatch.setattr(food, 'run_bg', lambda fn: fn())


def _create(client, text='', media=None, **fields):
    data = {'text': text, **fields}
    if media is not None:
        data['media'] = media
        return client.post('/api/food', data=data, content_type='multipart/form-data')
    return client.post('/api/food', json=data)


def _png(name='photo.jpg', content=b'\xff\xd8\xffFAKEJPEG'):
    return (io.BytesIO(content), name)


def test_create_plain_text_entry(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    r = _create(client, text='had a great sandwich')
    assert r.status_code == 201
    body = r.get_json()
    assert body['rawContent'] == 'had a great sandwich'
    assert body['media'] == []
    assert body['recipe'] is None

    listed = client.get('/api/food').get_json()
    assert len(listed) == 1 and listed[0]['id'] == body['id']


def test_create_requires_some_content(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    assert _create(client, text='').status_code == 400


def test_ai_structuring_fills_fields_and_links_recipe(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: {
        'dish': 'Tonkotsu ramen',
        'place': 'Kinton',
        'rating': 5,
        'notes': 'Rich broth, would go again.',
        'tags': ['japanese', 'ramen'],
        'recipe': {
            'title': 'Tonkotsu broth',
            'content': '## Ingredients\n- pork bones\n\n## Instructions\n1. Simmer 12h',
            'tags': ['japanese'],
        },
    })
    r = _create(client, text='ramen at Kinton, so good, I simmered pork bones 12h')
    body = r.get_json()
    assert body['dish'] == 'Tonkotsu ramen'
    assert body['place'] == 'Kinton'
    assert body['rating'] == 5
    assert body['recipe'] and body['recipe']['title'] == 'Tonkotsu broth'

    # The recipe is a real, browsable recipe row.
    recipe_id = body['recipe']['id']
    assert client.get(f'/api/cookbook/{recipe_id}').status_code == 200

    # And the entry appears in the journal shape with its rating/dish.
    journal = client.get('/api/food/journal').get_json()
    assert len(journal) == 1
    assert journal[0]['dish'] == 'Tonkotsu ramen'
    assert journal[0]['createdAt']


def test_manual_fields_win_over_ai(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: {
        'dish': 'AI dish', 'place': 'AI place', 'rating': 2,
        'notes': 'ai notes', 'tags': ['ai'], 'recipe': None,
    })
    r = _create(client, text='whatever', dish='My dish', rating='4')
    body = r.get_json()
    assert body['dish'] == 'My dish'      # manual kept
    assert body['rating'] == 4            # manual kept
    assert body['place'] == 'AI place'    # AI filled the empty one


def test_structuring_passes_the_memory_document_to_the_parser(client, monkeypatch):
    from backend.memory import set_memory

    set_memory('Their favourite ramen spot is Kinton.', source='user')
    seen = {}

    def _fake(text, **kwargs):
        seen['memory'] = kwargs.get('memory')
        return None
    monkeypatch.setattr(food, 'parse_food_entry', _fake)

    _create(client, text='ramen at kin tin')
    assert seen['memory'] == 'Their favourite ramen spot is Kinton.'


def test_create_with_media_saves_file_and_serves_it(client, food_root, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    r = _create(client, text='lunch', media=_png(content=b'JPEGBYTES'))
    body = r.get_json()
    assert len(body['media']) == 1
    m = body['media'][0]
    assert m['kind'] == 'image'

    # On disk under <root>/<entry>/<media>.jpg
    assert (food_root / body['id']).is_dir()

    served = client.get(m['url'])
    assert served.status_code == 200
    assert served.data == b'JPEGBYTES'


def test_video_media_detected_as_video(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    r = _create(client, text='dinner', media=_png(name='clip.mov', content=b'MOVDATA'))
    assert r.get_json()['media'][0]['kind'] == 'video'


def test_delete_entry_removes_media_and_dir(client, food_root, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    body = _create(client, text='snack', media=_png()).get_json()
    entry_id = body['id']
    media_id = body['media'][0]['id']
    assert (food_root / entry_id).is_dir()

    assert client.delete(f'/api/food/{entry_id}').status_code == 200
    assert client.get(f'/api/food/{entry_id}').status_code == 404
    assert client.get(f'/api/food/media/{media_id}').status_code == 404  # cascaded
    assert not (food_root / entry_id).exists()


def test_delete_single_media(client, food_root, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    body = _create(client, text='snack', media=_png()).get_json()
    media_id = body['media'][0]['id']
    r = client.delete(f'/api/food/media/{media_id}')
    assert r.status_code == 200
    assert client.get(f'/api/food/media/{media_id}').status_code == 404
    assert client.get(f"/api/food/{body['id']}").get_json()['media'] == []


def test_patch_updates_fields(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    body = _create(client, text='meh').get_json()
    r = client.patch(f"/api/food/{body['id']}", json={'dish': 'Pho', 'rating': 3, 'tags': ['vietnamese']})
    assert r.status_code == 200
    got = client.get(f"/api/food/{body['id']}").get_json()
    assert got['dish'] == 'Pho' and got['rating'] == 3


def test_add_media_to_existing_entry(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    body = _create(client, text='brunch').get_json()
    r = client.post(
        f"/api/food/{body['id']}/media",
        data={'media': _png()},
        content_type='multipart/form-data',
    )
    assert r.status_code == 201
    assert len(client.get(f"/api/food/{body['id']}").get_json()['media']) == 1


def test_tag_filter_and_tag_counts(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    a = _create(client, text='a', tags='sushi,dinner').get_json()
    _create(client, text='b', tags='dinner').get_json()

    tags = {t['name']: t['count'] for t in client.get('/api/food/tags').get_json()}
    assert tags['dinner'] == 2 and tags['sushi'] == 1

    only_sushi = client.get('/api/food?tag=sushi').get_json()
    assert [e['id'] for e in only_sushi] == [a['id']]


def test_gps_coordinates_round_trip(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    r = client.post(
        '/api/food',
        json={'text': 'bagel', 'latitude': 43.6532, 'longitude': -79.3832},
    )
    body = r.get_json()
    assert body['latitude'] == 43.6532
    assert body['longitude'] == -79.3832

    journal = client.get('/api/food/journal').get_json()
    assert journal[0]['latitude'] == 43.6532
    assert journal[0]['longitude'] == -79.3832


def test_invalid_coordinates_dropped(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    r = client.post(
        '/api/food',
        json={'text': 'bagel', 'latitude': 'not-a-number', 'longitude': 999},
    )
    body = r.get_json()
    assert body['latitude'] is None
    assert body['longitude'] is None


def test_photo_exif_sets_date_and_location(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    # Client also sends a "current" device GPS — the photo's EXIF must win.
    r = _create(
        client,
        text='old brunch',
        media=_png(name='brunch.jpg', content=_exif_jpeg()),
        latitude='1.0',
        longitude='2.0',
    )
    body = r.get_json()
    # 43°39'11"N, 79°22'59"W -> ~43.653, -79.383
    assert round(body['latitude'], 2) == 43.65
    assert round(body['longitude'], 2) == -79.38
    # Dated to the photo, not now.
    expected = int(datetime(2026, 3, 14, 9, 30, 0).timestamp())
    assert abs(datetime.fromisoformat(body['createdAt']).timestamp() - expected) < 2


def test_photo_without_gps_falls_back_to_device_location(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    r = _create(
        client,
        text='lunch',
        media=_png(name='lunch.jpg', content=_exif_jpeg(gps=None)),
        latitude='10.5',
        longitude='20.5',
    )
    body = r.get_json()
    assert body['latitude'] == 10.5  # device GPS kept when the photo has none
    assert body['longitude'] == 20.5


def test_heic_upload_is_transcoded_and_keeps_exif(client, monkeypatch):
    pytest.importorskip('pillow_heif')
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    heic = _exif_image(fmt='HEIF', dt='2025:12:25 18:05:00',
                       gps=('N', (48.0, 51.0, 30.0), 'E', (2.0, 17.0, 40.0)))
    body = _create(client, text='paris dinner',
                   media=_png(name='dinner.heic', content=heic)).get_json()

    # Stored as JPEG so it renders everywhere...
    assert len(body['media']) == 1
    served = client.get(body['media'][0]['url'])
    assert served.status_code == 200
    assert served.mimetype == 'image/jpeg'
    assert served.data[:3] == b'\xff\xd8\xff'  # JPEG magic

    # ...and the HEIC's date + GPS survived the transcode.
    assert round(body['latitude'], 2) == 48.86
    assert round(body['longitude'], 2) == 2.29
    expected = int(datetime(2025, 12, 25, 18, 5, 0).timestamp())
    assert abs(datetime.fromisoformat(body['createdAt']).timestamp() - expected) < 2


def test_voiceonly_log_is_not_backdated(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    before = datetime.now().timestamp()
    body = _create(client, text='just a voice note').get_json()
    assert datetime.fromisoformat(body['createdAt']).timestamp() >= before - 2


def test_missing_entry_and_media_404(client):
    assert client.get('/api/food/nope').status_code == 404
    assert client.get('/api/food/media/nope').status_code == 404
    assert client.patch('/api/food/nope', json={'dish': 'x'}).status_code == 404
    assert client.post('/api/food/nope/media', data={}).status_code == 404


def test_rejects_unsupported_media_type(client, monkeypatch):
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kwargs: None)
    # An .exe upload is dropped; the entry still saves via its text.
    body = _create(client, text='note', media=_png(name='evil.exe', content=b'MZ')).get_json()
    assert body['media'] == []


def test_storage_path_safety():
    from backend.food import storage
    assert storage.entry_dir('..') is None
    assert storage.media_path('ok', '..', 'png') is None
    assert storage.media_path('ok', 'm', 'exe') is None  # bad ext
    assert storage.resolve_stored_path('/etc/passwd') is None


def test_exif_helpers():
    from backend.food import exif
    assert exif._to_degrees((43, 39, 11)) == pytest.approx(43.6531, abs=1e-3)
    assert exif._to_degrees(None) is None
    assert exif._parse_exif_dt('2026:03:14 09:30:00') == int(
        datetime(2026, 3, 14, 9, 30, 0).timestamp()
    )
    assert exif._parse_exif_dt('garbage') is None


def test_extract_photo_meta_gracefully_handles_non_image(tmp_path):
    from backend.food.exif import extract_photo_meta
    p = tmp_path / 'not-an-image.jpg'
    p.write_bytes(b'\xff\xd8\xffNOTREALLYJPEG')
    assert extract_photo_meta(p) == {'taken_at': None, 'latitude': None, 'longitude': None}


def test_a_food_entry_with_a_client_id_replays_once(client):
    """The meal's id is minted by the browser so an offline capture can be
    replayed — and so its photos can be uploaded under the same entry
    afterwards, whichever of the two lands first."""
    body = {'id': '01ARZ3NDEKTSV4RRFFQ69G5FB0', 'text': 'ramen, very good'}
    first = client.post('/api/food', json=body)
    assert first.status_code == 201
    assert first.get_json()['id'] == body['id']
    assert client.post('/api/food', json=body).status_code == 201

    entries = client.get('/api/food').get_json()
    assert [e['id'] for e in entries] == [body['id']]


def test_a_replayed_photo_upload_does_not_duplicate_the_media(client):
    """A queued offline capture replays the whole multipart — text and photo
    together. The entry is idempotent by id, and so is each photo: without that
    a retry after a dropped response leaves the meal with two copies of the
    same picture."""
    import io

    def post():
        return client.post(
            '/api/food',
            data={
                'id': '01ARZ3NDEKTSV4RRFFQ69G5FB1',
                'mediaIds': json.dumps(['01ARZ3NDEKTSV4RRFFQ69G5FB2']),
                'text': 'ramen',
                'media': (io.BytesIO(b'\xff\xd8\xff\xe0jpegbytes'), 'meal.jpg'),
            },
            content_type='multipart/form-data',
        )

    assert post().status_code == 201
    assert post().status_code == 201

    entries = client.get('/api/food').get_json()
    assert len(entries) == 1
    assert [m['id'] for m in entries[0]['media']] == ['01ARZ3NDEKTSV4RRFFQ69G5FB2']
