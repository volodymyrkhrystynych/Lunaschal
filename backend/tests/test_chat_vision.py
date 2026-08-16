"""The chat model reading photos itself, instead of being read a description.

Qwen3.6 is a vision-language model, so with an mmproj on `[qwen36]` a photo can
ride into the turn as an `image_url` part. The setting defaults **off** because
the projector is a separate download into ~878 MiB of VRAM headroom, so what
matters most here is that both paths work and that the wrong one is never taken
silently.
"""
import io

import pytest
from PIL import Image

from backend.ai.chat import stamp_messages
from backend.ai.provider import chat_vision_enabled
from backend.chat import context as chat_context
from backend.db.connection import get_db
from backend.routes import chat as chat_routes


@pytest.fixture(autouse=True)
def chat_root(monkeypatch, tmp_path):
    monkeypatch.setenv('CHAT_ROOT', str(tmp_path / 'chat'))


@pytest.fixture(autouse=True)
def sync_bg(monkeypatch):
    monkeypatch.setattr(chat_routes, 'run_bg', lambda fn: fn())


@pytest.fixture(autouse=True)
def reads_photo(monkeypatch):
    monkeypatch.setattr(chat_routes, '_do_read_attachment', lambda path: 'A plate of vareniki.')


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (120, 120, 120)).save(buf, 'JPEG')
    return buf.getvalue()


def _enable_vision(client):
    r = client.patch('/api/settings/ai', json={'llamaChatVision': True})
    assert r.status_code == 200


def _attach(client, name='meal.jpg'):
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    r = client.post(f'/api/chat/conversations/{conv}/attachments',
                    data={'image': (io.BytesIO(_jpeg()), name)},
                    content_type='multipart/form-data')
    return conv, r.get_json()[0]


# --- The setting ---


def test_chat_vision_is_off_until_it_is_turned_on(client):
    """Off by default: `[qwen36]` ships with no projector, and sending images to
    a model that can't decode them looks like hallucination, not misconfiguration."""
    assert chat_vision_enabled() is False
    assert client.get('/api/settings').get_json()['llamaChatVision'] is False

    _enable_vision(client)
    assert chat_vision_enabled() is True
    assert client.get('/api/settings').get_json()['llamaChatVision'] is True


# --- Which path an upload takes ---


def test_with_vision_off_the_omni_model_pre_reads_the_photo(client):
    _, att = _attach(client)
    assert att['descriptionStatus'] == 'done'
    assert 'vareniki' in att['description'].lower()


def test_with_vision_on_nothing_pre_reads_the_photo(client, monkeypatch):
    """The whole point of the switch: no CPU-bound 12B generation per photo."""
    def _never(path):
        raise AssertionError('the omni model was asked to read a photo anyway')

    monkeypatch.setattr(chat_routes, '_do_read_attachment', _never)
    _enable_vision(client)
    _, att = _attach(client)

    # NULL, not 'running' — the composer must not spin on work never queued.
    assert att['descriptionStatus'] is None
    assert att['description'] is None


# --- What reaches the model ---


def test_with_vision_on_the_message_carries_an_image_part(client):
    _enable_vision(client)
    _, att = _attach(client)
    [msg] = chat_context.expand_attachments(
        [{'role': 'user', 'content': 'what is this', 'attachmentIds': [att['id']]}]
    )
    text, image = msg['content']
    assert text == {'type': 'text', 'text': 'what is this'}
    assert image['type'] == 'image_url'
    assert image['image_url']['url'].startswith('data:image/jpeg;base64,')


def test_with_vision_off_the_message_stays_a_string(client):
    _, att = _attach(client)
    [msg] = chat_context.expand_attachments(
        [{'role': 'user', 'content': 'what is this', 'attachmentIds': [att['id']]}]
    )
    assert isinstance(msg['content'], str)
    assert 'A plate of vareniki.' in msg['content']


def test_a_photo_with_no_text_is_all_image(client):
    _enable_vision(client)
    _, att = _attach(client)
    [msg] = chat_context.expand_attachments(
        [{'role': 'user', 'content': '', 'attachmentIds': [att['id']]}]
    )
    assert [p['type'] for p in msg['content']] == ['image_url']


def test_a_missing_file_becomes_a_note_rather_than_an_exception(client):
    """One broken attachment must not cost the turn the message it rode on."""
    _enable_vision(client)
    _, att = _attach(client)
    db = get_db()
    db.execute('UPDATE chat_attachments SET path=? WHERE id=?',
               ('/nowhere/gone.jpg', att['id']))
    db.commit()

    [msg] = chat_context.expand_attachments(
        [{'role': 'user', 'content': 'what is this', 'attachmentIds': [att['id']]}]
    )
    [text] = msg['content']
    assert 'file is missing' in text['text']


def test_messages_without_photos_are_untouched_either_way(client):
    _enable_vision(client)
    original = [{'role': 'user', 'content': 'hello', 'createdAt': '2026-08-09T10:00:00'}]
    assert chat_context.expand_attachments(original) == original


# --- Stamping must not flatten the parts ---


def test_the_time_prefix_lands_on_the_text_part_not_the_list(client):
    """stamp_messages used to f-string whatever it was given, which turned a
    content-part list into its own repr — the image silently became text
    describing a Python list."""
    [msg] = stamp_messages([{
        'role': 'user',
        'createdAt': '2026-08-09T10:00:00',
        'content': [
            {'type': 'text', 'text': 'what is this'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,AAA'}},
        ],
    }], now=int(__import__('datetime').datetime(2026, 8, 9, 12, 0).timestamp()))

    text, image = msg['content']
    assert text['text'].startswith('[today ')
    assert text['text'].endswith('what is this')
    # And the image survives verbatim.
    assert image['image_url']['url'] == 'data:image/jpeg;base64,AAA'


def test_an_image_only_message_still_gets_stamped(client):
    [msg] = stamp_messages([{
        'role': 'user',
        'createdAt': '2026-08-09T10:00:00',
        'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,AAA'}}],
    }])
    assert msg['content'][0]['type'] == 'text'
    assert msg['content'][1]['type'] == 'image_url'


def test_a_plain_string_message_is_stamped_exactly_as_before(client):
    [msg] = stamp_messages(
        [{'role': 'user', 'content': 'hello', 'createdAt': '2026-08-09T10:00:00'}]
    )
    assert isinstance(msg['content'], str)
    assert msg['content'].endswith('hello')


# --- Location ---


def test_the_device_position_is_stored_with_the_photo(client):
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    r = client.post(
        f'/api/chat/conversations/{conv}/attachments',
        data={'image': (io.BytesIO(_jpeg()), 'meal.jpg'),
              'latitude': '43.6446', 'longitude': '-79.3975'},
        content_type='multipart/form-data',
    )
    att = r.get_json()[0]
    assert (att['latitude'], att['longitude']) == (43.6446, -79.3975)


def test_a_half_position_is_refused(client):
    """A lone latitude is not half a location — it produces a row that looks
    located and isn't."""
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    r = client.post(
        f'/api/chat/conversations/{conv}/attachments',
        data={'image': (io.BytesIO(_jpeg()), 'meal.jpg'), 'latitude': '43.6446'},
        content_type='multipart/form-data',
    )
    att = r.get_json()[0]
    assert att['latitude'] is None and att['longitude'] is None


def test_a_junk_position_is_ignored_rather_than_stored(client):
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    r = client.post(
        f'/api/chat/conversations/{conv}/attachments',
        data={'image': (io.BytesIO(_jpeg()), 'meal.jpg'),
              'latitude': 'nan', 'longitude': '-79.3975'},
        content_type='multipart/form-data',
    )
    assert r.get_json()[0]['latitude'] is None


def test_no_position_is_the_normal_case_and_is_fine(client):
    _, att = _attach(client)
    assert att['latitude'] is None and att['longitude'] is None
