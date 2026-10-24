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


def test_local_uploads_are_never_backlog(client, fake_net):  # noqa: F811
    db = get_db()
    db.execute("INSERT INTO fics(id, title, source_type, site, created_at, updated_at)"
               " VALUES ('01J00000000000000000000099', 'Book', 'epub', ?, 1, 1)", (SITE,))
    db.execute('INSERT INTO fic_list_passes(site, list, completed_at) VALUES (?,?,1)', (SITE, 'watched'))
    db.commit()
    assert _backlog(client) == set()


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
