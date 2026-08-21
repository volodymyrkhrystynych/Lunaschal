"""Web search and fetch, exposed to the model as tools.

This is the only part of Lunaschal that makes arbitrary outbound requests, and
the URLs are chosen by an LLM rather than by the user — so the SSRF guard below
is not optional. The app binds 0.0.0.0 in network mode and llama-server sits on
localhost:8080, which is exactly the kind of neighbour an unguarded fetcher
hands to a prompt injection.

Search is pluggable because there is no good local option: Brave needs an API
key, a self-hosted SearXNG needs none. With no provider configured
the tools return an explanatory *result* rather than raising, so the agent
degrades to "answer from the wiki and repo context, and say search was
unavailable" instead of dying mid-loop — the same trust-first stance
backend/ai/learning_verification.py takes about evidence.
"""
import ipaddress
import logging
import socket
import threading
import time
from urllib.parse import urlparse, urlunparse

import requests

from backend.htmltext import strip_html_with_title

logger = logging.getLogger(__name__)

USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) Lunaschal/1.0'
MAX_REDIRECTS = 3
MAX_BYTES = 2_000_000
MAX_PAGE_CHARS = 6000
SEARCH_TIMEOUT = 15
FETCH_TIMEOUT = (5, 15)
DEFAULT_RESULTS = 5

# Pacing/retry/circuit-breaker for the search provider (SearXNG in particular
# fans a query out to several upstream engines, any of which can rate-limit or
# soft-ban us). See backend/research/CLAUDE.md.
SEARCH_STAGGER_SECONDS = 3.0
SEARCH_RETRY_BACKOFF = (5, 15, 30)  # same shape as backend/fanfic/download.py's RETRY_BACKOFF
SEARCH_BREAKER_THRESHOLD = 2  # consecutive exhausted web_search() calls before short-circuiting
SEARCH_BREAKER_COOLDOWN = 300  # seconds a tripped breaker stays open
_SOFT_BLOCK_MARKERS = ('captcha', 'blocked', 'too many request', 'rate limit', 'forbidden', 'access denied')
# SearXNG prefixes a reason with "Suspended:" once it has benched an engine —
# for up to a day (`suspended_time=86400` for a Startpage CAPTCHA). While it is
# suspended SearXNG doesn't contact the engine at all, so a retry inside our
# backoff window is a request that was never going to be sent.
_SUSPENDED_MARKER = 'suspend'

_ALLOWED_SCHEMES = ('http', 'https')
# Hostnames that never resolve to something we want to talk to, checked before
# DNS so a resolver that helpfully returns a public IP can't launder them.
_BLOCKED_SUFFIXES = ('.local', '.internal', '.localhost', '.home.arpa')
_TEXT_TYPES = ('text/html', 'text/plain', 'application/xhtml+xml')


class UnsafeUrl(ValueError):
    """The URL points somewhere we refuse to fetch from."""


class SearchUnavailable(RuntimeError):
    """No search provider is configured, or the provider failed."""


class SearchCircuitOpen(SearchUnavailable):
    """Search failed repeatedly and is being short-circuited without a network call."""


class SearchSoftBlocked(RuntimeError):
    """SearXNG returned no results and its unresponsive_engines looks like a CAPTCHA/ban.

    Raised only inside _search_searxng; _dispatch folds it into the same
    retry/backoff/breaker handling as an HTTP 429 and it never escapes past that.

    `retryable` is False when every engine that failed is already *suspended*:
    waiting 5, 15 and 30 seconds cannot change an answer SearXNG will give
    without making a request. That case trips the breaker immediately instead.
    """

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _is_public(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_public_url(url: str) -> str:
    """Reject anything that isn't a plain public http(s) URL.

    Note the honest limitation: the host is re-resolved when requests connects,
    so a DNS-rebinding attacker has a window between this check and the socket.
    Redirects are followed manually with this re-run on every hop, which closes
    the practical version of that. Full immunity needs a resolver that pins the
    validated address while preserving SNI; not worth it for a single-user app
    on localhost, but worth knowing before this is reused somewhere exposed.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrl(f'Only http(s) URLs are allowed, got {parsed.scheme or "none"}')
    host = parsed.hostname
    if not host:
        raise UnsafeUrl('URL has no host')

    lowered = host.lower()
    if lowered == 'localhost' or lowered.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeUrl(f'Refusing to fetch a local address: {host}')

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except socket.gaierror as e:
        raise UnsafeUrl(f'Could not resolve {host}: {e}') from e

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeUrl(f'Could not resolve {host}')
    # Every resolved address must be public: one private answer is enough to
    # make the connection unsafe, since we don't control which one is used.
    for address in addresses:
        if not _is_public(address):
            raise UnsafeUrl(f'{host} resolves to a non-public address ({address})')
    return url


def _settings() -> dict:
    from backend.ai.provider import get_settings
    return get_settings() or {}


# One search provider for the whole app: the Ideas research agent and the chat
# delegate both come through here. The retired Web Search tab's parallel
# `websearch_*` columns are folded into these once at startup
# (`_migrate_websearch_search_to_research` in backend/db/connection.py) rather
# than being read as a fallback here — a setting that works but appears nowhere
# in Settings is worse than one that was migrated into view.
def _search_setting(settings: dict, name: str) -> str:
    return (settings.get(f'research_{name}') or '').strip()


def search_provider() -> str:
    return _search_setting(_settings(), 'search_provider')


def is_search_configured() -> bool:
    settings = _settings()
    provider = _search_setting(settings, 'search_provider')
    if provider == 'brave':
        return bool(_search_setting(settings, 'search_key'))
    if provider == 'searxng':
        return bool(_search_setting(settings, 'searxng_url'))
    return False


_search_lock = threading.Lock()
_search_state = {'last_request': 0.0, 'consecutive_failures': 0, 'breaker_until': 0.0}


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _dispatch(provider: str, do_request) -> list[dict]:
    """Run one search request, serialized and paced against every other caller.

    Holds _search_lock for the whole call, including backoff sleeps, so a
    concurrent caller (the Ideas agent and the chat delegate's deep-research
    mode both go through here) is staggered rather than racing SearXNG. A
    tripped breaker short-circuits before any network call is made.
    """
    with _search_lock:
        now = _monotonic()
        if _search_state['breaker_until'] > now:
            remaining = round(_search_state['breaker_until'] - now)
            raise SearchCircuitOpen(
                f'{provider} search failed repeatedly and will not be retried for '
                f'about {remaining}s'
            )

        wait = SEARCH_STAGGER_SECONDS - (now - _search_state['last_request'])
        if wait > 0:
            _sleep(wait)

        last_exc: Exception | None = None
        give_up_now = False
        for backoff in (*SEARCH_RETRY_BACKOFF, None):
            _search_state['last_request'] = _monotonic()
            try:
                results = do_request()
            except (requests.HTTPError, SearchSoftBlocked) as e:
                status = getattr(getattr(e, 'response', None), 'status_code', None)
                if isinstance(e, SearchSoftBlocked):
                    retryable = e.retryable
                    # Every engine benched upstream: nothing to wait for, and
                    # the suspension outlives our cooldown, so open the breaker
                    # rather than spending the retries first.
                    give_up_now = not retryable
                else:
                    retryable = status in (429, 503)
                if retryable and backoff is not None:
                    logger.info('%s search rate-limited, retrying in %ss', provider, backoff)
                    _sleep(backoff)
                    last_exc = e
                    continue
                last_exc = e
                break
            except requests.RequestException as e:
                # Connection errors, timeouts, non-retryable 4xxs: not rate-limit
                # shaped, so retrying on a delay wouldn't help.
                last_exc = e
                break
            else:
                _search_state['consecutive_failures'] = 0
                _search_state['breaker_until'] = 0.0
                return results

        _search_state['consecutive_failures'] += 1
        if give_up_now or _search_state['consecutive_failures'] >= SEARCH_BREAKER_THRESHOLD:
            _search_state['breaker_until'] = _monotonic() + SEARCH_BREAKER_COOLDOWN
            _search_state['consecutive_failures'] = 0
        raise SearchUnavailable(f'Search request failed: {last_exc}') from last_exc


def web_search(query: str, limit: int = DEFAULT_RESULTS) -> list[dict]:
    """[{title, url, snippet}] from the configured provider."""
    query = (query or '').strip()
    if not query:
        return []
    settings = _settings()
    provider = _search_setting(settings, 'search_provider')
    key = _search_setting(settings, 'search_key')
    limit = max(1, min(limit, 10))

    if provider == 'brave':
        if not key:
            raise SearchUnavailable('Brave search needs an API key')
        return _dispatch('brave', lambda: _search_brave(query, key, limit))
    if provider == 'searxng':
        url = _search_setting(settings, 'searxng_url')
        if not url:
            raise SearchUnavailable('SearXNG needs a base URL')
        return _dispatch('searxng', lambda: _search_searxng(query, url, limit))

    raise SearchUnavailable('No web-search provider is configured')


def _search_brave(query: str, key: str, limit: int) -> list[dict]:
    resp = requests.get(
        'https://api.search.brave.com/res/v1/web/search',
        params={'q': query, 'count': limit},
        headers={'Accept': 'application/json', 'X-Subscription-Token': key},
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    results = (resp.json().get('web') or {}).get('results') or []
    return [
        {
            'title': r.get('title') or '',
            'url': r.get('url') or '',
            'snippet': (r.get('description') or '')[:400],
        }
        for r in results[:limit]
        if r.get('url')
    ]


def _search_searxng(query: str, base_url: str, limit: int) -> list[dict]:
    parsed = urlparse(base_url if '://' in base_url else f'http://{base_url}')
    endpoint = urlunparse((parsed.scheme, parsed.netloc, '/search', '', '', ''))
    # Deliberately not guarded by assert_public_url: a self-hosted SearXNG on
    # the LAN or localhost is the normal deployment, and this URL is typed by
    # the user in Settings rather than chosen by the model.
    resp = requests.get(
        endpoint,
        params={'q': query, 'format': 'json'},
        headers={'User-Agent': USER_AGENT},
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    results = [
        {
            'title': r.get('title') or '',
            'url': r.get('url') or '',
            'snippet': (r.get('content') or '')[:400],
        }
        for r in (payload.get('results') or [])[:limit]
        if r.get('url')
    ]
    if not results:
        unresponsive = payload.get('unresponsive_engines') or []
        if unresponsive:
            reasons = '; '.join(f'{entry}' for entry in unresponsive)
            entries = [str(entry).lower() for entry in unresponsive]
            blocked = [
                entry for entry in entries
                if any(marker in entry for marker in _SOFT_BLOCK_MARKERS)
            ]
            if blocked:
                # A retry is only worth its backoff if something might answer
                # differently next time: an engine that failed for another
                # reason (a timeout, a 5xx) or a block SearXNG hasn't suspended
                # the engine over yet.
                retryable = len(blocked) < len(entries) or any(
                    _SUSPENDED_MARKER not in entry for entry in blocked
                )
                raise SearchSoftBlocked(
                    f'SearXNG engines appear rate-limited or blocked: {reasons}',
                    retryable=retryable,
                )
            logger.info('SearXNG returned no results; unresponsive engines: %s', reasons)
    return results


def web_fetch(url: str) -> dict:
    """{url, title, text} for a public page. Raises UnsafeUrl when refused."""
    current = assert_public_url(url)

    for _ in range(MAX_REDIRECTS + 1):
        resp = requests.get(
            current,
            timeout=FETCH_TIMEOUT,
            headers={'User-Agent': USER_AGENT},
            allow_redirects=False,
            stream=True,
        )
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get('Location')
            resp.close()
            if not location:
                raise UnsafeUrl('Redirect without a destination')
            # Re-run the guard on every hop: a public URL redirecting to
            # 169.254.169.254 is the whole trick.
            current = assert_public_url(requests.compat.urljoin(current, location))
            continue

        resp.raise_for_status()
        content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip()
        if content_type and content_type not in _TEXT_TYPES:
            resp.close()
            raise UnsafeUrl(f'Refusing to read {content_type}; only text pages are fetched')

        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                break
        resp.close()
        body = b''.join(chunks).decode(resp.encoding or 'utf-8', errors='replace')
        text, title = strip_html_with_title(body, MAX_PAGE_CHARS)
        return {'url': current, 'title': title, 'text': text}

    raise UnsafeUrl(f'Too many redirects (more than {MAX_REDIRECTS})')


# --- Tool definitions for chat_with_tools ---

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'web_search',
            'description': (
                'Search the web for pages about a topic. Returns titles, URLs '
                'and short snippets — follow up with web_fetch to read one.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'The search query.'},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'web_fetch',
            'description': 'Read the text of a public web page by URL.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {'type': 'string', 'description': 'An http(s) URL.'},
                },
                'required': ['url'],
            },
        },
    },
]


# Gemma 4 will not follow a search with a fetch when the only instruction to do
# so is back in the system prompt — across live runs it searched, searched again
# and then wrote the article from snippets. It fetches readily when the results
# and the instruction sit together in the freshest message, so the reminder
# rides on the results themselves. Measured, not guessed: see
# docs/ideas-tab.md#the-first-live-run.
READ_ONE_REMINDER = (
    '\n\nThese are snippets, not sources. Open the most substantive of these '
    'with web_fetch and read it before you write anything up.'
)


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    """Execute a tool call. Returns (text for the model, event for the UI).

    Never raises: a failed tool is information the model can act on, and an
    exception here would abandon a run that is otherwise fine.
    """
    if name == 'web_search':
        query = (args.get('query') or '').strip()
        try:
            results = web_search(query)
        except SearchCircuitOpen as e:
            return (
                f'Web search is currently rate-limited/blocked upstream ({e}). '
                'Do not call web_search again — answer from the wiki and repo '
                'context for the rest of this turn.',
                {
                    'tool': 'web_search', 'arg': query, 'ok': False, 'error': str(e),
                    'circuit_open': True,
                },
            )
        except SearchUnavailable as e:
            return (
                f'Web search is unavailable ({e}). Answer from the wiki and repo '
                'context only, and say that you could not search the web.',
                {'tool': 'web_search', 'arg': query, 'ok': False, 'error': str(e)},
            )
        if not results:
            return (
                f'No results for "{query}".',
                {'tool': 'web_search', 'arg': query, 'ok': True, 'count': 0},
            )
        lines = [
            f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
            for i, r in enumerate(results)
        ]
        return (
            '\n'.join(lines) + READ_ONE_REMINDER,
            {
                'tool': 'web_search',
                'arg': query,
                'ok': True,
                'count': len(results),
                'results': [{'title': r['title'], 'url': r['url']} for r in results],
            },
        )

    if name == 'web_fetch':
        url = (args.get('url') or '').strip()
        try:
            page = web_fetch(url)
        except UnsafeUrl as e:
            return (
                f'Refused to fetch that URL: {e}',
                {'tool': 'web_fetch', 'arg': url, 'ok': False, 'error': str(e)},
            )
        except requests.RequestException as e:
            return (
                f'Could not fetch {url}: {e}',
                {'tool': 'web_fetch', 'arg': url, 'ok': False, 'error': str(e)},
            )
        header = f"{page['title']}\n{page['url']}\n\n" if page['title'] else f"{page['url']}\n\n"
        return (
            header + page['text'],
            {
                'tool': 'web_fetch',
                'arg': url,
                'ok': True,
                'title': page['title'],
                'url': page['url'],
            },
        )

    return (
        f'Unknown tool: {name}',
        {'tool': name, 'ok': False, 'error': 'unknown tool'},
    )
