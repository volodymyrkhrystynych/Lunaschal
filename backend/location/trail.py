"""What a day's raw fixes mean as a drawing: pure, DB-free, model-free.

The phone records in two modes (apple/LunaschalCore's LocationTrail.swift):
**fine** -- live GPS while walking, running or cycling -- and **coarse** -- iOS
visits and significant-change wakes the rest of the time. So a day's trail is a
few dense, trustworthy stretches joined by sparse points hundreds of metres
apart, and drawing it honestly means telling the two apart: a fine stretch is
a route, the line between two coarse points is only "got from here to there".
"""
import math
from dataclasses import dataclass

# Past these, a fix says less about where you were than the map's own detail.
MAX_ACCURACY_M = {'fine': 75.0, 'coarse': 1000.0, 'visit': 1000.0, 'region': 5000.0}
# Two fine fixes further apart in time than this weren't one continuous walk:
# the phone went coarse, or lost the sky, in between.
TRACK_GAP_S = 5 * 60
# Faster than this between neighbours is a bad fix rather than a journey --
# 250 km/h is past anything on the ground, and a plane trip is a jump anyway.
SPIKE_SPEED_MPS = 70.0
# A visit shorter than this is a traffic light, not a place.
MIN_DWELL_S = 5 * 60

_EARTH_M = 6_371_000.0


@dataclass(frozen=True)
class Fix:
    ts: float
    lat: float
    lon: float
    kind: str = 'fine'
    accuracy: float | None = None
    departed: float | None = None
    # A geofence ('region') stay names the saved place it was at; Core Motion's
    # activity rides on any fix the phone took while it knew one.
    place_id: str | None = None
    activity: str | None = None


@dataclass(frozen=True)
class Segment:
    a: Fix
    b: Fix
    # 'track' is a recorded route; 'jump' is a straight line standing in for a
    # route nobody recorded.
    style: str


def distance_m(a: Fix, b: Fix) -> float:
    """Haversine distance in metres."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_M * math.asin(min(1.0, math.sqrt(h)))


def _speed(a: Fix, b: Fix) -> float:
    return distance_m(a, b) / max(abs(b.ts - a.ts), 1.0)


def clean(fixes: list[Fix]) -> list[Fix]:
    """Time-ordered fixes with the untrustworthy ones dropped.

    Two filters: an accuracy ceiling per kind, and a spike filter -- a single
    fix that is impossibly fast to reach *and* to leave again, while its two
    neighbours are close to each other, is the GPS jumping, not you.
    """
    kept = [f for f in sorted(fixes, key=lambda f: f.ts)
            if f.accuracy is None or f.accuracy <= MAX_ACCURACY_M.get(f.kind, 0.0)]
    out: list[Fix] = []
    for i, fix in enumerate(kept):
        if out and i + 1 < len(kept):
            prev, nxt = out[-1], kept[i + 1]
            if (_speed(prev, fix) > SPIKE_SPEED_MPS and _speed(fix, nxt) > SPIKE_SPEED_MPS
                    and distance_m(prev, nxt) < distance_m(prev, fix) / 2):
                continue
        out.append(fix)
    return out


def segments(fixes: list[Fix]) -> list[Segment]:
    """Each consecutive pair of cleaned fixes, labelled track or jump."""
    out = []
    for a, b in zip(fixes, fixes[1:]):
        track = a.kind == 'fine' and b.kind == 'fine' and b.ts - a.ts <= TRACK_GAP_S
        out.append(Segment(a, b, 'track' if track else 'jump'))
    return out


STAY_KINDS = ('visit', 'region')


def places(fixes: list[Fix], now: float) -> list[Fix]:
    """Stays long enough to count as somewhere you were: iOS visits, and
    geofence stays at a saved place. One still in progress has no departure
    yet and is measured up to now."""
    return [f for f in fixes
            if f.kind in STAY_KINDS and (f.departed if f.departed is not None else now) - f.ts >= MIN_DWELL_S]


def tracked_m(fixes: list[Fix]) -> float:
    """Distance along recorded routes only. Jumps are left out: a straight
    line across a city says nothing about how far you went."""
    return sum(distance_m(s.a, s.b) for s in segments(fixes) if s.style == 'track')
