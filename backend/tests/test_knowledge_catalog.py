"""The Kiwix catalogue and the resumable downloader.

Every parsing test runs against a checked-in fixture captured from the live
service (`backend/tests/fixtures/kiwix/`), and no test here touches the
network: the catalogue is somebody else's uptime, and a suite that depends on
it fails on a train.

The transfer tests drive `download._fetch` through a fake `requests` whose
whole job is to misbehave in the specific ways a real mirror does -- dropping
the connection mid-file, ignoring a Range header, and serving a build that no
longer matches the bytes already on disk.
"""
import hashlib
import json
from pathlib import Path

import pytest

from backend.db.connection import get_db
from backend.offline_knowledge import catalog, download

FIXTURES = Path(__file__).parent / 'fixtures' / 'kiwix'


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def app(isolated_db):
    """A Flask app over the isolated DB, for the half of this module that
    drives the downloader directly rather than through a request."""
    from backend.db import connection
    if connection._conn is not None:
        try:
            connection._conn.close()
        except Exception:
            pass
        connection._conn = None
    from backend.app import create_app
    created = create_app()
    created.config.update(TESTING=True)
    return created


# --- catalogue parsing ---

def test_an_entry_feed_yields_entries_and_the_full_total():
    entries, total = catalog.parse_entries(fixture('entries_devdocs.xml'))
    # The page holds three; the catalogue holds 231. A UI that showed "3
    # results" for a filter matching 231 would be lying about the library.
    assert len(entries) == 3
    assert total == 231


def test_the_acquisition_link_is_the_meta4_not_the_zim():
    # Downloading the href as if it were the archive fetches a 2 KB XML file
    # named `.zim`, which is the mistake this assertion exists to prevent.
    entry = catalog.parse_entries(fixture('entries_devdocs.xml'))[0][0]
    assert entry['meta4Url'].endswith('.zim.meta4')
    assert entry['name'] == 'devdocs_en_sinon'


def test_a_devdocs_entry_is_labelled_docs_and_carries_the_catalogues_ftindex_claim():
    entry = catalog.parse_entries(fixture('entries_devdocs.xml'))[0][0]
    assert entry['kind'] == 'docs'
    # Surfaced so the user sees it before spending the download — but it is
    # the *catalogue's* claim and it is not reliable: this very archive
    # (devdocs_en_sinon_2026-08.zim) reports has_fulltext_index == True from
    # libzim and carries no `_ftindex` tag of its own. Only registry._probe,
    # reading the file on disk, settles it.
    assert entry['ftindex'] is False


def test_a_stack_exchange_entry_is_labelled_qa_and_may_have_a_fulltext_index():
    entries, total = catalog.parse_entries(fixture('entries_stackexchange.xml'))
    assert total == 181
    assert {e['kind'] for e in entries} == {'qa'}
    assert any(e['ftindex'] for e in entries)
    assert any(not e['ftindex'] for e in entries)


def test_a_lone_entry_document_parses_as_one_entry():
    # `/catalog/v2/entry/<uuid>` serves this shape. It is also not well-formed
    # (an undeclared `dc:` prefix), which is why fetch_entry does not call it —
    # but the parser handles the shape for the day that is fixed.
    xml = fixture('entry_sinon.xml').replace(
        '<entry>', '<entry xmlns:dc="http://purl.org/dc/terms/">', 1)
    entries, total = catalog.parse_entries(xml)
    assert total == 1
    assert entries[0]['name'] == 'devdocs_en_sinon'


def test_the_real_single_entry_endpoint_is_not_parseable():
    # Documenting the upstream bug rather than working around it silently: if
    # Kiwix fixes this, this test fails and fetch_entry can be simplified.
    with pytest.raises(catalog.CatalogUnavailable):
        catalog.parse_entries(fixture('entry_sinon.xml'))


def test_an_html_error_page_is_an_outage_not_an_empty_library():
    # `<html>503</html>` is perfectly well-formed XML, so without a check on
    # the root element a proxy's error page reads as a catalogue with nothing
    # in it — and the UI says the library is empty rather than unreachable.
    with pytest.raises(catalog.CatalogUnavailable):
        catalog.parse_entries('<html><body>503 Service Unavailable</body></html>')


def test_a_truncated_response_is_a_catalogue_error_not_a_crash():
    with pytest.raises(catalog.CatalogUnavailable):
        catalog.parse_entries('<feed><entry><title>half')


# --- metalink parsing ---

def test_meta4_gives_the_size_hashes_pieces_and_ranked_mirrors():
    meta = catalog.parse_meta4(fixture('sinon.meta4.xml'))
    assert meta['filename'] == 'devdocs_en_sinon_2026-08.zim'
    assert meta['sha256'] == (
        '2689ed4abaaeaf766bf9541596e5d59ea8065d0c0e0e0dcaf48e86e4d2087769')
    assert meta['md5'] == '494d9057e33ef0fdfc3cf108605b0e08'
    assert meta['pieceLength'] == 4194304
    assert len(meta['pieces']) == 1
    # priority="1" first, whatever order the document listed them in.
    assert meta['mirrors'][0].startswith('https://mirror-sites-ca.mblibrary.info/')


def test_the_meta4_size_wins_over_the_opds_length():
    # They genuinely disagree — 361379 against 361472 — and the mirrors serve
    # the meta4's number. Reserving disk against the wrong one is how a
    # download fails at 99%.
    entry = catalog.parse_entries(fixture('entry_sinon.xml').replace(
        '<entry>', '<entry xmlns:dc="http://purl.org/dc/terms/">', 1))[0][0]
    meta = catalog.parse_meta4(fixture('sinon.meta4.xml'))
    assert entry['approxSize'] == 361472
    assert meta['size'] == 361379


def test_a_piece_list_in_an_unknown_hash_is_ignored_rather_than_misread():
    xml = fixture('sinon.meta4.xml').replace(
        '<pieces length="4194304" type="sha-1">',
        '<pieces length="4194304" type="blake3">')
    meta = catalog.parse_meta4(xml)
    assert meta['pieceLength'] is None
    assert meta['pieces'] == []
    # The whole-file hash still gates the download; only the mid-flight
    # checkpoint is lost.
    assert meta['sha256']


# --- navigation feeds ---

def test_the_language_feed_carries_codes_and_counts_and_categories_do_not():
    languages = catalog.parse_navigation(fixture('languages.xml'))
    english = next(x for x in languages if x['code'] == 'eng')
    assert english['label'] == 'English'
    assert english['count'] == 1301

    categories = catalog.parse_navigation(fixture('categories.xml'))
    labels = [c['label'] for c in categories]
    assert 'stack_exchange' in labels
    # There is no devdocs *category* — DevDocs is reachable only as a tag, and
    # a picker offering it here would return nothing.
    assert 'devdocs' not in labels
    assert all(c['count'] is None for c in categories)


def test_fetch_entries_forwards_only_known_filters(monkeypatch):
    seen = {}

    def fake_get(url, params=None):
        seen.update(params or {})
        return fixture('entries_devdocs.xml')

    monkeypatch.setattr(catalog, '_get', fake_get)
    catalog.fetch_entries(q='Stack Overflow', lang='eng', evil='../../etc/passwd',
                          count=5000)
    assert seen['q'] == 'Stack Overflow'
    assert seen['lang'] == 'eng'
    assert 'evil' not in seen
    # Clamped, not honoured: this is a proxy onto somebody else's service.
    assert seen['count'] == catalog.MAX_COUNT


def test_fetch_entry_picks_the_matching_uuid_when_a_slug_is_ambiguous(monkeypatch):
    # `name=wikipedia_en_all` matches three flavours upstream. Returning the
    # first would silently download maxi when the user picked nopic.
    entries = catalog.parse_entries(fixture('entries_devdocs.xml'))[0]
    monkeypatch.setattr(catalog, 'fetch_entries', lambda **kw: (entries, len(entries)))
    wanted = entries[1]
    assert catalog.fetch_entry('whatever', wanted['uuid'])['name'] == wanted['name']
    assert catalog.fetch_entry('whatever', '00000000-0000-0000-0000-000000000000') is None
    # No uuid and more than one candidate: refuse rather than guess.
    assert catalog.fetch_entry('whatever') is None


# --- the destination ---

def _use_root(monkeypatch, path):
    monkeypatch.setattr(
        'backend.offline_knowledge.archive.configured_root', lambda: path)


def test_an_unconfigured_root_refuses_before_anything_is_fetched(monkeypatch):
    _use_root(monkeypatch, None)
    assert download.root_state()['state'] == 'unset'
    with pytest.raises(download.DownloadRefused, match='Settings'):
        download._require_root()


def test_a_missing_folder_reads_as_an_unplugged_drive(monkeypatch, tmp_path):
    _use_root(monkeypatch, tmp_path / 'nope')
    state = download.root_state()
    assert state['state'] == 'missing'
    assert 'unplugged' in state['reason']


def test_a_read_only_mount_and_a_permissions_problem_are_different_states(
        monkeypatch, tmp_path):
    # os.access answers False for both, and their fixes are unrelated — fsck
    # versus uid=/gid= in fstab, which is what exFAT needs.
    _use_root(monkeypatch, tmp_path)
    monkeypatch.setattr(download, '_is_readonly_mount', lambda p: True)
    assert download.root_state()['state'] == 'readonly'

    monkeypatch.setattr(download, '_is_readonly_mount', lambda p: False)
    monkeypatch.setattr(download.os, 'access', lambda p, mode: False)
    state = download.root_state()
    assert state['state'] == 'permissions'
    assert 'uid=' in state['reason']


def test_a_writable_root_has_no_complaint(monkeypatch, tmp_path):
    _use_root(monkeypatch, tmp_path)
    assert download.root_state() == {
        'root': str(tmp_path), 'state': 'writable', 'reason': None}


# --- piece verification ---

def _pieces(data: bytes, piece_length: int) -> list[str]:
    return [hashlib.sha1(data[i:i + piece_length]).hexdigest()
            for i in range(0, len(data), piece_length)]


def test_a_good_part_resumes_from_its_last_complete_piece(tmp_path):
    body = bytes(range(256)) * 40  # 10240 bytes
    piece = 4096
    part = tmp_path / 'a.zim.part'
    part.write_bytes(body[:9000])  # two whole pieces and a fragment
    # The fragment is discarded: a partial final piece has no hash to check
    # against, so trusting it is exactly the guess this function refuses.
    assert download._verified_prefix(part, piece, _pieces(body, piece)) == 8192


def test_a_part_from_a_rotated_build_is_truncated_back_to_the_good_prefix(tmp_path):
    body = bytes(range(256)) * 40
    piece = 4096
    stale = body[:4096] + b'\xff' * 4096 + body[8192:]
    part = tmp_path / 'a.zim.part'
    part.write_bytes(stale)
    # Without this, appending to yesterday's bytes only fails at the final
    # whole-file hash — after the entire archive has been fetched again.
    assert download._verified_prefix(part, piece, _pieces(body, piece)) == 4096


def test_with_no_piece_list_the_whole_part_is_resumed_optimistically(tmp_path):
    part = tmp_path / 'a.zim.part'
    part.write_bytes(b'x' * 1234)
    assert download._verified_prefix(part, 0, []) == 1234


# --- the transfer ---

class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, fail_after: int | None = None):
        self.body, self.status_code, self._fail_after = body, status, fail_after
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise download.requests.HTTPError(f'{self.status_code}')

    def iter_content(self, size):
        sent = 0
        for i in range(0, len(self.body), size):
            chunk = self.body[i:i + size]
            if self._fail_after is not None and sent >= self._fail_after:
                raise download.requests.ConnectionError('mirror hung up')
            sent += len(chunk)
            yield chunk

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def library(monkeypatch, tmp_path, app):
    """A writable archive root, a queued row, and a recording fake mirror."""
    _use_root(monkeypatch, tmp_path)
    # No real drain thread: these tests call run_one directly, and a daemon
    # thread holding the module-global connection past teardown segfaults the
    # interpreter rather than raising — see conftest's note on the same hazard.
    monkeypatch.setattr(download, 'ensure_worker', lambda: None)
    monkeypatch.setattr(download, 'CHUNK_BYTES', 1024)
    monkeypatch.setattr(download, 'CHECKPOINT_BYTES', 2048)

    body = bytes(range(256)) * 40
    piece = 4096
    meta = {
        'filename': 'devdocs_en_test_2026-08.zim',
        'size': len(body),
        'sha256': hashlib.sha256(body).hexdigest(),
        'md5': hashlib.md5(body).hexdigest(),
        'pieceLength': piece,
        'pieces': _pieces(body, piece),
        'mirrors': ['https://mirror.example.org/a.zim'],
    }
    monkeypatch.setattr(download.catalog, 'fetch_meta4', lambda url: meta)
    monkeypatch.setattr(download, 'assert_public_url', lambda url: url)

    with app.app_context():
        entry = {'name': 'devdocs_en_test', 'title': 'Test Docs', 'uuid': 'u',
                 'meta4Url': 'https://download.example.org/a.meta4'}
        row = download.queue(entry, meta)
    requests_seen: list[dict] = []
    return {'root': tmp_path, 'body': body, 'meta': meta, 'row': row,
            'requests': requests_seen, 'app': app}


def _install_mirror(monkeypatch, library, responder):
    def fake_get(url, headers=None, **kw):
        library['requests'].append({'url': url, 'headers': dict(headers or {})})
        return responder(len(library['requests']), headers or {})

    monkeypatch.setattr(download.requests, 'get', fake_get)


def test_a_clean_download_lands_verified_and_appears_in_the_library(
        monkeypatch, library):
    _install_mirror(monkeypatch, library,
                    lambda n, h: FakeResponse(library['body']))
    synced = []
    monkeypatch.setattr(download.registry, 'sync', lambda **kw: synced.append(kw))

    with library['app'].app_context():
        download.run_one(library['row']['id'])
        row = download.row(library['row']['id'])

    dest = library['root'] / 'devdocs_en_test_2026-08.zim'
    assert row['status'] == 'done'
    assert dest.read_bytes() == library['body']
    assert not dest.with_suffix('.zim.part').exists()
    # A finished download rescans, so the archive shows up without the user
    # pressing Rescan.
    assert synced


def test_a_dropped_connection_resumes_with_a_range_header(monkeypatch, library):
    body = library['body']

    def responder(attempt, headers):
        if 'Range' not in headers:
            # Dies after five chunks: one whole 4096-byte piece plus a
            # fragment, so there is something to resume from.
            return FakeResponse(body, fail_after=5120)
        start = int(headers['Range'].removeprefix('bytes=').rstrip('-'))
        return FakeResponse(body[start:], status=206)

    _install_mirror(monkeypatch, library, responder)
    monkeypatch.setattr(download.registry, 'sync', lambda **kw: None)

    with library['app'].app_context():
        # First pass dies mid-file and is recorded as an error the user can
        # resume from; the retry asks only for the bytes it is missing.
        download.run_one(library['row']['id'])
        assert download.row(library['row']['id'])['status'] == 'error'
        download.resume(library['row']['id'])
        download.run_one(library['row']['id'])
        row = download.row(library['row']['id'])

    assert row['status'] == 'done'
    assert (library['root'] / 'devdocs_en_test_2026-08.zim').read_bytes() == body
    ranges = [r['headers'].get('Range') for r in library['requests']]
    assert ranges[0] is None
    # Resumed at the last *whole* piece, not at the 5120 bytes on disk: the
    # trailing fragment has no hash to check against, so it is not trusted.
    assert ranges[-1] == 'bytes=4096-'


def test_a_server_that_ignores_range_starts_over_instead_of_appending(
        monkeypatch, library):
    body = library['body']
    part = library['root'] / 'devdocs_en_test_2026-08.zim.part'
    part.write_bytes(body[:4096])
    with library['app'].app_context():
        get_db().execute(
            'UPDATE knowledge_downloads SET downloaded_bytes=4096 WHERE id=?',
            (library['row']['id'],))
        get_db().commit()

    # 200 to a Range request means the whole file is coming. Appending it to
    # what we already have writes a second copy into the middle of the first,
    # and nothing notices until the checksum fails.
    def responder(attempt, headers):
        return FakeResponse(body, status=200)

    _install_mirror(monkeypatch, library, responder)
    monkeypatch.setattr(download.registry, 'sync', lambda **kw: None)

    with library['app'].app_context():
        download.run_one(library['row']['id'])
        row = download.row(library['row']['id'])

    assert row['status'] == 'done'
    assert (library['root'] / 'devdocs_en_test_2026-08.zim').read_bytes() == body


def test_a_checksum_mismatch_keeps_the_part_and_publishes_nothing(
        monkeypatch, library):
    _install_mirror(monkeypatch, library,
                    lambda n, h: FakeResponse(b'\x00' * len(library['body'])))
    synced = []
    monkeypatch.setattr(download.registry, 'sync', lambda **kw: synced.append(kw))

    with library['app'].app_context():
        download.run_one(library['row']['id'])
        row = download.row(library['row']['id'])

    assert row['status'] == 'error'
    assert 'checksum' in row['error']
    # Kept as a `.part`, never renamed: the same bytes under a real .zim name
    # are a corrupt archive the reader would try to open.
    assert (library['root'] / 'devdocs_en_test_2026-08.zim.part').exists()
    assert not (library['root'] / 'devdocs_en_test_2026-08.zim').exists()
    assert not synced


def test_too_little_free_space_is_refused_before_a_byte_is_fetched(
        monkeypatch, library):
    import collections
    usage = collections.namedtuple('usage', 'total used free')
    monkeypatch.setattr(download.shutil, 'disk_usage', lambda p: usage(1, 1, 10))
    _install_mirror(monkeypatch, library, lambda n, h: pytest.fail('fetched anyway'))

    with library['app'].app_context():
        download.run_one(library['row']['id'])
        row = download.row(library['row']['id'])

    assert row['status'] == 'error'
    assert 'free space' in row['error']
    assert not library['requests']


def test_a_non_public_mirror_is_refused(monkeypatch, library):
    from backend.research.web import UnsafeUrl

    def refuse(url):
        raise UnsafeUrl('Refusing to fetch a local address: localhost')

    monkeypatch.setattr(download, 'assert_public_url', refuse)
    _install_mirror(monkeypatch, library, lambda n, h: pytest.fail('fetched anyway'))

    with library['app'].app_context():
        download.run_one(library['row']['id'])
        row = download.row(library['row']['id'])

    assert row['status'] == 'error'
    assert not library['requests']


def test_queueing_the_same_archive_twice_is_refused(monkeypatch, library):
    with library['app'].app_context():
        with pytest.raises(download.DownloadRefused, match='already queued'):
            download.queue({'name': 'devdocs_en_test'}, library['meta'])


def test_an_archive_already_on_the_drive_is_not_downloaded_again(
        monkeypatch, library):
    (library['root'] / 'other.zim').write_bytes(b'x')
    meta = {**library['meta'], 'filename': 'other.zim'}
    with library['app'].app_context():
        with pytest.raises(download.DownloadRefused, match='already in the archive'):
            download.queue({'name': 'other'}, meta)


def test_a_filename_that_escapes_the_archive_folder_is_refused(monkeypatch, library):
    meta = {**library['meta'], 'filename': '../../etc/passwd.zim'}
    with library['app'].app_context():
        with pytest.raises(download.DownloadRefused, match='implausible'):
            download.queue({'name': 'evil'}, meta)


def test_deleting_a_download_removes_its_part_file(monkeypatch, library):
    part = library['root'] / 'devdocs_en_test_2026-08.zim.part'
    part.write_bytes(b'half')
    with library['app'].app_context():
        assert download.delete(library['row']['id'])
        assert download.row(library['row']['id']) is None
    assert not part.exists()


# --- the restart reset ---

@pytest.mark.parametrize('status', ['queued', 'downloading', 'verifying'])
def test_a_restart_parks_an_interrupted_download_at_paused_with_its_bytes(
        monkeypatch, library, status):
    from backend.db import connection

    with library['app'].app_context():
        db = get_db()
        db.execute(
            "UPDATE knowledge_downloads SET status=?, "
            'downloaded_bytes=8192 WHERE id=?', (status, library['row']['id']))
        db.commit()
        connection._reset_stale_knowledge_downloads(db)
        row = download.row(library['row']['id'])

    # Paused, not error: the bytes on disk are the whole point of a resumable
    # transfer. And not restarted either — deciding on its own to pull the
    # remaining 90 GB is not a startup path's call.
    assert row['status'] == 'paused'
    assert row['downloaded_bytes'] == 8192
    assert 'Resume' in row['error']


# --- routes ---

def test_the_config_route_reports_why_the_root_cannot_be_written_to(
        client, monkeypatch, tmp_path):
    _use_root(monkeypatch, tmp_path)
    monkeypatch.setattr(download, '_is_readonly_mount', lambda p: True)
    body = client.get('/api/knowledge/config').get_json()
    assert body['exists'] is True
    assert body['writeState'] == 'readonly'
    assert 'read-only' in body['writeReason']


def test_the_catalog_route_forwards_filters_and_returns_the_total(
        client, monkeypatch):
    seen = {}

    def fake_fetch(**filters):
        seen.update(filters)
        return catalog.parse_entries(fixture('entries_devdocs.xml'))

    monkeypatch.setattr(catalog, 'fetch_entries', fake_fetch)
    body = client.get('/api/knowledge/catalog?tag=devdocs&lang=eng&nope=1').get_json()
    assert seen == {'tag': 'devdocs', 'lang': 'eng'}
    assert body['total'] == 231
    assert body['entries'][0]['name'] == 'devdocs_en_sinon'


def test_an_unreachable_catalogue_is_a_gateway_error_not_a_500(client, monkeypatch):
    def boom(**kw):
        raise catalog.CatalogUnavailable('Could not reach the Kiwix catalogue')

    monkeypatch.setattr(catalog, 'fetch_entries', boom)
    resp = client.get('/api/knowledge/catalog')
    assert resp.status_code == 502
    assert 'Kiwix' in resp.get_json()['error']


def test_queueing_through_the_route_re_resolves_the_entry_server_side(
        client, monkeypatch, tmp_path):
    _use_root(monkeypatch, tmp_path)
    monkeypatch.setattr(download, 'ensure_worker', lambda: None)
    entry = catalog.parse_entries(fixture('entries_devdocs.xml'))[0][0]
    meta = catalog.parse_meta4(fixture('sinon.meta4.xml'))
    resolved = {}

    def fake_entry(name, uuid=''):
        resolved['name'], resolved['uuid'] = name, uuid
        return entry

    monkeypatch.setattr(catalog, 'fetch_entry', fake_entry)
    monkeypatch.setattr(catalog, 'fetch_meta4', lambda url: meta)

    resp = client.post('/api/knowledge/downloads', json={
        'name': 'devdocs_en_sinon', 'uuid': entry['uuid'],
        # A client cannot choose where bytes come from: these are ignored.
        'meta4Url': 'http://127.0.0.1/evil.meta4', 'sha256': 'deadbeef',
    })
    assert resp.status_code == 201
    assert resolved == {'name': 'devdocs_en_sinon', 'uuid': entry['uuid']}
    body = resp.get_json()
    assert body['filename'] == 'devdocs_en_sinon_2026-08.zim'
    assert body['status'] == 'queued'
    assert body['totalBytes'] == 361379  # the meta4's size, not the OPDS length


def test_an_unwritable_root_refuses_the_queue_with_the_reason(
        client, monkeypatch, tmp_path):
    _use_root(monkeypatch, None)
    entry = catalog.parse_entries(fixture('entries_devdocs.xml'))[0][0]
    monkeypatch.setattr(catalog, 'fetch_entry', lambda name, uuid='': entry)
    monkeypatch.setattr(
        catalog, 'fetch_meta4',
        lambda url: catalog.parse_meta4(fixture('sinon.meta4.xml')))
    resp = client.post('/api/knowledge/downloads', json={'name': 'devdocs_en_sinon'})
    assert resp.status_code == 409
    assert 'Settings' in resp.get_json()['error']


def test_the_downloads_route_prefers_the_live_counter_over_the_checkpoint(
        client, monkeypatch, tmp_path, app):
    _use_root(monkeypatch, tmp_path)
    monkeypatch.setattr(download, 'ensure_worker', lambda: None)
    meta = catalog.parse_meta4(fixture('sinon.meta4.xml'))
    with app.app_context():
        row = download.queue({'name': 'x', 'title': 'X'}, meta)
    # The column is only written every 16 MiB, so a healthy download would
    # look stalled between checkpoints if the row were the only source.
    download._set_progress(row['id'], downloadedBytes=99, bytesPerSecond=12.5)
    try:
        body = client.get('/api/knowledge/downloads').get_json()
    finally:
        download._clear_progress(row['id'])
    assert body[0]['downloadedBytes'] == 99
    assert body[0]['bytesPerSecond'] == 12.5


def test_pause_resume_and_delete_answer_404_for_an_unknown_id(client):
    assert client.post('/api/knowledge/downloads/nope/pause').status_code == 404
    assert client.post('/api/knowledge/downloads/nope/resume').status_code == 404
    assert client.delete('/api/knowledge/downloads/nope').status_code == 404


def test_pieces_round_trip_through_the_row(monkeypatch, library):
    with library['app'].app_context():
        row = download.row(library['row']['id'])
    assert json.loads(row['pieces_sha1']) == library['meta']['pieces']
    assert row['piece_length'] == 4096


def test_an_archive_rebuilt_upstream_restarts_instead_of_splicing(
        monkeypatch, library):
    """Kiwix republishes under the same URL when it rebuilds an archive."""
    new_body = bytes(range(255, -1, -1)) * 40
    part = library['root'] / 'devdocs_en_test_2026-08.zim.part'
    part.write_bytes(library['body'][:4096])
    with library['app'].app_context():
        get_db().execute(
            'UPDATE knowledge_downloads SET downloaded_bytes=4096 WHERE id=?',
            (library['row']['id'],))
        get_db().commit()

    rebuilt = {
        **library['meta'],
        'size': len(new_body),
        'sha256': hashlib.sha256(new_body).hexdigest(),
        'pieces': _pieces(new_body, 4096),
    }
    monkeypatch.setattr(download.catalog, 'fetch_meta4', lambda url: rebuilt)
    _install_mirror(monkeypatch, library, lambda n, h: FakeResponse(new_body))
    monkeypatch.setattr(download.registry, 'sync', lambda **kw: None)

    with library['app'].app_context():
        download.run_one(library['row']['id'])
        row = download.row(library['row']['id'])

    # The old bytes are consistent with the build they came from, so piece
    # verification alone would have happily appended the new build to them and
    # only failed at the final hash — after refetching the whole archive.
    assert row['status'] == 'done'
    assert row['sha256'] == rebuilt['sha256']
    assert (library['root'] / 'devdocs_en_test_2026-08.zim').read_bytes() == new_body
    # Started over: no Range header was sent against the stale prefix.
    assert all(r['headers'].get('Range') is None for r in library['requests'])
