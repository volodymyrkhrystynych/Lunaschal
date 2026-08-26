"""The pure triage gate. No model, no DB — every answer here is exact.

The cases that matter most are the ones that must *survive*: this gate decides
what the model never gets to look at, so a false rejection is a job the user
never hears about. Several of these are drawn from the live database, where the
first version of the gate rejected postings the user had actually applied to.
"""
from backend.jobs import triage


# --------------------------------------------------------------------------
# The gate: what it rejects
# --------------------------------------------------------------------------

def test_rejects_unambiguous_non_software_titles():
    for title in (
        'Line Cook',
        'Registered Nurse',
        'Forklift Operator',
        'Class 1 Driver',
        'Dental Hygienist',
        'Security Guard (Part Time)',
    ):
        assert triage.gate(title).keep is False, title


def test_rejects_the_marketing_titles_that_actually_flood_the_feed():
    """The live Ashby board turned out to be a marketing agency.

    Blue-collar exclusions caught none of its 74 postings; this cluster is what
    a software job seeker's feed is really polluted by, because the companies
    worth following advertise these roles on the same board.
    """
    for title in (
        'Paid Social Strategist (Remote US)',
        'Senior Digital Marketing Strategist -Startups/SMB (Remote Canada)',
        'SEO Strategist (Remote UK) - Future Opening',
        'Demand Generation Manager (Remote US)',
        'Content Strategist (Remote Canada) - Future Opening',
        'B2B Marketing Manager(Remote US)',
        'PR & Communications Manager (Remote US)',
        'Account Director, Client Strategy (Remote Canada)',
        'Motion & Graphic Designer (Remote US)',
        'Meta Strategist (Remote Canada)',
        'Controller (Remote US)',
    ):
        assert triage.gate(title).keep is False, title


def test_rejection_carries_the_phrase_that_caused_it():
    """A filter that discards opportunities has to be able to say why."""
    result = triage.gate('Senior Paid Social Strategist (Remote US)')
    assert result.keep is False
    assert result.reason == 'paid social'


def test_reason_prefers_the_more_specific_phrase():
    assert triage.gate('Nurse Practitioner').reason == 'nurse practitioner'


# --------------------------------------------------------------------------
# The gate: what must survive — the expensive direction to get wrong
# --------------------------------------------------------------------------

def test_tangential_software_titles_survive():
    """The reason the gate is exclusion-only rather than a whitelist.

    An inclusion list for 'developer' and 'AI' drops every one of these, and
    they are exactly the roles worth seeing.
    """
    for title in (
        'Forward Deployed Engineer',
        'Solutions Architect',
        'Research Scientist',
        'Developer Advocate',
        'Technical Program Manager',
        'Member of Technical Staff',
        'Site Reliability Engineer',
        'Business Analyst',
    ):
        assert triage.gate(title).keep is True, title


def test_a_software_signal_rescues_an_ambiguous_title():
    """Hybrid roles are real, and usually the interesting ones."""
    for title in (
        'Clinical Data Scientist',
        'Warehouse Automation Engineer',
        'Sales Engineer',
        'Technology Strategist',
        'Security Engineer',
        'Motor Controller Engineer',
    ):
        assert triage.gate(title).keep is True, title


def test_signal_inside_the_excluded_phrase_does_not_rescue_it():
    """Why the hard tier exists at all.

    'security' and 'data' are software signals, so rescue-first alone would
    keep both of these.
    """
    assert triage.gate('Security Guard').keep is False
    assert triage.gate('Data Entry Clerk').keep is False
    assert triage.gate('Technical Recruiter').keep is False
    assert triage.gate('Product Marketing Manager').keep is False


def test_engineering_role_in_a_marketing_domain_survives():
    """Regression: 'seo' was briefly a hard exclusion and rejected this.

    It is a posting the user had actually applied to. A domain word qualifying
    an engineering title must never outrank the engineering title.
    """
    assert triage.gate('Sr. Full Stack Engineer (SEO)').keep is True


def test_designer_titles_split_on_the_signal():
    """Regression, also from the live applications table: 'System Designer'
    and 'Solutions Designer' were rejected on the bare word 'designer'."""
    assert triage.gate('System Designer').keep is True
    assert triage.gate('Solutions Designer - Intermediate').keep is True
    assert triage.gate('Product Designer').keep is True
    assert triage.gate('UX Designer').keep is True
    assert triage.gate('Graphic Designer').keep is False


def test_an_unrecognised_title_is_kept():
    """Not recognising a title is not evidence against it."""
    assert triage.gate('Widget Wrangler, Level III').keep is True


def test_an_empty_title_is_kept():
    """The backfilled rows often have none — the confirmation mail never said."""
    assert triage.gate('').keep is True
    assert triage.gate('   ').keep is True


def test_substring_collisions_do_not_reject():
    """Phrases match on word boundaries, so a longer word is not a hit."""
    assert triage.gate('Driver Development Engineer').keep is True
    assert triage.gate('Barbershop Platform Engineer').keep is True


# --------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------

def test_normalize_collapses_punctuation_and_case():
    assert triage.normalize('Sr. Full-Stack Engineer  (SEO)') == 'sr full stack engineer seo'
    assert triage.normalize('Co-Op / Intern') == 'co op intern'
    assert triage.normalize(None) == ''


# --------------------------------------------------------------------------
# Seniority
# --------------------------------------------------------------------------

def test_stated_seniority_reads_the_title():
    assert triage.stated_seniority('Senior Software Engineer') == 'senior'
    assert triage.stated_seniority('Sr. Backend Developer') == 'senior'
    assert triage.stated_seniority('Junior Developer') == 'junior'
    assert triage.stated_seniority('Entry Level Analyst') == 'junior'
    assert triage.stated_seniority('Software Engineering Intern') == 'intern'
    assert triage.stated_seniority('Staff Engineer') == 'staff'
    assert triage.stated_seniority('Principal Engineer') == 'principal'


def test_most_senior_wins_when_a_title_says_two_things():
    assert triage.stated_seniority('Senior Staff Engineer') == 'staff'
    assert triage.stated_seniority('Principal Staff Engineer') == 'principal'


def test_seniority_is_blank_when_the_title_does_not_say():
    assert triage.stated_seniority('Software Engineer') == ''
    # 'associate' means junior at a startup and mid-to-senior at a bank.
    # Reporting nothing is better than guessing wrong in either direction.
    assert triage.stated_seniority('Associate Engineer') == ''


# --------------------------------------------------------------------------
# Years of experience
# --------------------------------------------------------------------------

def test_years_required_reads_a_plain_requirement():
    assert triage.years_required('You have 5+ years of experience in Python.') == 5


def test_a_range_counts_as_its_lower_bound():
    assert triage.years_required('3-5 years of professional experience') == 3
    assert triage.years_required('3 to 5 years experience required') == 3


def test_the_largest_requirement_wins():
    """A posting wanting 2 years of Python and 8 of engineering has an 8y bar,
    and the first number stated understates it."""
    text = '2+ years of Python experience and 8+ years of engineering experience'
    assert triage.years_required(text) == 8


def test_years_not_about_experience_are_ignored():
    """Without this, a company's own age becomes a hiring bar."""
    assert triage.years_required('We have been in business for 30 years.') is None
    assert triage.years_required('Our 15 years of growth continue.') is None


def test_years_required_is_none_when_unstated():
    assert triage.years_required('We want a great engineer.') is None
    assert triage.years_required('') is None


# --------------------------------------------------------------------------
# The mismatch flag — the one this feature was asked for by name
# --------------------------------------------------------------------------

def test_junior_title_demanding_long_experience_is_flagged():
    facts = triage.posting_facts(
        'Junior Software Developer',
        'The ideal candidate brings 10+ years of hands on experience.',
    )
    assert facts.seniority == 'junior'
    assert facts.years_required == 10
    assert facts.seniority_mismatch is True


def test_a_senior_title_demanding_long_experience_is_not_a_mismatch():
    facts = triage.posting_facts(
        'Senior Software Developer',
        'The ideal candidate brings 10+ years of hands on experience.',
    )
    assert facts.seniority_mismatch is False


def test_an_intern_posting_wanting_years_is_flagged():
    facts = triage.posting_facts(
        'Software Engineering Intern',
        'Requires 6 years of professional experience.',
    )
    assert facts.seniority_mismatch is True


def test_a_junior_posting_asking_for_little_is_not_flagged():
    facts = triage.posting_facts(
        'Junior Developer', '1-2 years of experience preferred.'
    )
    assert facts.years_required == 1
    assert facts.seniority_mismatch is False


def test_no_stated_years_is_never_a_mismatch():
    facts = triage.posting_facts('Junior Developer', 'Come learn with us.')
    assert facts.years_required is None
    assert facts.seniority_mismatch is False


def test_facts_serialize_for_the_model():
    facts = triage.posting_facts('Junior Developer', '9+ years of experience')
    assert facts.to_dict() == {
        'seniority': 'junior', 'yearsRequired': 9, 'seniorityMismatch': True,
    }
