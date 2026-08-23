from flask import Blueprint, jsonify, request

from backend.day_boundary import day_key_for
from backend.db.connection import get_db
from backend.geo import coord_pair
from backend.weather import sync

bp = Blueprint('weather', __name__, url_prefix='/api/lifestyle/weather')


def _location_dict(db) -> dict | None:
    resolved = sync.resolve_location(db)
    if resolved is None:
        return None
    lat, lon, source = resolved
    return {'latitude': lat, 'longitude': lon, 'source': source}


def _sun_dict(sun: dict | None) -> dict:
    if sun is None:
        return {'sunriseTs': None, 'sunsetTs': None}
    return {'sunriseTs': sun['sunriseTs'], 'sunsetTs': sun['sunsetTs']}


@bp.get('/today')
def today():
    db = get_db()
    hours, sun = sync.ensure_today(db)
    return jsonify({'hours': hours, 'location': _location_dict(db), **_sun_dict(sun)})


@bp.post('/location')
def update_location():
    """The frontend posts a fresh geolocation fix on each Lifestyle tab visit
    (see src/components/Lifestyle/WeatherCard.tsx) — this both logs it and
    resyncs today's weather against it, so remaining hours forecast for the
    new location and elapsed hours record what actually happened there."""
    body = request.get_json(silent=True) or {}
    coords = coord_pair(body.get('latitude'), body.get('longitude'))
    if coords is None:
        return jsonify({'error': 'latitude and longitude are required'}), 400
    lat, lon = coords

    db = get_db()
    day_key = day_key_for()
    sync.record_location(db, day_key, lat, lon, 'geolocation')
    hours = sync.sync_day(db, day_key, lat, lon, 'geolocation')
    sun = sync.sync_sun_times(db, day_key, lat, lon, 'geolocation')
    return jsonify({'hours': hours, 'location': _location_dict(db), **_sun_dict(sun)})
