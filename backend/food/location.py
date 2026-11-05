"""Where a meal was eaten, filled in rather than asked for.

A meal photographed on the phone usually arrives with coordinates (the
photo's own GPS, or the phone's fix at capture). When it doesn't — the in-app
camera hands over pixels without EXIF, and a fix may not have been fresh —
the location trail says where the phone was at that moment
(backend/location/lookup.py). Once a meal has coordinates, an empty `place`
becomes the saved place it falls inside: "Home", "Office".

**Only empty fields are filled.** A place typed by the user, or one the
structuring pass already read out of what was said, is never replaced, and a
meal's own coordinates are never moved.
"""
from backend.location.lookup import MAX_GAP_S, fix_near
from backend.places import list_places, nearby_places


def fill(db, entry_id: str, *, photo_coords: tuple[float, float] | None = None) -> dict:
    """Fill what is missing on one meal. Returns the columns it set."""
    row = db.execute('SELECT latitude, longitude, place, created_at FROM food_entries WHERE id=?',
                     (entry_id,)).fetchone()
    if row is None:
        return {}
    updates: dict = {}
    lat, lon = row['latitude'], row['longitude']
    if lat is None or lon is None:
        found = photo_coords or fix_near(db, row['created_at'])
        if found is not None:
            lat, lon = found
            updates.update(latitude=lat, longitude=lon)
    if not (row['place'] or '').strip() and lat is not None and lon is not None:
        names = nearby_places(lat, lon, list_places(db))
        if names:
            updates['place'] = names[0]
    if updates:
        sets = ', '.join(f'{k}=?' for k in updates)
        db.execute(f'UPDATE food_entries SET {sets} WHERE id=?', (*updates.values(), entry_id))
    return updates


def fill_between(db, start: float, end: float) -> int:
    """Meals from [start, end] (± the lookup gap) still missing a location —
    for a trail upload that arrived after the meal did."""
    rows = db.execute(
        'SELECT id FROM food_entries WHERE (latitude IS NULL OR longitude IS NULL)'
        ' AND created_at BETWEEN ? AND ?', (start - MAX_GAP_S, end + MAX_GAP_S)).fetchall()
    filled = sum(1 for r in rows if fill(db, r['id']))
    if filled:
        db.commit()
    return filled
