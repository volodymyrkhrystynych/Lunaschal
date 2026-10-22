"""Home and Work as dated roles over saved places, and the four things that
read them: the chat's places block, the weather fallback and the commute
anchor (the trail and the briefing are in test_location / test_whereabouts /
test_briefing_events)."""
import pytest

from backend import place_roles
from backend.db.connection import get_db
from backend.day_boundary import day_key_for
from backend.place_roles import PlaceRoleError


def place(client, name, lat=43.6677, lon=-79.3948, **extra):
    body = {'name': name, **({'latitude': lat, 'longitude': lon} if lat is not None else {}), **extra}
    res = client.post('/api/memory/places', json=body)
    assert res.status_code == 200, res.get_json()
    return res.get_json()['id']


def assign(client, role, place_id, since=None):
    body = {'role': role, 'placeId': place_id, **({'startedOn': since} if since else {})}
    return client.post('/api/memory/places/roles', json=body)


# --- the period rules ---

def test_a_new_home_closes_the_old_one_at_the_move_date(client):
    old, new = place(client, 'Old flat', 43.64), place(client, 'New flat')
    assert assign(client, 'home', old, '2025-01-01').status_code == 200
    moved = assign(client, 'home', new, '2026-03-01').get_json()
    assert moved['startedOn'] == '2026-03-01' and moved['endedOn'] is None
    periods = {p['placeName']: p for p in client.get('/api/memory/places/roles').get_json()}
    assert periods['Old flat']['endedOn'] == '2026-03-01'

    db = get_db()
    assert place_roles.roles_on(db, '2026-02-28')['home'].name == 'Old flat'
    assert place_roles.roles_on(db, '2026-03-01')['home'].name == 'New flat'
    assert place_roles.roles_on(db, '2024-12-31')['home'] is None


def test_a_backdated_home_that_overlaps_a_later_one_is_refused(client):
    a, b = place(client, 'A'), place(client, 'B', 43.70)
    assign(client, 'home', a, '2026-03-01')
    # B starting before A is not a move out of A; it would overlap A's period.
    res = assign(client, 'home', b, '2026-02-01')
    assert res.status_code == 400 and 'Home' in res.get_json()['error']
    # And the refused assignment left A untouched.
    assert client.get('/api/memory/places/roles').get_json()[0]['endedOn'] is None


def test_several_works_can_run_at_once_but_one_place_holds_one_role(client):
    home, office, cowork = place(client, 'Home'), place(client, 'Office', 43.65), place(client, 'Cowork', 43.66)
    assert assign(client, 'work', office, '2026-01-01').status_code == 200
    assert assign(client, 'work', cowork, '2026-02-01').status_code == 200
    assert {p.name for p in place_roles.roles_on(get_db(), '2026-02-02')['work']} == {'Office', 'Cowork'}
    # The same Work twice over the same days is a duplicate.
    assert assign(client, 'work', office, '2026-05-01').status_code == 400
    # A place that is Home cannot also be Work for those days.
    assign(client, 'home', home, '2026-01-01')
    assert assign(client, 'work', home, '2026-06-01').status_code == 400


def test_a_role_needs_a_place_with_coordinates(client):
    nowhere = place(client, 'Somewhere', lat=None)
    res = assign(client, 'home', nowhere)
    assert res.status_code == 400 and 'coordinates' in res.get_json()['error']
    assert assign(client, 'home', 'missing').status_code == 404
    assert assign(client, 'garage', place(client, 'X')).status_code == 400
    assert assign(client, 'home', place(client, 'Y', 43.7), 'yesterday').status_code == 400


def test_period_dates_are_editable_and_validated(client):
    p = assign(client, 'work', place(client, 'Office'), '2026-01-01').get_json()
    res = client.patch(f"/api/memory/places/roles/{p['id']}", json={'endedOn': '2026-06-01'})
    assert res.status_code == 200 and res.get_json()['endedOn'] == '2026-06-01'
    assert client.patch(f"/api/memory/places/roles/{p['id']}", json={'endedOn': '2025-12-01'}).status_code == 400
    # Reopening clears the end.
    assert client.patch(f"/api/memory/places/roles/{p['id']}", json={'endedOn': None}).get_json()['endedOn'] is None
    assert client.patch('/api/memory/places/roles/nope', json={}).status_code == 404


def test_a_place_with_history_cannot_be_deleted_until_its_periods_are(client):
    pid = place(client, 'Home')
    period = assign(client, 'home', pid).get_json()
    assert client.delete(f'/api/memory/places/{pid}').status_code == 409
    assert client.delete(f"/api/memory/places/roles/{period['id']}").status_code == 200
    assert client.delete(f"/api/memory/places/roles/{period['id']}").status_code == 404
    assert client.delete(f'/api/memory/places/{pid}').status_code == 200


def test_default_start_is_today(client):
    p = assign(client, 'home', place(client, 'Home')).get_json()
    assert p['startedOn'] == day_key_for()
    assert place_roles.current_home(get_db()).name == 'Home'


def test_module_errors_carry_a_status():
    with pytest.raises(PlaceRoleError) as e:
        place_roles.update_period(get_db(), 'nope', ended_on=None)
    assert e.value.status == 404


# --- consumers ---

def test_chat_places_block_says_which_role_each_place_has(client):
    from backend.places import format_places_context
    old, new, office = place(client, 'Old flat', 43.64), place(client, 'New flat'), place(client, 'Office', 43.65)
    assign(client, 'home', old, '2020-01-01')
    assign(client, 'home', new, '2021-01-01')
    assign(client, 'work', office, '2021-01-01')
    block = format_places_context()
    assert '"role": "current home"' in block
    assert '"role": "former home"' in block
    assert '"role": "current work"' in block


def test_weather_falls_back_to_home_before_the_configured_default(client):
    from backend.weather import sync
    db = get_db()
    client.patch('/api/settings/ai', json={'weatherDefaultLat': 1.0, 'weatherDefaultLon': 2.0})
    assert sync.resolve_location(db)[2] == 'default'
    assign(client, 'home', place(client, 'Home', 43.5, -79.5))
    assert sync.resolve_location(db) == (43.5, -79.5, 'home')
    # A real fix still wins over both.
    sync.record_location(db, day_key_for(), 3.0, 4.0, 'geolocation')
    assert sync.resolve_location(db)[2] == 'geolocation'


def test_commute_is_measured_from_home_and_remeasured_when_it_moves(client):
    assert client.get('/api/jobs/distance-anchor').get_json()['label'] == 'Union Station'
    job = client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme', 'location': 'Mississauga, ON'}).get_json()
    from_union = job['distanceKm']

    # Moving Home to Mississauga's centre makes that job local, without a re-sync.
    assign(client, 'home', place(client, 'Home', 43.5890, -79.6441))
    anchor = client.get('/api/jobs/distance-anchor').get_json()
    assert anchor['label'] == 'Home'
    row = get_db().execute('SELECT distance_km FROM jobs WHERE id=?', (job['id'],)).fetchone()
    assert row['distance_km'] < 3 < from_union


def test_an_inferred_distance_is_remeasured_from_its_kept_place(client):
    from backend.jobs import sync
    db = get_db()
    job = client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme', 'location': 'Remote - Canada'}).get_json()
    db.execute("UPDATE jobs SET distance_km=25, distance_precision='inferred', distance_place='mississauga' WHERE id=?",
               (job['id'],))
    db.commit()
    assign(client, 'home', place(client, 'Home', 43.5890, -79.6441))
    row = db.execute('SELECT distance_km, distance_precision FROM jobs WHERE id=?', (job['id'],)).fetchone()
    assert row['distance_km'] < 3 and row['distance_precision'] == 'inferred'
    # One with no kept place keeps its reading, as before.
    db.execute("UPDATE jobs SET distance_km=25, distance_place='' WHERE id=?", (job['id'],))
    db.commit()
    sync.recompute_distances(db)
    assert db.execute('SELECT distance_km FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == 25
