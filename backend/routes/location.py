"""The location trail (backend/location/): the phone's upload route, the
Journal's list of mapped days, and each day's picture."""
import logging
import re
from datetime import date, timedelta

from flask import Blueprint, Response, jsonify, request

from backend.day_boundary import day_key_for
from backend.db.connection import get_db
from backend.food import location as meal_location
from backend.journal import location as entry_location
from backend.location import days, ingest, patterns, work_calendar

log = logging.getLogger(__name__)

bp = Blueprint('location', __name__, url_prefix='/api/location')

_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


@bp.post('/points')
def upload_points():
    try:
        result = ingest.ingest(request.get_json(silent=True))
    except ingest.BatchError as e:
        return jsonify({'error': str(e)}), 400
    stay_days, span = result.pop('stayDays'), result.pop('span')
    db = get_db()
    # The points are stored; the phone must get its ack and drop them, so
    # nothing derived below may fail the request. Each is redone by the next
    # upload that touches the same days.
    try:
        work_calendar.sync_days(db, stay_days)
    except Exception:
        log.exception('work calendar sync failed for %s', stay_days)
    if span:
        try:
            meal_location.fill_between(db, *span)
            entry_location.fill_between(db, *span)
        except Exception:
            log.exception('location fill failed for %s', span)
    return jsonify(result)


@bp.get('/journal')
def journal():
    return jsonify(days.journal_days())


@bp.get('/patterns')
def location_patterns():
    """Weekly office days, hours at work and commute times over the last
    `days` days (default 8 weeks)."""
    try:
        span = min(max(int(request.args.get('days', 56)), 7), 366)
    except ValueError:
        return jsonify({'error': 'days must be a number'}), 400
    start = (date.fromisoformat(day_key_for()) - timedelta(days=span - 1)).isoformat()
    result = patterns.build(days.summaries_since(start))
    return jsonify({**result, 'days': span, 'since': start})


@bp.get('/days/<day>/map.png')
def day_map(day: str):
    if not _DAY.match(day):
        return jsonify({'error': 'day must be YYYY-MM-DD'}), 400
    drawn = days.map_png(day)
    if drawn is None:
        return jsonify({'error': 'no trail recorded that day'}), 404
    png, ver = drawn
    resp = Response(png, mimetype='image/png')
    # A URL naming the current version names these exact bytes forever; any
    # other URL (no ?v=, or an old one) must be asked again next time.
    resp.headers['Cache-Control'] = ('private, max-age=31536000, immutable'
                                     if request.args.get('v') == ver else 'no-cache')
    return resp
