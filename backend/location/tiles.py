"""Map tiles for the daily trail picture, fetched once and kept on disk.

OpenStreetMap's tile servers are free to use on three conditions
(https://operations.osmfoundation.org/policies/tiles/): an identifying
User-Agent, caching rather than refetching, and the attribution on every map.
This module does the first two; render.py draws the third. One user drawing
one picture a day, mostly over the same streets, is the light use the policy
exists to allow -- and after the first week nearly every tile is a cache hit.

`LOCATION_TILE_URL` points at another server with the same `{z}/{x}/{y}`
scheme, or, set empty, turns tiles off. With no tile the picture still draws,
on a plain background: a map that couldn't reach the internet is still a
record of the day. Tests and `./test-env.sh` run with tiles off.
"""
import io
import logging
import os
import time
from pathlib import Path

import requests
from PIL import Image

from backend.location.storage import location_root

log = logging.getLogger(__name__)

DEFAULT_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
USER_AGENT = 'Lunaschal/1.0 (self-hosted personal journal; one user, cached daily map)'
# OSM's minimum cache lifetime is 7 days; streets change slower than that.
MAX_AGE_S = 30 * 86400
TIMEOUT_S = 10
# A failure is remembered for a while, so a picture drawn offline doesn't spend
# ten seconds per tile timing out -- and doesn't retry them all on every view.
FAILURE_BACKOFF_S = 15 * 60
_failed: dict[tuple[int, int, int], float] = {}


def tile_url() -> str:
    return os.environ.get('LOCATION_TILE_URL', DEFAULT_URL)


def _path(z: int, x: int, y: int) -> Path:
    return location_root() / 'tiles' / str(z) / str(x) / f'{y}.png'


def fetch_tile(z: int, x: int, y: int) -> Image.Image | None:
    """The tile as an RGB image, from the cache if fresh, else the server.
    None when tiles are off or the tile can't be had; never raises."""
    url = tile_url()
    if not url:
        return None
    path = _path(z, x, y)
    try:
        if path.is_file() and time.time() - path.stat().st_mtime < MAX_AGE_S:
            return Image.open(path).convert('RGB')
    except OSError:
        pass
    key = (z, x, y)
    if time.time() - _failed.get(key, 0) < FAILURE_BACKOFF_S:
        return _stale(path)
    try:
        resp = requests.get(url.format(z=z, x=x, y=y), headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT_S)
        resp.raise_for_status()
        image = Image.open(io.BytesIO(resp.content)).convert('RGB')
    except (requests.RequestException, OSError) as e:
        log.info('map tile %s/%s/%s unavailable: %s', z, x, y, e)
        _failed[key] = time.time()
        return _stale(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(resp.content)
        tmp.replace(path)
    except OSError as e:
        log.warning('could not cache map tile %s: %s', path, e)
    return image


def _stale(path: Path) -> Image.Image | None:
    """An expired cached tile beats no tile when the server can't be reached."""
    try:
        return Image.open(path).convert('RGB') if path.is_file() else None
    except OSError:
        return None
