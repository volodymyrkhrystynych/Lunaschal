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

There is one thing this module answers besides "how far": **whether a posting is
out of range at all**, which is a strictly easier question. `verdict` can say
"further than 200 km" about `"Bengaluru, India"` without saying how much
further, because no point in India is within 200 km of the anchor. That is why
`_FAR_REGIONS` can exist alongside the rule that countries have no centroid: a
*bound* is not a guess, only a *distance* would be. A region name therefore
never produces a `Reading` and never reaches `distance_km`.
"""
from __future__ import annotations

import math
import re
import unicodedata
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
    # The tail: every one of these appeared as an unplaceable location on a
    # live posting, which is the only reason any of them is here. They are
    # ordinary points like the rest — the alternative was leaving ~310 rows
    # undecidable and visible on a feed that had been asked to exclude them.
    'menlo park': (37.4530, -122.1817),
    'sunnyvale': (37.3688, -122.0363),
    'san jose': (37.3382, -121.8863),
    'santa clara': (37.3541, -121.9552),
    'bellevue': (47.6101, -122.2015),
    'redmond': (47.6740, -122.1215),
    'dallas': (32.7767, -96.7970),
    'addison': (32.9618, -96.8292),
    'houston': (29.7604, -95.3698),
    'phoenix': (33.4484, -112.0740),
    'pittsburgh': (40.4406, -79.9959),
    'newark': (40.7357, -74.1724),
    'sandy': (40.5649, -111.8389),
    'lenexa': (38.9536, -94.7336),
    'deer park': (29.7050, -95.1238),
    'burnaby': (49.2488, -122.9805),
    'surrey': (49.1913, -122.8490),
    'mumbai': (19.0760, 72.8777),
    'bengaluru': (12.9716, 77.5946),
    'bangalore': (12.9716, 77.5946),
    'noida': (28.5355, 77.3910),
    'kochi': (9.9312, 76.2673),
    'taipei': (25.0330, 121.5654),
    'singapore': (1.3521, 103.8198),
    'hong kong': (22.3193, 114.1694),
    'dubai': (25.2048, 55.2708),
    'yerevan': (40.1792, 44.4991),
    'belgrade': (44.7866, 20.4489),
    'bucharest': (44.4268, 26.1025),
    'copenhagen': (55.6761, 12.5683),
    'lisbon': (38.7223, -9.1393),
    'barcelona': (41.3851, 2.1734),
    'madrid': (40.4168, -3.7038),
    'lyon': (45.7640, 4.8357),
    'bordeaux': (44.8378, -0.5792),
    'marseille': (43.2965, 5.3698),
    'brussels': (50.8503, 4.3517),
    'munich': (48.1351, 11.5820),
    'milan': (45.4642, 9.1900),
    'ferrara': (44.8381, 11.6198),
    'manchester': (53.4808, -2.2426),
    'sao paulo': (-23.5558, -46.6396),
    'buenos aires': (-34.6037, -58.3816),
    'bogota': (4.7110, -74.0721),
    'medellin': (6.2476, -75.5658),
    'mexico city': (19.4326, -99.1332),
    'sydney': (-33.8688, 151.2093),
    'melbourne': (-37.8136, 144.9631),
    'tel aviv': (32.0853, 34.7818),
    'tokyo': (35.6762, 139.6503),
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
    # Paris joins the ambiguous set for the same reason London did: Paris,
    # Ontario is ~95 km out and would be *in* range, so resolving a bare
    # "Paris" to France would exclude a job the user could take. Qualified, it
    # is ordinary. A bare one still reaches `out_of_range` whenever the string
    # says "France" anywhere, via `_FAR_REGIONS`.
    'paris france': (48.8566, 2.3522),
    'paris fr': (48.8566, 2.3522),
    'paris ontario': (43.1998, -80.3841),
    'paris on': (43.1998, -80.3841),
}

# Names that mean two different cities on two different continents, where
# guessing wrong is a 6,000 km error. Bare, they resolve to nothing; qualified,
# they are ordinary entries in `_CITIES` above. This is `urlmatch.py`'s
# instinct — ambiguity resolves to None, never to a best guess.
_AMBIGUOUS = frozenset({'london', 'cambridge', 'paris'})

# Regions with no point within `IN_RANGE_CEILING_KM` of the anchor. These are
# emphatically *not* gazetteer entries: they have no coordinate here and can
# never produce a `Reading`, because a country has no centre worth measuring.
# What they can do is settle the weaker question `verdict` asks — "is this
# further away than X?" — which needs a bound rather than a point. No guess is
# involved: there is no part of India or Alberta within 200 km of Union Station.
#
# Three kinds of name are deliberately absent, and each absence is load-bearing:
#   * `ontario` and `canada` contain the anchor itself;
#   * `new york` and `pennsylvania` reach to within ~130 km (Buffalo, Niagara
#     Falls NY, Erie PA), so the region says nothing about the posting;
#   * bare two-letter codes, because `on` is Ontario, `in` and `or` and `me`
#     are English words, and `ca` means Canada as often as California.
_FAR_REGIONS = frozenset({
    # Countries.
    'india', 'singapore', 'france', 'spain', 'belgium', 'germany', 'portugal',
    'italy', 'brazil', 'brasil', 'argentina', 'colombia', 'mexico', 'chile',
    'peru', 'uruguay', 'ecuador', 'venezuela', 'bolivia', 'paraguay',
    'australia', 'new zealand', 'japan', 'china', 'taiwan', 'hong kong',
    'south korea', 'korea', 'vietnam', 'thailand', 'philippines', 'indonesia',
    'malaysia', 'israel', 'turkey', 'egypt', 'nigeria', 'kenya', 'ghana',
    'south africa', 'morocco', 'tunisia', 'united arab emirates', 'uae',
    'saudi arabia', 'qatar', 'kuwait', 'bahrain', 'oman', 'jordan', 'lebanon',
    'pakistan', 'bangladesh', 'sri lanka', 'nepal', 'russia', 'ukraine',
    'poland', 'romania', 'serbia', 'bulgaria', 'greece', 'croatia', 'hungary',
    'czechia', 'czech republic', 'slovakia', 'austria', 'switzerland',
    'netherlands', 'denmark', 'sweden', 'norway', 'finland', 'iceland',
    'ireland', 'united kingdom', 'england', 'scotland', 'wales', 'armenia',
    'lithuania', 'latvia', 'estonia', 'slovenia', 'bosnia', 'albania',
    'moldova', 'belarus', 'cyprus', 'malta', 'luxembourg', 'costa rica',
    'panama', 'guatemala', 'el salvador', 'honduras', 'nicaragua',
    'dominican republic', 'jamaica', 'trinidad', 'barbados',
    # Canadian provinces and territories other than Ontario.
    'british columbia', 'alberta', 'saskatchewan', 'manitoba', 'quebec',
    'nova scotia', 'new brunswick', 'newfoundland', 'labrador',
    'prince edward island', 'yukon', 'nunavut', 'northwest territories',
    # US states whose nearest point is still well beyond the ceiling.
    'california', 'texas', 'washington', 'oregon', 'nevada', 'arizona', 'utah',
    'colorado', 'idaho', 'montana', 'wyoming', 'new mexico', 'oklahoma',
    'kansas', 'nebraska', 'iowa', 'missouri', 'arkansas', 'louisiana',
    'mississippi', 'alabama', 'georgia', 'florida', 'south carolina',
    'north carolina', 'tennessee', 'kentucky', 'virginia', 'west virginia',
    'maryland', 'delaware', 'new jersey', 'connecticut', 'rhode island',
    'massachusetts', 'vermont', 'new hampshire', 'maine', 'minnesota',
    'wisconsin', 'illinois', 'indiana', 'north dakota', 'south dakota',
    'hawaii', 'alaska',
})

# The radius beyond which `_FAR_REGIONS` is asserted to hold. It is not the
# user's setting — it is the number the region list was *built* against, and
# `verdict` refuses to use the list for a wider radius than this rather than
# quietly asserting something the data does not support.
IN_RANGE_CEILING_KM = 200.0

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
    """Lowercase alphanumeric tokens, with diacritics folded away first.

    The fold is not cosmetic. `[^a-z0-9]+` treats an accented letter as a
    separator, so `Montréal` split into `montr` + `al` and never matched
    `montreal` — and `São Paulo`, `Bogotá` and `Québec` failed the same way.
    Folding to the unaccented form is what a gazetteer keyed in ASCII needs to
    be reachable from the strings employers actually write.
    """
    folded = unicodedata.normalize('NFD', (location or '').lower())
    stripped = ''.join(c for c in folded if unicodedata.category(c) != 'Mn')
    return [token for token in _TOKEN_SPLIT.split(stripped) if token]


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


_MAX_REGION_TOKENS = max(len(key.split()) for key in _FAR_REGIONS)


def names_a_far_region(location: str) -> bool:
    """Whether the string names a region wholly beyond `IN_RANGE_CEILING_KM`.

    Whole-token runs only, the same shape as the gazetteer scan, so `indiana`
    is not read as `india` and `oregon` is not read as `or`. Note this is
    deliberately *not* longest-match-wins: a string mentioning two regions is
    out of range if either of them is, and the anchor's own region is absent
    from the set, so there is nothing a longer span could rescue.
    """
    tokens = tokenize(location)
    for width in range(min(_MAX_REGION_TOKENS, len(tokens)), 0, -1):
        for start in range(len(tokens) - width + 1):
            if ' '.join(tokens[start:start + width]) in _FAR_REGIONS:
                return True
    return False


def is_fully_remote(job) -> bool:
    """Remote by the board's flag, and not contradicted by the body.

    The same band `GET /feed?sort=distance` groups first and
    `src/lib/jobs.ts:attendsInPerson` mirrors on the card. `work_location` is a
    second column beside `remote` rather than an overwrite, so a "Remote -
    Canada" posting that turns out to want two days a week in an office is
    *not* fully remote and has to be judged on its distance like anything else.
    """
    remote = _field(job, 'remote')
    if not remote:
        return False
    return (_field(job, 'work_location') or '') not in ('onsite', 'hybrid')


def _field(job, snake: str):
    """One column, whichever case the caller's row happens to use.

    `triager.run_gate_sweep` hands `preferences.hard_gate` a raw sqlite row
    (snake_case) while other callers pass an API-shaped dict (camelCase).
    `soft_flags` already hedges the same way for `salary_max`.
    """
    if snake in job:
        return job[snake]
    head, *rest = snake.split('_')
    return job.get(head + ''.join(part.title() for part in rest))


def verdict(job, max_km: float) -> str:
    """`'in_range'`, `'out_of_range'` or `'unknown'` for one posting.

    Deliberately three-valued. `unknown` is not a soft `out_of_range`: a
    location the gazetteer could not read is *missing information about a
    posting, not a verdict on it*, and the caller is expected to keep it — the
    same reason the distance sort ranks unplaced rows on their keyword score
    instead of sinking them to the bottom.

    Order of evidence, strongest first:

    1. **Fully remote** is in range at any radius. It is a different state from
       zero kilometres, and the question "could I get there" does not arise.
    2. **A stored `distance_km`** was computed from a real point, by this
       module, at sync time.
    3. **A far region named in the text**, which settles the bound without ever
       producing a number — but only up to `IN_RANGE_CEILING_KM`, the radius
       the region list was actually built against.
    """
    if not max_km or max_km <= 0:
        return 'in_range'
    if is_fully_remote(job):
        return 'in_range'

    km = _field(job, 'distance_km')
    if km is not None:
        try:
            return 'in_range' if float(km) <= max_km else 'out_of_range'
        except (TypeError, ValueError):
            pass

    if max_km <= IN_RANGE_CEILING_KM and names_a_far_region(_field(job, 'location') or ''):
        return 'out_of_range'
    return 'unknown'


def reading_for(job: dict) -> Reading | None:
    """The best reading available for one normalized posting.

    Source-supplied coordinates beat the gazetteer — Adzuna is the only adapter
    that carries them, and a real point is better than a city centroid.
    """
    exact = from_coords(job.get('latitude'), job.get('longitude'))
    if exact is not None:
        return exact
    return resolve(job.get('location') or '')
