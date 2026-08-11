"""LLM classification of synced emails: a closed-vocabulary category, plus a
job-application-specific sub-status when the category is 'job_application'.
Same idiom as backend/ai/journal.py's tag classification — closed-vocab
tuples double as the JSON-schema enum, so an off-vocabulary value can't be
emitted at all, not just discouraged in the prompt.

Runs on backend.ai.background's single-worker executor after each new email
lands (see backend/email/sync.py), so a slow LLM call never stalls the next
Gmail API page or poll tick. classified_at IS NULL is the "still pending"
state — for both never-attempted and previously-failed rows — so a crash
mid-classification needs no separate in-progress flag to reset; a startup
sweep (sweep_unclassified) just re-enqueues anything still NULL.
"""
import re
import time

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured
from backend.db.connection import build_update, get_db

EMAIL_CATEGORIES = ('job_application', 'newsletter', 'notification', 'personal', 'other')
JOB_APPLICATION_STATUSES = ('sent', 'rejection', 'interview_next_step', 'other_update')

# Coarse classification needs far less context than extraction — smaller than
# recipes.py's 15000-char cap on purpose.
_MAX_BODY_CHARS = 6000

_CATEGORY_SYSTEM = (
    "You classify personal emails into exactly one category.\n"
    "Return ONLY valid JSON with one field:\n"
    '- "category": one of these exact values:\n'
    f"  {', '.join(EMAIL_CATEGORIES)}\n"
    "'job_application' means the email is part of a job search: an application "
    "confirmation, a recruiter reaching out, an interview invite, a rejection, "
    "or any other update tied to a specific job application.\n"
    'Example: {"category": "job_application"}'
)
_CATEGORY_SCHEMA = {
    'type': 'object',
    'properties': {'category': {'type': 'string', 'enum': list(EMAIL_CATEGORIES)}},
    'required': ['category'],
}

_JOB_STATUS_SYSTEM = (
    "You classify a job-application-related email into exactly one status.\n"
    "Return ONLY valid JSON with one field:\n"
    '- "status": one of these exact values:\n'
    f"  {', '.join(JOB_APPLICATION_STATUSES)}\n"
    "'sent' = confirms an application was submitted or received.\n"
    "'rejection' = the application was not successful.\n"
    "'interview_next_step' = an interview, screen, or other next step is being "
    "offered or scheduled.\n"
    "'other_update' = anything else tied to the application (e.g. an "
    "assessment request, a status check-in).\n"
    'Example: {"status": "rejection"}'
)
_JOB_STATUS_SCHEMA = {
    'type': 'object',
    'properties': {'status': {'type': 'string', 'enum': list(JOB_APPLICATION_STATUSES)}},
    'required': ['status'],
}


_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)

# Long enough to keep a human-meaningful link ("acme.com/careers") while
# collapsing the tracking monsters. Measured over a real mailbox: URLs are
# ~51% of all body text, and the longest single one was 2,328 characters —
# 39% of the whole budget below, spent on one redirect token.
_URL_KEEP_CHARS = 60


def _strip_urls(text: str) -> str:
    """Replace tracking URLs with a short marker.

    A link's query string is opaque entropy — campaign ids, HMACs, base64
    blobs — that says nothing about whether a message is a rejection or a
    newsletter, and it competes for the same character budget as the prose
    that does. Keeping a truncated head preserves the one part that can
    carry signal (the domain), and leaving a marker rather than deleting
    outright keeps "click here to schedule" from reading as a dangling
    sentence about nothing.
    """
    def shorten(match: re.Match[str]) -> str:
        url = match.group(0)
        return url if len(url) <= _URL_KEEP_CHARS else f'{url[:_URL_KEEP_CHARS]}…[link]'

    return _URL_RE.sub(shorten, text)


def _prompt_text(row) -> str:
    subject = row['subject'] or ''
    sender = row['sender'] or row['sender_email'] or ''
    # Strip before truncating, or a single long link at the top of a
    # newsletter can consume the window before the body is even reached.
    body = _strip_urls(row['body_text'] or '')
    # Marketing mail arrives with vertical whitespace used as layout; it
    # survives html_to_text as runs of blank lines that cost tokens and mean
    # nothing to a classifier.
    body = re.sub(r'\n{3,}', '\n\n', body)[:_MAX_BODY_CHARS]
    return f'From: {sender}\nSubject: {subject}\n\n{body}'


def classify_email(email_id: str) -> None:
    """Load the row, classify category (and job sub-status iff category is
    'job_application'), write the result back — or classification_error if
    something failed. Meant for run_bg(); never raises."""
    db = get_db()
    try:
        row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
        if not row or not is_ai_configured():
            return

        text = _prompt_text(row)
        category_data = chat_json(text, system=_CATEGORY_SYSTEM, schema=_CATEGORY_SCHEMA)
        category = category_data.get('category')
        if category not in EMAIL_CATEGORIES:
            category = 'other'

        job_status = None
        if category == 'job_application':
            status_data = chat_json(text, system=_JOB_STATUS_SYSTEM, schema=_JOB_STATUS_SCHEMA)
            status = status_data.get('status')
            job_status = status if status in JOB_APPLICATION_STATUSES else None

        build_update(
            db, 'emails',
            {
                'category': category,
                'job_status': job_status,
                'classified_at': int(time.time()),
                'classification_error': None,
            },
            'id=?', (email_id,),
        )
        db.commit()
    except Exception as e:
        build_update(db, 'emails', {'classification_error': str(e)}, 'id=?', (email_id,))
        db.commit()


def sweep_unclassified() -> int:
    """Re-enqueue anything left classified_at IS NULL by a prior crash.
    Returns the number enqueued."""
    from backend.ai.background import run_bg
    db = get_db()
    rows = db.execute('SELECT id FROM emails WHERE classified_at IS NULL').fetchall()
    for row in rows:
        run_bg(lambda eid=row['id']: classify_email(eid))
    return len(rows)
