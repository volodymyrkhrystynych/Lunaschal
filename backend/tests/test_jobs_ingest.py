"""Ingesting one user-supplied posting URL.

The network is stubbed at `requests.Session.get`. What is being tested is the
SSRF guard running on every hop, the desktop User-Agent, and the degradations
that keep a fetch useful when the model is unavailable.
"""
import pytest
import requests

from backend.jobs import ingest

HTML = """
<html><head><title>Backend Engineer at Acme</title></head>
<body><h1>Backend Engineer</h1><p>We use Python and Kubernetes.</p></body></html>
"""


class FakeResponse:
    def __init__(self, *, status=200, headers=None, body=b'', location=None):
        self.status_code = status
        self.headers = headers or {'Content-Type': 'text/html'}
        if location:
            self.headers['Location'] = location
        self._body = body
        self.encoding = 'utf-8'
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    is_permanent_redirect = is_redirect

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


@pytest.fixture
def capture(monkeypatch):
    """Record every request and serve queued responses."""
    calls = []
    queue = []

    def fake_get(self, url, **kwargs):
        calls.append({'url': url, 'headers': kwargs.get('headers', {})})
        return queue.pop(0) if queue else FakeResponse(body=HTML.encode())

    monkeypatch.setattr(requests.Session, 'get', fake_get)
    monkeypatch.setattr(ingest, 'assert_public_url', lambda url: url)
    return {'calls': calls, 'queue': queue}


def test_fetches_with_a_desktop_user_agent(capture):
    """A mobile UA gets a mobile page, and a mobile job page is mostly an
    interstitial asking you to install an app."""
    text, title = ingest.fetch_posting('https://acme.com/jobs/1')
    assert 'Python and Kubernetes' in text
    assert title == 'Backend Engineer at Acme'
    assert 'X11; Linux x86_64' in capture['calls'][0]['headers']['User-Agent']
    assert 'Mobile' not in capture['calls'][0]['headers']['User-Agent']


def test_redirects_are_followed_and_revalidated(capture, monkeypatch):
    seen = []

    def guard(url):
        seen.append(url)
        return url

    monkeypatch.setattr(ingest, 'assert_public_url', guard)
    capture['queue'].extend([
        FakeResponse(status=302, location='https://acme.com/jobs/final'),
        FakeResponse(body=HTML.encode()),
    ])

    ingest.fetch_posting('https://acme.com/jobs/1')
    # Both the original and the hop went through the guard — a public URL
    # redirecting to 169.254.169.254 is the whole trick.
    assert seen == ['https://acme.com/jobs/1', 'https://acme.com/jobs/final']


def test_a_private_url_is_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(
        ingest, 'assert_public_url',
        lambda url: (_ for _ in ()).throw(ingest.UnsafeUrl('private address')),
    )
    called = []
    monkeypatch.setattr(
        requests.Session, 'get',
        lambda self, *a, **k: called.append(1) or FakeResponse(),
    )
    with pytest.raises(ingest.UnsafeUrl):
        ingest.fetch_posting('http://127.0.0.1/admin')
    assert called == []


def test_non_text_content_is_refused(capture):
    capture['queue'].append(
        FakeResponse(headers={'Content-Type': 'application/pdf'}, body=b'%PDF')
    )
    with pytest.raises(ingest.FetchFailed, match='not a web page'):
        ingest.fetch_posting('https://acme.com/jobs/1.pdf')


def test_an_error_status_is_reported(capture):
    capture['queue'].append(FakeResponse(status=403, body=b''))
    with pytest.raises(ingest.FetchFailed, match='403'):
        ingest.fetch_posting('https://acme.com/jobs/1')


def test_a_javascript_only_page_says_to_paste_instead(capture):
    capture['queue'].append(FakeResponse(body=b'<html><body></body></html>'))
    with pytest.raises(ingest.FetchFailed, match='Paste the posting text'):
        ingest.fetch_posting('https://acme.com/jobs/1')


def test_a_redirect_loop_terminates(capture):
    for _ in range(ingest.MAX_REDIRECTS + 2):
        capture['queue'].append(
            FakeResponse(status=302, location='https://acme.com/loop')
        )
    with pytest.raises(ingest.FetchFailed, match='Too many redirects'):
        ingest.fetch_posting('https://acme.com/loop')


def test_an_oversized_page_stops_streaming_rather_than_failing(capture, monkeypatch):
    """The cap breaks after the chunk that crosses it, so overshoot is bounded
    by one 8KB read — the same contract as web.web_fetch."""
    monkeypatch.setattr(ingest, 'MAX_BYTES', 64)
    capture['queue'].append(
        FakeResponse(body=b'<html><body>' + b'x' * 500_000 + b'</body></html>')
    )
    text, _ = ingest.fetch_posting('https://acme.com/jobs/1')
    assert 0 < len(text) <= 8192


def test_page_text_is_capped_for_the_model(capture, monkeypatch):
    """MAX_PAGE_CHARS is the bound that actually protects the prompt budget."""
    monkeypatch.setattr(ingest, 'MAX_PAGE_CHARS', 100)
    capture['queue'].append(
        FakeResponse(body=b'<html><body>' + b'word ' * 5000 + b'</body></html>')
    )
    text, _ = ingest.fetch_posting('https://acme.com/jobs/1')
    assert len(text) <= 100


def test_a_network_error_is_a_readable_message(monkeypatch):
    monkeypatch.setattr(ingest, 'assert_public_url', lambda url: url)
    monkeypatch.setattr(
        requests.Session, 'get',
        lambda self, *a, **k: (_ for _ in ()).throw(
            requests.ConnectionError('name resolution failed')
        ),
    )
    with pytest.raises(ingest.FetchFailed, match='Could not reach'):
        ingest.fetch_posting('https://nope.example/jobs/1')


# --- extraction -----------------------------------------------------------

def test_extraction_returns_none_without_a_model(monkeypatch):
    monkeypatch.setattr(ingest, 'is_ai_configured', lambda: False)
    assert ingest.extract_job('some posting text') is None


def test_ingest_keeps_the_page_text_when_the_model_is_off(capture, monkeypatch):
    """Losing the fetch entirely would be worse than filling fields by hand."""
    monkeypatch.setattr(ingest, 'is_ai_configured', lambda: False)
    result = ingest.ingest_url('https://acme.com/jobs/1')
    assert 'Python and Kubernetes' in result['description']
    assert result['title'] == 'Backend Engineer at Acme'
    assert result['url'] == 'https://acme.com/jobs/1'


def test_extraction_falls_back_to_the_page_text_for_an_empty_description(monkeypatch):
    monkeypatch.setattr(ingest, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ingest, 'chat_json', lambda *a, **k: {
        'title': 'Backend Engineer', 'company': 'Acme', 'description': '',
    })
    result = ingest.extract_job('the full posting body')
    assert result['description'] == 'the full posting body'


def test_extraction_survives_a_model_failure(monkeypatch):
    monkeypatch.setattr(ingest, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ingest, 'chat_json', lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('llama-server is down')
    ))
    assert ingest.extract_job('text') is None


def test_extraction_coerces_salary_and_currency(monkeypatch):
    monkeypatch.setattr(ingest, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ingest, 'chat_json', lambda *a, **k: {
        'title': 'T', 'company': 'C', 'description': 'D',
        'salaryMin': 140000, 'salaryMax': 'not a number',
        'salaryCurrency': 'CAD-and-a-very-long-suffix', 'remote': 1,
    })
    result = ingest.extract_job('text')
    assert result['salaryMin'] == 140000.0
    assert result['salaryMax'] is None
    assert len(result['salaryCurrency']) <= 8
    assert result['remote'] is True
