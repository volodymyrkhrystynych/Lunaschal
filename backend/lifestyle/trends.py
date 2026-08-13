"""Weekly trend buckets for the Lifestyle tab's momentum chart — no DB, no Flask.

The route counts rows per local calendar day in SQL; this folds those day counts
into fixed-width weeks. Kept pure for the same reason `activity.summarize_day` is:
the bucketing rules (which Monday a day belongs to, what an empty week means) are
where the bugs live, and they're testable without a database.
"""

from datetime import date, timedelta

# Weeks start Monday, matching ISO. The heatmap grids Sunday-first because that's
# GitHub's convention for a day grid; a trend line has no such convention, and a
# week that ends on a weekend reads better for "did I apply to anything".
WEEK_START_WEEKDAY = 0  # Monday, as date.weekday() numbers it


def week_start(day: date) -> date:
    """The Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def weekly_series(
    day_counts: dict[str, dict[str, int]],
    end_date: date,
    weeks: int,
) -> list[dict]:
    """Fold per-day counts into `weeks` consecutive weeks ending with `end_date`'s.

    `day_counts` maps 'YYYY-MM-DD' to a dict of series-name -> count; every key
    that appears anywhere becomes a key on every bucket, so the client never has
    to check for a missing series on a quiet week.

    **Every week is emitted, including the empty ones.** This is the opposite of
    /heatmap, which omits days with nothing logged: there an absent box reads as
    a rest day, but a line chart that skips its zero weeks draws a flat trend
    over a month of doing nothing. A zero here is a measurement.

    Days outside the window are ignored rather than clamped into the edge
    buckets, which would make the first week silently mean "everything before".
    """
    if weeks < 1:
        return []
    series_names = sorted({name for counts in day_counts.values() for name in counts})
    last_start = week_start(end_date)
    first_start = last_start - timedelta(weeks=weeks - 1)

    buckets = {
        first_start + timedelta(weeks=i): {name: 0 for name in series_names}
        for i in range(weeks)
    }
    for iso, counts in day_counts.items():
        try:
            day = date.fromisoformat(iso)
        except ValueError:
            continue
        bucket = buckets.get(week_start(day))
        if bucket is None:
            continue
        for name, count in counts.items():
            bucket[name] = bucket.get(name, 0) + count

    return [
        {'weekStart': start.isoformat(), **buckets[start]}
        for start in sorted(buckets)
    ]
