"""Immutable issue PDFs on disk; small, versioned markup documents in SQLite."""
import json
import math
import os
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

from ulid import ULID

from backend.db.connection import get_db
from backend.newspapers.storage import newspapers_root

MAX_PDF_BYTES = 250 * 1024 * 1024
issue_lock = threading.Lock()


def archive_root():
    return Path(os.environ.get('NEWSPAPERS_ARCHIVE_ROOT') or newspapers_root()).expanduser().resolve()


def validate_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Use an issue date in YYYY-MM-DD format')
    return value


def issue_path(value):
    return archive_root() / 'toronto-star' / f'{validate_date(value)}.pdf'


def get_issue(value):
    return get_db().execute('SELECT * FROM newspaper_issues WHERE date = ?', (validate_date(value),)).fetchone()


def store_issue(value, stream):
    """Publish only complete PDFs, never replace an issue underneath its markup."""
    from pypdf import PdfReader, PdfWriter

    path = issue_path(value)
    with issue_lock:
        if get_issue(value):
            raise FileExistsError('This issue is already archived')
        path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as output:
                name = output.name
                size = 0
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise ValueError('PDF exceeds the 250 MB issue limit')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            try:
                reader = PdfReader(name)
                if reader.is_encrypted:
                    if not reader.decrypt(''):
                        raise ValueError()
                    # PressReader uses passwordless PDF encryption. Normalize
                    # it so both the reader and pdf-lib markup export can open
                    # the archived file, retaining the pages and their content.
                    writer = PdfWriter(clone_from=reader)
                    with tempfile.TemporaryFile() as normalized:
                        writer.write(normalized)
                        size = normalized.tell()
                        if size > MAX_PDF_BYTES:
                            raise ValueError()
                        normalized.seek(0)
                        with open(name, 'wb') as target:
                            while chunk := normalized.read(1024 * 1024):
                                target.write(chunk)
                            target.flush()
                            os.fsync(target.fileno())
                if not 0 < len(reader.pages) <= 500:
                    raise ValueError()
                page_count = len(reader.pages)
            except Exception:
                raise ValueError('Choose a readable, unencrypted PDF with 1–500 pages') from None
            os.replace(name, path)
            db = get_db()
            db.execute(
                'INSERT INTO newspaper_issues (id, date, pdf_path, byte_size, page_count, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (str(ULID()), value, str(path), size, page_count, int(time.time())),
            )
            db.commit()
        finally:
            if name:
                Path(name).unlink(missing_ok=True)
    return get_issue(value)


def public_issue(row):
    return {'date': row['date'], 'byteSize': row['byte_size'], 'pageCount': row['page_count'],
            'pdfUrl': f"/api/newspapers/issues/{row['date']}/pdf"}


def marked_pages(markup):
    """Page numbers carrying at least one stroke, for the Journal feed's stat.

    Reads the stored markup rather than a counter column: the count is a pure
    function of the strokes, and a column would be one more thing every write
    path had to remember to keep true.
    """
    try:
        strokes = json.loads(markup or '[]')
    except ValueError:
        return set()
    if not isinstance(strokes, list):
        return set()
    return {s['page'] for s in strokes
            if isinstance(s, dict) and type(s.get('page')) is int}


def validate_markup(data, pages):
    if not isinstance(data, list) or len(data) > 10000:
        raise ValueError('Invalid markup')
    total = 0
    for stroke in data:
        if not isinstance(stroke, dict) or set(stroke) != {'page', 'tool', 'points'}:
            raise ValueError('Invalid stroke')
        if type(stroke['page']) is not int or not 1 <= stroke['page'] <= pages or stroke['tool'] not in ('pen', 'highlight'):
            raise ValueError('Invalid stroke page or tool')
        points = stroke['points']
        if not isinstance(points, list) or not 1 <= len(points) <= 10000:
            raise ValueError('Invalid stroke points')
        total += len(points)
        if total > 100000:
            raise ValueError('This issue has too much markup')
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(
                type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in point
            ):
                raise ValueError('Invalid stroke coordinate')
    return json.dumps(data, separators=(',', ':'))
