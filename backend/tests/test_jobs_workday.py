from backend.jobs.sources import workday


def test_workday_url_parser_allows_only_known_host_shape():
    assert workday.parse_board_url(
        'https://acme.wd3.myworkdayjobs.com/en-US/External/jobs'
    ) == {'host': 'acme.wd3.myworkdayjobs.com', 'tenant': 'acme', 'site': 'External'}
    import pytest
    with pytest.raises(Exception):
        workday.parse_board_url('https://evil.example.com/en-US/External')


def test_workday_fetch_uses_cxs_json_and_full_detail(monkeypatch):
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    monkeypatch.setattr(workday.requests, 'post', lambda url, **kwargs: Response({
        'total': 1, 'jobPostings': [{'title': 'Platform Engineer',
          'externalPath': '/job/Toronto/Platform_R1', 'locationsText': 'Toronto',
          'bulletFields': ['R1'], 'postedOn': '2026-08-01'}]}))
    monkeypatch.setattr(workday.requests, 'get', lambda url, **kwargs: Response({
        'jobPostingInfo': {'jobReqId': 'R1', 'title': 'Platform Engineer',
          'location': 'Toronto', 'jobDescription': '<p>Build Kubernetes.</p>'}}))
    result = workday.fetch({'host': 'acme.wd3.myworkdayjobs.com',
                            'tenant': 'acme', 'site': 'External'})
    assert result.jobs[0]['sourceId'].endswith(':R1')
    assert result.jobs[0]['description'] == 'Build Kubernetes.'
    assert result.jobs[0]['url'].startswith('https://acme.wd3.myworkdayjobs.com/')


def test_workday_preserves_absolute_external_url(monkeypatch):
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    monkeypatch.setattr(workday.requests, 'post', lambda url, **kwargs: Response({
        'total': 1, 'jobPostings': [{'externalPath': '/job/R1'}]}))
    monkeypatch.setattr(workday.requests, 'get', lambda url, **kwargs: Response({
        'jobPostingInfo': {'jobReqId': 'R1', 'externalUrl': 'https://jobs.example.org/R1'}}))
    result = workday.fetch({'host': 'acme.wd3.myworkdayjobs.com',
                            'tenant': 'acme', 'site': 'External'})
    assert result.jobs[0]['url'] == 'https://jobs.example.org/R1'


# --------------------------------------------------------------------------
# Location facets: the scope the registered URL already chose
# --------------------------------------------------------------------------

import pytest


@pytest.mark.parametrize('query, expected', [
    # The four spellings of two facets seen across the registered boards.
    ('?Location_Country=a30a87ed25634629aa6c3958aa2b91ea',
     {'Location_Country': ['a30a87ed25634629aa6c3958aa2b91ea']}),
    ('?locationCountry=a30a87ed25634629aa6c3958aa2b91ea',
     {'locationCountry': ['a30a87ed25634629aa6c3958aa2b91ea']}),
    ('?LocationCountry=a30a87ed25634629aa6c3958aa2b91ea',
     {'LocationCountry': ['a30a87ed25634629aa6c3958aa2b91ea']}),
    ('?locations=951c033a9bfe1000bb1588990a0a0000&locations=29827a73287b01033875f6106f2c0000',
     {'locations': ['951c033a9bfe1000bb1588990a0a0000',
                    '29827a73287b01033875f6106f2c0000']}),
])
def test_the_url_parser_keeps_location_facets(query, expected):
    parsed = workday.parse_board_url(
        f'https://acme.wd3.myworkdayjobs.com/External{query}')
    assert parsed['facets'] == expected


def test_the_url_parser_ignores_tracking_and_unknown_parameters():
    """Autodesk's real URL carries a Google Analytics blob beside the facet.

    Forwarding it would send Workday a facet it has never heard of, and an
    unrecognised facet is how a board comes back empty.
    """
    parsed = workday.parse_board_url(
        'https://autodesk.wd1.myworkdayjobs.com/Ext'
        '?_gl=1*1evi7of*_gcl_au*MjAzOTY5NDYwMS4xNzUyNDEwNjg0'
        '&locationCountry=a30a87ed25634629aa6c3958aa2b91ea')
    assert parsed['facets'] == {
        'locationCountry': ['a30a87ed25634629aa6c3958aa2b91ea']}


def test_a_board_with_no_query_string_carries_no_facets():
    parsed = workday.parse_board_url('https://acme.wd3.myworkdayjobs.com/External')
    assert 'facets' not in parsed and 'searchText' not in parsed


def test_a_free_text_query_becomes_search_text_not_a_facet():
    """Salesforce registers `?q=Canada`, which is the board's search box."""
    parsed = workday.parse_board_url(
        'https://salesforce.wd12.myworkdayjobs.com/External_Career_Site?q=Canada')
    assert parsed['searchText'] == 'Canada'
    assert 'facets' not in parsed


def test_fetch_sends_the_facets_to_workday(monkeypatch):
    """The one that matters: the filter has to reach the request body.

    Every other fixture in this file monkeypatches `post` with `**kwargs` and
    never looks at what was sent, so nothing here previously pinned that
    `appliedFacets` was empty — which is exactly how it stayed empty.
    """
    sent = []

    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload

    def capture(url, **kwargs):
        sent.append(kwargs.get('json'))
        return Response({'total': 0, 'jobPostings': []})

    monkeypatch.setattr(workday.requests, 'post', capture)
    workday.fetch({'host': 'acme.wd3.myworkdayjobs.com', 'tenant': 'acme',
                   'site': 'External',
                   'facets': {'locationCountry': ['a30a87ed']},
                   'searchText': 'Canada'})
    assert sent[0]['appliedFacets'] == {'locationCountry': ['a30a87ed']}
    assert sent[0]['searchText'] == 'Canada'


def test_fetch_without_facets_still_sends_an_empty_applied_facets(monkeypatch):
    sent = []

    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload

    monkeypatch.setattr(workday.requests, 'post',
                        lambda url, **kwargs: (sent.append(kwargs.get('json')),
                                               Response({'total': 0, 'jobPostings': []}))[1])
    workday.fetch({'host': 'acme.wd3.myworkdayjobs.com', 'tenant': 'acme',
                   'site': 'External'})
    assert sent[0]['appliedFacets'] == {}
    assert sent[0]['searchText'] == ''


def test_existing_boards_are_rescoped_from_their_stored_url(client):
    """The 27 boards registered before facets were kept fix themselves.

    `workday_boards.url` is on the row, so the scope the user originally chose
    is re-derivable with no network and no re-registration — unlike the company
    boards, whose table stores no URL.
    """
    import json as json_mod
    import time as time_mod

    from backend.db.connection import get_db, _ensure_workday_board_facets

    db = get_db()
    now = int(time_mod.time())
    url = ('https://abbott.wd5.myworkdayjobs.com/abbottcareers'
           '?Location_Country=a30a87ed25634629aa6c3958aa2b91ea')
    db.execute(
        'INSERT INTO workday_boards (id, url, label, params, created_at, updated_at)'
        ' VALUES (?, ?, ?, ?, ?, ?)',
        ('stale', url, 'Abbott',
         json_mod.dumps({'host': 'abbott.wd5.myworkdayjobs.com',
                         'tenant': 'abbott', 'site': 'abbottcareers'}),
         now, now))
    db.commit()

    _ensure_workday_board_facets(db)

    params = json_mod.loads(
        db.execute('SELECT params FROM workday_boards WHERE id=?', ('stale',))
        .fetchone()['params'])
    assert params['facets'] == {
        'Location_Country': ['a30a87ed25634629aa6c3958aa2b91ea']}
    # The keys that were already right are untouched.
    assert params['tenant'] == 'abbott' and params['site'] == 'abbottcareers'


def test_rescoping_survives_a_board_whose_url_no_longer_parses(client):
    """One bad row must not take down startup for every other board."""
    import json as json_mod
    import time as time_mod

    from backend.db.connection import get_db, _ensure_workday_board_facets

    db = get_db()
    now = int(time_mod.time())
    db.execute(
        'INSERT INTO workday_boards (id, url, label, params, created_at, updated_at)'
        ' VALUES (?, ?, ?, ?, ?, ?)',
        ('broken', 'not-a-url', 'Broken', json_mod.dumps({}), now, now))
    db.commit()

    _ensure_workday_board_facets(db)  # must not raise
