from backend.jobs import sync


def test_saved_hunt_filters_are_source_independent():
    job = {'title': 'Senior Backend Engineer', 'location': 'Toronto, Canada',
           'remote': True, 'salaryMax': 180000}
    assert sync.matches_hunt(job, {'titleTerms': ['backend'], 'locationFilter': 'Toronto',
                                   'remoteOnly': True, 'salaryFloor': 150000,
                                   'seniority': 'senior'})
    assert not sync.matches_hunt(job, {'titleTerms': ['frontend']})
    assert not sync.matches_hunt(job, {'salaryFloor': 200000})


def test_salary_floor_does_not_guess_when_salary_is_missing():
    assert not sync.matches_hunt({'title': 'Engineer'}, {'salaryFloor': 100000})
