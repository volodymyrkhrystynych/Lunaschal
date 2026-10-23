"""Where a day was spent, as far as Home and Work can say: pure, DB-free.

Two kinds of stay feed this. iOS's own visits ("you stayed here", trail.places
keeps the ones long enough to count) are matched to the role place in force
that day whose radius they fall inside, nearest winning when radii overlap.
Geofence stays ('region' points, the phone monitoring Home and each Work) name
their place outright and are exact to the minute where visits can be late.

The two overlap all the time — the same morning at the office is usually both
a visit and a region stay — so time is never summed per stay. Each place's
stays are merged into intervals first, then clipped to the 4am day, so an
overnight stay at home counts only the part of the night this day owns.

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
    kind: str = 'visit'


@dataclass(frozen=True)
class Interval:
    """A run of merged stays at one role place."""
    place: RolePlace
    start: float
    end: float | None  # None while still there

    def clipped(self, bounds: tuple[int, int], now: float) -> tuple[float, float]:
        start, end = bounds
        return max(self.start, start), min(self.end if self.end is not None else now, end)


def iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def visits(fixes: list[trail.Fix], now: float) -> list[trail.Fix]:
    """The day's stays worth counting. Only the accuracy ceiling is applied,
    not trail.clean's spike filter: that judges a fix by its neighbours, and a
    stays-only list (the Journal's all-days query) has different neighbours
    than the full day, so the two paths would disagree about the same visit."""
    kept = [f for f in fixes if f.kind in trail.STAY_KINDS
            and (f.accuracy is None or f.accuracy <= trail.MAX_ACCURACY_M[f.kind])]
    return sorted(trail.places(kept, now), key=lambda f: f.ts)


def match(fix: trail.Fix, places: list[RolePlace]) -> RolePlace | None:
    if fix.place_id is not None:
        # A geofence stay names its place. One that is not a role place on this
        # day (a Work that ended last month, still monitored) matches nothing.
        return next((p for p in places if p.place_id == fix.place_id), None)
    best = None
    for place in places:
        d = trail.distance_m(fix, trail.Fix(0, place.lat, place.lon))
        if d <= place.radius_m and (best is None or d < best[0]):
            best = (d, place)
    return best[1] if best else None


def stays(fixes: list[trail.Fix], places: list[RolePlace], now: float) -> list[Stay]:
    return [Stay(f.ts, f.departed, f.lat, f.lon, match(f, places), f.kind) for f in visits(fixes, now)]


# Two stays at one place this close together are one stay: stepping out for
# lunch is not leaving work.
MERGE_GAP_S = 15 * 60


def intervals(stays_: list[Stay]) -> list[Interval]:
    """Each role place's stays merged into continuous intervals, in time order."""
    by_place: dict[str, list[Stay]] = {}
    for s in stays_:
        if s.place is not None:
            by_place.setdefault(s.place.place_id, []).append(s)
    out: list[Interval] = []
    for group in by_place.values():
        group.sort(key=lambda s: s.arrived)
        cur: Interval | None = None
        for s in group:
            if cur is not None and (cur.end is None or s.arrived <= cur.end + MERGE_GAP_S):
                end = None if cur.end is None or s.departed is None else max(cur.end, s.departed)
                cur = Interval(cur.place, cur.start, end)
            else:
                if cur is not None:
                    out.append(cur)
                cur = Interval(s.place, s.arrived, s.departed)
        if cur is not None:
            out.append(cur)
    return sorted(out, key=lambda i: i.start)


def summarize(stays_: list[Stay], bounds: tuple[int, int], now: float) -> dict | None:
    """{home, work} for the card, or None when no stay was at a role place.

    home: {seconds, lastArrival, here} — lastArrival is when the user last got
    home that day, `here` whether they still are. work: one entry per Work
    place, by time spent.
    """
    home = None
    work: dict[str, dict] = {}
    for i in intervals(stays_):
        a, d = i.clipped(bounds, now)
        seconds = max(0, int(d - a))
        if i.place.role == 'home':
            home = home or {'seconds': 0, 'lastArrival': None, 'here': False}
            home['seconds'] += seconds
            home['lastArrival'] = iso(i.start)
            home['here'] = i.end is None
        else:
            w = work.setdefault(i.place.place_id, {'name': i.place.name, 'seconds': 0,
                                                   'firstArrival': iso(i.start), 'lastDeparture': None})
            w['seconds'] += seconds
            w['lastDeparture'] = iso(i.end)
    if home is None and not work:
        return None
    return {'home': home, 'work': sorted(work.values(), key=lambda w: -w['seconds'])}


def describe(stays_: list[Stay]) -> list[dict]:
    """Every stay, labelled — what the briefing reads."""
    return [{'arrived': datetime.fromtimestamp(s.arrived).isoformat(),
             'departed': datetime.fromtimestamp(s.departed).isoformat() if s.departed is not None else None,
             'role': s.place.role if s.place else None,
             'place': s.place.name if s.place else None,
             'source': 'geofence' if s.kind == 'region' else 'visit',
             'latitude': round(s.lat, 5), 'longitude': round(s.lon, 5)}
            for s in stays_]
