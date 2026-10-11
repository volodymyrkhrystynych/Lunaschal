"""The location trail (backend/location/): the phone's upload route, the
Journal's list of mapped days, and each day's picture."""
import re

from flask import Blueprint, Response, jsonify, request

from backend.location import days, ingest

bp = Blueprint('location', __name__, url_prefix='/api/location')

_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


@bp.post('/points')
def upload_points():
    try:
        return jsonify(ingest.ingest(request.get_json(silent=True)))
    except ingest.BatchError as e:
        return jsonify({'error': str(e)}), 400


@bp.get('/journal')
def journal():
    return jsonify(days.journal_days())


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
