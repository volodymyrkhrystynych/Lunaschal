"""A YouTube video attached to a journal entry.

No real network and no real yt-dlp: `youtube_import.run_ytdlp` is monkeypatched
to write the files yt-dlp would have written, and the download thread is run
inline so a POST can be asserted on directly (the backend/tests/test_study.py
pattern).

What is worth pinning down here: the archive drive is checked *before* anything
is downloaded, the row exists from the moment the link is pasted so the card can
show itself working, captions are preferred to a transcription pass, the
summarizer never runs before there is a transcript for it to read, and a paused
GPU leaves the summary queued rather than recording it as a permanent failure.
"""
import json
import subprocess
from pathlib import Path

import pytest
from ulid import ULID

from backend.ai import service
from backend.db.connection import get_db
from backend.journal import youtube_import
from backend.routes import journal as journal_routes

URL = 'https://www.youtube.com/watch?v=aircAruvnKk'

CAPTIONS = """WEBVTT

00:00:01.000 --> 00:00:03.000
a neural network is a function

00:00:03.000 --> 00:00:05.000
a neural network is a function
that you train with gradient descent
"""


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _no_entry_background_work(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _allow_url(monkeypatch):
    """The SSRF guard is exercised in its own test below; everywhere else it
    would just reject the fixture URL for having no DNS."""
    monkeypatch.setattr(youtube_import, 'assert_public_url', lambda url: url)


@pytest.fixture
def archive_root(monkeypatch, tmp_path):
    """A stand-in for the external drive, mounted.

    Parent exists, root does not — the shape of a plugged-in drive whose
    `archive/journal/` has never been written to.
    """
    root = tmp_path / 'external' / 'journal-archive'
    root.parent.mkdir(parents=True)
    monkeypatch.setenv('JOURNAL_ARCHIVE_ROOT', str(root))
    return root


@pytest.fixture
def archive_unplugged(monkeypatch, tmp_path):
    """No drive at all: no override, and a backup path that does not exist."""
    monkeypatch.delenv('JOURNAL_ARCHIVE_ROOT', raising=False)
    missing = tmp_path / 'unplugged' / 'lunaschal'
    get_db().execute('UPDATE settings SET backup_path=?', (str(missing),))
    get_db().commit()
    return missing


@pytest.fixture(autouse=True)
def _no_download_thread(monkeypatch):
    """No test spawns the real daemon thread.

    It outlives the test, and the `client` fixture's teardown closes the
    module-global SQLite connection out from under it — which segfaults the
    interpreter rather than raising (see backend/tests/conftest.py). Tests that
    want the download opt into `sync_import` and get it inline.
    """
    monkeypatch.setattr(youtube_import, 'start_import_bg', lambda *a, **k: None)


@pytest.fixture
def sync_import(monkeypatch, _no_download_thread):
    """Run the download inline instead of on a daemon thread."""
    monkeypatch.setattr(youtube_import, 'start_import_bg', youtube_import.import_youtube)


@pytest.fixture
def entry_id(client):
    return client.post(
        '/api/journal', json={'content': 'Watched this on the train.'}
    ).get_json()['id']


def _fake_ytdlp(monkeypatch, *, captions=CAPTIONS, thumbnail=True, video='video.mp4',
                meta_rc=0, dl_rc=0, stderr='', title='But what is a neural network?',
                duration=1140):
    calls = []

    def run(args, timeout):
        calls.append(args)
        if '-J' in args:
            return subprocess.CompletedProcess(
                args, meta_rc,
                json.dumps({'title': title, 'duration': duration}), stderr,
            )
        if dl_rc == 0:
            out = Path(args[args.index('-o') + 1])
            directory = out.parent
            (directory / video).write_bytes(b'\x00\x00\x00 ftypmp42')
            if captions is not None:
                (directory / 'video.en.vtt').write_text(captions, encoding='utf-8')
            if thumbnail:
                (directory / 'video.jpg').write_bytes(b'\xff\xd8\xff\xe0jpeg')
        return subprocess.CompletedProcess(args, dl_rc, '', stderr)

    monkeypatch.setattr(youtube_import, 'run_ytdlp', run)
    return calls


def _jobs(monkeypatch):
    """Hold queued jobs back so the test runs them itself, in order."""
    from backend.ai import job_handlers  # noqa: F401  (registers handlers)
    from backend.ai import jobs as llm_jobs

    captured = []
    real = llm_jobs.enqueue

    def capture(kind, target_id=None, payload=None, *, commit=True):
        job_id = real(kind, target_id, payload, commit=commit)
        if job_id is not None:
            captured.append((kind, lambda jid=job_id: llm_jobs.process_one(jid)))
        return job_id

    monkeypatch.setattr(llm_jobs, 'enqueue', capture)
    return captured


def _attach(client, entry_id, url=URL, attachment_id=None):
    body = {'url': url}
    if attachment_id is not None:
        body['attachmentId'] = attachment_id
    return client.post(f'/api/journal/{entry_id}/attachments/link', json=body)


def _row(attachment_id):
    return get_db().execute(
        'SELECT * FROM journal_attachments WHERE id=?', (attachment_id,)
    ).fetchone()


# --- validation -------------------------------------------------------------

def test_a_non_youtube_url_is_rejected_before_anything_runs(
    client, entry_id, archive_root, monkeypatch
):
    calls = _fake_ytdlp(monkeypatch)
    res = _attach(client, entry_id, 'https://vimeo.com/12345')
    assert res.status_code == 400
    assert 'YouTube' in res.get_json()['error']
    assert calls == []


def test_a_missing_url_is_rejected(client, entry_id):
    assert client.post(
        f'/api/journal/{entry_id}/attachments/link', json={}
    ).status_code == 400


def test_attaching_to_an_entry_that_does_not_exist_yet_is_a_404(client):
    """The client retries this rather than surfacing it — it means the entry
    create has not landed yet, exactly as for a staged photo."""
    assert _attach(client, str(ULID())).status_code == 404


def test_a_private_url_never_reaches_yt_dlp(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """assert_public_url is the SSRF guard; a rejection must land on the row as
    an error rather than starting a download."""
    from backend.research.web import UnsafeUrl

    def reject(url):
        raise UnsafeUrl('That address is not reachable from here.')

    monkeypatch.setattr(youtube_import, 'assert_public_url', reject)
    calls = _fake_ytdlp(monkeypatch)

    a = _attach(client, entry_id).get_json()
    assert calls == []
    row = _row(a['id'])
    assert row['import_status'] == 'error'
    assert 'not reachable' in row['import_error']


# --- the happy path ---------------------------------------------------------

def test_the_row_exists_immediately_so_the_card_can_show_itself_downloading(
    client, entry_id, archive_root, monkeypatch
):
    """Deliberately without sync_import: nothing has downloaded yet."""
    _fake_ytdlp(monkeypatch)
    res = _attach(client, entry_id)
    assert res.status_code == 201
    a = res.get_json()
    assert a['kind'] == 'youtube'
    assert a['importStatus'] == 'importing'
    assert a['sourceUrl'] == URL
    # No file yet, so no URL pointing at one.
    assert 'url' not in a
    assert 'thumbnailUrl' not in a


def test_a_successful_import_records_the_video_and_its_captions(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    jobs = _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)

    a = _attach(client, entry_id).get_json()
    row = _row(a['id'])

    assert row['import_status'] == 'ready'
    assert row['import_error'] is None
    assert row['name'] == 'But what is a neural network?'
    assert row['duration_seconds'] == 1140
    assert row['mime'] == 'video/mp4'
    assert row['size'] > 0
    assert Path(row['path']).parent.parent == archive_root
    # The rolling caption window is collapsed, not concatenated twice.
    assert row['transcript'] == (
        'a neural network is a function\nthat you train with gradient descent'
    )
    assert row['transcript_status'] == 'done'
    # Captions mean no transcription pass; only the summary is queued.
    assert [k for k, _ in jobs] == ['journal.summarize_youtube']


def test_the_thumbnail_lands_on_the_ssd_not_the_archive_drive(
    client, entry_id, archive_root, monkeypatch, sync_import, tmp_path
):
    """So the card still draws with the drive unplugged."""
    _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)

    a = _attach(client, entry_id).get_json()
    row = _row(a['id'])

    thumb = Path(row['thumb_path'])
    assert thumb.is_file()
    assert str(tmp_path / 'journal-media') in str(thumb)
    assert str(archive_root) not in str(thumb)

    served = client.get(f"/api/journal/attachments/{a['id']}/thumbnail")
    assert served.status_code == 200
    assert served.mimetype == 'image/jpeg'


def test_the_download_asks_for_captions_a_poster_and_720p(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    from backend import ytdlp

    _jobs(monkeypatch)
    calls = _fake_ytdlp(monkeypatch)
    _attach(client, entry_id)

    dl = calls[1]
    assert '--write-auto-subs' in dl and '--write-subs' in dl
    assert '--write-thumbnail' in dl
    assert '--no-playlist' in dl
    fmt = dl[dl.index('-f') + 1]
    assert fmt == ytdlp.YTDLP_FORMAT
    assert f'height<={ytdlp.YTDLP_MAX_HEIGHT}' in fmt


def test_the_video_is_served_with_range_support(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """conditional=True is load-bearing: seeking a 20-minute video otherwise
    re-downloads it on every scrub."""
    _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)
    a = _attach(client, entry_id).get_json()

    served = client.get(
        f"/api/journal/attachments/{a['id']}/file", headers={'Range': 'bytes=4-7'}
    )
    assert served.status_code == 206
    assert served.data == b'ftyp'


# --- replay -----------------------------------------------------------------

def test_replaying_the_same_attachment_id_is_a_no_op(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """The phone re-POSTs until the server confirms, so a retry must not start
    a second download of the same video."""
    _jobs(monkeypatch)
    calls = _fake_ytdlp(monkeypatch)
    attachment_id = str(ULID())

    first = _attach(client, entry_id, attachment_id=attachment_id)
    downloads = len(calls)
    second = _attach(client, entry_id, attachment_id=attachment_id)

    assert first.status_code == second.status_code == 201
    assert second.get_json()['id'] == attachment_id
    assert len(calls) == downloads
    assert get_db().execute(
        'SELECT COUNT(*) c FROM journal_attachments WHERE entry_id=?', (entry_id,)
    ).fetchone()['c'] == 1


def test_losing_the_insert_race_does_not_start_a_second_download(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """Two replays can both pass the existence check before either inserts.
    The loser must not spawn a second yt-dlp writing into the same directory."""
    _jobs(monkeypatch)
    calls = _fake_ytdlp(monkeypatch)
    attachment_id = str(ULID())

    # Stand the row up behind the route's back, so its own INSERT is ignored
    # and `_load_attachment` is what answers.
    _attach(client, entry_id, attachment_id=attachment_id)
    downloads = len(calls)

    # A second POST whose *existence check* is made to miss, while the lookup
    # that builds the response still works — which is the real shape of the
    # race: by the time the response is built, the winner's row is there.
    real = journal_routes._load_attachment
    calls_seen = []

    def miss_once(aid):
        calls_seen.append(aid)
        return None if len(calls_seen) == 1 else real(aid)

    monkeypatch.setattr(journal_routes, '_load_attachment', miss_once)
    res = _attach(client, entry_id, attachment_id=attachment_id)

    assert res.status_code == 201
    assert len(calls) == downloads


# --- no captions ------------------------------------------------------------

def test_without_captions_the_audio_is_transcribed_instead(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    jobs = _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch, captions=None)
    monkeypatch.setattr(
        journal_routes, '_do_attachment_audio', lambda _p: 'Spoken words.'
    )

    a = _attach(client, entry_id).get_json()
    assert _row(a['id'])['transcript_status'] == 'running'
    assert [k for k, _ in jobs] == ['journal.transcribe_attachment']

    jobs[0][1]()
    row = _row(a['id'])
    assert row['transcript'] == 'Spoken words.'
    assert row['transcript_status'] == 'done'
    # Only now is there anything to summarize.
    assert [k for k, _ in jobs] == [
        'journal.transcribe_attachment', 'journal.summarize_youtube'
    ]


def test_a_videos_words_are_never_folded_into_the_entry(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """The entry is the user's commentary. `into_entry` is false for a video,
    unlike a voice clip, or watching something would rewrite what you said
    about it."""
    jobs = _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch, captions=None)
    monkeypatch.setattr(
        journal_routes, '_do_attachment_audio', lambda _p: 'Spoken words.'
    )

    _attach(client, entry_id)
    jobs[0][1]()

    entry = get_db().execute(
        'SELECT content, raw_content FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    assert 'Spoken words.' not in (entry['content'] or '')
    assert 'Spoken words.' not in (entry['raw_content'] or '')


# --- the summary ------------------------------------------------------------

def test_the_summary_lands_on_description_not_on_the_entry(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    jobs = _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)
    monkeypatch.setattr(
        'backend.ai.youtube.summarize_video',
        lambda title, transcript: 'It explains gradient descent.',
    )

    a = _attach(client, entry_id).get_json()
    jobs[0][1]()

    row = _row(a['id'])
    assert row['description'] == 'It explains gradient descent.'
    assert row['description_status'] == 'done'
    # The transcript is untouched — the record and the summary of it are
    # separate columns.
    assert 'gradient descent' in row['transcript']


def test_a_paused_gpu_leaves_the_summary_queued_not_failed(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """InferencePaused means "run this later". Recording it as an error is how
    an evening of paused work becomes permanent failure behind a done job."""
    jobs = _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)

    def paused(title, transcript):
        raise service.InferencePaused(service.PAUSED_MESSAGE)

    monkeypatch.setattr('backend.ai.youtube.summarize_video', paused)

    a = _attach(client, entry_id).get_json()
    jobs[0][1]()

    row = _row(a['id'])
    assert row['description'] is None
    assert row['description_status'] != 'error'
    assert get_db().execute(
        "SELECT status FROM llm_jobs WHERE kind='journal.summarize_youtube'"
    ).fetchone()['status'] == 'pending'


def test_a_failing_summary_is_recorded_not_swallowed(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    jobs = _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)

    def boom(title, transcript):
        raise RuntimeError('model went away')

    monkeypatch.setattr('backend.ai.youtube.summarize_video', boom)

    a = _attach(client, entry_id).get_json()
    jobs[0][1]()

    row = _row(a['id'])
    assert row['description_status'] == 'error'
    assert 'model went away' in row['description_error']


# --- failures ---------------------------------------------------------------

def test_an_unplugged_archive_drive_fails_before_downloading_anything(
    client, entry_id, archive_unplugged, monkeypatch, sync_import
):
    """The drive is checked first on purpose: failing after 300 MB has landed on
    the system SSD is the failure mode that looks like success."""
    calls = _fake_ytdlp(monkeypatch)

    a = _attach(client, entry_id).get_json()
    row = _row(a['id'])

    assert calls == []
    assert row['import_status'] == 'error'
    assert row['import_error']
    assert row['path'] == ''


def test_a_yt_dlp_failure_lands_on_the_row(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    _fake_ytdlp(monkeypatch, dl_rc=1, stderr='ERROR: video unavailable')
    a = _attach(client, entry_id).get_json()
    row = _row(a['id'])
    assert row['import_status'] == 'error'
    assert 'unavailable' in row['import_error']


def test_a_download_that_writes_no_playable_file_is_an_error(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    _fake_ytdlp(monkeypatch, video='video.txt')
    a = _attach(client, entry_id).get_json()
    assert _row(a['id'])['import_status'] == 'error'


def test_a_leftover_format_fragment_does_not_win_over_the_merged_file(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """`video.f137.mp4` globs and sorts before `video.mp4`; only the file whose
    stem is exactly `video` is the merged output."""
    _jobs(monkeypatch)

    def run(args, timeout):
        if '-J' in args:
            return subprocess.CompletedProcess(
                args, 0, json.dumps({'title': 'T', 'duration': 10}), ''
            )
        d = Path(args[args.index('-o') + 1]).parent
        (d / 'video.f137.mp4').write_bytes(b'fragment')
        (d / 'video.mp4').write_bytes(b'\x00\x00\x00 ftypmp42')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(youtube_import, 'run_ytdlp', run)
    a = _attach(client, entry_id).get_json()
    assert Path(_row(a['id'])['path']).name == 'video.mp4'


def test_a_broken_caption_file_does_not_undo_a_finished_download(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """The video is on disk and playable. Flipping the row to 'error' because
    the words half failed would show a broken card over a working file."""
    _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError('caption parsing blew up')

    monkeypatch.setattr(youtube_import, '_queue_words', boom)

    a = _attach(client, entry_id).get_json()
    row = _row(a['id'])

    assert row['import_status'] == 'ready'
    assert row['import_error'] is None
    assert Path(row['path']).is_file()


# --- deletion ---------------------------------------------------------------

def test_deleting_the_attachment_clears_both_roots(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)
    a = _attach(client, entry_id).get_json()
    row = _row(a['id'])
    video, thumb = Path(row['path']), Path(row['thumb_path'])
    assert video.is_file() and thumb.is_file()

    assert client.delete(f"/api/journal/attachments/{a['id']}").status_code == 200
    assert not video.exists()
    assert not thumb.exists()
    assert _row(a['id']) is None


# --- how it interacts with the rest of the entry ----------------------------

def test_an_importing_video_does_not_hold_up_the_entrys_title(
    client, entry_id, archive_root, monkeypatch
):
    """`_attachments_settled` gates the metadata wait on transcript_status, so a
    half-hour download must not land as 'running' — the title would wait out the
    full 300s cap and then time out anyway."""
    _fake_ytdlp(monkeypatch)
    a = _attach(client, entry_id).get_json()
    assert _row(a['id'])['transcript_status'] == 'idle'
    assert journal_routes._attachments_settled(entry_id, 1)


def test_polish_context_includes_a_watched_videos_summary(
    client, entry_id, archive_root, monkeypatch, sync_import
):
    """So a clip recorded about the video is polished against what the video
    actually said — the same use the audio descriptions are put to."""
    jobs = _jobs(monkeypatch)
    _fake_ytdlp(monkeypatch)
    monkeypatch.setattr(
        'backend.ai.youtube.summarize_video',
        lambda title, transcript: 'It explains gradient descent.',
    )
    _attach(client, entry_id)
    jobs[0][1]()

    assert 'gradient descent' in journal_routes._attachment_polish_context(entry_id)
