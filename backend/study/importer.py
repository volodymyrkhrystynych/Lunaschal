"""Study source imports: archive a web page, or pull a YouTube video down.

Progress is tracked in an in-memory registry, the same pattern as
backend/fanfic/download.py and the curated-tags scan; the persisted
`import_status` is what survives a restart, and connection.py's
`_reset_stale_study_imports` is what un-sticks a row whose thread died.

PDFs are not here: an upload is written to disk synchronously by the route,
because there is nothing to wait for.
"""
import json
import logging
import shutil
import subprocess
import threading
import time

import nh3

from backend.db.connection import get_db
from backend.htmltext import strip_html_with_title
from backend.research.web import UnsafeUrl, assert_public_url, fetch_public_page
from backend.study import storage, youtube

logger = logging.getLogger(__name__)

# Bigger than web.py's model-facing cap: this is an archive the user will read,
# not a snippet fed to a context window.
MAX_PAGE_BYTES = 5_000_000
_HTML_TYPES = ('text/html', 'application/xhtml+xml')

# yt-dlp is a system binary (as pdftotext is for backend/jobs/resume_review.py)
# rather than a Python dependency, so a box without it degrades to a clear
# error instead of failing to import the module.
YTDLP_METADATA_TIMEOUT = 60
YTDLP_DOWNLOAD_TIMEOUT = 30 * 60
# 1080p ceiling: this is study material on a half-width pane, and the 4K
# variant of a two-hour lecture is gigabytes for no visible gain.
YTDLP_FORMAT = 'bv*[height<=1080]+ba/b[height<=1080]/b'

_progress: dict[str, dict] = {}
_lock = threading.Lock()


# --- progress registry ---

def get_progress(source_id: str) -> dict | None:
    with _lock:
        p = _progress.get(source_id)
        return dict(p) if p else None


def is_active(source_id: str) -> bool:
    with _lock:
        p = _progress.get(source_id)
        return bool(p and not p.get('done'))


def start_progress(source_id: str, phase: str) -> None:
    with _lock:
        _progress[source_id] = {'phase': phase, 'error': None, 'done': False}


def _update_progress(source_id: str, **kw) -> None:
    with _lock:
        if source_id in _progress:
            _progress[source_id].update(kw)


def cancel_progress(source_id: str) -> None:
    with _lock:
        _progress.pop(source_id, None)


def _cancelled(source_id: str) -> bool:
    """Cancellation is absence from the registry, same as the fic downloader."""
    with _lock:
        return source_id not in _progress


# --- shared row bookkeeping ---

def _fail(source_id: str, message: str) -> None:
    get_db().execute(
        "UPDATE study_sources SET import_status='error', import_error=?, updated_at=?"
        ' WHERE id = ?',
        (message[:2000], int(time.time()), source_id),
    )
    get_db().commit()
    _update_progress(source_id, phase='error', error=message[:2000], done=True)


def _finish(source_id: str, **columns) -> None:
    columns['import_status'] = 'ready'
    columns['import_error'] = None
    columns['updated_at'] = int(time.time())
    assignments = ', '.join(f'{col} = ?' for col in columns)
    get_db().execute(
        f'UPDATE study_sources SET {assignments} WHERE id = ?',
        (*columns.values(), source_id),
    )
    get_db().commit()
    _update_progress(source_id, phase='done', done=True)


# --- web ---

def import_web(source_id: str, url: str) -> None:
    """Archive a public web page as sanitized HTML under the source's dir."""
    try:
        _update_progress(source_id, phase='fetching')
        final_url, body = fetch_public_page(
            url, allowed_types=_HTML_TYPES, max_bytes=MAX_PAGE_BYTES
        )
        if _cancelled(source_id):
            return

        _update_progress(source_id, phase='saving')
        # The title comes from the raw body: nh3 drops <head> along with
        # everything else outside the allowed tag set, so reading it after
        # sanitizing would always find nothing.
        _, title = strip_html_with_title(body)
        clean = sanitize_page_html(body)

        path = storage.source_file_path(source_id, 'article', 'html')
        if path is None:
            _fail(source_id, 'Could not build a storage path for this source.')
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(clean, encoding='utf-8')

        columns = {
            'file_path': str(path),
            'content_type': 'text/html',
            'size_bytes': path.stat().st_size,
            'source_url': final_url,
        }
        if title:
            columns['title'] = title
        _finish(source_id, **columns)
    except UnsafeUrl as e:
        _fail(source_id, str(e))
    except Exception as e:  # noqa: BLE001 — any failure must land on the row
        logger.exception('study web import failed for %s', source_id)
        _fail(source_id, f'{type(e).__name__}: {e}')


# Wider than backend/fanfic/sanitize.py's chapter set — an archived article is
# a whole page, so its headings, figures and tables are the content. Still no
# script/style/iframe/form, and still rendered into a sandboxed iframe on top.
_ALLOWED_TAGS = {
    'a', 'abbr', 'article', 'aside', 'b', 'blockquote', 'br', 'caption',
    'cite', 'code', 'dd', 'del', 'div', 'dl', 'dt', 'em', 'figcaption',
    'figure', 'footer', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr',
    'i', 'img', 'ins', 'li', 'main', 'mark', 'nav', 'ol', 'p', 'pre', 'q',
    's', 'section', 'small', 'span', 'strong', 'sub', 'sup', 'table', 'tbody',
    'td', 'tfoot', 'th', 'thead', 'time', 'tr', 'u', 'ul',
}

_ALLOWED_ATTRS = {
    'a': {'href', 'title'},
    'img': {'src', 'alt', 'title', 'width', 'height'},
    'td': {'colspan', 'rowspan'},
    'th': {'colspan', 'rowspan'},
    'ol': {'start'},
    'time': {'datetime'},
}

_CLEAN_CONTENT_TAGS = {'noscript', 'script', 'style'}


def sanitize_page_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        url_schemes={'http', 'https'},
        link_rel='noopener noreferrer',
    )


# --- youtube ---

def _run_ytdlp(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Wrapped so tests can stub one function instead of subprocess itself."""
    exe = shutil.which('yt-dlp')
    if exe is None:
        raise RuntimeError('yt-dlp is not installed on this machine.')
    return subprocess.run(
        [exe, *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def _ytdlp_error(proc: subprocess.CompletedProcess) -> str:
    tail = (proc.stderr or proc.stdout or '').strip().splitlines()
    return tail[-1][:500] if tail else f'yt-dlp exited {proc.returncode}'


def import_youtube(source_id: str, url: str) -> None:
    """Download one YouTube video into the source's dir via yt-dlp."""
    try:
        assert_public_url(url)
        video_id = youtube.parse_video_id(url)
        if video_id is None:
            _fail(source_id, 'That does not look like a YouTube video URL.')
            return
        # Normalized, so a `&list=` on the pasted URL can't turn this into a
        # playlist download behind --no-playlist's back.
        target = youtube.watch_url(video_id)

        directory = storage.source_dir(source_id)
        if directory is None:
            _fail(source_id, 'Could not build a storage path for this source.')
            return
        directory.mkdir(parents=True, exist_ok=True)

        _update_progress(source_id, phase='metadata')
        meta_proc = _run_ytdlp(
            ['-J', '--no-playlist', '--no-warnings', target], YTDLP_METADATA_TIMEOUT
        )
        if meta_proc.returncode != 0:
            _fail(source_id, _ytdlp_error(meta_proc))
            return
        try:
            meta = json.loads(meta_proc.stdout)
        except ValueError:
            meta = {}
        title = (meta.get('title') or '').strip()
        duration = meta.get('duration')
        duration = int(duration) if isinstance(duration, (int, float)) else None

        # Title first, so the library shows what is downloading rather than a
        # bare URL for however many minutes the download takes.
        if title:
            get_db().execute(
                'UPDATE study_sources SET title = ?, duration_seconds = ?, updated_at = ?'
                ' WHERE id = ?',
                (title, duration, int(time.time()), source_id),
            )
            get_db().commit()

        if _cancelled(source_id):
            return

        _update_progress(source_id, phase='downloading')
        dl_proc = _run_ytdlp(
            [
                '--no-playlist', '--no-warnings', '--no-part',
                '-f', YTDLP_FORMAT,
                '--merge-output-format', 'mp4',
                '-o', str(directory / 'video.%(ext)s'),
                target,
            ],
            YTDLP_DOWNLOAD_TIMEOUT,
        )
        if dl_proc.returncode != 0:
            _fail(source_id, _ytdlp_error(dl_proc))
            return

        # yt-dlp picks the container, so find what it actually wrote rather
        # than assuming --merge-output-format applied (it only does when a
        # merge was needed). `stem == 'video'` rather than a bare glob: a
        # leftover per-format fragment is named `video.f137.mp4`, which globs
        # and *sorts before* the merged `video.mp4`.
        written = sorted(
            p for p in directory.glob('video.*')
            if p.stem == 'video' and p.suffix.lower().lstrip('.') in storage.STORED_EXTS
        )
        if not written:
            _fail(source_id, 'yt-dlp reported success but wrote no playable file.')
            return
        path = written[0]

        _finish(
            source_id,
            file_path=str(path),
            content_type=storage.mimetype_for(path),
            size_bytes=path.stat().st_size,
            duration_seconds=duration,
            **({'title': title} if title else {}),
        )
    except UnsafeUrl as e:
        _fail(source_id, str(e))
    except subprocess.TimeoutExpired:
        _fail(source_id, 'yt-dlp timed out.')
    except Exception as e:  # noqa: BLE001 — any failure must land on the row
        logger.exception('study youtube import failed for %s', source_id)
        _fail(source_id, f'{type(e).__name__}: {e}')
