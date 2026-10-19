"""A Health workout's 1-5 star intensity, read from what the Watch recorded.

Heart rate first: the workout's average as a share of the highest rate seen
in the 180 days up to it. That ceiling is observed rather than 220 − age —
the app knows no age, and an observed peak tracks this heart rather than a
population formula. With too few heart-rate samples in the window (an
iPhone-only walk, a Watch worn loose) it falls back to active energy per
minute, which says less about effort but is never absent from a Watch
workout. Neither is available: no rating, and the session stays eligible
for the next pass.

The bands are the five labels in `src/lib/lifestyle.ts`: under 55% of peak is
"Not intense whatsoever", 85% and up is the top.
"""
HEART_RATE = 'HKQuantityTypeIdentifierHeartRate'
PEAK_WINDOW_S = 180 * 86400
# Fewer than this and an average is one or two stray readings.
MIN_SAMPLES = 3

_HR_BANDS = (0.55, 0.65, 0.75, 0.85)
# kcal/min of active energy: a stroll is ~3, a run ~11.
_KCAL_BANDS = (3.0, 5.0, 8.0, 11.0)


def _band(value: float, bands) -> int:
    return 1 + sum(value >= edge for edge in bands)


def from_heart_rate(average: float, peak: float) -> int | None:
    if not average or not peak or average <= 0 or peak <= 0:
        return None
    return _band(min(average / peak, 1.0), _HR_BANDS)


def from_energy(kcal: float | None, minutes: float) -> int | None:
    if kcal is None or kcal <= 0 or minutes <= 0:
        return None
    return _band(kcal / minutes, _KCAL_BANDS)


def rate(db, workout) -> int | None:
    """Stars for one `health_workouts` row, or None when nothing says."""
    start, end = workout['start_ts'], workout['end_ts']
    during = db.execute(
        'SELECT AVG(value) AS average, COUNT(*) AS n FROM health_samples'
        ' WHERE type=? AND start_ts>=? AND start_ts<=? AND value>0',
        (HEART_RATE, start, end),
    ).fetchone()
    if during['n'] >= MIN_SAMPLES:
        peak = db.execute(
            'SELECT MAX(value) FROM health_samples WHERE type=? AND start_ts>=? AND start_ts<=?',
            (HEART_RATE, end - PEAK_WINDOW_S, end),
        ).fetchone()[0]
        stars = from_heart_rate(during['average'], peak)
        if stars is not None:
            return stars
    minutes = min(workout['duration_s'], end - start) / 60
    return from_energy(workout['energy_kcal'], minutes)
