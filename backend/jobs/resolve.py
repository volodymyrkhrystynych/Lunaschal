"""A company's careers page → the board this app can actually sync.

The gap this fills: almost every company careers page is a thin wrapper around
a hosted ATS board, and syncing one needs that board's *slug*. Slugs cannot be
guessed. Ada's Greenhouse board is `ada18`; Cohere's Ashby board happens to be
`cohere`. Asking the user to type it means asking them to go and find it, which
is the tedious half of the job this feature exists to remove.

So: fetch the careers page, read the ATS links out of the raw HTML, and
**verify the guess against the board API before believing it**. A slug that
returns postings is right; one that 404s was a bad regex match on a URL that
happened to look like a board. That verification is what makes it safe to
detect rather than ask.

Detection is pure regex over HTML — no model, and no judgement to get wrong.
The page is user-supplied and fetched from inside the network, so it goes
through `ingest.fetch_html`, which re-runs `assert_public_url` on every
redirect hop.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from backend.jobs import ingest
from backend.jobs.sources import SourceError, fetch as fetch_source

logger = logging.getLogger(__name__)

# Path segments that match the slug pattern but are not slugs. Without this,
# `boards.greenhouse.io/embed/job_board?for=acme` resolves to "embed".
_NOT_SLUGS = frozenset({
    'embed', 'v1', 'boards', 'job_board', 'jobs', 'api', 'posting-api',
    'job-board', 'search', 'www',
})

# Ordered: the first supported ATS with a usable slug wins. A page linking to
# both its own Greenhouse board and some partner's Lever board is rare enough
# that "first match, then verify" is the right trade against complexity.
_SUPPORTED: list[tuple[str, list[str]]] = [
    ('greenhouse', [
        # The embed form carries the slug in a query parameter, not the path.
        r'greenhouse\.io/embed/job_board[^"\'\s]*[?&]for=([A-Za-z0-9_-]+)',
        r'boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)',
        # Both the legacy `boards.` and the current `job-boards.` hosts.
        r'(?:job-)?boards\.greenhouse\.io/([A-Za-z0-9_-]+)',
    ]),
    ('lever', [
        r'jobs\.lever\.co/([A-Za-z0-9_-]+)',
        r'api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)',
    ]),
    ('ashby', [
        r'jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)',
        r'api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_-]+)',
    ]),
]

# Recognised but not syncable. Naming them is the point: "we found Workday and
# cannot read it" is actionable, while "no board found" sends the user looking
# for something that was never there.
_UNSUPPORTED: list[tuple[str, str]] = [
    (r'myworkdayjobs\.com|myworkday\.com|workday\.com', 'Workday'),
    (r'\.bamboohr\.com', 'BambooHR'),
    (r'apply\.workable\.com|workable\.com', 'Workable'),
    (r'jobs\.smartrecruiters\.com|smartrecruiters\.com', 'SmartRecruiters'),
    (r'\.recruitee\.com', 'Recruitee'),
    (r'\.breezy\.hr', 'Breezy'),
    (r'jobs\.jobvite\.com|jobvite\.com', 'Jobvite'),
    (r'\.icims\.com', 'iCIMS'),
    (r'\.taleo\.net', 'Taleo'),
    (r'\.successfactors\.com', 'SuccessFactors'),
    (r'\.teamtailor\.com', 'Teamtailor'),
    (r'\.paylocity\.com', 'Paylocity'),
    (r'\.dayforcehcm\.com', 'Dayforce'),
    (r'\.ripplingats\.com|ats\.rippling\.com', 'Rippling'),
    (r'\.pinpointhq\.com', 'Pinpoint'),
    (r'\.applytojob\.com', 'JazzHR'),
]


@dataclass
class Resolution:
    """What a careers page turned out to be.

    `kind` set means it is syncable and `jobCount` came back from the live
    board. `detected` without `kind` means a recognised ATS this app cannot
    read — a real answer, not a failure.
    """
    url: str = ''
    kind: str | None = None
    slug: str = ''
    company: str = ''
    job_count: int = 0
    detected: str = ''
    error: str = ''
    candidates: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'url': self.url,
            'kind': self.kind,
            'slug': self.slug,
            'company': self.company,
            'jobCount': self.job_count,
            'detected': self.detected,
            'error': self.error,
            'candidates': [{'kind': k, 'slug': s} for k, s in self.candidates],
        }


def find_candidates(html: str, final_url: str = '') -> list[tuple[str, str]]:
    """Every (kind, slug) the page points at, best first, deduplicated.

    `final_url` is scanned too, because a careers page that simply redirects to
    the board has the whole answer in the URL and may have no useful body.
    """
    haystack = f'{final_url}\n{html}'
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for kind, patterns in _SUPPORTED:
        for pattern in patterns:
            for match in re.finditer(pattern, haystack, re.IGNORECASE):
                slug = match.group(1)
                if slug.lower() in _NOT_SLUGS:
                    continue
                key = (kind, slug)
                if key not in seen:
                    seen.add(key)
                    found.append(key)
    return found


def find_unsupported(html: str, final_url: str = '') -> str:
    """The name of a recognised but unsyncable ATS, or ''."""
    haystack = f'{final_url}\n{html}'
    for pattern, name in _UNSUPPORTED:
        if re.search(pattern, haystack, re.IGNORECASE):
            return name
    return ''


def verify(kind: str, slug: str) -> tuple[int, str]:
    """Ask the board whether this slug is real. Returns (job_count, error).

    A board with zero postings is still a valid board — companies pause
    hiring — so an empty list is success, not failure. Only an error means the
    slug was wrong.
    """
    try:
        result = fetch_source(kind, {'slug': slug})
    except SourceError as e:
        return 0, str(e)
    except Exception as e:
        logger.warning('Verifying %s/%s failed: %s', kind, slug, e)
        return 0, str(e)
    return len(result.jobs), ''


def resolve_careers_page(url: str) -> Resolution:
    """One careers page → a syncable board, or an honest explanation.

    Never raises for an ordinary failure: an unreachable page, a Cloudflare
    wall or an unrecognised ATS all come back as a `Resolution` carrying the
    reason, because all three are things the user can act on.
    """
    try:
        html, final_url = ingest.fetch_html(url)
    except ingest.UnsafeUrl as e:
        return Resolution(url=url, error=f'That URL is not reachable from here: {e}')
    except ingest.FetchFailed as e:
        return Resolution(url=url, error=str(e))

    candidates = find_candidates(html, final_url)
    result = Resolution(url=final_url, candidates=candidates)

    # Try each candidate against the live board. The first that answers is the
    # one — this is where a regex that matched some unrelated URL gets thrown
    # out rather than becoming a source that silently never syncs.
    errors = []
    for kind, slug in candidates:
        count, error = verify(kind, slug)
        if not error:
            result.kind = kind
            result.slug = slug
            result.job_count = count
            result.company = _company_name(html) or slug
            return result
        errors.append(f'{kind}/{slug}: {error}')

    unsupported = find_unsupported(html, final_url)
    if unsupported:
        result.detected = unsupported
        result.error = (
            f'{unsupported} — recognised, but this app cannot sync it. '
            'Open their careers page directly.'
        )
        return result

    if errors:
        result.error = 'Found board links, but none answered: ' + '; '.join(errors)
    else:
        result.error = (
            'No job board found on that page. It may load its listings with '
            'JavaScript — try the URL of the board itself.'
        )
    return result


_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)
# "Careers at Acme | Acme Inc" → "Acme". Titles are the only company name a
# careers page reliably carries, and they are reliably cluttered.
_TITLE_NOISE = re.compile(
    r'\b(careers?|jobs?|open roles?|open positions?|opportunities|'
    r'we.re hiring|join us|work with us|hiring)\b',
    re.IGNORECASE,
)
# Removing "Careers" from "Careers at Cohere" leaves "at Cohere", which then
# beats the bare "Cohere" on the other side of the separator.
_LEADING_CONNECTIVE = re.compile(r'^(?:at|with|for|to|@)\s+', re.IGNORECASE)


def _company_name(html: str) -> str:
    match = _TITLE_RE.search(html)
    if not match:
        return ''
    import html as html_mod
    title = html_mod.unescape(match.group(1)).strip()
    # Split on the usual title separators and take the longest surviving part
    # after removing the careers-page boilerplate.
    parts = []
    for part in re.split(r'\s*[|–—·]\s*|\s+[-]\s+', title):
        cleaned = _TITLE_NOISE.sub('', part).strip(' -–—|·:,')
        cleaned = _LEADING_CONNECTIVE.sub('', cleaned).strip(' -–—|·:,')
        if len(cleaned) > 1:
            parts.append(cleaned)
    return max(parts, key=len)[:80] if parts else ''
