"""Study sources: uploads, the two URL imports, and the file route.

No real network and no real yt-dlp — `importer.fetch_public_page` and
`importer.run_ytdlp` are monkeypatched, and the route's `_start_*_bg` thread
launchers are swapped for the synchronous functions so a POST can be asserted
on directly (the backend/tests/test_fanfic_import.py pattern).
"""
import io
import json
import subprocess
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.research.web import UnsafeUrl
from backend.routes import study as study_routes
from backend.study import importer, storage, youtube
from backend import ytdlp


@pytest.fixture(autouse=True)
def study_root(monkeypatch, tmp_path):
    root = tmp_path / 'study'
    monkeypatch.setenv('STUDY_ROOT', str(root))
    return root


@pytest.fixture
def archive_root(monkeypatch, tmp_path):
    """A stand-in for the external drive, mounted.

    The parent must exist and the root must not: that is exactly the shape of a
    plugged-in drive whose `archive/study/` has never been written to, and it is
    what `archive_location.resolve`'s parent probe is there for.
    """
    root = tmp_path / 'external' / 'study-archive'
    root.parent.mkdir()
    monkeypatch.setenv('STUDY_ARCHIVE_ROOT', str(root))
    return root


@pytest.fixture
def archive_unplugged(monkeypatch, tmp_path):
    """No drive at all: no override, and a backup path that does not exist."""
    monkeypatch.delenv('STUDY_ARCHIVE_ROOT', raising=False)
    missing = tmp_path / 'unplugged' / 'lunaschal'
    get_db().execute('UPDATE settings SET backup_path=?', (str(missing),))
    get_db().commit()
    return missing


@pytest.fixture
def sync_imports(monkeypatch):
    """Run imports inline instead of on a daemon thread."""
    monkeypatch.setattr(study_routes, '_start_web_import_bg', importer.import_web)
    monkeypatch.setattr(study_routes, '_start_youtube_import_bg', importer.import_youtube)


PAGE_HTML = """
<html><head><title>Attention Is All You Need</title>
<style>body{color:red}</style></head>
<body><script>alert(1)</script>
<h1>Attention</h1><p>The Transformer is a <b>model</b>.</p>
<iframe src="https://evil.example/x"></iframe>
</body></html>
"""


def _upload_pdf(client, data=b'%PDF-1.4 fake', name='Deep Learning.pdf'):
    return client.post(
        '/api/study/sources/pdf',
        data={'file': (io.BytesIO(data), name)},
        content_type='multipart/form-data',
    )


# --- pdf upload ---

def test_upload_pdf_stores_the_file_and_serves_it_back(client, study_root):
    res = _upload_pdf(client)
    assert res.status_code == 201
    body = res.get_json()
    source_id = body['id']
    assert body['source']['kind'] == 'pdf'
    assert body['source']['title'] == 'Deep Learning'
    assert body['source']['importStatus'] == 'ready'
    assert body['source']['sizeBytes'] == len(b'%PDF-1.4 fake')

    assert (study_root / source_id / 'book.pdf').read_bytes() == b'%PDF-1.4 fake'

    served = client.get(f'/api/study/sources/{source_id}/file')
    assert served.status_code == 200
    assert served.mimetype == 'application/pdf'
    assert served.data == b'%PDF-1.4 fake'


def test_upload_rejects_a_non_pdf(client):
    res = client.post(
        '/api/study/sources/pdf',
        data={'file': (io.BytesIO(b'nope'), 'notes.txt')},
        content_type='multipart/form-data',
    )
    assert res.status_code == 400
    assert client.get('/api/study/sources').get_json() == []


def test_the_stored_path_never_leaves_the_server(client):
    source_id = _upload_pdf(client).get_json()['id']
    listed = client.get('/api/study/sources').get_json()
    assert 'filePath' not in listed[0]
    assert 'filePath' not in client.get(f'/api/study/sources/{source_id}').get_json()


# --- web import ---

def test_web_import_archives_a_sanitized_page(client, study_root, monkeypatch, sync_imports):
    monkeypatch.setattr(
        importer, 'fetch_public_page',
        lambda url, **kw: ('https://arxiv.org/abs/1706.03762', PAGE_HTML),
    )
    res = client.post('/api/study/sources/web', json={'url': 'https://arxiv.org/abs/1706.03762'})
    assert res.status_code == 202
    source_id = res.get_json()['id']

    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['importStatus'] == 'ready'
    assert source['title'] == 'Attention Is All You Need'
    assert source['contentType'] == 'text/html'

    stored = (study_root / source_id / 'article.html').read_text()
    assert 'The Transformer is a <b>model</b>' in stored
    # Script/style content is dropped with the tag, not merely unwrapped, and
    # an iframe is not in the allowed set at all.
    assert 'alert(1)' not in stored
    assert 'color:red' not in stored
    assert '<iframe' not in stored

    served = client.get(f'/api/study/sources/{source_id}/file')
    assert served.status_code == 200
    assert served.mimetype == 'text/html'


def test_web_import_refuses_a_private_address(client, monkeypatch, sync_imports):
    def refuse(url, **kw):
        raise UnsafeUrl('169.254.169.254 resolves to a non-public address')

    monkeypatch.setattr(importer, 'fetch_public_page', refuse)
    res = client.post('/api/study/sources/web', json={'url': 'http://169.254.169.254/latest/'})
    source_id = res.get_json()['id']

    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['importStatus'] == 'error'
    assert 'non-public address' in source['importError']
    # Nothing half-written: a refused fetch never reaches the filesystem.
    assert storage.source_dir(source_id) is not None
    assert not storage.source_dir(source_id).exists()


def test_web_import_needs_a_url(client):
    assert client.post('/api/study/sources/web', json={}).status_code == 400


# --- youtube import ---

def _fake_ytdlp(monkeypatch, tmp_dir_written='video.mp4', *, meta_rc=0, dl_rc=0,
                stderr='', title='Lecture 1: Backprop', duration=3671):
    calls = []

    def run(args, timeout):
        calls.append(args)
        if '-J' in args:
            return subprocess.CompletedProcess(
                args, meta_rc,
                json.dumps({'title': title, 'duration': duration}), stderr,
            )
        if dl_rc == 0:
            # -o <dir>/video.%(ext)s — write what yt-dlp would have written.
            out = args[args.index('-o') + 1]
            path = out.replace('%(ext)s', tmp_dir_written.rsplit('.', 1)[1])
            from pathlib import Path
            Path(path).write_bytes(b'\x00\x00\x00 ftypmp42')
        return subprocess.CompletedProcess(args, dl_rc, '', stderr)

    monkeypatch.setattr(importer, 'run_ytdlp', run)
    return calls


def test_youtube_import_downloads_and_records_the_video(
    client, study_root, archive_root, monkeypatch, sync_imports
):
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    calls = _fake_ytdlp(monkeypatch)

    res = client.post(
        '/api/study/sources/youtube',
        json={'url': 'https://www.youtube.com/watch?v=aircAruvnKk&list=PLabc'},
    )
    assert res.status_code == 202
    source_id = res.get_json()['id']

    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['importStatus'] == 'ready'
    assert source['title'] == 'Lecture 1: Backprop'
    assert source['durationSeconds'] == 3671
    assert source['contentType'] == 'video/mp4'
    # On the drive, and nowhere else: a lecture is hundreds of megabytes and
    # `data/` is what the nightly rsync mirrors twice.
    assert (archive_root / source_id / 'video.mp4').is_file()
    assert not (study_root / source_id).exists()

    # The playlist in the pasted URL is dropped before yt-dlp sees it.
    assert 'https://www.youtube.com/watch?v=aircAruvnKk' in calls[0]
    assert all('list=' not in arg for call in calls for arg in call)
    assert '--no-playlist' in calls[1]


def test_youtube_import_serves_ranges_so_the_video_can_seek(
    client, archive_root, monkeypatch, sync_imports
):
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    _fake_ytdlp(monkeypatch)
    res = client.post(
        '/api/study/sources/youtube', json={'url': 'https://youtu.be/aircAruvnKk'}
    )
    source_id = res.get_json()['id']

    served = client.get(
        f'/api/study/sources/{source_id}/file', headers={'Range': 'bytes=4-7'}
    )
    assert served.status_code == 206
    assert served.data == b'ftyp'


def test_youtube_download_asks_for_h264_within_the_height_cap(
    client, archive_root, monkeypatch, sync_imports
):
    """Safari has no software AV1 decoder, and Apple's first hardware one is the
    A17 Pro / M3 — so on the 12.9" iPad Pro this tab targets, an AV1 download
    silently fails to play. The selector must ask for avc1/mp4a first."""
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    calls = _fake_ytdlp(monkeypatch)

    client.post('/api/study/sources/youtube', json={'url': 'https://youtu.be/aircAruvnKk'})

    fmt = calls[1][calls[1].index('-f') + 1]
    # The *first* branch is what wins on any normal YouTube video.
    assert fmt.split('/')[0] == (
        f'bv*[height<={ytdlp.YTDLP_MAX_HEIGHT}][vcodec^=avc1]'
        f'+ba[acodec^=mp4a]'
    )
    # Every branch stays inside the height cap except the bare last-resort one.
    branches = fmt.split('/')
    assert all(f'height<={ytdlp.YTDLP_MAX_HEIGHT}' in b for b in branches[:-1])
    assert branches[-1] == 'b'
    assert '--merge-output-format' in calls[1]


def test_youtube_import_ignores_a_leftover_format_fragment(
    client, archive_root, monkeypatch, sync_imports
):
    """`video.f137.mp4` globs like the real file and sorts *before* it."""
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)

    def run(args, timeout):
        if '-J' in args:
            return subprocess.CompletedProcess(
                args, 0, json.dumps({'title': 'Lecture', 'duration': 60}), ''
            )
        from pathlib import Path
        out = Path(args[args.index('-o') + 1])
        out.with_name('video.f137.mp4').write_bytes(b'fragment')
        out.with_name('video.mp4').write_bytes(b'merged')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(importer, 'run_ytdlp', run)
    res = client.post(
        '/api/study/sources/youtube', json={'url': 'https://youtu.be/aircAruvnKk'}
    )
    source_id = res.get_json()['id']

    assert client.get(f'/api/study/sources/{source_id}/file').data == b'merged'


def test_youtube_import_surfaces_a_ytdlp_failure(
    client, archive_root, monkeypatch, sync_imports
):
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    _fake_ytdlp(monkeypatch, dl_rc=1, stderr='ERROR: Video unavailable')

    res = client.post(
        '/api/study/sources/youtube', json={'url': 'https://youtu.be/aircAruvnKk'}
    )
    source_id = res.get_json()['id']

    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['importStatus'] == 'error'
    assert 'Video unavailable' in source['importError']


def test_youtube_import_refuses_when_the_archive_drive_is_gone(
    client, study_root, archive_unplugged, monkeypatch, sync_imports
):
    """The failure this whole storage split exists to prevent.

    A `mkdir -p` onto an unmounted mountpoint followed by a 279 MB download is
    the one failure mode that looks exactly like success — it fills the root
    partition and reports ready. So: refuse, say why, and write nothing.
    """
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    calls = _fake_ytdlp(monkeypatch)

    res = client.post(
        '/api/study/sources/youtube', json={'url': 'https://youtu.be/aircAruvnKk'}
    )
    source_id = res.get_json()['id']

    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['importStatus'] == 'error'
    assert source['importError'] == 'The backup drive is not connected.'
    # Nothing on the SSD, nothing conjured at the mountpoint, and yt-dlp was
    # never even asked for the metadata.
    assert not (study_root / source_id).exists()
    assert not archive_unplugged.exists()
    assert calls == []


def test_a_video_on_a_disconnected_drive_is_listed_but_not_served(
    client, archive_root, monkeypatch, sync_imports
):
    """Piano's model: the catalog stays browsable, the file 404s."""
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    _fake_ytdlp(monkeypatch)
    res = client.post(
        '/api/study/sources/youtube', json={'url': 'https://youtu.be/aircAruvnKk'}
    )
    source_id = res.get_json()['id']
    assert client.get(f'/api/study/sources/{source_id}').get_json()['fileAvailable']

    # Unplug it.
    monkeypatch.setenv('STUDY_ARCHIVE_ROOT', str(archive_root / 'nope' / 'gone'))

    listing = client.get('/api/study/sources').get_json()
    row = next(r for r in listing if r['id'] == source_id)
    assert row['title'] == 'Lecture 1: Backprop'
    assert row['fileAvailable'] is False
    assert row['fileUnavailableReason']
    assert client.get(f'/api/study/sources/{source_id}/file').status_code == 404


def test_a_pdf_is_always_available_and_stays_on_the_local_root(
    client, study_root, archive_unplugged
):
    """PDFs and articles are small and irreplaceable; they keep riding the
    backup, and an absent drive has nothing to do with them."""
    source_id = _upload_pdf(client).get_json()['id']

    assert (study_root / source_id / 'book.pdf').is_file()
    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['fileAvailable'] is True
    assert client.get(f'/api/study/sources/{source_id}/file').status_code == 200


def test_resolve_stored_path_guards_both_roots(client, study_root, archive_root, tmp_path):
    outside = tmp_path / 'elsewhere' / 'x.mp4'
    assert storage.resolve_stored_path(str(study_root / 'abc' / 'book.pdf')) is not None
    assert storage.resolve_stored_path(str(archive_root / 'abc' / 'video.mp4')) is not None
    assert storage.resolve_stored_path(str(outside)) is None
    # Too deep is still refused against both — the grandchild shape is the rule.
    assert storage.resolve_stored_path(str(study_root / 'a' / 'b' / 'c.pdf')) is None
    assert storage.resolve_stored_path(str(archive_root / 'a' / 'b' / 'c.mp4')) is None


def test_deleting_a_video_removes_it_from_the_archive(
    client, archive_root, monkeypatch, sync_imports
):
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    _fake_ytdlp(monkeypatch)
    res = client.post(
        '/api/study/sources/youtube', json={'url': 'https://youtu.be/aircAruvnKk'}
    )
    source_id = res.get_json()['id']
    assert (archive_root / source_id).is_dir()

    assert client.delete(f'/api/study/sources/{source_id}').status_code == 200
    assert not (archive_root / source_id).exists()


def test_youtube_import_rejects_a_non_youtube_url(client, monkeypatch, sync_imports):
    monkeypatch.setattr(importer, 'assert_public_url', lambda url: url)
    res = client.post('/api/study/sources/youtube', json={'url': 'https://vimeo.com/12345'})
    source_id = res.get_json()['id']

    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['importStatus'] == 'error'
    assert 'YouTube' in source['importError']
    # The URL stands in as the title, so a failed row still says what it was.
    assert source['title'] == 'https://vimeo.com/12345'


# --- url parsing ---

@pytest.mark.parametrize('url,expected', [
    ('https://www.youtube.com/watch?v=aircAruvnKk', 'aircAruvnKk'),
    ('https://youtube.com/watch?v=aircAruvnKk&t=42s', 'aircAruvnKk'),
    ('https://youtu.be/aircAruvnKk', 'aircAruvnKk'),
    ('https://youtu.be/aircAruvnKk?t=42', 'aircAruvnKk'),
    ('https://www.youtube.com/shorts/aircAruvnKk', 'aircAruvnKk'),
    ('https://www.youtube.com/embed/aircAruvnKk', 'aircAruvnKk'),
    ('https://m.youtube.com/watch?v=aircAruvnKk', 'aircAruvnKk'),
    ('https://vimeo.com/watch?v=aircAruvnKk', None),
    ('https://www.youtube.com/playlist?list=PLabc', None),
    ('https://www.youtube.com/watch?v=short', None),
    ('ftp://youtube.com/watch?v=aircAruvnKk', None),
    ('not a url', None),
])
def test_parse_video_id(url, expected):
    assert youtube.parse_video_id(url) == expected


# --- notes, deletion, guards ---

def test_binding_a_note_and_touching_the_open_time(client):
    source_id = _upload_pdf(client).get_json()['id']

    res = client.patch(
        f'/api/study/sources/{source_id}', json={'notePath': 'study/deep-learning.md'}
    )
    assert res.status_code == 200
    assert res.get_json()['notePath'] == 'study/deep-learning.md'

    touched = client.patch(f'/api/study/sources/{source_id}', json={'touch': True})
    assert touched.get_json()['lastOpenedAt'] is not None

    # An emptied path unbinds rather than storing ''.
    cleared = client.patch(f'/api/study/sources/{source_id}', json={'notePath': ''})
    assert cleared.get_json()['notePath'] is None


def test_position_round_trips_and_means_what_the_kind_says(client):
    """One column, two meanings — `kind` is what disambiguates it."""
    pdf_id = _upload_pdf(client).get_json()['id']
    assert client.get(f'/api/study/sources/{pdf_id}').get_json()['position'] is None

    body = client.patch(
        f'/api/study/sources/{pdf_id}', json={'position': 214}
    ).get_json()
    assert body['position'] == 214

    # Seconds, for a video — fractional, because currentTime is.
    assert client.patch(
        f'/api/study/sources/{pdf_id}', json={'position': 2831.5}
    ).get_json()['position'] == 2831.5

    # Explicitly forgotten.
    assert client.patch(
        f'/api/study/sources/{pdf_id}', json={'position': None}
    ).get_json()['position'] is None


def test_a_nonsense_position_is_cleaned_rather_than_stored(client):
    pdf_id = _upload_pdf(client).get_json()['id']

    def patched(value):
        return client.patch(
            f'/api/study/sources/{pdf_id}', json={'position': value}
        ).get_json()['position']

    assert patched(-3) == 0
    # A stored infinity would seek somewhere the media cannot go, and read as
    # a broken file rather than as a bad number.
    assert patched(float('inf')) is None
    assert patched('halfway') is None


def test_saving_a_position_is_not_an_edit_to_the_source(client):
    """This write fires every few seconds of scrolling. If it bumped
    `updated_at`, reading a document would reorder anything sorted by change."""
    pdf_id = _upload_pdf(client).get_json()['id']
    before = client.get(f'/api/study/sources/{pdf_id}').get_json()

    time.sleep(1.1)
    after = client.patch(
        f'/api/study/sources/{pdf_id}', json={'position': 7}
    ).get_json()

    assert after['position'] == 7
    assert after['updatedAt'] == before['updatedAt']

    # A real edit alongside it still counts as one.
    retitled = client.patch(
        f'/api/study/sources/{pdf_id}', json={'position': 9, 'title': 'Renamed'}
    ).get_json()
    assert retitled['updatedAt'] != before['updatedAt']


def test_the_note_mode_and_its_paper_round_trip(client):
    """The desk's right half has two modes, and a source remembers which one it
    was last studied with. The paper is an ordinary `papers` row, borrowed
    whole rather than modelled again."""
    source_id = _upload_pdf(client).get_json()['id']
    fresh = client.get(f'/api/study/sources/{source_id}').get_json()
    assert fresh['noteMode'] == 'note'
    assert fresh['paperId'] is None

    paper_id = str(ULID())
    get_db().execute(
        'INSERT INTO papers (id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (paper_id, 'Worked through', 0, 0),
    )
    get_db().commit()

    bound = client.patch(
        f'/api/study/sources/{source_id}',
        json={'paperId': paper_id, 'noteMode': 'paper'},
    ).get_json()
    assert bound['paperId'] == paper_id
    assert bound['noteMode'] == 'paper'

    # Back to the text editor, without unbinding the paper it already has.
    back = client.patch(
        f'/api/study/sources/{source_id}', json={'noteMode': 'note'}
    ).get_json()
    assert back['noteMode'] == 'note'
    assert back['paperId'] == paper_id


def test_an_unknown_paper_is_refused_rather_than_crashing(client):
    """`paper_id` is a real foreign key, so an id the papers table has never
    heard of would otherwise surface as an IntegrityError and a 500."""
    source_id = _upload_pdf(client).get_json()['id']

    res = client.patch(
        f'/api/study/sources/{source_id}', json={'paperId': str(ULID())}
    )
    assert res.status_code == 400
    assert client.get(f'/api/study/sources/{source_id}').get_json()['paperId'] is None

    assert (
        client.patch(
            f'/api/study/sources/{source_id}', json={'noteMode': 'sideways'}
        ).status_code
        == 400
    )


def test_deleting_the_paper_unbinds_the_source_rather_than_deleting_it(client):
    """ON DELETE SET NULL: the paper is reachable from the Paper tab like any
    other, so it can be deleted there — and that must cost the source its
    binding, not its existence."""
    source_id = _upload_pdf(client).get_json()['id']
    paper_id = str(ULID())
    get_db().execute(
        'INSERT INTO papers (id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (paper_id, 'Worked through', 0, 0),
    )
    get_db().commit()
    client.patch(
        f'/api/study/sources/{source_id}',
        json={'paperId': paper_id, 'noteMode': 'paper'},
    )

    assert client.delete(f'/api/paper/{paper_id}').status_code == 200

    row = client.get(f'/api/study/sources/{source_id}').get_json()
    assert row['paperId'] is None
    # The mode is left alone: the desk creates a fresh paper on the next visit
    # rather than silently moving the user back to the text editor.
    assert row['noteMode'] == 'paper'


def test_switching_note_mode_is_not_an_edit_to_the_source(client):
    """Which half of the desk you had open is a view preference, flipped every
    time you glance at the other pane — not a change to the source."""
    source_id = _upload_pdf(client).get_json()['id']
    before = client.get(f'/api/study/sources/{source_id}').get_json()

    time.sleep(1.1)
    after = client.patch(
        f'/api/study/sources/{source_id}', json={'noteMode': 'paper'}
    ).get_json()
    assert after['noteMode'] == 'paper'
    assert after['updatedAt'] == before['updatedAt']

    # Binding a paper *is* an edit, exactly as binding a note is.
    paper_id = str(ULID())
    get_db().execute(
        'INSERT INTO papers (id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (paper_id, 'Worked through', 0, 0),
    )
    get_db().commit()
    bound = client.patch(
        f'/api/study/sources/{source_id}', json={'paperId': paper_id}
    ).get_json()
    assert bound['updatedAt'] != before['updatedAt']


def test_delete_removes_the_row_and_the_directory(client, study_root):
    source_id = _upload_pdf(client).get_json()['id']
    assert (study_root / source_id).is_dir()

    assert client.delete(f'/api/study/sources/{source_id}').status_code == 200
    assert not (study_root / source_id).exists()
    assert client.get(f'/api/study/sources/{source_id}').status_code == 404
    assert client.delete(f'/api/study/sources/{source_id}').status_code == 404


def test_a_tampered_stored_path_is_not_served(client, tmp_path):
    source_id = _upload_pdf(client).get_json()['id']
    secret = tmp_path / 'secret.pdf'
    secret.write_bytes(b'not yours')
    db = get_db()
    db.execute('UPDATE study_sources SET file_path=? WHERE id=?', (str(secret), source_id))
    db.commit()

    assert client.get(f'/api/study/sources/{source_id}/file').status_code == 404


def test_stale_imports_are_reset_at_startup(client):
    """A row left 'importing' by a killed process has no thread behind it."""
    from backend.db.connection import _reset_stale_study_imports

    source_id = _upload_pdf(client).get_json()['id']
    db = get_db()
    db.execute("UPDATE study_sources SET import_status='importing' WHERE id=?", (source_id,))
    db.commit()

    _reset_stale_study_imports(db)

    source = client.get(f'/api/study/sources/{source_id}').get_json()
    assert source['importStatus'] == 'error'
    assert 'restart' in source['importError']


# --- Filing a source into the Journal ---
#
# The same lazy 4am move papers use: flagging is reversible until a boundary
# passes, and then the source leaves the library for the feed. What the card
# carries is the whole sitting -- the source, the pages of the paper it was
# written on, and the text of its Notebook note.

@pytest.fixture
def notebook_root(monkeypatch, tmp_path):
    root = tmp_path / 'notebook'
    root.mkdir()
    monkeypatch.setenv('NOTEBOOK_ROOT', str(root))
    return root


def _file_source(source_id, days_ago=2, hour=5):
    """Put the flag inside a finished 4am day, so the source has moved."""
    from backend.day_boundary import day_bounds, day_key_for

    start, _ = day_bounds(day_key_for(int(time.time()) - days_ago * 86400))
    at = start + hour * 3600
    db = get_db()
    db.execute(
        'UPDATE study_sources SET archive_requested_at=? WHERE id=?', (at, source_id)
    )
    db.commit()
    return start, at


def _journal_cards(client):
    return client.get('/api/study/journal').get_json()


def _new_source(client, name='Deep Learning.pdf'):
    res = _upload_pdf(client, name=name)
    assert res.status_code == 201
    return res.get_json()['id']


def _save_page_snapshot(client, page_id):
    """A page with a stored PNG, so it has a thumbnail to appear in the card."""
    return client.put(
        f'/api/paper/pages/{page_id}',
        data={
            'strokes': json.dumps([]),
            'width': '800',
            'height': '1000',
            'snapshot': (io.BytesIO(b'\x89PNG-fake'), 'snapshot.png'),
        },
        content_type='multipart/form-data',
    )


def test_flagging_keeps_the_source_in_the_library_until_4am(client):
    source_id = _new_source(client)

    r = client.patch(f'/api/study/sources/{source_id}', json={'archiveRequested': True})
    assert r.status_code == 200
    assert r.get_json()['pendingArchive'] is True

    listed = client.get('/api/study/sources').get_json()
    assert any(s['id'] == source_id and s['pendingArchive'] for s in listed)
    assert _journal_cards(client) == []


def test_once_the_boundary_passes_it_leaves_the_library_for_the_feed(client):
    source_id = _new_source(client)
    client.patch(f'/api/study/sources/{source_id}', json={'archiveRequested': True})
    _, flagged_at = _file_source(source_id)

    from backend.day_boundary import day_key_for

    assert all(s['id'] != source_id for s in client.get('/api/study/sources').get_json())
    cards = _journal_cards(client)
    assert len(cards) == 1
    assert cards[0]['id'] == source_id
    assert cards[0]['journalDate'] == day_key_for(flagged_at)
    assert cards[0]['kind'] == 'pdf'


def test_unflagging_returns_the_source_to_the_library(client):
    source_id = _new_source(client)
    client.patch(f'/api/study/sources/{source_id}', json={'archiveRequested': True})
    _file_source(source_id)
    assert len(_journal_cards(client)) == 1

    client.patch(f'/api/study/sources/{source_id}', json={'archiveRequested': False})
    listed = client.get('/api/study/sources').get_json()
    match = [s for s in listed if s['id'] == source_id]
    assert match and match[0]['pendingArchive'] is False
    assert _journal_cards(client) == []


def test_the_card_carries_the_media_the_pages_and_the_note_together(
    client, notebook_root
):
    """One sitting, one card: an article read and the page of notes taken
    beside it are not two events in the day's record."""
    source_id = _new_source(client)

    note_rel = 'study/wal.md'
    note_file = notebook_root / note_rel
    note_file.parent.mkdir(parents=True, exist_ok=True)
    note_file.write_text('# WAL\n\nReaders do not block the writer.\n')

    paper_id = client.post('/api/paper').get_json()['id']
    page_id = client.get(f'/api/paper/{paper_id}').get_json()['pages'][0]['id']
    _save_page_snapshot(client, page_id)
    client.patch(
        f'/api/study/sources/{source_id}',
        json={'notePath': note_rel, 'paperId': paper_id, 'archiveRequested': True},
    )
    _file_source(source_id)

    card = _journal_cards(client)[0]
    assert card['notePath'] == note_rel
    assert 'Readers do not block the writer.' in card['note']
    assert card['noteTruncated'] is False
    assert len(card['pages']) == 1
    assert card['pages'][0]['imageUrl'].startswith(f'/api/paper/pages/{page_id}/image')
    assert card['fileUrl'] == f'/api/study/sources/{source_id}/file'


def test_the_bound_paper_gets_no_card_of_its_own(client):
    """Otherwise the same drawings print twice in one day."""
    source_id = _new_source(client)
    paper_id = client.post('/api/paper').get_json()['id']
    client.patch(f'/api/study/sources/{source_id}', json={'paperId': paper_id})
    client.patch(f'/api/paper/{paper_id}', json={'archiveRequested': True})

    from backend.day_boundary import day_bounds, day_key_for

    start, _ = day_bounds(day_key_for(int(time.time()) - 2 * 86400))
    db = get_db()
    db.execute(
        'UPDATE papers SET archive_requested_at=? WHERE id=?',
        (start + 5 * 3600, paper_id),
    )
    db.commit()

    assert client.get('/api/paper/journal').get_json() == []


def test_the_timestamp_follows_the_ink_not_the_flag(client):
    """study_sources.updated_at is bumped by `touch` on every desk open, so it
    would place the card at the moment you sat down."""
    from datetime import datetime, timezone

    source_id = _new_source(client)
    paper_id = client.post('/api/paper').get_json()['id']
    client.patch(f'/api/study/sources/{source_id}', json={'paperId': paper_id})
    day_start, flagged_at = _file_source(source_id)

    drawn_at = day_start + 16 * 3600
    db = get_db()
    db.execute('UPDATE papers SET content_updated_at=? WHERE id=?', (drawn_at, paper_id))
    db.commit()

    card = _journal_cards(client)[0]
    assert card['archivedAt'] == datetime.fromtimestamp(
        drawn_at, tz=timezone.utc
    ).isoformat()
    assert card['archivedAt'] > datetime.fromtimestamp(
        flagged_at, tz=timezone.utc
    ).isoformat()


def test_the_note_s_own_mtime_counts_as_working_on_it(client, notebook_root):
    """A source studied with the Notebook half rather than the paper one."""
    import os
    from datetime import datetime, timezone

    source_id = _new_source(client)
    note_rel = 'study/typed.md'
    note_file = notebook_root / note_rel
    note_file.parent.mkdir(parents=True, exist_ok=True)
    note_file.write_text('typed while watching\n')

    client.patch(f'/api/study/sources/{source_id}', json={'notePath': note_rel})
    day_start, _ = _file_source(source_id)
    typed_at = day_start + 14 * 3600
    os.utime(note_file, (typed_at, typed_at))

    card = _journal_cards(client)[0]
    assert card['archivedAt'] == datetime.fromtimestamp(
        typed_at, tz=timezone.utc
    ).isoformat()


def test_a_source_with_no_notes_falls_back_to_when_it_was_opened(client):
    """A video simply watched through leaves no mark but the sitting itself."""
    from datetime import datetime, timezone

    source_id = _new_source(client)
    day_start, _ = _file_source(source_id)
    opened_at = day_start + 11 * 3600
    db = get_db()
    db.execute(
        'UPDATE study_sources SET last_opened_at=? WHERE id=?', (opened_at, source_id)
    )
    db.commit()

    card = _journal_cards(client)[0]
    assert card['archivedAt'] == datetime.fromtimestamp(
        opened_at, tz=timezone.utc
    ).isoformat()


def test_a_video_on_an_unplugged_drive_still_gets_a_card(
    client, archive_unplugged, monkeypatch, sync_imports
):
    """Listed and unreachable, the way viewerKindFor already treats it."""
    db = get_db()
    now = int(time.time())
    source_id = str(ULID())
    db.execute(
        'INSERT INTO study_sources (id, title, kind, source_url, import_status,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (source_id, 'A lecture', 'youtube', 'https://example.com/v', 'ready', now, now),
    )
    db.commit()
    _file_source(source_id)

    card = _journal_cards(client)[0]
    assert card['id'] == source_id
    assert card['fileAvailable'] is False
    assert card['fileUnavailableReason']


def test_the_feed_is_ordered_newest_first_by_the_last_edit(client):
    """buildFeed takes every source it is handed as already newest-first."""
    from backend.day_boundary import day_bounds, day_key_for

    ids = [_new_source(client, name=f'{i}.pdf') for i in range(3)]
    day_start, _ = day_bounds(day_key_for(int(time.time()) - 2 * 86400))
    db = get_db()
    for i, (source_id, opened_hour) in enumerate(zip(ids, (18, 6, 12))):
        db.execute(
            'UPDATE study_sources SET archive_requested_at=?, last_opened_at=?'
            ' WHERE id=?',
            (day_start + 5 * 3600 + i, day_start + opened_hour * 3600, source_id),
        )
    db.commit()

    assert [c['id'] for c in _journal_cards(client)] == [ids[0], ids[2], ids[1]]


def test_an_oversized_note_is_truncated_rather_than_dropped(client, notebook_root):
    from backend.routes.study import NOTE_BYTE_CAP

    source_id = _new_source(client)
    note_rel = 'study/huge.md'
    note_file = notebook_root / note_rel
    note_file.parent.mkdir(parents=True, exist_ok=True)
    note_file.write_text('x' * (NOTE_BYTE_CAP + 5000))

    client.patch(f'/api/study/sources/{source_id}', json={'notePath': note_rel})
    _file_source(source_id)

    card = _journal_cards(client)[0]
    assert card['noteTruncated'] is True
    assert len(card['note']) == NOTE_BYTE_CAP


def test_the_flag_reaches_the_client_as_a_state_not_a_timestamp(client):
    """`_LIST_COLS` is curated on purpose; the raw column stays server-side."""
    source_id = _new_source(client)
    client.patch(f'/api/study/sources/{source_id}', json={'archiveRequested': True})

    for row in (
        client.get(f'/api/study/sources/{source_id}').get_json(),
        client.get('/api/study/sources').get_json()[0],
    ):
        assert row['pendingArchive'] is True
        assert 'archiveRequestedAt' not in row
