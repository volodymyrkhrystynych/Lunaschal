"""Web search and fetch, exposed to the model as tools.

This is the only part of Lunaschal that makes arbitrary outbound requests, and
the URLs are chosen by an LLM rather than by the user — so the SSRF guard below
is not optional. The app binds 0.0.0.0 in network mode and llama-server sits on
localhost:8080, which is exactly the kind of neighbour an unguarded fetcher
hands to a prompt injection.

Search is pluggable because there is no good local option: Brave and Tavily
need an API key, a self-hosted SearXNG needs none. With no provider configured
the tools return an explanatory *result* rather than raising, so the agent
degrades to "answer from the wiki and repo context, and say search was
unavailable" instead of dying mid-loop — the same trust-first stance
backend/ai/learning_verification.py takes about evidence.
"""
import ipaddress
import logging
import socket
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

_ALLOWED_SCHEMES = ('http', 'https')
# Hostnames that never resolve to something we want to talk to, checked before
# DNS so a resolver that helpfully returns a public IP can't launder them.
_BLOCKED_SUFFIXES = ('.local', '.internal', '.localhost', '.home.arpa')
_TEXT_TYPES = ('text/html', 'text/plain', 'application/xhtml+xml')


class UnsafeUrl(ValueError):
    """The URL points somewhere we refuse to fetch from."""


class SearchUnavailable(RuntimeError):
    """No search provider is configured, or the provider failed."""


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


def search_provider() -> str:
    return (_settings().get('research_search_provider') or '').strip()


def is_search_configured() -> bool:
    settings = _settings()
    provider = (settings.get('research_search_provider') or '').strip()
    if provider in ('brave', 'tavily'):
        return bool(settings.get('research_search_key'))
    if provider == 'searxng':
        return bool(settings.get('research_searxng_url'))
    return False


def web_search(query: str, limit: int = DEFAULT_RESULTS) -> list[dict]:
    """[{title, url, snippet}] from the configured provider."""
    query = (query or '').strip()
    if not query:
        return []
    settings = _settings()
    provider = (settings.get('research_search_provider') or '').strip()
    key = settings.get('research_search_key') or ''
    limit = max(1, min(limit, 10))

    try:
        if provider == 'brave':
            if not key:
                raise SearchUnavailable('Brave search needs an API key')
            return _search_brave(query, key, limit)
        if provider == 'tavily':
            if not key:
                raise SearchUnavailable('Tavily search needs an API key')
            return _search_tavily(query, key, limit)
        if provider == 'searxng':
            url = (settings.get('research_searxng_url') or '').strip()
            if not url:
                raise SearchUnavailable('SearXNG needs a base URL')
            return _search_searxng(query, url, limit)
    except SearchUnavailable:
        raise
    except requests.RequestException as e:
        raise SearchUnavailable(f'Search request failed: {e}') from e

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


def _search_tavily(query: str, key: str, limit: int) -> list[dict]:
    resp = requests.post(
        'https://api.tavily.com/search',
        json={'api_key': key, 'query': query, 'max_results': limit},
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    return [
        {
            'title': r.get('title') or '',
            'url': r.get('url') or '',
            'snippet': (r.get('content') or '')[:400],
        }
        for r in (resp.json().get('results') or [])[:limit]
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
    return [
        {
            'title': r.get('title') or '',
            'url': r.get('url') or '',
            'snippet': (r.get('content') or '')[:400],
        }
        for r in (resp.json().get('results') or [])[:limit]
        if r.get('url')
    ]


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


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    """Execute a tool call. Returns (text for the model, event for the UI).

    Never raises: a failed tool is information the model can act on, and an
    exception here would abandon a run that is otherwise fine.
    """
    if name == 'web_search':
        query = (args.get('query') or '').strip()
        try:
            results = web_search(query)
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
            '\n'.join(lines),
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
