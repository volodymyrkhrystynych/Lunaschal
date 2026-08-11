"""Logging a meal from chat: `propose_food_log` and `_accept_food`.

The point of this path over the older `propose_calorie_log` is that the two
things a calorie count throws away — the photo and the exact words the user said
— survive into the food log. Both come from the message, not from the card, so
neither the model nor a later edit can rewrite them.
"""
import io
import json
import time

import pytest
from PIL import Image
from ulid import ULID

from backend.db.connection import get_db
from backend.delegate import tools
from backend.routes import chat as chat_routes


@pytest.fixture(autouse=True)
def roots(monkeypatch, tmp_path):
    monkeypatch.setenv('CHAT_ROOT', str(tmp_path / 'chat'))
    monkeypatch.setenv('FOOD_ROOT', str(tmp_path / 'food'))


@pytest.fixture(autouse=True)
def sync_bg(monkeypatch):
    monkeypatch.setattr(chat_routes, 'run_bg', lambda fn: fn())


@pytest.fixture(autouse=True)
def reads_photo(monkeypatch):
    monkeypatch.setattr(chat_routes, '_do_read_attachment', lambda path: 'A plate of vareniki.')


def _exif_jpeg(dt='2026:03:14 09:30:00'):
    img = Image.new('RGB', (8, 8), (120, 120, 120))
    exif = img.getexif()
    exif[0x0132] = dt
    exif.get_ifd(0x8769)[0x9003] = dt
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def _seed(client, *, user_content='had vareniki', raw_content=None, photo=None):
    """One user message (optionally with a photo) and the assistant reply that
    staged a food proposal against it."""
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']

    attachment_ids = []
    if photo is not None:
        r = client.post(f'/api/chat/conversations/{conv}/attachments',
                        data={'image': (io.BytesIO(photo), 'meal.jpg')},
                        content_type='multipart/form-data')
        attachment_ids = [a['id'] for a in r.get_json()]

    client.post(f'/api/chat/conversations/{conv}/messages',
                json={'role': 'user', 'content': user_content,
                      'rawContent': raw_content, 'attachmentIds': attachment_ids})
    assistant_id = client.post(f'/api/chat/conversations/{conv}/messages',
                               json={'role': 'assistant', 'content': 'noted'}).get_json()['id']
    return conv, assistant_id


def _stage(client, assistant_id, data, proposal_id='p1'):
    meta = {'agent': 'delegate', 'steps': [], 'sources': [],
            'proposals': [{'id': proposal_id, 'status': 'pending', 'kind': 'food', 'data': data}]}
    db = get_db()
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), assistant_id))
    db.commit()


def _accept(client, assistant_id, proposal_id='p1', data=None):
    body = {'action': 'accept'}
    if data is not None:
        body['data'] = data
    return client.post(f'/api/chat/proposals/{assistant_id}/{proposal_id}', json=body)


# --- Staging ---


def test_propose_food_log_stages_a_food_proposal():
    text, event = tools.run_tool('propose_food_log', {'dish': 'Vareniki', 'calories': 600})
    assert event['proposal']['kind'] == 'food'
    assert event['proposal']['data']['dish'] == 'Vareniki'
    assert 'Nothing has been saved yet' in text


def test_propose_food_log_needs_a_dish():
    text, event = tools.run_tool('propose_food_log', {'dish': '  '})
    assert event['ok'] is False


def test_propose_food_log_allows_a_meal_with_no_calorie_count():
    """Most meals are logged without a number, and demanding one is what would
    push the model into inventing it."""
    _, event = tools.run_tool('propose_food_log', {'dish': 'Vareniki'})
    assert event['ok'] is True
    assert event['proposal']['data']['calories'] is None


def test_propose_food_log_refuses_a_boolean_calorie_count():
    _, event = tools.run_tool('propose_food_log', {'dish': 'Vareniki', 'calories': True})
    assert event['ok'] is False


def test_propose_food_log_refuses_an_out_of_range_rating():
    _, event = tools.run_tool('propose_food_log', {'dish': 'Vareniki', 'rating': 9})
    assert event['ok'] is False


# --- Accepting ---


def test_accepting_writes_a_food_entry_with_the_verbatim_transcript(client):
    _, assistant_id = _seed(client, user_content='had vareniki at Movati',
                            raw_content='had vary nikki at motivate')
    _stage(client, assistant_id, {'dish': 'Vareniki', 'place': 'Movati',
                                  'notes': 'really good', 'rating': 4, 'tags': ['lunch']})
    r = _accept(client, assistant_id)
    assert r.status_code == 200

    row = get_db().execute('SELECT * FROM food_entries').fetchone()
    assert row['dish'] == 'Vareniki'
    assert row['place'] == 'Movati'
    assert row['rating'] == 4
    assert json.loads(row['tags']) == ['lunch']
    # What was actually said, not what the correction pass made of it.
    assert row['raw_content'] == 'had vary nikki at motivate'


def test_a_typed_message_falls_back_to_its_content(client):
    _, assistant_id = _seed(client, user_content='had a great sandwich')
    _stage(client, assistant_id, {'dish': 'Sandwich'})
    _accept(client, assistant_id)
    assert get_db().execute(
        'SELECT raw_content FROM food_entries'
    ).fetchone()['raw_content'] == 'had a great sandwich'


def test_the_photo_is_copied_into_the_food_entry_and_left_on_the_message(client):
    _, assistant_id = _seed(client, photo=_exif_jpeg())
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    r = _accept(client, assistant_id)
    assert r.get_json()['proposal']['result']['photos'] == 1

    db = get_db()
    media = db.execute('SELECT * FROM food_media').fetchone()
    assert media['kind'] == 'image'
    # Copied, not moved: the photo is part of what was said in the chat.
    chat_path = db.execute('SELECT path FROM chat_attachments').fetchone()['path']
    assert media['path'] != chat_path
    from pathlib import Path
    assert Path(chat_path).is_file() and Path(media['path']).is_file()


def test_the_photos_capture_date_dates_the_meal(client):
    """A chat about lunch can happen hours later, so the photo's EXIF is the
    source of truth for when it happened — as it already is in the Food tab."""
    _, assistant_id = _seed(client, photo=_exif_jpeg(dt='2026:03:14 09:30:00'))
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    _accept(client, assistant_id)

    created = get_db().execute('SELECT created_at FROM food_entries').fetchone()['created_at']
    assert time.strftime('%Y-%m-%d', time.localtime(created)) == '2026-03-14'


def test_calories_also_write_a_calorie_log_dated_from_the_meal(client):
    _, assistant_id = _seed(client, photo=_exif_jpeg(dt='2026:03:14 09:30:00'))
    _stage(client, assistant_id, {'dish': 'Vareniki', 'calories': 600})
    _accept(client, assistant_id)

    row = get_db().execute('SELECT * FROM calorie_logs').fetchone()
    assert (row['description'], row['calories'], row['date']) == ('Vareniki', 600, '2026-03-14')


def test_no_calorie_count_writes_no_calorie_log(client):
    _, assistant_id = _seed(client)
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    _accept(client, assistant_id)
    assert get_db().execute('SELECT COUNT(*) c FROM calorie_logs').fetchone()['c'] == 0


def test_editing_the_card_replaces_the_staged_payload(client):
    _, assistant_id = _seed(client)
    _stage(client, assistant_id, {'dish': 'Vareniki', 'calories': 600})
    _accept(client, assistant_id, data={'dish': 'Pierogi', 'calories': 450})

    row = get_db().execute('SELECT dish FROM food_entries').fetchone()
    assert row['dish'] == 'Pierogi'
    assert get_db().execute('SELECT calories FROM calorie_logs').fetchone()['calories'] == 450


def test_an_edit_cannot_rewrite_what_the_user_said(client):
    """`raw_content` is resolved from the message, so it is not a field the card
    can carry — sending one has no effect."""
    _, assistant_id = _seed(client, user_content='had vareniki',
                            raw_content='had vary nikki')
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    _accept(client, assistant_id, data={'dish': 'Vareniki', 'rawContent': 'I said something else'})
    assert get_db().execute(
        'SELECT raw_content FROM food_entries'
    ).fetchone()['raw_content'] == 'had vary nikki'


def test_a_rejected_edit_is_400_and_leaves_the_card_pending(client):
    _, assistant_id = _seed(client)
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    r = _accept(client, assistant_id, data={'dish': 'Vareniki', 'rating': 11})
    assert r.status_code == 400

    meta = json.loads(get_db().execute(
        'SELECT metadata FROM messages WHERE id=?', (assistant_id,)
    ).fetchone()['metadata'])
    assert meta['proposals'][0]['status'] == 'pending'


def test_a_missing_dish_is_rejected(client):
    _, assistant_id = _seed(client)
    _stage(client, assistant_id, {'dish': ''})
    assert _accept(client, assistant_id).status_code == 400


def test_dismissing_writes_nothing(client):
    _, assistant_id = _seed(client)
    _stage(client, assistant_id, {'dish': 'Vareniki', 'calories': 600})
    client.post(f'/api/chat/proposals/{assistant_id}/p1', json={'action': 'dismiss'})
    assert get_db().execute('SELECT COUNT(*) c FROM food_entries').fetchone()['c'] == 0
    assert get_db().execute('SELECT COUNT(*) c FROM calorie_logs').fetchone()['c'] == 0


def test_the_proposal_picks_up_the_user_message_it_answered_not_the_latest(client):
    """A card confirmed later in the conversation still belongs to the meal it
    was staged against."""
    conv, assistant_id = _seed(client, user_content='had vareniki')
    # A later exchange, after the card was staged.
    client.post(f'/api/chat/conversations/{conv}/messages',
                json={'role': 'user', 'content': 'anyway, what time is my meeting'})
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    _accept(client, assistant_id)
    assert get_db().execute(
        'SELECT raw_content FROM food_entries'
    ).fetchone()['raw_content'] == 'had vareniki'


def test_other_proposal_kinds_still_work_through_the_widened_handler(client):
    """The accept handlers grew a `ctx` argument; the four that predate it must
    behave exactly as before."""
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    assistant_id = client.post(f'/api/chat/conversations/{conv}/messages',
                               json={'role': 'assistant', 'content': ''}).get_json()['id']
    meta = {'proposals': [{'id': 'p1', 'status': 'pending', 'kind': 'task',
                           'data': {'title': 'buy milk', 'list': 'todo'}}]}
    db = get_db()
    db.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(meta), assistant_id))
    db.commit()

    r = client.post(f'/api/chat/proposals/{assistant_id}/p1', json={'action': 'accept'})
    assert r.status_code == 200
    assert db.execute('SELECT title FROM todos').fetchone()['title'] == 'buy milk'


# --- Location on the food entry ---


def _exif_jpeg_gps(dt='2026:03:14 09:30:00'):
    """A photo carrying both a capture date and a GPS fix, as an untouched
    iPhone original does."""
    img = Image.new('RGB', (8, 8), (120, 120, 120))
    exif = img.getexif()
    exif[0x0132] = dt
    exif.get_ifd(0x8769)[0x9003] = dt
    g = exif.get_ifd(0x8825)
    g[1], g[2] = 'N', (43.0, 39.0, 11.0)
    g[3], g[4] = 'W', (79.0, 22.0, 59.0)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def _attach_with_coords(client, conv, photo, latitude=None, longitude=None):
    data = {'image': (io.BytesIO(photo), 'meal.jpg')}
    if latitude is not None:
        data['latitude'], data['longitude'] = str(latitude), str(longitude)
    r = client.post(f'/api/chat/conversations/{conv}/attachments',
                    data=data, content_type='multipart/form-data')
    return [a['id'] for a in r.get_json()]


def _seed_with(client, photo, latitude=None, longitude=None):
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    ids = _attach_with_coords(client, conv, photo, latitude, longitude)
    client.post(f'/api/chat/conversations/{conv}/messages',
                json={'role': 'user', 'content': 'had this', 'attachmentIds': ids})
    assistant_id = client.post(f'/api/chat/conversations/{conv}/messages',
                               json={'role': 'assistant', 'content': 'noted'}).get_json()['id']
    return assistant_id


def _entry(client):
    return get_db().execute('SELECT * FROM food_entries').fetchone()


def test_the_photos_own_gps_wins(client):
    """EXIF says where the picture was taken; the device says where its owner was
    a moment ago. When both exist the photo is the better answer."""
    assistant_id = _seed_with(client, _exif_jpeg_gps(), latitude=1.0, longitude=2.0)
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    _accept(client, assistant_id)

    row = _entry(client)
    assert round(row['latitude'], 3) == 43.653
    assert round(row['longitude'], 3) == -79.383


def test_the_device_position_fills_in_when_the_photo_was_stripped(client):
    """The case this exists for: iOS re-encodes a pasted image and drops its GPS,
    so without this a pasted meal photo is unlocatable."""
    assistant_id = _seed_with(client, _exif_jpeg(), latitude=43.6446, longitude=-79.3975)
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    _accept(client, assistant_id)

    row = _entry(client)
    assert (row['latitude'], row['longitude']) == (43.6446, -79.3975)


def test_no_location_anywhere_leaves_it_null(client):
    assistant_id = _seed_with(client, _exif_jpeg())
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    _accept(client, assistant_id)

    row = _entry(client)
    assert row['latitude'] is None and row['longitude'] is None


def test_a_meal_with_no_photo_at_all_still_saves(client):
    _, assistant_id = _seed(client)
    _stage(client, assistant_id, {'dish': 'Vareniki'})
    assert _accept(client, assistant_id).status_code == 200
    assert _entry(client)['latitude'] is None
