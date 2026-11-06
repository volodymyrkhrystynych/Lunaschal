"""Did a reminder work? Pure apart from the facts it is handed.

A decision's reward is 1 if the thing it was about got done inside its
window, measured from when the reminder was due — whether it was sent or
held back, which is what makes the held ones a control:

- weight: within 2 hours
- calories: within 3 hours
- a habit, or setting goals: by the end of that 4am day

A day the user excused (`day_excuses`) is marked and left out of learning, as
is a snooze's follow-up: neither says anything about the reminder.
"""
WINDOWS = {'weight': 2 * 3600, 'calories': 3 * 3600}


def window_end(behavior: str, planned_for: int, day_end: int) -> int:
    family = behavior.split(':', 1)[0]
    return planned_for + WINDOWS[family] if family in WINDOWS else day_end


def outcome(done_at: list[int], planned_for: int, end: int) -> float:
    """1.0 if anything was done in [planned_for, end)."""
    return 1.0 if any(planned_for <= t < end for t in done_at) else 0.0
