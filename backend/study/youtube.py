"""Pure YouTube URL parsing — no network, no DB, so it unit-tests off fixtures.

Kept apart from importer.py for the reason backend/fanfic/xenforo.py is kept
apart from download.py: the parsing is where the bugs are, and it is the half
that can be tested without stubbing a subprocess.
"""
import re
from urllib.parse import parse_qs, urlparse

# YouTube ids are 11 characters of the URL-safe base64 alphabet.
_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')

_HOSTS = {
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'music.youtube.com', 'youtu.be', 'www.youtu.be',
}

# /shorts/<id>, /embed/<id>, /live/<id>, /v/<id> — every path shape that names
# the video in the path rather than in `?v=`.
_PATH_PREFIXES = ('shorts', 'embed', 'live', 'v')


def parse_video_id(url: str) -> str | None:
    """The video id in `url`, or None if it isn't a YouTube video URL.

    Returns None (rather than raising) for anything unrecognised so the caller
    can answer 422 with its own message. A playlist URL with no video in it is
    not a video: the importer takes one video at a time on purpose.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ('http', 'https'):
        return None
    host = (parsed.hostname or '').lower()
    if host not in _HOSTS:
        return None

    segments = [s for s in parsed.path.split('/') if s]

    # youtu.be/<id> — the whole path is the id.
    if host.endswith('youtu.be'):
        candidate = segments[0] if segments else ''
        return candidate if _VIDEO_ID.match(candidate) else None

    if segments and segments[0] == 'watch':
        candidate = (parse_qs(parsed.query).get('v') or [''])[0]
        return candidate if _VIDEO_ID.match(candidate) else None

    if len(segments) >= 2 and segments[0] in _PATH_PREFIXES:
        candidate = segments[1]
        return candidate if _VIDEO_ID.match(candidate) else None

    return None


def watch_url(video_id: str) -> str:
    """The canonical URL for an id, which is what yt-dlp is handed.

    Normalizing means a `&list=…` on the pasted URL cannot turn a single-video
    import into a playlist download behind `--no-playlist`'s back.
    """
    return f'https://www.youtube.com/watch?v={video_id}'


def preferred_sub_lang(meta: dict) -> str | None:
    """The one caption track to ask yt-dlp for, from its `-J` metadata.

    Asking for a *family* (`--sub-langs 'en.*'`) is what earned a
    `HTTP Error 429: Too Many Requests` on 'en-en-GB': YouTube lists the
    auto-translated variants (`en-en-GB`, `en-en-US`, …) beside the real `en`,
    and yt-dlp then fetches every one of them — a dozen caption requests for a
    transcript that only needs one. The metadata pass has already told us which
    tracks exist, so name a single exact language instead.

    Manual captions beat automatic ones (they are punctuated and correct), and
    within each the shortest matching tag wins, so a plain `en` beats `en-orig`
    and `en-GB`. Returns None when the upload has no English track at all, and
    then nothing is requested rather than something that cannot be there.
    """
    for key in ('subtitles', 'automatic_captions'):
        tracks = meta.get(key)
        if not isinstance(tracks, dict):
            continue
        english = [
            lang for lang in tracks
            if isinstance(lang, str)
            and (lang == 'en' or lang.lower().startswith(('en-', 'en_')))
        ]
        if english:
            return sorted(english, key=lambda lang: (len(lang), lang))[0]
    return None
