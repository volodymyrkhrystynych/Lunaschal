"""Feedback for a single drill attempt.

Two kinds, because the two drills measure different things. `rating_label` is
encouragement over the speed drill's numbers — no FSRS rating, just a
threshold-based label. `fallback_grade` is the offline stand-in for the blind
drill's real grader (`backend/ai/practice.py`), which needs the model.
"""
import re

TARGET_WPM = 40.0


def rating_label(accuracy: float, wpm: float) -> str:
    if accuracy < 80:
        return 'Needs work'
    if accuracy < 95 or wpm < TARGET_WPM * 0.6:
        return 'Good'
    return 'Great'


# Whitespace that sits against punctuation carries no meaning in any of the
# languages in the bank, so `() => {}` and `()=>{}` fold together. Quotes are
# deliberately absent: eating a space next to one would edit the contents of a
# string literal.
_PUNCT = r'(){}\[\]<>;,:=+\-*/%&|!?.'


def normalize_code(text: str) -> str:
    """Fold away formatting that the blind drill does not grade on."""
    collapsed = re.sub(r'\s+', ' ', text).strip()
    squeezed = re.sub(rf'\s*([{_PUNCT}])\s*', r'\1', collapsed)
    # A dropped semicolon at the very end is the single most common way a
    # remembered-correctly answer differs from the reference. Only the trailing
    # one goes: semicolons between statements separate them, and dropping those
    # would fold two different answers together.
    return squeezed.rstrip(';')


def fallback_grade(reference: str, submitted: str) -> dict:
    """Grade a recall attempt without the model.

    This is what runs when llama-server is unreachable, and it is a much worse
    judge than the real grader: it compares normalized text, so a correct
    answer that names a variable differently or uses an equivalent idiom is
    marked wrong. It is offered anyway so a session can continue offline — and
    the result is tagged `gradedBy: 'fallback'` so the UI can say out loud that
    the verdict came from a text comparison rather than a reading of the code.
    """
    passed = normalize_code(reference) == normalize_code(submitted)
    return {
        'verdict': 'correct' if passed else 'wrong',
        'passed': passed,
        'feedback': (
            'Graded offline by exact text comparison — it matches the reference.'
            if passed
            else 'Graded offline by exact text comparison, so this only means it '
            'differs from the reference. Compare them yourself: a different but '
            'correct answer fails this check.'
        ),
        'gradedBy': 'fallback',
    }
