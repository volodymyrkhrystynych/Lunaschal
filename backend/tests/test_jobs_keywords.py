"""Deterministic keyword gap. Pure — no model, so the answers are exact."""
from backend.jobs import keywords as kw

JD = """
We are hiring a Backend Engineer. You will build services in Python and Go,
deploy them on Kubernetes, and own our PostgreSQL schemas. Experience with
Terraform is required. Kubernetes experience is essential; we run Kubernetes
across three regions.
"""


def test_matched_and_missing_are_split_by_the_profile():
    report = kw.keyword_report(
        JD,
        profile_text='I write Python services backed by PostgreSQL.',
        profile_skills=['Python', 'PostgreSQL'],
    )
    assert 'python' in report.matched
    assert 'postgresql' in report.matched
    assert 'kubernetes' in report.missing
    assert 'terraform' in report.missing
    assert 'go' in report.missing


def test_missing_is_ordered_by_how_much_the_posting_stresses_it():
    """Kubernetes appears three times; Terraform once."""
    report = kw.keyword_report(JD, profile_text='', profile_skills=[])
    assert report.missing.index('kubernetes') < report.missing.index('terraform')


def test_a_bullet_counts_as_evidence_without_a_skills_entry():
    """Doing the work is evidence, whether or not it was typed into a list."""
    report = kw.keyword_report(
        'We need Kubernetes experience.',
        profile_text='Led the Kubernetes migration for our checkout service.',
        profile_skills=[],
    )
    assert report.matched == ['kubernetes']
    assert report.missing == []


def test_vocabulary_grows_with_the_users_own_skills():
    """Elixir is not in BASE_TERMS; listing it makes it detectable."""
    assert 'elixir' in kw.BASE_TERMS  # sanity: pick something that is not
    report = kw.keyword_report(
        'You will work in Zig on embedded targets.',
        profile_text='',
        profile_skills=['Zig'],
    )
    assert report.missing == ['zig']


def test_unknown_terms_are_never_invented():
    """Vocabulary-driven means no false positives."""
    report = kw.keyword_report(
        'You will use Frobnicator 9000 daily.', profile_text='', profile_skills=[]
    )
    assert report.matched == []
    assert report.missing == []


def test_word_boundaries_are_respected():
    # 'r' is a real vocabulary term and appears inside dozens of words.
    report = kw.keyword_report(
        'Strong preference for rigorous engineering.', profile_text='', profile_skills=[]
    )
    assert 'r' not in report.missing


def test_punctuated_technology_names_survive_normalization():
    report = kw.keyword_report(
        'You should know C++, CI/CD and Node.js.', profile_text='', profile_skills=[]
    )
    assert 'c++' in report.missing
    assert 'ci/cd' in report.missing
    assert 'node.js' in report.missing


def test_c_plus_plus_does_not_register_as_c():
    """Collapsing punctuation would fold 'c++' into the separate term 'c'."""
    report = kw.keyword_report('We write C++.', profile_text='', profile_skills=[])
    assert 'c++' in report.missing
    assert 'c' not in report.missing


def test_coverage_and_serialization():
    report = kw.keyword_report(
        'Python and Kubernetes.', profile_text='Python', profile_skills=['Python']
    )
    assert report.coverage == 0.5
    assert report.to_dict() == {
        'matched': ['python'], 'missing': ['kubernetes'], 'coverage': 0.5,
    }


def test_empty_posting_yields_an_empty_report():
    report = kw.keyword_report('', profile_text='Python', profile_skills=['Python'])
    assert report.matched == [] and report.missing == []
    assert report.coverage == 0.0


def test_ordering_is_stable_across_runs():
    """The vocabulary is a set; the output must not be."""
    runs = {tuple(kw.keyword_report(JD, '', []).missing) for _ in range(5)}
    assert len(runs) == 1
