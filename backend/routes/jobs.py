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

from backend.ai import job_match, priority
from backend.db.connection import build_update, get_db, row_to_dict
from backend.jobs import (
    build, ingest, linker, profile as profile_mod, queue as queue_mod, render,
    resolve, resume_import, retention, sources, storage, sync, tailor,
    triager, urlmatch,
)

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


@bp.post('/profile/import')
def import_profile():
    """Read an existing resume into a structured preview. Writes nothing.

    Preview-then-commit for the same reason the company resolver works that
    way: this feeds `profile_bullets`, the one table the anti-fabrication
    guarantee treats as fact, so it has to be inspectable before it is
    believed.
    """
    upload = request.files.get('file')
    try:
        if upload is not None:
            data = upload.read()
            if not data:
                return jsonify({'error': 'That file is empty.'}), 400
            lines = resume_import.extract_lines(
                data=data, filename=upload.filename or ''
            )
        else:
            text = (_body().get('text') or '').strip()
            if not text:
                return jsonify({'error': 'Paste your resume text, or pick a .docx.'}), 400
            lines = resume_import.extract_lines(text=text)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    if not lines:
        return jsonify({'error': 'Nothing readable in that document.'}), 400

    # The user is watching this one, so it takes a mark rather than deferring.
    token = priority.begin('resume-import')
    try:
        preview = resume_import.import_resume(lines)
    finally:
        priority.end(token)

    if preview is None:
        return jsonify({
            'error': 'The local model is unavailable, so the resume was not read.'
        }), 503
    return jsonify(preview)


@bp.post('/profile/import/commit')
def commit_profile_import():
    """Write a reviewed import into the profile. Appends; never replaces.

    Makes no model call — it stores exactly what the user approved, so what
    was on screen is what lands.
    """
    body = _body()
    db = get_db()
    now = _now()
    created = {'roles': 0, 'bullets': 0, 'skills': 0, 'education': 0}

    contact = body.get('contact') or {}
    if isinstance(contact, dict) and any(contact.values()):
        # Only fills blanks: an import must not overwrite a phone number the
        # user has already corrected by hand.
        current = db.execute('SELECT * FROM job_profile WHERE id=1').fetchone()
        updates = {'updated_at': now}
        for camel, snake in (('fullName', 'full_name'), ('email', 'email'),
                             ('phone', 'phone'), ('location', 'location'),
                             ('headline', 'headline')):
            value = (contact.get(camel) or '').strip()
            if value and not (current[snake] if current else ''):
                updates[snake] = value
        if len(updates) > 1:
            build_update(db, 'job_profile', updates, 'id=1')

    role_ord = db.execute(
        'SELECT COALESCE(MAX(ord), -1) AS m FROM profile_roles'
    ).fetchone()['m']
    for role in (body.get('roles') or []):
        if not isinstance(role, dict):
            continue
        company = (role.get('company') or '').strip()
        title = (role.get('title') or '').strip()
        if not company and not title:
            continue
        role_ord += 1
        role_id = str(ULID())
        db.execute(
            'INSERT INTO profile_roles (id, company, title, location, start_label,'
            ' end_label, ord, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (role_id, company, title, (role.get('location') or '').strip(),
             (role.get('startLabel') or '').strip(),
             (role.get('endLabel') or '').strip(), role_ord, now, now),
        )
        created['roles'] += 1

        for bullet_ord, bullet in enumerate(role.get('bullets') or []):
            text = (bullet.get('text') if isinstance(bullet, dict) else bullet) or ''
            text = text.strip()
            if not text:
                continue
            db.execute(
                'INSERT INTO profile_bullets (id, role_id, text, ord, created_at,'
                ' updated_at) VALUES (?, ?, ?, ?, ?, ?)',
                (str(ULID()), role_id, text, bullet_ord, now, now),
            )
            created['bullets'] += 1

    skill_ord = db.execute(
        'SELECT COALESCE(MAX(ord), -1) AS m FROM profile_skills'
    ).fetchone()['m']
    existing_skills = {
        r['name'].casefold()
        for r in db.execute('SELECT name FROM profile_skills').fetchall()
    }
    for raw in (body.get('skills') or []):
        name = (raw.get('name') if isinstance(raw, dict) else raw) or ''
        name = name.strip()
        if not name or name.casefold() in existing_skills:
            continue
        existing_skills.add(name.casefold())
        skill_ord += 1
        db.execute(
            'INSERT INTO profile_skills (id, name, category, ord, created_at,'
            " updated_at) VALUES (?, ?, '', ?, ?, ?)",
            (str(ULID()), name, skill_ord, now, now),
        )
        created['skills'] += 1

    edu_ord = db.execute(
        'SELECT COALESCE(MAX(ord), -1) AS m FROM profile_education'
    ).fetchone()['m']
    for entry in (body.get('education') or []):
        if not isinstance(entry, dict):
            continue
        institution = (entry.get('institution') or '').strip()
        credential = (entry.get('credential') or '').strip()
        if not institution and not credential:
            continue
        edu_ord += 1
        db.execute(
            'INSERT INTO profile_education (id, institution, credential, field,'
            ' start_label, end_label, ord, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (str(ULID()), institution, credential, (entry.get('field') or '').strip(),
             (entry.get('startLabel') or '').strip(),
             (entry.get('endLabel') or '').strip(), edu_ord, now, now),
        )
        created['education'] += 1

    db.commit()
    return jsonify({'created': created, 'profile': profile_mod.load_profile(db)}), 201


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


@bp.get('/applications/for-url')
def application_for_url():
    """Which application, if any, is the posting at `url`?

    The extension calls this when a tab opens so the user does not have to
    pick an application by hand. It answers with `null` rather than a guess
    when nothing matches or several do — the popup is the manual override, and
    a wrong auto-match would file answers against the wrong employer.
    """
    target = request.args.get('url') or ''
    db = get_db()
    rows = db.execute(
        'SELECT a.id, a.status, j.url, j.title, j.company FROM applications a'
        ' JOIN jobs j ON j.id = a.job_id'
    ).fetchall()

    match = urlmatch.best_match(target, [dict(r) for r in rows])
    if match is None:
        return jsonify({'application': None})
    return jsonify({'application': {
        'id': match['id'], 'status': match['status'],
        'title': match['title'], 'company': match['company'],
    }})


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
    result['recordedAnswers'] = _recorded_answers(db, application_id)
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
    registry, a poll endpoint and a stale-state sweep to buy nothing. The
    queued path in `backend/jobs/queue.py` is the one that needs all three, and
    it shares this body through `build_resume_version`.
    """
    db = get_db()
    if _application_row(db, application_id) is None:
        return jsonify({'error': 'Not found'}), 404

    try:
        version = build.build_resume_version(db, application_id, _body().get('steer'))
    except build.ProfileEmpty as e:
        return jsonify({'error': str(e)}), 400
    except build.TailoringUnavailable as e:
        return jsonify({'error': str(e)}), 503

    return jsonify({
        **version,
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


@bp.patch('/resumes/<version_id>')
def edit_resume(version_id):
    """Apply the user's corrections and re-render, in place.

    In place rather than a new version per save: a version per keystroke buries
    the one that matters. What protects the record instead is the 409 below —
    once the application has been sent, `resume_versions` is evidence of what
    the employer received and stops being editable.

    No model call, so no `priority` gate: this renders exactly what it is
    given, which is the whole point of an edit route existing beside `tailor`.
    """
    db = get_db()
    row = db.execute(
        'SELECT rv.*, a.applied_at FROM resume_versions rv'
        ' JOIN applications a ON a.id = rv.application_id WHERE rv.id=?',
        (version_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    if row['purged_at']:
        return jsonify({'error': 'This resume was deleted under the retention policy.'}), 410
    if row['applied_at']:
        return jsonify({
            'error': 'This application has been sent, so its resume is now a record '
                     'of what the employer received. Tailor a new version instead.',
        }), 409

    try:
        stored = json.loads(row['content'] or '{}')
    except ValueError:
        stored = {}

    content = tailor.apply_edits(stored, _body())

    application_id = row['application_id']
    job = db.execute(
        'SELECT j.title, j.company, j.location, j.description FROM applications a'
        ' JOIN jobs j ON j.id = a.job_id WHERE a.id=?',
        (application_id,),
    ).fetchone()
    loaded = profile_mod.load_profile(db)
    html = render.render_html(loaded, content, dict(job) if job else None)

    # Same paths as the original render, so the download URLs keep working and
    # a stale PDF can never outlive the HTML it was made from.
    pdf_path = storage.resume_path(application_id, version_id, 'pdf')
    docx_path = storage.resume_path(application_id, version_id, 'docx')
    wrote_pdf = bool(pdf_path) and render.render_pdf(html, pdf_path)
    wrote_docx = bool(docx_path) and render.render_docx(loaded, content, docx_path)

    db.execute(
        'UPDATE resume_versions SET content=?, html=?, pdf_path=?, docx_path=? WHERE id=?',
        (json.dumps(content), html,
         str(pdf_path) if wrote_pdf else None,
         str(docx_path) if wrote_docx else None, version_id),
    )
    db.commit()

    return jsonify({
        'id': version_id,
        'content': content,
        'html': html,
        'pdfAvailable': wrote_pdf,
        'docxAvailable': wrote_docx,
    })


@bp.get('/resumes/<version_id>/download.<ext>')
def download_resume(version_id, ext):
    """Serve a rendered resume.

    The path is recomputed from the two ids rather than read from the row, so a
    tampered `pdf_path` cannot walk out of the jobs root — the same reason the
    fanfic image route rebuilds its path instead of trusting the database.
    """
    if ext not in ('pdf', 'docx'):
        return jsonify({'error': 'Not found'}), 404

    db = get_db()
    row = db.execute(
        'SELECT application_id, purged_at FROM resume_versions WHERE id=?', (version_id,)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    if row['purged_at']:
        return jsonify({'error': 'This resume was deleted under the retention policy.'}), 410

    path = storage.resume_path(row['application_id'], version_id, ext)
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404

    # The name on the file is what an employer files away, so it carries the
    # user's name rather than the id this route was reached by.
    name_row = db.execute('SELECT full_name FROM job_profile WHERE id=1').fetchone()
    download_name = render.download_filename(
        name_row['full_name'] if name_row else '', ext
    )

    mimetype = ('application/pdf' if ext == 'pdf'
                else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    return send_file(path, mimetype=mimetype, as_attachment=True,
                     download_name=download_name, conditional=True)


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
# Recorded answers — what was actually put in one employer's form
# --------------------------------------------------------------------------
#
# Deliberately a different noun from the route above. `POST .../answers`
# *generates* answers and persists nothing; these store what the user really
# submitted, which is a record rather than a suggestion.

# A form field's label, and the answer typed into it. Both are bounded because
# they arrive from a content script running in a page we do not control.
_MAX_ANSWER_QUESTION = 500
_MAX_ANSWER_TEXT = 10_000
_MAX_ANSWER_BATCH = 200
_ANSWER_SOURCES = ('profile', 'bank', 'generated', 'unanswered', 'edited')


def _recorded_answers(db, application_id: str) -> list[dict]:
    return [
        row_to_dict(r) for r in db.execute(
            'SELECT * FROM application_answers WHERE application_id=?'
            ' ORDER BY ord, created_at',
            (application_id,),
        ).fetchall()
    ]


@bp.get('/applications/<application_id>/recorded-answers')
def list_recorded_answers(application_id):
    db = get_db()
    if _application_row(db, application_id) is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'answers': _recorded_answers(db, application_id)})


@bp.post('/applications/<application_id>/recorded-answers')
def record_answers(application_id):
    """Store a batch of answers, upserting on the question text.

    Upsert rather than replace-all because a Workday application spans several
    pages: a replace would drop page one the moment page two was recorded. It
    also makes the extension's record-as-you-fill safe to call repeatedly —
    correcting a field and re-recording updates the row instead of leaving two
    contradictory answers to the same question.
    """
    db = get_db()
    if _application_row(db, application_id) is None:
        return jsonify({'error': 'Not found'}), 404

    incoming = _body().get('answers')
    if not isinstance(incoming, list):
        return jsonify({'error': 'answers required'}), 400

    existing = {
        r['question']: r['id'] for r in db.execute(
            'SELECT id, question FROM application_answers WHERE application_id=?',
            (application_id,),
        ).fetchall()
    }
    next_ord = db.execute(
        'SELECT COALESCE(MAX(ord), -1) + 1 AS n FROM application_answers'
        ' WHERE application_id=?',
        (application_id,),
    ).fetchone()['n']

    now = _now()
    written = 0
    for item in incoming[:_MAX_ANSWER_BATCH]:
        if not isinstance(item, dict):
            continue
        question = str(item.get('question') or '').strip()[:_MAX_ANSWER_QUESTION]
        if not question:
            continue
        answer = str(item.get('answer') or '')[:_MAX_ANSWER_TEXT]
        source = item.get('source')
        if source not in _ANSWER_SOURCES:
            source = 'generated'
        page_url = str(item.get('pageUrl') or '')[:2000]

        found = existing.get(question)
        if found:
            db.execute(
                'UPDATE application_answers SET answer=?, source=?, page_url=?,'
                ' updated_at=? WHERE id=?',
                (answer, source, page_url, now, found),
            )
        else:
            answer_id = str(ULID())
            db.execute(
                'INSERT INTO application_answers (id, application_id, question,'
                ' answer, source, page_url, ord, created_at, updated_at)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (answer_id, application_id, question, answer, source, page_url,
                 next_ord, now, now),
            )
            existing[question] = answer_id
            next_ord += 1
        written += 1

    db.commit()
    return jsonify({'written': written, 'answers': _recorded_answers(db, application_id)})


@bp.delete('/applications/<application_id>/recorded-answers/<answer_id>')
def delete_recorded_answer(application_id, answer_id):
    db = get_db()
    cur = db.execute(
        'DELETE FROM application_answers WHERE id=? AND application_id=?',
        (answer_id, application_id),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})


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


# --------------------------------------------------------------------------
# Discovery: saved searches, the feed, and the queue
# --------------------------------------------------------------------------

_SEARCH_KINDS = ('adzuna', 'greenhouse', 'lever', 'ashby')


def _search_row(db, search_id):
    return db.execute('SELECT * FROM job_searches WHERE id=?', (search_id,)).fetchone()


def _search_dict(row) -> dict:
    result = row_to_dict(row)
    try:
        result['params'] = json.loads(row['params']) if row['params'] else {}
    except ValueError:
        result['params'] = {}
    return result


@bp.get('/searches')
def list_searches():
    rows = get_db().execute(
        'SELECT * FROM job_searches ORDER BY kind, label, created_at'
    ).fetchall()
    return jsonify([_search_dict(r) for r in rows])


@bp.post('/searches')
def create_search():
    body = _body()
    kind = (body.get('kind') or '').strip()
    if kind not in _SEARCH_KINDS:
        return jsonify({'error': f'Unknown source {kind!r}.'}), 400

    params = body.get('params') or {}
    if not isinstance(params, dict):
        return jsonify({'error': 'params must be an object.'}), 400

    # Validate the slug now rather than at the first sweep — an invalid source
    # that fails silently at 3am is worse than a rejected form.
    if kind != 'adzuna':
        try:
            sources.clean_slug(params.get('slug'))
        except sources.SourceError as e:
            return jsonify({'error': str(e)}), 400

    now = _now()
    search_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO job_searches (id, kind, label, params, enabled, interval_hours,'
        ' created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (search_id, kind, (body.get('label') or '').strip(), json.dumps(params),
         0 if body.get('enabled') is False else 1,
         int(body.get('intervalHours') or sync.DEFAULT_INTERVAL_HOURS), now, now),
    )
    db.commit()
    return jsonify(_search_dict(_search_row(db, search_id))), 201


@bp.patch('/searches/<search_id>')
def update_search(search_id):
    db = get_db()
    if _search_row(db, search_id) is None:
        return jsonify({'error': 'Not found'}), 404

    body = _body()
    updates = {'updated_at': _now()}
    if 'label' in body:
        updates['label'] = (body.get('label') or '').strip()
    if 'enabled' in body:
        updates['enabled'] = 1 if body['enabled'] else 0
    if 'intervalHours' in body:
        updates['interval_hours'] = int(body['intervalHours'] or sync.DEFAULT_INTERVAL_HOURS)
    if 'params' in body:
        params = body['params'] or {}
        if not isinstance(params, dict):
            return jsonify({'error': 'params must be an object.'}), 400
        updates['params'] = json.dumps(params)

    assignments = ', '.join(f'{col}=?' for col in updates)
    db.execute(f'UPDATE job_searches SET {assignments} WHERE id=?',
               (*updates.values(), search_id))
    db.commit()
    return jsonify(_search_dict(_search_row(db, search_id)))


@bp.delete('/searches/<search_id>')
def delete_search(search_id):
    db = get_db()
    db.execute('DELETE FROM job_searches WHERE id=?', (search_id,))
    db.commit()
    return jsonify({'ok': True})


@bp.post('/searches/resolve')
def resolve_company():
    """A company careers URL → the board behind it, verified.

    Synchronous, and deliberately does not create anything: the user sees what
    was found and how many postings it has before deciding. Slugs cannot be
    guessed (Ada's Greenhouse board is `ada18`), which is why this exists
    instead of a text field.
    """
    url = (_body().get('url') or '').strip()
    if not url:
        return jsonify({'error': 'Need a careers page URL.'}), 400
    if not url.startswith(('http://', 'https://')):
        url = f'https://{url}'

    return jsonify(resolve.resolve_careers_page(url).to_dict())


@bp.post('/searches/<search_id>/run')
def run_search(search_id):
    """Sync one search now. Synchronous: one board call is a second or two and
    the user is watching for the result."""
    db = get_db()
    row = _search_row(db, search_id)
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(sync.sync_search(db, _search_dict(row)))


@bp.post('/sync')
def run_sync():
    return jsonify(sync.run_sync_sweep())


@bp.post('/rescore')
def rescore():
    """Recompute every undismissed posting's score against the current profile.

    Called after a profile edit. The profile changes far more often than
    postings arrive, so without this the feed keeps ranking against whatever
    the skills list said last week. Pure string work, no model, so it is fast
    enough to run inline.
    """
    return jsonify({'rescored': sync.rescore_all(get_db())})


@bp.get('/feed')
def job_feed():
    """The triage feed: undismissed postings with no application yet.

    Rejected postings are excluded; **pending ones are not**. That is what
    keeps the feed working when the model is off or the triage backlog has not
    drained — a row nobody has judged shows exactly as it did before this
    feature existed, rather than the feed silently emptying.

    Ordering is the fit bucket first, then the deterministic keyword score
    within it. The model chooses the bucket but never the order inside one:
    a coarse grouping is stable between refreshes in a way a model-produced
    0-100 score would not be, which is the property `job_match.py` set out to
    protect.
    """
    db = get_db()
    limit = min(int(request.args.get('limit') or 100), 500)
    rows = db.execute(
        """
        SELECT j.* FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        WHERE j.dismissed = 0 AND a.id IS NULL AND j.triage_state != 'rejected'
        ORDER BY CASE j.triage_fit
                     WHEN 'strong' THEN 0 WHEN 'possible' THEN 1
                     WHEN 'stretch' THEN 2 ELSE 3 END,
                 j.match_score IS NULL, j.match_score DESC, j.posted_at DESC,
                 j.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    feed = [_feed_item(row) for row in rows]
    return jsonify(feed)


def _feed_item(row) -> dict:
    item = row_to_dict(row)
    try:
        item['matchReasons'] = json.loads(row['match_reasons']) if row['match_reasons'] else None
    except ValueError:
        item['matchReasons'] = None
    try:
        item['triageFlags'] = json.loads(row['triage_flags']) if row['triage_flags'] else []
    except ValueError:
        item['triageFlags'] = []
    # The description is the largest column by far and the card shows only a
    # few lines of it; sending all of it for 100 postings is megabytes. Once a
    # posting has been triaged the card shows `triageSummary` instead, and this
    # is only the fallback for a row still waiting on a verdict.
    item['description'] = (row['description'] or '')[:600]
    return item


@bp.get('/filtered')
def filtered_jobs():
    """What triage threw out, so it can be audited.

    This route is the reason rejection is a state rather than a delete. A
    filter that discards job opportunities has to be reviewable, or a bad rule
    is invisible until the search is over — the lesson the backfill's bogus
    "Software" company taught, where sampling by recency hid a row that had
    quietly absorbed 144 email links.
    """
    db = get_db()
    limit = min(int(request.args.get('limit') or 200), 1000)
    rows = db.execute(
        "SELECT * FROM jobs WHERE triage_state IN ('rejected', 'error')"
        ' ORDER BY triage_at DESC, created_at DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return jsonify([_feed_item(row) for row in rows])


@bp.post('/<job_id>/triage/restore')
def restore_job(job_id):
    """Put a wrongly-rejected posting back in the feed."""
    db = get_db()
    if not triager.restore(db, job_id):
        return jsonify({'error': 'Not found, or not rejected.'}), 404
    row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    return jsonify(_feed_item(row))


@bp.post('/<job_id>/triage')
def triage_job(job_id):
    """Judge one posting now, synchronously.

    Interactive, so it runs inline rather than through the worker — the user is
    looking at the result. Measured at 3-8 seconds against a full posting.
    """
    token = priority.begin('job-triage')
    try:
        result = triager.process_one(job_id)
    finally:
        priority.end(token)
    if not result['ok'] and result.get('error') == 'Not found':
        return jsonify({'error': 'Not found'}), 404
    row = get_db().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    return jsonify({**result, 'job': _feed_item(row) if row else None})


@bp.post('/<job_id>/triage/reset')
def reset_job_triage(job_id):
    """Send a posting back through triage — the explicit retry after an error."""
    db = get_db()
    if not triager.reset_pending(db, job_id):
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})


@bp.get('/triage/status')
def triage_status():
    db = get_db()
    return jsonify({
        'enabled': triager.is_enabled(db),
        'pending': triager.pending_count(db),
        'rejected': db.execute(
            "SELECT COUNT(*) c FROM jobs WHERE triage_state='rejected'"
        ).fetchone()['c'],
        'failed': db.execute(
            'SELECT COUNT(*) c FROM jobs WHERE triage_error IS NOT NULL'
        ).fetchone()['c'],
        **triager.status(),
    })


@bp.post('/triage/gate')
def run_triage_gate():
    """Apply the free title gate to everything pending. No model."""
    return jsonify(triager.run_gate_sweep(get_db()))


@bp.post('/<job_id>/dismiss')
def dismiss_job(job_id):
    db = get_db()
    body = _body()
    dismissed = 0 if body.get('dismissed') is False else 1
    db.execute('UPDATE jobs SET dismissed=?, updated_at=? WHERE id=?',
               (dismissed, _now(), job_id))
    db.commit()
    row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.post('/<job_id>/queue')
def queue_job(job_id):
    """Queue a posting for resume generation. Returns immediately.

    The whole point of the phone half of this feature: tapping Queue is a
    judgement, not a request to wait on a model. The worker in
    `backend/jobs/queue.py` does the slow part when the machine is quiet.
    """
    db = get_db()
    if db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone() is None:
        return jsonify({'error': 'Not found'}), 404

    now = _now()
    steer = (_body().get('steer') or '').strip()
    existing = db.execute(
        'SELECT * FROM applications WHERE job_id=?', (job_id,)
    ).fetchone()

    if existing is None:
        application_id = str(ULID())
        db.execute(
            'INSERT INTO applications (id, job_id, status, steer, queued_at,'
            ' created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (application_id, job_id, 'draft', steer, now, now, now),
        )
    else:
        application_id = existing['id']
        # Re-queueing is how you retry a failure, so the error is cleared here
        # rather than left to confuse the next look at the card.
        db.execute(
            'UPDATE applications SET queued_at=?, queue_error=NULL, '
            'steer=COALESCE(NULLIF(?, \'\'), steer), updated_at=? WHERE id=?',
            (now, steer, now, application_id),
        )
    db.commit()

    row = db.execute('SELECT * FROM applications WHERE id=?', (application_id,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@bp.get('/queue/status')
def queue_status():
    db = get_db()
    # Failed ones are counted separately, not as pending — they will not be
    # picked up again without an explicit re-queue, so calling them "waiting"
    # would promise something the worker is not going to do.
    pending = db.execute(
        """SELECT COUNT(*) AS c FROM applications a
           WHERE a.queued_at IS NOT NULL AND a.status='draft'
             AND a.queue_error IS NULL
             AND NOT EXISTS (SELECT 1 FROM resume_versions rv
                             WHERE rv.application_id = a.id)"""
    ).fetchone()
    failed = db.execute(
        'SELECT COUNT(*) AS c FROM applications WHERE queue_error IS NOT NULL'
    ).fetchone()
    return jsonify({
        **queue_mod.status(),
        'pending': pending['c'] if pending else 0,
        'failed': failed['c'] if failed else 0,
    })


@bp.post('/queue/drain')
def drain_queue():
    """Kick the queue by hand, ignoring the idle gate.

    The scheduler's drain waits for the machine to be quiet. This one does not:
    asking for it explicitly *is* the signal that now is a good time.
    """
    db = get_db()
    pending = queue_mod.next_queued(db)
    if pending is None:
        return jsonify({'submitted': None, 'reason': 'nothing queued'})
    submitted = queue_mod.submit(pending['id'])
    return jsonify({
        'submitted': pending['id'] if submitted else None,
        'reason': '' if submitted else 'already running',
    })


@bp.post('/<job_id>/rationale')
def job_rationale(job_id):
    """The on-demand advisory paragraph. Never changes `match_score`."""
    db = get_db()
    row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'Not found'}), 404

    loaded = profile_mod.load_profile(db)
    if profile_mod.is_empty(loaded):
        return jsonify({'error': 'Your profile is empty — nothing to compare against.'}), 400

    try:
        reasons = json.loads(row['match_reasons']) if row['match_reasons'] else {}
    except ValueError:
        reasons = {}

    job = {'title': row['title'], 'company': row['company'],
           'location': row['location'], 'description': row['description']}

    # The user is waiting on this one, so it takes an interactive mark rather
    # than deferring to it.
    token = priority.begin('job-rationale')
    try:
        assessment = job_match.assess_match(
            job, profile_mod.profile_text(loaded)[:4000], reasons
        )
    finally:
        priority.end(token)

    if assessment is None:
        return jsonify({'error': 'The local model is unavailable.'}), 503

    # Stored alongside the deterministic report, never replacing it — the score
    # and the sort stay computed.
    merged = {**reasons, 'assessment': assessment, 'assessedAt': _now()}
    db.execute('UPDATE jobs SET match_reasons=?, updated_at=? WHERE id=?',
               (json.dumps(merged), _now(), job_id))
    db.commit()
    return jsonify(assessment)
