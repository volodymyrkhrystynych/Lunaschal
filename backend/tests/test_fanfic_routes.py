"""Route-level tests: CRUD, FTS search, image traversal guard, journal links
and the ficRefs enrichment of journal responses."""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def fanfic_root(monkeypatch, tmp_path):
    monkeypatch.setenv('FANFIC_ROOT', str(tmp_path / 'fanfic'))
    return tmp_path / 'fanfic'


def make_fic(title='Test Fic', chapters=()):
    """Insert a fic + chapters directly; returns (fic_id, [chapter_ids])."""
    db = get_db()
    now = int(time.time())
    fic_id = str(ULID())
    db.execute(
        "INSERT INTO fics(id, title, author, source_type, site, thread_id,"
        " chapter_count, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (fic_id, title, 'Author', 'xenforo', 'forums.spacebattles.com',
         str(ULID()), len(chapters), now, now))
    chapter_ids = []
    for i, (ch_title, text) in enumerate(chapters, start=1):
        ch_id = str(ULID())
        chapter_ids.append(ch_id)
        db.execute(
            'INSERT INTO fic_chapters(id, fic_id, position, title, category,'
            ' content_html, content_text, source_post_id, word_count, created_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?)',
            (ch_id, fic_id, i, ch_title, 'threadmarks', f'<p>{text}</p>', text,
             str(1000 + i), len(text.split()), now))
    db.commit()
    return fic_id, chapter_ids


def make_journal_entry(client, content='Thoughts about the chapter'):
    resp = client.post('/api/journal', json={'content': content})
    assert resp.status_code == 201
    return resp.get_json()['id']


def test_get_404(client):
    assert client.get('/api/fanfic/nope').status_code == 404
    assert client.get('/api/fanfic/chapters/nope').status_code == 404


def test_list_excludes_description(client):
    make_fic()
    rows = client.get('/api/fanfic').get_json()
    assert len(rows) == 1
    assert 'description' not in rows[0]
    assert rows[0]['title'] == 'Test Fic'


def test_fts_search(client):
    fic_id, _ = make_fic('Wizard Fic', chapters=[
        ('Chapter One', 'The wizard cast a mighty spell.'),
        ('Chapter Two', 'A dragon appeared over the mountains.'),
    ])
    make_fic('Other Fic', chapters=[('Intro', 'Nothing relevant here.')])

    hits = client.get('/api/fanfic/search?query=wizard').get_json()
    assert len(hits) == 1
    assert hits[0]['id'] == fic_id
    assert hits[0]['matchedChapters'][0]['title'] == 'Chapter One'

    # FTS follows deletes
    client.delete(f'/api/fanfic/{fic_id}')
    assert client.get('/api/fanfic/search?query=wizard').get_json() == []


def test_image_traversal_guard(client, fanfic_root):
    secret = fanfic_root / 'secret.txt'
    fanfic_root.mkdir(parents=True)
    secret.write_text('secret-file-content')
    fic_id, _ = make_fic()
    # Hostile URLs must never leak file content outside the images dir
    # (some are neutralized by Werkzeug path normalization before routing,
    # in which case any status but the file's content is fine).
    for path in (
        f'/api/fanfic/{fic_id}/images/..%2fsecret.txt',
        f'/api/fanfic/{fic_id}/images/%2e%2e%2fsecret.txt',
        f'/api/fanfic/..%2f/images/secret.txt',
        f'/api/fanfic/{fic_id}/images/secret.txt',
    ):
        resp = client.get(path)
        assert b'secret-file-content' not in resp.data, path

    # The guard itself rejects hostile names outright
    from backend.fanfic.storage import safe_image_path
    assert safe_image_path(fic_id, '../secret.txt') is None
    assert safe_image_path(fic_id, '..') is None
    assert safe_image_path(fic_id, 'a/b.png') is None
    assert safe_image_path('..', 'secret.txt') is None
    assert safe_image_path(f'{fic_id}/..', 'secret.txt') is None
    ok = safe_image_path(fic_id, 'abc123.png')
    assert ok is not None and ok.name == 'abc123.png'


def test_delete_traversal_guard(client, fanfic_root):
    """DELETE /api/fanfic/.. must never rmtree outside the fanfic root —
    fanfic_root/'..' is the data dir holding the SQLite DB."""
    fanfic_root.mkdir(parents=True)
    canary = fanfic_root.parent / 'canary.txt'
    canary.write_text('still here')
    sibling = fanfic_root.parent / 'sibling-dir'
    sibling.mkdir()

    for fic_id in ('..', '.', '...', '..%2f', '%2e%2e'):
        client.delete(f'/api/fanfic/{fic_id}')
    assert canary.exists()
    assert sibling.is_dir()
    assert fanfic_root.is_dir()

    from backend.fanfic.storage import delete_fic_dir, fic_dir, pdf_path
    for hostile in ('..', '.', '...', '../x', 'a/b', ''):
        assert fic_dir(hostile) is None, hostile
        assert pdf_path(hostile) is None, hostile
        delete_fic_dir(hostile)  # must be a no-op, not an escape
    assert canary.exists()

    # legitimate ids still resolve and delete
    fic_id, _ = make_fic()
    (fanfic_root / fic_id).mkdir()
    assert fic_dir(fic_id) == fanfic_root / fic_id
    delete_fic_dir(fic_id)
    assert not (fanfic_root / fic_id).exists()
    assert fanfic_root.is_dir()


def test_reading_progress(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text'), ('Two', 'more text')])
    resp = client.post(f'/api/fanfic/{fic_id}/progress', json={'chapterId': chapter_ids[1]})
    assert resp.status_code == 200
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['lastReadChapterId'] == chapter_ids[1]
    # chapter of another fic rejected
    other_fic, other_chapters = make_fic('Other', chapters=[('X', 'y')])
    assert client.post(f'/api/fanfic/{fic_id}/progress',
                       json={'chapterId': other_chapters[0]}).status_code == 404


def test_journal_link_chapter_level(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    entry_id = make_journal_entry(client)

    resp = client.post(f'/api/fanfic/{fic_id}/journal-link',
                       json={'journalEntryId': entry_id, 'chapterId': chapter_ids[0]})
    assert resp.status_code == 201
    link_id = resp.get_json()['id']

    # idempotent
    again = client.post(f'/api/fanfic/{fic_id}/journal-link',
                        json={'journalEntryId': entry_id, 'chapterId': chapter_ids[0]})
    assert again.status_code == 200
    assert again.get_json()['id'] == link_id

    entries = client.get('/api/journal').get_json()
    entry = next(e for e in entries if e['id'] == entry_id)
    assert entry['ficRefs'] == [{
        'ficId': fic_id, 'ficTitle': 'Test Fic',
        'chapterId': chapter_ids[0], 'chapterTitle': 'One',
    }]

    # unlink
    resp = client.delete(
        f'/api/fanfic/{fic_id}/journal-link/{entry_id}?chapterId={chapter_ids[0]}')
    assert resp.status_code == 200
    entries = client.get('/api/journal').get_json()
    assert next(e for e in entries if e['id'] == entry_id)['ficRefs'] == []


def test_journal_link_fic_level(client):
    fic_id, _ = make_fic()
    entry_id = make_journal_entry(client)
    resp = client.post(f'/api/fanfic/{fic_id}/journal-link', json={'journalEntryId': entry_id})
    assert resp.status_code == 201
    # duplicate fic-level link is idempotent despite NULL chapter_id
    again = client.post(f'/api/fanfic/{fic_id}/journal-link', json={'journalEntryId': entry_id})
    assert again.status_code == 200

    entry = next(e for e in client.get('/api/journal').get_json() if e['id'] == entry_id)
    assert entry['ficRefs'] == [{
        'ficId': fic_id, 'ficTitle': 'Test Fic', 'chapterId': None, 'chapterTitle': None,
    }]


def test_journal_link_validation(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    entry_id = make_journal_entry(client)
    assert client.post(f'/api/fanfic/{fic_id}/journal-link', json={}).status_code == 400
    assert client.post('/api/fanfic/nope/journal-link',
                       json={'journalEntryId': entry_id}).status_code == 404
    assert client.post(f'/api/fanfic/{fic_id}/journal-link',
                       json={'journalEntryId': 'nope'}).status_code == 404
    assert client.post(f'/api/fanfic/{fic_id}/journal-link',
                       json={'journalEntryId': entry_id, 'chapterId': 'nope'}).status_code == 404


def test_fic_delete_cascades_refs(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    entry_id = make_journal_entry(client)
    client.post(f'/api/fanfic/{fic_id}/journal-link',
                json={'journalEntryId': entry_id, 'chapterId': chapter_ids[0]})
    client.delete(f'/api/fanfic/{fic_id}')
    # journal entry survives, ref is gone
    entry = next(e for e in client.get('/api/journal').get_json() if e['id'] == entry_id)
    assert entry['ficRefs'] == []


def test_journal_search_carries_fic_refs(client):
    fic_id, _ = make_fic()
    entry_id = make_journal_entry(client, content='pondering the wizard arc')
    client.post(f'/api/fanfic/{fic_id}/journal-link', json={'journalEntryId': entry_id})
    hits = client.get('/api/journal/search?query=pondering').get_json()
    assert hits and hits[0]['ficRefs'][0]['ficId'] == fic_id
