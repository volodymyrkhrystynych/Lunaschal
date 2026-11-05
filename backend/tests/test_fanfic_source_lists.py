"""Which of the user's own site lists each fic is on, and the Backlog."""
import requests

from backend.db.connection import get_db
from backend.fanfic import download, source_lists
from backend.tests.test_fanfic_watched_scan import (  # noqa: F401
    SITE, WATCHED_P2, _put_cookie, fake_net)


def _lists():
    rows = get_db().execute(
        'SELECT f.thread_id, l.list FROM fic_source_lists l JOIN fics f ON f.id=l.fic_id').fetchall()
    return {(r['thread_id'], r['list']) for r in rows}


def _backlog(client):
    return {f['sourceUrl'].split('.')[-1].strip('/')
            for f in client.get('/api/fanfic?folderId=backlog').get_json()}


def _add_fic(thread_id, source_type='xenforo', site=SITE):
    db = get_db()
    fic_id = f'01J0000000000000000000{thread_id:0>4}'
    db.execute('INSERT INTO fics(id, title, source_type, source_url, site, thread_id, created_at, updated_at)'
               ' VALUES (?,?,?,?,?,?,1,1)',
               (fic_id, f'Fic {thread_id}', source_type, f'https://{site}/threads/x.{thread_id}/',
                site, str(thread_id)))
    db.commit()
    return fic_id


def test_a_watched_scan_marks_every_thread_it_sees(client, fake_net):  # noqa: F811
    _put_cookie(client)
    client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert _lists() == {(t, 'watched') for t in ('12345', '67890', '11111', '22222')}
    assert get_db().execute('SELECT 1 FROM fic_list_passes WHERE site=? AND list=?',
                            (SITE, 'watched')).fetchone()


def test_backlog_is_the_forum_fics_on_no_list_once_a_pass_finished(client, fake_net):  # noqa: F811
    _add_fic(9001)
    assert _backlog(client) == set()  # no pass yet: unknown, not backlog
    _put_cookie(client)
    client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert _backlog(client) == {'9001'}
    fic = next(f for f in client.get('/api/fanfic').get_json() if f['sourceUrl'].endswith('.9001/'))
    assert fic['backlog'] is True and fic['sourceLists'] == []
    watched = next(f for f in client.get('/api/fanfic').get_json() if f['sourceUrl'].endswith('.12345/'))
    assert watched['backlog'] is False and watched['sourceLists'] == ['watched']


def test_a_forum_bookmark_does_not_keep_a_fic_out_of_the_backlog(client):
    db = get_db()
    for thread_id in ('1', '2', '3'):
        _add_fic(thread_id)
    _add_fic('4', source_type='ao3', site='archiveofourown.org')
    ids = {t: f'01J0000000000000000000{t:0>4}' for t in ('1', '2', '3', '4')}
    # 1: bookmarked with a "backlog" label; 2: bookmarked and watched;
    # 3: watched; 4: an AO3 bookmark, which is AO3's favourite.
    source_lists.mark(db, ids['1'], 'bookmarks')
    source_lists.mark(db, ids['2'], 'bookmarks')
    source_lists.mark(db, ids['2'], 'watched')
    source_lists.mark(db, ids['3'], 'watched')
    source_lists.mark(db, ids['4'], 'bookmarks')
    db.execute('INSERT INTO fic_list_passes(site, list, completed_at) VALUES (?,?,1)', (SITE, 'bookmarks'))
    db.execute("INSERT INTO fic_list_passes(site, list, completed_at) VALUES ('archiveofourown.org','bookmarks',1)")
    db.commit()
    # Only the forum's bookmarks have been walked: nothing is known about who
    # is watched there yet.
    assert _backlog(client) == set()
    db.execute('INSERT INTO fic_list_passes(site, list, completed_at) VALUES (?,?,1)', (SITE, 'watched'))
    db.commit()
    assert _backlog(client) == {'1'}


def test_local_uploads_are_never_backlog(client, fake_net):  # noqa: F811
    db = get_db()
    db.execute("INSERT INTO fics(id, title, source_type, site, created_at, updated_at)"
               " VALUES ('01J00000000000000000000099', 'Book', 'epub', ?, 1, 1)", (SITE,))
    db.execute('INSERT INTO fic_list_passes(site, list, completed_at) VALUES (?,?,1)', (SITE, 'watched'))
    db.commit()
    assert _backlog(client) == set()


def test_backlog_lists_newest_added_first_unless_a_sort_is_asked_for(client):
    db = get_db()
    # Chapters post in the opposite order, so activity and added disagree.
    for thread_id, added, posted in (('1', 100, 900), ('2', 300, 100), ('3', 200, 500)):
        _add_fic(thread_id)
        fic_id = f'01J0000000000000000000{thread_id:0>4}'
        db.execute('UPDATE fics SET created_at=? WHERE id=?', (added, fic_id))
        db.execute('INSERT INTO fic_chapters(id, fic_id, position, title, content_html, content_text, posted_at, created_at)'
                   ' VALUES (?,?,0,?,?,?,?,?)', (f'{fic_id}c', fic_id, 'Ch', '<p>text</p>', 'text', posted, added))
    db.execute('INSERT INTO fic_list_passes(site, list, completed_at) VALUES (?,?,1)', (SITE, 'watched'))
    db.commit()

    def order(query=''):
        return [f['title'] for f in client.get(f'/api/fanfic?folderId=backlog{query}').get_json()]

    assert order() == ['Fic 2', 'Fic 3', 'Fic 1']
    assert order('&sort=recent') != order()
    # The default everywhere else is still the sites' latest activity.
    assert [f['title'] for f in client.get('/api/fanfic').get_json()] == ['Fic 1', 'Fic 3', 'Fic 2']
    assert [f['title'] for f in client.get('/api/fanfic?sort=added').get_json()] == ['Fic 2', 'Fic 3', 'Fic 1']


def test_a_completed_pass_drops_a_thread_no_longer_watched(client, fake_net):  # noqa: F811
    _put_cookie(client)
    gone = _add_fic(9002)
    db = get_db()
    source_lists.mark(db, gone, 'watched', now=1)
    db.commit()
    client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert ('9002', 'watched') not in _lists()
    assert '9002' in _backlog(client)


def test_a_failed_pass_takes_nothing_off_the_list(client, fake_net, monkeypatch):  # noqa: F811
    _put_cookie(client)
    gone = _add_fic(9003)
    db = get_db()
    source_lists.mark(db, gone, 'watched', now=1)
    db.commit()
    real = download._fetch

    def flaky(url):
        if url == WATCHED_P2:
            raise requests.ConnectionError('reset')
        return real(url)
    monkeypatch.setattr(download, '_fetch', flaky)
    client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert ('9003', 'watched') in _lists()
    assert not db.execute('SELECT 1 FROM fic_list_passes').fetchone()


def test_a_resumed_pass_keeps_the_start_it_began_with(client, fake_net, monkeypatch):  # noqa: F811
    _put_cookie(client)
    gone = _add_fic(9004)
    db = get_db()
    real = download._fetch
    fail = {'p2': True}

    def flaky(url):
        if url == WATCHED_P2 and fail['p2']:
            raise requests.ConnectionError('reset')
        return real(url)
    monkeypatch.setattr(download, '_fetch', flaky)
    client.post(f'/api/fanfic/scan-watched/{SITE}')
    started = db.execute('SELECT pass_started_at FROM fanfic_watched_scans WHERE domain=?',
                         (SITE,)).fetchone()[0]
    # Seen on page 1 of *this* pass (after it began): must survive the prune.
    source_lists.mark(db, gone, 'watched', now=started + 1)
    db.commit()
    fail['p2'] = False
    client.post(f'/api/fanfic/scan-watched/{SITE}')
    assert ('9004', 'watched') in _lists()
    assert db.execute('SELECT 1 FROM fic_list_passes').fetchone()


def test_collection_scans_mark_their_collection():
    from backend.fanfic import collections, sites
    db = get_db()
    ref = sites.WorkRef('fanfiction.net', 'fanfiction', '77', 'https://www.fanfiction.net/s/77/1/')
    fic_id, _ = collections.queue_work(ref, listed_on='follows')
    assert db.execute('SELECT list FROM fic_source_lists WHERE fic_id=?', (fic_id,)).fetchone()[0] == 'follows'
    source_lists.complete_pass(db, 'fanfiction.net', 'follows', started_at=None)
    assert not db.execute('SELECT 1 FROM fic_list_passes').fetchone()  # no start: no claim
