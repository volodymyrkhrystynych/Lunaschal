"""Where the phone was at a moment, from the trail — for things recorded
without coordinates of their own (an entry typed on the desktop, a meal whose
photo lost its GPS), so their weather and place come from where the user
actually was rather than from wherever the app last guessed.

The phone records all day (fine GPS while moving, visits and significant-
change wakes otherwise), so the nearest fix in time is usually minutes away.
Past MAX_GAP_S it says nothing about that moment and the caller gets None.
"""
from backend.location import trail

MAX_GAP_S = 30 * 60
OPEN_STAY_S = 24 * 3600


def fix_near(db, ts: float, max_gap_s: float = MAX_GAP_S) -> tuple[float, float] | None:
    """(lat, lon) of the trustworthy fix closest in time to `ts`, or None.

    A stay that spans the moment (arrived before, departed after or still
    there) is an exact answer whatever its arrival time, so it is checked
    first — a long afternoon at the office has one point, at 09:00.
    """
    # A stay with no departure is only believed for a day after its arrival:
    # one whose exit never reached the server (the app was killed) would
    # otherwise claim every moment after it.
    stay = db.execute(
        "SELECT latitude, longitude FROM location_points WHERE kind IN ('visit','region')"
        ' AND fix_ts <= ? AND COALESCE(departed_ts, fix_ts + ?) >= ? AND fix_ts >= ?'
        " ORDER BY kind = 'region' DESC, fix_ts DESC LIMIT 1",
        (ts, OPEN_STAY_S, ts, ts - 7 * 86400)).fetchone()
    if stay is not None:
        return stay['latitude'], stay['longitude']
    rows = db.execute(
        'SELECT latitude, longitude, kind, accuracy_m, fix_ts FROM location_points'
        ' WHERE fix_ts BETWEEN ? AND ? ORDER BY ABS(fix_ts - ?) LIMIT 20',
        (ts - max_gap_s, ts + max_gap_s, ts)).fetchall()
    for r in rows:
        ceiling = trail.MAX_ACCURACY_M.get(r['kind'], 0.0)
        if r['accuracy_m'] is None or r['accuracy_m'] <= ceiling:
            return r['latitude'], r['longitude']
    return None


def latest_fix(db, since: float) -> tuple[float, float, float] | None:
    """(lat, lon, ts) of the newest trustworthy fix after `since`."""
    for r in db.execute('SELECT latitude, longitude, kind, accuracy_m, fix_ts FROM location_points'
                        ' WHERE fix_ts >= ? ORDER BY fix_ts DESC LIMIT 20', (since,)):
        if r['accuracy_m'] is None or r['accuracy_m'] <= trail.MAX_ACCURACY_M.get(r['kind'], 0.0):
            return r['latitude'], r['longitude'], r['fix_ts']
    return None
