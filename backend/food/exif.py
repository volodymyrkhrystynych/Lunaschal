"""Best-effort EXIF extraction for uploaded photos.

It lives under `food/` because the food log needed it first, but it is shared:
journal attachments read it too, and the two ask different questions of it.

`extract_photo_meta` answers when and where — the food log dates and locates a
meal by the photo rather than by the moment of upload, so an older picture still
lands on the right day. `extract_exif_block` keeps everything else the camera
wrote, for a journal attachment where the metadata is part of the record.

Everything degrades to None on any failure — a stripped image, an unreadable
HEIC (needs pillow-heif), or a file with no EXIF — so an upload is never blocked
by missing metadata.
"""
import json
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image

# EXIF tag numbers (used directly to stay independent of Pillow's enum layout).
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_DATETIME = 0x0132  # top-level DateTime
_DATETIME_ORIGINAL = 0x9003
_DATETIME_DIGITIZED = 0x9004
# GPS sub-tags
_GPS_LAT_REF = 1
_GPS_LAT = 2
_GPS_LON_REF = 3
_GPS_LON = 4


def extract_photo_meta(path: Path | str) -> dict:
    """Return {taken_at: int|None, latitude: float|None, longitude: float|None}."""
    result = {'taken_at': None, 'latitude': None, 'longitude': None}
    try:
        with Image.open(path) as img:
            exif = img.getexif()
    except Exception:
        return result
    if not exif:
        return result

    result['taken_at'] = _read_taken_at(exif)
    lat, lon = _read_gps(exif)
    result['latitude'] = lat
    result['longitude'] = lon
    return result


def _read_taken_at(exif) -> int | None:
    dt_str = None
    try:
        sub = exif.get_ifd(_EXIF_IFD)
        dt_str = sub.get(_DATETIME_ORIGINAL) or sub.get(_DATETIME_DIGITIZED)
    except Exception:
        pass
    if not dt_str:
        dt_str = exif.get(_DATETIME)
    return _parse_exif_dt(dt_str) if isinstance(dt_str, str) else None


def _parse_exif_dt(s: str) -> int | None:
    """EXIF timestamps are 'YYYY:MM:DD HH:MM:SS' with no timezone; interpret as
    local time (matching how the app reasons about journal days)."""
    try:
        dt = datetime.strptime(s.strip(), '%Y:%m:%d %H:%M:%S')
    except (ValueError, AttributeError):
        return None
    try:
        return int(dt.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _read_gps(exif) -> tuple[float | None, float | None]:
    try:
        gps = exif.get_ifd(_GPS_IFD)
    except Exception:
        return None, None
    if not gps:
        return None, None
    lat = _to_degrees(gps.get(_GPS_LAT))
    lon = _to_degrees(gps.get(_GPS_LON))
    if lat is None or lon is None:
        return None, None
    if str(gps.get(_GPS_LAT_REF, 'N')).upper().startswith('S'):
        lat = -lat
    if str(gps.get(_GPS_LON_REF, 'E')).upper().startswith('W'):
        lon = -lon
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return round(lat, 6), round(lon, 6)


def _to_degrees(value) -> float | None:
    """Convert an EXIF (deg, min, sec) rational triple to signed decimal degrees."""
    try:
        d, m, s = value
        return float(d) + float(m) / 60.0 + float(s) / 3600.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# --- Full EXIF block ----------------------------------------------------------
#
# `extract_photo_meta` above answers the two questions a *log row* asks of a
# photo: when and where. Everything else the camera wrote — body, lens,
# exposure, orientation — was read and thrown away. For a journal attachment
# that metadata is part of the record, so `extract_exif_block` keeps the lot.
#
# It is deliberately not a curated allowlist. A field nobody thought to name is
# exactly the one worth having later, so every readable tag is kept under its
# standard EXIF name and the filtering is about *representability*, not about
# interest: values that cannot survive a round trip through JSON are dropped,
# and the whole block is capped so one pathological file cannot write a
# megabyte into the row.

# MakerNote is a vendor blob, often kilobytes, and its contents are undocumented
# per-vendor binary. The two thumbnail pointers describe a JPEG embedded in the
# file, which we already have. None of the three survives JSON meaningfully.
_SKIP_TAGS = frozenset({
    'MakerNote', 'UserComment', 'JPEGInterchangeFormat',
    'JPEGInterchangeFormatLength', 'PrintImageMatching',
})

# A cap per value and for the block as a whole. Both are generous for real
# camera metadata (a full block off an iPhone is ~2 KB) and both exist only to
# bound the damage from a file whose tags are junk.
_MAX_VALUE_CHARS = 512
_MAX_BLOCK_CHARS = 16_384


def extract_exif_block(path: Path | str) -> dict | None:
    """Every readable EXIF tag, keyed by standard name, or None.

    Returns None — not an empty dict — when the file has no EXIF at all, so a
    caller can tell "no metadata" from "metadata that happened to be empty".
    Image dimensions are always included when the file opens, since they are a
    property of the picture rather than of the camera and are present even for
    an image whose EXIF was stripped.
    """
    block: dict = {}
    try:
        with Image.open(path) as img:
            block['ImageWidth'], block['ImageHeight'] = img.size
            exif = img.getexif()
    except Exception:
        return None

    if exif:
        block.update(_named_tags(exif, ExifTags.TAGS))
        try:
            block.update(_named_tags(exif.get_ifd(_EXIF_IFD), ExifTags.TAGS))
        except Exception:
            pass
        try:
            gps = _named_tags(exif.get_ifd(_GPS_IFD), ExifTags.GPSTAGS)
        except Exception:
            gps = {}
        if gps:
            block['GPS'] = gps

    if len(block) <= 2 and 'GPS' not in block:
        # Width and height alone are not metadata the photo carried.
        return None
    return _fit_to_cap(block)


def _named_tags(ifd, names: dict) -> dict:
    out: dict = {}
    if not ifd:
        return out
    for tag, value in ifd.items():
        name = names.get(tag)
        if name is None or name in _SKIP_TAGS:
            continue
        coerced = _jsonable(value)
        if coerced is not None:
            out[name] = coerced
    return out


def _jsonable(value):
    """A JSON-safe rendering of one EXIF value, or None to drop it.

    Pillow hands back `IFDRational` for every fractional tag (exposure, f-number,
    focal length) and raw `bytes` for anything it could not decode. The first
    becomes a float, the second is dropped: a byte string that is not text is
    not something a reader of this record can use, and guessing an encoding
    would put mojibake in the row instead.
    """
    if isinstance(value, (bytes, bytearray)):
        return None
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN/inf are valid floats and invalid JSON.
        return value if value == value and abs(value) != float('inf') else None
    if isinstance(value, str):
        cleaned = value.replace('\x00', '').strip()
        return cleaned[:_MAX_VALUE_CHARS] or None
    if isinstance(value, (tuple, list)):
        items = [_jsonable(v) for v in value]
        items = [v for v in items if v is not None]
        return items or None
    # IFDRational and anything else numeric-ish.
    try:
        num = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return num if num == num and abs(num) != float('inf') else None


def _fit_to_cap(block: dict) -> dict:
    """Drop the largest tags until the block serializes under the cap.

    Dropping rather than truncating the JSON text: a truncated blob is not
    parseable, and a row that cannot be read back is worse than one missing a
    tag nobody asked for.
    """
    try:
        if len(json.dumps(block)) <= _MAX_BLOCK_CHARS:
            return block
    except (TypeError, ValueError):
        return {k: v for k, v in block.items() if k in ('ImageWidth', 'ImageHeight')}
    ordered = sorted(block.items(), key=lambda kv: -len(repr(kv[1])))
    trimmed = dict(block)
    for name, _ in ordered:
        if name in ('ImageWidth', 'ImageHeight'):
            continue
        trimmed.pop(name, None)
        if len(json.dumps(trimmed)) <= _MAX_BLOCK_CHARS:
            break
    return trimmed
