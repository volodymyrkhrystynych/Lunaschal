import json
import time
from datetime import datetime, timezone

from flask import jsonify, request, send_file

from backend.db.connection import get_db
from backend.journal_moment import journal_moment
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


@bp.get('/issues/journal')
def journal_issues():
    """Archived issues for the Journal feed, newest first, each with how much of
    it has been written on.

    Filed under the day the issue was archived rather than under its own date,
    because that is the day it entered the record the feed is a record of — the
    same choice the archived-papers feed makes. The two agree on any normally
    downloaded issue and differ only when an old edition is uploaded by hand,
    where the upload day is the honest one.

    Inside that day the card sits at the last time the issue was *read* — the
    newer of opening it and marking it up — and not at created_at, which is
    whenever the overnight downloader ran and says nothing about the reading.
    An issue nobody has opened has no such moment and goes to the end of its
    day, above that day's last entry: it is the paper still waiting, not an
    event that happened at 6am. See backend/journal_moment.py.
    """
    rows = get_db().execute('SELECT * FROM newspaper_issues ORDER BY created_at DESC, date DESC').fetchall()
    dated = [(journal_moment(row['last_read_at'], row['created_at'], unworked_at_day_end=True), row)
             for row in rows]
    # Sorted here rather than in SQL: the key is computed, and buildFeed's n-way
    # merge (src/lib/journalFeed.ts) documents that every source it is handed is
    # already newest-first. Sorting on the moment alone keeps Python's stability
    # meaningful, so issues that tie — every unread pair from one day lands on
    # that day's last second — hold the archive order the query gave them.
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return jsonify([{**issues.public_issue(row),
                     'archivedAt': datetime.fromtimestamp(at, tz=timezone.utc).isoformat(),
                     'markedPages': len(issues.marked_pages(row['markup']))}
                    for at, row in dated])


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


@bp.post('/issues/<date>/opened')
def mark_issue_opened(date):
    """The reader has this issue on screen.

    A route of its own rather than a side effect of GET /markup, which is the
    request the reader actually makes on open: a GET that writes is a trap for
    whoever later puts a cache in front of it, and "give me the strokes" and "a
    person is reading this" are not the same event even when they arrive
    together.
    """
    row = lookup(date)
    if row is None:
        return jsonify(error='Issue not found'), 404
    db = get_db()
    db.execute('UPDATE newspaper_issues SET last_read_at=? WHERE date=?', (int(time.time()), date))
    db.commit()
    return jsonify(ok=True)


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
        # last_read_at rides along inside the compare-and-set rather than in a
        # second statement: a refused save (a stale revision) must not move the
        # card, and one UPDATE cannot half-apply.
        cursor = db.execute('UPDATE newspaper_issues SET markup = ?, revision = revision + 1, last_read_at = ?'
                            ' WHERE date = ? AND revision = ?',
                            (markup, int(time.time()), date, body['revision']))
        db.commit()
    if not cursor.rowcount:
        return jsonify(error='Markup changed in another reader. Reopen the issue before editing.'), 409
    return jsonify(revision=body['revision'] + 1)
