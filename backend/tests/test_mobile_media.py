import hashlib

import pytest
from ulid import ULID

from backend.db.connection import get_db


@pytest.fixture
def attachment(client, tmp_path, monkeypatch):
    root = tmp_path / 'journal'
    monkeypatch.setenv('JOURNAL_ROOT', str(root))
    entry_id, id = str(ULID()), str(ULID())
    path = root / id / 'attachment.m4a'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'0123456789')
    db = get_db()
    db.execute('INSERT INTO journal_entries(id,content,created_at,updated_at) VALUES(?,?,1,1)', (entry_id, 'voice'))
    db.execute('''INSERT INTO journal_attachments(id,entry_id,kind,name,path,mime,created_at)
                  VALUES(?,?,'audio','thought',?,'audio/mp4',1)''', (id, entry_id, str(path)))
    db.commit()
    return id, path


def listing(client, **args):
    return client.get('/api/mobile/media', query_string={'collection': 'journal_attachments', **args})


def test_manifest_hash_size_range_and_no_paths(client, attachment):
    id, path = attachment
    item = listing(client).json['items'][0]
    assert item['size'] == 10
    assert item['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert str(path) not in str(item)
    response = client.get(item['url'], headers={'Range': 'bytes=4-7'})
    assert response.status_code == 206
    assert response.data == b'4567'
    assert response.headers['ETag'] == f'"{item["sha256"]}"'


def test_replaced_file_refuses_old_resume_version(client, attachment):
    _, path = attachment
    item = listing(client).json['items'][0]
    path.write_bytes(b'changed!!!')
    assert client.get(item['url'], headers={'Range': 'bytes=5-'}).status_code == 412
    fresh = listing(client).json['items'][0]
    assert fresh['sha256'] != item['sha256']
    assert client.get(fresh['url']).data == b'changed!!!'


def test_removed_media_does_not_hide_record(client, attachment):
    _, path = attachment
    item = listing(client).json['items'][0]
    path.unlink()
    assert listing(client).json['items'][0]['available'] is False
    assert client.get(item['url']).status_code == 404


def test_archive_video_excluded_even_if_file_is_local(client, attachment):
    id, _ = attachment
    db = get_db()
    db.execute("UPDATE journal_attachments SET kind='youtube' WHERE id=?", (id,))
    db.commit()
    item = listing(client).json['items'][0]
    assert item['available'] is False
    assert item['sha256'] is None


def test_symlink_escape_is_not_hashed_or_served(client, attachment, tmp_path):
    _, path = attachment
    secret = tmp_path / 'secret'
    secret.write_text('private')
    path.unlink()
    path.symlink_to(secret)
    assert listing(client).json['items'][0]['available'] is False


@pytest.mark.parametrize('args', [{'collection': 'settings'}, {'limit': '0'}, {'limit': '101'}, {'limit': 'bad'}])
def test_invalid_manifest_requests(client, args):
    assert listing(client, **args).status_code == 400


def test_page_key_resumes_listing(client, attachment):
    first = listing(client, limit=1).json
    assert first['hasMore'] is False
    assert listing(client, after=first['after']).json['items'] == []
