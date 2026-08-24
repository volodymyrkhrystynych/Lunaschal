"""Reconstructing applications from confirmation mail.

The cases here are shapes taken from a real mailbox of ~1,900 confirmations,
including the ones that produced wrong answers on the first pass: a company
called "Software Developer", another called "the above position", and a
pipeline whose largest employer was Dayforce.
"""
from backend.jobs.backfill import (
    company_from_sender,
    is_unresolved_indeed,
    parse_confirmation,
)


def parse(**kw):
    return parse_confirmation(**kw)


# --- the confirmation shapes that actually arrive -----------------------------

def test_linkedin_confirmation_reads_company_from_subject_and_title_by_position():
    """LinkedIn's body is a layout, not a sentence: the role is the first line
    after the lead-in, so it is read by position rather than by pattern."""
    p = parse(
        subject='Volodymyr, your application was sent to Capston Inc',
        sender_email='jobs-noreply@linkedin.com',
        body_text=(
            'Your application was sent to Capston Inc\n'
            '\n'
            'Software Engineer\n'
            'Capston Inc\n'
            'Toronto, ON\n'
        ),
    )
    assert p is not None
    assert p.company == 'Capston Inc'
    assert p.title == 'Software Engineer'
    assert p.parser == 'linkedin'


def test_greenhouse_style_subject_names_the_company():
    p = parse(
        subject='Thank you for applying to Samsara',
        sender_email='no-reply@us.greenhouse-mail.io',
        body_text=('Dear Volodymyr,\n\nThank you for applying to the Senior '
                   'Software Engineer role and for choosing Samsara in your '
                   'job search!'),
    )
    assert p is not None
    assert p.company == 'Samsara'
    assert p.title == 'Senior Software Engineer'


def test_bamboo_style_body_supplies_the_role():
    p = parse(
        subject='Thank you for applying at Common Wealth Retirement',
        sender_email='notifications@app.bamboohr.com',
        body_text=('Thank you so much for your interest in Common Wealth '
                   'Retirement and for taking the time to apply for the '
                   'Junior Back End Engineer position.'),
    )
    assert p is not None
    assert p.company == 'Common Wealth Retirement'
    assert p.title == 'Junior Back End Engineer'


def test_indeed_takes_the_role_from_the_subject_and_company_from_html():
    """Indeed's plain-text part is contentless; only the HTML names the
    employer, which is the whole reason body_html is consulted at all."""
    p = parse(
        subject='Indeed Application: Front End Developer',
        sender_email='indeedapply@indeed.com',
        body_text='Your application has been submitted. Good luck!',
        body_html=('<div>Application submitted Front End Developer Pixel - '
                   'Toronto, ON 34 reviews The following items were sent to '
                   'Pixel. Good luck!</div>'),
    )
    assert p is not None
    assert p.company == 'Pixel'
    assert p.title == 'Front End Developer'


def test_ashby_pipe_subject_splits_company_from_role():
    p = parse(subject='Klue | Software Engineer II, AI',
              sender_email='no-reply@ashbyhq.com')
    assert p is not None
    assert p.company == 'Klue'
    assert p.title == 'Software Engineer II, AI'


# --- what must never become an application ------------------------------------

def test_a_confirmation_with_no_company_is_skipped():
    """geo.py's rule: half a fact is worse than none. An application with no
    employer cannot be linked, matched or resolved — it only pads the pipeline.
    """
    assert parse(
        subject='Indeed Application: Front End Developer',
        sender_email='indeedapply@indeed.com',
        body_text='Your application has been submitted. Good luck!',
    ) is None


def test_misclassified_mail_without_a_confirmation_phrase_is_skipped():
    """`job_status='sent'` is a model output over a mailbox of near-misses. The
    patterns are the real filter — a magic-link email says nothing about an
    application, so its sender domain must not become an employer."""
    assert parse(subject="Here's your magic link for Robinhood",
                 sender_email='noreply@mail3.guide.co') is None
    assert parse(subject='Thank you for Creating an Account!',
                 sender_email='system@successfactors.eu') is None
    assert parse(subject='Your Personalized Remote Jobs for Extra Income (Aug. 06)',
                 sender_email='newcomers_at_email@intch.org') is None


def test_a_generic_phrase_is_not_a_company():
    """"Thank you for your interest in the above position" is real confirmation
    mail that identifies no employer at all."""
    for phrase in ('the above position', 'our team', 'this opportunity'):
        assert parse(
            subject='Thank you for applying',
            sender_email='careers@example-ats.invalid',
            body_text=f'Thank you for your interest in {phrase}.',
        ) is None or parse(
            subject='Thank you for applying',
            sender_email='careers@example-ats.invalid',
            body_text=f'Thank you for your interest in {phrase}.',
        ).company != phrase


def test_a_job_title_never_becomes_the_company():
    """The first pass produced employers called "Software Developer". A title
    in the company slot invents a company that cannot exist."""
    p = parse(
        subject='Thank you for applying!',
        sender_email='noreply@recruiterflowmail.com',
        body_text='Thank you for applying to Software Developer.',
    )
    assert p is None or p.company != 'Software Developer'


def test_a_title_in_the_company_slot_becomes_the_title_when_the_sender_knows_the_company():
    """Amazon's mail names only the role in the body, but its own domain names
    the employer — so the two halves resolve to one correct row."""
    p = parse(
        subject='Keep track of your application',
        sender_email='noreply@mail.amazon.jobs',
        body_text='Thank you for your interest in Software Development Engineer.',
    )
    assert p is not None
    assert p.company == 'Amazon'
    assert p.title == 'Software Development Engineer'


def test_an_organisation_word_rescues_a_staffing_firm():
    """A recruiter really is called "Millennium Software and Staffing Inc";
    the title-word rule must not delete it."""
    p = parse(subject='Volodymyr, your application was sent to '
                      'Millennium Software and Staffing Inc',
              sender_email='jobs-noreply@linkedin.com')
    assert p is not None
    assert p.company == 'Millennium Software and Staffing Inc'


# --- run-on sentences ---------------------------------------------------------

def test_a_body_capture_stops_where_the_proper_noun_stops():
    """A body sentence runs past the name with no punctuation to cut at, so the
    company is taken as the proper-noun run."""
    p = parse(
        subject='Thank you for applying',
        sender_email='careers@lbcfg.invalid',
        body_text=('Thank you for your interest in Laurentian Bank and wish '
                   'you the best in your search.'),
    )
    assert p is not None
    assert p.company == 'Laurentian Bank'


def test_a_connector_only_survives_as_a_bridge_to_more_of_the_name():
    p = parse(
        subject='Thank you for applying',
        sender_email='careers@brightorder.invalid',
        body_text='We appreciate your interest in BrightOrder and the effort you put in.',
    )
    assert p is not None
    assert p.company == 'BrightOrder'


def test_a_lead_in_before_the_name_is_stripped():
    p = parse(
        subject='Thank you for applying',
        sender_email='careers@fitch.invalid',
        body_text='Thank you for your interest in a career with the Fitch Group.',
    )
    assert p is not None
    assert p.company == 'Fitch Group'


def test_a_subject_that_packs_the_role_after_the_company_is_split():
    p = parse(subject='Thank you for applying to Full Stack Software Developer I Role at Intuit',
              sender_email='no-reply@intuit.invalid')
    assert p is not None
    assert p.company == 'Intuit'
    assert 'Full Stack Software Developer' in p.title


def test_a_shouted_banner_after_the_company_is_cut():
    p = parse(subject='Thank you for applying to Meridian Credit Union and ACTION REQUIRED',
              sender_email='no-reply@meridian.invalid')
    assert p is not None
    assert p.company == 'Meridian Credit Union'


def test_a_lowercase_brand_from_a_subject_survives():
    """The proper-noun rule is body-only precisely so a subject pattern can
    capture a company that does not capitalise itself."""
    p = parse(subject='Thanks for applying to commonsku',
              sender_email='no-reply@commonsku.invalid')
    assert p is not None
    assert p.company == 'commonsku'


# --- the sender-domain fallback ----------------------------------------------

def test_an_ats_sender_never_becomes_the_employer():
    """The trap ATS_DOMAINS exists for: Greenhouse mails on behalf of every
    company, so its domain identifies none of them."""
    assert company_from_sender('no-reply@us.greenhouse-mail.io') == ''
    assert company_from_sender('no-reply@hire.lever.co') == ''
    assert company_from_sender('Rakuten@myworkday.com') == ''


def test_a_payroll_platform_never_becomes_the_employer():
    """Left unlisted, these produce a pipeline whose biggest employers are
    Dayforce and ADP."""
    for address in ('notify@dayforce.com', 'noreply@adp.com',
                    'no-reply@clientconnections.com',
                    'notifications@app.bamboohr.com'):
        assert company_from_sender(address) == ''


def test_a_mailbox_provider_is_not_an_employer():
    assert company_from_sender('v.khrystynych@gmail.com') == ''
    assert company_from_sender('someone@outlook.com') == ''


def test_a_company_domain_reads_as_the_company():
    assert company_from_sender('noreply@acosta.com') == 'Acosta'
    assert company_from_sender('careers@acosta.com') == 'Acosta'
    assert company_from_sender('noreply@mail.amazon.jobs') == 'Amazon'


def test_a_named_company_always_beats_an_inferred_one():
    """Samsara mails through Greenhouse. If the sender were consulted first the
    row would be attributed to nobody at all."""
    p = parse(subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io')
    assert p is not None
    assert p.company == 'Samsara'
    assert p.parser != 'sender-domain'


# --- what is recoverable, and what is not -------------------------------------

def test_an_indeed_confirmation_missing_its_html_is_reported_separately():
    """These are not parse failures — they are messages whose employer was
    never stored locally, and the fix is a re-fetch rather than a new pattern.
    """
    assert is_unresolved_indeed('Indeed Application: Front End Developer', '')
    assert not is_unresolved_indeed(
        'Indeed Application: Front End Developer', '<div>anything</div>')
    assert not is_unresolved_indeed('Thank you for applying to Samsara', '')


def test_the_company_key_collapses_legal_suffixes():
    """Two confirmations for one application must not become two rows; the key
    is what makes the backfill idempotent and the dedup correct."""
    a = parse(subject='Thanks for applying to Acme Inc.',
              sender_email='no-reply@acme.invalid')
    b = parse(subject='Thanks for applying to Acme',
              sender_email='no-reply@acme.invalid')
    assert a is not None and b is not None
    assert a.company_key == b.company_key == 'acme'


def test_parsing_is_pure_and_survives_empty_input():
    assert parse() is None
    assert parse(subject='', sender_email='', body_text='', body_html='') is None


# --- planning and committing against a database -------------------------------

import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.jobs import backfill

NOW = int(time.time())
DAY = 86400


@pytest.fixture
def account(_db=None):
    db = get_db()
    account_id = str(ULID())
    db.execute(
        'INSERT INTO email_accounts (id, provider, email_address, created_at, updated_at)'
        " VALUES (?, 'gmail', 'me@example.com', ?, ?)",
        (account_id, NOW, NOW),
    )
    db.commit()
    return account_id


def add_email(account_id, *, subject, sender_email, body='', html='',
              received_at=None, job_status='sent'):
    db = get_db()
    email_id = str(ULID())
    db.execute(
        'INSERT INTO emails (id, account_id, provider_message_id, subject, sender,'
        ' sender_email, body_text, body_html, received_at, category, job_status,'
        ' created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (email_id, account_id, str(ULID()), subject, sender_email, sender_email,
         body, html, received_at or NOW, 'job_application', job_status, NOW),
    )
    db.commit()
    return email_id


def test_plan_reports_what_it_would_create_and_writes_nothing(account):
    db = get_db()
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io')
    add_email(account, subject='Volodymyr, your application was sent to Klue',
              sender_email='jobs-noreply@linkedin.com',
              body='Your application was sent to Klue\n\nBackend Engineer\n')

    plan = backfill.plan(db)
    assert plan['toCreate'] == 2
    assert plan['companies'] == 2
    assert {i['company'] for i in plan['items']} == {'Samsara', 'Klue'}
    # A preview that writes is not a preview.
    assert db.execute('SELECT COUNT(*) c FROM applications').fetchone()['c'] == 0
    assert db.execute('SELECT COUNT(*) c FROM jobs').fetchone()['c'] == 0


def test_commit_creates_a_job_and_an_application_per_candidate(account):
    db = get_db()
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io',
              body='Thank you for applying to the Senior Engineer role at Samsara.')

    result = backfill.commit(db, applied_email='me@example.com')
    assert result['created'] == 1

    row = db.execute(
        'SELECT j.company, j.title, j.source, j.source_id, a.status, a.applied_email'
        ' FROM applications a JOIN jobs j ON j.id = a.job_id').fetchone()
    assert row['company'] == 'Samsara'
    assert row['source'] == 'manual'
    assert row['source_id'].startswith('backfill:')
    assert row['applied_email'] == 'me@example.com'


def test_a_backfilled_application_starts_below_acknowledged(account):
    """It must be created at a rank the linker can advance *from*. Writing
    'acknowledged' here would freeze every backfilled row at its starting
    status, because advance_status only ever moves forward."""
    db = get_db()
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io')
    backfill.commit(db)

    from backend.jobs.linkage import PROGRESS_RANK, advance_status
    status = db.execute('SELECT status FROM applications').fetchone()['status']
    assert status == 'submitted'
    assert PROGRESS_RANK[status] < PROGRESS_RANK['acknowledged']
    assert advance_status(status, 'rejection') == 'rejected'
    assert advance_status(status, 'interview_next_step') == 'interview'


def test_committing_twice_creates_nothing_the_second_time(account):
    """UNIQUE(source, source_id) is the idempotency, so an interrupted run
    resumes instead of doubling the pipeline."""
    db = get_db()
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io')

    assert backfill.commit(db)['created'] == 1
    second = backfill.commit(db)
    assert second['created'] == 0
    assert second['alreadyPresent'] == 1
    assert db.execute('SELECT COUNT(*) c FROM applications').fetchone()['c'] == 1


def test_two_confirmations_for_one_application_collapse(account):
    """An ATS acknowledgement and the board's own copy describe one
    application, not two."""
    db = get_db()
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io',
              body='for the Senior Engineer role', received_at=NOW - 5 * DAY)
    add_email(account, subject='Thanks for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io',
              body='for the Senior Engineer position', received_at=NOW)

    assert backfill.commit(db)['created'] == 1


def test_applied_at_is_the_earliest_confirmation(account):
    """A resend is not a second application and must not redate the first."""
    db = get_db()
    first = NOW - 30 * DAY
    add_email(account, subject='Thanks for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io',
              body='for the Senior Engineer role', received_at=NOW)
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io',
              body='for the Senior Engineer position', received_at=first)

    backfill.commit(db)
    assert db.execute('SELECT applied_at FROM applications').fetchone()['applied_at'] == first


def test_two_roles_at_one_company_are_two_applications(account):
    db = get_db()
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io',
              body='for the Senior Engineer role')
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io',
              body='for the Data Engineer role')

    assert backfill.commit(db)['created'] == 2


def test_unparseable_mail_creates_nothing_but_is_counted(account):
    db = get_db()
    add_email(account, subject="Here's your magic link for Robinhood",
              sender_email='noreply@mail3.guide.co')
    add_email(account, subject='Indeed Application: Front End Developer',
              sender_email='indeedapply@indeed.com',
              body='Your application has been submitted. Good luck!')

    plan = backfill.plan(db)
    assert plan['toCreate'] == 0
    assert plan['skipped'] == 2
    assert plan['unresolved_indeed'] == 1


def test_commit_reopens_the_scan_verdicts_the_new_rows_invalidate(account):
    """Every rejection in the mailbox has already been walked and dismissed
    against an empty application table. If those verdicts stand, the backfilled
    pipeline never advances past 'submitted'."""
    db = get_db()
    old = NOW - 200 * DAY
    add_email(account, subject='Thank you for applying to Samsara',
              sender_email='no-reply@us.greenhouse-mail.io', received_at=old)
    rejection = add_email(
        account, subject='Your application to Samsara',
        sender_email='no-reply@us.greenhouse-mail.io',
        received_at=old + DAY, job_status='rejection')

    # The linker already considered that rejection and found nothing to link.
    db.execute(
        'INSERT INTO job_email_scans (email_id, matched, scanned_at)'
        ' VALUES (?,0,?)', (rejection, old + DAY))
    db.commit()

    result = backfill.commit(db)
    assert result['created'] == 1
    assert result['reopenedScans'] >= 1
    assert db.execute(
        'SELECT COUNT(*) c FROM job_email_scans WHERE email_id=?',
        (rejection,)).fetchone()['c'] == 0


def test_a_bare_industry_word_is_not_a_company():
    """The first live run created an application at a company called
    "Software". Because linkage matches on the company name, that one bogus row
    absorbed 144 email links belonging to real employers — a poisoned record,
    not merely a missing one."""
    for name in ('Software', 'Recruiting', 'Confidential', 'Indeed',
                 'Management Inc', 'Canada Inc', 'Technologies'):
        p = parse(subject=f'Thank you for applying to {name}',
                  sender_email='no-reply@example-ats.invalid')
        assert p is None or p.company != name, f'{name} became a company'


def test_a_real_name_containing_an_industry_word_survives():
    """The rule is 'the whole name is generic', not 'contains a generic word'."""
    for name in ('ISG Search Inc', 'Score Media and Gaming Inc',
                 'Millennium Software and Staffing Inc', 'Atlantis IT'):
        p = parse(subject=f'Thank you for applying to {name}',
                  sender_email='no-reply@example-ats.invalid')
        assert p is not None and p.company == name, f'{name} was rejected'
