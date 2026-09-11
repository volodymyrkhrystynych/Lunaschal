"""Pull a YouTube video down onto a journal entry.

The shape is Study's (backend/study/importer.py) — an in-memory progress
registry for "what is it doing right now", a persisted `import_status` for what
survives a restart, and connection.py's `_reset_stale_journal_youtube_imports`
to un-stick a row whose thread died. The differences are all downstream of what
the video is *for*: Study archives something to read later, so it stops at the
download; here the video is the subject of a journal entry, so it also has to
produce words — captions if the upload has them, a transcription pass if not —
and then a few sentences saying what those words were about.

Deliberately a plain daemon thread rather than an `llm_jobs` row, exactly as
Study's is. The queue has a single worker shared with every caption and polish
in the app, and a thirty-minute download sitting in it would head-of-line block
all of them. Nothing here touches a model; the model work is enqueued at the
end, once there is a transcript for it to read.
"""
import json
import logging
import shutil
import subprocess
import threading
from pathlib import Path

from backend.archive_location import ArchiveUnavailable
from backend.db.connection import get_db
from backend.journal import archive
from backend.journal import storage as journal_storage
from backend.journal.vtt import vtt_to_text
from backend.research.web import UnsafeUrl, assert_public_url
from backend.study import youtube
from backend.ytdlp import (
    YTDLP_DOWNLOAD_TIMEOUT,
    YTDLP_FORMAT,
    YTDLP_METADATA_TIMEOUT,
    run_ytdlp,
    ytdlp_error,
)

logger = logging.getLogger(__name__)

# yt-dlp writes `video.en.vtt`, `video.en-orig.vtt`, `video.en-GB.vtt` … We ask
# for `en.*` and take whichever it produced, preferring the shortest name so a
# plain `en` beats an `en-orig` auto-track when a manual one exists.
_SUBS_GLOB = 'video*.vtt'

_progress: dict[str, dict] = {}
_lock = threading.Lock()


def get_progress(attachment_id: str) -> dict | None:
    with _lock:
        p = _progress.get(attachment_id)
        return dict(p) if p else None


def start_progress(attachment_id: str, phase: str) -> None:
    with _lock:
        _progress[attachment_id] = {'phase': phase, 'error': None, 'done': False}


def _update_progress(attachment_id: str, **kw) -> None:
    with _lock:
        if attachment_id in _progress:
            _progress[attachment_id].update(kw)


def cancel_progress(attachment_id: str) -> None:
    with _lock:
        _progress.pop(attachment_id, None)


def _cancelled(attachment_id: str) -> bool:
    """Cancellation is absence from the registry, same as the fic downloader."""
    with _lock:
        return attachment_id not in _progress


def _notify(entry_id: str) -> None:
    """Nudge the journal's SSE subscribers. Imported here rather than at module
    scope: backend/routes/journal.py imports this module."""
    try:
        from backend.routes.journal import _notify_subscribers

        _notify_subscribers(entry_id)
    except Exception:  # noqa: BLE001 — a missed nudge must not fail an import
        logger.debug('could not notify journal subscribers for %s', entry_id)


def _fail(attachment_id: str, entry_id: str, message: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE journal_attachments SET import_status='error', import_error=?"
        ' WHERE id = ?',
        (message[:2000], attachment_id),
    )
    db.commit()
    _update_progress(attachment_id, phase='error', error=message[:2000], done=True)
    _notify(entry_id)


def _set(attachment_id: str, **columns) -> None:
    assignments = ', '.join(f'{col} = ?' for col in columns)
    db = get_db()
    db.execute(
        f'UPDATE journal_attachments SET {assignments} WHERE id = ?',
        (*columns.values(), attachment_id),
    )
    db.commit()


def _pick_subtitle(directory: Path) -> Path | None:
    """The caption file to read, if yt-dlp wrote one.

    Shortest name first: with both a manual `video.en.vtt` and an automatic
    `video.en-orig.vtt` present, the manual track is the better transcript and
    is the one with the shorter name.
    """
    found = sorted(directory.glob(_SUBS_GLOB), key=lambda p: (len(p.name), p.name))
    return found[0] if found else None


def _store_thumbnail(directory: Path, attachment_id: str) -> str | None:
    """Move yt-dlp's poster off the archive drive and onto the SSD.

    See backend/journal/archive.py: the card has to draw when the drive is
    unplugged, and a JPEG is small enough that keeping it beside the database
    costs nothing.
    """
    written = sorted(
        p for p in directory.glob('video.*')
        if p.stem == 'video' and p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp')
    )
    if not written:
        return None
    dest = journal_storage.thumb_path(attachment_id)
    if dest is None:
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(written[0]), str(dest))
    except Exception as e:  # noqa: BLE001 — a missing poster is not a failure
        logger.warning('could not store thumbnail for %s: %s', attachment_id, e)
        return None
    return str(dest)


def import_youtube(attachment_id: str, entry_id: str, url: str) -> None:
    """Download one YouTube video onto a journal attachment, then queue words."""
    try:
        assert_public_url(url)
        video_id = youtube.parse_video_id(url)
        if video_id is None:
            _fail(attachment_id, entry_id, 'That does not look like a YouTube video URL.')
            return
        # Normalized, so a `&list=` on the pasted URL can't turn this into a
        # playlist download.
        target = youtube.watch_url(video_id)

        # The archive drive first, before a single byte is fetched: an unplugged
        # drive must fail here rather than after a 300 MB download, and
        # `video_dir` raises rather than falling back to the SSD.
        directory = archive.video_dir(attachment_id, create=True)
        if directory is None:
            _fail(attachment_id, entry_id, 'Bad attachment id.')
            return
        directory.mkdir(parents=True, exist_ok=True)

        _update_progress(attachment_id, phase='metadata')
        meta_proc = run_ytdlp(
            ['-J', '--no-playlist', '--no-warnings', target], YTDLP_METADATA_TIMEOUT
        )
        if meta_proc.returncode != 0:
            _fail(attachment_id, entry_id, ytdlp_error(meta_proc))
            return

        try:
            meta = json.loads(meta_proc.stdout or '{}')
        except ValueError:
            meta = {}
        title = (meta.get('title') or '').strip()
        duration = meta.get('duration')
        duration = int(duration) if isinstance(duration, (int, float)) else None

        # Written before the download so the card names itself while the bytes
        # are still arriving, instead of showing a bare URL for ten minutes.
        if title or duration is not None:
            _set(
                attachment_id,
                **({'name': title} if title else {}),
                duration_seconds=duration,
            )
            _notify(entry_id)

        if _cancelled(attachment_id):
            return

        _update_progress(attachment_id, phase='downloading')
        dl_proc = run_ytdlp(
            [
                '--no-playlist', '--no-warnings', '--no-part',
                '-f', YTDLP_FORMAT,
                '--merge-output-format', 'mp4',
                # Captions in the same invocation: a second yt-dlp run would
                # re-resolve the video and can disagree with the first about
                # which tracks exist.
                '--write-subs', '--write-auto-subs',
                '--sub-langs', 'en.*',
                '--sub-format', 'vtt/best',
                '--convert-subs', 'vtt',
                '--write-thumbnail', '--convert-thumbnails', 'jpg',
                '-o', str(directory / 'video.%(ext)s'),
                target,
            ],
            YTDLP_DOWNLOAD_TIMEOUT,
        )
        if dl_proc.returncode != 0:
            _fail(attachment_id, entry_id, ytdlp_error(dl_proc))
            return

        # yt-dlp picks the container, so find what it actually wrote rather than
        # assuming --merge-output-format applied (it only does when a merge was
        # needed). `stem == 'video'` rather than a bare glob: a leftover
        # per-format fragment is named `video.f137.mp4`, which globs and *sorts
        # before* the merged `video.mp4`.
        written = sorted(
            p for p in directory.glob('video.*')
            if p.stem == 'video'
            and p.suffix.lower().lstrip('.') in archive.STORED_EXTS
        )
        if not written:
            _fail(
                attachment_id, entry_id,
                'yt-dlp reported success but wrote no playable file.',
            )
            return
        path = written[0]

        thumb = _store_thumbnail(directory, attachment_id)

        _set(
            attachment_id,
            path=str(path),
            mime=archive.mimetype_for(path),
            size=path.stat().st_size,
            thumb_path=thumb,
            source_url=target,
            duration_seconds=duration,
            import_status='ready',
            import_error=None,
            **({'name': title} if title else {}),
        )
        _update_progress(attachment_id, phase='done', done=True)
        _notify(entry_id)

        # Outside the failure handling below, deliberately. The download has
        # already succeeded and the row already says `ready`; letting a broken
        # caption file reach `_fail` would flip a video that is on disk and
        # playable into an error the user cannot clear. Words are the bonus
        # half of this, and their absence is not a failed import.
        try:
            _queue_words(attachment_id, entry_id, directory, title, str(path))
        except Exception:  # noqa: BLE001
            logger.exception('could not queue words for %s', attachment_id)
    except UnsafeUrl as e:
        _fail(attachment_id, entry_id, str(e))
    except ArchiveUnavailable as e:
        # 'error' with a readable reason, which the card renders with a Retry —
        # so plugging the drive in and hitting Retry is the whole recovery story.
        _fail(attachment_id, entry_id, str(e))
    except subprocess.TimeoutExpired:
        _fail(attachment_id, entry_id, 'yt-dlp timed out.')
    except Exception as e:  # noqa: BLE001 — any failure must land on the row
        logger.exception('journal youtube import failed for %s', attachment_id)
        _fail(attachment_id, entry_id, f'{type(e).__name__}: {e}')


def _queue_words(
    attachment_id: str, entry_id: str, directory: Path, title: str, path: str
) -> None:
    """Get a transcript, then a summary of it.

    Captions when the upload has them — near-instant and already punctuated —
    and a transcription pass over the downloaded file when it does not.

    The summarize job is enqueued by whichever branch *produced* the transcript,
    never alongside the transcription: two jobs queued together run in order on
    one worker, and the summarizer would read an empty transcript and write
    nothing.
    """
    from backend.ai import jobs

    subtitle = _pick_subtitle(directory)
    text = ''
    if subtitle is not None:
        try:
            text = vtt_to_text(subtitle.read_text(encoding='utf-8', errors='replace'))
        except Exception as e:  # noqa: BLE001 — fall through to transcribing
            logger.warning('could not read captions for %s: %s', attachment_id, e)

    if text:
        _set(
            attachment_id,
            transcript=text,
            transcript_status='done',
            transcript_error=None,
        )
        _notify(entry_id)
        jobs.enqueue(
            'journal.summarize_youtube', attachment_id,
            {'entry_id': entry_id, 'title': title},
        )
        return

    # No captions: transcribe the audio out of the downloaded container.
    # `into_entry=False` — the video's words are not the user's words, and the
    # entry's text is their commentary.
    _set(attachment_id, transcript_status='running')
    _notify(entry_id)
    jobs.enqueue(
        'journal.transcribe_attachment', attachment_id,
        {
            'entry_id': entry_id, 'kind': 'youtube', 'path': path,
            'name': title or 'Video', 'into_entry': False,
        },
    )


def start_import_bg(attachment_id: str, entry_id: str, url: str) -> None:
    threading.Thread(
        target=import_youtube, args=(attachment_id, entry_id, url), daemon=True
    ).start()
