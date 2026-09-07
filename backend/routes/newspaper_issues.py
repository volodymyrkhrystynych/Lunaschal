import json

from flask import jsonify, request, send_file

from backend.db.connection import get_db
from backend.newspapers import issues, pressreader, scheduler
from backend.routes.newspapers import bp


@bp.get('/pressreader')
def pressreader_status():
    db = get_db()
    settings = db.execute('SELECT newspapers_auto_download FROM settings WHERE id=1').fetchone()
    jobs = db.execute('SELECT date, status, error FROM newspaper_downloads ORDER BY created_at DESC LIMIT 90').fetchall()
    return jsonify(sessionSaved=pressreader.session_path().is_file(), autoDownload=bool(settings and settings[0]),
                   jobs=[dict(row) for row in jobs])


@bp.put('/pressreader')
def pressreader_settings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or type(body.get('autoDownload')) is not bool:
        return jsonify(error='autoDownload must be true or false'), 400
    db = get_db()
    db.execute('UPDATE settings SET newspapers_auto_download=? WHERE id=1', (int(body['autoDownload']),))
    db.commit()
    scheduler.start_newspaper_scheduler()
    return pressreader_status()


@bp.post('/issues/<date>/download')
def download_issue(date):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        issues.validate_date(date)
        if date > datetime.now(ZoneInfo('America/Toronto')).date().isoformat():
            raise ValueError('Cannot download a future issue')
        job = scheduler.queue_issue(date, retry=True)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    scheduler.start_newspaper_scheduler()
    return jsonify(date=job['date'], status=job['status'], error=job['error']), 202


@bp.get('/issues')
def list_issues():
    rows = get_db().execute('SELECT * FROM newspaper_issues ORDER BY date DESC').fetchall()
    return jsonify({'issues': [issues.public_issue(r) for r in rows], 'archivePath': str(issues.archive_root())})


@bp.post('/issues/<date>')
def upload_issue(date):
    request.max_content_length = issues.MAX_PDF_BYTES + 1024 * 1024
    upload = request.files.get('file')
    if upload is None:
        return jsonify(error='Choose a PDF'), 400
    try:
        row = issues.store_issue(date, upload.stream)
    except FileExistsError as exc:
        return jsonify(error=str(exc)), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except OSError:
        return jsonify(error='Archive storage is unavailable; check the configured drive'), 503
    return jsonify(issues.public_issue(row)), 201


def lookup(date):
    try:
        return issues.get_issue(date)
    except ValueError:
        return None


@bp.get('/issues/<date>/pdf')
def issue_pdf(date):
    row = lookup(date)
    path = issues.issue_path(date) if row else None
    if row is None or str(path) != row['pdf_path'] or not path.is_file():
        return jsonify(error='Issue PDF is unavailable; check the archive drive'), 404
    return send_file(path, mimetype='application/pdf', conditional=True,
                     download_name=f'toronto-star-{date}.pdf')


@bp.get('/issues/<date>/markup')
def read_markup(date):
    row = lookup(date)
    if row is None:
        return jsonify(error='Issue not found'), 404
    return jsonify(revision=row['revision'], strokes=json.loads(row['markup']))


@bp.put('/issues/<date>/markup')
def write_markup(date):
    request.max_content_length = 8 * 1024 * 1024
    if request.content_length and request.content_length > 8 * 1024 * 1024:
        return jsonify(error='Markup is too large'), 413
    row = lookup(date)
    if row is None:
        return jsonify(error='Issue not found'), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or type(body.get('revision')) is not int:
        return jsonify(error='A markup revision is required'), 400
    try:
        markup = issues.validate_markup(body.get('strokes'), row['page_count'])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    with issues.issue_lock:
        db = get_db()
        cursor = db.execute('UPDATE newspaper_issues SET markup = ?, revision = revision + 1 WHERE date = ? AND revision = ?',
                            (markup, date, body['revision']))
        db.commit()
    if not cursor.rowcount:
        return jsonify(error='Markup changed in another reader. Reopen the issue before editing.'), 409
    return jsonify(revision=body['revision'] + 1)
