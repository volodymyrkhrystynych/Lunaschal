"""Any non-YouTube link on a journal entry, printed to a PDF by the browser.

The browser is the extension's archive tab (extension/archive.html); here it is
played by the test client calling connect/poll/result directly. What is worth
pinning down: the row is the queue and survives a restart, a lease that runs
out lets the page be taken again, a login wall keeps no file, a redirect onto
the LAN is refused even after the browser followed it, and an entry waits for
its page before it is titled.
"""
import io
import json
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db, init_db
from backend.journal import storage, web_archive, youtube_import
from backend.routes import journal as journal_routes
from backend.research.web import UnsafeUrl

URL = 'https://example.com/articles/how-bridges-stand'
CLIENT = 'archive-tab-0123456789abcdef'
OTHER = 'archive-tab-fedcba9876543210'


def _pdf(text='Bridges stand because of arches.') -> bytes:
    """A real one-page PDF with real text, so pypdf has something to read."""
    from weasyprint import HTML

    return HTML(string=f'<p>{text}</p>').write_pdf()


JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _no_entry_background_work(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _public_unless_marked(monkeypatch):
    """No DNS in tests: anything on `.lan` is private, everything else public."""
    def check(url):
        if '.lan' in url:
            raise UnsafeUrl('That address is not reachable from here.')
        return url
    monkeypatch.setattr(web_archive, 'assert_public_url', check)
    web_archive._reset_for_tests()
    yield
    web_archive._reset_for_tests()


@pytest.fixture(autouse=True)
def _no_download_thread(monkeypatch):
    monkeypatch.setattr(youtube_import, 'start_import_bg', lambda *a, **k: None)


@pytest.fixture
def entry_id(client):
    return client.post(
        '/api/journal', json={'content': 'Read this over lunch.'}
    ).get_json()['id']


def _attach(client, entry_id, url=URL, attachment_id=None):
    body = {'url': url}
    if attachment_id is not None:
        body['attachmentId'] = attachment_id
    return client.post(f'/api/journal/{entry_id}/attachments/link', json=body)


def _row(attachment_id):
    return get_db().execute(
        'SELECT * FROM journal_attachments WHERE id=?', (attachment_id,)
    ).fetchone()


def _poll(client, client_id=CLIENT):
    return client.post('/api/journal/web-archive/poll', json={'clientId': client_id})


def _result(client, attachment_id, *, client_id=CLIENT, outcome='ok', pdf=None,
            poster=None, **meta):
    data = {'meta': json.dumps({'clientId': client_id, 'outcome': outcome, **meta})}
    if pdf is not None:
        data['pdf'] = (io.BytesIO(pdf), 'page.pdf')
    if poster is not None:
        data['poster'] = (io.BytesIO(poster), 'poster.jpg')
    return client.post(
        f'/api/journal/web-archive/{attachment_id}/result',
        data=data, content_type='multipart/form-data',
    )


# --- attaching ---------------------------------------------------------------

def test_a_non_youtube_link_becomes_a_queued_page(client, entry_id):
    res = _attach(client, entry_id)
    assert res.status_code == 201
    a = res.get_json()
    assert a['kind'] == 'webpage'
    assert a['importStatus'] == 'queued'
    assert a['sourceUrl'] == URL
    assert 'url' not in a  # nothing to serve until the browser prints it


def test_a_youtube_link_still_takes_the_video_path(client, entry_id):
    a = _attach(client, entry_id, 'https://youtu.be/aircAruvnKk').get_json()
    assert a['kind'] == 'youtube'
    assert a['importStatus'] == 'importing'


@pytest.mark.parametrize('url', [
    'ftp://example.com/file',
    'not a link',
    'https://user:pw@example.com/',
    'http://router.lan/admin',
])
def test_links_the_browser_must_not_be_sent_to_are_refused(client, entry_id, url):
    res = _attach(client, entry_id, url)
    assert res.status_code == 400
    assert get_db().execute(
        "SELECT COUNT(*) FROM journal_attachments WHERE kind='webpage'"
    ).fetchone()[0] == 0


def test_replaying_the_same_link_is_a_no_op(client, entry_id):
    attachment_id = str(ULID())
    _attach(client, entry_id, attachment_id=attachment_id)
    again = _attach(client, entry_id, attachment_id=attachment_id)
    assert again.status_code == 201
    assert get_db().execute(
        'SELECT COUNT(*) FROM journal_attachments WHERE entry_id=?', (entry_id,)
    ).fetchone()[0] == 1


def test_a_page_cannot_be_sent_to_speech_transcription(client, entry_id):
    a = _attach(client, entry_id).get_json()
    assert client.post(f'/api/journal/attachments/{a["id"]}/transcribe').status_code == 400


# --- the queue ---------------------------------------------------------------

def test_poll_leases_a_page_to_one_browser_at_a_time(client, entry_id):
    a = _attach(client, entry_id).get_json()
    job = _poll(client).get_json()['job']
    assert job == {'id': a['id'], 'url': URL}
    assert _poll(client, OTHER).get_json()['job'] is None
    # The holder gets its own page back — a reloaded control tab resumes it.
    assert _poll(client).get_json()['job']['id'] == a['id']


def test_an_expired_lease_lets_another_browser_take_the_page(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    get_db().execute(
        'UPDATE journal_attachments SET archive_lease_until=? WHERE id=?',
        (int(time.time()) - 1, a['id']),
    )
    get_db().commit()
    assert _poll(client, OTHER).get_json()['job']['id'] == a['id']
    # …and the first browser's late result is refused rather than clobbering.
    assert _result(client, a['id'], pdf=_pdf()).status_code == 409


def test_disconnect_hands_the_page_back_at_once(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    client.post('/api/journal/web-archive/disconnect', json={'clientId': CLIENT})
    assert _poll(client, OTHER).get_json()['job']['id'] == a['id']


def test_a_bad_client_id_is_refused(client):
    assert _poll(client, 'short').status_code == 400


def test_the_card_can_tell_whether_any_browser_is_coming(client, entry_id):
    a = _attach(client, entry_id).get_json()
    status = client.get(f'/api/journal/attachments/{a["id"]}/import-status').get_json()
    assert status == {'done': False, 'phase': 'queued', 'archiverOnline': False}
    client.post('/api/journal/web-archive/connect', json={'clientId': CLIENT})
    status = client.get(f'/api/journal/attachments/{a["id"]}/import-status').get_json()
    assert status['archiverOnline'] is True


def test_a_queued_page_survives_a_restart(client, entry_id):
    """The startup reset fails an interrupted YouTube download; a page waiting
    for the browser was never running and must not be touched by it."""
    a = _attach(client, entry_id).get_json()
    init_db()
    assert _row(a['id'])['import_status'] == 'queued'


# --- results -----------------------------------------------------------------

def test_a_printed_page_is_stored_with_its_text_and_poster(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    res = _result(client, a['id'], pdf=_pdf(), poster=JPEG,
                  title='How bridges stand', finalUrl=URL)
    assert res.status_code == 200
    body = res.get_json()
    assert body['importStatus'] == 'ready'
    assert body['name'] == 'How bridges stand'
    assert body['mime'] == 'application/pdf'
    assert body['thumbnailUrl']
    row = _row(a['id'])
    assert 'arches' in row['transcript']
    assert row['transcript_status'] == 'done'
    assert row['archive_client'] is None
    assert storage.resolve_stored_path(row['path']).read_bytes().startswith(b'%PDF-')

    served = client.get(body['url'])
    assert served.status_code == 200
    assert served.mimetype == 'application/pdf'


def test_a_login_wall_keeps_the_link_and_no_file(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    body = _result(client, a['id'], outcome='login').get_json()
    assert body['importStatus'] == 'login_required'
    assert 'login' in body['importError']
    assert 'url' not in body
    assert body['sourceUrl'] == URL
    assert _row(a['id'])['path'] == ''


def test_a_redirect_onto_the_lan_is_refused_after_the_fact(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    body = _result(client, a['id'], pdf=_pdf(),
                   finalUrl='http://qbittorrent.lan:8081/').get_json()
    assert body['importStatus'] == 'error'
    assert 'private' in body['importError']
    assert _row(a['id'])['path'] == ''


def test_something_that_is_not_a_pdf_is_refused(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    assert _result(client, a['id'], pdf=b'<html>nope</html>').status_code == 400
    assert _row(a['id'])['import_status'] == 'queued'


def test_a_poster_that_is_not_a_jpeg_is_dropped_not_served(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    body = _result(client, a['id'], pdf=_pdf(), poster=b'GIF89a').get_json()
    assert body['importStatus'] == 'ready'
    assert 'thumbnailUrl' not in body


def test_a_browser_error_is_recorded(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    body = _result(client, a['id'], outcome='error', message='Tab crashed').get_json()
    assert body['importStatus'] == 'error'
    assert body['importError'] == 'Tab crashed'


def test_a_result_for_a_page_never_leased_is_refused(client, entry_id):
    a = _attach(client, entry_id).get_json()
    assert _result(client, a['id'], pdf=_pdf()).status_code == 409


def test_deleting_a_page_removes_its_files(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    _result(client, a['id'], pdf=_pdf(), poster=JPEG)
    directory = storage.resolve_stored_path(_row(a['id'])['path']).parent
    assert client.delete(f'/api/journal/attachments/{a["id"]}').status_code == 200
    assert not directory.exists()


# --- retry -------------------------------------------------------------------

def test_retry_puts_a_login_walled_page_back_in_the_queue(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    _result(client, a['id'], outcome='login')
    res = client.post(f'/api/journal/attachments/{a["id"]}/retry')
    assert res.status_code == 200
    assert res.get_json()['importStatus'] == 'queued'
    assert _poll(client, OTHER).get_json()['job']['id'] == a['id']


def test_retry_of_a_page_still_queued_is_a_conflict(client, entry_id):
    a = _attach(client, entry_id).get_json()
    assert client.post(f'/api/journal/attachments/{a["id"]}/retry').status_code == 409


def test_retry_restarts_a_failed_video_download(client, entry_id, monkeypatch):
    started = []
    monkeypatch.setattr(youtube_import, 'start_import_bg',
                        lambda *args: started.append(args))
    url = 'https://www.youtube.com/watch?v=aircAruvnKk'
    a = _attach(client, entry_id, url).get_json()
    started.clear()
    get_db().execute(
        "UPDATE journal_attachments SET import_status='error', import_error='boom'"
        ' WHERE id=?', (a['id'],),
    )
    get_db().commit()
    res = client.post(f'/api/journal/attachments/{a["id"]}/retry')
    assert res.status_code == 200
    assert res.get_json()['importStatus'] == 'importing'
    assert started == [(a['id'], entry_id, url)]


def test_retry_of_an_upload_is_refused(client, entry_id):
    db = get_db()
    attachment_id = str(ULID())
    db.execute(
        "INSERT INTO journal_attachments(id, entry_id, kind, name, path, created_at)"
        " VALUES (?,?,'image','x','',0)", (attachment_id, entry_id),
    )
    db.commit()
    assert client.post(f'/api/journal/attachments/{attachment_id}/retry').status_code == 400


# --- the entry's title -------------------------------------------------------

def test_the_title_waits_for_a_queued_page(client, entry_id):
    a = _attach(client, entry_id).get_json()
    assert journal_routes._attachments_settled(entry_id, 1) is False
    assert journal_routes._entry_has_video(entry_id) is True  # the long cap
    _poll(client)
    _result(client, a['id'], pdf=_pdf(), title='How bridges stand')
    assert journal_routes._attachments_settled(entry_id, 1) is True


def test_the_title_is_built_from_what_the_page_says(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    _result(client, a['id'], pdf=_pdf(), title='How bridges stand')
    context = journal_routes._metadata_context(entry_id)
    assert 'Web page "How bridges stand"' in context
    assert 'arches' in context


def test_a_login_walled_page_does_not_hold_the_title(client, entry_id):
    a = _attach(client, entry_id).get_json()
    _poll(client)
    _result(client, a['id'], outcome='login')
    assert journal_routes._attachments_settled(entry_id, 1) is True
