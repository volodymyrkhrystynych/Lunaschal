"""User-named places and local-only matching of recorded coordinates."""
import math
import json

from backend.geo import coord_pair


def list_places(db) -> list[dict]:
    return [dict(r) for r in db.execute('SELECT * FROM saved_places ORDER BY name')]


def format_places_context() -> str:
    from backend.db.connection import get_db
    from backend.day_boundary import day_key_for
    from backend.place_roles import active_on, list_periods
    db = get_db()
    places = list_places(db)
    if not places:
        return ''
    today = day_key_for()
    periods = list_periods(db)
    out = []
    for p in places:
        item = {k: p[k] for k in ('name', 'notes', 'latitude', 'longitude', 'radius_m')}
        mine = [r for r in periods if r['place_id'] == p['id']]
        current = next((r['role'] for r in mine if active_on(r, today)), None)
        if current:
            item['role'] = f'current {current}'
        elif mine:
            item['role'] = 'former ' + '/'.join(sorted({r['role'] for r in mine}))
        out.append(item)
    return 'User-saved places (names and context, not evidence of current whereabouts):\n' + json.dumps(
        out, ensure_ascii=False)


def nearby_places(latitude, longitude, places: list[dict]) -> list[str]:
    pair = coord_pair(latitude, longitude)
    if not pair:
        return []
    lat, lon = map(math.radians, pair)
    matches = []
    for place in places:
        target = coord_pair(place['latitude'], place['longitude'])
        if not target:
            continue
        plat, plon = map(math.radians, target)
        a = math.sin((plat - lat) / 2) ** 2 + math.cos(lat) * math.cos(plat) * math.sin((plon - lon) / 2) ** 2
        distance = 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, a))))
        if distance <= place['radius_m']:
            matches.append((distance, place['name']))
    return [name for _, name in sorted(matches)]
