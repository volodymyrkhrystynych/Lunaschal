"""Best-effort EXIF extraction for food-log photos.

When a user attaches an existing photo, the log should be dated and located by
the photo itself, not by the moment/place of upload. We read the capture date
(DateTimeOriginal) and GPS from the image's EXIF. Everything degrades to None on
any failure — a stripped image, an unreadable HEIC (needs pillow-heif), or a
file with no EXIF — so a log is never blocked by missing metadata.
"""
from datetime import datetime
from pathlib import Path

from PIL import Image

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
