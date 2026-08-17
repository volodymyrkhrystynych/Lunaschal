"""Deciding whether two URLs point at the same job posting.

The browser extension needs to answer one question when a tab opens: which
application is this? Asking the user every time defeats the purpose, and
guessing wrong is worse than not guessing — answers would be recorded against
somebody else's posting.

So matching is deliberately strict. Exact-after-normalization is a match, and
same-host-same-path is a match; nothing else is. Anything looser (host-only,
prefix, fuzzy) would confidently link two openings at the same company, which
is precisely the mistake `linkage.best_match` refuses to make for email.

Pure: no DB, no network.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

# urlsplit is happy to call "not a url" a hostname once a scheme is bolted on,
# so the shape of a host is checked rather than assumed. A dot is required
# (every real posting URL has one) with `localhost` exempted for development.
_HOST_RE = re.compile(r'^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$')

# Campaign junk that changes between two links to the same posting. `gh_src`
# is Greenhouse's *source* tag and is dropped; `gh_jid` is the job id and is
# emphatically kept, because on an embedded board it is the only thing that
# distinguishes one posting from another.
_TRACKING_EXACT = frozenset({
    'gh_src', 'source', 'src', 'ref', 'referrer', 'trk', 'trackingid',
    'fbclid', 'gclid', 'mc_cid', 'mc_eid',
})
_TRACKING_PREFIX = ('utm_',)


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_EXACT or lowered.startswith(_TRACKING_PREFIX)


def normalize(url: str) -> str:
    """A comparable form of `url`, or '' when there is nothing to compare.

    Scheme is dropped rather than normalized: boards move between http and
    https and a stored posting should not stop matching because of it.
    """
    if not url or not isinstance(url, str):
        return ''
    raw = url.strip()
    if not raw:
        return ''
    # urlsplit needs a scheme to find the host; a bare "acme.com/jobs/1" would
    # otherwise parse entirely as a path.
    if '//' not in raw:
        raw = '//' + raw

    try:
        parts = urlsplit(raw if '://' in raw else 'https:' + raw)
    except ValueError:
        return ''

    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if not _HOST_RE.match(host) or ('.' not in host and host != 'localhost'):
        return ''

    path = parts.path.rstrip('/')

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking(k)]
    query = urlencode(sorted(kept))

    return f'{host}{path}' + (f'?{query}' if query else '')


def path_key(url: str) -> str:
    """`normalize` with the query dropped — the weaker of the two signals."""
    normalized = normalize(url)
    return normalized.split('?', 1)[0] if normalized else ''


def same_posting(a: str, b: str) -> bool:
    """True when both URLs are the same posting under either rule."""
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return False
    if left == right:
        return True
    return path_key(a) == path_key(b)


def best_match(target: str, candidates: list[dict], url_key: str = 'url') -> dict | None:
    """The one candidate that matches `target`, or None.

    None when nothing matches *and* when more than one does: two applications
    sharing a URL is exactly the case where a confident guess quietly records
    an answer against the wrong employer.
    """
    exact_key = normalize(target)
    if not exact_key:
        return None
    loose_key = path_key(target)

    exact = [c for c in candidates if normalize(c.get(url_key) or '') == exact_key]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None

    loose = [c for c in candidates if path_key(c.get(url_key) or '') == loose_key]
    return loose[0] if len(loose) == 1 else None
