"""Which stretch of HealthKit sleep is "the night" that ends a given day key.

A day key's window starts at 04:00 (backend/day_boundary.py), and that
boundary is the anchor: the night belonging to day D is the main sleep whose
midpoint lies within eight hours either side of D's 04:00. That one rule covers
an early sleeper (21:00-05:00, midpoint 01:00), a late one (03:00-11:00,
midpoint 07:00) and keeps an afternoon nap (midpoint 15:00) out -- which
matters most on the nights the Watch wasn't worn, when the nap would otherwise
be the only candidate and become the day's wake time.

Day D's wake is the end of D's night, and D's bedtime is the *start* of D+1's
night, so a night is found once and serves both ends it touches.

Three details:

- **Asleep stages beat "in bed".** The Watch writes stages (core/deep/REM, or
  plain "asleep" on older models); the phone's sleep schedule writes only "in
  bed". In-bed is used only for a night with no asleep samples at all, because
  lying in bed with a phone is exactly what the activity-derived times already
  measure.
- **Short wakes don't split a night.** Asleep samples are merged across gaps of
  up to `MERGE_GAP`, so a 3am bathroom trip is one night, not two.
- **Every source is merged.** The iPhone and the Watch can both write sleep for
  the same hours; overlapping intervals collapse in the merge rather than being
  counted twice.
"""
from backend.day_boundary import day_bounds
from backend.db.connection import get_db

SLEEP_TYPE = 'HKCategoryTypeIdentifierSleepAnalysis'
IN_BED = 0
AWAKE = 2
# asleepUnspecified, asleepCore, asleepDeep, asleepREM
ASLEEP = frozenset({1, 3, 4, 5})

MERGE_GAP = 90 * 60
# A night shorter than this is a nap, or a Watch that slipped off.
MIN_NIGHT = 90 * 60
# Midpoint must fall within this far of the day's 04:00 boundary.
MIDPOINT_REACH = 8 * 3600
# How far around the boundary to read samples: a 16-hour night centred at the
# edge of the reach still fits.
READ_MARGIN = 16 * 3600


def merge(intervals: list[tuple[float, float]], gap: float = MERGE_GAP) -> list[tuple[float, float, float]]:
    """Collapse overlapping/near intervals into sessions of (start, end, slept),
    where `slept` counts only covered time -- the gaps bridged are not sleep."""
    sessions: list[list[float]] = []
    for start, end in sorted(intervals):
        if sessions and start <= sessions[-1][1] + gap:
            current = sessions[-1]
            # Only the part past the current end is new sleep.
            current[2] += max(0.0, end - max(start, current[1]))
            current[1] = max(current[1], end)
        else:
            sessions.append([start, end, end - start])
    return [(s, e, slept) for s, e, slept in sessions]


def pick_night(samples: list[tuple[float, float, int]], boundary: float) -> tuple[float, float] | None:
    """The main night around one 04:00 boundary, from (start, end, value) sleep
    samples. None when nothing qualifies -- not "slept zero hours"."""
    asleep = [(s, e) for s, e, v in samples if v in ASLEEP]
    chosen = asleep or [(s, e) for s, e, v in samples if v == IN_BED]
    best = None
    for start, end, slept in merge(chosen):
        if slept < MIN_NIGHT:
            continue
        midpoint = (start + end) / 2
        if not boundary - MIDPOINT_REACH <= midpoint < boundary + MIDPOINT_REACH:
            continue
        if best is None or slept > best[2]:
            best = (start, end, slept)
    return (best[0], best[1]) if best else None


def night_for_day(day_key: str) -> tuple[int, int] | None:
    """The night that ended as day `day_key` began, as whole unix seconds."""
    boundary = day_bounds(day_key)[0]
    rows = get_db().execute(
        'SELECT start_ts, end_ts, value FROM health_samples'
        # The lower start bound is only there so the (type, start_ts) index
        # can be used; no single sleep sample runs for a day.
        ' WHERE type = ? AND start_ts > ? AND start_ts < ? AND end_ts > ?',
        (SLEEP_TYPE, boundary - READ_MARGIN - 86400, boundary + READ_MARGIN, boundary - READ_MARGIN),
    ).fetchall()
    samples = [(r['start_ts'], r['end_ts'], int(r['value'])) for r in rows if r['value'] is not None]
    night = pick_night(samples, boundary)
    return (int(night[0]), int(night[1])) if night else None
