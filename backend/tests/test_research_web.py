"""Web search and fetch for the research agent.

The SSRF tests carry most of the weight here: the model picks these URLs, the
app binds 0.0.0.0 in network mode, and llama-server is on localhost:8080.
"""
import pytest
import requests

from backend.db.connection import get_db
from backend.research import web


@pytest.fixture(autouse=True)
def _reset_search_pacing(monkeypatch):
    """Every test gets a clean pacing/breaker state and never really sleeps.

    Without this, tests exercising _dispatch (added alongside the pacing/retry/
    circuit-breaker logic) would incur real SEARCH_STAGGER_SECONDS/backoff
    sleeps once run back-to-back, and breaker state would leak between tests.
    """
    web._search_state.update({'last_request': 0.0, 'consecutive_failures': 0, 'breaker_until': 0.0})
    monkeypatch.setattr(web, '_sleep', lambda seconds: None)


# --- The SSRF guard ---

@pytest.mark.parametrize('url', [
    'http://127.0.0.1/admin',
    'http://127.0.0.1:8080/v1/models',      # llama-server itself
    'https://localhost/secret',
    'http://[::1]:5000/api/settings',
    'http://10.0.0.5/',
    'http://192.168.1.1/',
    'http://172.16.0.1/',
    'http://169.254.169.254/latest/meta-data/',  # cloud metadata
    'http://0.0.0.0/',
    'http://nas.local/',
    'http://wiki.internal/',
])
def test_private_and_loopback_addresses_are_refused(url):
    with pytest.raises(web.UnsafeUrl):
        web.assert_public_url(url)


@pytest.mark.parametrize('url', [
    'file:///etc/passwd',
    'ftp://example.com/x',
    'gopher://example.com/',
    'not-a-url',
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(web.UnsafeUrl):
        web.assert_public_url(url)


def test_a_public_hostname_is_allowed(monkeypatch):
    monkeypatch.setattr(
        web.socket, 'getaddrinfo',
        lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))],
    )
    assert web.assert_public_url('https://example.com/page') == 'https://example.com/page'


def test_a_public_name_resolving_to_a_private_ip_is_refused(monkeypatch):
    """DNS is the interesting attack: the hostname looks fine."""
    monkeypatch.setattr(
        web.socket, 'getaddrinfo',
        lambda *a, **k: [(2, 1, 6, '', ('10.1.2.3', 443))],
    )
    with pytest.raises(web.UnsafeUrl, match='non-public'):
        web.assert_public_url('https://sneaky.example.com/')


def test_one_private_answer_among_several_is_enough_to_refuse(monkeypatch):
    """We don't control which resolved address the socket picks."""
    monkeypatch.setattr(
        web.socket, 'getaddrinfo',
        lambda *a, **k: [
            (2, 1, 6, '', ('93.184.216.34', 443)),
            (2, 1, 6, '', ('127.0.0.1', 443)),
        ],
    )
    with pytest.raises(web.UnsafeUrl):
        web.assert_public_url('https://mixed.example.com/')


def test_an_unresolvable_host_is_refused(monkeypatch):
    import socket as real_socket

    def boom(*a, **k):
        raise real_socket.gaierror('nope')

    monkeypatch.setattr(web.socket, 'getaddrinfo', boom)
    with pytest.raises(web.UnsafeUrl, match='resolve'):
        web.assert_public_url('https://nothing.example.com/')


# --- Fetching ---

class _FakeResponse:
    def __init__(self, *, status=200, headers=None, body=b'', redirect_to=None):
        self.status_code = status
        self.headers = headers or {'Content-Type': 'text/html'}
        self._body = body
        self.encoding = 'utf-8'
        if redirect_to:
            self.status_code = 302
            self.headers['Location'] = redirect_to

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    is_permanent_redirect = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code}')

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        pass


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(
        web.socket, 'getaddrinfo',
        lambda host, *a, **k: [(2, 1, 6, '', (
            '127.0.0.1' if host in ('evil.internal.example', 'private.example') else '93.184.216.34',
            443,
        ))],
    )


def test_fetch_returns_title_and_text(monkeypatch, public_dns):
    html = b'<html><head><title>FSRS</title><script>bad()</script></head>' \
           b'<body><h1>Spaced repetition</h1><p>Cards are scheduled.</p></body></html>'
    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _FakeResponse(body=html))

    page = web.web_fetch('https://example.com/fsrs')
    assert page['title'] == 'FSRS'
    assert 'Spaced repetition' in page['text']
    assert 'bad()' not in page['text'], 'script contents are stripped'


def test_fetch_follows_a_public_redirect(monkeypatch, public_dns):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return _FakeResponse(redirect_to='https://example.com/final')
        return _FakeResponse(body=b'<html><body>arrived</body></html>')

    monkeypatch.setattr(web.requests, 'get', fake_get)
    page = web.web_fetch('https://example.com/start')
    assert page['url'] == 'https://example.com/final'
    assert 'arrived' in page['text']


def test_fetch_refuses_a_redirect_into_a_private_host(monkeypatch, public_dns):
    """A public URL redirecting to the metadata service is the whole trick."""
    monkeypatch.setattr(
        web.requests, 'get',
        lambda *a, **k: _FakeResponse(redirect_to='https://private.example/secret'),
    )
    with pytest.raises(web.UnsafeUrl, match='non-public'):
        web.web_fetch('https://example.com/start')


def test_fetch_gives_up_after_too_many_redirects(monkeypatch, public_dns):
    monkeypatch.setattr(
        web.requests, 'get',
        lambda *a, **k: _FakeResponse(redirect_to='https://example.com/loop'),
    )
    with pytest.raises(web.UnsafeUrl, match='[Tt]oo many redirects'):
        web.web_fetch('https://example.com/loop')


def test_fetch_stops_reading_once_the_byte_cap_is_passed(monkeypatch, public_dns):
    """The point is that we stop pulling from the socket, not just that the
    text is short — an endless response must not be downloaded in full.
    Granularity is one chunk, so it reads at most one chunk past the cap."""
    monkeypatch.setattr(web, 'MAX_BYTES', 1000)
    huge = b'<html><body>' + (b'x' * 5_000_000) + b'</body></html>'
    served = []

    class Counting(_FakeResponse):
        def iter_content(self, size):
            for i in range(0, len(self._body), size):
                chunk = self._body[i:i + size]
                served.append(len(chunk))
                yield chunk

    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: Counting(body=huge))
    page = web.web_fetch('https://example.com/huge')

    assert sum(served) <= 1000 + 8192, 'stopped within one chunk of the cap'
    assert sum(served) < len(huge) / 100, 'nowhere near the full body'
    assert len(page['text']) <= web.MAX_PAGE_CHARS


def test_fetch_caps_the_text_it_hands_the_model(monkeypatch, public_dns):
    """Independent of the byte cap: a page that fits still gets clipped to the
    context budget."""
    monkeypatch.setattr(web, 'MAX_PAGE_CHARS', 200)
    body = b'<html><body>' + (b'word ' * 500) + b'</body></html>'
    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _FakeResponse(body=body))
    assert len(web.web_fetch('https://example.com/p')['text']) == 200


def test_fetch_refuses_non_text_content(monkeypatch, public_dns):
    monkeypatch.setattr(
        web.requests, 'get',
        lambda *a, **k: _FakeResponse(headers={'Content-Type': 'application/pdf'}),
    )
    with pytest.raises(web.UnsafeUrl, match='text pages'):
        web.web_fetch('https://example.com/doc.pdf')


# --- Providers ---

def _configure(provider, key=None, url=None):
    db = get_db()
    db.execute(
        'UPDATE settings SET research_search_provider=?, research_search_key=?,'
        ' research_searxng_url=?',
        (provider, key, url),
    )
    db.commit()


def test_no_provider_means_unavailable(client):
    assert web.is_search_configured() is False
    with pytest.raises(web.SearchUnavailable):
        web.web_search('fsrs')


def test_a_provider_without_its_key_is_not_configured(client):
    _configure('brave', key=None)
    assert web.is_search_configured() is False
    with pytest.raises(web.SearchUnavailable, match='API key'):
        web.web_search('fsrs')


def test_brave_results_are_mapped(client, monkeypatch):
    _configure('brave', key='k')
    assert web.is_search_configured() is True

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {'web': {'results': [
                {'title': 'FSRS', 'url': 'https://ex.com/a', 'description': 'sched'},
                {'title': 'No URL', 'description': 'dropped'},
            ]}}

    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: R())
    results = web.web_search('fsrs')
    assert results == [{'title': 'FSRS', 'url': 'https://ex.com/a', 'snippet': 'sched'}]


def test_searxng_needs_only_a_url(client, monkeypatch):
    _configure('searxng', url='http://localhost:8888')
    assert web.is_search_configured() is True

    seen = {}

    class R:
        def raise_for_status(self): pass
        def json(self):
            return {'results': [{'title': 'S', 'url': 'https://ex.com/s', 'content': 'c'}]}

    def fake_get(url, **kwargs):
        seen['url'] = url
        return R()

    monkeypatch.setattr(web.requests, 'get', fake_get)
    assert web.web_search('fsrs')[0]['title'] == 'S'
    assert seen['url'] == 'http://localhost:8888/search'


def test_search_network_errors_become_search_unavailable(client, monkeypatch):
    _configure('brave', key='k')

    def boom(*a, **k):
        raise requests.ConnectionError('down')

    monkeypatch.setattr(web.requests, 'get', boom)
    with pytest.raises(web.SearchUnavailable):
        web.web_search('fsrs')


def test_an_empty_query_searches_nothing(client):
    assert web.web_search('   ') == []


# --- Pacing, retry, circuit breaker ---

class _SearxResponse:
    """A fake SearXNG JSON response, success or error."""

    def __init__(self, *, status=200, results=None, unresponsive=None):
        self.status_code = status
        self._results = results if results is not None else []
        self._unresponsive = unresponsive or []

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(str(self.status_code))
            err.response = self
            raise err

    def json(self):
        return {'results': self._results, 'unresponsive_engines': self._unresponsive}


_OK_RESULT = [{'title': 'S', 'url': 'https://ex.com/s', 'content': 'c'}]


def test_stagger_delays_a_second_immediate_call(client, monkeypatch):
    _configure('searxng', url='http://localhost:8888')
    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _SearxResponse(results=_OK_RESULT))
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    web.web_search('q')  # first call: nothing to stagger against yet
    web.web_search('q')  # second call: paced against the first

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(web.SEARCH_STAGGER_SECONDS, abs=0.5)


def test_429_then_success_retries_with_backoff(client, monkeypatch):
    _configure('searxng', url='http://localhost:8888')
    responses = [_SearxResponse(status=429), _SearxResponse(status=429), _SearxResponse(results=_OK_RESULT)]
    seen = {'n': 0}

    def fake_get(*a, **k):
        r = responses[seen['n']]
        seen['n'] += 1
        return r

    monkeypatch.setattr(web.requests, 'get', fake_get)
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    results = web.web_search('q')

    assert results[0]['title'] == 'S'
    assert seen['n'] == 3
    assert sleeps == list(web.SEARCH_RETRY_BACKOFF[:2])


def test_429_exhausts_all_backoff_then_fails(client, monkeypatch):
    _configure('searxng', url='http://localhost:8888')
    seen = {'n': 0}

    def fake_get(*a, **k):
        seen['n'] += 1
        return _SearxResponse(status=429)

    monkeypatch.setattr(web.requests, 'get', fake_get)
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    with pytest.raises(web.SearchUnavailable):
        web.web_search('q')

    assert seen['n'] == len(web.SEARCH_RETRY_BACKOFF) + 1
    assert sleeps == list(web.SEARCH_RETRY_BACKOFF)


def test_connection_error_fails_fast_without_backoff(client, monkeypatch):
    _configure('searxng', url='http://localhost:8888')
    seen = {'n': 0}

    def boom(*a, **k):
        seen['n'] += 1
        raise requests.ConnectionError('down')

    monkeypatch.setattr(web.requests, 'get', boom)
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    with pytest.raises(web.SearchUnavailable):
        web.web_search('q')

    assert seen['n'] == 1, 'a hard connection failure is not rate-limit shaped: no retry'
    assert sleeps == []


def test_breaker_trips_after_repeated_failures_without_hitting_network(client, monkeypatch):
    _configure('searxng', url='http://localhost:8888')
    seen = {'n': 0}

    def fake_get(*a, **k):
        seen['n'] += 1
        return _SearxResponse(status=429)

    monkeypatch.setattr(web.requests, 'get', fake_get)
    monkeypatch.setattr(web, '_sleep', lambda s: None)

    for _ in range(web.SEARCH_BREAKER_THRESHOLD):
        with pytest.raises(web.SearchUnavailable):
            web.web_search('q')

    calls_before = seen['n']
    with pytest.raises(web.SearchCircuitOpen):
        web.web_search('q')
    assert seen['n'] == calls_before, 'a tripped breaker must not make a network call'


def test_breaker_resets_after_its_cooldown(client, monkeypatch):
    clock = {'t': 1000.0}
    monkeypatch.setattr(web, '_monotonic', lambda: clock['t'])
    monkeypatch.setattr(web, '_sleep', lambda s: None)
    _configure('searxng', url='http://localhost:8888')

    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _SearxResponse(status=429))
    for _ in range(web.SEARCH_BREAKER_THRESHOLD):
        with pytest.raises(web.SearchUnavailable):
            web.web_search('q')
    with pytest.raises(web.SearchCircuitOpen):
        web.web_search('q')

    clock['t'] += web.SEARCH_BREAKER_COOLDOWN + 1
    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _SearxResponse(results=_OK_RESULT))

    assert web.web_search('q')[0]['title'] == 'S'


def test_a_success_resets_the_failure_counter(client, monkeypatch):
    """One failure below threshold, then a success, must not leave a stale
    count that trips the breaker on a later unrelated single failure."""
    _configure('searxng', url='http://localhost:8888')
    monkeypatch.setattr(web, '_sleep', lambda s: None)

    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _SearxResponse(status=429))
    with pytest.raises(web.SearchUnavailable):
        web.web_search('q')

    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _SearxResponse(results=_OK_RESULT))
    web.web_search('q')
    assert web._search_state['consecutive_failures'] == 0

    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: _SearxResponse(status=429))
    with pytest.raises(web.SearchUnavailable):
        web.web_search('q')
    assert web._search_state['breaker_until'] == 0.0, 'a single failure alone must not trip it'


def test_searxng_soft_block_triggers_a_retry_that_recovers(client, monkeypatch):
    _configure('searxng', url='http://localhost:8888')
    responses = [
        _SearxResponse(results=[], unresponsive=[['startpage', 'CAPTCHA'], ['duckduckgo', 'too many requests']]),
        _SearxResponse(results=_OK_RESULT),
    ]
    seen = {'n': 0}

    def fake_get(*a, **k):
        r = responses[seen['n']]
        seen['n'] += 1
        return r

    monkeypatch.setattr(web.requests, 'get', fake_get)
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    results = web.web_search('q')

    assert results[0]['title'] == 'S'
    assert sleeps == [web.SEARCH_RETRY_BACKOFF[0]]


def test_a_suspended_engine_is_not_retried_and_opens_the_breaker(client, monkeypatch):
    """SearXNG's real answer when Brave/Startpage bench us — see searxng/settings.yml.

    While an engine is suspended SearXNG makes no request to it, so the three
    backoff sleeps would buy 50 seconds of the same empty payload.
    """
    _configure('searxng', url='http://localhost:8888')
    calls = {'n': 0}

    def fake_get(*a, **k):
        calls['n'] += 1
        return _SearxResponse(
            results=[],
            unresponsive=[
                ['brave', 'Suspended: too many requests'],
                ['startpage', 'Suspended: CAPTCHA'],
            ],
        )

    monkeypatch.setattr(web.requests, 'get', fake_get)
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    with pytest.raises(web.SearchUnavailable):
        web.web_search('q')

    assert calls['n'] == 1, 'a suspension must not be retried'
    assert sleeps == []
    assert web._search_state['breaker_until'] > 0.0, 'one suspension is enough to open it'

    with pytest.raises(web.SearchCircuitOpen):
        web.web_search('q')
    assert calls['n'] == 1, 'the breaker short-circuits without a network call'


def test_a_fresh_captcha_alongside_a_suspension_still_retries(client, monkeypatch):
    """DuckDuckGo's CAPTCHA carries suspended_time=0, so the engine is still asked."""
    _configure('searxng', url='http://localhost:8888')
    responses = [
        _SearxResponse(
            results=[],
            unresponsive=[['brave', 'Suspended: too many requests'], ['duckduckgo', 'CAPTCHA']],
        ),
        _SearxResponse(results=_OK_RESULT),
    ]
    seen = {'n': 0}

    def fake_get(*a, **k):
        r = responses[seen['n']]
        seen['n'] += 1
        return r

    monkeypatch.setattr(web.requests, 'get', fake_get)
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    assert web.web_search('q')[0]['title'] == 'S'
    assert sleeps == [web.SEARCH_RETRY_BACKOFF[0]]


def test_a_suspension_beside_a_timeout_still_retries(client, monkeypatch):
    """The timeout might not repeat, so the retry has something to win."""
    _configure('searxng', url='http://localhost:8888')
    responses = [
        _SearxResponse(
            results=[],
            unresponsive=[['startpage', 'Suspended: CAPTCHA'], ['bing', 'timeout']],
        ),
        _SearxResponse(results=_OK_RESULT),
    ]
    seen = {'n': 0}

    def fake_get(*a, **k):
        r = responses[seen['n']]
        seen['n'] += 1
        return r

    monkeypatch.setattr(web.requests, 'get', fake_get)
    monkeypatch.setattr(web, '_sleep', lambda s: None)

    assert web.web_search('q')[0]['title'] == 'S'


def test_benign_unresponsive_engines_still_return_empty_without_retry(client, monkeypatch, caplog):
    _configure('searxng', url='http://localhost:8888')
    monkeypatch.setattr(
        web.requests, 'get',
        lambda *a, **k: _SearxResponse(results=[], unresponsive=[['startpage', 'timeout']]),
    )
    sleeps = []
    monkeypatch.setattr(web, '_sleep', lambda s: sleeps.append(s))

    with caplog.at_level('INFO', logger='backend.research.web'):
        results = web.web_search('q')

    assert results == []
    assert sleeps == []
    assert any('unresponsive' in r.message.lower() for r in caplog.records)


def test_run_tool_wording_distinguishes_circuit_open_from_plain_unavailable(client, monkeypatch):
    def _raise(q, limit=5):
        raise web.SearchCircuitOpen('searxng search failed repeatedly and will not be retried for about 42s')

    monkeypatch.setattr(web, 'web_search', _raise)
    text, event = web.run_tool('web_search', {'query': 'fsrs'})

    assert 'do not call web_search again' in text.lower()
    assert 'could not search' not in text.lower()
    assert event['ok'] is False
    assert event['circuit_open'] is True


# --- run_tool: the model-facing surface ---

def test_run_tool_never_raises_on_an_unconfigured_search(client):
    """A failed tool is information the model can act on; an exception would
    abandon a run that is otherwise fine."""
    text, event = web.run_tool('web_search', {'query': 'fsrs'})
    assert 'unavailable' in text.lower()
    assert 'could not search' in text.lower()
    assert event['ok'] is False


def test_run_tool_formats_search_results(client, monkeypatch):
    monkeypatch.setattr(web, 'web_search', lambda q, limit=5: [
        {'title': 'FSRS', 'url': 'https://ex.com/a', 'snippet': 'sched'},
    ])
    text, event = web.run_tool('web_search', {'query': 'fsrs'})
    assert 'https://ex.com/a' in text
    assert event == {
        'tool': 'web_search', 'arg': 'fsrs', 'ok': True, 'count': 1,
        'results': [{'title': 'FSRS', 'url': 'https://ex.com/a'}],
    }


def test_search_results_carry_the_reminder_to_open_one(client, monkeypatch):
    """The nudge rides on the results, not the system prompt.

    Gemma 4 searched, searched again and wrote from snippets when the only
    instruction to read a page was back in GATHER_SYSTEM; it fetches when the
    results and the instruction arrive together. Measured — see
    docs/ideas-tab.md#the-first-live-run.
    """
    monkeypatch.setattr(web, 'web_search', lambda q, limit=5: [
        {'title': 'FSRS', 'url': 'https://ex.com/a', 'snippet': 'sched'},
    ])
    text, _ = web.run_tool('web_search', {'query': 'fsrs'})
    assert 'web_fetch' in text
    assert text.startswith('1. FSRS'), 'the results still come first'


def test_an_empty_result_set_does_not_tell_the_model_to_open_one(client, monkeypatch):
    monkeypatch.setattr(web, 'web_search', lambda q, limit=5: [])
    text, event = web.run_tool('web_search', {'query': 'fsrs'})
    assert 'web_fetch' not in text
    assert event['count'] == 0


def test_run_tool_reports_a_refused_url_without_raising(client):
    text, event = web.run_tool('web_fetch', {'url': 'http://127.0.0.1/admin'})
    assert 'Refused' in text
    assert event['ok'] is False


def test_run_tool_reports_an_unknown_tool(client):
    text, event = web.run_tool('rm_rf', {})
    assert 'Unknown tool' in text
    assert event['ok'] is False


def test_tool_definitions_are_openai_shaped():
    names = {t['function']['name'] for t in web.TOOLS}
    assert names == {'web_search', 'web_fetch'}
    for tool in web.TOOLS:
        assert tool['type'] == 'function'
        assert tool['function']['parameters']['type'] == 'object'
        assert tool['function']['parameters']['required']
