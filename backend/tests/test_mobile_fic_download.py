import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.mobile_sync import fic_download


def fic_with_chapters(count, *, text='Words', source_type='xenforo'):
    db = get_db()
    fic = str(ULID())
    db.execute("INSERT INTO fics(id,title,source_type,created_at,updated_at) VALUES (?,'Book',?,1,1)", (fic, source_type))
    ids = []
    # Inserted out of order: the page must come back by position, not by id.
    for position in reversed(range(count)):
        chapter = str(ULID())
        db.execute("INSERT INTO fic_chapters(id,fic_id,position,title,content_html,content_text,created_at,updated_at) "
                   "VALUES (?,?,?,?,'',?,1,1)", (chapter, fic, position, f'Chapter {position}', text))
        ids.append((position, chapter))
    db.commit()
    return fic, [chapter for _, chapter in sorted(ids)]


def test_pages_one_fics_chapters_in_position_order(client):
    fic, chapters = fic_with_chapters(5)
    other, _ = fic_with_chapters(3)
    first = client.get(f'/api/mobile/fics/{fic}/download?limit=2').json
    assert [c['id'] for c in first['chapters']] == chapters[:2]
    assert first['hasMore'] and first['totalChapters'] == 5
    assert first['textBytes'] == 5 * len('Words') and first['pageBytes'] == 2 * len('Words')
    assert first['bytesBefore'] == 0
    assert first['fic']['id'] == fic and first['media'] is None
    seen = [c['id'] for c in first['chapters']]
    after = first['after']
    while True:
        page = client.get(f'/api/mobile/fics/{fic}/download', query_string={'limit': 2, 'after': after}).json
        seen += [c['id'] for c in page['chapters']]
        after = page['after']
        if not page['hasMore']:
            break
    assert seen == chapters
    assert page['bytesBefore'] == 4 * len('Words')
    assert other not in {c['data']['ficId'] for c in first['chapters']}


def test_chapters_carry_the_same_revision_a_bootstrap_would(client):
    fic, chapters = fic_with_chapters(1)
    db = get_db()
    db.execute("UPDATE fic_chapters SET title='Renamed' WHERE id=?", (chapters[0],))
    db.commit()
    page = client.get(f'/api/mobile/fics/{fic}/download').json
    bootstrap = client.get('/api/mobile/sync?collections=fic_chapters').json
    by_id = {c['id']: c for c in bootstrap['changes']}
    assert page['chapters'][0] == by_id[chapters[0]]
    assert page['chapters'][0]['data']['title'] == 'Renamed'
    assert page['epoch'] == bootstrap['epoch']


def test_a_page_stops_at_its_byte_target_but_never_comes_back_empty(client, monkeypatch):
    monkeypatch.setattr(fic_download, 'PAGE_BYTE_TARGET', 10)
    fic, chapters = fic_with_chapters(3, text='x' * 25)
    page = client.get(f'/api/mobile/fics/{fic}/download?limit=20').json
    assert [c['id'] for c in page['chapters']] == chapters[:1]
    assert page['hasMore']


def test_pdf_fic_carries_its_media_descriptor(client, tmp_path, monkeypatch):
    root = tmp_path / 'fanfic'
    monkeypatch.setenv('FANFIC_ROOT', str(root))
    fic, _ = fic_with_chapters(0, source_type='pdf')
    path = root / fic / 'book.pdf'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'%PDF-1.4\nexample book')
    media = client.get(f'/api/mobile/fics/{fic}/download').json['media']
    assert media['available'] and media['size'] == path.stat().st_size
    assert 'path' not in media and str(root) not in str(media)


def test_capabilities_advertise_it(client):
    assert client.get('/api/mobile/capabilities').json['ficDownload'] is True


@pytest.mark.parametrize('query', [{'limit': 0}, {'limit': 51}, {'after': 'nocolon'}, {'after': 'x:1'}])
def test_rejects_bad_paging(client, query):
    fic, _ = fic_with_chapters(1)
    assert client.get(f'/api/mobile/fics/{fic}/download', query_string=query).status_code == 400


def test_unknown_fic_is_404(client):
    assert client.get(f'/api/mobile/fics/{ULID()}/download').status_code == 404
