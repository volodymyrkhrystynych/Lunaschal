"""User-named places and local-only matching of recorded coordinates."""
import math
import json

from backend.geo import coord_pair


def list_places(db) -> list[dict]:
    return [dict(r) for r in db.execute('SELECT * FROM saved_places ORDER BY name')]


def format_places_context() -> str:
    from backend.db.connection import get_db
    places = list_places(get_db())
    if not places:
        return ''
    return 'User-saved places (names and context, not evidence of current whereabouts):\n' + json.dumps(
        [{k: p[k] for k in ('name', 'notes', 'latitude', 'longitude', 'radius_m')} for p in places], ensure_ascii=False)


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
