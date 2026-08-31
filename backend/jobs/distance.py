"""How far a posting is from the anchor — computed, never judged.

`keywords.py` scores a posting against the profile at sync time with no model
call, which is what keeps a 200-job sync free and the feed sorted the moment it
lands. This answers the other question the feed has to answer — "could I
actually get there?" — and is deliberately built the same way: a static
gazetteer, a haversine, no network, no geocoder, no model.

Three rules, and the first two are the reason this module is a gazetteer rather
than a geocoding client:

**An unrecognised place resolves to None, never to a large number.** A row that
looks located and isn't is worse than one that is honestly unlocated, and a
fabricated 4,000 km would sort a real job off the end of the feed. That is
`backend/geo.py`'s rule about half a location, applied one layer up.

**Provinces and countries are absent on purpose.** "Remote - Canada" has no
point on a map worth measuring, and anchoring it at the geographic centre of
the country would be a guess wearing a number's clothes. Only places with a
meaningful centre are in the table.

**Remote is not zero kilometres.** It is a different state, carried by
`jobs.remote` and grouped separately by the feed. Folding it in here would rank
every remote posting ahead of a job three subway stops away.

Matching is longest-match-wins over token spans, the same shape as
`keywords.py`: the location string is reduced to tokens, every gazetteer key
that appears as a whole-token run is collected, spans contained in a longer
span are dropped, and of what survives the **nearest** wins — so
`"Toronto, ON; New York, NY"` is a Toronto job rather than whichever name the
string happened to put first.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Union Station: the transit hub the commute would actually be measured from,
# not the geographic middle of the core.
ANCHOR = (43.6453, -79.3806)
ANCHOR_LABEL = 'Union Station'

EARTH_RADIUS_KM = 6371.0088

# Toronto's own districts, kept apart from municipalities only so the card can
# say how precise the number is. Both are real points; neither is a guess.
_DISTRICTS = {
    'downtown toronto': (43.6532, -79.3832),
    'toronto': (43.6532, -79.3832),
    'north york': (43.7615, -79.4111),
    'scarborough': (43.7764, -79.2318),
    'etobicoke': (43.6205, -79.5132),
    'east york': (43.6907, -79.3277),
    'york': (43.6896, -79.4772),
    'midtown toronto': (43.7043, -79.3974),
    'liberty village': (43.6371, -79.4200),
    'yorkville': (43.6708, -79.3931),
    'willowdale': (43.7712, -79.4139),
    'don mills': (43.7334, -79.3462),
}

_CITIES = {
    # 905 and the rest of the GTA.
    'mississauga': (43.5890, -79.6441),
    'brampton': (43.6832, -79.7629),
    'markham': (43.8561, -79.3370),
    'richmond hill': (43.8828, -79.4403),
    'vaughan': (43.8361, -79.4983),
    'concord': (43.8000, -79.4833),
    'thornhill': (43.8156, -79.4247),
    'woodbridge': (43.7758, -79.5967),
    'maple': (43.8563, -79.5085),
    'oakville': (43.4675, -79.6877),
    'burlington': (43.3255, -79.7990),
    'milton': (43.5183, -79.8774),
    'georgetown': (43.6500, -79.9200),
    'halton hills': (43.6300, -79.9500),
    'pickering': (43.8384, -79.0868),
    'ajax': (43.8509, -79.0204),
    'whitby': (43.8975, -78.9429),
    'oshawa': (43.8971, -78.8658),
    'newmarket': (44.0592, -79.4613),
    'aurora': (44.0065, -79.4504),
    'king city': (43.9250, -79.5286),
    'stouffville': (43.9709, -79.2456),
    'caledon': (43.8686, -79.8658),
    'hamilton': (43.2557, -79.8711),
    'barrie': (44.3894, -79.6903),
    # The rest of southern Ontario.
    'kitchener': (43.4516, -80.4925),
    'waterloo': (43.4643, -80.5204),
    'guelph': (43.5448, -80.2482),
    'ottawa': (45.4215, -75.6972),
    'kingston': (44.2312, -76.4860),
    'windsor': (42.3149, -83.0364),
    'st catharines': (43.1594, -79.2469),
    'niagara falls': (43.0896, -79.0849),
    'peterborough': (44.3091, -78.3197),
    # Elsewhere in Canada.
    'montreal': (45.5019, -73.5674),
    'quebec city': (46.8139, -71.2080),
    'ottawa gatineau': (45.4215, -75.6972),
    'vancouver': (49.2827, -123.1207),
    'victoria': (48.4284, -123.3656),
    'calgary': (51.0447, -114.0719),
    'edmonton': (53.5461, -113.4938),
    'winnipeg': (49.8951, -97.1384),
    'saskatoon': (52.1332, -106.6700),
    'halifax': (44.6488, -63.5752),
    "st john s": (47.5615, -52.7126),
    # US hubs that show up in this inventory often enough to be worth ranking.
    'new york': (40.7128, -74.0060),
    'brooklyn': (40.6782, -73.9442),
    'san francisco': (37.7749, -122.4194),
    'palo alto': (37.4419, -122.1430),
    'mountain view': (37.3861, -122.0839),
    'seattle': (47.6062, -122.3321),
    'boston': (42.3601, -71.0589),
    'austin': (30.2672, -97.7431),
    'chicago': (41.8781, -87.6298),
    'denver': (39.7392, -104.9903),
    'atlanta': (33.7490, -84.3880),
    'los angeles': (34.0522, -118.2437),
    'san diego': (32.7157, -117.1611),
    'dublin': (53.3498, -6.2603),
    'berlin': (52.5200, 13.4050),
    'amsterdam': (52.3676, 4.9041),
    # Qualified forms of the ambiguous names below. Longest-match-wins picks
    # these over the bare token whenever the string bothered to say which one.
    'london ontario': (42.9849, -81.2453),
    'london on': (42.9849, -81.2453),
    'london uk': (51.5074, -0.1278),
    'london england': (51.5074, -0.1278),
    'london united kingdom': (51.5074, -0.1278),
    'cambridge ontario': (43.3616, -80.3144),
    'cambridge on': (43.3616, -80.3144),
    'cambridge ma': (42.3736, -71.1097),
    'cambridge massachusetts': (42.3736, -71.1097),
    'cambridge uk': (52.2053, 0.1218),
    'cambridge united kingdom': (52.2053, 0.1218),
}

# Names that mean two different cities on two different continents, where
# guessing wrong is a 6,000 km error. Bare, they resolve to nothing; qualified,
# they are ordinary entries in `_CITIES` above. This is `urlmatch.py`'s
# instinct — ambiguity resolves to None, never to a best guess.
_AMBIGUOUS = frozenset({'london', 'cambridge'})

GAZETTEER: dict[str, tuple[float, float]] = {**_CITIES, **_DISTRICTS}

_TOKEN_SPLIT = re.compile(r'[^a-z0-9]+')

# Longest key first, so the span scan can stop widening once nothing can match.
_MAX_KEY_TOKENS = max(len(key.split()) for key in GAZETTEER)


@dataclass(frozen=True)
class Reading:
    """A distance somebody could act on: how far, from where, how precisely."""

    km: float
    # 'exact'    — the source posted coordinates
    # 'district' — a Toronto sub-area centre
    # 'city'     — a municipal centre
    # 'inferred' — a model read it out of the body; the weakest of the four,
    #              and the only one that did not come from a structured field
    precision: str
    place: str

    def to_dict(self) -> dict:
        return {'km': round(self.km, 1), 'precision': self.precision,
                'place': self.place}


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle kilometres between two (latitude, longitude) pairs."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def tokenize(location: str) -> list[str]:
    return [token for token in _TOKEN_SPLIT.split((location or '').lower()) if token]


def _matches(tokens: list[str]) -> list[tuple[int, int, str]]:
    """Every gazetteer key present as a whole-token run: (start, end, key)."""
    found = []
    for start in range(len(tokens)):
        for width in range(1, min(_MAX_KEY_TOKENS, len(tokens) - start) + 1):
            key = ' '.join(tokens[start:start + width])
            if key in GAZETTEER:
                found.append((start, start + width, key))
    return found


def _longest_spans(found: list[tuple[int, int, str]]) -> list[str]:
    """Drop any match wholly inside a longer one — 'york' inside 'north york'."""
    kept = []
    for start, end, key in found:
        contained = any(
            other_start <= start and end <= other_end
            and (other_end - other_start) > (end - start)
            for other_start, other_end, _ in found
        )
        if not contained:
            kept.append(key)
    return kept


def from_coords(latitude, longitude) -> Reading | None:
    """A reading from coordinates a source supplied, e.g. Adzuna's."""
    from backend.geo import coord_pair

    pair = coord_pair(latitude, longitude)
    if pair is None:
        return None
    return Reading(km=haversine(ANCHOR, pair), precision='exact', place='')


def resolve(location: str) -> Reading | None:
    """Distance from the anchor for a free-text location, or None.

    None is returned for an empty string, for a place the gazetteer does not
    know, and for a bare ambiguous name — three different reasons that all
    mean the same thing to a caller: do not put a number on this row.
    """
    tokens = tokenize(location)
    if not tokens:
        return None

    best: Reading | None = None
    for key in _longest_spans(_matches(tokens)):
        if key in _AMBIGUOUS:
            continue
        point = GAZETTEER[key]
        reading = Reading(
            km=haversine(ANCHOR, point),
            precision='district' if key in _DISTRICTS else 'city',
            place=key,
        )
        # Nearest wins: a posting listing several offices is reachable at the
        # closest one, and that is the number the feed is sorting on.
        if best is None or reading.km < best.km:
            best = reading
    return best


def selectable_places() -> list[str]:
    """The keys a model may point at, ambiguous bare names excluded.

    Handed to `ai/job_triage.build_schema` as an enum bound. Sorted so the
    prompt and the grammar are stable between runs — an enum whose order
    changed every call would invalidate the prefix cache for no reason.
    """
    return sorted(key for key in GAZETTEER if key not in _AMBIGUOUS)


def resolve_keys(keys) -> Reading | None:
    """A reading from gazetteer keys a model selected, or None.

    Precision is 'inferred' and never anything better: this came from reading
    prose, not from a structured field the employer filled in, and the card
    says so. Unknown keys are dropped rather than corrected — `normalize_result`
    already applied the bound, and anything that got past it is a bug, not a
    near-miss to be repaired here.
    """
    best: Reading | None = None
    for key in keys or []:
        point = GAZETTEER.get(key) if isinstance(key, str) else None
        if point is None or key in _AMBIGUOUS:
            continue
        reading = Reading(km=haversine(ANCHOR, point), precision='inferred',
                          place=key)
        if best is None or reading.km < best.km:
            best = reading
    return best


def reading_for(job: dict) -> Reading | None:
    """The best reading available for one normalized posting.

    Source-supplied coordinates beat the gazetteer — Adzuna is the only adapter
    that carries them, and a real point is better than a city centroid.
    """
    exact = from_coords(job.get('latitude'), job.get('longitude'))
    if exact is not None:
        return exact
    return resolve(job.get('location') or '')
