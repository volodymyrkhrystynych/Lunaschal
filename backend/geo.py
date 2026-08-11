"""Validating coordinates that arrive from a client.

One place, shared by the food log and chat photo uploads, for the same reason
`backend/tags.py` exists: two features accepting latitude/longitude with two
slightly different notions of what counts as valid is how one of them ends up
storing a NaN, and a NaN in a coordinate column is a row that no map query will
ever match and no comparison will ever reject.
"""
import math


def parse_coord(raw) -> float | None:
    """A finite coordinate in range, or None.

    `-180..180` rather than the tighter `-90..90` for latitude: the two columns
    are validated by the same rule everywhere in this app, and a caller that
    swapped them has a bug this function is not positioned to catch.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and -180 <= value <= 180 else None


def coord_pair(raw_latitude, raw_longitude) -> tuple[float, float] | None:
    """Both coordinates, or None if either is missing or unusable.

    A lone latitude is not half a location — storing one without the other
    produces a row that looks located and isn't.
    """
    latitude = parse_coord(raw_latitude)
    longitude = parse_coord(raw_longitude)
    if latitude is None or longitude is None:
        return None
    return latitude, longitude
