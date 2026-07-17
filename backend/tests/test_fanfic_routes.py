"""Route-level tests: CRUD, title/tag search, image traversal guard, journal
links and the ficRefs enrichment of journal responses."""
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


def test_list_orders_by_latest_chapter_not_fic_created(client):
    older_fic, _ = make_fic('Older Fic', chapters=[('Ch 1', 'hello world')])
    newer_fic, _ = make_fic('Newer Fic', chapters=[('Ch 1', 'hello world')])

    # A later "check updates" adds a fresh chapter to the older fic, which
    # should bump it back to the top even though it was created first.
    db = get_db()
    db.execute(
        'INSERT INTO fic_chapters(id, fic_id, position, title, category,'
        ' content_html, content_text, source_post_id, word_count, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?)',
        (str(ULID()), older_fic, 2, 'Ch 2', 'threadmarks', '<p>new</p>', 'new',
         '2000', 1, int(time.time()) + 1000))
    db.commit()

    rows = client.get('/api/fanfic').get_json()
    assert [r['id'] for r in rows] == [older_fic, newer_fic]


def test_search_matches_title(client):
    fic_id, _ = make_fic('The Wizard of Bones')
    make_fic('Other Fic')

    # case-insensitive substring match
    for q in ('wizard', 'WIZARD', 'izard of bo'):
        hits = client.get(f'/api/fanfic/search?query={q}').get_json()
        assert [h['id'] for h in hits] == [fic_id], q

    client.delete(f'/api/fanfic/{fic_id}')
    assert client.get('/api/fanfic/search?query=wizard').get_json() == []


def test_search_matches_tags(client):
    fic_a, _ = make_fic('Fic A')
    fic_b, _ = make_fic('Fic B')
    _tag_fic(fic_a, 'isekai')
    _tag_fic(fic_b, 'time travel')

    assert [h['id'] for h in
            client.get('/api/fanfic/search?query=Isekai').get_json()] == [fic_a]
    assert [h['id'] for h in
            client.get('/api/fanfic/search?query=travel').get_json()] == [fic_b]


def test_search_ignores_chapter_text(client):
    make_fic('Quiet Title', chapters=[('Chapter One', 'The wizard cast a mighty spell.')])
    assert client.get('/api/fanfic/search?query=wizard').get_json() == []
    assert client.get('/api/fanfic/search?query=Chapter+One').get_json() == []


def test_search_words_combine_title_and_tags(client):
    fic_a, _ = make_fic('Dragon Story')
    fic_b, _ = make_fic('Dragon Saga')
    _tag_fic(fic_a, 'isekai')

    # every word must hit the title or a tag; words can hit different fields
    assert [h['id'] for h in
            client.get('/api/fanfic/search?query=dragon+isekai').get_json()] == [fic_a]
    assert {h['id'] for h in
            client.get('/api/fanfic/search?query=dragon').get_json()} == {fic_a, fic_b}
    assert client.get('/api/fanfic/search?query=dragon+nothing').get_json() == []


def test_search_escapes_like_wildcards(client):
    make_fic('Plain Fic')
    fic_pct, _ = make_fic('100% Wolf')
    assert [h['id'] for h in
            client.get('/api/fanfic/search?query=%25').get_json()] == [fic_pct]
    assert client.get('/api/fanfic/search?query=_').get_json() == []


def test_search_results_carry_library_meta(client):
    fic_id, _ = make_fic('Wizard Fic')
    _tag_fic(fic_id, 'isekai')
    hits = client.get('/api/fanfic/search?query=wizard').get_json()
    assert hits[0]['tags'] == ['isekai']
    assert hits[0]['folderIds'] == []


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


# --- folders ---

def make_folder(client, name):
    resp = client.post('/api/fanfic/folders', json={'name': name})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()['id']


def test_folder_crud(client):
    # static /folders route must not be swallowed by /<fic_id>
    assert client.get('/api/fanfic/folders').get_json() == []

    folder_id = make_folder(client, 'backlog')
    assert client.post('/api/fanfic/folders', json={'name': 'backlog'}).status_code == 409
    assert client.post('/api/fanfic/folders', json={'name': '  '}).status_code == 400

    folders = client.get('/api/fanfic/folders').get_json()
    assert [(f['name'], f['ficCount']) for f in folders] == [('backlog', 0)]

    assert client.patch(f'/api/fanfic/folders/{folder_id}',
                        json={'name': 'finished'}).status_code == 200
    assert client.patch('/api/fanfic/folders/nope', json={'name': 'x'}).status_code == 404
    other = make_folder(client, 'favorites')
    assert client.patch(f'/api/fanfic/folders/{other}',
                        json={'name': 'finished'}).status_code == 409

    assert client.delete(f'/api/fanfic/folders/{folder_id}').status_code == 200
    assert [f['name'] for f in client.get('/api/fanfic/folders').get_json()] == ['favorites']


def _add_chapter(fic_id, position=1, posted_at=None, created_at=None):
    db = get_db()
    db.execute(
        'INSERT INTO fic_chapters(id, fic_id, position, title, category,'
        ' content_html, content_text, source_post_id, word_count, posted_at, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (str(ULID()), fic_id, position, f'Ch {position}', 'threadmarks', '<p>x</p>', 'x',
         str(ULID()), 1, posted_at, created_at or int(time.time())))
    db.commit()


def test_folder_reorder(client):
    a = make_folder(client, 'a')
    b = make_folder(client, 'b')
    c = make_folder(client, 'c')

    def names():
        return [f['name'] for f in client.get('/api/fanfic/folders').get_json()]

    # default order is creation order
    assert names() == ['a', 'b', 'c']

    assert client.put('/api/fanfic/folders/order',
                      json={'ids': [c, a, b]}).status_code == 200
    assert names() == ['c', 'a', 'b']

    # new folders append at the end of the custom order
    make_folder(client, 'd')
    assert names() == ['c', 'a', 'b', 'd']


def test_folder_reorder_validation(client):
    a = make_folder(client, 'a')
    b = make_folder(client, 'b')

    for bad in ({}, {'ids': 'x'}, {'ids': [a]}, {'ids': [a, a]},
                {'ids': [a, b, 'nope']}, {'ids': [a, 'nope']}):
        assert client.put('/api/fanfic/folders/order', json=bad).status_code == 400, bad
    # failed calls leave the order untouched
    assert [f['name'] for f in client.get('/api/fanfic/folders').get_json()] == ['a', 'b']


def test_all_view_groups_fics_by_folder_order(client):
    now = int(time.time())
    fic_a, _ = make_fic('A')
    fic_b, _ = make_fic('B')
    fic_c, _ = make_fic('C')  # stays unsorted
    # distinct forum dates make within-group order deterministic:
    # a is the most recently updated, then b, then c
    _add_chapter(fic_a, posted_at=now - 50)
    _add_chapter(fic_b, posted_at=now - 100)
    _add_chapter(fic_c, posted_at=now - 10)
    first = make_folder(client, 'first')
    second = make_folder(client, 'second')
    client.post(f'/api/fanfic/{fic_a}/folders', json={'folderId': first})
    client.post(f'/api/fanfic/{fic_b}/folders', json={'folderId': second})

    def ids():
        return [f['id'] for f in client.get('/api/fanfic').get_json()]

    # folder 1's fics, then folder 2's, unsorted last — even though the
    # unsorted fic has the newest forum activity
    assert ids() == [fic_a, fic_b, fic_c]

    # swapping the folders swaps the groups
    client.put('/api/fanfic/folders/order', json={'ids': [second, first]})
    assert ids() == [fic_b, fic_a, fic_c]

    # a fic in several folders sorts under its earliest-positioned one:
    # a joins the leading folder "second" and outranks b there by recency
    client.post(f'/api/fanfic/{fic_a}/folders', json={'folderId': second})
    assert ids() == [fic_a, fic_b, fic_c]

    # single-folder and unsorted views ignore grouping entirely
    assert [f['id'] for f in
            client.get(f'/api/fanfic?folderId={second}').get_json()] == [fic_a, fic_b]
    assert [f['id'] for f in
            client.get('/api/fanfic?folderId=unsorted').get_json()] == [fic_c]

    # sort=recent opts out of folder grouping too: c (unsorted, most recent)
    # now leads even though it trails both folders in the grouped view
    assert [f['id'] for f in
            client.get('/api/fanfic?sort=recent').get_json()] == [fic_c, fic_a, fic_b]


def test_list_orders_by_forum_post_date_not_import_date(client):
    now = int(time.time())
    # imported just now, but its latest forum post is old
    stale, _ = make_fic('Stale')
    _add_chapter(stale, posted_at=now - 5000, created_at=now)
    # imported long ago, but has recent forum activity
    fresh, _ = make_fic('Fresh')
    _add_chapter(fresh, posted_at=now - 100, created_at=now - 3000)

    ids = [f['id'] for f in client.get('/api/fanfic').get_json()]
    assert ids == [fresh, stale]

    # chapters without a forum date (epub/docx) fall back to import time
    epub, _ = make_fic('Epub')
    _add_chapter(epub, posted_at=None, created_at=now + 50)
    ids = [f['id'] for f in client.get('/api/fanfic').get_json()]
    assert ids == [epub, fresh, stale]

    # search uses the same ordering
    hits = [f['id'] for f in client.get('/api/fanfic/search?query=s').get_json()]
    assert hits == [fresh, stale]


def test_folder_membership(client):
    fic_id, _ = make_fic()
    folder_id = make_folder(client, 'favorites')

    assert client.post(f'/api/fanfic/{fic_id}/folders',
                       json={'folderId': folder_id}).status_code == 200
    # idempotent
    assert client.post(f'/api/fanfic/{fic_id}/folders',
                       json={'folderId': folder_id}).status_code == 200
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['folderIds'] == [folder_id]
    assert client.get('/api/fanfic/folders').get_json()[0]['ficCount'] == 1

    # unknown fic / folder rejected
    assert client.post('/api/fanfic/nope/folders',
                       json={'folderId': folder_id}).status_code == 404
    assert client.post(f'/api/fanfic/{fic_id}/folders',
                       json={'folderId': 'nope'}).status_code == 404

    # deleting the folder removes only the membership, never the fic
    assert client.delete(f'/api/fanfic/folders/{folder_id}').status_code == 200
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['title'] == 'Test Fic'
    assert fic['folderIds'] == []


def test_folder_membership_remove_and_fic_cascade(client):
    fic_id, _ = make_fic()
    folder_id = make_folder(client, 'backlog')
    client.post(f'/api/fanfic/{fic_id}/folders', json={'folderId': folder_id})

    assert client.delete(f'/api/fanfic/{fic_id}/folders/{folder_id}').status_code == 200
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['folderIds'] == []

    # deleting a fic cascades its memberships
    client.post(f'/api/fanfic/{fic_id}/folders', json={'folderId': folder_id})
    client.delete(f'/api/fanfic/{fic_id}')
    assert client.get('/api/fanfic/folders').get_json()[0]['ficCount'] == 0


def _tag_fic(fic_id, *names):
    db = get_db()
    now = int(time.time())
    db.executemany(
        'INSERT INTO fic_site_tags(fic_id, name, created_at) VALUES (?,?,?)',
        [(fic_id, n, now) for n in names])
    db.commit()


def test_list_filters_by_folder_and_tag(client):
    fic_a, _ = make_fic('Fic A')
    fic_b, _ = make_fic('Fic B')
    folder_id = make_folder(client, 'favorites')
    client.post(f'/api/fanfic/{fic_a}/folders', json={'folderId': folder_id})
    _tag_fic(fic_a, 'isekai')
    _tag_fic(fic_b, 'isekai', 'worm')

    assert {f['id'] for f in client.get('/api/fanfic').get_json()} == {fic_a, fic_b}
    assert [f['id'] for f in client.get(f'/api/fanfic?folderId={folder_id}').get_json()] == [fic_a]
    assert {f['id'] for f in client.get('/api/fanfic?tag=isekai').get_json()} == {fic_a, fic_b}
    assert [f['id'] for f in client.get('/api/fanfic?tag=worm').get_json()] == [fic_b]
    # filters intersect
    assert [f['id'] for f in
            client.get(f'/api/fanfic?folderId={folder_id}&tag=worm').get_json()] == []

    # list rows carry the tags for chips
    rows = client.get('/api/fanfic').get_json()
    assert next(f for f in rows if f['id'] == fic_b)['tags'] == ['isekai', 'worm']


def test_list_filters_unsorted(client):
    fic_a, _ = make_fic('Fic A')
    fic_b, _ = make_fic('Fic B')
    folder_id = make_folder(client, 'favorites')
    client.post(f'/api/fanfic/{fic_a}/folders', json={'folderId': folder_id})

    assert [f['id'] for f in client.get('/api/fanfic?folderId=unsorted').get_json()] == [fic_b]


def test_site_tag_index(client):
    fic_a, _ = make_fic('Fic A')
    fic_b, _ = make_fic('Fic B')
    _tag_fic(fic_a, 'isekai')
    _tag_fic(fic_b, 'isekai', 'worm')
    assert client.get('/api/fanfic/tags').get_json() == [
        {'name': 'isekai', 'count': 2},
        {'name': 'worm', 'count': 1},
    ]


# --- read tracking ---

def test_progress_marks_chapter_read(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text'), ('Two', 'more')])
    client.post(f'/api/fanfic/{fic_id}/progress', json={'chapterId': chapter_ids[0]})

    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    assert [c['isRead'] for c in chapters] == [True, False]
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['readCount'] == 1
    assert client.get('/api/fanfic').get_json()[0]['readCount'] == 1


def test_bulk_read_toggle(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'a'), ('Two', 'b'), ('Three', 'c')])

    resp = client.post(f'/api/fanfic/{fic_id}/read',
                       json={'chapterIds': chapter_ids[:2], 'read': True})
    assert resp.status_code == 200
    assert resp.get_json()['readCount'] == 2
    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    assert [c['isRead'] for c in chapters] == [True, True, False]

    # marking again is idempotent
    resp = client.post(f'/api/fanfic/{fic_id}/read',
                       json={'chapterIds': chapter_ids[:2], 'read': True})
    assert resp.get_json()['readCount'] == 2

    # unmark one
    resp = client.post(f'/api/fanfic/{fic_id}/read',
                       json={'chapterIds': [chapter_ids[0]], 'read': False})
    assert resp.get_json()['readCount'] == 1
    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    assert [c['isRead'] for c in chapters] == [False, True, False]


def test_bulk_read_validation(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'a')])
    other_fic, other_chapters = make_fic('Other', chapters=[('X', 'y')])

    assert client.post(f'/api/fanfic/{fic_id}/read', json={}).status_code == 400
    assert client.post(f'/api/fanfic/{fic_id}/read',
                       json={'chapterIds': [], 'read': True}).status_code == 400
    assert client.post(f'/api/fanfic/{fic_id}/read',
                       json={'chapterIds': chapter_ids, 'read': 'yes'}).status_code == 400
    # a chapter belonging to another fic is rejected wholesale
    assert client.post(f'/api/fanfic/{fic_id}/read',
                       json={'chapterIds': chapter_ids + other_chapters,
                             'read': True}).status_code == 404
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['readCount'] == 0


def test_read_state_cascades_on_delete(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'a')])
    client.post(f'/api/fanfic/{fic_id}/read', json={'chapterIds': chapter_ids, 'read': True})
    client.delete(f'/api/fanfic/{fic_id}')
    rows = get_db().execute('SELECT COUNT(*) AS n FROM fic_chapter_reads').fetchone()
    assert rows['n'] == 0


# --- review ---

def test_review_roundtrip(client):
    fic_id, _ = make_fic()
    resp = client.patch(f'/api/fanfic/{fic_id}/review',
                        json={'rating': 4, 'review': 'Great worldbuilding.'})
    assert resp.status_code == 200
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['rating'] == 4
    assert fic['review'] == 'Great worldbuilding.'
    # rating rides the list columns for the library card
    assert client.get('/api/fanfic').get_json()[0]['rating'] == 4

    # partial update: rating only, review preserved
    client.patch(f'/api/fanfic/{fic_id}/review', json={'rating': 5})
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['rating'] == 5
    assert fic['review'] == 'Great worldbuilding.'

    # null clears
    client.patch(f'/api/fanfic/{fic_id}/review', json={'rating': None, 'review': None})
    fic = client.get(f'/api/fanfic/{fic_id}').get_json()
    assert fic['rating'] is None
    assert fic['review'] is None


def test_review_validation(client):
    fic_id, _ = make_fic()
    for bad in (0, 6, 'x', 3.5, True):
        assert client.patch(f'/api/fanfic/{fic_id}/review',
                            json={'rating': bad}).status_code == 400, bad
    assert client.patch(f'/api/fanfic/{fic_id}/review', json={}).status_code == 400
    assert client.patch('/api/fanfic/nope/review', json={'rating': 3}).status_code == 404
    # whitespace-only review is stored as null
    client.patch(f'/api/fanfic/{fic_id}/review', json={'review': '   '})
    assert client.get(f'/api/fanfic/{fic_id}').get_json()['review'] is None
