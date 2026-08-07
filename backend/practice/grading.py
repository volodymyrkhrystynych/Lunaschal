"""Qualitative feedback for a single drill attempt.

This is encouragement, not scheduling — there's no FSRS rating here, just a
threshold-based label shown to the user after each snippet.
"""

TARGET_WPM = 40.0


def rating_label(accuracy: float, wpm: float) -> str:
    if accuracy < 80:
        return 'Needs work'
    if accuracy < 95 or wpm < TARGET_WPM * 0.6:
        return 'Good'
    return 'Great'
