"""Which drill a snippet gets next: typed out, or written from memory.

Two drills run over the same bank. A **speed** drill shows the snippet and
measures how fast and how accurately it is copied. A **blind** drill shows only
the prompt — the code is never sent to the browser — and grades what was
written on whether it is valid syntax that does what was asked, not on whether
it matches the reference character for character.

Blind is the harder half, so it is earned rather than offered: a snippet stays
on speed drills until it has been typed out accurately a couple of times, and
from then on the share of blind runs rises with how fast it is typed. Recall
that fails drops the next run back to speed — the fix for "I couldn't remember
it" is seeing it again, not being asked again immediately.

Nothing here is random. The mode is a pure function of the snippet's recorded
progress, so the same progress always yields the same next drill and a whole
sequence can be walked in a test. Randomness would also make the drill's
difficulty unexplainable to the person doing it, which is the opposite of what
a progression is for.
"""

SPEED = 'speed'
BLIND = 'blind'

# "Writing it out is quite good" — the gate blind runs sit behind. Two clean
# copies rather than one, so a single lucky run doesn't unlock it, and accuracy
# gates harder than speed: knowing the characters is what recall tests, and
# being slow at typing them is not a reason to withhold it.
#
# 90 rather than 95: the speed drill now fills in indentation and line breaks
# (src/lib/practice.ts), so the accuracy it reports is over characters that
# decide what the program does. A run of 95 against a figure that included
# layout was a stricter gate than it looked, and it left blind runs unreachable
# in a whole language for anyone typing a steady 94.
UNLOCK_ATTEMPTS = 2
UNLOCK_ACCURACY = 90.0
UNLOCK_WPM = 25.0

# Above this the snippet is fluent under the fingers and there is little left to
# gain from copying it again, so most of its runs become recall.
FLUENT_WPM = 55.0

MIN_BLIND_SHARE = 0.25
MAX_BLIND_SHARE = 0.75


def _f(progress: dict | None, key: str) -> float:
    value = (progress or {}).get(key)
    return float(value) if value is not None else 0.0


def is_unlocked(progress: dict | None) -> bool:
    """Has this snippet been typed out well enough to be asked for blind?"""
    return (
        _f(progress, 'attempts_count') >= UNLOCK_ATTEMPTS
        and _f(progress, 'best_accuracy') >= UNLOCK_ACCURACY
        and _f(progress, 'best_wpm') >= UNLOCK_WPM
    )


def blind_share(progress: dict | None) -> float:
    """The fraction of this snippet's runs that should be blind.

    Ramps linearly from MIN_BLIND_SHARE at the unlock speed to MAX_BLIND_SHARE
    at FLUENT_WPM. Never reaches 1: a snippet you can write from memory is
    still worth re-copying occasionally, and a run that shows the code is the
    only way a forgotten detail comes back.
    """
    if not is_unlocked(progress):
        return 0.0
    wpm = _f(progress, 'best_wpm')
    span = FLUENT_WPM - UNLOCK_WPM
    ramp = 1.0 if span <= 0 else (wpm - UNLOCK_WPM) / span
    ramp = min(1.0, max(0.0, ramp))
    return MIN_BLIND_SHARE + ramp * (MAX_BLIND_SHARE - MIN_BLIND_SHARE)


def next_mode(progress: dict | None) -> str:
    """SPEED or BLIND for this snippet's next run, from its progress row."""
    if not is_unlocked(progress):
        return SPEED

    # A failed recall is a request to see it again. Without this the same
    # snippet would be asked for blind over and over while it is precisely the
    # one that hasn't been learned yet.
    if progress is not None and progress.get('last_recall_passed') == 0:
        return SPEED

    speed_runs = _f(progress, 'attempts_count')
    blind_runs = _f(progress, 'recall_attempts_count')
    total = speed_runs + blind_runs
    if total <= 0:
        return SPEED
    # Compare the share already spent on recall against the share this snippet
    # has earned; falling short is what schedules the next blind run. Driving
    # the mix off realized counts (rather than a modulo of the run number) is
    # what makes the ratio follow speed as it improves, instead of locking in
    # whatever cadence was set when the snippet first unlocked.
    return BLIND if (blind_runs / total) < blind_share(progress) else SPEED
