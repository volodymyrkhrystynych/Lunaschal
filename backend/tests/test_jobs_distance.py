"""The commute gazetteer, and the sort that reads it.

The interesting cases are all about *refusing* to produce a number: an
unrecognised place, a bare ambiguous name, a country. A wrong distance is worse
than no distance here, because the feed sorts on it.
"""
import json

import pytest

from backend.jobs import distance


# --------------------------------------------------------------------------
# resolve()
# --------------------------------------------------------------------------

@pytest.mark.parametrize('location, expected_place', [
    ('Toronto, ON, Canada', 'toronto'),
    ('toronto', 'toronto'),
    ('Hybrid - Toronto', 'toronto'),
    ('Mississauga, Ontario', 'mississauga'),
    ('Markham, Ontario, Canada', 'markham'),
    ('Waterloo, ON', 'waterloo'),
    ('Ottawa', 'ottawa'),
])
def test_resolve_finds_known_places(location, expected_place):
    reading = distance.resolve(location)
    assert reading is not None
    assert reading.place == expected_place
    assert reading.km >= 0


def test_toronto_is_near_the_anchor():
    reading = distance.resolve('Toronto, ON')
    assert reading.km < 3
    assert reading.precision == 'district'


def test_the_905_lands_in_a_plausible_range():
    assert 15 < distance.resolve('Mississauga').km < 35
    assert 15 < distance.resolve('Markham').km < 35
    assert 50 < distance.resolve('Hamilton').km < 80
    assert 80 < distance.resolve('Waterloo, ON').km < 120


@pytest.mark.parametrize('location', [
    '',
    '   ',
    'Remote - Canada',
    'Canada',
    'Ontario',
    'United States',
    'Remote - United Kingdom',
    'Anywhere',
    'Nowhereville, Nowhere',
])
def test_unrecognised_places_resolve_to_none(location):
    """A country is not a point. Guessing one would sort real jobs off the feed."""
    assert distance.resolve(location) is None


def test_longest_match_wins_over_a_contained_name():
    """'North York' must not resolve as the borough of York."""
    north_york = distance.resolve('North York, ON')
    assert north_york.place == 'north york'

    # Same rule, the case where getting it wrong crosses a border.
    assert distance.resolve('New York, NY').place == 'new york'


def test_nearest_wins_for_a_multi_location_posting():
    """A posting with several offices is reachable at the closest one."""
    reading = distance.resolve('Toronto, Ontario, Canada; New York, NY')
    assert reading.place == 'toronto'

    # Order in the string must not decide it.
    assert distance.resolve('New York, NY; Toronto, ON').place == 'toronto'


@pytest.mark.parametrize('bare', ['London', 'Cambridge'])
def test_bare_ambiguous_names_resolve_to_none(bare):
    """London ON and London UK are 5,500 km apart. Ambiguity resolves to None."""
    assert distance.resolve(bare) is None


@pytest.mark.parametrize('location, low, high', [
    ('London, ON', 140, 200),
    ('London, Ontario', 140, 200),
    ('London, UK', 5500, 6000),
    ('London, United Kingdom', 5500, 6000),
    ('Cambridge, ON', 60, 100),
    ('Cambridge, MA', 600, 750),
])
def test_a_qualified_ambiguous_name_resolves(location, low, high):
    reading = distance.resolve(location)
    assert reading is not None
    assert low < reading.km < high


# --------------------------------------------------------------------------
# Coordinates, and the precedence between the two sources
# --------------------------------------------------------------------------

def test_coordinates_beat_the_gazetteer():
    """Adzuna posts a real point; a city centroid is the fallback, not the rule."""
    job = {'location': 'Ottawa, ON', 'latitude': 43.6532, 'longitude': -79.3832}
    reading = distance.reading_for(job)
    assert reading.precision == 'exact'
    assert reading.km < 3  # Toronto's point, not Ottawa's name.


def test_half_a_coordinate_falls_back_to_the_name():
    """backend/geo.py's rule: a lone latitude is not a location."""
    job = {'location': 'Ottawa, ON', 'latitude': 45.4215, 'longitude': None}
    reading = distance.reading_for(job)
    assert reading.precision == 'city'
    assert reading.place == 'ottawa'


def test_unusable_coordinates_do_not_produce_a_reading():
    assert distance.from_coords('nonsense', 12) is None
    assert distance.from_coords(float('nan'), 12) is None
    assert distance.from_coords(None, None) is None


def test_reading_for_an_unplaceable_posting_is_none():
    assert distance.reading_for({'location': 'Remote - Canada'}) is None
    assert distance.reading_for({}) is None


def test_haversine_is_symmetric_and_zero_at_a_point():
    a, b = (43.6453, -79.3806), (45.4215, -75.6972)
    assert distance.haversine(a, a) == 0
    assert distance.haversine(a, b) == pytest.approx(distance.haversine(b, a))
    assert 340 < distance.haversine(a, b) < 365


def test_the_anchor_is_union_station():
    """A regression pin: the number on every card is relative to this point."""
    assert distance.ANCHOR == pytest.approx((43.6453, -79.3806))


# --------------------------------------------------------------------------
# Storage and the feed
# --------------------------------------------------------------------------

def _job(client, **fields):
    body = {'title': 'Engineer', 'company': 'Acme', 'description': 'python'}
    body.update(fields)
    return client.post('/api/jobs', json=body).get_json()


def test_a_created_job_stores_its_distance(client):
    job = _job(client, location='Mississauga, ON')
    assert job['distanceKm'] is not None
    assert 15 < job['distanceKm'] < 35
    assert job['distancePrecision'] == 'city'


def test_an_unplaceable_job_stores_null_not_zero(client):
    """NULL is load-bearing: it means unknown, and the UI must not print it."""
    job = _job(client, location='Remote - Canada')
    assert job['distanceKm'] is None
    assert job['distancePrecision'] == ''


def test_feed_distance_sort_puts_remote_first_then_nearest(client):
    _job(client, title='Far', location='Ottawa, ON')
    _job(client, title='Near', location='Toronto, ON')
    _job(client, title='Middle', location='Markham, ON')
    _job(client, title='Anywhere', location='Remote - Canada', remote=True)
    _job(client, title='Unplaced', location='Remote - Canada')

    feed = client.get('/api/jobs/feed?sort=distance').get_json()
    assert [j['title'] for j in feed] == [
        'Anywhere',   # remote leads its own band
        'Near', 'Middle', 'Far',  # then nearest outwards
        'Unplaced',   # then what could not be placed at all
    ]


def test_the_default_sort_is_unchanged(client):
    """No `sort` param must behave exactly as the feed did before this existed."""
    _job(client, title='Far', location='Ottawa, ON')
    _job(client, title='Near', location='Toronto, ON')

    default = [j['id'] for j in client.get('/api/jobs/feed').get_json()]
    explicit = [j['id'] for j in client.get('/api/jobs/feed?sort=match').get_json()]
    assert default == explicit


def test_rescore_backfills_distances_for_rows_that_predate_the_column(client):
    from backend.db.connection import get_db

    _job(client, location='Markham, ON')
    db = get_db()
    db.execute("UPDATE jobs SET distance_km=NULL, distance_precision=''")
    db.commit()

    result = client.post('/api/jobs/rescore').get_json()
    assert result['distances'] == 1
    row = db.execute('SELECT distance_km FROM jobs').fetchone()
    assert row['distance_km'] is not None


def test_rescore_reads_coordinates_out_of_stored_raw(client):
    """A row synced before the column existed is fixed without re-fetching."""
    from backend.db.connection import get_db
    from backend.jobs import sync

    db = get_db()
    _job(client, location='Ottawa, ON')
    db.execute(
        'UPDATE jobs SET raw=?',
        (json.dumps({'latitude': 43.6532, 'longitude': -79.3832}),),
    )
    db.commit()

    assert sync.recompute_distances(db) == 1
    row = db.execute(
        'SELECT distance_km, distance_precision FROM jobs'
    ).fetchone()
    assert row['distance_precision'] == 'exact'
    assert row['distance_km'] < 3


# --------------------------------------------------------------------------
# The model's contribution: a bounded pointer at a known place
# --------------------------------------------------------------------------

def test_selectable_places_excludes_the_ambiguous_bare_names():
    """The model must not be offered a name that resolves to nothing."""
    places = distance.selectable_places()
    assert 'toronto' in places and 'london on' in places
    assert 'london' not in places and 'cambridge' not in places
    assert places == sorted(places)  # stable order keeps the grammar cacheable


def test_resolve_keys_takes_the_nearest_and_marks_it_inferred():
    reading = distance.resolve_keys(['ottawa', 'toronto'])
    assert reading.place == 'toronto'
    assert reading.precision == 'inferred'


def test_resolve_keys_drops_anything_not_in_the_gazetteer():
    assert distance.resolve_keys(['atlantis']) is None
    assert distance.resolve_keys(['london']) is None  # bare ambiguous
    assert distance.resolve_keys([]) is None
    assert distance.resolve_keys(None) is None


def test_the_schema_binds_cities_to_the_list_it_is_given():
    from backend.ai import job_triage

    schema = job_triage.build_schema(['kubernetes'], ['toronto', 'ottawa'])
    assert schema['properties']['cities']['items']['enum'] == ['toronto', 'ottawa']
    assert schema['properties']['workLocation']['enum'] == list(job_triage.WORK_LOCATIONS)
    assert 'workLocation' in schema['required']


def test_the_schema_omits_cities_when_there_is_nothing_to_bind():
    """Unbounded free-text cities would put a fabricated place on the sort key."""
    from backend.ai import job_triage

    assert 'cities' not in job_triage.build_schema(None, [])['properties']


def test_normalize_drops_a_city_outside_the_bound():
    from backend.ai import job_triage

    result = job_triage.normalize_result(
        {'relevant': True, 'reason': 'r', 'fit': 'strong', 'summary': 's',
         'flags': [], 'workLocation': 'hybrid',
         'cities': ['toronto', 'atlantis']},
        {}, ['toronto', 'ottawa'],
    )
    assert result['cities'] == ['toronto']
    assert result['workLocation'] == 'hybrid'


def test_normalize_falls_back_to_unclear_for_a_bad_work_location():
    from backend.ai import job_triage

    result = job_triage.normalize_result(
        {'relevant': True, 'reason': 'r', 'fit': 'strong', 'summary': 's',
         'flags': [], 'workLocation': 'moon'}, {}, [],
    )
    assert result['workLocation'] == 'unclear'
    assert result['cities'] == []


def test_an_inferred_city_fills_a_distance_the_gazetteer_could_not(client):
    from backend.db.connection import get_db
    from backend.jobs import triager

    job = _job(client, location='Remote - Canada', remote=True)
    db = get_db()
    triager._store(db, job['id'], {
        'relevant': True, 'reason': '', 'fit': 'strong',
        'summary': 'Two days a week in the Toronto office.',
        'flags': [], 'workLocation': 'hybrid', 'cities': ['toronto'],
    })

    row = db.execute(
        'SELECT distance_km, distance_precision, work_location FROM jobs WHERE id=?',
        (job['id'],),
    ).fetchone()
    assert row['distance_km'] < 3
    assert row['distance_precision'] == 'inferred'
    assert row['work_location'] == 'hybrid'


def test_an_inferred_city_never_overwrites_a_structured_reading(client):
    """The board's own location field is a statement; this is an inference."""
    from backend.db.connection import get_db
    from backend.jobs import triager

    job = _job(client, location='Ottawa, ON')
    db = get_db()
    before = db.execute('SELECT distance_km FROM jobs WHERE id=?',
                        (job['id'],)).fetchone()['distance_km']

    triager._store(db, job['id'], {
        'relevant': True, 'reason': '', 'fit': 'strong', 'summary': '',
        'flags': [], 'workLocation': 'onsite', 'cities': ['toronto'],
    })

    row = db.execute(
        'SELECT distance_km, distance_precision FROM jobs WHERE id=?',
        (job['id'],),
    ).fetchone()
    assert row['distance_km'] == before      # Ottawa's, not Toronto's
    assert row['distance_precision'] == 'city'


def test_a_body_that_contradicts_the_remote_flag_leaves_the_remote_band(client):
    """'Remote' that means two days in a Toronto office is not a remote job."""
    from backend.db.connection import get_db
    from backend.jobs import triager

    truly_remote = _job(client, title='Anywhere', location='Remote - Canada',
                        remote=True)
    hybrid = _job(client, title='Actually hybrid', location='Remote - Canada',
                  remote=True)
    near = _job(client, title='Near', location='Toronto, ON')

    db = get_db()
    for job, verdict in (
        (truly_remote, {'workLocation': 'remote', 'cities': []}),
        (hybrid, {'workLocation': 'hybrid', 'cities': ['markham']}),
    ):
        triager._store(db, job['id'], {
            'relevant': True, 'reason': '', 'fit': '', 'summary': '',
            'flags': [], **verdict,
        })
    del near

    feed = client.get('/api/jobs/feed?sort=distance').get_json()
    # The genuinely remote one leads; the hybrid one sorts by its distance,
    # behind the Toronto posting rather than ahead of it.
    assert [j['title'] for j in feed] == ['Anywhere', 'Near', 'Actually hybrid']


# --------------------------------------------------------------------------
# verdict() — the three-valued answer the commute radius gate reads
# --------------------------------------------------------------------------

def _row(location='', *, remote=0, work_location='', distance_km=None):
    """A posting in the snake_case shape `preferences.hard_gate` receives."""
    return {'location': location, 'remote': remote,
            'work_location': work_location, 'distance_km': distance_km}


@pytest.mark.parametrize('location', [
    'Bengaluru, India',
    'Munich, Germany',
    'Sydney, New South Wales, Australia',
    'Anywhere in Belgium',
    'Vancouver, British Columbia',
    'Austin, Texas',
])
def test_a_far_region_is_out_of_range_without_a_distance(location):
    """A bound needs no point: no part of India is within 200 km of the anchor.

    The row carries no `distance_km` — the whole value of the region list is
    settling postings the gazetteer never placed.
    """
    assert distance.verdict(_row(location), 200.0) == 'out_of_range'


@pytest.mark.parametrize('location', [
    'Canada',
    'Remote - Canada',
    'Ontario',
    'Buffalo, NY',
    'Erie, Pennsylvania',
    'United States',
    'N/A',
    '',
])
def test_a_region_that_reaches_the_anchor_stays_unknown(location):
    """`unknown` is not a soft rejection, and these genuinely have no answer.

    Ontario and Canada contain the anchor; New York and Pennsylvania come
    within ~130 km at Buffalo, Niagara Falls and Erie. A region that overlaps
    the radius says nothing about the posting, so it must not be in the list.
    """
    assert distance.verdict(_row(location), 200.0) == 'unknown'


def test_a_stored_distance_decides_before_the_region_list():
    near = _row('Somewhere, India', distance_km=12.0)
    assert distance.verdict(near, 200.0) == 'in_range'


def test_fully_remote_is_in_range_at_any_radius():
    assert distance.verdict(_row('Remote', remote=1), 200.0) == 'in_range'
    assert distance.verdict(_row('Remote', remote=1), 1.0) == 'in_range'


def test_a_body_that_contradicts_the_remote_flag_is_judged_on_distance():
    """The case `work_location` exists for: "Remote" that wants office days.

    The board flagged it remote and the model read hybrid out of the body, so
    it is not fully remote and has to answer for its 3,359 km like anything
    else.
    """
    hybrid = _row('Vancouver, BC', remote=1, work_location='hybrid',
                  distance_km=3359.0)
    assert distance.verdict(hybrid, 200.0) == 'out_of_range'
    onsite = _row('Vancouver, BC', remote=1, work_location='onsite',
                  distance_km=3359.0)
    assert distance.verdict(onsite, 200.0) == 'out_of_range'


def test_an_unset_radius_puts_everything_in_range():
    far = _row('Bengaluru, India', distance_km=13000.0)
    assert distance.verdict(far, 0) == 'in_range'
    assert distance.verdict(far, None) == 'in_range'


def test_the_region_list_is_not_applied_beyond_the_ceiling_it_was_built_for():
    """It asserts "further than 200 km", not "further than any radius".

    Asked about a 5,000 km radius the list has nothing to say about Germany,
    so the honest answer is `unknown` rather than a bound it cannot support.
    """
    row = _row('Munich, Germany')
    assert distance.verdict(row, distance.IN_RANGE_CEILING_KM) == 'out_of_range'
    assert distance.verdict(row, 5000.0) == 'unknown'


def test_verdict_reads_a_camelcase_row_too():
    """`hard_gate` is handed a raw sqlite row by one caller and a dict by another."""
    camel = {'location': 'Bengaluru', 'remote': 0, 'workLocation': '',
             'distanceKm': 13000.0}
    assert distance.verdict(camel, 200.0) == 'out_of_range'


def test_a_null_distance_never_raises():
    """`None > max_km` would TypeError inside the gate sweep."""
    assert distance.verdict(_row('Nowhere in particular'), 200.0) == 'unknown'


def test_a_region_word_inside_a_longer_token_is_not_a_match():
    """`indiana` is not `india`, and `oregon` is not `or`."""
    assert not distance.names_a_far_region('Indianapolis')
    assert distance.names_a_far_region('Indianapolis, Indiana')


# --------------------------------------------------------------------------
# Diacritics
# --------------------------------------------------------------------------

@pytest.mark.parametrize('location, expected', [
    ('Montréal, Quebec, Canada', 'montreal'),
    ('MONTRÉAL', 'montreal'),
    ('São Paulo', 'sao paulo'),
    ('Bogotá, Colombia', 'bogota'),
])
def test_accented_names_reach_the_ascii_gazetteer(location, expected):
    """`[^a-z0-9]` treated an accent as a separator, splitting montréal in two."""
    reading = distance.resolve(location)
    assert reading is not None and reading.place == expected


# --------------------------------------------------------------------------
# Paris joins the ambiguous set
# --------------------------------------------------------------------------

def test_bare_paris_is_declined_because_paris_ontario_is_in_range():
    assert distance.resolve('Paris') is None


def test_a_qualified_paris_resolves_on_either_side():
    assert distance.resolve('Paris, France').km > 5000
    assert distance.resolve('Paris, ON').km < 200


# --------------------------------------------------------------------------
# recompute_distances: two ways it used to lose a reading
# --------------------------------------------------------------------------

def test_rescore_does_not_clear_a_reading_the_model_inferred(client):
    """`inferred` is the only reading an unplaceable row can have.

    It is written from the posting body precisely because the `location` field
    cannot be read, so recomputing from that same field produces None — and an
    unguarded overwrite deleted it. `_store_inferred_distance` is guarded on
    `distance_km IS NULL`, so only a re-triage could ever put it back.
    """
    from backend.db.connection import get_db
    from backend.jobs import sync

    db = get_db()
    _job(client, location='Remote - Canada')
    db.execute("UPDATE jobs SET distance_km=1.5, distance_precision='inferred'")
    db.commit()

    sync.recompute_distances(db)
    row = db.execute('SELECT distance_km, distance_precision FROM jobs').fetchone()
    assert row['distance_km'] == 1.5
    assert row['distance_precision'] == 'inferred'


def test_rescore_still_refreshes_a_reading_it_can_recompute(client):
    """The guard must not freeze rows a gazetteer improvement should reach."""
    from backend.db.connection import get_db
    from backend.jobs import sync

    db = get_db()
    _job(client, location='Markham, ON')
    db.execute("UPDATE jobs SET distance_km=9999.0, distance_precision='inferred'")
    db.commit()

    sync.recompute_distances(db)
    row = db.execute('SELECT distance_km, distance_precision FROM jobs').fetchone()
    assert row['distance_km'] < 100
    assert row['distance_precision'] == 'city'


def test_rescore_finds_coordinates_inside_workdays_raw_wrapper(client):
    """Workday stores `raw` as {'listing', 'detail'}, not a flat row.

    Looking only at the top level found nothing there — a shape difference,
    read as an absence of data.
    """
    from backend.db.connection import get_db
    from backend.jobs import sync

    db = get_db()
    _job(client, location='Nowhere the gazetteer knows')
    db.execute('UPDATE jobs SET raw=?', (json.dumps({
        'listing': {'title': 'Platform Engineer'},
        'detail': {'latitude': 43.6532, 'longitude': -79.3832},
    }),))
    db.commit()

    sync.recompute_distances(db)
    row = db.execute('SELECT distance_km, distance_precision FROM jobs').fetchone()
    assert row['distance_precision'] == 'exact'
    assert row['distance_km'] < 3
