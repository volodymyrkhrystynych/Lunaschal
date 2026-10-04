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


@pytest.fixture
def pdf_book(client, tmp_path, monkeypatch):
    root = tmp_path / 'fanfic'
    monkeypatch.setenv('FANFIC_ROOT', str(root))
    id = str(ULID())
    path = root / id / 'book.pdf'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'%PDF-1.4\nexample book')
    db = get_db()
    db.execute("INSERT INTO fics(id,title,source_type,created_at,updated_at) VALUES(?,'Book','pdf',1,1)", (id,))
    db.commit()
    return id, path


def test_pdf_book_manifest_ranges_and_capability(client, pdf_book):
    id, path = pdf_book
    assert 'fics' in client.get('/api/mobile/capabilities').json['mediaCollections']
    item = client.get('/api/mobile/media?collection=fics').json['items'][0]
    assert item['id'] == id
    assert item['mime'] == 'application/pdf'
    assert item['size'] == path.stat().st_size
    assert item['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert str(path) not in str(item)
    response = client.get(item['url'], headers={'Range': 'bytes=0-7'})
    assert response.status_code == 206
    assert response.data == b'%PDF-1.4'
    path.write_bytes(b'%PDF-1.4\nupdated book')
    assert client.get(item['url']).status_code == 412
    assert client.get('/api/mobile/media?collection=fics&after=' + id).json['items'] == []


def test_missing_pdf_keeps_metadata_without_serving_file(client, pdf_book):
    _, path = pdf_book
    item = client.get('/api/mobile/media?collection=fics').json['items'][0]
    path.unlink()
    assert client.get('/api/mobile/media?collection=fics').json['items'][0]['available'] is False
    assert client.get(item['url']).status_code == 404


def test_non_pdf_book_does_not_expose_leftover_pdf(client, pdf_book):
    id, _ = pdf_book
    db = get_db()
    db.execute("UPDATE fics SET source_type='epub' WHERE id=?", (id,))
    db.commit()
    assert client.get('/api/mobile/media?collection=fics').json['items'][0]['available'] is False


@pytest.mark.parametrize('target_kind', ['outside', 'other_book', 'directory'])
def test_pdf_symlinks_cannot_cross_book_identity(client, pdf_book, tmp_path, target_kind):
    _, path = pdf_book
    original = client.get('/api/mobile/media?collection=fics').json['items'][0]
    path.unlink()
    if target_kind == 'directory':
        target = tmp_path / 'external'
        target.mkdir()
        (target / 'book.pdf').write_bytes(b'private')
        path.parent.rmdir()
        path.parent.symlink_to(target, target_is_directory=True)
    else:
        target = tmp_path / 'private.pdf' if target_kind == 'outside' else path.parent.parent / str(ULID()) / 'book.pdf'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'private')
        path.symlink_to(target)
    assert client.get('/api/mobile/media?collection=fics').json['items'][0]['available'] is False
    assert client.get(original['url']).status_code == 404
