from backend.jobs import preferences


def loaded(**profile):
    return {'profile': profile, 'roles': []}


def test_the_clearance_gate():
    assert 'clearance' in preferences.hard_gate(
        {'description': 'Must hold Secret clearance'},
        loaded(avoidClearanceRoles=True),
    )


def test_the_retired_text_gates_no_longer_reject():
    """`remoteOnly` and `allowedLocations` were replaced by the commute radius.

    Both decided geography with string operations — one searching the body for
    "remote"/"on site", the other substring-matching a comma-separated list —
    and neither could tell that Mississauga is 24 km away. The columns are kept
    so nothing is destroyed, but a value left in either must no longer filter
    anything, or a stale setting would silently keep hiding postings.
    """
    assert preferences.hard_gate(
        {'location': 'Toronto', 'description': 'Five days in office'},
        loaded(remoteOnly=True),
    ) == ''
    assert preferences.hard_gate(
        {'location': 'Vancouver', 'description': ''},
        loaded(allowedLocations='Toronto, Montreal'),
    ) == ''


def test_past_employers_are_blacklisted_but_current_employer_is_not():
    data = {'profile': {'companyBlacklist': ['Explicit Co']}, 'roles': [
        {'company': 'Old Co', 'endLabel': '2024'},
        {'company': 'Current Co', 'endLabel': 'Present'},
    ]}
    assert preferences.hard_gate({'company': 'Old Co'}, data)
    assert preferences.hard_gate({'company': 'Explicit Co'}, data)
    assert preferences.hard_gate({'company': 'Current Co'}, data) == ''


def test_soft_preferences_annotate_and_never_reject():
    flags = preferences.soft_flags(
        {'salaryMax': 120000, 'description': 'Flexible hours'},
        loaded(softSalaryFloor=140000, softPreferences='parental leave'),
    )
    assert {flag['kind'] for flag in flags} == {
        'salary_below_preference', 'soft_preference_missing',
    }


# --------------------------------------------------------------------------
# The commute radius
# --------------------------------------------------------------------------

def test_the_radius_rejects_a_posting_known_to_be_far():
    reason = preferences.hard_gate(
        {'location': 'Vancouver, BC', 'description': '', 'distance_km': 3359.0},
        loaded(maxDistanceKm=200))
    assert 'beyond your 200 km radius' in reason


def test_the_radius_rejects_a_far_region_with_no_distance():
    """The region list is what makes the gate useful on unplaced rows."""
    reason = preferences.hard_gate(
        {'location': 'Bengaluru, India', 'description': '', 'distance_km': None},
        loaded(maxDistanceKm=200))
    assert 'beyond your 200 km radius' in reason


def test_the_radius_keeps_a_posting_inside_it():
    assert preferences.hard_gate(
        {'location': 'Mississauga, ON', 'description': '', 'distance_km': 24.0},
        loaded(maxDistanceKm=200)) == ''


def test_the_radius_never_rejects_a_location_it_could_not_read():
    """An unreadable location is missing information, not a far-away job.

    These are the ~75 rows that say only "Canada", "United States" or "N/A",
    and hiding them would be a verdict the data does not support.
    """
    for location in ('Canada', 'N/A', 'United States', ''):
        assert preferences.hard_gate(
            {'location': location, 'description': '', 'distance_km': None},
            loaded(maxDistanceKm=200)) == '', location


def test_the_radius_exempts_a_fully_remote_posting():
    assert preferences.hard_gate(
        {'location': 'Remote - Anywhere', 'description': '', 'remote': 1,
         'work_location': 'remote', 'distance_km': None},
        loaded(maxDistanceKm=200)) == ''


def test_a_remote_flag_the_body_contradicts_is_still_judged_on_distance():
    reason = preferences.hard_gate(
        {'location': 'Remote - Canada', 'description': '', 'remote': 1,
         'work_location': 'hybrid', 'distance_km': 3359.0},
        loaded(maxDistanceKm=200))
    assert 'beyond your' in reason


def test_an_unset_radius_is_inert():
    """Every other gate here is inert when unset; this one has to match."""
    job = {'location': 'Bengaluru, India', 'description': '', 'distance_km': 13000.0}
    assert preferences.hard_gate(job, loaded()) == ''
    assert preferences.hard_gate(job, loaded(maxDistanceKm=None)) == ''
    assert preferences.hard_gate(job, loaded(maxDistanceKm='')) == ''


def test_the_radius_gate_does_not_raise_on_a_null_distance():
    """`None > max_km` would TypeError, and this runs over every pending row."""
    preferences.hard_gate({'location': 'Somewhere', 'description': ''},
                          loaded(maxDistanceKm=200))
