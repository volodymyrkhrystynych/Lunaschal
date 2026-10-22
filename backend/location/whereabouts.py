"""Where a day was spent, as far as Home and Work can say: pure, DB-free.

The trail's visits are iOS's own "you stayed here" records (trail.places
keeps those long enough to count). Each is matched to the role place in force
that day whose radius it falls inside — nearest wins when radii overlap — and
the time inside each is summed, clipped to the 4am day so an overnight stay at
home counts only the part of the night this day owns.

A visit that matches nothing is still a visit; it is reported with no role so
the briefing can see the day had places the user never named.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.location import trail
from backend.place_roles import RolePlace


@dataclass(frozen=True)
class Stay:
    arrived: float
    departed: float | None  # None while still there
    lat: float
    lon: float
    place: RolePlace | None


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def visits(fixes: list[trail.Fix], now: float) -> list[trail.Fix]:
    """The day's visits worth counting. Only the accuracy ceiling is applied,
    not trail.clean's spike filter: that judges a fix by its neighbours, and a
    visit-only list (the Journal's all-days query) has different neighbours
    than the full day, so the two paths would disagree about the same visit."""
    ceiling = trail.MAX_ACCURACY_M['visit']
    kept = [f for f in fixes if f.kind == 'visit' and (f.accuracy is None or f.accuracy <= ceiling)]
    return sorted(trail.places(kept, now), key=lambda f: f.ts)


def match(fix: trail.Fix, places: list[RolePlace]) -> RolePlace | None:
    best = None
    for place in places:
        d = trail.distance_m(fix, trail.Fix(0, place.lat, place.lon))
        if d <= place.radius_m and (best is None or d < best[0]):
            best = (d, place)
    return best[1] if best else None


def stays(fixes: list[trail.Fix], places: list[RolePlace], now: float) -> list[Stay]:
    return [Stay(f.ts, f.departed, f.lat, f.lon, match(f, places)) for f in visits(fixes, now)]


def summarize(stays_: list[Stay], bounds: tuple[int, int], now: float) -> dict | None:
    """{home, work} for the card, or None when no stay was at a role place.

    home: {seconds, lastArrival, here} — lastArrival is when the user last got
    home that day, `here` whether they still are. work: one entry per Work
    place, by time spent.
    """
    start, end = bounds
    home = None
    work: dict[str, dict] = {}
    for s in stays_:
        if s.place is None:
            continue
        a, d = max(s.arrived, start), min(s.departed if s.departed is not None else now, end)
        seconds = max(0, int(d - a))
        if s.place.role == 'home':
            home = home or {'seconds': 0, 'lastArrival': None, 'here': False}
            home['seconds'] += seconds
            home['lastArrival'] = _iso(s.arrived)
            home['here'] = s.departed is None
        else:
            w = work.setdefault(s.place.place_id, {'name': s.place.name, 'seconds': 0,
                                                   'firstArrival': _iso(s.arrived), 'lastDeparture': None})
            w['seconds'] += seconds
            w['lastDeparture'] = _iso(s.departed)
    if home is None and not work:
        return None
    return {'home': home, 'work': sorted(work.values(), key=lambda w: -w['seconds'])}


def describe(stays_: list[Stay]) -> list[dict]:
    """Every stay, labelled — what the briefing reads."""
    return [{'arrived': datetime.fromtimestamp(s.arrived).isoformat(),
             'departed': datetime.fromtimestamp(s.departed).isoformat() if s.departed is not None else None,
             'role': s.place.role if s.place else None,
             'place': s.place.name if s.place else None,
             'latitude': round(s.lat, 5), 'longitude': round(s.lon, 5)}
            for s in stays_]
