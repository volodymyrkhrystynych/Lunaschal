"""HTTP for the job-application module.

Thin on purpose: every judgement call lives in `backend/jobs/`, and these
handlers move rows and shape JSON. The two places that are not thin are
`submit` (which starts three clocks — retention, linkage rescan, closed_at) and
the resume download (which recomputes the path from ids rather than trusting
the one stored in the row).
"""
import json
import time

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import build_update, get_db, row_to_dict
from backend.jobs import ingest, linker, profile as profile_mod, render, retention, storage, tailor

bp = Blueprint('jobs', __name__, url_prefix='/api/jobs')

APPLICATION_STATUSES = (
    'draft', 'ready', 'submitted', 'acknowledged', 'interview',
    'offer', 'rejected', 'withdrawn', 'ghosted',
)

# Statuses that mean the application has gone out. Reaching one stamps
# applied_at, which is what retention and linkage both measure from.
_SUBMITTED_STATUSES = frozenset(APPLICATION_STATUSES) - {'draft', 'ready'}


def _now() -> int:
    return int(time.time())


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _json_or_none(value):
    return json.dumps(value) if value else None


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------

@bp.get('/profile')
def get_profile():
    return jsonify(profile_mod.load_profile(get_db()))


@bp.patch('/profile')
def update_profile():
    body = _body()
    field_map = {
        'fullName': 'full_name', 'email': 'email', 'phone': 'phone',
        'location': 'location', 'headline': 'headline', 'summary': 'summary',
    }
    updates = {'updated_at': _now()}
    for camel, snake in field_map.items():
        if camel in body:
            updates[snake] = body[camel] or ''
    if 'links' in body:
        updates['links'] = _json_or_none(body['links'])
    db = get_db()
    build_update(db, 'job_profile', updates, 'id=1')
    db.commit()
    return jsonify(profile_mod.load_profile(db))


# Each child table is CRUD over the same tiny shape, so one factory produces
# all four rather than four near-identical handler triples.
_CHILD_TABLES = {
    'roles': ('profile_roles', {
        'company': 'company', 'title': 'title', 'location': 'location',
        'startLabel': 'start_label', 'endLabel': 'end_label', 'ord': 'ord',
    }),
    'bullets': ('profile_bullets', {
        'roleId': 'role_id', 'text': 'text', 'ord': 'ord',
    }),
    'skills': ('profile_skills', {
        'name': 'name', 'category': 'category', 'years': 'years', 'ord': 'ord',
    }),
    'education': ('profile_education', {
        'institution': 'institution', 'credential': 'credential', 'field': 'field',
        'startLabel': 'start_label', 'endLabel': 'end_label', 'notes': 'notes',
        'ord': 'ord',
    }),
    'answers': ('profile_answers', {
        'slug': 'slug', 'question': 'question', 'answer': 'answer', 'ord': 'ord',
    }),
}


@bp.post('/profile/<kind>')
def create_profile_child(kind):
    entry = _CHILD_TABLES.get(kind)
    if entry is None:
        return jsonify({'error': 'Unknown profile section'}), 404
    table, field_map = entry
    body = _body()

    if kind == 'bullets' and not body.get('roleId'):
        return jsonify({'error': 'roleId required'}), 400

    now = _now()
    columns = {'id': str(ULID()), 'created_at': now, 'updated_at': now}
    for camel, snake in field_map.items():
        if camel in body:
            columns[snake] = body[camel]
    if 'tags' in body:
        columns['tags'] = _json_or_none(body['tags'])

    placeholders = ', '.join('?' for _ in columns)
    db = get_db()
    try:
        db.execute(
            f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({placeholders})',
            tuple(columns.values()),
        )
        db.commit()
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'id': columns['id']}), 201


@bp.patch('/profile/<kind>/<item_id>')
def update_profile_child(kind, item_id):
    entry = _CHILD_TABLES.get(kind)
    if entry is None:
        return jsonify({'error': 'Unknown profile section'}), 404
    table, field_map = entry
    body = _body()

    updates = {'updated_at': _now()}
    for camel, snake in field_map.items():
        if camel in body:
            updates[snake] = body[camel]
    if 'tags' in body:
        updates['tags'] = _json_or_none(body['tags'])

    db = get_db()
    cursor = build_update(db, table, updates, 'id=?', (item_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


@bp.delete('/profile/<kind>/<item_id>')
def delete_profile_child(kind, item_id):
    entry = _CHILD_TABLES.get(kind)
    if entry is None:
        return jsonify({'error': 'Unknown profile section'}), 404
    table, _ = entry
    db = get_db()
    cursor = db.execute(f'DELETE FROM {table} WHERE id=?', (item_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

@bp.get('')
def list_jobs():
    db = get_db()
    include_dismissed = request.args.get('dismissed') == '1'
    where = '' if include_dismissed else ' WHERE j.dismissed = 0'
    rows = db.execute(
        f"""
        SELECT j.*, a.id AS application_id, a.status AS application_status
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        {where}
        ORDER BY j.match_score IS NULL, j.match_score DESC, j.created_at DESC
        """
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('')
def create_job():
    """Create a job from a URL, from pasted text, or from typed fields.

    A URL is fetched here rather than in a thread: the user is waiting on the
    result to fill the form, and a spinner they can see beats a job they have
    to poll for.
    """
    body = _body()
    url = (body.get('url') or '').strip()
    text = (body.get('text') or '').strip()

    fields = {
        'title': body.get('title') or '',
        'company': body.get('company') or '',
        'location': body.get('location') or '',
        'remote': bool(body.get('remote')),
        'salaryMin': body.get('salaryMin'),
        'salaryMax': body.get('salaryMax'),
        'salaryCurrency': body.get('salaryCurrency') or '',
        'description': body.get('description') or '',
        'url': url,
    }

    if url and not fields['description']:
        try:
            fields = ingest.ingest_url(url)
        except ingest.UnsafeUrl as e:
            return jsonify({'error': f'That URL is not reachable from here: {e}'}), 400
        except ingest.FetchFailed as e:
            return jsonify({'error': str(e)}), 502
    elif text and not fields['description']:
        extracted = ingest.extract_job(text, url=url)
        fields = extracted if extracted else {**fields, 'description': text}

    if not fields.get('title') and not fields.get('company'):
        return jsonify({'error': 'Need at least a title or a company.'}), 400

    now = _now()
    job_id = str(ULID())
    db = get_db()
    db.execute(
        """
        INSERT INTO jobs (id, source, source_id, url, company, title, location,
                          remote, salary_min, salary_max, salary_currency,
                          description, fetched_at, created_at, updated_at)
        VALUES (?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, job_id, fields.get('url') or '', fields.get('company') or '',
         fields.get('title') or '', fields.get('location') or '',
         1 if fields.get('remote') else 0, fields.get('salaryMin'),
         fields.get('salaryMax'), fields.get('salaryCurrency') or '',
         fields.get('description') or '', now if url else None, now, now),
    )
    db.commit()
    return jsonify(row_to_dict(
        db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    )), 201


@bp.get('/<job_id>')
def get_job(job_id):
    row = get_db().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.patch('/<job_id>')
def update_job(job_id):
    body = _body()
    field_map = {
        'title': 'title', 'company': 'company', 'location': 'location',
        'url': 'url', 'description': 'description', 'salaryMin': 'salary_min',
        'salaryMax': 'salary_max', 'salaryCurrency': 'salary_currency',
    }
    updates = {'updated_at': _now()}
    for camel, snake in field_map.items():
        if camel in body:
            updates[snake] = body[camel]
    for flag in ('remote', 'dismissed'):
        if flag in body:
            updates[flag] = 1 if body[flag] else 0

    db = get_db()
    cursor = build_update(db, 'jobs', updates, 'id=?', (job_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


@bp.delete('/<job_id>')
def delete_job(job_id):
    db = get_db()
    # Applications cascade, so their rendered files would otherwise be orphaned
    # on disk with no row left to find them by.
    for row in db.execute('SELECT id FROM applications WHERE job_id=?', (job_id,)).fetchall():
        storage.delete_application_dir(row['id'])
    cursor = db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

def _application_row(db, application_id):
    return db.execute(
        """
        SELECT a.*, j.company, j.title, j.url AS job_url, j.location, j.description
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.id=?
        """,
        (application_id,),
    ).fetchone()


@bp.get('/applications')
def list_applications():
    db = get_db()
    status = request.args.get('status')
    sql = """
        SELECT a.*, j.company, j.title, j.url AS job_url, j.location
        FROM applications a JOIN jobs j ON j.id = a.job_id
    """
    params: tuple = ()
    if status in APPLICATION_STATUSES:
        sql += ' WHERE a.status=?'
        params = (status,)
    sql += ' ORDER BY a.applied_at IS NULL DESC, a.applied_at DESC, a.created_at DESC'
    return jsonify([row_to_dict(r) for r in db.execute(sql, params).fetchall()])


@bp.post('/applications')
def create_application():
    body = _body()
    job_id = body.get('jobId')
    db = get_db()
    if not job_id or db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone() is None:
        return jsonify({'error': 'Unknown job'}), 400

    existing = db.execute(
        'SELECT id FROM applications WHERE job_id=?', (job_id,)
    ).fetchone()
    if existing:
        return jsonify({'id': existing['id'], 'existing': True}), 200

    now = _now()
    application_id = str(ULID())
    db.execute(
        'INSERT INTO applications (id, job_id, status, steer, applied_email,'
        ' created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (application_id, job_id, 'draft', body.get('steer') or '',
         body.get('appliedEmail') or '', now, now),
    )
    db.commit()
    return jsonify({'id': application_id}), 201


@bp.get('/applications/<application_id>')
def get_application(application_id):
    db = get_db()
    row = _application_row(db, application_id)
    if row is None:
        return jsonify({'error': 'Not found'}), 404

    result = row_to_dict(row)
    result['resumes'] = [
        row_to_dict(r) for r in db.execute(
            'SELECT id, label, keywords, pdf_path, docx_path, purged_at, created_at'
            ' FROM resume_versions WHERE application_id=? ORDER BY created_at DESC',
            (application_id,),
        ).fetchall()
    ]
    result['emails'] = [
        row_to_dict(r) for r in db.execute(
            """
            SELECT e.id, e.subject, e.sender, e.sender_email, e.received_at,
                   e.job_status, l.link_kind, l.confidence
            FROM job_email_links l JOIN emails e ON e.id = l.email_id
            WHERE l.application_id=? ORDER BY e.received_at DESC
            """,
            (application_id,),
        ).fetchall()
    ]
    return jsonify(result)


@bp.patch('/applications/<application_id>')
def update_application(application_id):
    body = _body()
    db = get_db()
    row = db.execute(
        'SELECT status, applied_at FROM applications WHERE id=?', (application_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404

    now = _now()
    updates = {'updated_at': now}
    for camel, snake in (('steer', 'steer'), ('coverLetter', 'cover_letter'),
                         ('notes', 'notes'), ('appliedEmail', 'applied_email')):
        if camel in body:
            updates[snake] = body[camel] or ''

    new_status = body.get('status')
    if new_status is not None:
        if new_status not in APPLICATION_STATUSES:
            return jsonify({'error': 'Unknown status'}), 400
        updates['status'] = new_status
        # Reaching a submitted state stamps applied_at if nothing else has —
        # it is the origin for retention and for every linkage window.
        if new_status in _SUBMITTED_STATUSES and not row['applied_at']:
            updates['applied_at'] = now

    build_update(db, 'applications', updates, 'id=?', (application_id,))
    db.commit()

    if new_status is not None:
        retention.stamp_closed(db, application_id, new_status, now=now)
        if 'applied_at' in updates:
            linker.rescan_since(db, now)

    return jsonify({'success': True})


@bp.post('/applications/<application_id>/submit')
def submit_application(application_id):
    """Mark an application sent. Starts the retention clock and re-opens the
    linkage question for recent mail."""
    body = _body()
    db = get_db()
    row = db.execute(
        'SELECT applied_at FROM applications WHERE id=?', (application_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404

    now = _now()
    updates = {
        'status': 'submitted',
        'applied_at': row['applied_at'] or now,
        'updated_at': now,
    }
    if body.get('appliedEmail'):
        updates['applied_email'] = body['appliedEmail']
    build_update(db, 'applications', updates, 'id=?', (application_id,))
    db.commit()

    retention.stamp_closed(db, application_id, 'submitted', now=now)
    linker.rescan_since(db, now)
    # Immediate rather than waiting for the next tick: the confirmation email
    # has usually already arrived by the time the user marks this.
    swept = linker.run_linkage_sweep(now=now)

    return jsonify({'success': True, 'linkage': swept})


@bp.delete('/applications/<application_id>')
def delete_application(application_id):
    db = get_db()
    cursor = db.execute('DELETE FROM applications WHERE id=?', (application_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    storage.delete_application_dir(application_id)
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Tailoring and rendering
# --------------------------------------------------------------------------

@bp.post('/applications/<application_id>/tailor')
def tailor_application(application_id):
    """Tailor a resume for this application and render it.

    Synchronous. One `chat_json` call against a local model is seconds, and the
    user is looking at the result — a background job would need a progress
    registry, a poll endpoint and a stale-state sweep to buy nothing.
    """
    db = get_db()
    row = _application_row(db, application_id)
    if row is None:
        return jsonify({'error': 'Not found'}), 404

    body = _body()
    steer = body.get('steer')
    if steer is not None:
        db.execute(
            'UPDATE applications SET steer=?, updated_at=? WHERE id=?',
            (steer, _now(), application_id),
        )
        db.commit()
    else:
        steer = row['steer'] or ''

    loaded = profile_mod.load_profile(db)
    if profile_mod.is_empty(loaded):
        return jsonify({
            'error': 'Your profile is empty — add some experience before tailoring.'
        }), 400

    job = {
        'title': row['title'], 'company': row['company'],
        'location': row['location'], 'description': row['description'],
    }

    # Nothing is held open across this call: everything above committed.
    content = tailor.tailor_resume(loaded, job, steer)
    if content is None:
        return jsonify({
            'error': 'The local model is unavailable, so the resume was not tailored.'
        }), 503

    now = _now()
    version_id = str(ULID())
    html = render.render_html(loaded, content, job)

    pdf_path = storage.resume_path(application_id, version_id, 'pdf')
    docx_path = storage.resume_path(application_id, version_id, 'docx')
    wrote_pdf = bool(pdf_path) and render.render_pdf(html, pdf_path)
    wrote_docx = bool(docx_path) and render.render_docx(loaded, content, docx_path)

    db.execute(
        'INSERT INTO resume_versions (id, application_id, label, content, keywords,'
        ' html, pdf_path, docx_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (version_id, application_id, (row['title'] or 'Resume')[:80],
         json.dumps(content), json.dumps(content.get('keywords') or {}), html,
         str(pdf_path) if wrote_pdf else None,
         str(docx_path) if wrote_docx else None, now),
    )
    if row['status'] == 'draft':
        db.execute(
            "UPDATE applications SET status='ready', updated_at=? WHERE id=?",
            (now, application_id),
        )
    db.commit()

    return jsonify({
        'id': version_id,
        'content': content,
        'html': html,
        'pdfAvailable': wrote_pdf,
        'docxAvailable': wrote_docx,
        'renderers': {
            'pdf': render.is_pdf_available(),
            'docx': render.is_docx_available(),
        },
    }), 201


@bp.get('/resumes/<version_id>')
def get_resume(version_id):
    row = get_db().execute(
        'SELECT * FROM resume_versions WHERE id=?', (version_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    result = row_to_dict(row)
    try:
        result['content'] = json.loads(row['content'] or '{}')
    except ValueError:
        result['content'] = {}
    return jsonify(result)


@bp.get('/resumes/<version_id>/download.<ext>')
def download_resume(version_id, ext):
    """Serve a rendered resume.

    The path is recomputed from the two ids rather than read from the row, so a
    tampered `pdf_path` cannot walk out of the jobs root — the same reason the
    fanfic image route rebuilds its path instead of trusting the database.
    """
    if ext not in ('pdf', 'docx'):
        return jsonify({'error': 'Not found'}), 404

    row = get_db().execute(
        'SELECT application_id, purged_at FROM resume_versions WHERE id=?', (version_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    if row['purged_at']:
        return jsonify({'error': 'This resume was deleted under the retention policy.'}), 410

    path = storage.resume_path(row['application_id'], version_id, ext)
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404

    mimetype = ('application/pdf' if ext == 'pdf'
                else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    return send_file(path, mimetype=mimetype, as_attachment=True,
                     download_name=f'resume.{ext}', conditional=True)


# --------------------------------------------------------------------------
# Answer kit
# --------------------------------------------------------------------------

@bp.post('/applications/<application_id>/answers')
def answer_kit(application_id):
    """Answer a form's questions for this application.

    The client posts the questions it found (typed by the user, or scraped from
    the page by the browser overlay). Most come back without a model call at
    all — see backend/jobs/answers.py's resolution order.
    """
    from backend.jobs import answers as answers_mod

    db = get_db()
    row = _application_row(db, application_id)
    if row is None:
        return jsonify({'error': 'Not found'}), 404

    body = _body()
    questions = body.get('questions')
    if not isinstance(questions, list) or not questions:
        return jsonify({'error': 'questions required'}), 400

    cleaned = []
    for q in questions:
        if isinstance(q, str):
            cleaned.append({'label': q, 'type': 'text', 'options': []})
        elif isinstance(q, dict) and q.get('label'):
            kind = q.get('type')
            cleaned.append({
                'label': str(q['label'])[:500],
                'type': kind if kind in answers_mod.QUESTION_TYPES else 'text',
                'options': [str(o) for o in (q.get('options') or []) if o][:30],
            })
    if not cleaned:
        return jsonify({'error': 'questions required'}), 400

    steer = body.get('steer')
    if steer is not None:
        db.execute(
            'UPDATE applications SET steer=?, updated_at=? WHERE id=?',
            (steer, _now(), application_id),
        )
        db.commit()
    else:
        steer = row['steer'] or ''

    loaded = profile_mod.load_profile(db)
    job = {
        'title': row['title'], 'company': row['company'],
        'description': row['description'],
    }
    return jsonify({
        'answers': answers_mod.answer_questions(cleaned, loaded, job, steer),
    })


# --------------------------------------------------------------------------
# Email linkage
# --------------------------------------------------------------------------

@bp.post('/linkage/sweep')
def run_sweep():
    return jsonify(linker.run_linkage_sweep())


@bp.get('/linkage/unlinked')
def list_unlinked():
    db = get_db()
    rows = linker.unlinked_job_emails(db)
    for row in rows:
        row['suggestions'] = linker.suggestions_for_email(db, row['id'])
    return jsonify(rows)


@bp.post('/linkage/link')
def create_link():
    body = _body()
    application_id = body.get('applicationId')
    email_id = body.get('emailId')
    if not application_id or not email_id:
        return jsonify({'error': 'applicationId and emailId required'}), 400

    db = get_db()
    if db.execute('SELECT 1 FROM applications WHERE id=?', (application_id,)).fetchone() is None:
        return jsonify({'error': 'Unknown application'}), 400
    email = db.execute('SELECT job_status FROM emails WHERE id=?', (email_id,)).fetchone()
    if email is None:
        return jsonify({'error': 'Unknown email'}), 400

    now = _now()
    linker.link(db, application_id, email_id, 1.0, 'manual', now=now)
    db.execute(
        'INSERT OR REPLACE INTO job_email_scans (email_id, scanned_at, matched)'
        ' VALUES (?, ?, 1)',
        (email_id, now),
    )
    db.commit()
    status = linker.apply_email_status(db, application_id, email['job_status'], now=now)
    return jsonify({'success': True, 'statusChange': status})


@bp.delete('/linkage/link')
def remove_link():
    body = _body()
    db = get_db()
    cursor = db.execute(
        'DELETE FROM job_email_links WHERE application_id=? AND email_id=?',
        (body.get('applicationId'), body.get('emailId')),
    )
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@bp.get('/stats')
def stats():
    db = get_db()
    counts = {
        r['status']: r['count']
        for r in db.execute(
            'SELECT status, COUNT(*) count FROM applications GROUP BY status'
        ).fetchall()
    }
    active = db.execute(
        """
        SELECT a.id, a.status, a.applied_at, j.company, j.title
        FROM applications a JOIN jobs j ON j.id = a.job_id
        WHERE a.status IN ('submitted','acknowledged','interview','offer')
        ORDER BY a.applied_at DESC LIMIT 10
        """
    ).fetchall()
    unlinked = db.execute(
        """
        SELECT COUNT(*) c FROM emails e
        JOIN job_email_scans s ON s.email_id = e.id AND s.matched = 0
        LEFT JOIN job_email_links l ON l.email_id = e.id
        WHERE e.category = 'job_application' AND l.id IS NULL
        """
    ).fetchone()
    upcoming = db.execute(
        'SELECT COUNT(*) c FROM applications'
        ' WHERE purged_at IS NULL AND purge_after IS NOT NULL AND purge_after < ?',
        (_now() + 30 * 86400,),
    ).fetchone()

    return jsonify({
        'counts': {status: counts.get(status, 0) for status in APPLICATION_STATUSES},
        'total': sum(counts.values()),
        'active': [row_to_dict(r) for r in active],
        'unlinkedEmails': unlinked['c'] if unlinked else 0,
        'purgingSoon': upcoming['c'] if upcoming else 0,
    })


@bp.post('/retention/sweep')
def run_retention():
    return jsonify(retention.run_purge_sweep())
