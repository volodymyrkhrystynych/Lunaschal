"""Producing one `resume_versions` row: tailor, render, persist.

Extracted from the tailor route because the queue worker needs exactly the same
thing and a second copy is how the two would drift — the worker would keep
rendering DOCX long after the route stopped, or vice versa. The route is now
the interactive caller and `queue.py` the background one; both land here.

The transaction discipline is the point of the ordering below: everything the
model call needs is read first, the steer write is committed *before* the call,
and only then does `tailor_resume` run. `get_db()` is one process-global
connection, so a transaction left open across a multi-second model call would
be committed by whatever Flask request happened to finish next.
"""
from __future__ import annotations

import json
import logging
import time

from ulid import ULID

from backend.jobs import profile as profile_mod, render, storage, tailor

logger = logging.getLogger(__name__)


class ProfileEmpty(Exception):
    """There is nothing to tailor from. Distinct from a model failure: the fix
    is the user's, not a retry."""


class TailoringUnavailable(Exception):
    """The model could not be reached, so nothing was written.

    Raised rather than returning a fallback for `polish_journal_entry`'s
    reason: a resume built from an unavailable model and a resume built from a
    working one must never be indistinguishable.
    """


def application_row(db, application_id):
    return db.execute(
        """
        SELECT a.*, j.company, j.title, j.url AS job_url, j.location, j.description
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id=?
        """,
        (application_id,),
    ).fetchone()


def build_resume_version(db, application_id: str, steer: str | None = None) -> dict:
    """Tailor, render and store one resume version. Returns the stored row.

    `steer` overrides and persists; None reuses whatever the application
    already carries, which is what makes the second tap ("just autofill") work
    without re-recording.
    """
    row = application_row(db, application_id)
    if row is None:
        raise LookupError(f'No application {application_id}')

    now = int(time.time())
    if steer is not None:
        db.execute(
            'UPDATE applications SET steer=?, updated_at=? WHERE id=?',
            (steer, now, application_id),
        )
        db.commit()
    else:
        steer = row['steer'] or ''

    loaded = profile_mod.load_profile(db)
    if profile_mod.is_empty(loaded):
        raise ProfileEmpty('Your profile is empty — add some experience before tailoring.')

    job = {
        'title': row['title'], 'company': row['company'],
        'location': row['location'], 'description': row['description'],
    }

    # Nothing is held open across this call: every write above committed.
    content = tailor.tailor_resume(loaded, job, steer)
    if content is None:
        raise TailoringUnavailable(
            'The local model is unavailable, so the resume was not tailored.'
        )

    version_id = str(ULID())
    html = render.render_html(loaded, content, job)

    pdf_path = storage.resume_path(application_id, version_id, 'pdf')
    docx_path = storage.resume_path(application_id, version_id, 'docx')
    wrote_pdf = bool(pdf_path) and render.render_pdf(html, pdf_path)
    wrote_docx = bool(docx_path) and render.render_docx(loaded, content, docx_path)

    stored_at = int(time.time())
    db.execute(
        'INSERT INTO resume_versions (id, application_id, label, content, keywords,'
        ' html, pdf_path, docx_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (version_id, application_id, (row['title'] or 'Resume')[:80],
         json.dumps(content), json.dumps(content.get('keywords') or {}), html,
         str(pdf_path) if wrote_pdf else None,
         str(docx_path) if wrote_docx else None, stored_at),
    )
    if row['status'] == 'draft':
        db.execute(
            "UPDATE applications SET status='ready', queue_error=NULL, updated_at=? "
            'WHERE id=?',
            (stored_at, application_id),
        )
    db.commit()

    return {
        'id': version_id,
        'content': content,
        'html': html,
        'pdfAvailable': wrote_pdf,
        'docxAvailable': wrote_docx,
    }
