"""Careers page → board slug.

The regexes below are written against markup shapes taken from real careers
pages (Cohere and Wealthsimple on Ashby, Ada on Greenhouse). The load-bearing
behaviour is not the matching, though — it is that **a match is not believed
until the board API answers**, so a regex that hits an unrelated URL produces
an honest failure rather than a source that silently never syncs.
"""
import pytest

from backend.jobs import ingest, resolve
from backend.jobs.sources.base import SourceError, SourceResult


def page(body: str, title: str = 'Careers at Acme | Acme Inc') -> str:
    return f'<html><head><title>{title}</title></head><body>{body}</body></html>'


@pytest.fixture
def board_answers(monkeypatch):
    """Every slug is real and has 3 postings, unless a test says otherwise."""
    def fake(kind, params, creds=None):
        return SourceResult(jobs=[{'sourceId': f'{kind}-{i}'} for i in range(3)])

    monkeypatch.setattr(resolve, 'fetch_source', fake)


def stub_page(monkeypatch, html: str, final_url: str = 'https://acme.com/careers'):
    monkeypatch.setattr(ingest, 'fetch_html', lambda url: (html, final_url))


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

def test_ashby_link_is_found():
    html = page('<a href="https://jobs.ashbyhq.com/cohere">View open positions</a>')
    assert resolve.find_candidates(html) == [('ashby', 'cohere')]


def test_greenhouse_new_and_legacy_hosts_both_match():
    """Real pages use `job-boards.greenhouse.io`; older ones use `boards.`."""
    new = page('<a href="https://job-boards.greenhouse.io/ada18">Jobs</a>')
    old = page('<a href="https://boards.greenhouse.io/acme">Jobs</a>')
    assert resolve.find_candidates(new) == [('greenhouse', 'ada18')]
    assert resolve.find_candidates(old) == [('greenhouse', 'acme')]


def test_the_greenhouse_embed_carries_its_slug_in_a_query_parameter():
    """Without the `for=` pattern this resolves to the literal path 'embed'."""
    html = page(
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmecorp">'
        '</script>'
    )
    assert resolve.find_candidates(html) == [('greenhouse', 'acmecorp')]


def test_a_path_segment_that_is_not_a_slug_is_skipped():
    html = page('<a href="https://boards.greenhouse.io/embed">x</a>')
    assert resolve.find_candidates(html) == []


def test_lever_link_is_found():
    html = page('<a href="https://jobs.lever.co/acme/some-role-id">Apply</a>')
    assert resolve.find_candidates(html) == [('lever', 'acme')]


def test_the_final_url_is_scanned_too():
    """A careers page that just redirects to the board has no useful body."""
    assert resolve.find_candidates(
        page('<p>Redirecting…</p>'), 'https://jobs.ashbyhq.com/wealthsimple'
    ) == [('ashby', 'wealthsimple')]


def test_duplicate_links_collapse_to_one_candidate():
    html = page(
        '<a href="https://jobs.ashbyhq.com/cohere">Jobs</a>'
        '<a href="https://jobs.ashbyhq.com/cohere">Openings</a>'
    )
    assert resolve.find_candidates(html) == [('ashby', 'cohere')]


def test_two_different_boards_are_both_offered():
    html = page(
        '<a href="https://boards.greenhouse.io/acme">A</a>'
        '<a href="https://jobs.lever.co/other">B</a>'
    )
    assert set(resolve.find_candidates(html)) == {
        ('greenhouse', 'acme'), ('lever', 'other')
    }


@pytest.mark.parametrize('markup,expected', [
    ('<a href="https://acme.wd3.myworkdayjobs.com/careers">Jobs</a>', 'Workday'),
    ('<a href="https://acme.bamboohr.com/careers">Jobs</a>', 'BambooHR'),
    ('<a href="https://apply.workable.com/acme/">Jobs</a>', 'Workable'),
    ('<a href="https://jobs.smartrecruiters.com/Acme">Jobs</a>', 'SmartRecruiters'),
    ('<a href="https://acme.recruitee.com/">Jobs</a>', 'Recruitee'),
    ('<a href="https://acme.breezy.hr/">Jobs</a>', 'Breezy'),
    ('<a href="https://careers-acme.icims.com/jobs">Jobs</a>', 'iCIMS'),
])
def test_an_unsupported_ats_is_named_rather_than_ignored(markup, expected):
    assert resolve.find_unsupported(page(markup)) == expected


def test_a_page_with_no_board_finds_nothing():
    assert resolve.find_candidates(page('<p>Email us at jobs@acme.com</p>')) == []
    assert resolve.find_unsupported(page('<p>Email us</p>')) == ''


# --------------------------------------------------------------------------
# Verification — the part that makes detection safe
# --------------------------------------------------------------------------

def test_a_verified_slug_is_returned_with_its_job_count(monkeypatch, board_answers):
    stub_page(monkeypatch, page('<a href="https://jobs.ashbyhq.com/cohere">Jobs</a>'))
    result = resolve.resolve_careers_page('https://cohere.com/careers')

    assert (result.kind, result.slug, result.job_count) == ('ashby', 'cohere', 3)
    assert result.error == ''


def test_a_slug_the_board_rejects_is_not_believed(monkeypatch):
    """A regex can match a URL that merely looks like a board. The board API
    is the arbiter, so a bad guess fails loudly instead of becoming a source
    that silently never syncs."""
    def refuse(kind, params, creds=None):
        raise SourceError('Not found — check the board slug.')

    monkeypatch.setattr(resolve, 'fetch_source', refuse)
    stub_page(monkeypatch, page('<a href="https://jobs.lever.co/blog">Jobs</a>'))

    result = resolve.resolve_careers_page('https://acme.com/careers')

    assert result.kind is None
    assert 'none answered' in result.error


def test_the_first_candidate_that_answers_wins(monkeypatch):
    def selective(kind, params, creds=None):
        if kind == 'greenhouse':
            raise SourceError('Not found')
        return SourceResult(jobs=[{'sourceId': '1'}])

    monkeypatch.setattr(resolve, 'fetch_source', selective)
    stub_page(monkeypatch, page(
        '<a href="https://boards.greenhouse.io/wrong">A</a>'
        '<a href="https://jobs.lever.co/right">B</a>'
    ))

    result = resolve.resolve_careers_page('https://acme.com/careers')

    assert (result.kind, result.slug) == ('lever', 'right')


def test_a_board_with_no_postings_is_still_a_valid_board(monkeypatch):
    """Companies pause hiring; an empty board is not a wrong slug."""
    monkeypatch.setattr(resolve, 'fetch_source',
                        lambda kind, params, creds=None: SourceResult(jobs=[]))
    stub_page(monkeypatch, page('<a href="https://jobs.ashbyhq.com/quiet">Jobs</a>'))

    result = resolve.resolve_careers_page('https://acme.com/careers')

    assert result.kind == 'ashby'
    assert result.job_count == 0
    assert result.error == ''


def test_an_unsupported_ats_reports_what_it_is(monkeypatch, board_answers):
    stub_page(monkeypatch, page(
        '<a href="https://acme.wd3.myworkdayjobs.com/careers">Jobs</a>'
    ))
    result = resolve.resolve_careers_page('https://acme.com/careers')

    assert result.kind is None
    assert result.detected == 'Workday'
    assert 'Workday' in result.error


def test_an_unreachable_page_is_a_result_not_an_exception(monkeypatch):
    """Cloudflare walls are common on careers pages; the user can act on the
    message, so it must reach them rather than becoming a 500."""
    def blocked(url):
        raise ingest.FetchFailed('The page returned HTTP 403.')

    monkeypatch.setattr(ingest, 'fetch_html', blocked)
    result = resolve.resolve_careers_page('https://acme.com/careers')

    assert result.kind is None
    assert '403' in result.error


def test_an_unsafe_url_is_refused_without_fetching(monkeypatch):
    def unsafe(url):
        raise ingest.UnsafeUrl('resolves to a private address')

    monkeypatch.setattr(ingest, 'fetch_html', unsafe)
    result = resolve.resolve_careers_page('http://192.168.1.1/careers')

    assert result.kind is None
    assert 'not reachable' in result.error


# --------------------------------------------------------------------------
# Company name
# --------------------------------------------------------------------------

@pytest.mark.parametrize('title,expected', [
    ('Careers at Cohere | Cohere', 'Cohere'),
    ('Wealthsimple — Careers', 'Wealthsimple'),
    ('Jobs', ''),
    ('Open Positions | Ada Support Inc', 'Ada Support Inc'),
])
def test_the_company_name_is_dug_out_of_the_title(title, expected):
    assert resolve._company_name(page('<p>x</p>', title=title)) == expected


def test_the_slug_is_used_when_the_title_gives_nothing(monkeypatch, board_answers):
    stub_page(monkeypatch, page(
        '<a href="https://jobs.ashbyhq.com/cohere">Jobs</a>', title='Careers'
    ))
    assert resolve.resolve_careers_page('https://cohere.com/careers').company == 'cohere'


# --------------------------------------------------------------------------
# Through HTTP
# --------------------------------------------------------------------------

def test_the_route_resolves_and_creates_nothing(client, monkeypatch, board_answers):
    """Seeing the result before committing to it is the point."""
    stub_page(monkeypatch, page('<a href="https://jobs.ashbyhq.com/cohere">Jobs</a>'))

    body = client.post('/api/jobs/searches/resolve',
                       json={'url': 'https://cohere.com/careers'}).get_json()

    assert body['kind'] == 'ashby'
    assert body['slug'] == 'cohere'
    assert body['jobCount'] == 3
    assert client.get('/api/jobs/searches').get_json() == []


def test_the_route_accepts_a_bare_hostname(client, monkeypatch, board_answers):
    seen = {}

    def capture(url):
        seen['url'] = url
        return page('<a href="https://jobs.ashbyhq.com/cohere">Jobs</a>'), url

    monkeypatch.setattr(ingest, 'fetch_html', capture)
    client.post('/api/jobs/searches/resolve', json={'url': 'cohere.com/careers'})

    assert seen['url'] == 'https://cohere.com/careers'


def test_the_route_needs_a_url(client):
    assert client.post('/api/jobs/searches/resolve', json={}).status_code == 400


# --------------------------------------------------------------------------
# scope_filters: the office/location scope a careers URL already carries
# --------------------------------------------------------------------------

def test_a_greenhouse_embed_url_yields_its_office():
    assert resolve.scope_filters(
        'greenhouse',
        'https://job-boards.greenhouse.io/embed/job_board?for=stripe&offices%5B%5D=87006'
    ) == {'offices': ['87006']}


def test_several_offices_are_all_kept():
    """Take-Two registers two, and dropping either would hide a real office."""
    assert resolve.scope_filters(
        'greenhouse',
        'https://job-boards.greenhouse.io/taketwo?offices%5B%5D=65538&offices%5B%5D=73331'
    ) == {'offices': ['65538', '73331']}


def test_a_lever_url_yields_its_location():
    assert resolve.scope_filters(
        'lever', 'https://jobs.lever.co/hashtagpaid/?location=Toronto'
    ) == {'locations': ['Toronto']}


def test_a_url_with_no_scope_yields_nothing():
    """The common case — 96 of the registered boards are bare slugs."""
    assert resolve.scope_filters(
        'greenhouse', 'https://job-boards.greenhouse.io/ada18') == {}


def test_a_department_filter_is_not_treated_as_a_location():
    """Clutch scopes by team. That is a different intent and is not applied."""
    assert resolve.scope_filters(
        'greenhouse',
        'https://job-boards.greenhouse.io/clutch/?departments%5B%5D=4004505004'
    ) == {}


def test_ashby_is_declined_because_its_filter_cannot_be_honoured():
    """`?locationId=<uuid>` names an id the posting API never returns.

    Storing it would look configured and filter nothing, which is worse than
    an honestly unscoped board.
    """
    assert resolve.scope_filters(
        'ashby', 'https://jobs.ashbyhq.com/alan?locationId=669a4aa2-35f5-4e13'
    ) == {}
