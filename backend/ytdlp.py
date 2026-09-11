"""The shared yt-dlp layer: how we invoke it, and what we ask it to download.

Two features pull YouTube videos down — the Study desk's sources
(backend/study/importer.py) and the Journal's video attachments
(backend/journal/youtube_import.py). They differ in where the bytes land and
what happens to them afterwards, but not in *what* they fetch, so the format
selection lives here rather than in either of them. A second copy of
`YTDLP_FORMAT` is a second place for the codec rule below to be got wrong.

yt-dlp is a system binary (as pdftotext is for backend/jobs/resume_review.py)
rather than a Python dependency, so a box without it degrades to a clear error
instead of failing to import the module.
"""
import shutil
import subprocess

YTDLP_METADATA_TIMEOUT = 60
YTDLP_DOWNLOAD_TIMEOUT = 30 * 60

# 720p ceiling, for two independent reasons that happen to agree.
#
# The Study pane is half the window, and on the 12.9" iPad Pro that tab is
# built for that is ~683 CSS pt — 1366 physical px at 2x — so a 1280x720 stream
# is already pixel-matched and 1080p is oversampled for it. The Journal's video
# card is smaller still. Measured on a 25-minute lecture: 720p H.264 is 279 MB
# against 572 MB for 1080p, and these files go to an archive drive that is
# deliberately not backed up but is not infinite either.
#
# Bump this to 1080 if fullscreen playback ever matters more than disk.
YTDLP_MAX_HEIGHT = 720

# H.264 + AAC, explicitly, in preference to whatever "best" happens to be.
#
# Left to itself yt-dlp picks AV1 + Opus for anything modern on YouTube, and
# **Safari cannot decode AV1 without a hardware decoder** — Apple's first is the
# A17 Pro / M3, so every 12.9" iPad Pro (A12Z, M1, M2) fails to play it, with no
# software fallback and no error worth the name: the player just sits there.
# That is the one device the Study tab exists for, so codec compatibility
# outranks compression efficiency here.
#
# The fallbacks descend deliberately: merged avc1+mp4a, then a progressive avc1
# stream, then anything at all within the height cap, then anything. YouTube
# publishes avc1 at every tier up to 1080p, so the first branch nearly always
# wins; the tail exists so an unusual upload still imports rather than failing.
YTDLP_FORMAT = (
    f'bv*[height<={YTDLP_MAX_HEIGHT}][vcodec^=avc1]+ba[acodec^=mp4a]/'
    f'b[height<={YTDLP_MAX_HEIGHT}][vcodec^=avc1]/'
    f'bv*[height<={YTDLP_MAX_HEIGHT}]+ba/'
    f'b[height<={YTDLP_MAX_HEIGHT}]/b'
)


def run_ytdlp(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Wrapped so tests can stub one function instead of subprocess itself."""
    exe = shutil.which('yt-dlp')
    if exe is None:
        raise RuntimeError('yt-dlp is not installed on this machine.')
    return subprocess.run(
        [exe, *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def ytdlp_error(proc: subprocess.CompletedProcess) -> str:
    tail = (proc.stderr or proc.stdout or '').strip().splitlines()
    return tail[-1][:500] if tail else f'yt-dlp exited {proc.returncode}'
