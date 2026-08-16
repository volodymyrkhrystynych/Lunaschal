"""The source adapters: slug safety, normalization, and graceful absence.

No network anywhere here — `get_json` is stubbed with recorded payload shapes.
What is being tested is the translation from each board's idiosyncratic JSON
into the one shape `sync.py` consumes, plus the two failure modes that matter:
a hostile slug, and a source that simply is not configured.
"""
import pytest

from backend.jobs import sources
from backend.jobs.sources import adzuna, ashby, base, greenhouse, lever


# --------------------------------------------------------------------------
# Slug validation — the one security-relevant line in the module
# --------------------------------------------------------------------------

def test_a_normal_slug_passes():
    assert base.clean_slug('acme-corp') == 'acme-corp'
    assert base.clean_slug('  acme_corp2 ') == 'acme_corp2'


@pytest.mark.parametrize('hostile', [
    '../../v1/boards',          # walks back up the API path
    'acme/jobs',                # adds a path segment
    'evil.com',                 # dots can start a host
    'acme@evil.com',            # turns the rest of the URL into userinfo
    'acme?x=1',                 # injects a query parameter
    'acme#frag',
    'acme corp',
    '',
    None,
])
def test_a_hostile_slug_is_refused(hostile):
    with pytest.raises(sources.SourceError):
        base.clean_slug(hostile)


def test_an_unknown_source_kind_raises():
    with pytest.raises(sources.SourceError):
        sources.fetch('monster', {})


# --------------------------------------------------------------------------
# Adzuna
# --------------------------------------------------------------------------

def test_adzuna_without_credentials_is_not_an_error():
    """Three of four sources work without keys; one unconfigured source must
    not take the feed down with it."""
    result = adzuna.fetch({'what': 'python'}, creds={})
    assert result.jobs == []
    assert 'Settings' in result.message


def test_adzuna_rejects_a_country_it_does_not_serve():
    with pytest.raises(sources.SourceError):
        adzuna.fetch({'country': 'zz'}, creds={'app_id': 'x', 'app_key': 'y'})


def test_adzuna_normalizes_a_result(monkeypatch):
    payload = {'results': [{
        'id': 12345,
        'title': 'Senior Python Engineer',
        'company': {'display_name': 'Acme Inc'},
        'location': {'display_name': 'Toronto, ON'},
        'salary_min': 90000, 'salary_max': '120000',
        'description': 'We want Python and Postgres…',
        'redirect_url': 'https://www.adzuna.ca/land/ad/12345',
        'created': '2026-08-01T10:00:00Z',
        'contract_time': 'full_time',
    }]}
    monkeypatch.setattr(adzuna, 'get_json', lambda url, params=None: payload)

    job = adzuna.fetch({'what': 'python'}, creds={'app_id': 'a', 'app_key': 'b'}).jobs[0]
    assert job['sourceId'] == '12345'
    assert job['company'] == 'Acme Inc'
    assert job['salaryMin'] == 90000 and job['salaryMax'] == 120000
    assert job['url'].endswith('/12345')


def test_adzuna_flags_its_description_as_a_snippet(monkeypatch):
    """Adzuna sends a truncated blurb, not the posting. The flag is what stops
    the feed from presenting its coverage number as the same measurement the
    company boards produce."""
    monkeypatch.setattr(adzuna, 'get_json', lambda url, params=None: {
        'results': [{'id': 1, 'title': 'Dev', 'description': 'blurb…'}]
    })
    job = adzuna.fetch({}, creds={'app_id': 'a', 'app_key': 'b'}).jobs[0]
    assert job['descriptionIsSnippet'] is True


def test_adzuna_treats_a_zero_salary_as_absent(monkeypatch):
    """Several boards spell 'not disclosed' as 0, and a card reading $0–$0 is
    worse than a card with no range at all."""
    monkeypatch.setattr(adzuna, 'get_json', lambda url, params=None: {
        'results': [{'id': 1, 'title': 'Dev', 'salary_min': 0, 'salary_max': 0}]
    })
    job = adzuna.fetch({}, creds={'app_id': 'a', 'app_key': 'b'}).jobs[0]
    assert job['salaryMin'] is None and job['salaryMax'] is None


# --------------------------------------------------------------------------
# Greenhouse
# --------------------------------------------------------------------------

def test_greenhouse_normalizes_and_strips_escaped_html(monkeypatch):
    """Greenhouse HTML-escapes the body and sends it as a string, so it needs
    unescaping before stripping or the text is full of &lt;p&gt;."""
    payload = {'jobs': [{
        'id': 4242,
        'title': 'Platform Engineer',
        'location': {'name': 'Remote - Canada'},
        'content': '&lt;p&gt;We use &lt;strong&gt;Kubernetes&lt;/strong&gt; daily.&lt;/p&gt;',
        'absolute_url': 'https://boards.greenhouse.io/acme/jobs/4242',
        'updated_at': '2026-08-10T12:00:00-04:00',
    }]}
    monkeypatch.setattr(greenhouse, 'get_json', lambda url, params=None: payload)

    job = greenhouse.fetch({'slug': 'acme'}).jobs[0]
    assert job['sourceId'] == '4242'
    assert 'Kubernetes' in job['description']
    assert '<' not in job['description'] and '&lt;' not in job['description']
    assert job['remote'] is True
    assert job['company'] == 'acme'


def test_greenhouse_reads_the_real_company_name_not_the_slug(monkeypatch):
    """Checked against a live board: `company_name` is a plain string, and the
    slug is a bad fallback — Ada's board is `ada18`, which is not a name."""
    monkeypatch.setattr(greenhouse, 'get_json', lambda url, params=None: {
        'jobs': [{'id': 1, 'title': 'Dev', 'company_name': 'Ada'}]
    })
    assert greenhouse.fetch({'slug': 'ada18'}).jobs[0]['company'] == 'Ada'


def test_greenhouse_falls_back_to_the_slug_only_when_unnamed(monkeypatch):
    monkeypatch.setattr(greenhouse, 'get_json', lambda url, params=None: {
        'jobs': [{'id': 1, 'title': 'Dev'}]
    })
    assert greenhouse.fetch({'slug': 'acme'}).jobs[0]['company'] == 'acme'


def test_greenhouse_dates_a_posting_by_when_it_went_up(monkeypatch):
    """`updated_at` moves on every typo fix, which would re-float old postings
    to the top of a feed sorted by recency."""
    monkeypatch.setattr(greenhouse, 'get_json', lambda url, params=None: {
        'jobs': [{'id': 1, 'title': 'Dev',
                  'first_published': '2026-06-01T00:00:00Z',
                  'updated_at': '2026-08-15T00:00:00Z'}]
    })
    assert greenhouse.fetch({'slug': 'a'}).jobs[0]['postedAt'] == '2026-06-01T00:00:00Z'


def test_greenhouse_asks_for_the_full_content(monkeypatch):
    """Without ?content=true the body is absent and the keyword report has
    nothing to read."""
    seen = {}

    def fake(url, params=None):
        seen.update(params or {})
        return {'jobs': []}

    monkeypatch.setattr(greenhouse, 'get_json', fake)
    greenhouse.fetch({'slug': 'acme'})
    assert seen.get('content') == 'true'


# --------------------------------------------------------------------------
# Lever
# --------------------------------------------------------------------------

def test_lever_folds_the_requirement_lists_into_the_description(monkeypatch):
    """Lever puts requirements in `lists`, not in the description. Dropping
    them would hand the keyword report a posting with no requirements in it."""
    payload = [{
        'id': 'abc-123',
        'text': 'Backend Engineer',
        'categories': {'location': 'Toronto'},
        'workplaceType': 'remote',
        'descriptionPlain': 'Join our team.',
        'lists': [{'text': 'Requirements',
                   'content': '<li>5 years of Terraform</li><li>Go</li>'}],
        'hostedUrl': 'https://jobs.lever.co/acme/abc-123',
        'createdAt': 1754006400000,
    }]
    monkeypatch.setattr(lever, 'get_json', lambda url, params=None: payload)

    job = lever.fetch({'slug': 'acme'}).jobs[0]
    assert 'Terraform' in job['description']
    assert 'Join our team.' in job['description']
    assert job['remote'] is True
    assert job['postedAt'] == 1754006400000


def test_lever_handles_a_posting_with_no_lists(monkeypatch):
    monkeypatch.setattr(lever, 'get_json', lambda url, params=None: [
        {'id': 'x', 'text': 'Dev', 'descriptionPlain': 'Body.'}
    ])
    assert lever.fetch({'slug': 'acme'}).jobs[0]['description'] == 'Body.'


# --------------------------------------------------------------------------
# Ashby
# --------------------------------------------------------------------------

def test_ashby_reads_the_salary_range(monkeypatch):
    payload = {'jobs': [{
        'id': 'ash-1',
        'title': 'Staff Engineer',
        'location': 'Vancouver, BC',
        'isRemote': False,
        'descriptionPlain': 'Rust and distributed systems.',
        'jobUrl': 'https://jobs.ashbyhq.com/acme/ash-1',
        'publishedAt': '2026-08-05T00:00:00Z',
        'compensation': {'summaryComponents': [
            {'compensationType': 'EquityPercentage', 'minValue': 0.1},
            {'compensationType': 'Salary', 'minValue': 150000,
             'maxValue': 190000, 'currencyCode': 'CAD'},
        ]},
    }]}
    monkeypatch.setattr(ashby, 'get_json', lambda url, params=None: payload)

    job = ashby.fetch({'slug': 'acme'}).jobs[0]
    assert (job['salaryMin'], job['salaryMax']) == (150000, 190000)
    assert job['salaryCurrency'] == 'CAD'
    assert job['remote'] is False


def test_ashby_without_compensation_reports_no_range(monkeypatch):
    monkeypatch.setattr(ashby, 'get_json', lambda url, params=None: {
        'jobs': [{'id': 'a', 'title': 'Dev', 'location': 'Remote'}]
    })
    job = ashby.fetch({'slug': 'acme'}).jobs[0]
    assert job['salaryMin'] is None and job['salaryCurrency'] == ''
    assert job['remote'] is True


# --------------------------------------------------------------------------
# Shared behaviour
# --------------------------------------------------------------------------

def test_a_board_that_returns_the_wrong_shape_yields_nothing(monkeypatch):
    """A 200 carrying HTML instead of the expected JSON object must produce an
    empty list, not an exception that kills the whole sweep."""
    for module, params in ((greenhouse, {'slug': 'a'}), (ashby, {'slug': 'a'})):
        monkeypatch.setattr(module, 'get_json', lambda url, params=None: ['unexpected'])
        assert module.fetch(params).jobs == []

    monkeypatch.setattr(lever, 'get_json', lambda url, params=None: {'not': 'a list'})
    assert lever.fetch({'slug': 'a'}).jobs == []


def test_non_dict_rows_are_skipped(monkeypatch):
    monkeypatch.setattr(greenhouse, 'get_json', lambda url, params=None: {
        'jobs': [None, 'junk', {'id': 1, 'title': 'Real Job'}]
    })
    jobs = greenhouse.fetch({'slug': 'acme'}).jobs
    assert [j['title'] for j in jobs] == ['Real Job']
