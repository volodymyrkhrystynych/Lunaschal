"""The workout activity types, and how a day collapses to one heatmap box.

Ordered most- to least-specific: when a single day holds more than one session,
the box takes the colour of the highest-priority one and a corner mark records
that something else happened too (docs/lifestyle-tab.md §1).

The list is append-in-place, not append-only: `priority()` looks a row's type up
by index, so inserting a type shifts the ones below it. That only changes which
colour a *mixed* day shows, never what was logged, and unknown types already
sort last so a row written by an older build can't break the heatmap.
"""

# Index in this tuple *is* the priority — first wins.
ACTIVITY_TYPES = (
    'goodlife_brother',
    'goodlife_alone',
    'building',
    'lifting_home',
    'outside',
)

ACTIVITY_LABELS = {
    'goodlife_brother': 'Goodlife with brother',
    'goodlife_alone': 'Goodlife alone',
    'building': 'Building workout room',
    'lifting_home': 'Lifting at home',
    'outside': 'Outside',
}


def is_activity_type(value) -> bool:
    return value in ACTIVITY_TYPES


def priority(activity_type: str) -> int:
    """Lower is higher-priority. Unknown types sort last rather than raising —
    a row written by an older build must never break the heatmap."""
    try:
        return ACTIVITY_TYPES.index(activity_type)
    except ValueError:
        return len(ACTIVITY_TYPES)


def summarize_day(sessions: list[dict]) -> dict | None:
    """Collapse one day's sessions into the heatmap's per-box summary.

    Returns {activityType, secondary, durationMinutes, intensityRating, sessions}
    where `secondary` is True when a *different* activity type also happened that
    day (two Goodlife-alone sessions are one activity, not two). Duration sums
    across the day; intensity takes the day's hardest session, since a box shaded
    by "how hard was that day" shouldn't be dragged down by a warm-up walk.
    """
    if not sessions:
        return None
    ordered = sorted(sessions, key=lambda s: priority(s['locationType']))
    primary = ordered[0]['locationType']
    durations = [s['durationMinutes'] for s in sessions if s.get('durationMinutes')]
    intensities = [s['intensityRating'] for s in sessions if s.get('intensityRating')]
    return {
        'activityType': primary,
        'secondary': any(s['locationType'] != primary for s in ordered),
        'durationMinutes': sum(durations) if durations else None,
        'intensityRating': max(intensities) if intensities else None,
        'sessions': sessions,
    }
