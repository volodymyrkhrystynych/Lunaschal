"""Apple Health mirror (backend/apple_health/): the phone's upload route, and reads.

`POST /sync` is the only writer. Everything else is read-only and returns raw
numbers for analysis; `/activity` is the shaped view the Lifestyle card draws.
Day parameters are 4am day keys, like everywhere else in the app.
"""
import csv
import io
import re
from datetime import date, timedelta

from flask import Blueprint, Response, jsonify, request

from backend.day_boundary import day_key_for
from backend.apple_health import ingest, queries

bp = Blueprint('apple_health', __name__, url_prefix='/api/apple-health')

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TYPE_RE = re.compile(r'^HK[A-Za-z0-9]{1,120}$')
DEFAULT_LIMIT = 5000
MAX_LIMIT = 100000


@bp.post('/sync')
def sync():
    try:
        return jsonify(ingest.ingest(request.get_json(silent=True)))
    except ingest.BatchError as e:
        return jsonify({'error': str(e)}), 400


def _range():
    """(first_day, last_day, error) from ?from=&to=, defaulting to the last 30 days."""
    today = day_key_for()
    last = request.args.get('to', today)
    first = request.args.get('from')
    if not _DATE_RE.match(last) or (first is not None and not _DATE_RE.match(first)):
        return None, None, 'from/to must be YYYY-MM-DD'
    if first is None:
        first = (date.fromisoformat(last) - timedelta(days=29)).isoformat()
    if first > last:
        return None, None, 'from is after to'
    return first, last, None


def _limit():
    try:
        return max(1, min(int(request.args.get('limit', DEFAULT_LIMIT)), MAX_LIMIT))
    except ValueError:
        return DEFAULT_LIMIT


@bp.get('/types')
def types():
    return jsonify(queries.catalog())


@bp.get('/samples')
def samples():
    type_ = request.args.get('type', '')
    if not _TYPE_RE.match(type_):
        return jsonify({'error': 'type must be a HealthKit identifier'}), 400
    first, last, err = _range()
    if err:
        return jsonify({'error': err}), 400
    start, end = queries.window(first, last)
    rows = queries.samples(type_, start, end, _limit())
    return jsonify([queries.sample_dict(r) for r in rows])


@bp.get('/workouts')
def workouts():
    first, last, err = _range()
    if err:
        return jsonify({'error': err}), 400
    start, end = queries.window(first, last)
    return jsonify([queries.workout_dict(r) for r in queries.workouts(start, end, _limit())])


@bp.get('/daily')
def daily():
    types_ = [t for t in request.args.get('types', '').split(',') if t]
    if not types_ or not all(_TYPE_RE.match(t) for t in types_):
        return jsonify({'error': 'types must be a comma-separated list of HealthKit identifiers'}), 400
    first, last, err = _range()
    if err:
        return jsonify({'error': err}), 400
    return jsonify(queries.daily(types_, first, last))


@bp.get('/activity')
def activity():
    try:
        days = max(1, min(int(request.args.get('days', 28)), 366))
    except ValueError:
        return jsonify({'error': 'days must be a number'}), 400
    return jsonify(queries.activity(day_key_for(), days))


@bp.get('/export.csv')
def export_csv():
    """One type's samples (or `type=HKWorkoutType`) as CSV over a day range.
    Built in memory: a range of heart rate is tens of thousands of rows, which is
    well within that, and a streamed response would hold the DB cursor open
    across the whole download."""
    type_ = request.args.get('type', '')
    if not _TYPE_RE.match(type_):
        return jsonify({'error': 'type must be a HealthKit identifier'}), 400
    first, last, err = _range()
    if err:
        return jsonify({'error': err}), 400
    start, end = queries.window(first, last)
    out = io.StringIO()
    writer = csv.writer(out)
    if type_ == 'HKWorkoutType':
        writer.writerow(['id', 'activity_type', 'activity_name', 'start', 'end', 'duration_s',
                         'energy_kcal', 'distance_m', 'source', 'metadata'])
        for r in queries.workouts(start, end, MAX_LIMIT):
            writer.writerow([r['id'], r['activity_type'], r['activity_name'], r['start_ts'], r['end_ts'],
                             r['duration_s'], r['energy_kcal'], r['distance_m'], r['source_name'],
                             r['metadata'] or ''])
    else:
        writer.writerow(['id', 'start', 'end', 'value', 'unit', 'source', 'device', 'metadata'])
        for r in queries.samples(type_, start, end, MAX_LIMIT):
            writer.writerow([r['id'], r['start_ts'], r['end_ts'], r['value'], r['unit'] or '',
                             r['source_name'] or '', r['device'] or '', r['metadata'] or ''])
    name = f'{type_}_{first}_{last}.csv'
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})
