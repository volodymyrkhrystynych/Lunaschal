"""Pure linkage scoring and status advance. No DB, no network, no model."""
import time

from backend.jobs import linkage
from backend.jobs.linkage import ApplicationFacts, EmailFacts

DAY = 86400
NOW = int(time.time())


def app(**kwargs) -> ApplicationFacts:
    defaults = {
        'application_id': 'app-1',
        'company': 'Acme',
        'title': 'Backend Engineer',
        'job_url': 'https://acme.com/careers/backend',
        'applied_email': 'me@example.com',
        'applied_at': NOW - 7 * DAY,
    }
    return ApplicationFacts(**{**defaults, **kwargs})


def email(**kwargs) -> EmailFacts:
    defaults = {
        'sender_email': 'careers@acme.com',
        'subject': 'Your application to Acme',
        'body_text': '',
        'received_at': NOW,
    }
    return EmailFacts(**{**defaults, **kwargs})


# --- normalization -------------------------------------------------------

def test_normalize_company_strips_legal_suffixes():
    assert linkage.normalize_company('Acme Inc.') == 'acme'
    assert linkage.normalize_company('Acme Technologies Ltd') == 'acme technologies'
    assert linkage.normalize_company('  ACME,  LLC ') == 'acme'


def test_normalize_company_keeps_meaningful_words():
    # 'Technologies' is not a legal form and distinguishes real companies.
    assert linkage.normalize_company('Acme Technologies') == 'acme technologies'


def test_same_site_matches_subdomains_either_way():
    assert linkage.same_site('careers.acme.com', 'acme.com')
    assert linkage.same_site('acme.com', 'careers.acme.com')
    assert not linkage.same_site('acme.com', 'notacme.com')
    assert not linkage.same_site('', 'acme.com')


def test_domain_helpers():
    assert linkage.domain_of_email('No Reply <bot@Acme.com>') == 'acme.com'
    assert linkage.domain_of_url('https://WWW.Acme.com/jobs/1') == 'acme.com'


# --- the ATS fallthrough, the case this module exists for ----------------

def test_ats_sender_earns_no_domain_credit():
    """Two Greenhouse applications must not tie on the sender domain."""
    acme = app(application_id='acme', company='Acme',
               job_url='https://boards.greenhouse.io/acme/jobs/1')
    globex = app(application_id='globex', company='Globex',
                 job_url='https://boards.greenhouse.io/globex/jobs/2')

    message = email(
        sender_email='no-reply@greenhouse.io',
        subject='Your application to Acme',
    )
    top, confident = linkage.best_match(message, [acme, globex])

    assert top.application_id == 'acme'
    # Globex scores nothing at all, so the winner is not merely ahead.
    globex_score = next(
        s.score for s in [linkage.score_link(message, globex)]
    )
    assert globex_score == 0.0
    assert confident


def test_ats_email_naming_two_of_your_employers_is_not_confident():
    """Uniqueness is what makes the name decisive — so ambiguity removes it."""
    acme = app(application_id='acme', company='Acme',
               job_url='https://boards.greenhouse.io/acme/jobs/1', title='Chef')
    globex = app(application_id='globex', company='Globex',
                 job_url='https://boards.greenhouse.io/globex/jobs/2', title='Chef')

    message = email(
        sender_email='no-reply@greenhouse.io',
        subject='Updates on your Acme and Globex applications',
    )
    top, confident = linkage.best_match(message, [acme, globex])
    assert top is not None
    assert not confident


def test_low_scoring_match_without_a_company_name_stays_a_suggestion():
    """The uniqueness rule is about names, not about lowering the bar."""
    message = email(
        sender_email='no-reply@greenhouse.io',
        subject='Backend Engineer — next steps',
    )
    top, confident = linkage.best_match(message, [app()])
    assert top.score < linkage.AUTO_LINK_THRESHOLD
    assert not confident


def test_ats_domains_recognised_including_subdomains():
    assert linkage.is_ats_domain('greenhouse.io')
    assert linkage.is_ats_domain('mail.hire.lever.co')
    assert linkage.is_ats_domain('us.greenhouse-mail.io')
    assert not linkage.is_ats_domain('acme.com')


def test_employer_domain_alone_is_enough_to_auto_link():
    message = email(sender_email='recruiting@careers.acme.com', subject='Hello')
    top, confident = linkage.best_match(message, [app()])
    assert confident
    assert top.score >= linkage.AUTO_LINK_THRESHOLD


# --- guards ---------------------------------------------------------------

def test_email_before_the_application_never_matches():
    message = email(received_at=NOW - 30 * DAY)
    assert linkage.score_link(message, app()).score == 0.0


def test_email_slightly_before_applied_at_still_matches():
    """The confirmation often beats the user writing down that they applied."""
    message = email(received_at=NOW - 7 * DAY - 3600)
    assert linkage.score_link(message, app()).score > 0


def test_unsubmitted_application_never_matches():
    assert linkage.score_link(email(), app(applied_at=None)).score == 0.0


def test_very_old_application_never_matches():
    old = app(applied_at=NOW - 500 * DAY)
    assert linkage.score_link(email(), old).score == 0.0


# --- title overlap --------------------------------------------------------

def test_title_overlap_is_partial_not_exact():
    overlap = linkage.title_overlap(
        'Senior Backend Engineer, Payments',
        'Your application to Acme — Backend Engineer',
    )
    assert 0 < overlap < 1


def test_title_overlap_ignores_generic_words():
    # 'position'/'remote' are stopwords, so they cannot inflate the score.
    assert linkage.title_overlap('Remote Position', 'a remote position') == 0.0


def test_score_is_capped_at_one():
    message = email(
        sender_email='careers@acme.com',
        subject='Acme — Backend Engineer',
        body_text='Acme Backend Engineer',
    )
    assert linkage.score_link(message, app()).score <= 1.0


# --- ambiguity ------------------------------------------------------------

def test_two_close_candidates_block_auto_linking():
    """Two applications to the same company is exactly when not to guess."""
    first = app(application_id='a', title='Backend Engineer')
    second = app(application_id='b', title='Backend Engineer')
    message = email(sender_email='careers@acme.com', subject='Acme update')

    top, confident = linkage.best_match(message, [first, second])
    assert top is not None
    assert not confident


def test_no_candidates_returns_nothing():
    top, confident = linkage.best_match(email(), [])
    assert top is None
    assert not confident


def test_rank_candidates_drops_zero_scores():
    unrelated = app(application_id='other', company='Globex',
                    job_url='https://globex.com/j/1', title='Chef')
    ranked = linkage.rank_candidates(email(), [app(), unrelated])
    assert [r.application_id for r in ranked] == ['app-1']


# --- status advance -------------------------------------------------------

def test_rejection_sets_rejected():
    assert linkage.advance_status('submitted', 'rejection') == 'rejected'


def test_stale_acknowledgment_cannot_demote_an_interview():
    assert linkage.advance_status('interview', 'sent') is None


def test_nothing_reopens_a_rejection_automatically():
    assert linkage.advance_status('rejected', 'sent') is None
    assert linkage.advance_status('rejected', 'interview_next_step') is None


def test_rejection_after_an_offer_is_ignored():
    assert linkage.advance_status('offer', 'rejection') is None


def test_withdrawn_is_never_overwritten():
    for status in ('sent', 'rejection', 'interview_next_step'):
        assert linkage.advance_status('withdrawn', status) is None


def test_other_update_never_changes_status():
    assert linkage.advance_status('submitted', 'other_update') is None
    assert linkage.advance_status('submitted', None) is None


def test_forward_moves_are_allowed():
    assert linkage.advance_status('submitted', 'sent') == 'acknowledged'
    assert linkage.advance_status('acknowledged', 'interview_next_step') == 'interview'


def test_ghosted_reopens_on_a_reply():
    assert linkage.advance_status('ghosted', 'interview_next_step') == 'interview'
    assert linkage.advance_status('ghosted', 'sent') == 'acknowledged'


def test_same_status_is_not_a_change():
    assert linkage.advance_status('interview', 'interview_next_step') is None
