"""Commutes: the trips between Home and a Work, read off the trail. Pure.

A commute is the gap between leaving one role place and arriving at the
other — Home to a Work, or a Work to Home — as long as nothing else claimed
that gap (a stop at an unnamed place on the way home makes it two trips, not
one commute). Its route is every fix the phone took inside the gap, from the
place it left to the place it reached.

How the trip was made comes from Core Motion's activity on those fixes, when
the phone recorded it: the mode that covered the most *time* wins, so a short
walk to the car does not turn a drive into a walk. Without activity, average
speed is the fallback, and the result says which of the two it came from.
"""
from backend.location import trail
from backend.location.whereabouts import Interval, intervals, iso

# Longer than this between leaving and arriving is not a commute: the user
# went somewhere first and the trail did not catch the stop.
MAX_COMMUTE_S = 3 * 3600
MOVING = ('walking', 'running', 'cycling', 'automotive')
# Average speed bands (m/s) for the fallback: a brisk walk is ~1.5, a city
# bike ~4-5, anything past ~6.5 is wheels with a motor.
WALK_MAX_MPS = 2.0
CYCLE_MAX_MPS = 6.5


def _mode(path: list[trail.Fix], seconds: float, meters: float) -> tuple[str, str]:
    weight: dict[str, float] = {}
    for a, b in zip(path, path[1:]):
        if a.activity in MOVING:
            weight[a.activity] = weight.get(a.activity, 0.0) + max(0.0, b.ts - a.ts)
    if weight:
        return max(weight, key=weight.get), 'motion'
    speed = meters / seconds if seconds > 0 else 0.0
    if speed <= WALK_MAX_MPS:
        return 'walking', 'speed'
    if speed <= CYCLE_MAX_MPS:
        return 'cycling', 'speed'
    return 'automotive', 'speed'


def _trip(a: Interval, b: Interval, fixes: list[trail.Fix]) -> dict | None:
    if a.end is None:
        return None
    seconds = b.start - a.end
    if not 0 < seconds <= MAX_COMMUTE_S:
        return None
    moving = [f for f in fixes if f.kind not in trail.STAY_KINDS and a.end <= f.ts <= b.start]
    start = trail.Fix(a.end, a.place.lat, a.place.lon)
    end = trail.Fix(b.start, b.place.lat, b.place.lon)
    path = [start, *moving, end]
    meters = sum(trail.distance_m(p, q) for p, q in zip(path, path[1:]))
    # The first and last legs carry the activity of the fix that follows the
    # departure, so give the departure point the first fix's activity.
    if moving:
        path[0] = trail.Fix(start.ts, start.lat, start.lon, activity=moving[0].activity)
    mode, source = _mode(path, seconds, meters)
    return {
        'direction': 'toWork' if b.place.role == 'work' else 'toHome',
        'from': a.place.name, 'to': b.place.name,
        'departed': iso(a.end), 'arrived': iso(b.start),
        'seconds': int(seconds), 'meters': int(meters),
        'mode': mode, 'modeSource': source,
        'tracked': any(f.kind == 'fine' for f in moving),
    }


def commutes(stays, fixes: list[trail.Fix]) -> list[dict]:
    """Every Home↔Work trip in a day, in order. `stays` is
    whereabouts.stays(...) for the same day, so the role places are the ones
    in force that day."""
    spans = intervals(stays)
    out = []
    # Any stay at all (named or not) between two role intervals breaks the
    # trip: that gap was spent somewhere, not travelling.
    unnamed = [s for s in stays if s.place is None]
    for a, b in zip(spans, spans[1:]):
        if {a.place.role, b.place.role} != {'home', 'work'}:
            continue
        if a.end is not None and any(a.end < s.arrived < b.start for s in unnamed):
            continue
        trip = _trip(a, b, fixes)
        if trip:
            out.append(trip)
    return out
