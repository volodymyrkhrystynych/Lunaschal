from backend.jobs import preferences


def loaded(**profile):
    return {'profile': profile, 'roles': []}


def test_hard_remote_location_and_clearance_gates():
    assert 'remote' in preferences.hard_gate(
        {'location': 'Toronto', 'description': 'Five days in office'},
        loaded(remoteOnly=True),
    )
    assert 'outside' in preferences.hard_gate(
        {'location': 'Vancouver', 'description': ''},
        loaded(allowedLocations='Toronto, Montreal'),
    )
    assert 'clearance' in preferences.hard_gate(
        {'description': 'Must hold Secret clearance'},
        loaded(avoidClearanceRoles=True),
    )


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
