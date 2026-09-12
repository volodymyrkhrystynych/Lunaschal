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

    'pages' are the pictures the reader has rendered of this issue — the pages
    written on, plus page 1 as the cover. They exist only for issues that have
    been opened since the reader learned to make them, so an older issue can
    report marked pages and carry no pictures at all.
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
    # One scandir names every issue that has ever been opened in the reader,
    # and the rest — most of an archive — cost nothing, because an unopened
    # issue has no directory to look in.
    rendered = issues.snapshot_index()
    return jsonify([{**issues.public_issue(row),
                     'archivedAt': datetime.fromtimestamp(at, tz=timezone.utc).isoformat(),
                     'markedPages': len(issues.marked_pages(row['markup'])),
                     'pages': [{'page': page, 'imageUrl': page_image_url(row['date'], page, mtime)}
                               for page, mtime in rendered.get(row['date'], [])]}
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


def page_image_url(date, page, mtime):
    """Cache-busted on the file's own mtime rather than on the issue revision:
    revision bumps for the whole issue on every markup save, so using it would
    re-fetch forty thumbnails because one page was drawn on. Same idea as
    backend/routes/paper.py's page_image_url, keyed differently for that
    reason."""
    return f'/api/newspapers/issues/{date}/pages/{page}?v={mtime}'


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


@bp.get('/issues/<date>/pages')
def list_issue_pages(date):
    """Which pages of this issue already have a rendered picture.

    A route of its own rather than a field on GET /markup, which the reader
    asks for in the same breath. {revision, strokes} is not only a response: it
    is also the PUT body, and the shape of the localStorage draft the reader
    recovers from (src/components/NewspaperReader.tsx). A field that only ever
    travels one of those three directions is how a `delete body.pages` gets
    written later. The extra round trip is free — the reader opens with a
    Promise.all in which the PDF is the long pole.
    """
    row = lookup(date)
    if row is None:
        return jsonify(error='Issue not found'), 404
    return jsonify(pages=[{'page': page, 'url': page_image_url(date, page, mtime), 'updatedAt': mtime}
                          for page, mtime in issues.snapshot_pages(date)])


@bp.get('/issues/<date>/pages/<int:page>')
def issue_page_image(date, page):
    row = lookup(date)
    if row is None:
        return jsonify(error='Issue not found'), 404
    if not 1 <= page <= row['page_count']:
        return jsonify(error='No such page in this issue'), 404
    path = issues.snapshot_path(date, page)
    if not path.is_file():
        return jsonify(error='Page image has not been rendered yet'), 404
    return send_file(path, mimetype='image/jpeg', conditional=True)


@bp.put('/issues/<date>/pages/<int:page>')
def store_issue_page_image(date, page):
    """A page picture from the reader, as a raw JPEG body.

    Not multipart: Paper posts its snapshot as a form part because it rides
    alongside the strokes, and here the picture is the whole payload.

    Deliberately does not touch last_read_at. The worker that sends these is
    machinery, not a person reading; POST /issues/<date>/opened is the event.
    """
    request.max_content_length = issues.MAX_SNAPSHOT_BYTES + 1024
    if request.content_length and request.content_length > issues.MAX_SNAPSHOT_BYTES:
        return jsonify(error='Page image is too large'), 413
    row = lookup(date)
    if row is None:
        return jsonify(error='Issue not found'), 404
    if not 1 <= page <= row['page_count']:
        return jsonify(error='No such page in this issue'), 400
    try:
        path = issues.store_snapshot(date, page, request.get_data())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except OSError:
        return jsonify(error='Page image storage is unavailable'), 503
    mtime = int(path.stat().st_mtime)
    return jsonify(page=page, url=page_image_url(date, page, mtime), updatedAt=mtime)


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
    # Only after the compare-and-set has actually landed: before it, a refused
    # save would delete the thumbnails of the markup it was refused in favour
    # of — the same reason last_read_at rides inside the CAS above.
    #
    # Page 1 survives whatever happens to the ink. It is the issue's cover, and
    # the cover is what gives an unmarked paper a picture in the Journal feed;
    # without this line, erasing everything would also erase the card.
    try:
        issues.prune_snapshots(date, issues.marked_pages(markup) | {1})
    except OSError:
        pass  # The markup is saved. A stale thumbnail is not worth a 500.
    return jsonify(revision=body['revision'] + 1)
