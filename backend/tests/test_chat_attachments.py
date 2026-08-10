"""Photos attached to a chat message.

The thing under test is not really the upload — it is that a picture becomes
*text* the text-only chat model can read, and that every way of failing to read
it stays visible rather than being papered over.
"""
import io

import pytest
from PIL import Image

from backend.chat import context as chat_context
from backend.db.connection import get_db
from backend.routes import chat as chat_routes


@pytest.fixture(autouse=True)
def chat_root(monkeypatch, tmp_path):
    root = tmp_path / 'chat'
    monkeypatch.setenv('CHAT_ROOT', str(root))
    return root


@pytest.fixture(autouse=True)
def sync_bg(monkeypatch):
    """Run the photo-reading job inline so upload -> description is deterministic."""
    monkeypatch.setattr(chat_routes, 'run_bg', lambda fn: fn())


@pytest.fixture(autouse=True)
def reads_photo(monkeypatch):
    """Stand in for the CPU-only omni model. Tests that care override it."""
    monkeypatch.setattr(chat_routes, '_do_read_attachment',
                        lambda path: 'A plate of vareniki. The menu reads "VARENIKI".')


def _jpeg(size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, (120, 120, 120)).save(buf, 'JPEG')
    return buf.getvalue()


def _conversation(client) -> str:
    return client.post('/api/chat/conversations', json={}).get_json()['id']


def _upload(client, conv_id, content=None, name='photo.jpg', mime=None):
    data = {'image': (io.BytesIO(content if content is not None else _jpeg()), name)}
    if mime:
        data['image'] = (io.BytesIO(content if content is not None else _jpeg()), name, mime)
    return client.post(f'/api/chat/conversations/{conv_id}/attachments',
                       data=data, content_type='multipart/form-data')


def test_upload_stores_the_photo_and_reads_it(client):
    conv = _conversation(client)
    r = _upload(client, conv)
    assert r.status_code == 201
    [att] = r.get_json()
    assert att['url'] == f"/api/chat/attachments/{att['id']}/file"
    # `path` is a server-side location and must never reach the client.
    assert 'path' not in att
    assert att['descriptionStatus'] == 'done'
    assert 'vareniki' in att['description'].lower()


def test_the_file_is_served_back(client):
    conv = _conversation(client)
    [att] = _upload(client, conv).get_json()
    r = client.get(f"/api/chat/attachments/{att['id']}/file")
    assert r.status_code == 200
    assert r.data.startswith(b'\xff\xd8\xff')


def test_heic_is_transcoded_to_jpeg_on_upload(client):
    """backend/ai/images.py refuses to send HEIC to the model and browsers won't
    render it, so the conversion has to happen at the door or the photo is dead
    weight everywhere downstream."""
    conv = _conversation(client)
    r = _upload(client, conv, content=_jpeg(), name='IMG_0042.HEIC', mime='image/heic')
    assert r.status_code == 201
    [att] = r.get_json()
    assert att['mime'] == 'image/jpeg'
    path = get_db().execute(
        'SELECT path FROM chat_attachments WHERE id=?', (att['id'],)
    ).fetchone()['path']
    assert path.endswith('.jpg')


def test_a_non_image_upload_is_refused(client):
    conv = _conversation(client)
    r = _upload(client, conv, content=b'not an image', name='notes.txt', mime='text/plain')
    assert r.status_code == 400
    assert get_db().execute('SELECT COUNT(*) c FROM chat_attachments').fetchone()['c'] == 0


def test_an_empty_file_is_refused_and_leaves_nothing_behind(client, chat_root):
    conv = _conversation(client)
    r = _upload(client, conv, content=b'')
    assert r.status_code == 400
    assert get_db().execute('SELECT COUNT(*) c FROM chat_attachments').fetchone()['c'] == 0
    assert list((chat_root / conv).glob('*')) == []


def test_an_oversized_file_is_refused(client, monkeypatch):
    monkeypatch.setattr(chat_routes, 'MAX_IMAGE_BYTES', 10)
    conv = _conversation(client)
    assert _upload(client, conv).status_code == 413


def test_one_bad_file_does_not_discard_the_good_ones(client):
    """Attaching three photos where one is a .txt should not throw away the two
    that were fine — the user picked all of them deliberately."""
    conv = _conversation(client)
    r = client.post(
        f'/api/chat/conversations/{conv}/attachments',
        data={'image': [
            (io.BytesIO(_jpeg()), 'a.jpg'),
            (io.BytesIO(b'nope'), 'b.txt', 'text/plain'),
            (io.BytesIO(_jpeg()), 'c.jpg'),
        ]},
        content_type='multipart/form-data',
    )
    assert r.status_code == 201
    assert len(r.get_json()) == 2


def test_a_failed_read_is_recorded_rather_than_failing_the_upload(client, monkeypatch):
    def _boom(path):
        raise RuntimeError('No vision model configured')

    monkeypatch.setattr(chat_routes, '_do_read_attachment', _boom)
    conv = _conversation(client)
    r = _upload(client, conv)
    assert r.status_code == 201
    row = client.get(f"/api/chat/attachments/{r.get_json()[0]['id']}").get_json()
    assert row['descriptionStatus'] == 'error'
    assert 'No vision model configured' in row['descriptionError']


def test_uploading_to_a_missing_conversation_is_404(client):
    assert _upload(client, '01JUNKJUNKJUNKJUNKJUNKJUNK').status_code == 404


def test_a_tampered_stored_path_is_not_served(client):
    """Defence in depth: `resolve_stored_path` only serves a direct grandchild of
    the root, so a rewritten row can't turn the file route into an arbitrary read."""
    conv = _conversation(client)
    [att] = _upload(client, conv).get_json()
    db = get_db()
    db.execute('UPDATE chat_attachments SET path=? WHERE id=?', ('/etc/passwd', att['id']))
    db.commit()
    assert client.get(f"/api/chat/attachments/{att['id']}/file").status_code == 404


# --- Binding to a message ---


def _send(client, conv, content='had vareniki', attachment_ids=None, **body):
    return client.post(
        f'/api/chat/conversations/{conv}/messages',
        json={'role': 'user', 'content': content,
              'attachmentIds': attachment_ids or [], **body},
    ).get_json()['id']


def test_sending_binds_the_staged_photos_to_the_message(client):
    conv = _conversation(client)
    [att] = _upload(client, conv).get_json()
    msg_id = _send(client, conv, attachment_ids=[att['id']])

    messages = client.get('/api/chat/today').get_json()['messages']
    [msg] = [m for m in messages if m['id'] == msg_id]
    assert [a['id'] for a in msg['attachments']] == [att['id']]


def test_a_photo_cannot_be_stolen_by_a_second_message(client):
    """Binding is scoped to rows still unbound, so a replayed send can't move a
    photo off the message it was actually part of."""
    conv = _conversation(client)
    [att] = _upload(client, conv).get_json()
    first = _send(client, conv, attachment_ids=[att['id']])
    _send(client, conv, content='again', attachment_ids=[att['id']])

    owner = get_db().execute(
        'SELECT message_id FROM chat_attachments WHERE id=?', (att['id'],)
    ).fetchone()['message_id']
    assert owner == first


def test_a_photo_from_another_conversation_is_not_bound(client):
    conv_a = _conversation(client)
    [att] = _upload(client, conv_a).get_json()
    # A second conversation has to be inserted directly: create is find-or-create
    # per chat day, so posting again returns the same row.
    db = get_db()
    db.execute("INSERT INTO conversations(id, day_key, mode, created_at, updated_at)"
               " VALUES ('conv-b','2020-01-01','chat',0,0)")
    db.commit()
    _send(client, 'conv-b', attachment_ids=[att['id']])
    assert get_db().execute(
        'SELECT message_id FROM chat_attachments WHERE id=?', (att['id'],)
    ).fetchone()['message_id'] is None


def test_a_staged_photo_can_be_deleted_but_a_sent_one_cannot(client):
    conv = _conversation(client)
    [staged] = _upload(client, conv).get_json()
    assert client.delete(f"/api/chat/attachments/{staged['id']}").status_code == 200
    assert client.get(f"/api/chat/attachments/{staged['id']}").status_code == 404

    [sent] = _upload(client, conv).get_json()
    _send(client, conv, attachment_ids=[sent['id']])
    assert client.delete(f"/api/chat/attachments/{sent['id']}").status_code == 409


def test_deleting_a_conversation_removes_its_photo_files(client, chat_root):
    conv = _conversation(client)
    _upload(client, conv)
    assert (chat_root / conv).is_dir()
    client.delete(f'/api/chat/conversations/{conv}')
    assert not (chat_root / conv).exists()


def test_raw_content_is_stored_verbatim_alongside_the_sent_text(client):
    conv = _conversation(client)
    msg_id = _send(client, conv, content='had vareniki at Movati',
                   rawContent='had vary nikki at motivate')
    row = get_db().execute(
        'SELECT content, raw_content FROM messages WHERE id=?', (msg_id,)
    ).fetchone()
    assert row['content'] == 'had vareniki at Movati'
    assert row['raw_content'] == 'had vary nikki at motivate'


def test_a_typed_message_keeps_raw_content_null(client):
    conv = _conversation(client)
    msg_id = _send(client, conv, content='typed this one')
    assert get_db().execute(
        'SELECT raw_content FROM messages WHERE id=?', (msg_id,)
    ).fetchone()['raw_content'] is None


# --- The description reaching the model ---


def test_the_description_is_appended_to_the_message_content(client):
    conv = _conversation(client)
    [att] = _upload(client, conv).get_json()
    [msg] = chat_context.expand_attachments(
        [{'role': 'user', 'content': 'what is this', 'attachmentIds': [att['id']]}]
    )
    assert msg['content'].startswith('what is this')
    assert 'VARENIKI' in msg['content']
    assert 'cannot see images' in msg['content']


def test_an_unread_photo_says_so_instead_of_inviting_a_guess(client, monkeypatch):
    def _boom(path):
        raise RuntimeError('nope')

    monkeypatch.setattr(chat_routes, '_do_read_attachment', _boom)
    conv = _conversation(client)
    [att] = _upload(client, conv).get_json()
    [msg] = chat_context.expand_attachments(
        [{'role': 'user', 'content': 'what is this', 'attachmentIds': [att['id']]}]
    )
    assert 'could not be read' in msg['content']
    assert 'rather than guessing' in msg['content']


def test_messages_without_attachments_pass_through_untouched(client):
    """The voice listener, task nudges and Writing discussions can't attach
    anything; their messages must take exactly the path they always did."""
    original = [{'role': 'user', 'content': 'hello', 'createdAt': '2026-08-09T10:00:00'}]
    assert chat_context.expand_attachments(original) == original


def test_an_unknown_attachment_id_is_skipped_silently(client):
    """A stale client replaying an old message must not be able to fail a turn."""
    [msg] = chat_context.expand_attachments(
        [{'role': 'user', 'content': 'hello', 'attachmentIds': ['does-not-exist']}]
    )
    assert msg['content'] == 'hello'
