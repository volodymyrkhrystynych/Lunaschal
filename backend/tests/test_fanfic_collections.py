"""Offline collection discovery, downloads and migration preservation."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.db.connection import get_db
from backend.fanfic import collections, download, sites


def response(text='', url='https://www.fanfiction.net/favorites/story.php', data=None):
    return SimpleNamespace(text=text, url=url, json=lambda: data)


def ffn(chapter=1):
    return f'''<div id="profile_top"><b class="xcontrast_txt">A story</b>
    <a href="/u/42/Writer">Writer</a><div class="xcontrast_txt">Summary</div></div>
    <select name="chapter"><option value="1">First</option><option value="2">Second</option></select>
    <div id="storytext"><p>Text for chapter {chapter}</p><script>bad()</script></div>'''


AO3 = '''<div id="workskin"><div class="preface"><h2 class="title">AO3 story</h2>
<h3 class="byline">Writer</h3></div><div id="chapters">
<div class="chapter"><div class="chapter preface group"><h3 class="title"><a href="/works/12/chapters/90">First</a></h3></div>
<div class="userstuff" role="article"><p>First chapter text</p></div></div>
<div class="chapter"><div class="chapter preface group"><h3 class="title"><a href="/works/12/chapters/91">Second</a></h3></div>
<div class="userstuff" role="article"><p>Second chapter text</p></div></div></div></div>'''


def patreon(allowed=True):
    return {'data': {'id': '45', 'type': 'post', 'attributes': {
        'title': 'A post', 'content': '<p>Members chapter</p>', 'current_user_can_view': allowed,
        'published_at': '2026-01-02T10:00:00Z'},
        'relationships': {'user': {'data': {'id': '7'}}}},
        'included': [{'type': 'user', 'id': '7', 'attributes': {'full_name': 'Writer'}}]}


@pytest.mark.parametrize('url,kind,key', [
    ('https://m.fanfiction.net/s/123/7/Title', 'fanfiction', '123'),
    ('https://archiveofourown.org/works/12/chapters/90?view_adult=true', 'ao3', '12'),
    ('https://www.patreon.com/posts/a-post-45', 'patreon', '45'),
])
def test_canonical_work_urls(url, kind, key):
    ref = sites.parse_work_url(url)
    assert (ref.source_type, ref.id) == (kind, key)
    assert sites.parse_work_url(ref.url) == ref


@pytest.mark.parametrize('url', ['https://fanfiction.net.evil/s/123/1',
                                'https://user@www.patreon.com/posts/45',
                                'file:///works/12', 'https://archiveofourown.org:5000/works/12'])
def test_reject_untrusted_work_urls(url):
    assert sites.parse_work_url(url) is None


def test_parsers():
    ref = sites.parse_work_url('https://www.fanfiction.net/s/123/1/')
    book = sites.parse_ffn(ffn(), ref)
    assert (book['title'], book['author'], book['total']) == ('A story', 'Writer', 2)
    book = sites.parse_ao3(AO3, sites.parse_work_url('https://archiveofourown.org/works/12'))
    assert [c[0] for c in book['chapters']] == ['90', '91']
    assert sites.parse_patreon(patreon())['author'] == 'Writer'
    with pytest.raises(ValueError, match='locked'):
        sites.parse_patreon(patreon(False))


def dated_list(added):
    return f'''<form><table id="gui_table1"><tr>
      <td><a href="/s/123/1/Story">Story</a></td>
      <td><a href="/u/42/Writer">Writer</a></td><td>Books</td>
      <td>09-12-2026</td><td>{added}</td><td>Remove</td>
      </tr></table></form>'''


@pytest.mark.parametrize('added,expected', [('03-14-2019', 1552521600),
                                           ('invalid', None), ('', None)])
def test_list_addition_dates_are_not_story_updates(added, expected):
    for path, field in [('favorites', 'favorited_at'), ('alert', 'followed_at')]:
        refs, _ = sites.parse_collection(dated_list(added),
                                        f'https://www.fanfiction.net/{path}/story.php')
        assert getattr(refs[0], field) == expected
    refs, _ = sites.parse_collection(
        '<div id="content_wrapper_inner"><a href="/s/123/1/">Story</a>'
        '<span data-xutime="1552521600">Published</span></div>',
        'https://www.fanfiction.net/u/42/Reader')
    assert refs[0].favorited_at is None and refs[0].followed_at is None


def test_list_dates_enrich_existing_story_without_redownload(client, monkeypatch):
    ref = sites.parse_work_url('https://www.fanfiction.net/s/123/1/')
    fic_id, _ = collections.queue_work(ref)
    monkeypatch.setattr(collections, '_fetch', lambda url: response(ffn()))
    download.run_drain_pending()
    for path, date in [('favorites', '03-14-2019'), ('alert', '03-15-2019')]:
        refs, _ = sites.parse_collection(dated_list(date),
                                        f'https://www.fanfiction.net/{path}/story.php')
        assert collections.queue_work(refs[0]) == (fic_id, False)
    collections.queue_work(ref)  # A scan without dates must retain saved history.
    db = get_db()
    assert db.execute('SELECT COUNT(*) FROM fics').fetchone()[0] == 1
    assert db.execute('SELECT update_pending FROM fics').fetchone()[0] == 0
    collections.run_work(fic_id, ref.url)  # Metadata refresh must also retain it.
    result = client.get('/api/fanfic').json
    assert result[0]['sourceFavoritedAt'].startswith('2019-03-14')
    assert result[0]['sourceFollowedAt'].startswith('2019-03-15')


def test_source_date_migration_is_idempotent():
    from backend.db.connection import _ensure_fic_source_dates
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fics(id TEXT PRIMARY KEY, title TEXT)')
    db.execute("INSERT INTO fics VALUES ('old','Keep')")
    _ensure_fic_source_dates(db)
    db.execute('UPDATE fics SET source_favorited_at=1552521600')
    _ensure_fic_source_dates(db)
    assert db.execute('SELECT * FROM fics').fetchone() == ('old', 'Keep', 1552521600, None)
    db.close()


def test_ao3_one_shot_and_login_wall():
    ref = sites.parse_work_url('https://archiveofourown.org/works/12')
    book = sites.parse_ao3('''<div id="workskin"><div class="preface"><h2 class="title">One</h2></div>
        <div id="chapters"><div class="userstuff">One shot</div></div></div>''', ref)
    assert book['total'] == 1
    with pytest.raises(ValueError, match='unavailable'):
        sites.parse_ao3('<form>Log in</form>', ref)


def test_collection_ignores_navigation_and_paginates():
    url = 'https://archiveofourown.org/users/Reader/bookmarks'
    refs, next_url = sites.parse_collection('''<a href="/works/999">Recommendation</a>
      <ol class="bookmark index"><li><h4 class="heading"><a href="/works/12">Story</a>
      <a href="/users/Writer">Writer</a></h4></li></ol>
      <ol class="pagination"><li class="next"><a href="?page=2">Next →</a></li></ol>''', url)
    assert [r.id for r in refs] == ['12']
    assert next_url == url + '?page=2'
    with pytest.raises(ValueError, match='Session expired'):
        sites.parse_collection('<input type="password">', url)
    with pytest.raises(ValueError, match='outside'):
        sites.parse_collection('''<ol class="bookmark index"></ol>
            <a rel="next" href="https://evil.test/">Next</a>''', url)


def test_patreon_pagination_skips_locked_posts():
    data = {'data': [patreon()['data'], {**patreon(False)['data'], 'id': '46'}],
            'links': {'next': '?page%5Bcursor%5D=next'}}
    refs, next_url, skipped = sites.parse_patreon_collection(data, sites.patreon_api('stream'))
    assert [r.id for r in refs] == ['45']
    assert 'cursor' in next_url and skipped == 1


def test_patreon_structured_text_preserves_prose():
    data = patreon()
    data['data']['attributes']['content'] = None
    data['data']['attributes']['content_json_string'] = json.dumps({
        'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
            {'type': 'text', 'text': 'A <chapter>', 'marks': [{'type': 'bold'}]}]}]})
    book = sites.parse_patreon(data)
    assert book['chapters'][0][2] == '<p><strong>A &lt;chapter&gt;</strong></p>'


def test_locked_post_fails_without_saving_preview(monkeypatch):
    monkeypatch.setattr(collections, '_fetch', lambda u: response(url=u, data=patreon(False)))
    fic_id, _ = collections.queue_work(sites.parse_work_url('https://www.patreon.com/posts/45'))
    download.run_drain_pending()
    db = get_db()
    assert db.execute('SELECT download_status FROM fics WHERE id=?', (fic_id,)).fetchone()[0] == 'error'
    assert db.execute('SELECT COUNT(*) FROM fic_chapters').fetchone()[0] == 0


def test_restart_requeues_interrupted_online_work():
    from backend.db.connection import _reset_stale_fic_downloads
    fic_id, _ = collections.queue_work(sites.parse_work_url('https://archiveofourown.org/works/12'))
    db = get_db()
    db.execute("UPDATE fics SET download_status='downloading',update_pending=0 WHERE id=?", (fic_id,))
    db.commit()
    _reset_stale_fic_downloads(db)
    row = db.execute('SELECT download_status,update_pending FROM fics WHERE id=?', (fic_id,)).fetchone()
    assert tuple(row) == ('error', 1)


def test_scan_resume_and_dedup(monkeypatch):
    first, follows = sites.collection_urls('fanfiction.net', 'all')
    second = first + '?page=2'
    calls = []
    pages = {first: f'<div id="gui_table1"><a href="/s/123/1/">A</a></div><a rel="next" href="{second}">Next</a>',
             follows: '<div id="gui_table1"><a href="/s/123/1/Other-title">A</a></div>'}
    def fetch(url):
        calls.append(url)
        if url not in pages:
            raise ValueError('Temporary failure')
        return response(pages[url], url)
    monkeypatch.setattr(collections, '_fetch', fetch)
    scan_id = collections.create_scan('fanfiction.net', 'all', '')
    collections.run_scan(scan_id)
    row = get_db().execute('SELECT * FROM fanfic_collection_scans').fetchone()
    assert row['status'] == 'error'
    assert json.loads(row['remaining_urls'])[0] == second
    assert get_db().execute('SELECT COUNT(*) FROM fics').fetchone()[0] == 1
    pages[second] = '<div id="gui_table1"><a href="/s/124/1/">B</a></div>'
    assert collections.create_scan('fanfiction.net', 'all', '') == scan_id
    collections.run_scan(scan_id)
    assert calls.count(first) == 1
    row = get_db().execute('SELECT * FROM fanfic_collection_scans').fetchone()
    assert (row['status'], row['found'], row['imported']) == ('complete', 3, 2)
    assert get_db().execute('SELECT COUNT(*) FROM fics WHERE update_pending=1').fetchone()[0] == 2


def test_repeated_pagination_stops(monkeypatch):
    url = sites.collection_urls('fanfiction.net', 'favorites')[0]
    monkeypatch.setattr(collections, '_fetch', lambda _: response(
        f'<div id="gui_table1"></div><a rel="next" href="{url}">Next</a>', url))
    scan_id = collections.create_scan('fanfiction.net', 'favorites', '')
    collections.run_scan(scan_id)
    row = get_db().execute('SELECT status,error FROM fanfic_collection_scans').fetchone()
    assert row['status'] == 'error' and 'repeated' in row['error']


@pytest.mark.parametrize('url,body', [
    ('https://www.fanfiction.net/s/123/1/', ffn()),
    ('https://archiveofourown.org/works/12', AO3),
    ('https://www.patreon.com/posts/45', ''),
])
def test_download_queue_and_deep_update_preserve_reading(monkeypatch, url, body):
    monkeypatch.setattr(collections, '_fetch', lambda u: response(body, u, patreon()))
    ref = sites.parse_work_url(url)
    fic_id, created = collections.queue_work(ref)
    assert created
    download.run_drain_pending()
    db = get_db()
    fic = db.execute('SELECT * FROM fics WHERE id=?', (fic_id,)).fetchone()
    assert fic['download_status'] == 'complete', fic['download_error']
    assert fic['chapter_count'] == (1 if ref.source_type == 'patreon' else 2)
    before = [dict(r) for r in db.execute('SELECT * FROM fic_chapters WHERE fic_id=? ORDER BY position', (fic_id,))]
    assert 'bad()' not in before[0]['content_html']
    db.execute('UPDATE fics SET last_read_chapter_id=?,update_pending=1,deep_pending=1 WHERE id=?',
               (before[0]['id'], fic_id))
    db.commit()
    download.run_drain_pending()
    after = db.execute('SELECT id FROM fic_chapters WHERE fic_id=? ORDER BY position', (fic_id,)).fetchall()
    assert [r['id'] for r in after] == [r['id'] for r in before]
    assert db.execute('SELECT last_read_chapter_id FROM fics WHERE id=?', (fic_id,)).fetchone()[0] == before[0]['id']
    assert collections.queue_work(ref) == (fic_id, False)


def test_failed_chapter_is_resumable(monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        if '/2/' in url:
            raise ValueError('Unavailable chapter')
        return response(ffn(), url)
    monkeypatch.setattr(collections, '_fetch', fetch)
    ref = sites.parse_work_url('https://www.fanfiction.net/s/123/1/')
    fic_id, _ = collections.queue_work(ref)
    download.run_drain_pending()
    assert get_db().execute('SELECT download_status FROM fics').fetchone()[0] == 'error'
    assert get_db().execute('SELECT COUNT(*) FROM fic_chapters').fetchone()[0] == 1
    monkeypatch.setattr(collections, '_fetch', lambda u: response(ffn(2), u))
    collections.queue_work(ref)
    download.run_drain_pending()
    assert get_db().execute('SELECT chapter_count FROM fics').fetchone()[0] == 2


def test_ao3_one_shot_growing_keeps_first_chapter_id(monkeypatch):
    one_shot = '''<div id="workskin"><div class="preface"><h2 class="title">One</h2></div>
        <div id="chapters"><div class="userstuff">First chapter text</div></div></div>'''
    monkeypatch.setattr(collections, '_fetch', lambda u: response(one_shot, u))
    ref = sites.parse_work_url('https://archiveofourown.org/works/12')
    fic_id, _ = collections.queue_work(ref)
    download.run_drain_pending()
    db = get_db()
    first_id = db.execute('SELECT id FROM fic_chapters').fetchone()[0]
    db.execute('UPDATE fics SET update_pending=1,last_read_chapter_id=? WHERE id=?', (first_id, fic_id))
    db.commit()
    monkeypatch.setattr(collections, '_fetch', lambda u: response(AO3, u))
    download.run_drain_pending()
    assert db.execute('SELECT COUNT(*) FROM fic_chapters').fetchone()[0] == 2
    assert db.execute('SELECT id FROM fic_chapters WHERE position=1').fetchone()[0] == first_id
    assert db.execute('SELECT last_read_chapter_id FROM fics').fetchone()[0] == first_id


def test_collection_routes_validate_session_and_username(client, monkeypatch):
    monkeypatch.setattr(collections, 'start_scans', lambda: None)
    assert client.post('/api/fanfic/collections', json={'site': 'archiveofourown.org'}).status_code == 400
    assert client.put('/api/fanfic/cookies', json={'domain': 'archiveofourown.org', 'cookie': 'session=fake'}).status_code == 200
    assert client.post('/api/fanfic/collections', json={'site': 'archiveofourown.org', 'username': '../bad'}).status_code == 400
    result = client.post('/api/fanfic/collections', json={'site': 'archiveofourown.org', 'username': 'Reader'})
    assert result.status_code == 202
    assert client.get('/api/fanfic/collections').json[0]['status'] == 'pending'
    cookies = client.get('/api/fanfic/cookies').json
    assert sites.DOMAINS <= {c['domain'] for c in cookies}
    assert all('cookie' not in c for c in cookies)


def test_import_and_update_routes(client, monkeypatch):
    from backend.routes import fanfic
    monkeypatch.setattr(fanfic, '_start_drain_bg', lambda: None)
    first = client.post('/api/fanfic/import', json={'url': 'https://m.fanfiction.net/s/123/2/Title'})
    second = client.post('/api/fanfic/import', json={'url': 'https://www.fanfiction.net/s/123/1/Other'})
    assert first.status_code == 202
    assert second.json['id'] == first.json['id'] and second.json['alreadyExists']
    result = client.post('/api/fanfic/' + first.json['id'] + '/check-updates', json={'deep': True})
    assert result.status_code == 202


def test_cross_host_redirect_does_not_send_session(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False
        return SimpleNamespace(status_code=302, headers={'Location': 'https://evil.test/'}, close=lambda: None)
    monkeypatch.setattr(download, '_http_get', get)
    with pytest.raises(ValueError, match='outside'):
        collections._fetch('https://www.patreon.com/posts/45')
    assert calls == ['https://www.patreon.com/posts/45']


def test_migration_keeps_chapters_and_foreign_keys():
    from backend.db.fic_sources_migration import ensure_fic_sources
    schema = (Path(__file__).parents[1] / 'db/schema.sql').read_text().replace(
        "'pdf','fanfiction','ao3','patreon'", "'pdf'")
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(schema)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute("INSERT INTO fics(id,title,source_type,created_at,updated_at) VALUES ('fic','Old','epub',1,1)")
    db.execute("INSERT INTO fic_chapters(id,fic_id,position,title,content_html,content_text,created_at)"
               " VALUES ('chapter','fic',1,'Chapter','<p>Keep</p>','Keep',1)")
    db.commit()
    ensure_fic_sources(db)
    ensure_fic_sources(db)
    assert db.execute('SELECT fic_id FROM fic_chapters').fetchone()[0] == 'fic'
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    db.execute("INSERT INTO fics(id,title,source_type,created_at,updated_at) VALUES ('new','New','ao3',1,1)")
    db.execute("DELETE FROM fics WHERE id='fic'")
    assert not db.execute('SELECT * FROM fic_chapters').fetchall()
    db.close()
