"""A day's trail as the Journal sees it: the feed's list of days, and the
picture for one of them, drawn on first view and cached until its inputs move.

The picture is derived, never stored as a record. Its file name carries a
*version*, a hash of everything drawn into it (the day's fixes, the journal
entries pinned on it, the renderer itself), so a point uploaded late or an
entry written after the last view changes the URL rather than serving a stale
image under the old one. That is what makes "redrawn if late data arrives"
free: there is no job to schedule and nothing to invalidate.
"""
import hashlib
import logging
import time
from datetime import datetime, timezone

from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import get_db
from backend.journal_moment import journal_moment
from backend.location import render, tiles, trail, whereabouts
from backend.location.storage import location_root, map_path
from backend.place_roles import list_periods, roles_for_day

log = logging.getLogger(__name__)

# Bump when the drawing changes, so every cached map is redrawn on next view.
RENDER_VERSION = 2


def fixes(day: str) -> list[trail.Fix]:
    rows = get_db().execute(
        'SELECT fix_ts, latitude, longitude, kind, accuracy_m, departed_ts FROM location_points'
        ' WHERE day_key=? ORDER BY fix_ts', (day,)).fetchall()
    return [trail.Fix(r['fix_ts'], r['latitude'], r['longitude'], r['kind'], r['accuracy_m'], r['departed_ts'])
            for r in rows]


def _located_entries(start: int | None = None, end: int | None = None):
    sql = ('SELECT id, created_at, updated_at, latitude, longitude FROM journal_entries'
           ' WHERE latitude IS NOT NULL AND longitude IS NOT NULL')
    args: tuple = ()
    if start is not None:
        sql += ' AND created_at >= ? AND created_at < ?'
        args = (start, end)
    return get_db().execute(sql + ' ORDER BY created_at', args).fetchall()


def pins(day: str) -> list[tuple[float, float]]:
    """Where each journal entry written that day was written."""
    return [(r['latitude'], r['longitude']) for r in _located_entries(*day_bounds(day))]


def _version(point_stats: tuple, entries: list, roles: list) -> str:
    # The role places are drawn into the picture (home and work visits are
    # coloured), so a new Home, or a corrected Work radius, is a new picture.
    h = hashlib.sha1(repr((RENDER_VERSION, tuple(point_stats),
                           [(e['id'], e['updated_at'], e['latitude'], e['longitude']) for e in entries],
                           sorted(roles, key=lambda r: r.place_id))).encode())
    return h.hexdigest()[:16]


_DAY_STATS = ('SELECT day_key, COUNT(*) AS n, MAX(received_at) AS received, MAX(fix_ts) AS last_fix,'
              ' COUNT(departed_ts) AS departures,'
              ' SUM(kind = \'visit\' AND COALESCE(departed_ts, ?) - fix_ts >= ?) AS places'
              ' FROM location_points')


def _stats_key(row) -> tuple:
    return (row['n'], row['received'], row['last_fix'], row['departures'])


def version(day: str) -> str | None:
    """The current picture's version, or None when the day has no trail."""
    row = get_db().execute(_DAY_STATS + ' WHERE day_key=? GROUP BY day_key',
                           (time.time(), trail.MIN_DWELL_S, day)).fetchone()
    if row is None:
        return None
    return _version(_stats_key(row), _located_entries(*day_bounds(day)), roles_for_day(list_periods(get_db()), day))


def image_url(day: str, ver: str) -> str:
    return f'/api/location/days/{day}/map.png?v={ver}'


def journal_days() -> list[dict]:
    """One card per day the phone recorded a trail, newest first.

    Only days with a trail: a located entry on its own was already on the map
    the entry's own card shows, and a card for every such day would fill years
    of history the moment this shipped. Each card sits at the last second of
    its day (journal_moment's `unworked_at_day_end`), so it reads as the day's
    summary, above that day's last entry.
    """
    db = get_db()
    now = time.time()
    rows = db.execute(_DAY_STATS + ' GROUP BY day_key ORDER BY day_key DESC',
                      (now, trail.MIN_DWELL_S)).fetchall()
    by_day: dict[str, list] = {}
    for e in _located_entries():
        by_day.setdefault(day_key_for(e['created_at']), []).append(e)
    periods = list_periods(db)
    visits_by_day: dict[str, list] = {}
    for v in db.execute('SELECT day_key, fix_ts, latitude, longitude, kind, accuracy_m, departed_ts'
                        " FROM location_points WHERE kind='visit' ORDER BY fix_ts"):
        visits_by_day.setdefault(v['day_key'], []).append(
            trail.Fix(v['fix_ts'], v['latitude'], v['longitude'], v['kind'], v['accuracy_m'], v['departed_ts']))
    out = []
    for row in rows:
        day = row['day_key']
        entries = by_day.get(day, [])
        roles = roles_for_day(periods, day)
        ver = _version(_stats_key(row), entries, roles)
        stays = whereabouts.stays(visits_by_day.get(day, []), roles, now) if roles else []
        at = journal_moment(None, day_bounds(day)[0], unworked_at_day_end=True)
        out.append({
            'date': day,
            'archivedAt': datetime.fromtimestamp(at, tz=timezone.utc).isoformat(),
            'pointCount': row['n'],
            'placeCount': row['places'] or 0,
            'entryCount': len(entries),
            'imageUrl': image_url(day, ver),
            'whereabouts': whereabouts.summarize(stays, day_bounds(day), now) if stays else None,
        })
    return out


def map_png(day: str) -> tuple[bytes, str] | None:
    """The day's picture and its version, drawn now if not already cached.
    None when the day has no trail."""
    ver = version(day)
    if ver is None:
        return None
    path = map_path(day, ver)
    try:
        return path.read_bytes(), ver
    except FileNotFoundError:
        pass
    now = time.time()
    cleaned = trail.clean(fixes(day))
    roles = roles_for_day(list_periods(get_db()), day)
    places = trail.places(cleaned, now)
    png = render.render(cleaned, trail.segments(cleaned), places,
                        pins(day), tiles.fetch_tile,
                        place_roles=[(m.role if (m := whereabouts.match(p, roles)) else None) for p in places])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(png)
        tmp.replace(path)
        # Older versions of this day are never served again.
        for old in path.parent.glob(f'{day}-*.png'):
            if old != path:
                old.unlink(missing_ok=True)
    except OSError as e:
        log.warning('could not cache day map %s: %s', path, e)
    return png, ver
