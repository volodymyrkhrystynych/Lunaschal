"""What an uploaded video says about itself: its length, when and where it was
shot, and a poster frame to draw the card with.

All of it comes from ffprobe/ffmpeg and all of it is best-effort — a video with
no metadata, an ffmpeg that is not installed, or a container ffprobe cannot read
leaves the fields empty and the upload succeeds anyway. The poster is what turns
the iPhone feed's black box into a picture; the creation date is what keeps an
old clip from claiming it was shot the moment it was attached.

The parsing is split from the subprocess so it can be tested from canned
ffprobe JSON, without ffmpeg on the machine running the tests.
"""
import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

from backend.geo import coord_pair

logger = logging.getLogger(__name__)

_TIMEOUT = 60

# `+43.6532-079.3832+076.000/` — latitude, longitude, optional altitude, as an
# Apple camera writes it into `com.apple.quicktime.location.ISO6709`.
_ISO6709 = re.compile(r'^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)')

# `2024-05-01T12:34:56-0400`: the offset has no colon, which fromisoformat
# accepts only on newer Pythons, so it is normalized first.
_BASIC_OFFSET = re.compile(r'([+-]\d{2})(\d{2})$')


def parse_probe(data: dict) -> dict:
    """{duration_seconds, captured_at, latitude, longitude} from ffprobe's
    `-show_format -show_streams` JSON. Any of them may be None."""
    fmt = data.get('format') or {}
    tags = {str(k).lower(): v for k, v in (fmt.get('tags') or {}).items()}

    duration = None
    try:
        duration = int(round(float(fmt.get('duration'))))
    except (TypeError, ValueError):
        pass

    # Apple's own key wins: it carries the local offset the clip was shot at,
    # where `creation_time` is UTC — and some editors rewrite `creation_time` to
    # the moment of export, which is exactly the date this exists to keep.
    captured = (
        _parse_when(tags.get('com.apple.quicktime.creationdate'))
        or _parse_when(tags.get('creation_time'))
    )
    if captured is None:
        for stream in data.get('streams') or []:
            stream_tags = {str(k).lower(): v for k, v in (stream.get('tags') or {}).items()}
            captured = _parse_when(stream_tags.get('creation_time'))
            if captured is not None:
                break

    latitude, longitude = _parse_iso6709(
        tags.get('com.apple.quicktime.location.iso6709') or tags.get('location')
    )
    return {
        'duration_seconds': duration,
        'captured_at': captured,
        'latitude': latitude,
        'longitude': longitude,
    }


def _parse_when(value) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    text = _BASIC_OFFSET.sub(r'\1:\2', text)
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        # Without an offset there is no honest instant to store.
        return None
    try:
        ts = int(when.timestamp())
    except (OverflowError, OSError, ValueError):
        return None
    # A camera with no clock set writes 1904-01-01 (the QuickTime epoch) or
    # 1970; neither is a date anything was filmed on.
    return ts if ts > 0 else None


def _parse_iso6709(value) -> tuple[float | None, float | None]:
    if not isinstance(value, str):
        return None, None
    match = _ISO6709.match(value.strip())
    if not match:
        return None, None
    try:
        pair = coord_pair(float(match.group(1)), float(match.group(2)))
    except (TypeError, ValueError):
        return None, None
    return pair if pair else (None, None)


def probe(path: Path | str) -> dict:
    """`parse_probe` over the real file; every field None when ffprobe is
    missing or cannot read it."""
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json',
             '-show_format', '-show_streams', str(path)],
            check=True, capture_output=True, text=True, timeout=_TIMEOUT,
        )
        return parse_probe(json.loads(out.stdout or '{}'))
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.info('ffprobe could not read %s: %s', path, e)
        return parse_probe({})


def make_poster(path: Path | str, out: Path | str) -> bool:
    """Write one frame of the video to `out` as a JPEG, at most 640px wide.

    Half a second in rather than the very first frame, which on a phone clip is
    often black or mid-focus — and then the first frame after all, for a clip
    too short to have a frame at 0.5s. ffmpeg applies the rotation a phone
    records as metadata on its own, so a portrait clip gets a portrait poster.
    """
    out = Path(out)
    for seek in ('0.5', '0'):
        try:
            subprocess.run(
                ['ffmpeg', '-y', '-loglevel', 'error', '-ss', seek, '-i', str(path),
                 '-frames:v', '1', '-vf', "scale='min(640,iw)':-2", '-q:v', '4',
                 str(out)],
                check=True, capture_output=True, timeout=_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.info('poster frame failed for %s: %s', path, e)
            return False
        if out.is_file() and out.stat().st_size > 0:
            return True
    out.unlink(missing_ok=True)
    return False
