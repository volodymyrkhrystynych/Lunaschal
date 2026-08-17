"""Matching a browser tab to an application.

Strictness is the whole point: the extension records answers against whatever
this returns, so a confident wrong match files an interview answer under the
wrong employer. Ambiguity must resolve to None, not to a best guess.
"""
from backend.jobs import urlmatch


# --------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------

def test_scheme_host_case_and_www_do_not_matter():
    assert (urlmatch.normalize('https://WWW.Acme.com/jobs/1')
            == urlmatch.normalize('http://acme.com/jobs/1'))


def test_a_trailing_slash_does_not_matter():
    assert (urlmatch.normalize('https://acme.com/jobs/1/')
            == urlmatch.normalize('https://acme.com/jobs/1'))


def test_a_fragment_is_dropped():
    assert (urlmatch.normalize('https://acme.com/jobs/1#apply')
            == urlmatch.normalize('https://acme.com/jobs/1'))


def test_tracking_parameters_are_dropped():
    assert (urlmatch.normalize('https://acme.com/jobs?utm_source=x&gh_src=abc')
            == urlmatch.normalize('https://acme.com/jobs'))


def test_the_greenhouse_job_id_is_kept():
    """gh_src is a campaign tag; gh_jid identifies the posting. On an embedded
    board it is the only thing separating two openings."""
    one = urlmatch.normalize('https://acme.com/careers?gh_jid=1')
    two = urlmatch.normalize('https://acme.com/careers?gh_jid=2')
    assert one != two
    assert 'gh_jid=1' in one


def test_query_parameter_order_does_not_matter():
    assert (urlmatch.normalize('https://acme.com/j?b=2&a=1')
            == urlmatch.normalize('https://acme.com/j?a=1&b=2'))


def test_a_schemeless_url_still_parses():
    assert urlmatch.normalize('acme.com/jobs/1') == 'acme.com/jobs/1'


def test_junk_normalizes_to_empty():
    for value in ('', '   ', None, 'not a url', 123):
        assert urlmatch.normalize(value) == ''


def test_empty_never_matches_itself():
    """Two unparseable URLs are not the same posting."""
    assert not urlmatch.same_posting('', '')
    assert not urlmatch.same_posting('nonsense', 'nonsense else')


# --------------------------------------------------------------------------
# best_match
# --------------------------------------------------------------------------

CANDIDATES = [
    {'id': 'a', 'url': 'https://boards.greenhouse.io/acme/jobs/1'},
    {'id': 'b', 'url': 'https://jobs.lever.co/other/xyz'},
]


def test_an_exact_match_wins():
    match = urlmatch.best_match('https://boards.greenhouse.io/acme/jobs/1?utm_source=x',
                                CANDIDATES)
    assert match['id'] == 'a'


def test_no_match_returns_none():
    assert urlmatch.best_match('https://example.com/nothing', CANDIDATES) is None


def test_a_different_query_still_matches_on_path():
    """The weaker rule: same host and path, different query."""
    match = urlmatch.best_match('https://boards.greenhouse.io/acme/jobs/1?foo=1',
                                CANDIDATES)
    assert match['id'] == 'a'


def test_two_candidates_sharing_a_url_match_nothing():
    """Two applications to the same posting is exactly where a guess corrupts
    the record, so it declines rather than picking one."""
    duplicated = [
        {'id': 'a', 'url': 'https://acme.com/jobs/1'},
        {'id': 'b', 'url': 'https://acme.com/jobs/1'},
    ]
    assert urlmatch.best_match('https://acme.com/jobs/1', duplicated) is None


def test_an_exact_match_is_not_beaten_by_a_loose_one():
    candidates = [
        {'id': 'loose', 'url': 'https://acme.com/careers?gh_jid=999'},
        {'id': 'exact', 'url': 'https://acme.com/careers?gh_jid=1'},
    ]
    match = urlmatch.best_match('https://acme.com/careers?gh_jid=1', candidates)
    assert match['id'] == 'exact'


def test_two_postings_on_one_embedded_board_do_not_collide_loosely():
    """Both share host+path and differ only by gh_jid, so the loose rule finds
    two and must decline rather than guess."""
    candidates = [
        {'id': 'a', 'url': 'https://acme.com/careers?gh_jid=1'},
        {'id': 'b', 'url': 'https://acme.com/careers?gh_jid=2'},
    ]
    assert urlmatch.best_match('https://acme.com/careers?gh_jid=3', candidates) is None


def test_an_empty_target_matches_nothing():
    assert urlmatch.best_match('', CANDIDATES) is None


def test_candidates_without_urls_are_ignored():
    assert urlmatch.best_match('https://acme.com/j', [{'id': 'x', 'url': ''}]) is None


# --------------------------------------------------------------------------
# GET /applications/for-url — what the extension asks when a tab opens
# --------------------------------------------------------------------------

def make_application(client, url, company='Acme'):
    # A description is supplied so the route does not try to fetch `url`.
    job_id = client.post('/api/jobs', json={
        'title': 'Engineer', 'company': company, 'url': url,
        'description': 'We need Python.',
    }).get_json()['id']
    return client.post('/api/jobs/applications', json={'jobId': job_id}).get_json()['id']


def test_a_tab_url_finds_its_application(client):
    application_id = make_application(client, 'https://boards.greenhouse.io/acme/jobs/1')

    found = client.get('/api/jobs/applications/for-url',
                       query_string={'url': 'https://boards.greenhouse.io/acme/jobs/1'})
    body = found.get_json()['application']
    assert body['id'] == application_id
    assert body['company'] == 'Acme'


def test_tracking_parameters_do_not_stop_the_match(client):
    """The link you click carries campaign junk the stored posting never had."""
    application_id = make_application(client, 'https://boards.greenhouse.io/acme/jobs/1')

    found = client.get('/api/jobs/applications/for-url', query_string={
        'url': 'https://boards.greenhouse.io/acme/jobs/1?utm_source=newsletter',
    })
    assert found.get_json()['application']['id'] == application_id


def test_an_unrelated_page_matches_nothing(client):
    make_application(client, 'https://boards.greenhouse.io/acme/jobs/1')

    found = client.get('/api/jobs/applications/for-url',
                       query_string={'url': 'https://news.example.com/article'})
    assert found.get_json()['application'] is None


def test_a_missing_url_is_answered_with_null_rather_than_an_error(client):
    """The extension asks on every tab; a 400 would be noise in its console."""
    found = client.get('/api/jobs/applications/for-url')
    assert found.status_code == 200
    assert found.get_json()['application'] is None


def test_the_route_is_not_shadowed_by_the_application_detail_route(client):
    """`/applications/for-url` and `/applications/<id>` overlap; Werkzeug
    matches the static rule first, and this fails loudly if that changes."""
    found = client.get('/api/jobs/applications/for-url', query_string={'url': ''})
    assert found.status_code == 200
    assert 'application' in found.get_json()
