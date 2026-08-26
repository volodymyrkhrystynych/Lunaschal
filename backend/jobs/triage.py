"""The cheap half of feed triage: what can be decided without a model.

The board adapters pull *every* open posting on a board — a company with 400
openings puts 400 rows in `jobs`, janitorial and legal and warehouse included.
Nothing downstream filters: `keywords.py` scores the body and the feed sorts by
that score, so the triage screen has always been a whole job board with an
ordering applied.

This module is the first of two layers that fix that. It answers only the
question a regex can answer honestly — "is this unambiguously not a software
role?" — and hands everything else to `backend/ai/job_triage.py`, which reads
the posting properly.

**It is exclusion-only, and that is the whole design.** An inclusion whitelist
for "developer" and "AI" would drop exactly the tangential roles worth seeing:
Forward Deployed Engineer, Solutions Architect, Research Scientist, Developer
Advocate, Technical Program Manager. An exclusion list of `registered nurse`
and `forklift operator` drops none of them. So the gate **fails open** — it
rejects only the unmistakable, and anything it is unsure about survives to the
layer qualified to judge it. Same instinct as `urlmatch.py` resolving ambiguity
to None: the cheap layer never gets to make the irreversible call.

The gate runs on the **title alone**, before any body is fetched, because that
is all that is reliably available at that point for an Adzuna row. The model
never judges on the title alone — a title is a weak signal ("Member of
Technical Staff" says nothing), and title-only judgement is precisely how a
tangential role gets thrown away.

Pure: no DB, no network, no model, no clock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Three tiers, checked in this order: a conclusive phrase, then a software
# signal that can rescue an ambiguous one, then the ambiguous phrases
# themselves. The middle tier is what keeps "Clinical Data Scientist" and
# "Warehouse Automation Engineer"; the first tier is what stops it rescuing
# "Security Guard" on the word 'security'.

# Conclusive: no software signal redeems these.
#
# The entry bar is narrow and specific — a phrase belongs here **only when the
# phrase itself collides with a software signal**, so that leaving it in the
# soft tier would let the collision rescue it. "Security Guard" contains
# 'security'; "Data Entry Clerk" contains 'data'; "Technical Recruiter" and
# "Product Marketing Manager" put the signal in the surrounding title.
#
# Everything else belongs in SOFT_EXCLUDED, because a marketing or trades title
# has no software signal to be rescued by in the first place. Getting this
# wrong is not theoretical: 'seo' sat here briefly and rejected "Sr. Full Stack
# Engineer (SEO)", a posting the user had actually applied to.
HARD_EXCLUDED = frozenset({
    'security guard', 'data entry clerk', 'recruiter', 'talent acquisition',
    'marketing manager', 'marketing coordinator', 'marketing specialist',
    'marketing analyst', 'product marketing', 'office administrator',
    'payroll administrator',
})

# A software-adjacent word rescues an *ambiguous* title from the soft list
# below. Hybrid roles are real and usually the interesting ones — "Clinical
# Data Scientist", "Healthcare Platform Engineer", "Sales Engineer" — and each
# contains a word the soft list would otherwise catch.
#
# This set only ever *overrides* a soft exclusion. A title matching nothing at
# all is kept regardless, so there is no need to enumerate every software term
# here — only the ones that need to win an argument.
SOFTWARE_SIGNALS = frozenset({
    'engineer', 'engineering', 'developer', 'development', 'software',
    'programmer', 'data', 'database', 'machine learning', 'ml', 'ai',
    'artificial intelligence', 'llm', 'nlp', 'devops', 'sre', 'architect',
    'scientist', 'infrastructure', 'platform', 'backend', 'back end',
    'frontend', 'front end', 'fullstack', 'full stack', 'ios', 'android',
    'cloud', 'cyber', 'technical', 'technology', 'computer', 'systems',
    'api', 'robotics', 'firmware', 'embedded', 'python', 'java',
    'javascript', 'sysadmin', 'qa', 'sdet', 'ux', 'ui', 'product', 'web',
    'mobile', 'security', 'it', 'tech', 'analytics', 'automation',
    'integration', 'blockchain', 'research', 'quantitative', 'algorithm',
    'system', 'solutions', 'saas', 'salesforce', 'developer advocate',
})

# Ambiguous: usually not a software role, but a software signal in the same
# title flips it. "Meta Strategist" goes; "Technology Strategist" stays.
SOFT_EXCLUDED = frozenset({
    # marketing and sales — the category that actually floods a developer's
    # feed, since the companies worth following advertise these on the same
    # board. None of these carries a software signal, so soft is enough: a
    # title that *does* carry one ("Full Stack Engineer (SEO)") is kept.
    'strategist', 'designer', 'seo', 'sem', 'ppc', 'paid social', 'paid media',
    'paid search', 'demand generation', 'digital marketing', 'social media',
    'media buyer', 'content strategist', 'copywriter', 'brand manager',
    'public relations', 'email marketing', 'growth marketing',
    'communications manager', 'campaign manager', 'community manager',
    'account executive', 'account strategist', 'account director',
    'sales representative', 'sales associate', 'inside sales',
    'business development representative', 'territory manager',
    'graphic designer', 'motion designer', 'motion graphic', 'art director',
    'illustrator', 'video editor',
    'human resources', 'hr generalist', 'financial analyst', 'accountant',
    'controller', 'bookkeeper', 'paralegal', 'legal counsel',
    # healthcare
    'registered nurse', 'nurse practitioner', 'licensed practical nurse',
    'personal support worker', 'dental hygienist', 'dental assistant',
    'pharmacist', 'pharmacy assistant', 'physiotherapist', 'physiotherapy',
    'occupational therapist', 'phlebotomist', 'veterinary', 'veterinarian',
    'caregiver', 'care aide', 'midwife', 'paramedic', 'chiropractor',
    'massage therapist', 'optometrist', 'dietitian', 'nursing',
    # food service
    'line cook', 'sous chef', 'executive chef', 'prep cook', 'dishwasher',
    'barista', 'bartender', 'busser', 'food service', 'kitchen helper',
    'restaurant manager', 'restaurant server', 'food server', 'waitress',
    'fast food', 'catering',
    # retail and hospitality
    'cashier', 'retail associate', 'store manager', 'housekeeper',
    'housekeeping', 'front desk agent', 'concierge', 'janitor', 'custodian',
    'room attendant', 'valet', 'flight attendant', 'hair stylist', 'barber',
    'esthetician', 'nail technician',
    # trades and industrial
    'welder', 'machinist', 'electrician', 'plumber', 'carpenter',
    'hvac technician', 'millwright', 'forklift operator', 'warehouse associate',
    'warehouse worker', 'general labourer', 'general laborer', 'labourer',
    'production worker', 'assembler', 'machine operator', 'heavy equipment',
    'construction worker', 'roofer', 'painter', 'landscaper', 'groundskeeper',
    'sheet metal', 'cnc operator', 'boilermaker', 'pipefitter',
    # driving and logistics
    'truck driver', 'delivery driver', 'class 1 driver', 'az driver',
    'dz driver', 'transport driver', 'bus driver', 'courier', 'shunt driver',
    'order picker', 'picker packer', 'material handler',
    # education and care
    'early childhood educator', 'teaching assistant', 'daycare',
    'child care worker', 'social worker', 'youth worker',
    # security and clerical
    'loss prevention', 'correctional officer', 'accounts payable',
    'accounts receivable', 'receptionist', 'administrative assistant',
    'executive assistant', 'office manager',
    # other
    'real estate agent', 'insurance broker', 'mortgage broker',
    'travel consultant', 'funeral', 'farm worker', 'fisher',
})


def _phrase_re(terms) -> re.Pattern:
    """One alternation over `terms`, longest first.

    Longest first because Python alternation is leftmost-then-first-alternative
    and the matched phrase is shown to the user as the reason — a title hitting
    both 'nurse practitioner' and 'nursing' should report the specific one.
    """
    return re.compile(
        r'(?<![a-z0-9])(?:'
        + '|'.join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        + r')(?![a-z0-9])'
    )


_HARD_RE = _phrase_re(HARD_EXCLUDED)
_SOFT_RE = _phrase_re(SOFT_EXCLUDED)
_SIGNAL_RE = _phrase_re(SOFTWARE_SIGNALS)

# Seniority as the title states it, most senior first — "Senior Staff Engineer"
# is staff, not senior. `associate` is deliberately absent: it means junior at a
# startup and mid-to-senior at a bank, and guessing wrong in either direction is
# worse than reporting 'unclear'.
_SENIORITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ('principal', r'\bprincipal\b'),
    ('staff', r'\bstaff\b'),
    ('lead', r'\b(?:lead|leader)\b'),
    ('senior', r'\b(?:senior|snr|sr)\b'),
    ('junior', r'\b(?:junior|jnr|jr|entry level|entry|graduate|new grad|trainee)\b'),
    ('intern', r'\b(?:intern|internship|co op|coop|practicum)\b'),
)

# "5 years", "5+ years", "3-5 years", "3 to 5 years" — all of which reach this
# already normalized, so the `+` and the dash are gone and the separator is a
# bare space. Matching the punctuation here instead would silently never fire.
#
# The lower bound is the requirement: a posting asking for 3-5 reads 3.
_YEARS_RE = re.compile(r'(\d{1,2})\s+(?:to\s+)?(?:(\d{1,2})\s+)?years?\b')

# A number of years only counts as a *requirement* when experience is what is
# being counted. Without this, "10 years of company history" and "5 years
# running" become hiring bars.
_EXPERIENCE_NEAR = re.compile(
    r'\b(?:experience|exp|background|working|industry|professional|relevant|'
    r'hands on|track record|expertise|practice|building|developing)\b'
)

# How far past "N years" to look for the word that makes it a requirement.
_EXPERIENCE_WINDOW = 40

# A junior-titled posting demanding at least this many years is the mismatch
# worth flagging. Four is a stretch for a new grad but not absurd; five is the
# point at which the title and the body are describing different jobs.
MISMATCH_YEARS = 5


def normalize(text: str) -> str:
    """Lowercase, punctuation collapsed to single spaces.

    Deliberately simpler than `keywords._normalize`, which preserves `+`, `#`
    and `/` because they distinguish 'c++' from 'c'. Job titles have no such
    terms, and keeping the punctuation here would stop 'co-op' matching 'co op'.
    """
    lowered = (text or '').lower()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', lowered).split())


@dataclass(frozen=True)
class GateResult:
    """Whether a posting is worth spending a model call on.

    `reason` is empty when kept and carries the matched phrase when rejected,
    because a filter that discards job opportunities has to be able to say why
    — see the filtered list in the feed.
    """
    keep: bool
    reason: str = ''


def gate(title: str) -> GateResult:
    """Reject a posting on its title alone, or pass it on. Fails open.

    Three tiers, and the order is the design:

    1. A **hard** phrase names the whole job and settles it — "Security Guard"
       is not rescued by the word 'security'.
    2. A **software signal** rescues anything the soft list would have caught.
       "Clinical Data Scientist" and "Warehouse Automation Engineer" are real
       jobs, and both are lost without this step.
    3. A **soft** phrase rejects only what step 2 did not vouch for.

    A title matching none of the three is kept. That is the common case and the
    intended one: not recognising a title is not evidence against it.
    """
    normalized = normalize(title)
    if not normalized:
        # No title is not evidence of irrelevance. `upsert_job` already refuses
        # a posting with no title, so this is only reachable for the backfilled
        # rows, whose title the confirmation email never stated.
        return GateResult(keep=True)

    hard = _HARD_RE.search(normalized)
    if hard:
        return GateResult(keep=False, reason=hard.group(0))

    if _SIGNAL_RE.search(normalized):
        return GateResult(keep=True)

    soft = _SOFT_RE.search(normalized)
    if soft:
        return GateResult(keep=False, reason=soft.group(0))

    return GateResult(keep=True)


def stated_seniority(title: str) -> str:
    """The seniority the title claims, or '' when it does not say."""
    normalized = normalize(title)
    for level, pattern in _SENIORITY_PATTERNS:
        if re.search(pattern, normalized):
            return level
    return ''


def years_required(text: str) -> int | None:
    """The largest number of years of *experience* the posting asks for.

    Largest, not first: a posting wanting "2+ years of Python and 8+ years of
    engineering" has an eight-year bar, and the first number understates it.
    Each mention contributes its lower bound, so "3-5 years" counts as 3.

    None when the posting never states one, which is most of them.
    """
    haystack = normalize(text)
    best: int | None = None
    for match in _YEARS_RE.finditer(haystack):
        tail = haystack[match.end():match.end() + _EXPERIENCE_WINDOW]
        if not _EXPERIENCE_NEAR.search(tail):
            continue
        lower = int(match.group(1))
        if best is None or lower > best:
            best = lower
    return best


@dataclass(frozen=True)
class PostingFacts:
    """What the posting says about itself, computed rather than inferred.

    Handed to the model as fact, the way `keywords.py`'s report already is, so
    the model spends its judgement on what needs judging instead of re-deriving
    something a regex gets exactly right.
    """
    seniority: str = ''
    years_required: int | None = None
    seniority_mismatch: bool = False

    def to_dict(self) -> dict:
        return {
            'seniority': self.seniority,
            'yearsRequired': self.years_required,
            'seniorityMismatch': self.seniority_mismatch,
        }


def posting_facts(title: str, description: str) -> PostingFacts:
    """Seniority, years demanded, and whether those two disagree.

    The mismatch is the flag this feature was asked for by name: a posting
    titled "Junior Developer" whose body wants ten years is not a junior role,
    and noticing that is worth a regex rather than a paragraph of model output.
    """
    seniority = stated_seniority(title)
    years = years_required(description)
    mismatch = (
        seniority in ('junior', 'intern')
        and years is not None
        and years >= MISMATCH_YEARS
    )
    return PostingFacts(
        seniority=seniority, years_required=years, seniority_mismatch=mismatch
    )
