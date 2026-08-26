"""The bounded schema and the clamp around the triage call.

No model here — what is under test is the shape of what it is allowed to say.
The enum bound is the load-bearing part: it is what stops the model naming a
requirement the posting never stated, the same guarantee `tailor.py` gets from
bounding `bulletIndexes` to real bullets.
"""
from backend.ai import job_triage


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------

def test_missing_must_haves_is_bound_to_the_computed_terms():
    schema = job_triage.build_schema(['kubernetes', 'terraform'])
    assert schema['properties']['missingMustHaves']['items']['enum'] == [
        'kubernetes', 'terraform',
    ]


def test_the_field_is_dropped_rather_than_left_unbounded():
    """An empty profile means nothing to bind against.

    Omitting the field is the safe answer: an unbounded string list here is
    exactly the invented-requirement failure the bound exists to prevent.
    """
    assert 'missingMustHaves' not in job_triage.build_schema([])['properties']
    assert 'missingMustHaves' not in job_triage.build_schema(None)['properties']


def test_building_a_schema_does_not_mutate_the_base():
    job_triage.build_schema(['python'])
    assert 'missingMustHaves' not in job_triage._BASE_SCHEMA['properties']


def test_a_rejection_must_carry_a_reason():
    """The filtered list exists to be audited, and a row saying only 'no' is
    not auditable."""
    assert 'reason' in job_triage.build_schema([])['required']


def test_fit_and_flag_kinds_are_closed_vocabularies():
    schema = job_triage.build_schema([])
    assert schema['properties']['fit']['enum'] == list(job_triage.FIT_LEVELS)
    kinds = schema['properties']['flags']['items']['properties']['kind']['enum']
    assert kinds == list(job_triage.FLAG_KINDS)


# --------------------------------------------------------------------------
# The clamp
# --------------------------------------------------------------------------

def test_unknown_flag_kinds_are_dropped():
    result = job_triage.normalize_result(
        {'relevant': True, 'fit': 'strong', 'summary': 'x',
         'flags': [{'kind': 'vibes_off', 'detail': 'hmm'},
                   {'kind': 'unpaid', 'detail': 'no salary'}]},
        {},
    )
    assert [f['kind'] for f in result['flags']] == ['unpaid']


def test_an_out_of_range_fit_falls_back_rather_than_passing_through():
    result = job_triage.normalize_result(
        {'relevant': True, 'fit': 'perfect', 'summary': 'x', 'flags': []}, {}
    )
    assert result['fit'] == 'possible'


def test_the_computed_mismatch_is_added_when_the_model_missed_it():
    """It is a regex result, not an opinion — and it is the flag this feature
    was asked for by name."""
    result = job_triage.normalize_result(
        {'relevant': True, 'fit': 'possible', 'summary': 'x', 'flags': []},
        {'seniority': 'junior', 'yearsRequired': 10, 'seniorityMismatch': True},
    )
    assert result['flags'][0]['kind'] == 'seniority_mismatch'
    assert '10 years' in result['flags'][0]['detail']


def test_the_computed_mismatch_is_not_duplicated():
    result = job_triage.normalize_result(
        {'relevant': True, 'fit': 'possible', 'summary': 'x',
         'flags': [{'kind': 'seniority_mismatch', 'detail': 'the model saw it'}]},
        {'seniority': 'junior', 'yearsRequired': 10, 'seniorityMismatch': True},
    )
    assert [f['kind'] for f in result['flags']] == ['seniority_mismatch']
    assert result['flags'][0]['detail'] == 'the model saw it'


def test_no_mismatch_means_no_added_flag():
    result = job_triage.normalize_result(
        {'relevant': True, 'fit': 'strong', 'summary': 'x', 'flags': []},
        {'seniority': 'senior', 'yearsRequired': 10, 'seniorityMismatch': False},
    )
    assert result['flags'] == []


def test_malformed_flags_do_not_crash_the_clamp():
    result = job_triage.normalize_result(
        {'relevant': True, 'fit': 'strong', 'summary': 'x',
         'flags': ['not a dict', None, {'kind': 'unpaid', 'detail': 'x'}]},
        {},
    )
    assert [f['kind'] for f in result['flags']] == ['unpaid']


def test_missing_must_haves_are_kept_as_strings_only():
    result = job_triage.normalize_result(
        {'relevant': True, 'fit': 'strong', 'summary': 'x', 'flags': [],
         'missingMustHaves': ['kubernetes', 7, None]},
        {},
    )
    assert result['missingMustHaves'] == ['kubernetes']


def test_an_unconfigured_model_returns_none(monkeypatch):
    """None rather than a default verdict: a 'relevant' nobody decided is
    indistinguishable from one that was."""
    monkeypatch.setattr('backend.ai.job_triage.is_ai_configured', lambda: False)
    assert job_triage.triage_posting({'title': 'x'}, '', {}, {}) is None


def test_a_model_error_returns_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr('backend.ai.job_triage.is_ai_configured', lambda: True)

    def boom(*a, **k):
        raise RuntimeError('llama is down')

    monkeypatch.setattr('backend.ai.job_triage.chat_json', boom)
    assert job_triage.triage_posting({'title': 'x'}, '', {}, {}) is None


def test_the_computed_facts_reach_the_prompt(monkeypatch):
    """The model must be told the mismatch, not left to notice it."""
    seen = {}
    monkeypatch.setattr('backend.ai.job_triage.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.ai.job_triage.chat_json',
        lambda prompt, **k: seen.update(prompt=prompt) or {
            'relevant': True, 'reason': '', 'fit': 'strong', 'summary': 's',
            'flags': [],
        },
    )

    job_triage.triage_posting(
        {'title': 'Junior Dev', 'description': 'body'},
        'Their background',
        {'seniority': 'junior', 'yearsRequired': 9, 'seniorityMismatch': True},
        {'matched': ['python'], 'missing': ['kubernetes']},
    )

    assert 'MISMATCH' in seen['prompt']
    assert 'kubernetes' in seen['prompt']
    assert 'Their background' in seen['prompt']
