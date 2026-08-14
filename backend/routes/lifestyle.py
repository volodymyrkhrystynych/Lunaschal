"""Lifestyle tab: workouts, activity heatmap, body weight, selfies, calories.

See docs/lifestyle-tab.md. Tasks are not here on purpose — the tab renders daily
tasks and to-dos through /api/tasks, so a to-do ticked off in the briefing and
one ticked off here are the same row. (Chores were folded into the to-do list;
`normalize_list` in backend/todo_recurrence.py still accepts the old name.)

/trends is the exception to "lifestyle owns lifestyle data": it counts journal
entries and sent job applications, which live in other features' tables. It sits
here because the chart does — one endpoint for one card beats making the client
stitch two feature APIs together to draw two lines on one axis.
"""
import re
import time
from datetime import date as date_cls, timedelta

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.ai.background import run_bg
from backend.ai.workouts import parse_workout
from backend.db.connection import build_update, get_db, row_to_dict
from backend.day_boundary import DAY_ROLLOVER_HOUR, day_bounds, day_key_for
from backend.lifestyle import storage
from backend.lifestyle.activity import is_activity_type, summarize_day
from backend.lifestyle.exercises import canonicalize, display_name
from backend.lifestyle.trends import week_start, weekly_series

bp = Blueprint('lifestyle', __name__, url_prefix='/api/lifestyle')

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# A selfie straight off a phone camera is a few MB; anything past this is not a
# photo of a person and shouldn't be written to disk.
MAX_SELFIE_BYTES = 20 * 1024 * 1024

# Intensity is a 5-star scale, not the old 1-10 RPE: the ten-point version was
# too subjective to rate consistently, so each star now has a written meaning
# (see src/lib/lifestyle.ts INTENSITY_LABELS, which the UI shows as tooltips).
# Rows written under the old scale were folded to 1-5 once, at startup, by
# `_migrate_workout_intensity_to_stars` in backend/db/connection.py.
INTENSITY_MAX = 5


def _today() -> str:
    return day_key_for()


def _parse_date(value, default=None):
    """Returns (iso_date_str, error_or_None). Absent/blank falls back to `default`."""
    if value is None or value == '':
        return default, None
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None, 'date must be YYYY-MM-DD'
    try:
        date_cls.fromisoformat(value)
    except ValueError:
        return None, 'date must be a real calendar date'
    return value, None


def _parse_int(value, name, low, high):
    """Returns (int_or_None, error_or_None). Absent/blank -> (None, None)."""
    if value is None or value == '':
        return None, None
    if isinstance(value, bool):
        return None, f'{name} must be a number'
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None, f'{name} must be a number'
    if not (low <= n <= high):
        return None, f'{name} must be between {low} and {high}'
    return n, None


def _parse_float(value, name, low, high):
    if value is None or value == '':
        return None, None
    if isinstance(value, bool):
        return None, f'{name} must be a number'
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None, f'{name} must be a number'
    if n != n or not (low <= n <= high):
        return None, f'{name} must be between {low} and {high}'
    return n, None


# --- Workout sessions --------------------------------------------------------

_SESSION_COLS = (
    'id, date, location_type, duration_minutes, intensity_rating, raw_text, '
    'notes, parse_status, created_at, updated_at'
)


def _session_exercises(db, session_ids: list[str]) -> dict[str, list[dict]]:
    """Exercises (with their sets) for a batch of sessions, keyed by session id.

    Batched rather than per-session so listing N workouts stays two queries
    instead of 2N.
    """
    if not session_ids:
        return {}
    marks = ','.join('?' * len(session_ids))
    ex_rows = db.execute(
        f'SELECT id, session_id, name_raw, name_canonical, position FROM workout_exercises'
        f' WHERE session_id IN ({marks}) ORDER BY position ASC',
        session_ids,
    ).fetchall()
    if not ex_rows:
        return {}
    ex_ids = [r['id'] for r in ex_rows]
    set_marks = ','.join('?' * len(ex_ids))
    set_rows = db.execute(
        f'SELECT id, exercise_id, weight, reps, set_order FROM workout_sets'
        f' WHERE exercise_id IN ({set_marks}) ORDER BY set_order ASC',
        ex_ids,
    ).fetchall()

    sets_by_ex: dict[str, list[dict]] = {}
    for r in set_rows:
        sets_by_ex.setdefault(r['exercise_id'], []).append(row_to_dict(r))

    by_session: dict[str, list[dict]] = {}
    for r in ex_rows:
        ex = row_to_dict(r)
        ex['displayName'] = display_name(r['name_canonical'])
        ex['sets'] = sets_by_ex.get(r['id'], [])
        by_session.setdefault(r['session_id'], []).append(ex)
    return by_session


def _sessions_with_exercises(db, rows) -> list[dict]:
    by_session = _session_exercises(db, [r['id'] for r in rows])
    out = []
    for r in rows:
        d = row_to_dict(r)
        d['exercises'] = by_session.get(r['id'], [])
        out.append(d)
    return out


def _known_exercise_names(db) -> list[str]:
    """Canonical names already in use, most-used first — `canonicalize` folds an
    ambiguous short name onto the earliest match, so the exercise actually
    trained wins over one logged once."""
    rows = db.execute(
        'SELECT name_canonical, COUNT(*) AS n FROM workout_exercises'
        ' GROUP BY name_canonical ORDER BY n DESC, name_canonical ASC'
    ).fetchall()
    return [r['name_canonical'] for r in rows]


def _replace_exercises(db, session_id: str, exercises: list[dict]) -> None:
    """Swap in a freshly parsed exercise list. Cascades drop the old sets."""
    db.execute('DELETE FROM workout_exercises WHERE session_id=?', (session_id,))
    known = _known_exercise_names(db)
    for position, ex in enumerate(exercises):
        canonical = canonicalize(ex['name'], known)
        if not canonical:
            continue
        if canonical not in known:
            known.append(canonical)
        ex_id = str(ULID())
        db.execute(
            'INSERT INTO workout_exercises(id, session_id, name_raw, name_canonical, position)'
            ' VALUES (?,?,?,?,?)',
            (ex_id, session_id, ex['name'], canonical, position),
        )
        for order, s in enumerate(ex.get('sets') or []):
            db.execute(
                'INSERT INTO workout_sets(id, exercise_id, weight, reps, set_order)'
                ' VALUES (?,?,?,?,?)',
                (str(ULID()), ex_id, s.get('weight'), s.get('reps'), order),
            )


def structure_workout(session_id: str, text: str) -> None:
    """Parse a session's raw text into exercises/sets. Safe to run in a worker
    thread; the raw text is never touched, so a failure only sets parse_status
    and the user can retry with POST /workouts/<id>/reparse."""
    parsed = parse_workout(text)
    db = get_db()
    if not db.execute('SELECT 1 FROM workout_sessions WHERE id=?', (session_id,)).fetchone():
        return
    if parsed is None:
        build_update(db, 'workout_sessions', {'parse_status': 'error'}, 'id=?', (session_id,))
        db.commit()
        return
    _replace_exercises(db, session_id, parsed)
    build_update(
        db, 'workout_sessions',
        {'parse_status': 'done', 'updated_at': int(time.time())},
        'id=?', (session_id,),
    )
    db.commit()


@bp.get('/workouts')
def list_workouts():
    limit = min(max(int(request.args.get('limit', 30)), 1), 200)
    offset = max(int(request.args.get('offset', 0)), 0)
    db = get_db()
    rows = db.execute(
        f'SELECT {_SESSION_COLS} FROM workout_sessions'
        ' ORDER BY date DESC, created_at DESC LIMIT ? OFFSET ?',
        (limit, offset),
    ).fetchall()
    return jsonify(_sessions_with_exercises(db, rows))


@bp.get('/workouts/<session_id>')
def get_workout(session_id):
    db = get_db()
    row = db.execute(
        f'SELECT {_SESSION_COLS} FROM workout_sessions WHERE id=?', (session_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_sessions_with_exercises(db, [row])[0])


@bp.post('/workouts')
def create_workout():
    body = request.get_json(silent=True) or {}

    location = body.get('locationType')
    if not is_activity_type(location):
        return jsonify({'error': 'locationType must be one of the four activity types'}), 400
    day, err = _parse_date(body.get('date'), _today())
    if err:
        return jsonify({'error': err}), 400
    duration, err = _parse_int(body.get('durationMinutes'), 'durationMinutes', 0, 24 * 60)
    if err:
        return jsonify({'error': err}), 400
    intensity, err = _parse_int(body.get('intensityRating'), 'intensityRating', 1, INTENSITY_MAX)
    if err:
        return jsonify({'error': err}), 400

    raw_text = (body.get('rawText') or '').strip() or None
    notes = (body.get('notes') or '').strip() or None

    now = int(time.time())
    session_id = str(ULID())
    # 'skipped' rather than 'pending' when there's nothing to parse, so the UI
    # never shows a spinner waiting on a parse that will not be scheduled.
    parse_status = 'pending' if raw_text else 'skipped'
    db = get_db()
    db.execute(
        'INSERT INTO workout_sessions(id, date, location_type, duration_minutes,'
        ' intensity_rating, raw_text, notes, parse_status, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?)',
        (session_id, day, location, duration, intensity, raw_text, notes, parse_status, now, now),
    )
    db.commit()

    if raw_text:
        run_bg(lambda: structure_workout(session_id, raw_text))

    row = db.execute(
        f'SELECT {_SESSION_COLS} FROM workout_sessions WHERE id=?', (session_id,)
    ).fetchone()
    return jsonify(_sessions_with_exercises(db, [row])[0]), 201


@bp.patch('/workouts/<session_id>')
def update_workout(session_id):
    body = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute(
        'SELECT raw_text FROM workout_sessions WHERE id=?', (session_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    updates: dict = {}
    if 'locationType' in body:
        if not is_activity_type(body['locationType']):
            return jsonify({'error': 'locationType must be one of the four activity types'}), 400
        updates['location_type'] = body['locationType']
    if 'date' in body:
        day, err = _parse_date(body['date'])
        if err or day is None:
            return jsonify({'error': err or 'date required'}), 400
        updates['date'] = day
    if 'durationMinutes' in body:
        duration, err = _parse_int(body['durationMinutes'], 'durationMinutes', 0, 24 * 60)
        if err:
            return jsonify({'error': err}), 400
        updates['duration_minutes'] = duration
    if 'intensityRating' in body:
        intensity, err = _parse_int(body['intensityRating'], 'intensityRating', 1, INTENSITY_MAX)
        if err:
            return jsonify({'error': err}), 400
        updates['intensity_rating'] = intensity
    if 'notes' in body:
        updates['notes'] = (body['notes'] or '').strip() or None

    reparse_text = None
    if 'rawText' in body:
        text = (body['rawText'] or '').strip() or None
        updates['raw_text'] = text
        # Editing the text invalidates the parsed sets hanging off the old text.
        updates['parse_status'] = 'pending' if text else 'skipped'
        if text != row['raw_text']:
            reparse_text = text
            if text is None:
                db.execute('DELETE FROM workout_exercises WHERE session_id=?', (session_id,))

    if not updates:
        return jsonify({'error': 'nothing to update'}), 400
    updates['updated_at'] = int(time.time())
    build_update(db, 'workout_sessions', updates, 'id=?', (session_id,))
    db.commit()

    if reparse_text:
        run_bg(lambda: structure_workout(session_id, reparse_text))
    return jsonify({'success': True})


@bp.post('/workouts/<session_id>/reparse')
def reparse_workout(session_id):
    """Re-run the AI parse over the untouched raw text (docs §2: a bad parse
    never loses data)."""
    db = get_db()
    row = db.execute(
        'SELECT raw_text FROM workout_sessions WHERE id=?', (session_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if not row['raw_text']:
        return jsonify({'error': 'no raw text to parse'}), 400
    text = row['raw_text']
    build_update(db, 'workout_sessions', {'parse_status': 'pending'}, 'id=?', (session_id,))
    db.commit()
    run_bg(lambda: structure_workout(session_id, text))
    return jsonify({'success': True})


@bp.delete('/workouts/<session_id>')
def delete_workout(session_id):
    db = get_db()
    cur = db.execute('DELETE FROM workout_sessions WHERE id=?', (session_id,))
    db.commit()
    if not cur.rowcount:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


# --- Activity heatmap --------------------------------------------------------

@bp.get('/heatmap')
def heatmap():
    """One entry per day that has at least one session, in [start, end].

    Days with nothing logged are simply absent — the client grids the calendar
    and looks days up, so sending ~365 empty rows would be wasted payload.
    """
    start, err = _parse_date(request.args.get('start'))
    if err:
        return jsonify({'error': err}), 400
    end, err = _parse_date(request.args.get('end'))
    if err:
        return jsonify({'error': err}), 400

    query = f'SELECT {_SESSION_COLS} FROM workout_sessions'
    clauses, params = [], []
    if start:
        clauses.append('date >= ?')
        params.append(start)
    if end:
        clauses.append('date <= ?')
        params.append(end)
    if clauses:
        query += ' WHERE ' + ' AND '.join(clauses)
    query += ' ORDER BY date ASC, created_at ASC'

    db = get_db()
    sessions = _sessions_with_exercises(db, db.execute(query, params).fetchall())

    by_day: dict[str, list[dict]] = {}
    for s in sessions:
        by_day.setdefault(s['date'], []).append(s)
    return jsonify([
        {'date': day, **summarize_day(day_sessions)}
        for day, day_sessions in sorted(by_day.items())
    ])


# --- Weekly trends -----------------------------------------------------------

# Half a year of weeks: long enough for a trend, short enough that every point
# still has room on a phone-width chart.
DEFAULT_TREND_WEEKS = 26
MAX_TREND_WEEKS = 104


@bp.get('/trends')
def trends():
    """Weekly counts of journal entries written and job applications sent.

    Two different measures of keeping at it, on one axis: both are "things I did
    this week", so a shared scale is the comparison the chart exists for.

    Applications come from the email classifier — `category='job_application'`
    with `job_status='sent'`, i.e. an application that went out, not a rejection
    or an interview reply coming back. Days are local, not UTC: an entry written
    at 11pm belongs to that evening's week.
    """
    weeks = request.args.get('weeks', DEFAULT_TREND_WEEKS, type=int)
    if weeks is None:
        weeks = DEFAULT_TREND_WEEKS
    weeks = max(1, min(weeks, MAX_TREND_WEEKS))

    today = date_cls.fromisoformat(day_key_for())
    window_start = week_start(today) - timedelta(weeks=weeks - 1)
    # 4am local boundary of the first day in the window; the columns are unix ints.
    since = day_bounds(window_start.isoformat())[0]

    db = get_db()
    day_counts: dict[str, dict[str, int]] = {}
    _rollover_seconds = DAY_ROLLOVER_HOUR * 3600

    def _collect(name: str, sql: str) -> None:
        for row in db.execute(sql, (since,)).fetchall():
            day_counts.setdefault(row['day'], {})[name] = row['count']

    # Bucketing shifts the epoch back by the day-rollover hour first, so a row
    # just after local midnight still lands in the previous 4am-day.
    _collect('journalEntries', f"""
        SELECT date(created_at - {_rollover_seconds}, 'unixepoch', 'localtime') AS day, COUNT(*) AS count
        FROM journal_entries WHERE created_at >= ? GROUP BY day
    """)
    _collect('applications', f"""
        SELECT date(received_at - {_rollover_seconds}, 'unixepoch', 'localtime') AS day, COUNT(*) AS count
        FROM emails
        WHERE category='job_application' AND job_status='sent' AND received_at >= ?
        GROUP BY day
    """)

    # weekly_series only emits the series it saw, so name them here — a stretch
    # with no applications at all must still draw a zero line, not vanish.
    for key in ('applications', 'journalEntries'):
        day_counts.setdefault(today.isoformat(), {}).setdefault(key, 0)

    return jsonify({'weeks': weekly_series(day_counts, today, weeks)})


# --- Exercises & progression -------------------------------------------------

@bp.get('/exercises')
def list_exercises():
    """The canonical exercise list, most-trained first — feeds the progression
    picker and doubles as the vocabulary `canonicalize` folds new names onto."""
    db = get_db()
    rows = db.execute(
        'SELECT we.name_canonical AS name, COUNT(DISTINCT we.session_id) AS session_count,'
        ' MAX(s.date) AS last_date'
        ' FROM workout_exercises we JOIN workout_sessions s ON s.id = we.session_id'
        ' GROUP BY we.name_canonical ORDER BY session_count DESC, we.name_canonical ASC'
    ).fetchall()
    return jsonify([
        {
            'name': r['name'],
            'displayName': display_name(r['name']),
            'sessionCount': r['session_count'],
            'lastDate': r['last_date'],
        }
        for r in rows
    ])


@bp.get('/exercises/<path:name>/progression')
def exercise_progression(name):
    """Per-day progression for one canonical exercise.

    Returns both metrics the doc leaves open (top-set weight vs volume =
    weight x reps) so the chart can toggle without another round trip.
    """
    db = get_db()
    rows = db.execute(
        'SELECT s.date AS date, ws.weight AS weight, ws.reps AS reps'
        ' FROM workout_sets ws'
        ' JOIN workout_exercises we ON we.id = ws.exercise_id'
        ' JOIN workout_sessions s ON s.id = we.session_id'
        ' WHERE we.name_canonical = ? ORDER BY s.date ASC',
        (name,),
    ).fetchall()

    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r['date'], []).append(r)

    points = []
    for day, day_rows in sorted(by_day.items()):
        weights = [r['weight'] for r in day_rows if r['weight'] is not None]
        volume = sum(
            r['weight'] * r['reps']
            for r in day_rows
            if r['weight'] is not None and r['reps'] is not None
        )
        points.append({
            'date': day,
            'maxWeight': max(weights) if weights else None,
            'totalVolume': volume or None,
            'totalReps': sum(r['reps'] for r in day_rows if r['reps'] is not None) or None,
            'setCount': len(day_rows),
        })
    return jsonify({'name': name, 'displayName': display_name(name), 'points': points})


@bp.post('/exercises/merge')
def merge_exercises():
    """Fold one canonical exercise into another — the manual escape hatch for a
    name the automatic canonicalization split or joined wrongly."""
    body = request.get_json(silent=True) or {}
    source = (body.get('from') or '').strip()
    target = (body.get('into') or '').strip()
    if not source or not target:
        return jsonify({'error': 'from and into are required'}), 400
    if source == target:
        return jsonify({'error': 'from and into must differ'}), 400

    db = get_db()
    cur = db.execute(
        'UPDATE workout_exercises SET name_canonical=? WHERE name_canonical=?',
        (target, source),
    )
    db.commit()
    if not cur.rowcount:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True, 'moved': cur.rowcount})


# --- Body weight -------------------------------------------------------------

@bp.get('/weight')
def list_weight():
    start, err = _parse_date(request.args.get('start'))
    if err:
        return jsonify({'error': err}), 400
    end, err = _parse_date(request.args.get('end'))
    if err:
        return jsonify({'error': err}), 400

    query = 'SELECT id, date, weight, created_at, updated_at FROM body_weight_logs'
    clauses, params = [], []
    if start:
        clauses.append('date >= ?')
        params.append(start)
    if end:
        clauses.append('date <= ?')
        params.append(end)
    if clauses:
        query += ' WHERE ' + ' AND '.join(clauses)
    query += ' ORDER BY date ASC'
    db = get_db()
    return jsonify([row_to_dict(r) for r in db.execute(query, params).fetchall()])


@bp.post('/weight')
def log_weight():
    """Upsert the weigh-in for a day. Logging twice in one day corrects it
    rather than adding a second point to the line."""
    body = request.get_json(silent=True) or {}
    weight, err = _parse_float(body.get('weight'), 'weight', 1, 1000)
    if err:
        return jsonify({'error': err}), 400
    if weight is None:
        return jsonify({'error': 'weight is required'}), 400
    day, err = _parse_date(body.get('date'), _today())
    if err:
        return jsonify({'error': err}), 400

    now = int(time.time())
    db = get_db()
    db.execute(
        'INSERT INTO body_weight_logs(id, date, weight, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)'
        ' ON CONFLICT(date) DO UPDATE SET weight=excluded.weight, updated_at=excluded.updated_at',
        (str(ULID()), day, weight, now, now),
    )
    db.commit()
    row = db.execute(
        'SELECT id, date, weight, created_at, updated_at FROM body_weight_logs WHERE date=?',
        (day,),
    ).fetchone()
    return jsonify(row_to_dict(row)), 201


@bp.delete('/weight/<log_id>')
def delete_weight(log_id):
    db = get_db()
    cur = db.execute('DELETE FROM body_weight_logs WHERE id=?', (log_id,))
    db.commit()
    if not cur.rowcount:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})


# --- Daily selfie ------------------------------------------------------------

def _selfie_dict(row) -> dict:
    d = row_to_dict(row)
    d.pop('path', None)  # a filesystem path is no business of the client
    d['url'] = f'/api/lifestyle/selfies/{row["id"]}/image'
    return d


@bp.get('/selfies')
def list_selfies():
    limit = min(max(int(request.args.get('limit', 30)), 1), 400)
    db = get_db()
    rows = db.execute(
        'SELECT id, date, path, mime, created_at FROM lifestyle_selfies'
        ' ORDER BY date DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return jsonify([_selfie_dict(r) for r in rows])


@bp.post('/selfies')
def upload_selfie():
    """Store the day's selfie. One per day: re-uploading replaces it, so a bad
    shot can be retaken without leaving two entries for the same square."""
    file = request.files.get('image')
    if file is None or not file.filename:
        return jsonify({'error': 'image is required'}), 400
    day, err = _parse_date(request.form.get('date'), _today())
    if err:
        return jsonify({'error': err}), 400

    ext = storage.resolve_ext(file.mimetype, file.filename)
    if ext is None:
        return jsonify({'error': 'unsupported image type'}), 400

    data = file.read()
    if not data:
        return jsonify({'error': 'image is empty'}), 400
    if len(data) > MAX_SELFIE_BYTES:
        return jsonify({'error': 'image is too large'}), 413

    selfie_id = str(ULID())
    path = storage.selfie_path(selfie_id, ext)
    if path is None:
        return jsonify({'error': 'unsupported image type'}), 400
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    db = get_db()
    previous = db.execute(
        'SELECT id FROM lifestyle_selfies WHERE date=?', (day,)
    ).fetchone()
    if previous:
        db.execute('DELETE FROM lifestyle_selfies WHERE id=?', (previous['id'],))
    db.execute(
        'INSERT INTO lifestyle_selfies(id, date, path, mime, created_at) VALUES (?,?,?,?,?)',
        (selfie_id, day, str(path), file.mimetype or None, int(time.time())),
    )
    db.commit()
    # Only after the row is committed — losing the new photo to a failed insert
    # would be worse than leaving one orphaned directory behind.
    if previous:
        storage.delete_selfie_dir(previous['id'])

    row = db.execute(
        'SELECT id, date, path, mime, created_at FROM lifestyle_selfies WHERE id=?',
        (selfie_id,),
    ).fetchone()
    return jsonify(_selfie_dict(row)), 201


@bp.get('/selfies/<selfie_id>/image')
def get_selfie_image(selfie_id):
    db = get_db()
    row = db.execute(
        'SELECT path, mime FROM lifestyle_selfies WHERE id=?', (selfie_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    path = storage.resolve_stored_path(row['path'])
    if path is None or not path.is_file():
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype=row['mime'] or None)


@bp.delete('/selfies/<selfie_id>')
def delete_selfie(selfie_id):
    db = get_db()
    cur = db.execute('DELETE FROM lifestyle_selfies WHERE id=?', (selfie_id,))
    db.commit()
    if not cur.rowcount:
        return jsonify({'error': 'Not found'}), 404
    storage.delete_selfie_dir(selfie_id)
    return jsonify({'success': True})


# --- Calories ----------------------------------------------------------------

@bp.get('/calories')
def list_calories():
    """A day's entries plus its running total (docs §6: enough to eyeball)."""
    day, err = _parse_date(request.args.get('date'), _today())
    if err:
        return jsonify({'error': err}), 400
    db = get_db()
    rows = db.execute(
        'SELECT id, date, description, calories, created_at FROM calorie_logs'
        ' WHERE date=? ORDER BY created_at ASC',
        (day,),
    ).fetchall()
    entries = [row_to_dict(r) for r in rows]
    return jsonify({
        'date': day,
        'entries': entries,
        'total': sum(e['calories'] for e in entries),
    })


@bp.post('/calories')
def log_calories():
    body = request.get_json(silent=True) or {}
    description = (body.get('description') or '').strip()
    if not description:
        return jsonify({'error': 'description is required'}), 400
    calories, err = _parse_int(body.get('calories'), 'calories', 0, 20000)
    if err:
        return jsonify({'error': err}), 400
    if calories is None:
        return jsonify({'error': 'calories is required'}), 400
    day, err = _parse_date(body.get('date'), _today())
    if err:
        return jsonify({'error': err}), 400

    entry_id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO calorie_logs(id, date, description, calories, created_at)'
        ' VALUES (?,?,?,?,?)',
        (entry_id, day, description, calories, int(time.time())),
    )
    db.commit()
    row = db.execute(
        'SELECT id, date, description, calories, created_at FROM calorie_logs WHERE id=?',
        (entry_id,),
    ).fetchone()
    return jsonify(row_to_dict(row)), 201


@bp.delete('/calories/<entry_id>')
def delete_calories(entry_id):
    db = get_db()
    cur = db.execute('DELETE FROM calorie_logs WHERE id=?', (entry_id,))
    db.commit()
    if not cur.rowcount:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})
