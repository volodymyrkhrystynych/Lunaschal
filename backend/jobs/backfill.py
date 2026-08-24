"""Reconstruct applications from the confirmation mail they produced.

The Jobs feature creates an `applications` row when you apply *through* the
app. Anyone who was already job-hunting before it existed has the entire search
sitting in their mailbox instead — confirmations, rejections and interview
threads that the classifier has already tagged, all of it pointing at
applications that were never recorded. Linkage cannot help: it attaches mail to
applications, and there are none, so a mailbox with thousands of tagged messages
produces an empty pipeline.

This reads the confirmations back into `jobs` + `applications` rows so the
existing machinery has something to attach to. It deliberately does *not* try to
work out status: a backfilled row is created at 'submitted', and the ordinary
linker advances it to acknowledged/interview/rejected from the very same mail,
using the monotonic rules in linkage.py. Reimplementing that here would be a
second opinion on a question already answered.

Two rules decide what gets created:

**A confirmation must name a company, or it is skipped.** This is `geo.py`'s
rule about half a coordinate: an application row with no employer is a row that
looks like an application and cannot be linked, matched or reasoned about. It is
worse than nothing, because it pads the pipeline with entries that will never
resolve. The role title is allowed to be empty — linkage matches on company, and
"applied to Acme, role unrecorded" is a true statement — but the company is not
optional.

**The classifier's `job_status='sent'` is a starting filter, not a verdict.**
It is a model output over a mailbox full of near-misses, and it tags things like
"Thank you for Creating an Account!" and job-alert newsletters as confirmations.
Requiring one of the patterns below to match is what actually separates an
application from mail that merely smells like one — a newsletter never says
"your application was sent to".

The parsers run against `body_text` wherever possible. The exception is Indeed,
which is both the largest single source of confirmations and the only one that
sends a plain-text part with no company in it at all — its `body_text` is
"Your application has been submitted. Good luck!" and nothing else. The company
is only in the HTML part, which means Indeed confirmations synced before
`emails.body_html` existed cannot be attributed to an employer from local data
alone. `unresolved_indeed()` reports how many are in that state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.htmltext import strip_html
from backend.jobs.linkage import domain_of_email, is_ats_domain, normalize_company

# Subdomains a company sends mail from; they precede the name rather than being
# it, so 'careers.acosta.com' is Acosta and not Careers.
_MAIL_SUBDOMAINS = frozenset({
    'mail', 'email', 'e', 'em', 'smtp', 'mailer', 'notifications', 'notify',
    'no-reply', 'noreply', 'careers', 'jobs', 'hr', 'recruiting', 'talent',
    'apply', 'application', 'applications', 'send', 'reply', 'info', 'news',
})

# Trailing punctuation a subject line puts after a company name. Stripped from
# the captured group rather than excluded in the pattern, because a company can
# legitimately contain a period ("Acme Inc.") and the pattern cannot tell the
# abbreviation's dot from the sentence's.
_TRAILING = ' \t!.,:;-–—\'"'

# Noise that follows a company name in a subject often enough to be worth
# cutting, and that no company name contains.
_COMPANY_TAIL = re.compile(
    r'\s*[-–—|(]\s*(?:job|position|role|application|req(?:uisition)?\b|id\b|#).*$',
    re.I,
)

# A body capture runs on until the sentence does, so "your interest in Robinhood
# – we're excited to…" arrives with the rest of the clause attached. Subject
# lines do not need this: they end where the company does.
_CLAUSE_TAIL = re.compile(r'\s*[–—,;:(]\s.*$|\s+[-–—]\s.*$')

# Lead-ins that sit between the pattern's anchor and the actual name: "your
# interest in *a career with* the Fitch Group". Stripped rather than written
# into every pattern, because they combine freely with all of them.
_COMPANY_LEAD = re.compile(
    r'^(?:a|an|the)\s+(?:career|position|role|job|opportunity|opening)\s+'
    r'(?:with|at|in|for)\s+(?:the\s+)?'
    r'|^(?:working|joining|a\s+role)\s+(?:with|at|for)\s+(?:the\s+)?'
    r'|^(?:us|our\s+team)\s+(?:at|in)\s+'
    r'|^our\s+(?:company|organisation|organization|team)\s+'
    r'|^employment\s+(?:with|at)\s+(?:the\s+)?'
    r'|^joining\s+(?=[A-Z])',
    re.I,
)

# What a loose pattern captures when the sentence never actually named anyone.
# "Thank you for your interest in the above position" is a real sentence in real
# confirmation mail, and it identifies no employer at all.
_GENERIC_COMPANY = frozenset({
    'above position', 'position', 'role', 'this role', 'this position',
    'the above position', 'our company', 'the company', 'opportunity',
    'this opportunity', 'the opportunity', 'our team', 'the team', 'us',
    'this job', 'the job', 'job', 'our organization', 'the organisation',
    'our organisation', 'the organization', 'your application', 'application',
    'the following', 'career', 'careers', 'this vacancy', 'the vacancy',
})

# Mailbox providers and aggregators: the domain is real but it is not the
# employer, so it must never become one. ATS domains are excluded separately
# via linkage.is_ats_domain, which already maintains that list.
_NON_EMPLOYER_DOMAINS = frozenset({
    'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'yahoo.com',
    'icloud.com', 'me.com', 'aol.com', 'proton.me', 'protonmail.com',
    'indeed.com', 'indeedemail.com', 'linkedin.com', 'glassdoor.com',
    'ziprecruiter.com', 'monster.ca', 'monster.com', 'dice.com',
    # HR/payroll suites and recruiting platforms that send on an employer's
    # behalf exactly as an ATS does. Same failure if they are not listed: every
    # application through one is attributed to the platform, so "Dayforce" or
    # "ADP" shows up as the biggest employer in the pipeline.
    'dayforce.com', 'adp.com', 'clientconnections.com', 'successfactors.eu',
    'successfactors.com', 'myworkday.com', 'workday.com', 'icims.com',
    'bamboohr.com', 'applytojob.com', 'crelate.net', 'jobright.ai',
    'getgotjobs.co.uk', 'guide.co', 'collage.co', 'workablemail.com',
})


# Every pattern above is itself proof that a message is a confirmation: only an
# application confirmation says "your application was sent to". The sender-domain
# fallback carries no such proof — a company's own domain sends marketing, magic
# links and account notices too — so it needs this gate to stand in for one.
# Without it the fallback re-admits precisely the misclassified mail that
# requiring a pattern was keeping out.
_CONFIRMATION_PHRASE = re.compile(
    r'\bapplication\b|\bapplying\b|\bapplied\b|\bapplicant\b'
    r'|\bwe(?:\'ve| have)? received your\b|\bthank you for your submission\b',
    re.I,
)


@dataclass(frozen=True)
class ParsedConfirmation:
    """What one confirmation email says about the application behind it.

    `parser` names the pattern that matched, which is what makes a preview
    reviewable: a run that suddenly attributes six hundred applications to one
    company is obvious when the rule responsible is on screen next to it.
    """
    company: str
    title: str
    parser: str

    @property
    def company_key(self) -> str:
        return normalize_company(self.company)


def _clean(value: str) -> str:
    """A captured group reduced to a plausible name."""
    text = (value or '').strip().strip(_TRAILING)
    text = _COMPANY_TAIL.sub('', text)
    # Collapse the whitespace an HTML-to-text pass leaves behind.
    return ' '.join(text.split()).strip(_TRAILING)


# --- company, from the subject line ------------------------------------------
#
# Ordered: the first match wins, so the specific patterns precede the loose
# ones. Every one of these is a sentence only an application confirmation
# says, which is what keeps misclassified newsletters out.
_SUBJECT_COMPANY = [
    # "Volodymyr, your application was sent to Capston Inc"
    ('linkedin', re.compile(
        r'your application was sent to\s+(?P<company>.+)$', re.I)),
    # "Thank you for applying to Samsara" / "Thanks for applying at CMiC"
    ('applying-to', re.compile(
        r'\bfor applying\s+(?:to|at|with)\s+(?:the\s+)?(?P<company>.+)$', re.I)),
    # "Thank you for your application to Hybrid Financial"
    ('application-to', re.compile(
        r'\byour application (?:to|at|with)\s+(?P<company>.+)$', re.I)),
    # "Thanks for your interest in Rakuten!"
    ('interest-in', re.compile(
        r'\byour interest in\s+(?P<company>.+)$', re.I)),
    # Ashby's rejection/confirmation shape: "Klue | Software Engineer II, AI"
    ('pipe', re.compile(
        r'^(?P<company>[^|]{2,48})\s*\|\s*(?P<title>.+)$')),
]

# --- company, from the body, when the subject withheld it ---------------------
_BODY_COMPANY = [
    ('indeed-html', re.compile(
        r'The following items were sent to\s+(?P<company>.+?)\.\s', re.S)),
    ('body-applying-to', re.compile(
        r'\bfor applying\s+(?:to|at|with)\s+(?:the\s+)?(?P<company>[^\n.!]{2,60})', re.I)),
    ('body-interest-in', re.compile(
        r'\byour interest in\s+(?P<company>[^\n.!]{2,60})', re.I)),
    ('body-choosing', re.compile(
        r'\bchoosing\s+(?P<company>[^\n.!]{2,60})\s+in your job search', re.I)),
]

# --- role title ---------------------------------------------------------------
_SUBJECT_TITLE = [
    # Indeed puts the role in the subject and nothing else anywhere.
    ('indeed', re.compile(r'^Indeed Application:\s*(?P<title>.+)$', re.I)),
    ('application-for', re.compile(
        r'\byour (?:recent )?(?:job )?application for\s+(?P<title>.+)$', re.I)),
    ('thank-you-for-application-for', re.compile(
        r'\bfor your application for\s+(?P<title>.+)$', re.I)),
]

_BODY_TITLE = [
    # "for the Junior Back End Engineer position" / "the Senior SWE role"
    # Both "applying for the X role" and "applying to the X role" occur, often
    # in the same message from the same ATS.
    ('for-the-role', re.compile(
        r'\b(?:for|to)\s+the\s+(?P<title>[^\n.!]{2,70}?)\s+'
        r'(?:position|role|opening|job|vacancy)\b',
        re.I)),
    ('applying-for-the', re.compile(
        r'\bapplic\w+\s+for\s+(?:the\s+)?(?P<title>[^\n.!]{2,70}?)\s*(?:\.|,|\n|$)', re.I)),
]


# Lowercase words that appear *inside* a company name. They only stay if a
# capitalised word follows, which is what separates "Millennium Software and
# Staffing" from "BrightOrder and the effort you put in".
_NAME_CONNECTORS = frozenset({'and', '&', 'of', 'for', 'de', 'the', 'at', 'en'})

# "Thank you for applying to Full Stack Developer I Role at Intuit" puts both
# facts in the slot the company pattern captures. Splitting on ' at ' recovers
# the company *and* the title; without it the whole string becomes the employer.
_ROLE_AT_COMPANY = re.compile(
    r'^(?P<title>.{2,70}?)\s+(?:role\s+|position\s+|job\s+)?(?:at|with)\s+(?P<company>.{2,60})$',
    re.I,
)

# Words that make a string a job title rather than an employer. A capture that
# is nothing but these is a pattern that matched the wrong half of a sentence,
# and an "application to Software Developer" is worse than no row at all — it
# invents a company that does not exist and can never be linked to.
_TITLE_WORDS = re.compile(
    r'\b(?:developer|engineer|engineering|analyst|architect|designer|manager|'
    r'consultant|scientist|administrator|specialist|technician|intern|'
    r'programmer|lead|director|officer|associate|assistant|coordinator|'
    r'representative|full[- ]?stack|frontend|front[- ]end|backend|back[- ]end|'
    r'devops|qa|sre)\b',
    re.I,
)

# Tokens that mark a real organisation even alongside title words, so
# "Score Media and Gaming Inc" survives while "Software Developer" does not.
_ORG_WORDS = re.compile(
    r'\b(?:inc|inc\.|llc|ltd|limited|corp|corporation|co|company|group|'
    r'technologies|technology|solutions|systems|services|consulting|labs|'
    r'holdings|partners|bank|university|hospital|institute|agency|media|'
    r'capital|health|financial|gaming|studios|ventures|staffing|search|'
    r'association|union|credit|foundation|society|council)\b',
    re.I,
)

# 'software' is deliberately not an organisation word. It rescues "Millennium
# Software and Staffing" but equally rescues "Software Developer", and the
# staffing firms are already covered by their own suffixes — so the trade only
# ever cost real companies nothing and let job titles through.

# A phrase ending in one of these is describing the job, not the employer:
# "your application to the Overnight Food Production Team Member role".
_ENDS_WITH_ROLE = re.compile(
    r'\b(?:role|position|opening|vacancy|opportunity|posting|req)$', re.I)

# A subject packs several clauses into one line. A company name never contains
# an exclamation mark, and a dash is followed by the role or a banner far more
# often than by more of the name.
_SUBJECT_CLAUSE = re.compile(r'\s*[!¡]\s*.*$|\s+[-–—]\s+(?P<rest>.*)$')

# A marketing banner bolted onto the end of a subject: "…applying to Meridian
# Credit Union and ACTION REQUIRED: Survey". Cut at a connector followed by a
# shouted word. The four-character floor keeps real initialisms — RBC, BMO,
# CIBC — out of it, since those are the company rather than a banner after it.
_SUBJECT_BANNER = re.compile(r'\s+(?:and|[-–—:])\s+(?=[A-Z]{4,}\b)')


def _looks_like_job_title(text: str) -> bool:
    """True when a captured 'company' is really a role.

    Organisation words win ties: a staffing firm can legitimately be called
    "ISG Search Inc" or "Millennium Software and Staffing", and rejecting those
    would cost more real applications than the false companies it prevents.
    """
    if _ENDS_WITH_ROLE.search(text.strip()):
        return True
    if not _TITLE_WORDS.search(text):
        return False
    return not _ORG_WORDS.search(text)


def _trim_subject_clause(text: str) -> str:
    """Cut a subject capture back to the clause the company is in.

    The dash is only cut when what follows looks like a role or a shouted
    banner ("Hootsuite - Intermediate Software Developer", "Meridian Credit
    Union and ACTION REQUIRED"), because plenty of real names are hyphenated.
    """
    banner = _SUBJECT_BANNER.search(text)
    if banner:
        text = text[:banner.start()].strip()
    m = _SUBJECT_CLAUSE.search(text)
    if not m:
        return text
    rest = (m.groupdict().get('rest') or '').strip()
    if rest and not (_TITLE_WORDS.search(rest) or rest.isupper()):
        return text
    return text[:m.start()].strip()


def _proper_noun_run(text: str) -> str:
    """The company name inside a run-on sentence fragment.

    A body pattern captures until the sentence ends, not until the name does:
    "your interest in Laurentian Bank and wish you the best in your search".
    Trimming by clause punctuation does not help, because there is none. What
    reliably marks the name in these sentences is that it is the proper noun —
    so take the first run of capitalised words and stop where it stops.

    This is why the rule is applied to body captures only. Subject lines end
    where the company does, and running it there would mangle a lowercase brand
    ("commonsku") that a subject pattern captured correctly.
    """
    tokens = (text or '').split()
    start = next((i for i, t in enumerate(tokens) if t[:1].isupper()), None)
    if start is None:
        return ''
    out = []
    i = start
    while i < len(tokens):
        token = tokens[i]
        if token[:1].isupper():
            out.append(token)
            i += 1
            continue
        # A connector survives only as a bridge to more of the name.
        if (token.lower().strip(',&') in _NAME_CONNECTORS
                and i + 1 < len(tokens) and tokens[i + 1][:1].isupper()):
            out.append(token)
            i += 1
            continue
        break
    return ' '.join(out)


def _clean_company(value: str, *, from_body: bool) -> str:
    """A captured company name, or '' when the capture named no one.

    `from_body` turns on clause trimming. A subject line ends where the company
    does; a body sentence carries on past it, so only the body needs cutting —
    doing it to subjects would truncate "Smith, Rogers & Co, Ltd" at its comma.
    """
    text = (value or '').strip()
    if from_body:
        text = _CLAUSE_TAIL.sub('', text)
        text = _COMPANY_LEAD.sub('', text)
        text = _proper_noun_run(text)
    else:
        text = _trim_subject_clause(text)
        text = _COMPANY_LEAD.sub('', text)
        text = re.sub(r'^(?:the|a|an)\s+', '', text, flags=re.I)
    text = _clean(text)
    if not text:
        return ''
    if normalize_company(text) in _GENERIC_COMPANY or text.lower() in _GENERIC_COMPANY:
        return ''
    # A capture that is all lowercase function words is prose that happened to
    # sit where a name goes.
    if not re.search(r'[A-Za-z0-9]', text):
        return ''
    return text


def _first_match(patterns, text: str, group: str, clean=_clean) -> tuple[str, str]:
    """(value, parser_name) for the first pattern that captures `group`."""
    if not text:
        return '', ''
    for name, pattern in patterns:
        m = pattern.search(text)
        if not m:
            continue
        try:
            captured = clean(m.group(group))
        except IndexError:
            continue
        if captured:
            return captured, name
    return '', ''


def company_from_sender(sender_email: str) -> str:
    """The employer implied by a sender address, when it implies one.

    An ATS sends on the employer's behalf, so its domain says nothing about who
    the job is with — that is exactly the trap `linkage.ATS_DOMAINS` exists to
    avoid, and reusing it here keeps one list rather than two. Mailbox providers
    and job boards are excluded for the same reason. What is left is mail from a
    company's own domain, where the domain is the best evidence available.

    Returns a display form ("acosta.com" -> "Acosta"), not a normalized key.
    """
    domain = domain_of_email(sender_email)
    if not domain or is_ats_domain(domain) or domain in _NON_EMPLOYER_DOMAINS:
        return ''
    # Drop the public suffix and any mail-only subdomain: 'careers.acosta.co.uk'
    # and 'acosta.com' should both read as 'Acosta'.
    labels = [p for p in domain.split('.') if p]
    while len(labels) > 1 and labels[0] in _MAIL_SUBDOMAINS:
        labels.pop(0)
    if not labels:
        return ''
    name = labels[0]
    if len(name) < 2 or name in _GENERIC_COMPANY:
        return ''
    return name.replace('-', ' ').title()


def _linkedin_title(body: str) -> str:
    """LinkedIn's confirmation body is positional, not prose.

        Your application was sent to Capston Inc
        <blank>
        Software Engineer
        Capston Inc
        Toronto, ON

    There is no sentence to match, so the title is read by position: the first
    non-empty line after the lead-in. Guarded by that lead-in so the rule only
    ever runs on the layout it was written for.
    """
    lines = [ln.strip() for ln in (body or '').splitlines()]
    for i, line in enumerate(lines):
        if re.match(r'your application was sent to\b', line, re.I):
            for candidate in lines[i + 1:]:
                if candidate:
                    return _clean(candidate)
            break
    return ''


def parse_confirmation(
    *,
    subject: str = '',
    sender_email: str = '',
    body_text: str = '',
    body_html: str = '',
) -> ParsedConfirmation | None:
    """One email in, one reconstructed application out — or None.

    None means "this is not a confirmation I can attribute to an employer",
    which covers both genuinely unparseable mail and the classifier's false
    positives. The caller counts these rather than guessing at them.
    """
    subject = (subject or '').strip()
    body = body_text or ''
    # Indeed's plain-text part is deliberately contentless; the HTML part is
    # where the employer is. Falling back to it costs nothing when body_text
    # already carries the answer, because the subject patterns run first.
    html_text = ' '.join(strip_html(body_html or '').split()) if body_html else ''

    subject_clean = lambda v: _clean_company(v, from_body=False)  # noqa: E731
    body_clean = lambda v: _clean_company(v, from_body=True)  # noqa: E731

    split_title = ''
    company, parser = _first_match(_SUBJECT_COMPANY, subject, 'company', subject_clean)
    if company:
        # "…applying to Full Stack Developer I Role at Intuit" — the capture
        # holds both halves. Only split when the right-hand side is plausibly a
        # company, so "Thanks for applying at CMiC" keeps CMiC whole.
        m = _ROLE_AT_COMPANY.match(company)
        if m and not _looks_like_job_title(_clean(m.group('company'))):
            candidate = _clean(m.group('company'))
            if candidate:
                split_title = _clean(m.group('title'))
                company = candidate
    if company and _looks_like_job_title(company):
        # The pattern matched the role, not the employer. Keep it as the title
        # and fall through — recording a company that is a job title invents an
        # employer, while "Amazon / Software Development Engineer" is what the
        # message actually said once the sender is consulted.
        split_title = split_title or company
        company = ''
    if not company:
        company, parser = _first_match(_BODY_COMPANY, body, 'company', body_clean)
        if company and _looks_like_job_title(company):
            split_title = split_title or company
            company = ''
    if not company and html_text:
        company, parser = _first_match(_BODY_COMPANY, html_text, 'company', body_clean)
        if company and _looks_like_job_title(company):
            split_title = split_title or company
            company = ''
    if not company:
        # Last resort, and only for mail the employer sent itself. This runs
        # after every pattern precisely because a named company beats an
        # inferred one: "applying to Samsara" via greenhouse-mail.io must stay
        # Samsara, and would become nothing here.
        if _CONFIRMATION_PHRASE.search(subject) or _CONFIRMATION_PHRASE.search(body[:2000]):
            company = company_from_sender(sender_email)
            parser = 'sender-domain' if company else parser
    if not company:
        return None

    title, _ = _first_match(_SUBJECT_TITLE, subject, 'title')
    if not title:
        title = split_title
    if not title:
        title, _ = _first_match(_SUBJECT_COMPANY, subject, 'title')
    if not title:
        title = _linkedin_title(body)
    if not title:
        title, _ = _first_match(_BODY_TITLE, body, 'title')
    if not title and html_text:
        title, _ = _first_match(_BODY_TITLE, html_text, 'title')

    return ParsedConfirmation(company=company, title=title, parser=parser)


def is_unresolved_indeed(subject: str, body_html: str) -> bool:
    """An Indeed confirmation whose employer is only in an HTML part we lack.

    Separated from the ordinary "could not parse" count because the fix is
    different and known: these are recoverable by re-fetching the message body
    from the provider, not by writing a better pattern.
    """
    if not re.match(r'^Indeed Application:\s*\S', (subject or '').strip(), re.I):
        return False
    return not (body_html or '').strip()
