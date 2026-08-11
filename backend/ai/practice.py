"""Grade a from-memory recall attempt in the Practice tab's blind drill.

The blind drill asks for a snippet from its prompt alone, and the whole point
of it is that it must *not* be graded the way the speed drill is. A per-character
diff against the reference marks a correct answer wrong for naming a variable
differently, closing a string with the other quote, or reaching the same result
by an equivalent idiom — which teaches copying, not the syntax. So the judgement
is "is this valid, and does it do what was asked", which needs a reader.

When the model is unavailable the caller still gets a verdict, from
`practice.grading.fallback_grade` — a normalized text comparison, tagged as such
so the UI can admit what graded it. That degradation is deliberate: an offline
session keeps running, and the label is what stops a text-diff verdict passing
itself off as a reading of the code.
"""
from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.practice.grading import fallback_grade

_MAX_SUBMISSION_CHARS = 4000

_RECALL_SYSTEM = (
    "You grade a short code snippet that someone wrote from memory, from a "
    "one-line description of what to write. Judge exactly two things:\n"
    "1. Is it valid syntax for the language?\n"
    "2. Does it do what the task asked?\n"
    "Ignore everything else. Whitespace, indentation, line breaks, quote style, "
    "missing or extra semicolons and trailing commas are NOT errors here. "
    "Identifier names are free unless the task named them explicitly.\n"
    "The reference answer is ONE correct answer, not the only one. An "
    "equivalent idiom that does the same job is fully correct — do not mark an "
    "answer down for differing from the reference.\n"
    "Verdicts:\n"
    '- "correct": valid syntax and does what was asked.\n'
    '- "partial": the right idea, but broken syntax or a missing piece that '
    "would change what it does (a dropped dependency array, a missing await, "
    "the wrong hook).\n"
    '- "wrong": not what was asked, or not recoverable code.\n'
    'Write "feedback" as one or two sentences addressed to the writer, naming '
    "the specific piece that is missing or wrong. No praise padding, no restating "
    "the whole answer. If the answer is correct but differs from the reference, "
    "say what it did differently and that it is fine."
)

_RECALL_SCHEMA = {
    'type': 'object',
    'properties': {
        # Bounded by the grammar rather than requested in the prose, so an
        # unparseable verdict cannot come back at all.
        'verdict': {'type': 'string', 'enum': ['correct', 'partial', 'wrong']},
        'feedback': {'type': 'string'},
    },
    'required': ['verdict', 'feedback'],
}


def _prompt(title: str, task: str, language: str, reference: str, submitted: str) -> str:
    return (
        f'Language: {language}\n'
        f'Snippet: {title}\n'
        f'Task given to the writer: {task}\n\n'
        f'Reference answer (one correct form):\n{reference}\n\n'
        f'What they wrote from memory:\n{submitted}'
    )


def grade_recall(
    *, title: str, task: str, language: str, reference: str, submitted: str
) -> dict:
    """Judge a recall attempt.

    Returns `{verdict, passed, feedback, gradedBy}`. `gradedBy` is `'model'`
    when it was read, `'fallback'` when it was text-compared because the model
    was unconfigured or the call failed. Never raises — a session that cannot
    be graded is worse than one graded crudely and told so.
    """
    if not submitted.strip():
        return {
            'verdict': 'wrong',
            'passed': False,
            'feedback': 'Nothing was written.',
            'gradedBy': 'empty',
        }
    if not is_ai_configured():
        return fallback_grade(reference, submitted)
    try:
        data = chat_json(
            _prompt(title, task, language, reference, submitted[:_MAX_SUBMISSION_CHARS]),
            system=_RECALL_SYSTEM,
            schema=_RECALL_SCHEMA,
        )
    except Exception as e:
        print(f'Practice recall grading failed: {e}')
        return fallback_grade(reference, submitted)

    verdict = data.get('verdict') if isinstance(data, dict) else None
    if verdict not in ('correct', 'partial', 'wrong'):
        return fallback_grade(reference, submitted)
    feedback = data.get('feedback')
    return {
        'verdict': verdict,
        'passed': verdict == 'correct',
        'feedback': feedback.strip() if isinstance(feedback, str) else '',
        'gradedBy': 'model',
    }
