"""Where the location feature keeps files: cached map tiles and rendered day
maps, under `LOCATION_ROOT` (default ./data/location/). Both are derived -- a
tile can be refetched and a map redrawn from location_points -- so losing the
directory costs time, never data."""
import os
import re
from pathlib import Path

_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def location_root() -> Path:
    return Path(os.environ.get('LOCATION_ROOT', './data/location')).expanduser().resolve()


def map_path(day: str, version: str) -> Path:
    if not _DAY.match(day) or not re.fullmatch(r'[0-9a-f]{1,40}', version):
        raise ValueError('bad day or version')
    return location_root() / 'maps' / f'{day}-{version}.png'
