"""Matching a job-application email to the application it belongs to.

Pure: no DB, no network, no model. Everything here is a judgement call about
evidence, which is exactly the kind of thing that should be testable against
fixtures rather than discovered in production six months into a job search.

The hard case is that **the sender is usually not the employer**. Mail about a
Greenhouse application comes from `no-reply@greenhouse.io`, not from
`acme.com`, so a naive domain match scores every Greenhouse rejection against
every Greenhouse application equally. `ATS_DOMAINS` names the senders where the
domain carries no information about *which* employer, so the score falls
through to the company name in the subject and body instead of being spent on a
signal that is real but useless.

Scores are additive evidence capped at 1.0, not probabilities. Two thresholds:
above AUTO_LINK_THRESHOLD the link is made automatically, above
SUGGEST_THRESHOLD it is offered to the user. Between them is where the
interesting mail lives, and guessing there costs more than asking.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Senders that are an applicant-tracking system rather than the employer. The
# domain of a message from one of these says which ATS the company bought, not
# which company it is, so it earns no domain credit.
ATS_DOMAINS = frozenset({
    'greenhouse.io',
    'greenhouse-mail.io',
    'us.greenhouse-mail.io',
    'hire.lever.co',
    'lever.co',
    'ashbyhq.com',
    'myworkday.com',
    'myworkdayjobs.com',
    'workday.com',
    'icims.com',
    'successfactors.com',
    'taleo.net',
    'smartrecruiters.com',
    'workable.com',
    'workablemail.com',
    'bamboohr.com',
    'jobvite.com',
    'breezy.hr',
    'recruitee.com',
    'teamtailor.com',
    'ripplingats.com',
})

# Legal-form suffixes stripped before comparing company names, so "Acme Inc."
# in the subject matches "Acme" on the job. Deliberately only legal forms —
# stripping words like "Technologies" or "Labs" would collapse genuinely
# different employers onto the same name.
_LEGAL_SUFFIXES = frozenset({
    'inc', 'incorporated', 'llc', 'llp', 'ltd', 'limited', 'corp', 'corporation',
    'co', 'company', 'gmbh', 'plc', 'sa', 'srl', 'bv', 'nv', 'ag', 'ab', 'oy',
    'as', 'pty', 'pte', 'kk', 'sas',
})

# Dropped from job titles before overlap scoring: they appear in most postings
# and in most subject lines, so they inflate every score equally.
_TITLE_STOPWORDS = frozenset({
    'the', 'and', 'for', 'with', 'our', 'you', 'your', 'job', 'role', 'position',
    'opening', 'opportunity', 'team', 'remote', 'hybrid', 'onsite', 'full',
    'time', 'part', 'contract', 'permanent', 'new', 'req', 'id',
})

AUTO_LINK_THRESHOLD = 0.6
SUGGEST_THRESHOLD = 0.3

# An application older than this is not getting a reply, and a coincidental
# name collision becomes likelier than a real one.
MAX_LOOKBACK_DAYS = 400

# Mail can legitimately arrive slightly before the recorded applied_at: the
# confirmation is often faster than the human writing down that they applied.
_EARLY_MAIL_GRACE_SECONDS = 6 * 3600

_DAY = 86400

# What an email's job_status implies about the application's status.
# 'other_update' maps to nothing on purpose — an assessment request or a
# status ping says the application is alive, not where it has got to.
EMAIL_STATUS_MAP = {
    'sent': 'acknowledged',
    'interview_next_step': 'interview',
    'rejection': 'rejected',
    'other_update': None,
}

# Forward-only ordering. 'ghosted' sits level with 'submitted' so a reply after
# a silent stretch still advances rather than being read as a downgrade.
PROGRESS_RANK = {
    'draft': 0,
    'ready': 1,
    'submitted': 2,
    'ghosted': 2,
    'acknowledged': 3,
    'interview': 4,
    'offer': 5,
}

# Only ever set by the user. An automated status change must not overrule
# someone who explicitly walked away.
USER_ONLY_STATUSES = frozenset({'withdrawn'})


@dataclass(frozen=True)
class EmailFacts:
    """The parts of an `emails` row linkage looks at."""
    sender_email: str = ''
    subject: str = ''
    body_text: str = ''
    received_at: int = 0


@dataclass(frozen=True)
class ApplicationFacts:
    """An application plus the job fields it is matched on."""
    application_id: str = ''
    company: str = ''
    title: str = ''
    job_url: str = ''
    applied_email: str = ''
    applied_at: int | None = None


@dataclass
class LinkScore:
    application_id: str
    score: float
    reasons: list[str] = field(default_factory=list)
    # Machine-readable version of `reasons`: 'domain', 'company', 'title'.
    # `best_match` needs to reason about *which* evidence fired across all
    # candidates, not just the totals — see its uniqueness rule.
    signals: set[str] = field(default_factory=set)


def _normalize_text(text: str) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed.

    Padding the result with spaces at the comparison site gives word-boundary
    matching for free, which keeps the two-letter company "Co" from matching
    the word "company".
    """
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).split())


def normalize_company(name: str) -> str:
    """Company name reduced to its comparable core: 'Acme Inc.' -> 'acme'."""
    tokens = _normalize_text(name).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return ' '.join(tokens)


def domain_of_email(address: str) -> str:
    _, _, domain = (address or '').strip().lower().rpartition('@')
    return domain.strip('>').strip()


def domain_of_url(url: str) -> str:
    host = urlsplit(url or '').hostname or ''
    host = host.lower()
    return host[4:] if host.startswith('www.') else host


def is_ats_domain(domain: str) -> bool:
    domain = (domain or '').lower()
    return any(domain == d or domain.endswith('.' + d) for d in ATS_DOMAINS)


def same_site(a: str, b: str) -> bool:
    """True when two hostnames plausibly belong to the same organisation.

    Subdomain containment either way, so `careers.acme.com` matches `acme.com`.
    No public-suffix list: this is one signal among several and a wrong answer
    on an exotic TLD costs a suggestion, not a mislink.
    """
    if not a or not b:
        return False
    return a == b or a.endswith('.' + b) or b.endswith('.' + a)


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Word-boundary substring test over already-normalized text."""
    if len(needle) < 3:
        return False
    return f' {needle} ' in f' {haystack} '


def title_overlap(title: str, text: str) -> float:
    """Fraction of a job title's significant words present in `text`.

    Exact-phrase matching fails on real subject lines — "Senior Backend
    Engineer, Payments" arrives as "Your application to Acme — Backend
    Engineer" — so this measures how much of the title survived instead.
    """
    tokens = [t for t in _normalize_text(title).split()
              if len(t) >= 3 and t not in _TITLE_STOPWORDS]
    if not tokens:
        return 0.0
    hay = _normalize_text(text)
    hits = sum(1 for t in tokens if _contains_phrase(hay, t))
    return hits / len(tokens)


def score_link(email: EmailFacts, app: ApplicationFacts) -> LinkScore:
    """How strongly `email` looks like a reply to `app`. 0.0 to 1.0."""
    reasons: list[str] = []
    signals: set[str] = set()

    # An application that was never submitted has nothing to reply to.
    if not app.applied_at:
        return LinkScore(app.application_id, 0.0, ['not submitted'])

    # Mail that predates the application cannot be about it.
    if email.received_at < app.applied_at - _EARLY_MAIL_GRACE_SECONDS:
        return LinkScore(app.application_id, 0.0, ['email predates application'])

    if email.received_at - app.applied_at > MAX_LOOKBACK_DAYS * _DAY:
        return LinkScore(app.application_id, 0.0, ['application too old'])

    score = 0.0
    sender_domain = domain_of_email(email.sender_email)

    if is_ats_domain(sender_domain):
        # The domain identifies the ATS, not the employer. Say so, and let the
        # name signals below carry the decision.
        reasons.append(f'{sender_domain} is an ATS, not the employer')
    elif same_site(sender_domain, domain_of_url(app.job_url)):
        score += 0.6
        signals.add('domain')
        reasons.append(f'sender domain matches the posting ({sender_domain})')

    company = normalize_company(app.company)
    if company:
        if _contains_phrase(_normalize_text(email.subject), company):
            score += 0.35
            signals.add('company')
            reasons.append('company name in the subject')
        elif _contains_phrase(_normalize_text(email.body_text), company):
            score += 0.2
            signals.add('company')
            reasons.append('company name in the body')

    subject_overlap = title_overlap(app.title, email.subject)
    if subject_overlap:
        score += 0.25 * subject_overlap
        signals.add('title')
        reasons.append(f'{round(subject_overlap * 100)}% of the job title in the subject')
    else:
        body_overlap = title_overlap(app.title, email.body_text)
        if body_overlap:
            score += 0.1 * body_overlap
            signals.add('title')
            reasons.append(f'{round(body_overlap * 100)}% of the job title in the body')

    return LinkScore(app.application_id, min(score, 1.0), reasons, signals)


def rank_candidates(
    email: EmailFacts, applications: list[ApplicationFacts]
) -> list[LinkScore]:
    """Every application scored against one email, best first."""
    scored = [score_link(email, app) for app in applications]
    scored.sort(key=lambda s: s.score, reverse=True)
    return [s for s in scored if s.score > 0]


def best_match(
    email: EmailFacts, applications: list[ApplicationFacts]
) -> tuple[LinkScore | None, bool]:
    """The winner and whether it is confident enough to link without asking.

    Two rules the additive score cannot express on its own, because both are
    about the *field* of candidates rather than any one of them:

    - **Uniqueness beats magnitude.** Mail from `no-reply@greenhouse.io` saying
      "Your application to Acme" earns no domain credit by design, so it tops
      out well under the auto-link threshold — yet if Acme is the only employer
      you have applied to that it names, the identification is not ambiguous at
      all. A signal that singles out exactly one application is decisive
      regardless of what it adds up to.

    - **A close runner-up blocks everything.** Two applications to the same
      company scoring alike is precisely where a confident guess quietly
      corrupts the record, and where asking costs one tap.
    """
    ranked = rank_candidates(email, applications)
    if not ranked:
        return None, False
    top = ranked[0]
    if top.score < SUGGEST_THRESHOLD:
        return top, False

    if len(ranked) > 1 and (top.score - ranked[1].score) < 0.15:
        return top, False

    if top.score >= AUTO_LINK_THRESHOLD:
        return top, True

    # Naming exactly one employer identifies it, however little that scored.
    named_company = [s for s in ranked if 'company' in s.signals]
    if len(named_company) == 1 and named_company[0] is top:
        return top, True

    return top, False


def advance_status(current: str, job_status: str | None) -> str | None:
    """The application's new status given an email's job_status, or None.

    Monotonic along PROGRESS_RANK so a confirmation email that syncs late can
    never walk an application back from 'interview' to 'acknowledged'.
    """
    target = EMAIL_STATUS_MAP.get(job_status or '')
    if not target or current == target:
        return None
    if current in USER_ONLY_STATUSES:
        return None

    if target == 'rejected':
        # A rejection is unambiguous and final — except after an offer, where
        # it is far likelier to be a stale message about a different stage.
        return None if current in ('rejected', 'offer') else 'rejected'

    # Nothing reopens a rejection automatically; a real reversal is rare enough
    # to be worth a human confirming it.
    if current == 'rejected':
        return None

    if PROGRESS_RANK.get(target, 0) > PROGRESS_RANK.get(current, 0):
        return target
    return None
